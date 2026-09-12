# -*- coding: utf-8 -*-
"""唤醒投递回归测试（v0.1.2 幽灵会话 bug 钉死）。

背景（实机踩坑）：/console/chat/task 端点用
``(session_id, user_id, channel)`` 三元组**全等**匹配 chat，找不到就
``get_or_create_chat`` 静默新建。v0.1.1 的 payload 把 user_id 写死为
"main"，与 console 会话真实 user_id（如 "default"）错配，导致完成通知
唤醒投递到一个凭空创建的幽灵 chat，而不是注册通知的原会话。
"""

import asyncio

import pytest

import notifier as notifier_mod
import utils as utils_mod
from helpers import chunk_text, is_error, py_cmd, run
from manager import get_manager
from tools.notice import process_tools_notice
from tools.exec import process_tools_exec

Notifier = notifier_mod.Notifier


# ── 1) payload 保真：真实 user_id/session_id，绝不再写死 "main" ──


def test_submit_wake_task_payload_uses_real_user():
    am = pytest.importorskip(
        "qwenpaw.agents.tools.agent_management",
        reason="唤醒 kit 依赖 QwenPaw 内核",
    )
    captured = {}

    def fake_submit(base_url, request_payload, to_agent=None, timeout=None,
                    **kwargs):
        captured["payload"] = request_payload
        captured["to_agent"] = to_agent
        return {"task_id": "task-fake-1"}

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(am, "submit_agent_chat_task", fake_submit)
        monkey.setattr(am, "resolve_agent_api_base_url",
                      lambda: "http://127.0.0.1:9")
        out = Notifier._submit_wake_task(
            "agent-A", "user-real", "sess-1", "正文",
        )
    finally:
        monkey.undo()
    assert out == {"ok": True, "task_id": "task-fake-1"}
    payload = captured["payload"]
    assert payload["user_id"] == "user-real", "user_id 必须来自会话真实维度"
    assert payload["user_id"] != "main", "v0.1.1 写死 main 是幽灵会话根因"
    assert payload["session_id"] == "sess-1"
    assert payload["channel"] == "console"
    assert captured["to_agent"] == "agent-A"


def test_submit_wake_task_empty_user_falls_back_default():
    am = pytest.importorskip("qwenpaw.agents.tools.agent_management")
    captured = {}

    def fake_submit(base_url, request_payload, to_agent=None, timeout=None,
                    **kwargs):
        captured["payload"] = request_payload
        return {"task_id": "t"}

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(am, "submit_agent_chat_task", fake_submit)
        monkey.setattr(am, "resolve_agent_api_base_url",
                      lambda: "http://127.0.0.1:9")
        Notifier._submit_wake_task("a", "", "s", "正文")
    finally:
        monkey.undo()
    assert captured["payload"]["user_id"] == "default"


# ── 1b) 信使提交（0.5.0 非 console 通道）：payload 保真 + 409/错误映射 ──


def test_submit_messenger_task_payload_and_header():
    """信使提交：真实三元组进 payload；X-Agent-Id 携带注册 agent 维度。"""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("qwenpaw.agents.tools.agent_management")

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None, **kw):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResp()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(httpx, "Client", FakeClient)
        monkey.setattr(
            "qwenpaw.agents.tools.agent_management."
            "resolve_agent_api_base_url",
            lambda: "http://127.0.0.1:9",
        )
        out = Notifier._submit_messenger_task(
            "agent-A", "user-real", "wecom:user-real", "wecom", "正文",
        )
    finally:
        monkey.undo()
    assert out == {"ok": True}
    assert captured["url"].endswith("/api/process-tools/wake-channel")
    payload = captured["payload"]
    assert payload == {
        "channel": "wecom",
        "user_id": "user-real",
        "session_id": "wecom:user-real",
        "text": "正文",
    }
    assert captured["headers"]["X-Agent-Id"] == "agent-A"


def test_submit_messenger_task_maps_409_and_error():
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("qwenpaw.agents.tools.agent_management")

    class FakeResp:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.text = str(body)

        def json(self):
            if isinstance(self._body, dict):
                return self._body
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, responder):
            self._responder = responder

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            return self._responder()

    responses = iter([
        FakeResp(409, {"detail": "busy"}),
        FakeResp(404, {"detail": "频道未配置: wecom"}),
    ])
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(
            httpx, "Client", lambda *a, **kw: FakeClient(
                lambda: next(responses),
            ),
        )
        monkey.setattr(
            "qwenpaw.agents.tools.agent_management."
            "resolve_agent_api_base_url",
            lambda: "http://127.0.0.1:9",
        )
        busy = Notifier._submit_messenger_task("a", "u", "s", "wecom", "t")
        err = Notifier._submit_messenger_task("a", "u", "s", "wecom", "t")
    finally:
        monkey.undo()
    assert busy == {"busy": True}, "409 必须映射为 busy（触发重试）"
    assert err["ok"] is False
    assert "频道未配置" in err["error"]


def test_wake_via_messenger_busy_then_success(monkeypatch):
    """信使 409 → 延后重试，成功即返回（重试间隔压缩到毫秒级）。"""
    nt = Notifier()
    calls = []

    async def fake_to_thread(fn, *args):
        calls.append(1)
        if len(calls) < 2:
            return {"busy": True}
        return {"ok": True}

    monkeypatch.setattr(notifier_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(notifier_mod, "WAKE_RETRY_SECONDS", 0.001)

    async def main():
        return await nt._wake_via_messenger(
            "a", "u", "s", "wecom", "正文",
        )

    ok, reason = run(main())
    assert ok is True and reason == ""
    assert len(calls) == 2


def test_wake_via_messenger_error_no_retry(monkeypatch):
    nt = Notifier()
    calls = []

    async def fake_to_thread(fn, *args):
        calls.append(1)
        return {"ok": False, "error": "500: 内部错误"}

    monkeypatch.setattr(notifier_mod.asyncio, "to_thread", fake_to_thread)

    async def main():
        return await nt._wake_via_messenger(
            "a", "u", "s", "wecom", "正文",
        )

    ok, reason = run(main())
    assert ok is False
    assert reason == "500: 内部错误"
    assert len(calls) == 1, "非冲突错误不应触发重试"



# ── 2) deliver 双投递语义：气泡按 session_id；唤醒仅 console 且带真实 user ──


def test_deliver_wake_routing_and_channel_guard(monkeypatch):
    cps = pytest.importorskip("qwenpaw.app.console_push_store")

    pushed = []

    async def fake_append(session_id, text, *, sticky=False):
        pushed.append((session_id, sticky))

    woken = []

    async def fake_wake(self, agent_id, user_id, session_id, text):
        woken.append((agent_id, user_id, session_id))
        return True, ""

    messengered = []

    async def fake_messenger(
        self, agent_id, user_id, session_id, channel, text,
    ):
        messengered.append((channel, agent_id, user_id, session_id))
        return True, ""

    monkeypatch.setattr(cps, "append", fake_append)
    monkeypatch.setattr(Notifier, "_try_wake", fake_wake)
    monkeypatch.setattr(Notifier, "_wake_via_messenger", fake_messenger)

    nt = Notifier()
    key = ("agent-1", "user-x", "sess-9")

    # 非 console：信使路由（0.5.0），不写死信气泡，不走 console 唤醒
    r = run(nt.deliver(key, "正文", wake_channel="matrix"))
    assert woken == []
    assert pushed == [], "console_push_store 对非 console 是死信，不写"
    assert messengered == [("matrix", "agent-1", "user-x", "sess-9")]
    assert "IM唤醒✅" in r and "气泡" not in r

    # console：气泡 + 唤醒固定双投递，带真实 (agent_id, user_id, session_id)
    r = run(nt.deliver(key, "正文2", wake_channel="console"))
    assert woken == [("agent-1", "user-x", "sess-9")]
    assert pushed == [("sess-9", True)]
    assert "唤醒✅" in r and "气泡✅" in r
    assert messengered.__len__() == 1, "console 不走信使"


def test_deliver_surfaces_messenger_failure_reason(monkeypatch):
    """非 console 信使失败：reason 必须进报告（与 console 唤醒同语义）。"""
    async def fake_messenger_fail(
        self, agent_id, user_id, session_id, channel, text,
    ):
        return False, "频道未配置: wecom"

    monkeypatch.setattr(
        Notifier, "_wake_via_messenger", fake_messenger_fail,
    )

    nt = Notifier()
    r = run(nt.deliver(("a", "u", "s"), "正文", wake_channel="wecom"))
    assert "IM唤醒❌(频道未配置: wecom)" in r


def test_deliver_surfaces_wake_failure_reason(monkeypatch):
    """0.4.0 实链路发现裸「唤醒❌」无法定位死因——reason 必须进报告。"""
    pytest.importorskip("qwenpaw.app.console_push_store")

    async def fake_append(session_id, text, *, sticky=False):
        return None

    async def fake_wake_fail(self, agent_id, user_id, session_id, text):
        return False, "RuntimeError: 连接被拒绝"

    monkeypatch.setattr(
        "qwenpaw.app.console_push_store.append", fake_append,
    )
    monkeypatch.setattr(Notifier, "_try_wake", fake_wake_fail)

    nt = Notifier()
    r = run(nt.deliver(("a", "u", "s"), "正文", wake_channel="console"))
    assert "唤醒❌(RuntimeError: 连接被拒绝)" in r
    assert "气泡✅" in r


def test_try_wake_exception_reason_tuple(monkeypatch):
    """_try_wake 异常路径：返回 (False, 类型: 消息) 而非裸 False。"""
    nt = Notifier()

    def boom(*args, **kwargs):
        raise RuntimeError("模拟挂掉")

    monkeypatch.setattr(nt, "_submit_wake_task", boom)
    ok, reason = run(nt._try_wake("a", "u", "s", "正文"))
    assert ok is False
    assert "RuntimeError" in reason and "模拟挂掉" in reason


def test_try_wake_error_passthrough(monkeypatch):
    """非 409 错误响应：error 文本原样进 reason，且不重试不睡眠。"""
    nt = Notifier()
    calls = []

    def fake_submit(*args, **kwargs):
        calls.append(1)
        return {"ok": False, "error": "404: 端点不存在"}

    monkeypatch.setattr(nt, "_submit_wake_task", fake_submit)
    ok, reason = run(nt._try_wake("a", "u", "s", "正文"))
    assert ok is False
    assert reason == "404: 端点不存在"
    assert len(calls) == 1, "非冲突错误不应触发重试"


# ── 3) notice 工具接线：频道快照贯穿注册；已结束进程改报错引导 wait ──


def test_notice_snapshots_channel_for_running_process(monkeypatch):
    import tools.notice as notice_tool

    monkeypatch.setattr(notice_tool, "current_channel", lambda: "wecom")

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c)
        stored = notifier_mod.get_notifier().get(mp.key, 1)
        assert stored.wake_channel == "wecom", "注册时应快照 contextvar 频道"
        await mp.kill()

    run(main())


def test_notice_immediate_path_retired_no_delivery(monkeypatch):
    """0.4.2 设计：已结束进程 notice → error，不投递任何东西
    （旧立即投递路径会撞前台 run 的会话忙自锁，唤醒 10 分钟空转）。"""
    import tools.notice as notice_tool

    calls = []

    async def fake_deliver(self, mp_key, text, wake_channel="console"):
        calls.append(wake_channel)
        return "记录✅"

    monkeypatch.setattr(notice_tool, "current_channel", lambda: "telegram")
    monkeypatch.setattr(Notifier, "deliver", fake_deliver)

    async def main():
        await process_tools_exec(py_cmd("print('chan')"), background=True)
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        c = await process_tools_notice(1)
        assert is_error(c) and "check" in chunk_text(c)
        assert calls == [], "已结束进程不得触发投递"

    run(main())


# ── 4) utils.current_channel 兜底 ──


def test_current_channel_fallback_default_console(monkeypatch):
    # 无请求上下文（contextvar 未设值/内核不可用）→ 回落 console
    assert utils_mod.current_channel() == "console"

    # contextvar 可设值时透传真实频道
    ac = pytest.importorskip("qwenpaw.app.agent_context")
    monkeypatch.setattr(
        ac, "get_current_channel", lambda: "dingtalk", raising=True,
    )
    assert utils_mod.current_channel() == "dingtalk"
