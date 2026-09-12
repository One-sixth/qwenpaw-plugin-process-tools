# -*- coding: utf-8 -*-
"""ProcessManager / ManagedProcess 单测。"""

import asyncio
import os
import time

import pytest

from helpers import py_cmd, run
from manager import (
    RING_LIMIT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_KILLED,
    STATUS_RUNNING,
    get_manager,
)


def test_start_and_complete(tmp_dir):
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("print('hello world')"))
        rc = await mp.wait(timeout=60)
        assert rc == 0
        assert mp.status == STATUS_COMPLETED
        assert "hello world" in mp.get_log_text()
        assert os.path.isfile(mp.log_path)
        assert mp.pid is not None

    run(main())


def test_stderr_merged_into_stdout():
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("import sys; sys.stderr.write('to-stderr\\n')"))
        assert await mp.wait(timeout=60) == 0
        assert "to-stderr" in mp.get_log_text()

    run(main())


def test_failure_exit_code():
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("import sys; sys.exit(3)"))
        assert await mp.wait(timeout=60) == 3
        assert mp.status == "failed"

    run(main())


def test_id_monotonic_not_reused():
    async def main():
        m = get_manager()
        a = await m.start(py_cmd("print('a')"))
        assert a.num == 1
        assert await a.wait(timeout=60) == 0
        b = await m.start(py_cmd("print('b')"))
        assert b.num == 2  # 不复用已结束编号
        await b.wait(timeout=60)

    run(main())


def test_many_concurrent_processes():
    """0.4.4 移除并发上限：同时 8 个运行中进程全部正常存活。"""
    async def main():
        m = get_manager()
        procs = [
            await m.start(py_cmd("import time; time.sleep(30)"))
            for _ in range(8)
        ]
        for mp in procs:
            assert mp.status == STATUS_RUNNING
        for mp in procs:
            await mp.kill()

    run(main())


def test_finished_process_not_counted():
    """启动→结束 交替 7 轮正常工作（旧并发上限时代的名额语义已随上限移除）。"""
    async def main():
        m = get_manager()
        for i in range(7):
            mp = await m.start(py_cmd(f"print({i})"))
            assert await mp.wait(timeout=60) == 0

    run(main())


def test_kill_marks_killed():
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("import time; time.sleep(300)"))
        await asyncio.sleep(0.5)
        note = await mp.kill()
        assert mp.status == STATUS_KILLED
        assert note  # 有描述
        # kill 的 wait 兜底后 future 已完成
        assert await mp.wait(timeout=5) == mp.exit_code

    run(main())


def test_write_stdin_and_read_buffer():
    async def main():
        m = get_manager()
        # 注意：cmd.exe 不认单引号，测试代码里不能出现 > < & | 等元字符
        mp = await m.start(
            py_cmd("import sys; [print('ECHO:'+l.strip()) for l in sys.stdin]")
        )
        await asyncio.sleep(0.8)  # 等解释器起来
        err = await mp.write_stdin("ping\n")  # 内核方法不自动补换行（工具层才补）
        assert err is None
        # 轮询等输出
        deadline = time.time() + 20
        text = ""
        while time.time() < deadline:
            await asyncio.sleep(0.3)
            _s, data, nxt = mp.get_buffer(0, RING_LIMIT)
            from sanitizer import sanitize_full

            text = sanitize_full(data, final=False)
            if "ECHO:ping" in text:
                break
        assert "ECHO:ping" in text
        await mp.kill()

    run(main())


def test_write_stdin_after_exit_returns_error():
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("print('bye')"))
        assert await mp.wait(timeout=60) == 0
        err = await mp.write_stdin("x")
        assert err and "已结束" in err

    run(main())


def test_ring_buffer_offset_semantics():
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("print('aaaaaaaa'); print('bbbbbbbb')"))
        assert await mp.wait(timeout=60) == 0
        start, data, nxt = mp.get_buffer(0, 1000)
        assert start == 0 and nxt == mp.total_out
        assert b"aaaaaaaa" in data and b"bbbbbbbb" in data
        # 从中间偏移续读
        _s2, data2, nxt2 = mp.get_buffer(9, 1000)
        assert data2.startswith(b";") or b"bbbbbbbb" in data2
        assert nxt2 == mp.total_out

    run(main())


def test_ring_buffer_evicts_old():
    """产出超过 512KB 时旧数据被挤出，dropped_bytes 前进。"""
    async def main():
        m = get_manager()
        mp = await m.start(
            py_cmd("import sys; sys.stdout.write('x'*(200*1024)); "
                   "sys.stdout.write('y'*(500*1024))")
        )
        assert await mp.wait(timeout=120) == 0
        assert mp.total_out > RING_LIMIT
        assert mp.dropped_bytes > 0
        # 最旧已丢的偏移处读：自动抬到缓冲起点
        start, data, _nxt = mp.get_buffer(0, 10)
        assert start >= mp.dropped_bytes
        assert len(data) == 10

    run(main())


def test_session_isolation(monkeypatch):
    import manager as manager_mod

    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("print('in session A')"))
        assert await mp.wait(timeout=60) == 0

        # 切到会话 B：看不到 A 的进程
        key_b = ("test-agent", "test-user", "session-B")
        manager_mod.session_key = lambda *a, **k: key_b
        assert m.get(mp.num) is None
        assert m.list_session() == []
        b = await m.start(py_cmd("print('in session B')"))
        assert b.num == 1  # B 的编号独立从 1 开始
        assert await b.wait(timeout=60) == 0

    run(main())
    # 恢复由 autouse fixture 每个测试重装


def test_shutdown_all_kills_running():
    async def main():
        m = get_manager()
        mp1 = await m.start(py_cmd("import time; time.sleep(300)"))
        mp2 = await m.start(py_cmd("import time; time.sleep(300)"))
        await asyncio.sleep(0.3)
        await m.shutdown_all()
        assert mp1.status == STATUS_KILLED
        assert mp2.status == STATUS_KILLED

    run(main())


def test_shield_wait_survives_timeout_cancel():
    """设计文档 §4.7 教训回归：wait_for 超时不得撕毁共享 future。"""
    async def main():
        m = get_manager()
        mp = await m.start(py_cmd("import time; time.sleep(1.5); print('done')"))
        # 第一个等待者超时
        assert await mp.wait(timeout=0.3) is None
        # future 必须仍然可用：第二个等待者能拿到真实结果
        assert await mp.wait(timeout=60) == 0
        assert "done" in mp.get_log_text()

    run(main())


def test_cleanup_old_logs(tmp_dir):
    import manager as manager_mod

    m = get_manager()
    log_dir = os.path.join(manager_mod.get_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    old = os.path.join(log_dir, "proc_old_1.log")
    new = os.path.join(log_dir, "proc_new_1.log")
    for p in (old, new):
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
    past = time.time() - 31 * 86400
    os.utime(old, (past, past))
    assert m.cleanup_old_logs(days=30) == 1
    assert not os.path.exists(old) and os.path.exists(new)


def test_reader_death_kills_child_not_false_finish():
    """S1 钉死：reader 死亡（feed 抛异常）时 monitor 必须终止子进程，
    状态走 killed、退出码非 None，绝不能谎报结束后放着活进程不占名额。"""
    async def main():
        m = get_manager()
        mp = await m.start(
            py_cmd(
                "import time; "
                "[print(i) or time.sleep(0.1) for i in range(200)]"
            )
        )

        def boom(_data):
            raise OSError("simulated disk full")

        mp._sanitizer.feed = boom
        # 等 monitor 走完异常兜底（子进程有输出 → reader 很快炸）
        deadline = time.time() + 20
        while time.time() < deadline and mp.status == STATUS_RUNNING:
            await asyncio.sleep(0.2)
        assert mp.status == STATUS_KILLED
        assert mp.exit_code is not None
        assert m._running_count(mp.key) == 0  # 已结束不占名额，语义自洽
        # 事件循环收尾时子进程必须已死（kill 短等过）
        assert await mp.wait(timeout=10) == mp.exit_code

    run(main())


def test_spawn_never_runs_when_log_setup_fails(monkeypatch):
    """S2 钉死：日志目录建不出来时必须在 spawn 之前失败，
    绝不允许先起进程再炸出无人认领的孤儿。"""
    async def main():
        import manager as manager_mod
        import asyncio as aio

        calls = []
        real_spawn = aio.create_subprocess_shell

        async def spy(*a, **k):
            calls.append(a)
            return await real_spawn(*a, **k)

        monkeypatch.setattr(aio, "create_subprocess_shell", spy)

        m = get_manager()
        # data 根指向 文件/子目录 → makedirs 必炸 NotADirectoryError
        victim = manager_mod.get_data_dir()
        os.makedirs(victim, exist_ok=True)  # 先保证父存在？不：victim 下放个普通文件
        blocker = os.path.join(victim, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        manager_mod.get_data_dir = lambda: os.path.join(blocker, "sub")

        try:
            await m.start(py_cmd("import time; time.sleep(60)"))
            raised = False
        except RuntimeError:
            raised = True  # manager.start 统一包 RuntimeError（含编号消耗提示）
        assert raised, "log 目录失败应当抛 RuntimeError"
        assert calls == [], "spawn 不允许被调用——先建日志后 spawn 的顺序钉死"

    run(main())


def test_status_for_exit_maps_control_c():
    """实机冒烟反馈钉死：Windows CTRL_BREAK 的 0xC000013A 必须是 killed，
    不能谎报 failed 让 agent 误判任务出错。"""
    from manager import status_for_exit

    # 无符号 / 有符号 32 位双形态
    assert status_for_exit(0xC000013A, False) == STATUS_KILLED
    assert status_for_exit(-1073741510, False) == STATUS_KILLED
    # 常规路径不变
    assert status_for_exit(0, False) == STATUS_COMPLETED
    assert status_for_exit(1, False) == STATUS_FAILED
    assert status_for_exit(1, True) == STATUS_KILLED


@pytest.mark.skipif(os.name != "nt", reason="Windows sigint 语义专测")
def test_windows_sigint_yields_killed_not_failed():
    """带自定义 SIGINT handler 的进程在 Windows 被 CTRL_BREAK 后，
    实链路必须收敛到 status=killed（handler 不执行、OS 直接终止）。

    注意：GenerateConsoleCtrlEvent 要求目标进程组 attach 在当前控制台上；
    无控制台的宿主（如管道化测试运行器）里 CTRL_BREAK 无处送达，
    signal_interrupt 返回失败文案或进程根本不退出——这两种情况 skip，
    不在无控制台环境伪装验证通过。
    """
    async def main():
        code = (
            "import signal,time;"
            "f=lambda *a: print('graceful',flush=True);"
            "[signal.signal(getattr(signal,n),f)"
            " for n in ('SIGINT','SIGBREAK') if hasattr(signal,n)];"
            "[time.sleep(0.2) for _ in range(300)]"
        )
        m = get_manager()
        mp = await m.start(py_cmd(code))
        await asyncio.sleep(1.0)
        note = mp.signal_interrupt(group=False)
        if "失败" in note:
            await mp.kill()
            pytest.skip(f"环境无控制台可送达 CTRL_BREAK：{note}")
        rc = await mp.wait(timeout=30)
        if rc is None:
            await mp.kill()
            pytest.skip("CTRL_BREAK 未送达（宿主无控制台），进程仍在跑")
        assert mp.status == STATUS_KILLED

    run(main())


# ── 编号落盘续号（0.2.1：重启后 #N 不复用旧日志文件） ──


def test_alloc_id_persists_across_restart(tmp_dir):
    """模拟宿主重启：内存计数器清零后应从磁盘续号，日志文件名不撞。"""
    import manager as manager_mod

    key = ("test-agent", "test-user", "test-session")
    m = manager_mod.get_manager()
    assert m._alloc_id(key) == 1
    assert m._alloc_id(key) == 2
    manager_mod.reset_manager()  # 模拟重启：新实例内存为空
    m2 = manager_mod.get_manager()
    n3 = m2._alloc_id(key)
    assert n3 == 3, "重启后编号必须续排，否则 append 复用旧日志文件"
    # 日志路径与编号一致性（撞号事故的场景回归）
    assert m2._counter_path(key).endswith(".cnt")


def test_alloc_id_backfills_from_existing_logs(tmp_dir):
    """0.2.1 首装盲区：计数文件没有，但 logs/ 已有历史号段——必须兜底。"""
    import manager as manager_mod

    key = ("test-agent", "test-user", "test-session")
    token = manager_mod.ProcessManager._session_token(key)
    log_dir = os.path.join(manager_mod.get_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    for stale in (1, 2, 3):
        with open(os.path.join(
            log_dir, f"proc_{token}_{stale}.log",
        ), "w", encoding="utf-8") as f:
            f.write("legacy")
    # 干扰项：别的会话 token 与非法编号文件名不参与兜底
    with open(os.path.join(log_dir, "proc_zzzz_99.log"), "w") as f:
        f.write("other session")
    with open(os.path.join(log_dir, f"proc_{token}_x.log"), "w") as f:
        f.write("bad digits")
    m = manager_mod.get_manager()  # 计数文件不存在（首装场景）
    assert m._alloc_id(key) == 4, "必须跳过 logs 里已占用的 1~3"
    assert m._alloc_id(key) == 5


def test_alloc_id_isolated_per_session(tmp_dir):
    """不同会话 key 各有计数器，互不影响。"""
    import manager as manager_mod

    key_a = ("ag", "u1", "s1")
    key_b = ("ag", "u1", "s2")
    m = manager_mod.get_manager()
    assert m._alloc_id(key_a) == 1
    assert m._alloc_id(key_b) == 1
    assert m._alloc_id(key_a) == 2


def test_alloc_id_survives_disk_failure(tmp_dir, monkeypatch):
    """计数器目录不可写 → 静默降级纯内存计数，alloc 不抛错。"""
    import manager as manager_mod

    blocker = os.path.join(tmp_dir, "not_a_dir")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("x")
    monkeypatch.setattr(
        manager_mod, "get_data_dir",
        lambda: os.path.join(blocker, "sub"),  # 父路径是文件 → makedirs 必炸
    )
    m = manager_mod.get_manager()
    key = ("ag", "u", "s")
    assert m._alloc_id(key) == 1
    assert m._alloc_id(key) == 2  # 磁盘失败，内存仍单调


def test_cleanup_removes_old_counters(tmp_dir):
    """过期清理同时覆盖 logs 与 counters 两目录。"""
    import manager as manager_mod

    m = manager_mod.get_manager()
    root = manager_mod.get_data_dir()
    old_cnt = os.path.join(root, "counters", "old.cnt")
    new_cnt = os.path.join(root, "counters", "new.cnt")
    old_log = os.path.join(root, "logs", "old.log")
    new_log = os.path.join(root, "logs", "new.log")
    for p in (old_cnt, new_cnt, old_log, new_log):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1")
    past = time.time() - 40 * 86400
    os.utime(old_cnt, (past, past))
    os.utime(old_log, (past, past))
    removed = m.cleanup_old_logs(days=30)
    assert removed == 2
    assert not os.path.exists(old_cnt) and not os.path.exists(old_log)
    assert os.path.exists(new_cnt) and os.path.exists(new_log)
