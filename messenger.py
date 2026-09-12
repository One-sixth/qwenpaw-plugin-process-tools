# -*- coding: utf-8 -*-
"""messenger — 非 console 频道的通知信使（agent 回合 + 回复送达 IM）。

背景（0.4.4 止）：非 console 会话（wecom/dingtalk/feishu/qq…）注册的
通知完全静默丢失——气泡写 console_push_store 是死信（该 session 无网页
消费）；/console/chat/task 唤醒被守卫跳过（v0.1.2：三元组错配会凭空
造幽灵会话）。方法 1 实测（0.5.0 前调查）：chat/task 的 payload
``channel`` 字段可透传真实频道名，三元组全等命中现有会话、agent 正常
跑——但回复只落 session 文件（stream_one 无频道路由），IM 收不到。

0.5.0 信使路由 = cron agent job 同款链路的插件化单点实现：

    handler（get_agent_for_request 拿 workspace）
      1) channel_manager.get_channel 预检（未配置 → 分类报错）
      2) chat_manager.get_or_create_chat 幂等取 chat（三元组全等，
         快照来自注册时 contextvar，真实会话必命中）
      3) task_tracker.get_status(chat.id) 忙检（running → 409，
         通知侧复用 30s×20 重试）
      4) workspace.stream_query(req) 跑一轮真实 agent 回合
         （cron executor 同款 dict 形态；user 消息与回复落 session
         文件，WebUI 可见）
      5) channel_manager.send_event(channel, user_id, session_id,
         event) 把完成的 assistant message 送回 IM——基类只放行
         message+Completed（base.py:2302）；wecom 底层走 aibot WS
         SEND_MSG 主动推送（无需回调帧，sessionWebhook 过期问题
         不存在）

  超时：单次 agent 回合上限 ``WAKE_AGENT_TIMEOUT_SECONDS``（wait_for
  到点取消 stream），HTTP 侧超时须大于它。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# agent 回合上限（含模型推理 + 工具调用）；HTTP 侧超时须大于此值
WAKE_AGENT_TIMEOUT_SECONDS = 300


async def run_wake(
    workspace,
    *,
    channel: str,
    user_id: str,
    session_id: str,
    text: str,
) -> dict:
    """跑一轮 agent 回合并把回复事件送回频道。

    Returns:
        {"ok": True, "chat_id": ...}
        {"ok": False, "busy": True}                          会话忙 → 通知侧重试
        {"ok": False, "error": "频道未配置: xxx"}
        {"ok": False, "error": "agent 回合超时(...)"}
        {"ok": False, "error": "...", "agent_done": True}    回合已启动但中途失败
    """
    channel_manager = getattr(workspace, "channel_manager", None)
    chat_manager = getattr(workspace, "chat_manager", None)
    if channel_manager is None or chat_manager is None:
        return {"ok": False, "error": "workspace 缺少 channel/chat manager"}

    # 1) 频道预检（未配置提前分类报错，不空跑 agent 回合）
    ch = await channel_manager.get_channel(channel)
    if ch is None:
        return {"ok": False, "error": f"频道未配置: {channel}"}

    # 2) 幂等取 chat（三元组全等；真实会话必命中，仅会话被删的边缘才新建，
    #    与 cron executor 同语义）
    chat = await chat_manager.get_or_create_chat(
        session_id=session_id,
        user_id=user_id,
        channel=channel,
    )

    # 3) 忙检（run_key = chat.id，与 console task_tracker 同键）
    tracker = getattr(workspace, "task_tracker", None)
    if tracker is not None:
        status = await tracker.get_status(chat.id)
        if status == "running":
            return {"ok": False, "busy": True}

    # 4+5) agent 回合 + 事件送频道（cron executor 同款 dict 形态）
    prompt = (
        "【后台进程通知】以下托管进程状态发生变化，请查看并按需处理"
        "（可用 process_tools_check / process_tools_list 获取详情；"
        "你的回复会直接送达用户手机，请保持简洁）：\n\n"
        f"{text}"
    )
    req = {
        "channel": channel,
        "user_id": user_id or "default",
        "session_id": session_id,
        "input": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        ],
        # suppress_console_push：cron agent job 同款——非 console 会话
        # 不写 console_push_store（0.4.x 时代的死信路径，0.5.0 废弃）
        "request_context": {
            "source": "process_tools_notice",
            "suppress_console_push": True,
        },
    }
    meta = {"suppress_console_push": True}

    agent_done = False

    async def _run() -> None:
        nonlocal agent_done
        async for event in workspace.stream_query(req):
            agent_done = True
            # 基类只发 message+Completed；其余事件静默跳过
            await channel_manager.send_event(
                channel=channel,
                user_id=user_id,
                session_id=session_id,
                event=event,
                meta=meta,
            )

    try:
        await asyncio.wait_for(
            _run(), timeout=WAKE_AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        note = (
            "agent 回合超时(>" f"{WAKE_AGENT_TIMEOUT_SECONDS}s)"
        )
        if agent_done:
            return {"ok": False, "error": note, "agent_done": True}
        return {"ok": False, "error": note}
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if agent_done:
            result["agent_done"] = True
        return result
    return {"ok": True, "chat_id": chat.id}


def create_router():
    """构造信使路由（延迟 import，无 fastapi 环境不破坏模块加载）。"""
    from fastapi import APIRouter, HTTPException, Request

    from qwenpaw.app.agent_context import get_agent_for_request

    router = APIRouter()

    @router.post("/wake-channel")
    async def wake_channel(request: Request, body: dict) -> dict:
        """通知信使：唤醒 agent 跑一轮并把回复送回注册频道。

        Body: {channel, user_id, session_id, text}
        忙碌 → HTTP 409（通知侧复用 30s×20 重试）。
        """
        channel = (body.get("channel") or "").strip()
        user_id = (body.get("user_id") or "").strip()
        session_id = (body.get("session_id") or "").strip()
        text = body.get("text") or ""
        if not (channel and session_id and text):
            raise HTTPException(
                status_code=400,
                detail="channel / session_id / text 必填",
            )

        # X-Agent-Id header：通知侧携带注册时的 agent 维度，
        # 多 agent 场景精确路由回注册会话所属 workspace
        workspace = await get_agent_for_request(request)
        result = await run_wake(
            workspace,
            channel=channel,
            user_id=user_id,
            session_id=session_id,
            text=text,
        )
        if result.get("busy"):
            raise HTTPException(
                status_code=409,
                detail="A task is already running for this chat.",
            )
        return result

    return router
