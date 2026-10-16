# -*- coding: utf-8 -*-
"""命令控制字符展示转义（0.6.3）回归。

钉死展示契约：命令里的控制字符（真换行、制表符等）在所有展示点
（exec 回显 / check / list / 通知文本）反斜杠转义，"命令："行恒为
单行；存储原串不动（spawn 用原文）。
"""

import os
import re
import sys

from helpers import chunk_text, is_error, run

from manager import ManagedProcess
from notifier import Notifier
from tools.check import process_tools_check
from tools.exec import process_tools_exec
from tools.list import process_tools_list
from tools.wait import process_tools_wait


def _text(chunk):
    return chunk_text(chunk)


def test_exec_check_wait_list_command_ctrl_escaped():
    """exec 后台 / check / wait / list 的"命令"行转义且单行。"""
    argv = [sys.executable, "-c", "import sys\nprint(1)"]
    expected = f"命令：{sys.executable} -c import sys\\nprint(1)"

    async def main():
        chunk = await process_tools_exec(argv, background=True, no_shell=True)
        assert not is_error(chunk)
        text = _text(chunk)
        assert [l for l in text.split("\n") if l.startswith("命令：")] == [expected]

        num = re.search(r"进程 #(\d+)", text).group(1)
        chk = _text(await process_tools_check(num))
        assert [l for l in chk.split("\n") if l.startswith("命令：")] == [expected]

        lst = _text(await process_tools_list(limit=0))
        lines = [l for l in lst.split("\n") if l.startswith(f"#{num} ")]
        assert len(lines) == 1
        assert f"$ {sys.executable} -c import sys\\nprint(1)" in lines[0]

        wk = _text(await process_tools_wait(num, timeout=30))
        wlines = [l for l in wk.split("\n") if l.startswith(f"#{num} ")]
        assert len(wlines) == 1
        assert f"$ {sys.executable} -c import sys\\nprint(1)" in wlines[0]

    run(main())


def test_summary_line_truncates_raw_before_escape(tmp_dir):
    """复审修复：summary_line 先截断原串再转义——
    转义后超 80 且切点邻控制字符时不得出现悬空反斜杠。"""
    # 恰好 80 字符（不截断）：尾部真换行显示为字面 \n
    mp = ManagedProcess(
        1, "a" * 79 + "\n", ("a", "u", "s"), tmp_dir, os.path.join(tmp_dir, "x.log"),
    )
    assert mp.summary_line().endswith("a" * 79 + "\\n")

    # 91 字符（触发截断）：切点在纯 'a' 区，标记不受转义影响
    mp2 = ManagedProcess(
        1, "a" * 90 + "\n", ("a", "u", "s"), tmp_dir, os.path.join(tmp_dir, "y.log"),
    )
    assert mp2.summary_line().endswith("a" * 80 + "<<<TRUNCATED>>>")


def test_notifier_texts_command_ctrl_escaped(tmp_dir):
    """完成/周期通知文本的"命令"行同样转义。"""
    log = os.path.join(tmp_dir, "p.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("out\n")
    mp = ManagedProcess(
        1, 'python -c "import sys\nprint(1)"', ("a", "u", "s"), tmp_dir, log,
    )
    nt = Notifier()
    expected = '命令：python -c "import sys\\nprint(1)"'
    for text in (nt.build_completion_text(mp), nt.build_periodic_text(mp)):
        assert [l for l in text.split("\n") if l.startswith("命令：")] == [expected]


def test_notifier_texts_separator_line(tmp_dir):
    """v0.6.7：通知文本输出段同款分隔线家族；占位不加分隔线。"""
    nt = Notifier()
    # 有输出：完成/周期通知文本均含分隔线
    log = os.path.join(tmp_dir, "p.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("notice-line\n")
    mp = ManagedProcess(1, "python -c pass", ("a", "u", "s"), tmp_dir, log)
    comp = nt.build_completion_text(mp)
    peri = nt.build_periodic_text(mp)
    assert "————以下为输出末尾————" in comp
    assert "————以下为输出末尾————" in peri
    assert "notice-line" in comp and "notice-line" in peri
    # 空日志：占位，无分隔线
    empty_log = os.path.join(tmp_dir, "empty.log")
    open(empty_log, "w", encoding="utf-8").close()
    mp2 = ManagedProcess(2, "python -c pass", ("a", "u", "s"), tmp_dir, empty_log)
    comp2 = nt.build_completion_text(mp2)
    peri2 = nt.build_periodic_text(mp2)
    assert "（无输出）" in comp2 and "以下为" not in comp2
    assert "（暂无输出）" in peri2 and "以下为" not in peri2


def test_name_ctrl_escaped(tmp_dir):
    """name 含控制字符同样转义（header 行恒单行）。"""
    argv = [sys.executable, "-c", "print(1)"]
    bad_name = "bad\nname"
    expected_label = "「bad\\nname」"

    async def main():
        chunk = await process_tools_exec(
            argv, background=True, no_shell=True, name=bad_name,
        )
        assert not is_error(chunk)
        text = _text(chunk)
        lines = [l for l in text.split("\n") if "进程 #" in l]
        assert len(lines) == 1
        assert expected_label in lines[0]

        num = re.search(r"进程 #(\d+)", text).group(1)
        chk = _text(await process_tools_check(num))
        # name 非空时 check 首行是 #N「label」（紧跟标签无空格）
        clines = [l for l in chk.split("\n") if l.startswith(f"#{num}「")]
        assert len(clines) == 1
        assert expected_label in clines[0]

    run(main())

    # summary_line / 通知文本（直接构造）
    log = os.path.join(tmp_dir, "n.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("out\n")
    mp = ManagedProcess(1, "cmd", ("a", "u", "s"), tmp_dir, log, name=bad_name)
    assert expected_label in mp.summary_line()
    nt = Notifier()
    for text in (nt.build_completion_text(mp), nt.build_periodic_text(mp)):
        assert expected_label in text.split("\n")[0]
