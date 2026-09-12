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


# ── 4) 路由壳：参数校验 + busy→409（fastapi 可用才测）──


def test_router_rejects_missing_fields():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient  # noqa: F401
    from fastapi import FastAPI

    router = messenger.create_router()
    app = FastAPI()
    app.include_router(router, prefix="/process-tools")
    client = TestClient(app)

    resp = client.post(
        "/process-tools/wake-channel",
        json={"channel": "wecom", "session_id": "", "text": "x"},
    )
    assert resp.status_code == 400
