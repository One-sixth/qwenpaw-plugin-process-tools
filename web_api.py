# -*- coding: utf-8 -*-
"""web_api — HTTP 轻量端点：会话实时活动状态（前端及时刷新伴生）。

背景：QwenPaw 前端主对话气泡区在同一会话内永不自动重拉（SessionLoader
仅切换会话触发）。notice 唤醒等后台任务在会话里生成回复时，页面看不到
任何动静，用户要手动刷新才发现。

0.3.2 判据（用户实测三轮迭代定稿）：
  · 后端活动：本端点返回 task_tracker 对该 chat 的实时 running/idle，
    外加 workspace 级 last_run_at 作为「run 身份键」（同一 run 期间稳定，
    新 run 必变）——前端据此保证**每个 run 至多刷一次**，根除连环闪。
  · 前端不知情：浏览器脚本检测发送按钮是否处于
    ``qwenpaw-sender-actions-btn-loading-button`` 态——该态从发出消息
    起覆盖整个 run（含等待吐字的思考空窗），是「页面已经知道并正在
    展示生成」的精确 UI 信号（MutationObserver 的 DOM 动静判据在
    等待吐字期会误判，0.3.1 实测被否）。
  两信号一与：后端 running ∧ 本页按钮非 loading → reload 接直播
  （SPA 冷启动进入 running 会话会原生 reconnect 到进行中的 SSE 流）。

路由挂载：``register_http_router(prefix="/process-tools")``
→ ``GET /api/process-tools/chat-status?chat_id=<chat UUID>``

run_key 即 chat.id（console.py ``tracker.attach_or_start(chat.id, …)``），
所以拿 chat UUID 直接查 tracker。
"""

import logging

logger = logging.getLogger(__name__)


async def chat_probe(request, chat_id: str) -> dict:
    """探测 {"status", "run_at"}；拿不到宿主状态时 status='unknown'。

    status: 'running' | 'idle' | 'unknown'
    run_at: workspace 最近一次 run 的启动时刻（epoch 秒，可 None）——
            同一 run 期间稳定，前端用作「本 run 已刷新」去重键。
    """
    if not chat_id:
        return {"status": "unknown", "run_at": None}
    try:
        from qwenpaw.app.agent_context import get_agent_for_request

        workspace = await get_agent_for_request(request)
        status = await workspace.task_tracker.get_status(chat_id)
        try:
            gstate = await workspace.task_tracker.get_global_status()
            last_run = gstate.get("last_run_at")
            run_at = (
                last_run.timestamp()
                if getattr(last_run, "timestamp", None)
                else None
            )
        except Exception:  # noqa: BLE001  run_at 是附属信号，坏不拖主
            run_at = None
        return {"status": status, "run_at": run_at}
    except Exception as e:  # noqa: BLE001
        logger.debug("process-tools web_api 查询 chat status 失败: %s", e)
        return {"status": "unknown", "run_at": None}


def create_router():
    """构造 FastAPI 路由（延迟 import，无 fastapi 环境不破坏模块加载）。

    0.5.0 起同 router 挂两个端点：/chat-status（会话指纹）+
    /wake-channel（IM 信使，messenger.add_wake_route）——内核约束
    插件 HTTP prefix 插件级唯一，禁止同一插件重复注册 router。
    """
    from fastapi import APIRouter, Request

    try:
        from .messenger import add_wake_route
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from messenger import add_wake_route

    router = APIRouter()

    @router.get("/chat-status")
    async def get_chat_status(request: Request, chat_id: str):
        """该 chat 后端是否正在生成 + run 身份键。"""
        probe = await chat_probe(request, chat_id)
        return {
            "chat_id": chat_id,
            "status": probe["status"],
            "run_at": probe["run_at"],
        }

    add_wake_route(router)
    return router
