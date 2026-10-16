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


# ── escape_ctrl：控制字符展示转义（0.6.3） ──


def test_escape_ctrl_named():
    from utils import escape_ctrl

    assert escape_ctrl("\n\r\t\b\f") == "\\n\\r\\t\\b\\f"


def test_escape_ctrl_hex_fallback():
    """其余 C0/DEL/C1 控制字符用 \\xNN。"""
    from utils import escape_ctrl

    assert escape_ctrl("\x01\x02\x1f\x7f") == "\\x01\\x02\\x1f\\x7f"
    assert escape_ctrl("\x80\x9f") == "\\x80\\x9f"


def test_escape_ctrl_identity():
    """非控制字符（中文、反斜杠、空格等）原样保留。"""
    from utils import escape_ctrl

    s = "D:\\Scripts\\备份.py --输出 报告.md 100% a-b_c"
    assert escape_ctrl(s) == s


def test_escape_ctrl_mixed():
    from utils import escape_ctrl

    assert escape_ctrl('python -c "import sys\nprint(1)"\t--x') == (
        'python -c "import sys\\nprint(1)"\\t--x'
    )


def test_truncate_line_marker_upper():
    """0.6.3：截断标记统一为 <<<TRUNCATED>>>。"""
    from utils import truncate_line

    assert truncate_line("x" * 200, 120) == "x" * 120 + "<<<TRUNCATED>>>"
    assert truncate_line("short") == "short"


def test_make_result_marker_upper():
    from utils import make_result

    assert make_result("y" * 100, max_chars=50) == (
        "y" * 50 + "\n<<<TRUNCATED>>> 内容已超出 max_chars 限制。"
    )


def test_read_log_tail_toctou_returns_empty(monkeypatch):
    """0.6.5：isfile 过后文件被并发删除/锁定（TOCTOU）→ 与"不存在"
    同语义返回 ([], 0)，不抛 OSError（通知渲染链路不可失败的前提）。"""
    import os

    from utils import read_log_tail

    def boom(path, *, follow_symlinks=True):
        raise OSError("文件刚被删了")

    monkeypatch.setattr(os.path, "getsize", boom)
    lines, size = read_log_tail(__file__, tail_lines=5)
    assert lines == [] and size == 0


# ── resolve_cwd / get_tool_base_dir：对齐内核 cwd 语义（0.6.8） ──


def test_resolve_cwd_empty_returns_empty():
    from utils import resolve_cwd

    assert resolve_cwd("") == ""
    assert resolve_cwd(None) == ""  # 防御：通道传 null


def test_resolve_cwd_relative_mounts_first_project_dir(monkeypatch, tmp_dir):
    """相对 cwd 挂第一个项目目录（内核 roots[0] 语义）。"""
    from pathlib import Path

    import utils as utils_mod

    proj_a = os.path.join(tmp_dir, "proj_a")
    proj_b = os.path.join(tmp_dir, "proj_b")
    os.makedirs(proj_a)
    monkeypatch.setattr(
        utils_mod, "get_project_dir_paths", lambda: [proj_a, proj_b]
    )
    got = utils_mod.resolve_cwd("sub/dir")
    expected = str(Path(os.path.join(proj_a, "sub", "dir")).resolve())
    assert got == expected


def test_resolve_cwd_relative_falls_back_to_base_dir(monkeypatch, tmp_dir):
    """无项目目录配置 → 挂 get_tool_base_dir()（内核 or [base_dir] 语义）。"""
    from pathlib import Path

    import utils as utils_mod

    base = os.path.join(tmp_dir, "ws")
    os.makedirs(base)
    monkeypatch.setattr(utils_mod, "get_project_dir_paths", lambda: [])
    monkeypatch.setattr(utils_mod, "get_tool_base_dir", lambda: base)
    got = utils_mod.resolve_cwd("sub")
    assert got == str(Path(os.path.join(base, "sub")).resolve())


def test_resolve_cwd_relative_no_base_returns_empty(monkeypatch):
    """连基准目录都没有 → 返回 "" 交由调用方二次回落，绝不拿宿主进程
    cwd 拼相对路径（旧 abspath 行为的 bug 不许回归）。"""
    import utils as utils_mod

    monkeypatch.setattr(utils_mod, "get_project_dir_paths", lambda: [])
    monkeypatch.setattr(utils_mod, "get_tool_base_dir", lambda: "")
    assert utils_mod.resolve_cwd("sub") == ""


def test_resolve_cwd_absolute_untouched(tmp_dir):
    """绝对路径 expanduser 后照用，不挂基准目录。"""
    from pathlib import Path

    import utils as utils_mod

    target = os.path.join(tmp_dir, "abs_dir")
    os.makedirs(target)
    assert utils_mod.resolve_cwd(target) == str(Path(target).resolve())


def test_resolve_cwd_expanduser():
    """~ 前缀先展开（内核 Path.expanduser 同款）。"""
    from pathlib import Path

    import utils as utils_mod

    assert utils_mod.resolve_cwd("~") == str(
        Path(os.path.expanduser("~")).resolve()
    )


def test_get_tool_base_dir_kernel_unavailable_falls_back(monkeypatch):
    """内核导入失败（无 qwenpaw 环境）→ 回落 get_workspace_dir()。"""
    import sys

    import utils as utils_mod

    monkeypatch.setitem(sys.modules, "qwenpaw", None)
    monkeypatch.setitem(sys.modules, "qwenpaw.config", None)
    monkeypatch.setitem(sys.modules, "qwenpaw.config.context", None)
    monkeypatch.setattr(utils_mod, "get_workspace_dir", lambda: "D:\\fake_ws")
    assert utils_mod.get_tool_base_dir() == "D:\\fake_ws"


def test_get_tool_base_dir_passthrough_when_kernel_available():
    """qwenpaw 可导入时透传内核值（project dir → workspace → WORKING_DIR，
    str 化）；非空必为绝对路径。"""
    import utils as utils_mod

    got = utils_mod.get_tool_base_dir()
    assert isinstance(got, str)
    assert got == "" or os.path.isabs(got)
