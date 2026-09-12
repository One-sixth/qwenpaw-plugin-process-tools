# -*- coding: utf-8 -*-
"""会话聚合器（0.6.0）核心行为回归。

钉死作者拍板的状态机：
- 首条通知启动 3s 聚合窗口（测试压缩为毫秒级）；
- 窗口到点会话忙 → 以窗口粒度忙等，**无上限、永不放弃**，期间新
  通知继续累积，重试时聚合文本包含新到的通知；
- 空闲时取走累积全部一口气投出（console=一气泡+一唤醒；IM=一回合）；
- 真失败（fail）消费本批不重试；
- console 气泡只在最终投递时发（busy 重试期间不重复刷气泡）；
- flusher 投递完退出，新通知懒启动（生命周期闭环）。
"""

import asyncio

import pytest

import notifier as notifier_mod
from notifier import Notifier

WINDOW = 0.01  # 聚合窗口压缩到毫秒级


def wait_done(nt, acc_key, rounds=800):
    """返回 async 等待 flusher 消费完并退出的协程。"""

    async def _main():
        acc = nt._accumulators.get(acc_key)
        assert acc is not None, "enqueue 必须建立累积器"
        for _ in range(rounds):
            if not acc.items and (
                acc.flusher_task is None or acc.flusher_task.done()
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("flusher 未在预期时间内完成")

    return _main()


def run(coro):
    return asyncio.run(coro)


def test_busy_waits_forever_and_accumulates(monkeypatch):
    """忙 → 忙等（窗口粒度）→ 期间新通知累积 → 重试批次含新通知。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", WINDOW)

    attempts = []

    async def flaky_ms_once(self, a, u, s, channel, text):
        attempts.append(text)
        if len(attempts) < 3:  # 前两次忙，第三次成功
            return "busy", "会话忙"
        return "ok", ""

    monkeypatch.setattr(Notifier, "_messenger_once", flaky_ms_once)

    nt = Notifier()
    key = ("a", "u", "sess-busy")
    acc_key = ("a", "u", "sess-busy", "wecom")

    async def main():
        await nt.deliver(key, "第一条", wake_channel="wecom")
        # 等 flusher 进入忙等（前两次尝试）
        for _ in range(50):
            if len(attempts) >= 1:
                break
            await asyncio.sleep(0.01)
        await nt.deliver(key, "第二条", wake_channel="wecom")  # 忙等期间入队
        await wait_done(nt, acc_key)

    run(main())
    assert len(attempts) == 3
    assert "第一条" in attempts[0] and "第二条" not in attempts[0], (
        "首投时第二条尚未入队"
    )
    assert "第一条" in attempts[1] and "第二条" in attempts[1], (
        "忙等期间新通知聚合进重试批次（作者设计语义）"
    )
    assert attempts[2] == attempts[1], "第三次尝试成功，批次一致"


def test_fail_consumes_batch_without_retry(monkeypatch):
    """真失败（频道未配置等）：消费本批、不重试、flusher 退出。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", WINDOW)

    attempts = []

    async def failing_ms_once(self, a, u, s, channel, text):
        attempts.append(text)
        return "fail", "IM唤醒❌(频道未配置: wecom)"

    monkeypatch.setattr(Notifier, "_messenger_once", failing_ms_once)

    nt = Notifier()
    key = ("a", "u", "sess-fail")
    acc_key = ("a", "u", "sess-fail", "wecom")

    async def main():
        await nt.deliver(key, "孤批通知", wake_channel="wecom")
        await wait_done(nt, acc_key)
        # flusher 已退出（items 空），再入队会懒启动新 flusher
        await nt.deliver(key, "第二批", wake_channel="wecom")
        await wait_done(nt, acc_key)

    run(main())
    assert len(attempts) == 2, "fail 不重试；新通知懒启动新批次"
    assert attempts[0] == "孤批通知"
    assert attempts[1] == "第二批"


def test_console_bubble_only_on_final_delivery(monkeypatch):
    """console：busy 重试期间不发气泡；最终成功才发一条聚合气泡。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", WINDOW)
    cps = pytest.importorskip("qwenpaw.app.console_push_store")

    pushed = []

    async def fake_push(session_id, text, *, sticky=False):
        pushed.append(text)

    attempts = []

    async def busy_then_ok(self, a, u, s, text):
        attempts.append(text)
        if len(attempts) < 2:
            return "busy", "会话忙"
        return "ok", ""

    monkeypatch.setattr(cps, "append", fake_push)
    monkeypatch.setattr(Notifier, "_wake_once", busy_then_ok)

    nt = Notifier()
    key = ("a", "u", "sess-bubble")
    acc_key = ("a", "u", "sess-bubble", "console")

    async def main():
        await nt.deliver(key, "通知A", wake_channel="console")
        for _ in range(50):
            if len(attempts) >= 1:
                break
            await asyncio.sleep(0.01)
        await nt.deliver(key, "通知B", wake_channel="console")
        await wait_done(nt, acc_key)

    run(main())
    assert len(pushed) == 1, "只有最终成功那次发气泡"
    assert "通知A" in pushed[0] and "通知B" in pushed[0]


def test_join_notice_text_shapes():
    """聚合文本：单条直通；多条带数量标头 + 分隔线。"""
    assert Notifier._join_notice_text(["单条"]) == "单条"
    joined = Notifier._join_notice_text(["A", "B", "C"])
    assert joined.startswith("【进程通知聚合 ×3】")
    assert "A" in joined and "B" in joined and "C" in joined
    assert joined.count("─" * 24) == 2  # 3 条之间 2 道分隔线


def test_flusher_exception_keeps_items_and_resets_task(monkeypatch):
    """flusher 异常路径：items 保留不丢、flusher_task 复位、
    新通知入队时懒启动的新 flusher 能把保留批次一并投出。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", WINDOW)

    attempts = []

    async def broken_then_ok(self, a, u, s, channel, text):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("信使通道瞬时崩坏")
        return "ok", ""

    monkeypatch.setattr(Notifier, "_messenger_once", broken_then_ok)

    nt = Notifier()
    key = ("a", "u", "sess-crash")
    acc_key = ("a", "u", "sess-crash", "wecom")

    async def main():
        await nt.deliver(key, "幸存通知", wake_channel="wecom")
        # 等 flusher 异常退出（items 保留）
        acc = nt._accumulators[acc_key]
        for _ in range(200):
            if acc.flusher_task is None or acc.flusher_task.done():
                break
            await asyncio.sleep(0.01)
        assert acc.flusher_task is None or acc.flusher_task.done()
        assert acc.items and "幸存通知" in acc.items[0], (
            "异常批次必须保留在累积器里"
        )
        # 新通知入队 → 懒启动新 flusher → 保留批次 + 新通知一起投出
        await nt.deliver(key, "后续通知", wake_channel="wecom")
        await wait_done(nt, acc_key)

    run(main())
    assert len(attempts) == 2
    assert "幸存通知" in attempts[1] and "后续通知" in attempts[1]


def test_single_notice_passes_through_verbatim(monkeypatch):
    """单条通知不套聚合标头，原样投递（console 唤醒文本保真）。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", WINDOW)

    woken = []

    async def fake_wake_once(self, a, u, s, text):
        woken.append(text)
        return "ok", ""

    monkeypatch.setattr(Notifier, "_wake_once", fake_wake_once)

    nt = Notifier()
    key = ("a", "u", "sess-single")
    acc_key = ("a", "u", "sess-single", "console")

    async def main():
        await nt.deliver(key, "[进程 #1 ✅ 已完成] exit=0", wake_channel="console")
        await wait_done(nt, acc_key)

    run(main())
    assert woken == ["[进程 #1 ✅ 已完成] exit=0"]
