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

# ── parse_process_ids：外围引号容错（0.4.4） ──


def test_parse_process_ids_quoted_forms():
    """LLM 偶发把 JSON 数组字符串整体再包一层双引号：
    逐层 json.loads（最多 2 次）+ 病态变体受限兜底（作者拍板：不扩展
    单引号，双层引号仍拒绝）。"""
    from utils import parse_process_ids

    # 标准形式不回归
    assert parse_process_ids([1, "2"]) == ([1, 2], None)
    assert parse_process_ids("[1, 2]") == ([1, 2], None)
    assert parse_process_ids('["1", "#2"]') == ([1, 2], None)
    # 外围多包一层双引号 → 逐层 loads 正常解析（目标场景）
    assert parse_process_ids('"[1, 2]"') == ([1, 2], None)
    assert parse_process_ids('"["1", "2"]"') == ([1, 2], None)
    # 单个编号被引号包裹 → 逐层 loads 后按单编号接受
    assert parse_process_ids('"3"') == ([3], None)
    assert parse_process_ids('"#4"') == ([4], None)
    # 两层引号剥到最后不是数组/编号 JSON → parse_int 拒绝
    value, err = parse_process_ids('""5""')
    assert value is None and err
    # 单引号数组不在兼容范围（只做双引号）：剥完 json.loads 仍失败
    value, err = parse_process_ids('"[\'1\', \'2\']"')
    assert value is None and err
    # 坏 JSON 仍报错
    value, err = parse_process_ids('["1", oops]')
    assert value is None and err


# ── parse_env：外围引号容错（0.4.4，同 parse_process_ids 手法） ──


def test_parse_env_quoted_json_object():
    """LLM 偶发把 JSON 对象字符串整体再包一层双引号：剥一次后正常解析。"""
    from utils import parse_env

    # 标准形式不回归
    assert parse_env({"A": "1"}) == ({"A": "1"}, None)
    assert parse_env('{"A": "1"}') == ({"A": "1"}, None)
    # 数值自动转 str
    assert parse_env({"PORT": 8080}) == ({"PORT": "8080"}, None)
    # 外围多包一层双引号 → 剥一次后正常解析（目标场景）
    import json as _json

    quoted = '"' + _json.dumps({"A": "1"}) + '"'
    assert parse_env(quoted) == ({"A": "1"}, None)
    # 只剥一次：两层引号剥掉外层后不是合法 JSON → 报错
    value, err = parse_env('""A""')
    assert value is None and err
    # 坏 JSON 仍报错
    value, err = parse_env('{"A": oops}')
    assert value is None and err


def test_parse_process_ids_channel_double_encoded():
    """实链路（0.4.4 冒烟抓到）：通道字符串化把 JSON 数组文本字符串再
    编码一层，参数实际到达 '"[\\"21\\", \\"20\\"]"'（外围引号+内部转义）。
    逐层 json.loads（最多 2 次）必须能解。"""
    import json as _json

    from utils import parse_env, parse_process_ids

    inner_text = _json.dumps(["21", "20"])   # '["21", "20"]'
    double = _json.dumps(inner_text)         # '"[\"21\", \"20\"]"'
    assert parse_process_ids(double) == ([21, 20], None)
    # env 同款双层
    env_inner = _json.dumps({"A": "1"})      # '{"A": "1"}'
    env_double = _json.dumps(env_inner)      # '"{\"A\": \"1\"}"'
    assert parse_env(env_double) == ({"A": "1"}, None)
