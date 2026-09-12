# -*- coding: utf-8 -*-
"""通知系统：注册制 + 完成/周期通知 + 按频道投递。

核心语义：

- **opt-in**：后台进程不自动推送任何东西，必须显式 notice 注册；
- **完成通知幂等**：`completion_sent` 标记防重复；
- **周期通知**：interval_seconds ≥ 30 才生效，进程退出即取消；
- **投递按频道分流**（0.5.0）：
  - console 会话（双投递）：
    1. 通知气泡：``console_push_store.append(session_id, text, sticky=True)``
       （QwenPaw 前端 2.5s 轮询展示，用户可见）；
    2. 唤醒 agent：POST ``/console/chat/task`` 后台任务端点
       （复用内核 ``agents.tools.agent_management`` 官方客户端 kit，
       与 ``submit_to_agent`` 同路；会话忙碌返回 409 → 延后重试）。
       **约束**：该端点按 (session_id, user_id, channel) 三元组**全等**
       匹配会话、找不到就新建——payload 必须带 contextvar 真实维度
       （v0.1.2 修复写死 user_id 导致的幽灵会话）。
  - 非 console 会话（信使路由，0.5.0 新增）：
    气泡不再投（console_push_store 是死信）；改经插件信使端点
    ``POST /api/process-tools/wake-channel``（messenger.py）——
    agent 跑一轮真实回合，回复经 ``channel_manager.send_event``
    送回注册频道（cron agent job 同款链路）。忙碌 409 → 同款
    30s×20 重试。

所有投递失败都不阻塞调用方：气泡与通知记录仍然保留。
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
WAKE_RETRY_SECONDS = 30.0  # 409（会话忙）重试间隔
WAKE_MAX_RETRIES = 20  # 唤醒重试上限（约 10 分钟）
TAIL_LINES_IN_NOTICE = 10
# 信使 HTTP 超时须大于 messenger.WAKE_AGENT_TIMEOUT_SECONDS（300s）
MESSENGER_HTTP_TIMEOUT = 330.0


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

    # ── 双投递 ──

    async def deliver(
        self,
        mp_key: tuple,
        text: str,
        wake_channel: str = "console",
    ) -> str:
        """把通知投出去，返回投递情况描述。

        mp_key = (agent_id, user_id, session_id)
        wake_channel = 注册通知时快照的频道，投递按频道分流（0.5.0）：
        console 走气泡+chat/task 双投递；非 console 走信使路由
        （agent 回合 + 回复送回频道，无气泡）。
        """
        agent_id, user_id, session_id = mp_key
        report = []
        if wake_channel == "console":
            # 1) 通知气泡（进程内直写 console push store，用户侧可见）
            try:
                from qwenpaw.app.console_push_store import (
                    append as push_append,
                )

                await push_append(session_id, text, sticky=True)
                report.append("气泡✅")
            except Exception as e:  # noqa: BLE001
                logger.debug("process-tools 通知气泡投递失败: %s", e)
                report.append(f"气泡❌({type(e).__name__})")
            # 2) 唤醒 agent（同 submit_to_agent 的官方路径，固定执行）
            ok, reason = await self._try_wake(
                agent_id, user_id, session_id, text,
            )
            if ok:
                report.append("唤醒✅")
            else:
                logger.warning(
                    "process-tools 唤醒投递失败: %s", reason,
                )
                report.append(f"唤醒❌({reason})")
        else:
            # 非 console：信使路由（agent 跑一轮 + 回复送回频道）。
            # console_push_store 是死信（该 session 无网页消费），不写。
            ok, reason = await self._wake_via_messenger(
                agent_id, user_id, session_id, wake_channel, text,
            )
            if ok:
                report.append("IM唤醒✅")
            else:
                logger.warning(
                    "process-tools IM 信使唤醒失败: %s", reason,
                )
                report.append(f"IM唤醒❌({reason})")
        return " ".join(report)

    async def _send_completion(self, mp: "ManagedProcess", notice: Notice) -> None:
        if notice.completion_sent:
            return
        notice.completion_sent = True
        text = self.build_completion_text(mp)
        result = await self.deliver(
            mp.key, text, notice.wake_channel,
        )
        logger.info("proc #%d 完成通知已投递：%s", mp.num, result)

    async def _try_wake(
        self, agent_id: str, user_id: str, session_id: str, text: str,
    ) -> tuple:
        """通过本地 API 提交后台 chat task 唤醒 agent；忙则延后重试。

        Returns:
            (ok, reason)：失败时 reason 为具体死因（异常类型/响应错误/
            重试耗尽），成功时为 ""。忙(409)不算死因，只算等待。
        """
        reason = ""
        for _attempt in range(WAKE_MAX_RETRIES):
            try:
                submitted = await asyncio.to_thread(
                    self._submit_wake_task,
                    agent_id, user_id, session_id, text,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("process-tools 唤醒提交异常: %s", e)
                return False, f"{type(e).__name__}: {e}"
            if submitted.get("ok"):
                return True, ""
            if submitted.get("conflict"):
                # 会话正忙：延后重试（设计文档「忙碌时排队」的近似实现）
                reason = f"会话忙，重试 {WAKE_MAX_RETRIES} 次(约 10 分钟)后仍未成功"
                await asyncio.sleep(WAKE_RETRY_SECONDS)
                continue
            return False, str(submitted.get("error") or "未知失败")
        return False, reason

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
    # ── IM 信使（非 console 频道，0.5.0）──

    async def _wake_via_messenger(
        self,
        agent_id: str,
        user_id: str,
        session_id: str,
        channel: str,
        text: str,
    ) -> tuple:
        """经插件信使端点唤醒 agent 并把回复送回频道；忙则延后重试。

        端点内 agent 回合同步执行（上限 300s），HTTP 超时给足余量。
        409（会话忙）与 console 唤醒同款 30s×20 重试。

        Returns:
            (ok, reason)：成功时 reason 为 ""。
        """
        reason = ""
        for _attempt in range(WAKE_MAX_RETRIES):
            try:
                submitted = await asyncio.to_thread(
                    self._submit_messenger_task,
                    agent_id, user_id, session_id, channel, text,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("process-tools 信使提交异常: %s", e)
                return False, f"{type(e).__name__}: {e}"
            if submitted.get("ok"):
                return True, ""
            if submitted.get("busy"):
                reason = (
                    f"会话忙，重试 {WAKE_MAX_RETRIES} 次"
                    "(约 10 分钟)后仍未成功"
                )
                await asyncio.sleep(WAKE_RETRY_SECONDS)
                continue
            return False, str(submitted.get("error") or "未知失败")
        return False, reason

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
