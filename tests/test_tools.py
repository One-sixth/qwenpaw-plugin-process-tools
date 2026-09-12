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

    async def fake(self, mp_key, text, wake_channel="console"):
        self.calls.append((mp_key, text, wake_channel))
        return "记录✅"


def test_notice_completion_fires(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('noticed-output')"), background=True)
        mp = get_manager().get(1)
        c = await process_tools_notice(1, interval_seconds=0)
        assert not is_error(c) and "仅完成通知" in chunk_text(c)
        assert await mp.wait(timeout=60) == 0
        # 监听器是 create_task，给一点调度时间
        deadline = time.time() + 5
        while time.time() < deadline and not rec.calls:
            await asyncio.sleep(0.05)
        assert rec.calls, "完成通知未发出"
        _key, text, _chan = rec.calls[0]
        assert "已完成" in text and "noticed-output" in text

    run(main())


def test_notice_on_finished_returns_error_directing_to_check(monkeypatch):
    """2026-09-05 作者拍板新设计：notice 只面向未来事件——已结束进程
    返回错误并引导 check（等待语义对已知终态多此一举）。旧「立即投递」
    路径存在前台 run 占会话导致唤醒 20 连 409 自锁 10 分钟的空转缺陷，
    已废除。"""
    rec = _Recorder()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('quick')"), background=True)
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        c = await process_tools_notice(1)
        assert is_error(c)
        text = chunk_text(c)
        assert "已结束" in text and "process_tools_check" in text
        assert rec.calls == [], "已结束进程不得再触发任何投递"

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


def test_send_completion_concurrent_double_delivers_once(monkeypatch):
    """S3 钉死（新落点）：exit 路径 _send_completion 并发双调，deliver
    只能执行一次（检查+置位同一同步段，中间不得有 await）。
    旧落点「notice 立即投递」已随 0.4.2 设计废除。"""
    class SlowRec(_Recorder):
        async def fake(self, mp_key, text, wake_channel="console"):
            await asyncio.sleep(0.3)
            self.calls.append((mp_key, text, wake_channel))
            return "记录✅"

    rec = SlowRec()
    monkeypatch.setattr(notifier_mod.Notifier, "deliver", rec.fake)

    async def main():
        await process_tools_exec(py_cmd("print('dup')"), background=True)
        mp = get_manager().get(1)
        assert await mp.wait(timeout=60) == 0
        nt = notifier_mod.Notifier()
        notice = notifier_mod.Notice(process_num=1, session_key=mp.key)
        await asyncio.gather(
            nt._send_completion(mp, notice),
            nt._send_completion(mp, notice),
        )
        assert len(rec.calls) == 1, "并发下投递了不止一次"
        assert notice.completion_sent is True

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


# ── list：limit 截断 + 新→旧排序（0.4.4） ──


def test_list_limit_and_newest_first():
    """默认新进程在前；limit=2 只显最新 2 个并提示看全部；limit=0 全列。"""
    async def main():
        # 依次创建 3 个进程（编号单调分配，num 递增即创建顺序）
        for i in range(3):
            c = await process_tools_exec(py_cmd(f"print({i})"))
            assert not is_error(c)

        text = chunk_text(await process_tools_list())
        assert text.index("#3 [") < text.index("#2 [") < text.index("#1 ["), \
            f"应新进程在前：{text}"

        text2 = chunk_text(await process_tools_list(limit=2))
        assert "#3 [" in text2 and "#1 [" not in text2
        assert "limit=0" in text2, "截断时应提示 limit=0 看全部"

        text0 = chunk_text(await process_tools_list(limit=0))
        assert "#1 [" in text0 and "#3 [" in text0

    run(main())
