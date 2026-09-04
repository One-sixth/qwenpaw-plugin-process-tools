# -*- coding: utf-8 -*-
"""通知系统：注册制 + 完成/周期通知 + 双投递（气泡 / 唤醒）。

沿用《AI_MED_UI 进程工具设计》§4.6 的核心语义：

- **opt-in**：后台进程不自动推送任何东西，必须显式 notice 注册；
- **完成通知幂等**：`completion_sent` 标记防重复；
- **周期通知**：interval_seconds ≥ 30 才生效，进程退出即取消；
- **同级同路**：通知以「用户消息」级别送达——
  1. 通知气泡：``console_push_store.append(session_id, text, sticky=True)``
     （QwenPaw 前端 2.5s 轮询展示，用户可见）；
  2. 唤醒 agent：POST ``/api/console/chat/task`` 后台任务端点
     （复用内核 ``agents.tools.agent_management`` 官方客户端 kit，
     与 ``submit_to_agent`` 同路；会话忙碌返回 409 → 延后重试）。

跨 channel（非 console）会话的唤醒是 best-effort：失败时气泡与
通知记录仍然保留，不阻塞调用方。
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


@dataclass
class Notice:
    """一个进程的通知注册记录。"""

    process_num: int
    session_key: tuple
    interval_seconds: int = 0  # 0 = 仅完成通知
    wake_agent: bool = True
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
        head = (
            f"[进程 #{mp.num} {state_cn}]"
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
        return (
            f"[进程 #{mp.num} 运行中] 已运行 {mins}m{secs:02d}s\n"
            f"命令：{truncate_line(mp.command, 120)}\n"
            f"输出末尾：\n{tail}"
        )

    # ── 双投递 ──

    async def deliver(self, mp_key: tuple, text: str, wake_agent: bool) -> str:
        """把通知投出去，返回投递情况描述。

        mp_key = (agent_id, user_id, session_id)
        """
        agent_id, _user_id, session_id = mp_key
        report = []
        # 1) 通知气泡（进程内直写 console push store，用户侧可见）
        try:
            from qwenpaw.app.console_push_store import append as push_append

            await push_append(session_id, text, sticky=True)
            report.append("气泡✅")
        except Exception as e:  # noqa: BLE001
            logger.debug("process-tools 通知气泡投递失败: %s", e)
            report.append(f"气泡❌({type(e).__name__})")
        # 2) 唤醒 agent（同 submit_to_agent 的官方路径）
        if wake_agent:
            ok = await self._try_wake(agent_id, session_id, text)
            report.append("唤醒✅" if ok else "唤醒❌")
        return " ".join(report)

    async def _send_completion(self, mp: "ManagedProcess", notice: Notice) -> None:
        if notice.completion_sent:
            return
        notice.completion_sent = True
        text = self.build_completion_text(mp)
        result = await self.deliver(mp.key, text, notice.wake_agent)
        logger.info("proc #%d 完成通知已投递：%s", mp.num, result)

    async def _try_wake(self, agent_id: str, session_id: str, text: str) -> bool:
        """通过本地 API 提交后台 chat task 唤醒 agent；忙则延后重试。"""
        for _attempt in range(WAKE_MAX_RETRIES):
            try:
                submitted = await asyncio.to_thread(
                    self._submit_wake_task, agent_id, session_id, text,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("process-tools 唤醒提交异常: %s", e)
                return False
            if submitted.get("ok"):
                return True
            if submitted.get("conflict"):
                # 会话正忙：延后重试（设计文档「忙碌时排队」的近似实现）
                await asyncio.sleep(WAKE_RETRY_SECONDS)
                continue
            return False
        return False

    @staticmethod
    def _submit_wake_task(agent_id: str, session_id: str, text: str) -> dict:
        """同步提交唤醒任务（放 to_thread 跑）。

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
            "user_id": "main",
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

    # ── 周期通知 ──

    def _start_periodic(self, mp: "ManagedProcess", notice: Notice) -> None:
        async def _loop() -> None:
            try:
                while mp.status == "running":
                    await asyncio.sleep(notice.interval_seconds)
                    if mp.status != "running":
                        break
                    text = self.build_periodic_text(mp)
                    await self.deliver(mp.key, text, notice.wake_agent)
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
