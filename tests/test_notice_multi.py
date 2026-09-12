# -*- coding: utf-8 -*-
"""process_tools_notice 批量注册测试：部分成功语义、三段式消息、去重、混合更新。

消息定稿（0.6.1 作者拍板）：三固定段「已生效 → 不存在 → 已结束」按序
拼接、空段跳过；有生效即 success，全失败才 error。
"""

from helpers import chunk_text, is_error, py_cmd, run

import notifier as notifier_mod
from manager import get_manager
from tools.exec import process_tools_exec
from tools.notice import process_tools_notice


def _registered_nums():
    """当前会话已注册通知的进程编号集合（测试会话键固定单键）。"""
    regs = notifier_mod.get_notifier()._notices
    assert len(regs) <= 1, "测试环境固定单会话键"
    return set(next(iter(regs.values())).keys()) if regs else set()


# ── 全成功 ──


def test_notice_single_success_message():
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice("#1")

    chunk = run(_t())
    assert not is_error(chunk)
    assert chunk_text(chunk) == "已生效：#1（仅完成通知）"
    assert _registered_nums() == {1}


def test_notice_single_success_with_interval():
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice(1, interval_seconds=900)

    chunk = run(_t())
    assert not is_error(chunk)
    assert chunk_text(chunk) == "已生效：#1（每 900s 进度通知 + 完成通知）"


def test_notice_multi_all_success_one_line():
    """批量全成功压缩为一行枚举。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice([1, 2], interval_seconds=900)

    chunk = run(_t())
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert text == "已生效：#1、#2（每 900s 进度通知 + 完成通知）"
    assert _registered_nums() == {1, 2}


def test_notice_multi_dedup_same_num():
    """重复编号去重保序：[1, 1, 2] 只注册 1、2。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice([1, 1, 2])

    chunk = run(_t())
    assert not is_error(chunk)
    assert chunk_text(chunk) == "已生效：#1、#2（仅完成通知）"
    assert _registered_nums() == {1, 2}


def test_notice_multi_json_string_channel_defense():
    """JSON 数组字符串 process_id 可用（通道字符串化防御，同 wait）。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice("[1, 2]")

    chunk = run(_t())
    assert not is_error(chunk)
    assert "已生效：#1、#2" in chunk_text(chunk)


# ── 部分成功（success 态 + 失败段） ──


def test_notice_partial_missing_still_registers_valid():
    """不存在的编号单独报告，有效编号照常注册，整体 success。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice([1, 42])

    chunk = run(_t())
    assert not is_error(chunk), "部分生效应为 success 语义"
    lines = chunk_text(chunk).splitlines()
    assert lines[0] == "已生效：#1（仅完成通知）"
    assert lines[1] == "不存在：#42（用 process_tools_list 查看全部编号）"
    assert len(lines) == 2
    assert _registered_nums() == {1}


def test_notice_partial_ended_still_registers_valid():
    """已结束的编号单独报告（带终态+exit），有效编号照常注册。"""
    async def _t():
        await process_tools_exec(py_cmd("print('quick')"), background=True)
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        return await process_tools_notice([1, 2])

    chunk = run(_t())
    assert not is_error(chunk)
    lines = chunk_text(chunk).splitlines()
    assert lines[0] == "已生效：#2（仅完成通知）"
    assert lines[1].startswith("已结束：#1 completed exit=0（")
    assert "process_tools_check" in lines[1]
    assert _registered_nums() == {2}


def test_notice_partial_full_three_sections_in_order():
    """最混合：三段齐全且按「已生效→不存在→已结束」定序。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        await process_tools_exec(py_cmd("print('done')"), background=True)
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        mp = get_manager().get(2)
        assert await mp.wait(timeout=60) == 0
        # #1 运行中 / #2 已结束 / #3 运行中 / #42 不存在
        return await process_tools_notice([1, 2, 42, 3], interval_seconds=900)

    chunk = run(_t())
    assert not is_error(chunk)
    lines = chunk_text(chunk).splitlines()
    assert lines[0] == "已生效：#1、#3（每 900s 进度通知 + 完成通知）"
    assert lines[1] == "不存在：#42（用 process_tools_list 查看全部编号）"
    assert lines[2].startswith("已结束：#2 completed exit=0（")
    assert len(lines) == 3
    assert _registered_nums() == {1, 3}


def test_notice_ended_item_shows_name():
    """已结束段带进程名（有名字时）：#N「name」 status exit=X。"""
    async def _t():
        await process_tools_exec(
            py_cmd("print('x')", ), background=True, name="构建",
        )
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        return await process_tools_notice(1)

    chunk = run(_t())
    assert is_error(chunk), "全失败（仅已结束）应为 error"
    text = chunk_text(chunk)
    assert text.startswith("已结束：#1「构建」 completed exit=0（")
    assert "process_tools_check" in text
    assert _registered_nums() == set()


# ── 全失败（error 态） ──


def test_notice_all_missing_is_error():
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        # 有一个有效进程在，但请求里只有无效编号
        return await process_tools_notice([42, 43])

    chunk = run(_t())
    assert is_error(chunk), "没有任何生效应为 error"
    assert chunk_text(chunk) == (
        "不存在：#42、#43（用 process_tools_list 查看全部编号）"
    )
    assert _registered_nums() == set(), "全失败不得有半注册"


def test_notice_multi_all_ended_is_error():
    async def _t():
        await process_tools_exec(py_cmd("print('a')"), background=True)
        await process_tools_exec(py_cmd("print('b')"), background=True)
        mp1 = get_manager().get(1)
        mp2 = get_manager().get(2)
        assert await mp1.wait(timeout=60) == 0
        assert await mp2.wait(timeout=60) == 0
        return await process_tools_notice([1, 2])

    chunk = run(_t())
    assert is_error(chunk)
    assert chunk_text(chunk) == (
        "已结束：#1 completed exit=0、#2 completed exit=0"
        "（用 process_tools_check 查看结果）"
    )
    assert _registered_nums() == set()


# ── 混合更新 + 注册 ──


def test_notice_multi_mixed_update_and_register():
    """已注册的走更新（重建周期任务）、新的走注册，同批生效同格式。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        first = await process_tools_notice(1)
        batch = await process_tools_notice([1, 2], interval_seconds=900)
        regs = notifier_mod.get_notifier()._notices
        return first, batch, dict(regs)

    first, batch, regs = run(_t())
    assert chunk_text(first) == "已生效：#1（仅完成通知）"
    assert not is_error(batch)
    assert chunk_text(batch) == "已生效：#1、#2（每 900s 进度通知 + 完成通知）"
    key = next(iter(regs))
    assert set(regs[key].keys()) == {1, 2}
    assert regs[key][1].interval_seconds == 900
    assert regs[key][2].interval_seconds == 900


# ── 参数错误（整批拒绝，行为不变） ──


def test_notice_multi_empty_list_rejected():
    chunk = run(process_tools_notice([]))
    assert is_error(chunk)


def test_notice_multi_interval_too_small_rejects_whole_batch():
    """interval 非法在任何注册发生前整批拒绝。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_notice([1, 42], interval_seconds=10)

    chunk = run(_t())
    assert is_error(chunk)
    assert "太小" in chunk_text(chunk)
    assert _registered_nums() == set(), "参数错误不得有半注册"
