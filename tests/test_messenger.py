# -*- coding: utf-8 -*-
"""信使路由（messenger.run_wake）单测——0.5.0 非 console 通知投递。

核心保证：
- agent 回合跑在注册频道的三元组上（req 形态 = cron executor 同款）；
- 回复事件经 channel_manager.send_event 送回频道；
- 忙检（task_tracker running）在 stream_query 之前短路 → busy；
- 频道未配置在 agent 回合之前短路 → 分类报错；
- send_event/超时异常不吞：带 agent_done 语义返回。
"""

from types import SimpleNamespace

import pytest

import messenger


# ── fakes ──


class FakeChannelManager:
    def __init__(self, configured=("wecom",)):
        self._configured = set(configured)
        self.sent = []

    async def get_channel(self, name):
        return object() if name in self._configured else None

    async def send_event(self, *, channel, user_id, session_id, event,
                         meta=None):
        self.sent.append(
            {
                "channel": channel,
                "user_id": user_id,
                "session_id": session_id,
                "event": event,
                "meta": meta,
            },
        )


class FakeChatManager:
    def __init__(self):
        self.calls = []

    async def get_or_create_chat(self, *, session_id, user_id, channel):
        self.calls.append((session_id, user_id, channel))
        return SimpleNamespace(id="chat-1")


class FakeTracker:
    def __init__(self, status="idle"):
        self._status = status
        self.queries = []

    async def get_status(self, run_key):
        self.queries.append(run_key)
        return self._status


def make_workspace(events, *, channel_manager=None, tracker=None):
    """构造带 stream_query 的假 workspace；stream_query 记录收到的 req。"""
    queries = []

    async def stream_query(req):
        queries.append(req)
        for ev in events:
            yield ev

    ws = SimpleNamespace(
        channel_manager=channel_manager or FakeChannelManager(),
        chat_manager=FakeChatManager(),
        tracker=FakeTracker() if tracker is None else tracker,
    )
    # 属性名对齐内核：workspace.task_tracker
    ws.task_tracker = ws.tracker
    ws.stream_query = stream_query
    ws.queries = queries
    return ws


def ev(object_name="message", status="completed"):
    return SimpleNamespace(object=object_name, status=status)


WAKE_KW = dict(
    channel="wecom",
    user_id="LongXiaDuGong",
    session_id="wecom:LongXiaDuGong",
    text="通知正文",
)


def run(coro):
    return __import__("asyncio").run(coro)


# ── 1) 全成功：req 形态 + send_event 全事件转发 ──


def test_run_wake_success_full_flow():
    events = [ev("tool"), ev("message", "completed")]
    cm = FakeChannelManager()
    ws = make_workspace(events, channel_manager=cm)

    out = run(messenger.run_wake(ws, **WAKE_KW))

    assert out["ok"] is True and out["chat_id"] == "chat-1"
    # 忙检在回合前，run_key=chat.id
    assert ws.task_tracker.queries == ["chat-1"]
    # 每个事件都转发给 send_event（过滤是基类职责），参数保真
    assert [s["event"] for s in cm.sent] == events
    for s in cm.sent:
        assert s["channel"] == "wecom"
        assert s["user_id"] == "LongXiaDuGong"
        assert s["session_id"] == "wecom:LongXiaDuGong"
        assert s["meta"]["suppress_console_push"] is True
    # req 形态 = cron executor 同款
    req = ws.queries[0]
    assert req["channel"] == "wecom"
    assert req["user_id"] == "LongXiaDuGong"
    assert req["session_id"] == "wecom:LongXiaDuGong"
    assert req["input"][0]["role"] == "user"
    assert "通知正文" in req["input"][0]["content"][0]["text"]
    assert req["request_context"]["source"] == "process_tools_notice"
    assert req["request_context"]["suppress_console_push"] is True


def test_run_wake_empty_user_falls_back_default():
    ws = make_workspace([ev()])
    kw = dict(WAKE_KW, user_id="")
    out = run(messenger.run_wake(ws, **kw))
    assert out["ok"] is True
    assert ws.queries[0]["user_id"] == "default"


# ── 2) 短路分支：忙 / 频道未配置 ──


def test_run_wake_busy_short_circuits():
    ws = make_workspace(
        [ev()], tracker=FakeTracker(status="running"),
    )
    out = run(messenger.run_wake(ws, **WAKE_KW))
    assert out == {"ok": False, "busy": True}
    assert ws.queries == [], "忙时不得启动 agent 回合"
    assert ws.chat_manager.calls, "忙检需要先拿到 chat.id"


def test_run_wake_channel_missing_short_circuits():
    cm = FakeChannelManager(configured=())  # 什么频道都没配
    ws = make_workspace([ev()], channel_manager=cm)
    out = run(messenger.run_wake(ws, **WAKE_KW))
    assert out["ok"] is False
    assert "频道未配置" in out["error"] and "wecom" in out["error"]
    assert ws.queries == [], "频道缺失不得空跑 agent 回合"


# ── 3) 失败语义：send_event 异常 / 超时 ──


def test_run_wake_send_event_error_carries_agent_done():
    events = [ev()]
    cm = FakeChannelManager()

    async def boom(**kwargs):
        raise RuntimeError("WS 断了")

    cm.send_event = boom
    ws = make_workspace(events, channel_manager=cm)

    out = run(messenger.run_wake(ws, **WAKE_KW))
    assert out["ok"] is False
    assert out["agent_done"] is True, "事件已产出=回合已启动"
    assert "RuntimeError" in out["error"]


def test_run_wake_before_first_event_error_no_agent_done():
    async def broken_stream(req):
        raise ValueError("模型挂了")
        yield  # pragma: no cover

    ws = make_workspace([])
    ws.stream_query = broken_stream
    out = run(messenger.run_wake(ws, **WAKE_KW))
    assert out["ok"] is False
    assert "agent_done" not in out, "首个事件前失败≠回合已启动"
    assert "ValueError" in out["error"]


def test_run_wake_timeout(monkeypatch):
    import asyncio

    monkeypatch.setattr(messenger, "WAKE_AGENT_TIMEOUT_SECONDS", 0.05)

    async def slow_stream(req):
        await asyncio.sleep(5)
        yield ev()  # pragma: no cover

    ws = make_workspace([])
    ws.stream_query = slow_stream
    out = run(messenger.run_wake(ws, **WAKE_KW))
    assert out["ok"] is False
    assert "超时" in out["error"]


# ── 4) 会话门闩：并发信使回合互斥（0.5.0 并发丢失 bug 回归钉死）──


def test_gate_busy_while_first_turn_running():
    """第一回合跑着时，同会话第二通知 → busy（不再并发 stream_query）。"""
    import asyncio

    messenger.reset_gates()
    release = asyncio.Event()

    async def slow_stream(req):
        await release.wait()  # 模拟 agent 回合进行中
        yield ev()  # pragma: no cover

    ws1 = make_workspace([])
    ws1.stream_query = slow_stream
    ws2 = make_workspace([ev()])

    async def main():
        t1 = asyncio.create_task(messenger.run_wake(ws1, **WAKE_KW))
        await asyncio.sleep(0.05)  # 让 t1 拿到 gate 并挂进 stream_query
        out2 = await messenger.run_wake(ws2, **WAKE_KW)
        release.set()
        out1 = await t1
        return out1, out2

    out1, out2 = run(main())
    assert out1["ok"] is True
    assert out2 == {"ok": False, "busy": True}, "同会话并发必须被 gate 挡下"
    assert ws2.queries == [], "busy 不得启动第二个 stream_query"


def test_gate_released_after_completion_and_failure():
    """回合完成与失败后 gate 必须释放（finally 语义），后续通知可再入。"""
    messenger.reset_gates()

    ws_ok = make_workspace([ev()])
    assert run(messenger.run_wake(ws_ok, **WAKE_KW))["ok"] is True
    assert run(messenger.run_wake(ws_ok, **WAKE_KW))["ok"] is True

    # 失败路径（send_event 炸）后 gate 也得释放
    messenger.reset_gates()
    cm = FakeChannelManager()
    cm.send_event = _boom
    ws_fail = make_workspace([ev()], channel_manager=cm)
    out = run(messenger.run_wake(ws_fail, **WAKE_KW))
    assert out["ok"] is False
    assert run(messenger.run_wake(ws_ok, **WAKE_KW))["ok"] is True


async def _boom(**kwargs):
    raise RuntimeError("WS 断了")


def test_gate_scoped_per_session_key():
    """不同 agent/频道/会话互不阻塞（键含四元组）。"""
    import asyncio

    messenger.reset_gates()
    release = asyncio.Event()

    async def slow_stream(req):
        await release.wait()
        yield ev()  # pragma: no cover

    ws1 = make_workspace([])
    ws1.stream_query = slow_stream
    ws_other = make_workspace([ev()])
    ws_other.agent_id = "agent-B"

    async def main():
        t1 = asyncio.create_task(messenger.run_wake(ws1, **WAKE_KW))
        await asyncio.sleep(0.05)
        # 同频道不同 session_id（+不同 agent）→ gate 键不同 → 不阻塞
        other_kw = dict(WAKE_KW, session_id="wecom:OtherUser")
        out2 = await messenger.run_wake(ws_other, **other_kw)
        release.set()
        await t1
        return out2

    out2 = run(main())
    assert out2["ok"] is True, "不同会话键不得互相阻塞"


# ── 5) 路由壳：参数校验 + busy→409（fastapi 可用才测）──


def test_router_rejects_missing_fields():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient  # noqa: F401
    from fastapi import APIRouter, FastAPI

    # 0.5.0 修复：信使端点并入既有 router（add_wake_route），
    # 内核约束插件 HTTP prefix 插件级唯一，禁止独立注册
    router = APIRouter()
    messenger.add_wake_route(router)
    app = FastAPI()
    app.include_router(router, prefix="/process-tools")
    client = TestClient(app)

    resp = client.post(
        "/process-tools/wake-channel",
        json={"channel": "wecom", "session_id": "", "text": "x"},
    )
    assert resp.status_code == 400


def test_web_api_router_contains_wake_channel():
    """web_api.create_router() 单 router 双端点：chat-status + wake-channel。"""
    pytest.importorskip("fastapi")
    import web_api

    router = web_api.create_router()
    paths = {r.path for r in router.routes}
    assert "/chat-status" in paths
    assert "/wake-channel" in paths
