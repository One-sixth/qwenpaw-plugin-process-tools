# -*- coding: utf-8 -*-
"""五个工具函数的端到端测试（不依赖运行中的 QwenPaw 服务）。"""

import asyncio
import time

import notifier as notifier_mod
from helpers import chunk_text, is_error, py_cmd, run
from manager import get_manager
from tools.check import process_tools_check
from tools.communicate import process_tools_communicate
from tools.exec import process_tools_exec
from tools.list import process_tools_list
from tools.notice import process_tools_notice


# ── exec：前台 ──


def test_exec_foreground_success():
    chunk = run(process_tools_exec(py_cmd("print('fore-ok')"), timeout=60))
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "fore-ok" in text
    assert "exit=0" in text


def test_exec_foreground_timeout_kills():
    chunk = run(
        process_tools_exec(py_cmd("import time; time.sleep(300)"), timeout=1)
    )
    assert is_error(chunk)
    assert "超时" in chunk_text(chunk)
    mp = get_manager().list_session()[0]
    assert mp.status == "killed"


def test_exec_empty_command():
    assert is_error(run(process_tools_exec("   ")))


def test_exec_bad_timeout_string():
    assert is_error(
        run(process_tools_exec(py_cmd("print(1)"), timeout="abc"))
    )


# ── exec 后台 + list + check ──


def test_exec_background_then_list_check():
    async def main():
        chunk = await process_tools_exec(
            py_cmd("print('bg-line1'); print('bg-line2')"), background=True,
        )
        assert not is_error(chunk)
        text = chunk_text(chunk)
        assert "进程 #1 已启动" in text
        assert "process_tools_notice" in text

        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0

        listed = chunk_text(await process_tools_list())
        assert "#1 [completed" in listed

        checked = chunk_text(await process_tools_check(1, tail_lines=5))
        assert "bg-line2" in checked
        assert "exit=0" in checked

    run(main())


def test_check_unknown_id():
    chunk = run(process_tools_check(99))
    assert is_error(chunk)
    assert "不存在" in chunk_text(chunk)


def test_id_formats_accepted():
    """1 / "1" / "#1" 三种写法都能解析。"""
    async def main():
        chunk = await process_tools_exec(py_cmd("print('x')"), timeout=60)
        assert not is_error(chunk)
        for form in (1, "1", "#1"):
            c = await process_tools_check(form)
            assert not is_error(c), form

    run(main())


# ── communicate ──


def test_communicate_write_read_sigkill():
    async def main():
        await process_tools_exec(
            py_cmd("import sys; [print('ECHO:'+l.strip()) for l in sys.stdin]"),
            background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.8)

        w = await process_tools_communicate(1, "write_stdin", text="hello")
        assert not is_error(w)

        deadline = time.time() + 20
        seen = ""
        offset = 0
        while time.time() < deadline:
            await asyncio.sleep(0.3)
            r = await process_tools_communicate(
                1, "read_stdout", read_offset=offset,
            )
            seen = chunk_text(r)
            if "ECHO:hello" in seen:
                break
        assert "ECHO:hello" in seen

        k = await process_tools_communicate(1, "send_sigkill")
        assert not is_error(k)
        assert mp.status == "killed"

    run(main())


def test_communicate_bad_action():
    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        chunk = await process_tools_communicate(1, "dance")
        assert is_error(chunk) and "dance" in chunk_text(chunk)
        await mp.kill()

    run(main())


def test_communicate_sigint_running_process():
    async def main():
        code = (
            "import signal,time,sys;"
            "f=lambda *a: (print('caught',flush=True));"
            "[signal.signal(getattr(signal,n),f)"
            " for n in ('SIGINT','SIGTERM','SIGBREAK') if hasattr(signal,n)];"
            "[time.sleep(0.2) for _ in range(150)]"
        )
        await process_tools_exec(py_cmd(code), background=True)
        mp = get_manager().get(1)
        await asyncio.sleep(1.2)
        chunk = await process_tools_communicate(1, "send_sigint")
        assert not is_error(chunk)
        await asyncio.sleep(1.0)
        assert mp.status in ("running", "killed", "failed", "completed")
        if mp.status == "running":
            await mp.kill()

    run(main())


# ── notice ──


class _Recorder:
    """替身 deliver：记录通知，不真投递。"""

    def __init__(self):
        self.calls = []

    async def fake(self, mp_key, text, wake_agent):
        self.calls.append((mp_key, text, wake_agent))
        return "记录✅"


def test_notice_completion_fires(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('noticed-output')"), background=True)
        mp = get_manager().get(1)
        c = await process_tools_notice(1, interval_seconds=0, wake_agent=False)
        assert not is_error(c) and "仅完成通知" in chunk_text(c)
        assert await mp.wait(timeout=60) == 0
        # 监听器是 create_task，给一点调度时间
        deadline = time.time() + 5
        while time.time() < deadline and not rec.calls:
            await asyncio.sleep(0.05)
        assert rec.calls, "完成通知未发出"
        _key, text, wake = rec.calls[0]
        assert "已完成" in text and "noticed-output" in text
        assert wake is False

    run(main())


def test_notice_on_finished_sends_immediately(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('quick')"), background=True)
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        c = await process_tools_notice(1)
        assert not is_error(c) and "立即发出" in chunk_text(c)
        assert len(rec.calls) == 1
        c2 = await process_tools_notice(1)
        assert not is_error(c2) and "重复" in chunk_text(c2)
        assert len(rec.calls) == 1

    run(main())


def test_notice_interval_too_small():
    async def main():
        await process_tools_exec(
            py_cmd("import time; time.sleep(30)"), background=True,
        )
        mp = get_manager().get(1)
        await asyncio.sleep(0.5)
        chunk = await process_tools_notice(1, interval_seconds=10)
        assert is_error(chunk) and "太小" in chunk_text(chunk)
        await mp.kill()

    run(main())


def test_notice_unknown_process():
    assert is_error(run(process_tools_notice(42)))


# ── 审查钉死：并发幂等 / 坏 cwd ──


def test_notice_concurrent_double_call_delivers_once(monkeypatch):
    """S3 钉死：并发双注册已结束进程，deliver 只能执行一次
    （先查后置幂等标志，检查与置位之间不得有 await）。"""
    class SlowRec(_Recorder):
        async def fake(self, mp_key, text, wake_agent):
            await asyncio.sleep(0.3)
            self.calls.append((mp_key, text, wake_agent))
            return "记录✅"

    rec = SlowRec()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('dup')"), background=True)
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        c1, c2 = await asyncio.gather(
            process_tools_notice(1), process_tools_notice(1),
        )
        assert len(rec.calls) == 1, "并发下投递了不止一次"
        texts = [chunk_text(c1), chunk_text(c2)]
        immediate = [t for t in texts if "立即发出" in t]
        dup = [t for t in texts if "重复" in t]
        assert len(immediate) == 1 and len(dup) == 1

    run(main())


def test_exec_bad_cwd_returns_error(monkeypatch):
    """R2 钉死：坏 cwd 必须返回错误 ToolChunk（不崩溃），
    且带编号消耗提示（R8）。"""
    chunk = run(
        process_tools_exec(
            py_cmd("print(1)"),
            cwd="Z:\\no_such_dir_at_all_xyz",
            timeout=30,
        )
    )
    assert is_error(chunk)
    text = chunk_text(chunk)
    assert "启动失败" in text and "已消耗" in text
    # 绝不能留下注册表里的活进程
    assert get_manager().list_session() == []
