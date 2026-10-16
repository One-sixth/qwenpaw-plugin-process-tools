# -*- coding: utf-8 -*-
"""process_tools_notice cancel 参数（0.6.5）核心行为回归。

钉死作者拍板的设计：
- cancel 取消注册：周期任务停 + exit 不再入队完成通知（闸①短路）；
- cancel 撤回聚合缓存中等待发送的待发通知（闸②清扫 + 闸③渲染过滤，
  swap-and-clear 拿走即消费）；
- 三态归档报告：已取消（撤回条数不进报告）/ 无注册 / 不存在，部分成功语义；
- cancel 幂等（重复取消报"无注册"）；cancel 后重新注册恢复正常投递；
- cancel=True 时 interval_seconds 静默忽略。
"""

import asyncio
import time

import helpers
import notifier as notifier_mod
import pytest
from helpers import chunk_text, is_error, py_cmd, run
from manager import get_manager
from notifier import Notifier, PendingNotice
from tools.notice import process_tools_notice
from tools.exec import process_tools_exec


class _Recorder:
    """替身 deliver：记录投递（渲染文本），不真投递。"""

    def __init__(self):
        self.texts = []

    async def fake(self, mp_key, pending, wake_channel="console"):
        # 注意：rec.fake 是绑定方法，self=rec 而非 Notifier 实例——
        # 渲染需显式取 notifier 单例（build_*_text 不依赖实例状态）
        self.texts.append(pending.render(notifier_mod.get_notifier()))
        return "记录✅"


async def _wait_calls(rec, n, timeout=5.0):
    """协程：轮询直到 rec 收到 n 条投递（监听器是 create_task，需让调度）。"""
    deadline = time.time() + timeout
    while time.time() < deadline and len(rec.texts) < n:
        await asyncio.sleep(0.05)
    return len(rec.texts)


def test_cancel_running_stops_periodic_and_completion(monkeypatch):
    """取消运行中进程的注册：周期任务停、exit 后无完成通知。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", 0.05)
    rec = _Recorder()
    monkeypatch.setattr(Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)  # 等进程进入 running
        c = await process_tools_notice(1, interval_seconds=30)
        assert not is_error(c) and "已生效" in chunk_text(c)
        nt = notifier_mod.get_notifier()
        assert nt.get(mp.key, 1) is not None

        c = await process_tools_notice(1, cancel=True)
        assert not is_error(c) and "已取消" in chunk_text(c)
        assert nt.get(mp.key, 1) is None, "注册记录已弹出"
        # 周期任务已取消（cancel 调 task.cancel()，让一拍再生效）
        await asyncio.sleep(0.1)
        # exit：闸①短路，完成通知不入队
        await mp.kill()
        await asyncio.sleep(0.5)
        assert rec.texts == [], "取消后周期/完成通知都不得投出"

    run(main())


def test_cancel_revokes_pending_completion_in_flush_window(monkeypatch):
    """撤回聚合窗口中的待发完成通知（本需求核心场景）：
    进程 exit → 完成通知入队 → 窗口内 cancel → flusher 不投出。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", 0.3)

    delivered = []

    async def fake_deliver_once(self, key, text, channel):
        delivered.append(text)
        return "ok", ""

    monkeypatch.setattr(Notifier, "_deliver_once", fake_deliver_once)

    async def main():
        await process_tools_exec(py_cmd("print('revoked')"), background=True)
        mp = get_manager().get(1)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c)
        assert await mp.wait(timeout=60) == 0
        # exit 监听器 create_task：等完成通知入队（但仍在聚合窗口内）
        nt = notifier_mod.get_notifier()
        acc = None
        deadline = time.time() + 5
        while time.time() < deadline:
            acc = nt._accumulators.get((*mp.key, "console"))
            if acc and acc.items:
                break
            await asyncio.sleep(0.01)
        assert acc is not None and acc.items, "完成通知应已入队待发"
        # 窗口内取消：撤回待发条目（报告只说取消成功，条数不进报告）
        c = await process_tools_notice(1, cancel=True)
        assert not is_error(c)
        assert "已取消：#1" in chunk_text(c)
        # flusher 醒来渲染：批次空，不投递
        await asyncio.sleep(0.6)
        assert delivered == [], "已撤回的完成通知不得投出"

    run(main())


def test_cancel_mixed_three_states(monkeypatch):
    """混合编号：已取消 / 无注册 / 不存在 三段报告，部分成功=success。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", 0.05)
    rec = _Recorder()
    monkeypatch.setattr(Notifier, "deliver", rec.fake)

    async def main():
        # #1 注册、#2 存在但不注册、#42 不存在
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp1 = get_manager().get(1)
        await asyncio.sleep(0.5)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c)

        c = await process_tools_notice([1, 2, 42], cancel=True)
        text = chunk_text(c)
        assert not is_error(c), "部分成功也是成功"
        assert "已取消" in text and "#1" in text
        assert "无注册" in text and "#2" in text
        assert "不存在" in text and "#42" in text
        await mp1.kill()

    run(main())


def test_cancel_unregistered_returns_error():
    async def main():
        await process_tools_exec(py_cmd("import time; time.sleep(30)"), background=True)
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        c = await process_tools_notice(1, cancel=True)
        assert is_error(c) and "无注册" in chunk_text(c)
        await mp.kill()

    run(main())


def test_cancel_unknown_process_returns_error():
    c = run(process_tools_notice(42, cancel=True))
    assert is_error(c) and "不存在" in chunk_text(c)


def test_cancel_idempotent(monkeypatch):
    """重复取消：第二次报"无注册"（幂等友好）。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", 0.05)
    rec = _Recorder()
    monkeypatch.setattr(Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("import time; time.sleep(30)"), background=True)
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        c1 = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c1)
        c2 = await process_tools_notice(1, cancel=True)
        assert not is_error(c2) and "已取消" in chunk_text(c2)
        c3 = await process_tools_notice(1, cancel=True)
        assert is_error(c3) and "无注册" in chunk_text(c3)
        await mp.kill()

    run(main())


def test_cancel_then_reregister_normal_delivery(monkeypatch):
    """取消后重新注册：新 Notice 正常工作，完成通知照发。"""
    monkeypatch.setattr(notifier_mod, "FLUSH_WINDOW_SECONDS", 0.05)
    rec = _Recorder()
    monkeypatch.setattr(Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('reborn')"), background=True)
        mp = get_manager().get(1)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c)
        c = await process_tools_notice(1, cancel=True)
        assert not is_error(c)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c) and "已生效" in chunk_text(c)
        assert await mp.wait(timeout=60) == 0
        await _wait_calls(rec, 1)
        assert rec.texts, "重新注册后完成通知正常投出"
        assert "已完成" in rec.texts[0] and "reborn" in rec.texts[0]

    run(main())


def test_cancel_ignores_interval_seconds():
    """cancel=True 时 interval_seconds 静默忽略（含非法区间不报错）。"""
    async def main():
        await process_tools_exec(py_cmd("import time; time.sleep(30)"), background=True)
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        # interval_seconds=10 本是非法值（1~29），cancel 模式下不校验
        c = await process_tools_notice(1, interval_seconds=10, cancel=True)
        assert is_error(c) and "无注册" in chunk_text(c), (
            "cancel 模式忽略 interval：错误只能是'无注册'而非'间隔太小'"
        )
        await mp.kill()

    run(main())
