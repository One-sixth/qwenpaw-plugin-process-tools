# -*- coding: utf-8 -*-
"""进程注册表与托管进程内核。

架构沿用《AI_MED_UI 进程工具设计》：

- 注册表 key = (agent_id, user_id, session_id)，跨会话不可见不可操作；
- `#N` 按会话单调分配、永不复用；每会话并发运行上限 5（已结束不占名额）；
- 子进程走 asyncio.create_subprocess_shell（v1 无 PTY，管道合并
  stderr→stdout 模拟单流），单一 reader 任务读 stdout（避免设计文档
  §4.3 的 executor/os.read 取消残留问题——asyncio StreamReader 由
  事件循环内部 add_reader 驱动，天然安全）；
- 输出三路之 v1 两路：512KB 环形缓冲（供 read_stdout 回放）+
  净化日志落盘（供 check/notice/用户读取）；
- 共享 _exit_future 用 asyncio.shield 保护（设计文档 §4.7 教训：
  wait_for 超时取消会沿 await 链撕毁共享 future，伤害其他等待者）；
- 前台等待被取消时尽力 kill + 短等，绝不留孤儿；
- 应用 shutdown 时托管进程全部终止。

跨平台信号/杀进程语义：
- POSIX：start_new_session=True（pid==pgid），SIGINT 单发进程或
  killpg 整组，kill 用 SIGKILL 整组兜底；
- Windows：CREATE_NEW_PROCESS_GROUP 启动；"sigint" 语义映射为
  CTRL_BREAK_EVENT（对该进程组生效，等价整组中断路径）；kill 用
  taskkill /F /T 杀树，兜底 proc.kill()。
"""

import asyncio
import collections
import logging
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple

try:
    from .sanitizer import Sanitizer
    from .utils import (
        get_data_dir,
        read_log_tail,
        sanitize_session_token,
        session_key,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from sanitizer import Sanitizer
    from utils import (
        get_data_dir,
        read_log_tail,
        sanitize_session_token,
        session_key,
    )

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

RING_LIMIT = 512 * 1024  # 环形缓冲上限（字节），同设计文档
READ_CHUNK = 4096
MAX_RUNNING_PER_SESSION = 5
KILL_GRACE_SECONDS = 3.0
LOG_KEEP_DAYS = 30

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"

# Windows 实测（2026-09-05 实机冒烟）：CTRL_BREAK 走控制台默认处置程序，
# 子进程的 SIGINT/SIGBREAK handler 根本不会被调用，进程以
# STATUS_CONTROL_C_EXIT (0xC000013A) 被 OS 终止。这是"被中断杀死"，
# 不是"任务失败"——必须映射为 killed，否则 agent 会误判。
WIN_STATUS_CONTROL_C_EXIT = 0xC000013A
# 同一状态的有符号 32 位形态（returncode 可能以负数出现）
WIN_STATUS_CONTROL_C_EXIT_SIGNED = WIN_STATUS_CONTROL_C_EXIT - 2**32  # -1073741510


def status_for_exit(exit_code, killed_by_us: bool) -> str:
    """退出码 → 进程状态映射（纯函数，便于钉死测试）。"""
    if killed_by_us:
        return STATUS_KILLED
    # returncode 在不同路径可能以有符号/无符号 32 位出现，双形态都认
    if exit_code in (WIN_STATUS_CONTROL_C_EXIT, WIN_STATUS_CONTROL_C_EXIT_SIGNED):
        return STATUS_KILLED
    if exit_code == 0:
        return STATUS_COMPLETED
    return STATUS_FAILED


class ManagedProcess:
    """一个被托管的子进程。"""

    def __init__(
        self,
        num: int,
        command: str,
        key: Tuple[str, str, str],
        cwd: str,
        log_path: str,
    ) -> None:
        self.num = num  # 会话内编号 #N
        self.command = command
        self.key = key
        self.cwd = cwd
        self.log_path = log_path
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.status = STATUS_RUNNING
        self.exit_code: Optional[int] = None

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._sanitizer = Sanitizer()
        self._log_handle = None
        self._log_lost = False  # 日志盘写失败后置 True（降级丢段）
        self._reader_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._exit_future: Optional[asyncio.Future] = None
        self._killed_by_us = False

        # 环形缓冲（原始字节，带全局偏移）
        self._ring: collections.deque = collections.deque()
        self._ring_bytes = 0
        self._dropped_bytes = 0
        self._total_out = 0

        # 通知挂点：async def listener(mp, event:str)
        self._listeners: List[Callable] = []

    # ── 生命周期 ──

    async def start(self) -> None:
        """spawn 子进程并挂 reader/monitor 任务。"""
        kwargs: dict = dict(
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
            )
        # S2 修复：先备好日志文件句柄再 spawn——spawn 成功后若建目录失败，
        # 会留下一个无主句柄、不进注册表、无人可杀的孤儿进程。
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._log_handle = open(
            self.log_path, "a", encoding="utf-8", newline="\n",
        )
        try:
            self._proc = await asyncio.create_subprocess_shell(
                self.command,
                cwd=self.cwd or None,
                **kwargs,
            )
        except Exception:
            self._close_log()
            raise
        self._exit_future = asyncio.get_running_loop().create_future()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._monitor_task = asyncio.create_task(self._monitor())

    def _write_log(self, text: str) -> None:
        """S1 修复：日志写失败降级为丢段 + 告警，reader 绝不能因此死亡。"""
        if self._log_handle is None or self._log_lost:
            return
        try:
            self._log_handle.write(text)
            self._log_handle.flush()
        except (OSError, ValueError) as e:
            # ValueError：句柄已被意外关闭；OSError：磁盘满/IO 错
            self._log_lost = True
            logger.warning(
                "proc #%d 日志落盘失败（后续输出仅存环形缓冲）：%s",
                self.num, e,
            )

    async def _read_loop(self) -> None:
        """唯一读取任务：拉干 stdout，喂环形缓冲与净化日志。

        读管道本身的异常上抛（交给 _monitor 兜底杀进程）；
        日志写异常在 _write_log 内部消化，不会杀死 reader。
        """
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            data = await self._proc.stdout.read(READ_CHUNK)
            if not data:
                break
            self._push_ring(data)
            self._total_out += len(data)
            text = self._sanitizer.feed(data)
            if text:
                self._write_log(text)
        rest = self._sanitizer.flush()
        if rest:
            self._write_log(rest)

    async def _monitor(self) -> None:
        """退出监控：等 reader 收尾 → 取退出码 → 置状态 → 触发监听。"""
        assert self._proc is not None
        try:
            if self._reader_task:
                await self._reader_task
            self.exit_code = await self._proc.wait()
        except asyncio.CancelledError:
            # 被取消（测试收尾/应用关闭）：同步尽力终止子进程，然后
            # 必须重新上抛——吞掉取消再 await 会让 _cancel_all_tasks
            # 永久死等（取消不会重发），这是本套件最硬的一条纪律。
            self._emergency_stop()
            self._close_log()
            raise
        except Exception as e:  # noqa: BLE001
            # S1 修复：reader 死亡（读管道异常）等故障不能谎报结束——
            # 子进程可能还活着。先同步终止（防管道满挂死/防孤儿），
            # 再取真实退出码，状态按 killed 语义走。
            logger.warning("proc #%d monitor 异常，终止子进程：%s", self.num, e)
            self._killed_by_us = True
            self._emergency_stop()
            try:
                self.exit_code = await asyncio.wait_for(
                    self._proc.wait(), KILL_GRACE_SECONDS + 2.0,
                )
            except Exception:  # noqa: BLE001
                self.exit_code = self._proc.returncode
        finally:
            self._close_log()

        self.finished_at = time.time()
        self.status = status_for_exit(self.exit_code, self._killed_by_us)

        if self._exit_future is not None and not self._exit_future.done():
            self._exit_future.set_result(self.exit_code)

        listeners = list(self._listeners)
        for listener in listeners:
            try:
                asyncio.create_task(listener(self, "exit"))
            except Exception as e:  # noqa: BLE001
                logger.warning("proc #%d listener error: %s", self.num, e)

    def _emergency_stop(self) -> None:
        """monitor 被取消时的同步兜底：尽力立刻终止子进程/进程组。"""
        try:
            if os.name == "posix":
                import signal

                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass
            self._proc.kill()  # Windows：TerminateProcess（POSIX 兜底）
        except Exception:  # noqa: BLE001
            pass

    def _close_log(self) -> None:
        try:
            if self._log_handle:
                self._log_handle.close()
        except Exception:  # noqa: BLE001
            pass
        self._log_handle = None

    # ── 等待与取消 ──

    async def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """等待进程结束，返回退出码；超时返回 None。

        共享 _exit_future 必须包 shield：任何等待方的超时/取消只毁
        自己的等待，future 本体完好，其他等待者仍能收到结果
        （设计文档 §4.7 的 asyncio.shield 教训）。
        """
        if self._exit_future is None:
            return self.exit_code
        if self._exit_future.done():
            return self._exit_future.result()
        if timeout is None or timeout <= 0:
            return await asyncio.shield(self._exit_future)
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._exit_future), timeout,
            )
        except asyncio.TimeoutError:
            return None

    # ── 交互 ──

    async def write_stdin(self, data: str) -> Optional[str]:
        """向 stdin 写入文本。成功返回 None，失败返回错误消息。"""
        if self.status != STATUS_RUNNING or self._proc is None:
            return f"进程 #{self.num} 已结束（状态 {self.status}），无法写入 stdin"
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(data.encode("utf-8", errors="replace"))
            await self._proc.stdin.drain()
        except Exception as e:  # noqa: BLE001
            return f"写入 stdin 出错：{e}"
        return None

    def signal_interrupt(self, group: bool = False) -> str:
        """发送中断信号（sigint 语义）。返回描述实际发送内容的文本。

        POSIX：group=False → SIGINT 主进程；group=True → killpg SIGINT 整组。
        Windows：CTRL_BREAK_EVENT 对进程组生效（无法只中断主进程），
        统一描述为进程组中断。
        """
        assert self._proc is not None
        pid = self._proc.pid
        try:
            if os.name == "posix":
                import signal

                if group:
                    os.killpg(os.getpgid(pid), signal.SIGINT)
                    return f"已向进程组 {pid} 发送 SIGINT（整组中断）"
                os.kill(pid, signal.SIGINT)
                return f"已向主进程 {pid} 发送 SIGINT"
            else:
                os.kill(pid, _ctrl_break_event())
                return (
                    f"已向进程组 {pid} 发送 CTRL_BREAK"
                    "（Windows 实测：不经 CPython 信号机制，"
                    "handler 不会执行，进程以 0xC000013A 被 OS 终止，"
                    "≈ 略轻于 sigkill 的第二档硬杀；优雅退出请走 stdin 指令）"
                )
        except ProcessLookupError:
            return "目标进程已不存在（可能刚刚退出）"
        except Exception as e:  # noqa: BLE001
            return f"发送信号失败：{e}"

    async def kill(self) -> str:
        """强杀（含子孙进程），尽力等待收尾，绝不留孤儿。"""
        assert self._proc is not None
        self._killed_by_us = True
        pid = self._proc.pid
        note = ""
        try:
            if os.name == "posix":
                import signal

                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    note = f"已 SIGKILL 进程组 {pid}"
                except ProcessLookupError:
                    pass
                except Exception:  # noqa: BLE001
                    self._proc.kill()
                    note = f"killpg 失败，已 SIGKILL 主进程 {pid}"
            else:
                try:
                    await asyncio.to_thread(_taskkill_tree, pid)
                    note = f"已 taskkill /F /T 杀进程树 {pid}"
                except Exception:  # noqa: BLE001
                    self._proc.kill()
                    note = f"taskkill 失败，已终止主进程 {pid}"
        except Exception as e:  # noqa: BLE001
            note = f"杀进程时出错：{e}"
        # 短等收尾（monitor 会置状态）；等不到也不阻塞调用方
        await self.wait(timeout=KILL_GRACE_SECONDS)
        return note

    # ── 输出 ──

    def _push_ring(self, data: bytes) -> None:
        self._ring.append(data)
        self._ring_bytes += len(data)
        while self._ring_bytes > RING_LIMIT and len(self._ring) > 1:
            old = self._ring.popleft()
            self._ring_bytes -= len(old)
            self._dropped_bytes += len(old)

    def get_buffer(self, offset: int, max_bytes: int) -> Tuple[int, bytes, int]:
        """从环形缓冲取 [offset, ...) 的原始字节。

        offset 早于缓冲起点时自动抬升（旧数据已被挤出）。

        Returns:
            (实际返回的起始偏移, 字节, 下一个可读偏移)
        """
        offset = max(offset, self._dropped_bytes)
        pos = self._dropped_bytes
        chunks: List[bytes] = []
        total = 0
        for chunk in self._ring:
            end = pos + len(chunk)
            if end > offset and total < max_bytes:
                take = chunk[max(0, offset - pos) :]
                if total + len(take) > max_bytes:
                    take = take[: max_bytes - total]
                chunks.append(take)
                total += len(take)
            pos = end
            if total >= max_bytes:
                break
        data = b"".join(chunks)
        start = offset if data else min(offset, self._total_out)
        return start, data, start + len(data)

    def pending_output(self) -> str:
        """尚未落完整行的净化残段（实时尾部展示用）。"""
        return self._sanitizer.get_pending()

    @property
    def dropped_bytes(self) -> int:
        """已被环形缓冲挤出（丢弃）的最旧字节数。"""
        return self._dropped_bytes

    @property
    def total_out(self) -> int:
        """进程累计产出的原始字节总数（= 下一个可写偏移）。"""
        return self._total_out

    def get_log_tail(self, tail_lines: int = 20) -> Tuple[List[str], int]:
        """读净化日志尾部若干行 + 日志字节大小。"""
        return read_log_tail(self.log_path, tail_lines)

    def get_log_text(self, max_bytes: int = 262144) -> str:
        """读取净化日志全文（超大时保留头部与尾部，中间标注省略）。"""
        if not os.path.isfile(self.log_path):
            return ""
        size = os.path.getsize(self.log_path)
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            if size <= max_bytes:
                return f.read()
            head = f.read(max_bytes // 2)
            f.seek(size - max_bytes // 2)
            tail = f.read()
        omitted = size - len(head) - len(tail)
        return f"{head}\n<<中间省略 {omitted} 字节>>\n{tail}"

    # ── 状态摘要 ──

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def pid(self) -> Optional[int]:
        """子进程 OS pid（启动失败或未 spawn 时为 None）。"""
        return self._proc.pid if self._proc else None

    def summary_line(self) -> str:
        """list 工具用的一行摘要。"""
        mins, secs = divmod(int(self.elapsed()), 60)
        state = self.status
        code = "" if self.exit_code is None else f" exit={self.exit_code}"
        cmd = self.command.replace("\n", " ")
        if len(cmd) > 80:
            # 统一用 <<truncated>> 截断风格（实机冒烟观感反馈）
            cmd = cmd[:80] + "<<truncated>>"
        return (
            f"#{self.num} [{state}{code}] {mins // 60:02d}:"
            f"{mins % 60:02d}:{secs:02d} $ {cmd}"
        )

    def add_listener(self, listener: Callable) -> None:
        self._listeners.append(listener)


def _ctrl_break_event():
    """取 Windows CTRL_BREAK_EVENT，非 Windows 下不会被调用。"""
    import signal

    return signal.CTRL_BREAK_EVENT


def _taskkill_tree(pid: int) -> None:
    """Windows：taskkill /F /T /PID 杀进程树（同步，调用方放 to_thread）。"""
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=10,
    )


class ProcessManager:
    """进程注册表（应用内单例）。"""

    def __init__(self) -> None:
        self._sessions: Dict[Tuple[str, str, str], Dict[int, ManagedProcess]] = {}
        self._counters: Dict[Tuple[str, str, str], int] = {}

    # ── 编号与注册 ──

    @staticmethod
    def _session_token(key: Tuple[str, str, str]) -> str:
        return sanitize_session_token("|".join(key))

    def _counter_path(self, key: Tuple[str, str, str]) -> str:
        return os.path.join(
            get_data_dir(), "counters", f"{self._session_token(key)}.cnt",
        )

    def _alloc_id(self, key: Tuple[str, str, str]) -> int:
        """单调编号 + 落盘续号。

        内存计数器在宿主重启后归零，#N 会复用重启前的旧日志文件
        （append 混排两个进程的输出）。与磁盘历史最大号取 max 续排，
        「编号 #N 单调分配」跨重启成立；磁盘读写失败静默降级为纯内存
        计数——附属功能绝不阻塞 exec 主流程。

        计数文件缺失时以 ``logs/`` 里本会话 token 的最大既有编号兜底
        （0.2.1 首装盲区：计数器开始记录前，历史日志已占用的号段）。
        """
        n = self._counters.get(key, 0) + 1
        path = self._counter_path(key)
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk_n = int(f.read().strip() or 0)
            if disk_n >= n:
                n = disk_n + 1
        except (OSError, ValueError):
            n = max(n, self._max_logged_num(key) + 1)
        self._counters[key] = n
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(n))
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("process-tools 编号计数器落盘失败: %s", e)
        return n

    def _max_logged_num(self, key: Tuple[str, str, str]) -> int:
        """扫 logs/ 目录中本会话 token 的最大编号（无则 0），兜底用。"""
        prefix = f"proc_{self._session_token(key)}_"
        log_dir = os.path.join(get_data_dir(), "logs")
        best = 0
        try:
            names = os.listdir(log_dir)
        except OSError:
            return 0
        for name in names:
            if not name.startswith(prefix) or not name.endswith(".log"):
                continue
            digits = name[len(prefix):-4]
            try:
                best = max(best, int(digits))
            except ValueError:
                continue
        return best

    def _running_count(self, key: Tuple[str, str, str]) -> int:
        return sum(
            1
            for mp in self._sessions.get(key, {}).values()
            if mp.status == STATUS_RUNNING
        )

    async def start(self, command: str, cwd: str = "") -> ManagedProcess:
        """在当前会话注册表启动一个托管进程。

        Raises:
            RuntimeError: 并发运行数达到上限
        """
        key = session_key()
        if not command or not command.strip():
            raise RuntimeError("command 不能为空")
        if self._running_count(key) >= MAX_RUNNING_PER_SESSION:
            raise RuntimeError(
                f"本会话运行中进程已达上限 {MAX_RUNNING_PER_SESSION} 个，"
                "请先用 process_tools_check/communicate 结束部分进程，"
                "或等待其完成（已结束进程不占名额）",
            )
        num = self._alloc_id(key)
        log_path = os.path.join(
            get_data_dir(),
            "logs",
            f"proc_{self._session_token(key)}_{num}.log",
        )
        mp = ManagedProcess(num, command, key, cwd, log_path)
        try:
            await mp.start()
        except Exception as e:  # noqa: BLE001
            # 启动失败也烧掉编号（永不复用原则），文案明确告知
            raise RuntimeError(
                f"进程启动失败（编号 #{num} 已消耗，不回收）：{e}"
            ) from e
        self._sessions.setdefault(key, {})[num] = mp
        return mp

    def get(self, num: int) -> Optional[ManagedProcess]:
        """按 #N 查当前会话的进程。跨会话不可见。"""
        key = session_key()
        return self._sessions.get(key, {}).get(num)

    def list_session(self) -> List[ManagedProcess]:
        """当前会话的全部进程（按编号升序）。"""
        key = session_key()
        return sorted(self._sessions.get(key, {}).values(), key=lambda m: m.num)

    def list_all_sessions(self) -> Dict[Tuple[str, str, str], List[ManagedProcess]]:
        """全部会话（供管理/排查，工具层默认不暴露）。"""
        return {k: sorted(v.values(), key=lambda m: m.num) for k, v in self._sessions.items()}

    async def shutdown_all(self) -> None:
        """应用退出：终止所有仍在运行的托管进程。"""
        tasks = []
        for procs in self._sessions.values():
            for mp in procs.values():
                if mp.status == STATUS_RUNNING:
                    tasks.append(mp.kill())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("ProcessManager：已终止全部托管进程")

    def cleanup_old_logs(self, days: int = LOG_KEEP_DAYS) -> int:
        """清理 N 天前的日志与编号计数器文件，返回删除数（startup hook 调用）。"""
        data_root = os.path.join(get_data_dir(), "logs")
        counters_dir = os.path.join(get_data_dir(), "counters")
        cutoff = time.time() - days * 86400
        removed = 0
        for d in (data_root, counters_dir):
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                path = os.path.join(d, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    continue
        if removed:
            logger.info("process-tools 清理旧日志/计数器 %d 个", removed)
        return removed


# ── 单例 ──────────────────────────────────────────────────

_MANAGER: Optional[ProcessManager] = None


def get_manager() -> ProcessManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ProcessManager()
    return _MANAGER


def reset_manager() -> None:
    """测试用：重置单例。"""
    global _MANAGER
    _MANAGER = None
