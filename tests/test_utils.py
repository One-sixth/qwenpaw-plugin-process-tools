# -*- coding: utf-8 -*-
"""utils 底层函数测试（审查 R1/R6 钉死）。"""

import os

from utils import read_log_tail, sanitize_session_token


def test_read_log_tail_basic(tmp_dir):
    path = os.path.join(tmp_dir, "t.log")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("l1\nl2\nl3\nl4\nl5\n")
    lines, size = read_log_tail(path, tail_lines=3)
    assert lines == ["l3", "l4", "l5"]
    assert size == os.path.getsize(path)


def test_read_log_tail_small_file_returns_all(tmp_dir):
    path = os.path.join(tmp_dir, "t.log")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("a\nb\n")
    lines, _size = read_log_tail(path, tail_lines=10)
    assert lines == ["a", "b"]


def test_read_log_tail_window_starts_at_line_head_keeps_first_line(tmp_dir):
    """R1 钉死：截断窗口恰好落在行首时，第一行是完整的，不许错杀。"""
    path = os.path.join(tmp_dir, "t.log")
    data = b"aaa\nbbb\nccc\n"  # 12 字节
    with open(path, "wb") as f:
        f.write(data)
    lines, _size = read_log_tail(path, tail_lines=5, max_bytes=4)
    # 窗口 = b[8:]=b"ccc\n"，前探一字节是 \n → 保留整行
    assert lines == ["ccc"]


def test_read_log_tail_window_midline_drops_partial(tmp_dir):
    """窗口起点在行中时，只丢那条残行。"""
    path = os.path.join(tmp_dir, "t.log")
    data = b"aaaa\nbbbb\ncccc\n"  # 15 字节
    with open(path, "wb") as f:
        f.write(data)
    lines, _size = read_log_tail(path, tail_lines=5, max_bytes=6)
    # 窗口 = b[9:] = b"bb\ncccc\n" 起行残 → 丢 "bb" → ["cccc"]
    assert lines == ["cccc"]


def test_read_log_tail_missing_file():
    lines, size = read_log_tail(os.path.join("nope", "nothere.log"), 5)
    assert lines == [] and size == 0


def test_session_token_deterministic_and_stable():
    """R6 钉死：token 必须跨调用确定（crc32 而非进程随机 hash()）。"""
    t1 = sanitize_session_token("agent|user|session-xyz")
    t2 = sanitize_session_token("agent|user|session-xyz")
    assert t1 == t2
    assert t1 != sanitize_session_token("agent|user|session-xyz2")
    assert len(t1) <= 40 + 1 + 8


def test_session_token_cross_process_stable(tmp_dir):
    """子进程（不同 PYTHONHASHSEED）算出的 token 必须一致。"""
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, r'{}');"
        "from utils import sanitize_session_token;"
        "print(sanitize_session_token('a|b|c'))"
    ).format(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONHASHSEED="12345")
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, check=True,
    )
    assert out.stdout.strip() == sanitize_session_token("a|b|c")
