# -*- coding: utf-8 -*-
"""进程注册表与托管进程内核。

- 注册表 key = (agent_id, user_id, session_id)，跨会话不可见不可操作；
- `#N` 按会话单调分配、永不复用；
- 子进程走 asyncio.create_subprocess_shell（无 PTY，管道合并
  stderr→stdout 模拟单流），单一 reader 任务读 stdout（asyncio
  StreamReader 由事件循环内部 add_reader 驱动，天然安全）；
- 输出三路之两路：512KB 环形缓冲（供 read_stdout 回放）+
  净化日志落盘（供 check/notice/用户读取）；
- 共享 _exit_future 用 asyncio.shield 保护（wait_for 超时取消会沿
  await 链撕毁共享 future，伤害其他等待者）；
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
import json
import logging
import os
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

try:
    from .sanitizer import Sanitizer
    from .utils import (
        agent_aliases,
        get_data_dir,
        read_log_tail,
        sanitize_session_token,
        session_file_candidates,
        session_key,
        workspaces_root,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from sanitizer import Sanitizer
    from utils import (
        agent_aliases,
        get_data_dir,
        read_log_tail,
        sanitize_session_token,
        session_file_candidates,
        session_key,
        workspaces_root,
    )

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

RING_LIMIT = 512 * 1024  # 环形缓冲上限（字节），同设计文档
READ_CHUNK = 4096
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


def build_subprocess_env(extra: Optional[dict] = None) -> dict:
    """子进程环境：宿主 os.environ ⊕ 用户 env ⊕ PATH 前置宿主 python 目录。

    对齐框架 execute_shell_command 惯例（shell.py 入口）：增量叠加、永不
    全量替换；PATH 前置 sys.executable 所在目录使子进程 `python`/`pip`
    命中框架所在环境；Windows 下 PATH 键名大小写变体（Path/path）做
    不敏感归并，避免同键双份。
    """
    env = dict(os.environ)
    if extra:
        host_by_upper = {k.upper(): k for k in env}
        for k, v in extra.items():
            host_key = host_by_upper.get(k.upper())
            if host_key is not None and host_key != k:
                env.pop(host_key)  # 用户用变体键覆盖时先摘掉宿主那份
            env[k] = v
    bin_dir = os.path.dirname(sys.executable)
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    cur = env.get(path_key, "")
    env[path_key] = f"{bin_dir}{os.pathsep}{cur}" if cur else bin_dir
    return env


class ManagedProcess:
    """一个被托管的子进程。"""

    def __init__(
        self,
        num: int,
        command: Union[str, list],
        key: Tuple[str, str, str],
        cwd: str,
        log_path: str,
        name: str = "",
        env: Optional[dict] = None,
        hide_window: bool = False,
        encoding: str = "utf-8",
        display: str = "",
    ) -> None:
        self.num = num  # 会话内编号 #N
        # command 双形态：str=shell 命令行；list=argv 原生直启（no_shell）
        # 存储/展示统一用字符串（shell 包装时 display 传用户原文，
        # 回显不被 pwsh 前缀污染），spawn 用 _spawn_target
        if isinstance(command, (list, tuple)):
            self._spawn_target: Union[str, list] = [str(x) for x in command]
            self.command = display or " ".join(self._spawn_target)
        else:
            self._spawn_target = str(command)
            self.command = self._spawn_target
        self.key = key
        self.cwd = cwd
        self.log_path = log_path
        self.name = (name or "").strip()[:80]  # 人肉标签（展示用）
        self.encoding = encoding or "utf-8"  # 管道编解码 codec（auto 已由工具层解析）
        self._env = env or None  # 增量环境变量（spawn 时并入 os.environ）
        self._hide_window = bool(hide_window)  # Windows：压掉子进程控制台窗口
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.status = STATUS_RUNNING
        self.exit_code: Optional[int] = None

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._sanitizer = Sanitizer(self.encoding)
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
        # 对齐框架惯例：始终并入宿主环境 + PATH 前置宿主 python 目录
        kwargs["env"] = build_subprocess_env(self._env)
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if self._hide_window:
                flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE（subprocess 未导出该常量）
                kwargs["startupinfo"] = si
            kwargs["creationflags"] = flags
        # S2 修复：先备好日志文件句柄再 spawn——spawn 成功后若建目录失败，
        # 会留下一个无主句柄、不进注册表、无人可杀的孤儿进程。
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._log_handle = open(
            self.log_path, "a", encoding="utf-8", newline="\n",
        )
        try:
            if isinstance(self._spawn_target, list):
                self._proc = await asyncio.create_subprocess_exec(
                    *self._spawn_target,
                    cwd=self.cwd or None,
                    **kwargs,
                )
            else:
                self._proc = await asyncio.create_subprocess_shell(
                    self._spawn_target,
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
            self._proc.stdin.write(
                data.encode(self.encoding, errors="replace")
            )
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
        label = f"「{self.name[:40]}」" if self.name else ""
        cmd = self.command.replace("\n", " ")
        if len(cmd) > 80:
            # 统一用 <<truncated>> 截断风格（实机冒烟观感反馈）
            cmd = cmd[:80] + "<<truncated>>"
        return (
            f"#{self.num}{label} [{state}{code}] {mins // 60:02d}:"
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

    async def start(
        self,
        command: Union[str, list],
        cwd: str = "",
        name: str = "",
        env: Optional[dict] = None,
        hide_window: bool = False,
        encoding: str = "utf-8",
        display: str = "",
    ) -> ManagedProcess:
        """在当前会话注册表启动一个托管进程。

        Raises:
            RuntimeError: command 为空，或启动失败（编号已消耗）
        """
        key = session_key()
        if isinstance(command, (list, tuple)):
            if not command:
                raise RuntimeError("command 不能为空")
        elif not str(command or "").strip():
            raise RuntimeError("command 不能为空")
        num = self._alloc_id(key)
        log_path = os.path.join(
            get_data_dir(),
            "logs",
            f"proc_{self._session_token(key)}_{num}.log",
        )
        mp = ManagedProcess(
            num, command, key, cwd, log_path,
            name=name, env=env, hide_window=hide_window, encoding=encoding,
            display=display,
        )
        try:
            await mp.start()
        except Exception as e:  # noqa: BLE001
            # 启动失败也烧掉编号（永不复用原则），文案明确告知
            raise RuntimeError(
                f"进程启动失败（编号 #{num} 已消耗，不回收）：{e}"
            ) from e
        self._sessions.setdefault(key, {})[num] = mp
        return mp

    def spawn_detached(
        self,
        command: Union[str, list],
        cwd: str = "",
        env: Optional[dict] = None,
    ) -> int:
        """启动完全脱离托管的进程（detach 模式），返回 OS 级 PID。

        command 双形态同托管路径：str 走 shell=True，list 走原生直启。
        与托管路径相反的选择：stdio 全 DEVNULL（无 PIPE 也就无环形缓冲/
        净化日志/reader 任务）、不注册不进 #N 编号、无 monitor（宿主退出
        不清理它，也不受 shutdown_all 影响）。后续管理靠系统命令用 PID
        自理（Windows taskkill / 类 Unix kill）。同步函数，async 侧放
        to_thread 调用。
        """
        if isinstance(command, (list, tuple)):
            if not command:
                raise RuntimeError("command 不能为空")
            spawn: Union[str, list] = [str(x) for x in command]
        else:
            if not str(command or "").strip():
                raise RuntimeError("command 不能为空")
            spawn = str(command)
        kwargs: dict = dict(
            shell=isinstance(spawn, str),
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=build_subprocess_env(env),
        )
        if os.name == "posix":
            # 新会话：脱离宿主进程组，免疫宿主终端的 SIGHUP/整组信号
            kwargs["start_new_session"] = True
        else:
            # CREATE_NO_WINDOW：子进程获得「没有窗口的独立 console」——
            # 宿主退出关控制台时它不被连坐（达成 detach 语义），且无窗可弹。
            # ⚠️ 不用 DETACHED_PROCESS 的实测依据（2026-09-05）：完全无
            # console 时 PowerShell（7 与 5.1 同病）无法初始化宿主，
            # 0.3 秒内 exit 0 静默罢工、-Command 根本不执行——detach +
            # shell=pwsh 组合全军覆没；CREATE_NO_WINDOW 变体矩阵 3/3 存活。
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        return subprocess.Popen(spawn, **kwargs).pid

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

    # ── 死会话数据清理 ────────────────────────────────────

    STALE_GRACE_SECONDS = 600.0

    def cleanup_stale_sessions(
        self,
        workspaces: Optional[List[str]] = None,
        grace: float = STALE_GRACE_SECONDS,
    ) -> Tuple[int, int]:
        """启动清理：会话已死则其计数器与日志即刻移除（startup hook 调用）。

        会话判活采「chats.json 条目 ∧ 真实会话文件」**双存在**——UI 删除
        chat 只删索引不删文件、手动删/挪文件则索引可能残留，任一单判据
        都不可靠，两种死法必须全覆盖。token 是三元组的 crc32 单向映射，
        无法从文件名反推会话，故正向枚举活会话构建 live 集合、对差集清除。

        mtime 在宽限期（默认 600s）内的文件不删（防新建会话索引/文件
        迟落盘的窗口误杀）；chats.json 缺失或损坏的工作区整体跳过——
        读不到依据时绝不误删。返回 (删除计数器数, 删除日志数)。
        """
        if workspaces is None:
            workspaces = self._scan_workspaces()
        now = time.time()
        removed_cnt = removed_log = 0
        for ws in workspaces:
            data = os.path.join(ws, "process_tools_data")
            counters_dir = os.path.join(data, "counters")
            logs_dir = os.path.join(data, "logs")
            if not (os.path.isdir(counters_dir) or os.path.isdir(logs_dir)):
                continue
            try:
                with open(os.path.join(ws, "chats.json"), encoding="utf-8") as f:
                    chats = json.load(f).get("chats", [])
            except (OSError, ValueError, AttributeError):
                logger.debug("process-tools 清理跳过（chats.json 不可读）: %s", ws)
                continue
            live = self._live_tokens(ws, chats)
            removed_cnt += self._sweep_dead_files(
                counters_dir,
                live,
                lambda n: n[:-4] if n.endswith(".cnt") else None,
                now,
                grace,
            )
            removed_log += self._sweep_dead_files(
                logs_dir, live, self._log_token_of, now, grace
            )
        if removed_cnt or removed_log:
            logger.info(
                "process-tools 死会话数据清理：计数器 %d 个 / 日志 %d 个",
                removed_cnt,
                removed_log,
            )
        return removed_cnt, removed_log

    @staticmethod
    def _scan_workspaces() -> List[str]:
        """workspaces/ 下全部一级子目录（各 agent 工作区）。"""
        root = workspaces_root()
        if not root:
            return []
        try:
            return [
                os.path.join(root, d)
                for d in sorted(os.listdir(root))
                if os.path.isdir(os.path.join(root, d))
            ]
        except OSError:
            return []

    @staticmethod
    def _log_token_of(name: str) -> Optional[str]:
        """从 proc_{token}_{N}.log 提取会话 token；解析不出的名字返回 None（永不碰）。"""
        if not (name.startswith("proc_") and name.endswith(".log")):
            return None
        token, sep, digits = name[len("proc_"):-len(".log")].rpartition("_")
        return token if sep and digits.isdigit() else None

    @staticmethod
    def _live_tokens(ws: str, chats: list) -> Set[str]:
        """枚举该工作区「索引与文件双活」chat 的 token live 集合。

        token 文本必须与 exec 时 session_key 三元组拼法逐字一致：
        "agent|user|session"（原字符，非文件名净化形态）。agent 维度取
        agent.json id 与目录名双别名——多留无害，多删有害。
        """
        live: Set[str] = set()
        aliases = agent_aliases(ws)
        sessions_dir = os.path.join(ws, "sessions")
        for chat in chats:
            try:
                sid = str(chat["session_id"])
                uid = str(chat.get("user_id") or "")
                channel = str(chat.get("channel") or "")
            except (KeyError, TypeError):
                continue
            if not sid:
                continue
            candidates = session_file_candidates(sessions_dir, sid, uid, channel)
            if not any(os.path.exists(p) for p in candidates):
                continue
            for alias in aliases:
                live.add(sanitize_session_token(f"{alias}|{uid}|{sid}"))
        return live

    @staticmethod
    def _sweep_dead_files(
        directory: str,
        live: Set[str],
        token_of: Callable[[str], Optional[str]],
        now: float,
        grace: float,
    ) -> int:
        """删除目录中 token 不活且超过宽限期的文件，返回删除数。"""
        removed = 0
        try:
            names = os.listdir(directory)
        except OSError:
            return 0
        for name in names:
            token = token_of(name)
            if token is None or token in live:
                continue
            path = os.path.join(directory, name)
            try:
                if now - os.path.getmtime(path) < grace:
                    continue
                os.remove(path)
                removed += 1
            except OSError:
                continue
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
