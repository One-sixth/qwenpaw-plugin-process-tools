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

    monkeypatch.setattr(cps, "append", fake_append)
    monkeypatch.setattr(Notifier, "_try_wake", fake_wake)

    nt = Notifier()
    key = ("agent-1", "user-x", "sess-9")

    # 非 console：跳过唤醒但气泡仍投（session_id 定位不受影响）
    r = run(nt.deliver(key, "正文", wake_channel="matrix"))
    assert woken == []
    assert "matrix" in r and "唤醒⏭️" in r and "气泡✅" in r
    assert pushed == [("sess-9", True)]

    # console：唤醒带真实 (agent_id, user_id, session_id) 三元组
    # （0.4.4 起气泡+唤醒固定双投递，无 wake_agent 开关）
    r = run(nt.deliver(key, "正文2", wake_channel="console"))
    assert woken == [("agent-1", "user-x", "sess-9")]
    assert "唤醒✅" in r


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
