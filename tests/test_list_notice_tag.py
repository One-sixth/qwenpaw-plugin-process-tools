# -*- coding: utf-8 -*-
"""process_tools_list 通知注册标记（0.6.9）。

钉死作者拍板的设计：
- 仅 running 进程显示通知标记（已结束即使注册记录残留也不显示）；
- 周期注册时周期与完成通知都显示（🔔周期Ns+完成通知）；
- 仅完成通知注册显示 🔔完成通知；
- cancel 取消注册后标记消失（注册记录已弹出）；
- 标记插在状态方括号内、exit code 之前，不出现在命令之后；
- summary_line 空 tag 与默认调用输出恒等（旧格式逐字节兼容）。
"""

import asyncio

import notifier as notifier_mod
import pytest
from helpers import chunk_text, is_error, py_cmd, run
from manager import get_manager
from notifier import Notifier
from tools.exec import process_tools_exec
from tools.list import process_tools_list
from tools.notice import process_tools_notice


async def _silent_deliver(self, mp_key, pending, wake_channel="console"):
    """替身 deliver：吞掉投递（list 测试不关心通知文本）。

    带 self：经 monkeypatch 挂到 Notifier 类属性后走实例调用绑定，
    与 test_notice_cancel.py 的 _Recorder.fake 同款惯例——否则 exit
    listener 一旦真实运行即 TypeError（复审 C-1 修复）。
    """
    return "记录✅"


@pytest.fixture(autouse=True)
def _mute_deliver(monkeypatch):
    monkeypatch.setattr(Notifier, "deliver", _silent_deliver)


def _tag_lines(text: str):
    """返回含 🔔 标记的行。"""
    return [ln for ln in text.splitlines() if "🔔" in ln]


def _line_by_num(text: str):
    """按行首进程编号索引 list 输出各行（混合状态用例用）。"""
    out = {}
    for ln in text.splitlines():
        if ln.startswith("#"):
            num = int(ln[1:].split("「")[0].split(" ")[0])
            out[num] = ln
    return out


def test_list_shows_completion_tag():
    """running + 完成通知注册 → [running 🔔完成通知]，且标记在命令段之前。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        await asyncio.sleep(0.5)  # 防御性余量（status 构造即 running，非正确性依赖）
        c = await process_tools_notice(1)
        assert not is_error(c)
        c = await process_tools_list()
        tagged = _tag_lines(chunk_text(c))
        assert len(tagged) == 1, chunk_text(c)
        assert "[running 🔔完成通知]" in tagged[0]
        # 位置断言：标记段必须在命令段（$）之前（复审 C-2）
        assert tagged[0].index("[running") < tagged[0].index("$")

    run(main())


def test_list_shows_periodic_plus_completion_tag():
    """周期注册 → 周期与完成通知都显示（作者定稿：两个都要）。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        await asyncio.sleep(0.5)
        c = await process_tools_notice(1, interval_seconds=30)
        assert not is_error(c)
        c = await process_tools_list()
        tagged = _tag_lines(chunk_text(c))
        assert len(tagged) == 1
        assert "[running 🔔周期30s+完成通知]" in tagged[0]

    run(main())


def test_list_hides_after_cancel():
    """cancel 后注册记录弹出 → 无标记。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        await asyncio.sleep(0.5)
        c = await process_tools_notice(1)
        assert not is_error(c)
        c = await process_tools_notice(1, cancel=True)
        assert not is_error(c) and "已取消" in chunk_text(c)
        c = await process_tools_list()
        assert _tag_lines(chunk_text(c)) == []

    run(main())


def test_list_hides_ended_process():
    """已结束进程即使注册记录残留（completion_sent 已置）也不显示。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(0.3)"), background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.2)  # 防御性余量，同上
        c = await process_tools_notice(1)
        assert not is_error(c)
        assert await mp.wait(timeout=60) == 0
        assert notifier_mod.get_notifier().get(mp.key, 1) is not None, (
            "注册记录残留属预期——list 侧负责按状态过滤"
        )
        c = await process_tools_list()
        assert _tag_lines(chunk_text(c)) == []

    run(main())


def test_list_no_tag_without_registration():
    """未注册通知的 running 进程无标记。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        await asyncio.sleep(0.5)
        c = await process_tools_list()
        assert _tag_lines(chunk_text(c)) == []

    run(main())


def test_list_mixed_registration_states():
    """多进程混合注册状态：各进程标记相互独立（复审 C-3 补）。

    #1 完成通知 / #2 周期 / #3 未注册 / #4 已结束——若查找串线
    （取错 num），单进程测试全绿而本用例必挂。
    """

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )  # #1
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )  # #2
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )  # #3（不注册）
        await process_tools_exec(
            py_cmd("import time; time.sleep(0.3)"), background=True,
        )  # #4（等它结束）
        mp4 = get_manager().get(4)
        c = await process_tools_notice(1)
        assert not is_error(c)
        c = await process_tools_notice(2, interval_seconds=30)
        assert not is_error(c)
        assert await mp4.wait(timeout=60) == 0
        await asyncio.sleep(0.3)  # 防御性余量：等 exit listener 收尾
        c = await process_tools_list()
        by_num = _line_by_num(chunk_text(c))
        assert {1, 2, 3, 4} <= set(by_num), chunk_text(c)
        assert "[running 🔔完成通知]" in by_num[1]
        assert "[running 🔔周期30s+完成通知]" in by_num[2]
        assert "🔔" not in by_num[3], "未注册进程不得有标记"
        assert "🔔" not in by_num[4], "已结束进程不得有标记"
        assert "[completed" in by_num[4]

    run(main())


def test_summary_line_empty_tag_identity():
    """空 tag 与默认调用输出恒等（旧格式逐字节兼容的直接钉，复审 C-疑问2）。"""

    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        assert mp.summary_line() == mp.summary_line(state_tag="")

    run(main())
