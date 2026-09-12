# -*- coding: utf-8 -*-
"""通知系统：注册制 + 完成/周期通知 + 会话聚合投递。

核心语义：

- **opt-in**：后台进程不自动推送任何东西，必须显式 notice 注册；
- **完成通知幂等**：`completion_sent` 标记防重复；
- **周期通知**：interval_seconds ≥ 30 才生效，进程退出即取消；
- **会话聚合投递**（0.6.0，全部会话类型统一逻辑）：
  通知不直接投递，进入所属会话的**累积器**（内存态，仅本进程内有效，
  宿主关闭/崩溃即丢弃、无恢复）：
    1. 首条通知启动 flusher，睡 ``FLUSH_WINDOW_SECONDS``（3s）聚合
       后续通知；
    2. 窗口到点投递：会话忙（console 唤醒 409 / 信使 gate+tracker）
       → 再等 3s **忙等无上限、永不放弃**，期间新通知继续累积；
    3. 会话空闲 → 取走累积**全部**通知，聚合为一条文本一口气投出：
       console = 一条气泡 + 一个唤醒回合；非 console = 一个信使回合
       （agent 一条回复覆盖全部通知）；
    4. 投递期间新到的通知进入下一轮聚合；队列空则 flusher 退出，
       下条通知懒启动。
  真失败（频道未配置/回合异常）不重试，记日志；busy 是唯一重试态。
  投递函数全部单次语义（无内部重试循环），重试节奏单点归 flusher。

console 会话（双投递）：
  1. 通知气泡：``console_push_store.append(session_id, text, sticky=True)``
     （QwenPaw 前端 2.5s 轮询展示，用户可见）——聚合文本一条气泡；
  2. 唤醒 agent：POST ``/console/chat/task`` 后台任务端点（内核官方
     kit；忙 409 → flusher 忙等）。**约束**：该端点按 (session_id,
     user_id, channel) 三元组**全等**匹配会话、找不到就新建——payload
     必须带 contextvar 真实维度（v0.1.2 幽灵会话教训）。

非 console 会话（信使路由，0.5.0）：
  POST ``/api/process-tools/wake-channel``（messenger.py）——agent 跑
  一轮真实回合，回复经 ``channel_manager.send_event`` 送回注册频道
  （cron agent job 同款链路）。会话级门闩 + tracker 双重忙检。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .manager import ManagedProcess

try:
    from .utils import truncate_line
except ImportError:  # 兼容 pytest 直接以项目根导入
    from utils import truncate_line

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 30  # 周期通知最小间隔（同设计文档）
RECOMMENDED_INTERVAL_SECONDS = 900
TAIL_LINES_IN_NOTICE = 10
# 信使 HTTP 超时须大于 messenger.WAKE_AGENT_TIMEOUT_SECONDS（300s）
MESSENGER_HTTP_TIMEOUT = 330.0
# 聚合窗口（秒）：首条通知后至少等这么久，收集同会话后续通知；
# 会话忙时同样以该粒度忙等（0.6.0：忙等无上限、永不放弃）
FLUSH_WINDOW_SECONDS = 3.0


@dataclass
class _NoticeAccumulator:
    """一个会话的通知累积器（key = agent/user/session/channel 四元组）。

    仅内存态：宿主关闭/崩溃即丢，无恢复语义（0.6.0 作者拍板）。
    items 只 append + 头部按已投递数量删除（flusher 单线程操作）。
    """

    channel: str = "console"  # 首条通知的频道快照（同 key 恒定）
    items: list = field(default_factory=list)
    flusher_task: Optional[asyncio.Task] = None


@dataclass
class Notice:
    """一个进程的通知注册记录。"""

    process_num: int
    session_key: tuple
    interval_seconds: int = 0  # 0 = 仅完成通知
    # 注册时的频道快照（contextvar）：console 走气泡+chat/task 双投递；
    # 非 console 走信使路由（messenger.py，0.5.0），频道名即投递目标。
    # 任何一维写死都会造成三元组错配（v0.1.2 教训），必须用真实值。
    wake_channel: str = "console"
    completion_sent: bool = False
    exit_listener_added: bool = False  # 防重复注册叠加监听（R9）
    periodic_task: Optional[asyncio.Task] = None
    registered_at: float = field(default_factory=time.time)

    def interval_active(self) -> bool:
        return self.interval_seconds >= MIN_INTERVAL_SECONDS


class Notifier:
    """通知调度器（应用内单例，挂在 ProcessManager 之外）。"""

    def __init__(self) -> None:
        # key: (agent_id, user_id, session_id) -> {num: Notice}
        self._notices: dict = {}
        # key: (agent_id, user_id, session_id, channel) -> _NoticeAccumulator
        self._accumulators: dict = {}

    # ── 注册 ──

    def register(self, mp: "ManagedProcess", notice: Notice) -> None:
        """注册通知并把退出监听挂到进程上；按需启动周期任务。

        重复注册（如更新间隔）不会叠加 exit 监听——用
        exit_listener_added 标志去重（监听闭包捕获的就是这个 notice）。
        """
        key = mp.key
        self._notices.setdefault(key, {})[mp.num] = notice
        if not notice.exit_listener_added:
            mp.add_listener(self._make_exit_listener(mp, notice))
            notice.exit_listener_added = True
        if notice.interval_active():
            self._start_periodic(mp, notice)

    def get(self, key: tuple, num: int) -> Optional[Notice]:
        return self._notices.get(key, {}).get(num)

    def set_notice(self, key: tuple, notice: Notice) -> None:
        """登记 Notice 对象（不挂监听不启动周期任务，用于恢复/补记场景）。"""
        self._notices.setdefault(key, {})[notice.process_num] = notice

    def _make_exit_listener(self, mp: "ManagedProcess", notice: Notice):
        async def _on_exit(proc: "ManagedProcess", event: str) -> None:
            if event != "exit":  # pragma: no cover
                return
            self._stop_periodic(notice)
            await self._send_completion(mp, notice)

        return _on_exit

    # ── 消息构建（同设计文档 §4.6 完成通知内容）──

    def build_completion_text(self, mp: "ManagedProcess") -> str:
        mins, secs = divmod(int(mp.elapsed()), 60)
        state_cn = {
            "completed": "✅ 已完成",
            "failed": "❌ 失败",
            "killed": "🛑 已终止",
        }.get(mp.status, mp.status)
        label = f"「{mp.name}」" if mp.name else ""
        head = (
            f"[进程 #{mp.num}{label} {state_cn}]"
            f" exit={mp.exit_code} 用时 {mins}m{secs:02d}s\n"
            f"命令：{truncate_line(mp.command, 120)}\n"
            f"日志：{mp.log_path}"
        )
        lines, _size = mp.get_log_tail(TAIL_LINES_IN_NOTICE)
        if lines:
            return head + "\n输出末尾：\n" + "\n".join(lines)
        return head + "\n（无输出）"

    def build_periodic_text(self, mp: "ManagedProcess") -> str:
        mins, secs = divmod(int(mp.elapsed()), 60)
        lines, _size = mp.get_log_tail(5)
        tail = "\n".join(lines) if lines else "（暂无输出）"
        label = f"「{mp.name}」" if mp.name else ""
        return (
            f"[进程 #{mp.num}{label} 运行中] 已运行 {mins}m{secs:02d}s\n"
            f"命令：{truncate_line(mp.command, 120)}\n"
            f"输出末尾：\n{tail}"
        )

    # ── 聚合投递（0.6.0：全部会话类型统一逻辑）──

    async def deliver(
        self,
        mp_key: tuple,
        text: str,
        wake_channel: str = "console",
    ) -> str:
        """通知入队（聚合器统一入口，0.6.0）。

        通知进入所属会话的累积器，由 flusher 聚合投递：首条通知启动
        3s 聚合窗口；窗口到点会话忙则 3s 忙等（**无上限、永不放弃**，
        期间通知继续累积）；空闲时取走全部累积一口气投出。仅内存态：
        宿主关闭/崩溃则未投递通知丢弃，无恢复语义。

        mp_key = (agent_id, user_id, session_id)

        Returns: 入队描述（非投递结果——投递结果由 flusher 记日志）。
        """
        agent_id, user_id, session_id = mp_key
        key = (agent_id, user_id, session_id, wake_channel)
        acc = self._accumulators.get(key)
        if acc is None:
            acc = _NoticeAccumulator(channel=wake_channel)
            self._accumulators[key] = acc
        acc.items.append(text)
        if acc.flusher_task is None or acc.flusher_task.done():
            acc.flusher_task = asyncio.create_task(self._flusher(key, acc))
        logger.info(
            "proc-tools 通知入队：会话 %s（累积 %d 条，聚合投递中）",
            session_id[:30], len(acc.items),
        )
        return f"已入队聚合（会话累积 {len(acc.items)} 条）"

    @staticmethod
    def _join_notice_text(items: list) -> str:
        """多条通知聚合为一条文本；单条原样直通。"""
        if len(items) == 1:
            return items[0]
        sep = "\n\n" + "─" * 24 + "\n\n"
        return f"【进程通知聚合 ×{len(items)}】\n\n" + sep.join(items)

    async def _flusher(self, key: tuple, acc: _NoticeAccumulator) -> None:
        """会话聚合投递循环（内存态，随任务结束丢弃）。

        节奏：睡一个聚合窗口 → 会话忙则继续以窗口粒度忙等（无上限，
        期间新通知持续累积）→ 空闲时取走全部累积一口气投递 → 投递
        期间新到的进下一轮；队列空则退出（下条通知懒启动）。
        """
        try:
            while True:
                await asyncio.sleep(FLUSH_WINDOW_SECONDS)
                batch = list(acc.items)
                if not batch:
                    return  # 队列空 → flusher 退出
                text = self._join_notice_text(batch)
                status, detail = await self._deliver_once(
                    key, text, acc.channel,
                )
                if status == "busy":
                    # 忙：本批不消费（新通知继续累积），下轮重新聚合
                    logger.info(
                        "proc-tools 会话 %s 忙，%ds 后重试聚合投递",
                        key[2][:30], int(FLUSH_WINDOW_SECONDS),
                    )
                    continue
                # ok / fail 都消费本批（fail 不重试，作者拍板）
                del acc.items[:len(batch)]
                if status == "ok":
                    logger.info(
                        "proc-tools 会话 %s 聚合投递成功（%d 条）：%s",
                        key[2][:30], len(batch), detail,
                    )
                else:
                    logger.warning(
                        "proc-tools 会话 %s 聚合投递失败（%d 条，"
                        "不重试）：%s",
                        key[2][:30], len(batch), detail,
                    )
                # 回到外层：投递期间新到的通知再睡一个窗口聚合
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("proc-tools 聚合 flusher 异常")
        finally:
            acc.flusher_task = None  # 允许下条通知懒启动新 flusher

    async def _deliver_once(
        self, key: tuple, text: str, wake_channel: str,
    ) -> tuple:
        """单次聚合投递（无内部重试）。Returns (status, detail)。

        status ∈ "ok" | "busy" | "fail"；detail 为投递报告。
        console：唤醒成功才发气泡（busy 重试期间不重复刷气泡）；
        fail 时仍尽力发气泡（通知文本至少可见）。
        """
        # key = (agent_id, user_id, session_id, channel)——channel 已由
        # wake_channel 参数显式传入，这里只取前三维
        agent_id, user_id, session_id = key[:3]
        if wake_channel == "console":
            status, reason = await self._wake_once(
                agent_id, user_id, session_id, text,
            )
            if status == "busy":
                return "busy", "唤醒busy(会话忙)"
            bubble = await self._push_bubble(session_id, text)
            if status == "ok":
                return "ok", f"{bubble} 唤醒✅"
            return "fail", f"{bubble} 唤醒❌({reason})"
        status, reason = await self._messenger_once(
            agent_id, user_id, session_id, wake_channel, text,
        )
        if status == "ok":
            return "ok", "IM唤醒✅"
        if status == "busy":
            return "busy", "IM唤醒busy(会话忙)"
        return "fail", f"IM唤醒❌({reason})"

    @staticmethod
    async def _push_bubble(session_id: str, text: str) -> str:
        """投一条 console 气泡（尽力，失败不阻断）。Returns 描述。"""
        try:
            from qwenpaw.app.console_push_store import append as push_append

            await push_append(session_id, text, sticky=True)
            return "气泡✅"
        except Exception as e:  # noqa: BLE001
            logger.debug("process-tools 通知气泡投递失败: %s", e)
            return f"气泡❌({type(e).__name__})"

    async def _send_completion(self, mp: "ManagedProcess", notice: Notice) -> None:
        if notice.completion_sent:
            return
        notice.completion_sent = True
        text = self.build_completion_text(mp)
        result = await self.deliver(
            mp.key, text, notice.wake_channel,
        )
        logger.info("proc #%d 完成通知已入队：%s", mp.num, result)

    async def _wake_once(
        self, agent_id: str, user_id: str, session_id: str, text: str,
    ) -> tuple:
        """单次提交 console 唤醒（无内部重试，重试节奏归 flusher）。

        ``/console/chat/task`` 端点 409 = 会话忙 → "busy"。

        Returns:
            ("ok"|"busy"|"fail", reason)：fail 时 reason 为具体死因。
        """
        try:
            submitted = await asyncio.to_thread(
                self._submit_wake_task,
                agent_id, user_id, session_id, text,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("process-tools 唤醒提交异常: %s", e)
            return "fail", f"{type(e).__name__}: {e}"
        if submitted.get("ok"):
            return "ok", ""
        if submitted.get("conflict"):
            return "busy", "会话忙"
        return "fail", str(submitted.get("error") or "未知失败")

    @staticmethod
    def _submit_wake_task(
        agent_id: str, user_id: str, session_id: str, text: str,
    ) -> dict:
        """同步提交唤醒任务（放 to_thread 跑）。

        user_id 必须是 contextvar 的真实用户维度：``/console/chat/task``
        用 (session_id, user_id, channel) 三元组**全等**匹配 chat，
        任何一维写死都会在错配时静默新建一个幽灵会话（v0.1.1 实机
        踩坑：写死 "main" 导致通知投递到凭空创建的新 chat）。

        Returns:
            {"ok": True, "task_id": ...} | {"conflict": True} | {"ok": False, "error": ...}
        """
        try:
            from qwenpaw.agents.tools.agent_management import (
                resolve_agent_api_base_url,
                submit_agent_chat_task,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"内核客户端不可用: {e}"}

        prompt = (
            "【后台进程通知】以下托管进程状态发生变化，请查看并按需处理"
            "（可用 process_tools_check / process_tools_list 获取详情）：\n\n"
            f"{text}"
        )
        payload = {
            "session_id": session_id,
            "user_id": user_id or "default",
            "channel": "console",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                },
            ],
            "request_context": {"source": "process_tools_notice"},
        }
        base_url = resolve_agent_api_base_url()
        try:
            resp = submit_agent_chat_task(
                base_url, payload, to_agent=agent_id, timeout=15,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        if isinstance(resp, dict) and "error" in resp:
            if resp.get("error") == 409:
                return {"conflict": True}
            # submit_agent_chat_task 把 409 的 detail 装进 error 字段
            if "already running" in str(resp.get("error", "")).lower():
                return {"conflict": True}
            return {"ok": False, "error": str(resp.get("error"))}
        if isinstance(resp, dict) and resp.get("task_id"):
            return {"ok": True, "task_id": resp["task_id"]}
        return {"ok": False, "error": f"未知响应: {resp!r}"}
    # ── IM 信使（非 console 频道，0.5.0；单次化于 0.6.0）──

    async def _messenger_once(
        self,
        agent_id: str,
        user_id: str,
        session_id: str,
        channel: str,
        text: str,
    ) -> tuple:
        """单次提交信使请求（无内部重试，重试节奏归 flusher）。

        信使端点 busy（会话门闩/tracker 忙）→ "busy"。

        Returns:
            ("ok"|"busy"|"fail", reason)：fail 时 reason 为具体死因。
        """
        try:
            submitted = await asyncio.to_thread(
                self._submit_messenger_task,
                agent_id, user_id, session_id, channel, text,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("process-tools 信使提交异常: %s", e)
            return "fail", f"{type(e).__name__}: {e}"
        if submitted.get("ok"):
            return "ok", ""
        if submitted.get("busy"):
            return "busy", "会话忙"
        return "fail", str(submitted.get("error") or "未知失败")

    @staticmethod
    def _submit_messenger_task(
        agent_id: str,
        user_id: str,
        session_id: str,
        channel: str,
        text: str,
    ) -> dict:
        """同步提交信使请求（放 to_thread 跑）。

        X-Agent-Id 携带注册时的 agent 维度：信使端点经
        get_agent_for_request 按它路由回注册会话所属 workspace
        （多 agent 场景不串台）。

        Returns:
            {"ok": True} | {"busy": True} | {"ok": False, "error": ...}
        """
        try:
            import httpx

            from qwenpaw.agents.tools.agent_management import (
                resolve_agent_api_base_url,
            )

            base_url = resolve_agent_api_base_url()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"内核客户端不可用: {e}"}

        payload = {
            "channel": channel,
            "user_id": user_id or "default",
            "session_id": session_id,
            "text": text,
        }
        try:
            with httpx.Client(timeout=MESSENGER_HTTP_TIMEOUT) as client:
                resp = client.post(
                    f"{base_url}/api/process-tools/wake-channel",
                    json=payload,
                    headers={"X-Agent-Id": agent_id or "default"},
                )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if resp.status_code == 409:
            return {"busy": True}
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = None
            return {
                "ok": False,
                "error": (
                    f"{resp.status_code}: "
                    f"{detail or resp.text[:120]}"
                ),
            }
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"响应解析失败: {e}"}
        if isinstance(data, dict) and data.get("ok"):
            return {"ok": True}
        return {
            "ok": False,
            "error": str(
                (data or {}).get("error") if isinstance(data, dict)
                else f"未知响应: {data!r}"
            ),
        }

    # ── 周期通知 ──

    def _start_periodic(self, mp: "ManagedProcess", notice: Notice) -> None:
        async def _loop() -> None:
            try:
                while mp.status == "running":
                    await asyncio.sleep(notice.interval_seconds)
                    if mp.status != "running":
                        break
                    text = self.build_periodic_text(mp)
                    await self.deliver(
                        mp.key, text,
                        notice.wake_channel,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("process-tools 周期通知任务异常")

        notice.periodic_task = asyncio.create_task(_loop())

    def _stop_periodic(self, notice: Notice) -> None:
        task = notice.periodic_task
        if task and not task.done():
            task.cancel()
        notice.periodic_task = None


# ── 单例 ──────────────────────────────────────────────────

_NOTIFIER: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _NOTIFIER
    if _NOTIFIER is None:
        _NOTIFIER = Notifier()
    return _NOTIFIER


def reset_notifier() -> None:
    """测试用：重置单例。"""
    global _NOTIFIER
    _NOTIFIER = None
