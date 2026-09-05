# -*- coding: utf-8 -*-
"""process_tools_wait 测试：单/批 all 语义、超时不杀、幂等、编号解析。"""

from helpers import chunk_text, is_error, py_cmd, run

from tools.exec import process_tools_exec
from tools.wait import process_tools_wait


def test_wait_single_completion():
    """后台快进程：wait 到终态 + exit + 输出尾部。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(1);print('w-done')"),
            background=True,
        )
        return await process_tools_wait(1, timeout=30)

    chunk = run(_t())
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "completed" in text and "exit=0" in text
    assert "w-done" in text


def test_wait_already_terminated_is_immediate():
    """已结束进程幂等返回（前台 exec 收掉后 wait 不再等待）。"""
    run(process_tools_exec(py_cmd("print(1)"), timeout=30))

    async def _t():
        import time

        t0 = time.time()
        chunk = await process_tools_wait("#1", timeout=1)
        return chunk, time.time() - t0

    chunk, dt = run(_t())
    assert not is_error(chunk)
    assert dt < 0.5, "已结束进程应立即返回"
    assert "completed" in chunk_text(chunk)


def test_wait_timeout_reports_running_and_spares_process():
    """W1/W3 拍板钉：超时=error「仍运行中」，进程不死、无杀引导。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        chunk = await process_tools_wait(1, timeout=1)
        # 存活证据必须在 shutdown_all 动手之前取（run() 收尾会杀进程）
        from manager import get_manager

        mp = get_manager().get(1)
        return chunk, (mp.status if mp else None)

    chunk, status = run(_t())
    assert is_error(chunk)
    text = chunk_text(chunk)
    assert "仍运行中" in text
    assert "taskkill" not in text and "send_sigkill" not in text
    assert status == "running", "wait 超时绝不能动进程"


def test_wait_multiple_all_semantics():
    """列表输入：全部结束才成功返回。"""
    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(0.5);print('A')"), background=True,
        )
        await process_tools_exec(
            py_cmd("import time;time.sleep(1.2);print('B')"), background=True,
        )
        return await process_tools_wait([1, 2], timeout=40)

    chunk = run(_t())
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "#1" in text and "#2" in text
    assert "全部结束" in text


def test_wait_list_json_string_channel_defense():
    """JSON 数组字符串 process_id 可用（通道字符串化防御）。"""
    async def _t():
        await process_tools_exec(py_cmd("print(7)"), background=True)
        return await process_tools_wait("[1]", timeout=30)

    chunk = run(_t())
    assert not is_error(chunk)
    assert "exit=0" in chunk_text(chunk)


def test_wait_multiple_timeout_shows_finished_part():
    """批量超时：error 里点名仍运行的，也列出已结束的。"""
    async def _t():
        await process_tools_exec(py_cmd("print('quick')"), background=True)
        await process_tools_exec(
            py_cmd("import time;time.sleep(30)"), background=True,
        )
        return await process_tools_wait([1, 2], timeout=3)

    chunk = run(_t())
    assert is_error(chunk)
    text = chunk_text(chunk)
    assert "#2 仍运行中" in text
    assert "已结束：#1" in text


def test_wait_tail_zero_status_only():
    """tail_lines=0：只回状态行，无输出小节。

    （不断言输出内容缺席——命令回显行自带命令行文本，是污染断言的经典陷阱）
    """
    async def _t():
        await process_tools_exec(py_cmd("print(112233)"), background=True)
        return await process_tools_wait(1, timeout=30, tail_lines=0)

    chunk = run(_t())
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "输出尾部" not in text
    assert "completed" in text


def test_wait_no_such_process():
    chunk = run(process_tools_wait(9))
    assert is_error(chunk)
    assert "不存在" in chunk_text(chunk)


def test_wait_empty_list_rejected():
    chunk = run(process_tools_wait([]))
    assert is_error(chunk)


def test_wait_multi_waiters_safe():
    """多等待者并发等同一路（shield 共享 future 架构红利）。"""
    import asyncio

    async def _t():
        await process_tools_exec(
            py_cmd("import time;time.sleep(1.5);print('multi')"),
            background=True,
        )
        a, b = await asyncio.gather(
            process_tools_wait(1, timeout=30),
            process_tools_wait(1, timeout=30),
        )
        return a, b

    ca, cb = run(_t())
    assert not is_error(ca) and not is_error(cb)
    assert "exit=0" in chunk_text(ca) and "exit=0" in chunk_text(cb)
