"""PowerShell 调用运算符补丁（`ps_quote_call_fix`）用例。

PowerShell 把「引号开头的字符串」当表达式，后面跟参数就是 ParserError；
补 `&` 才能执行。纯函数，跨平台可跑。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 需要补 & 的形态 ──────────────────────────────────────


def test_double_quote_path_with_arg():
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix(r'"C:\Program Files\App\app.exe" -c "print(1)"', "pwsh")
    assert out == r'& "C:\Program Files\App\app.exe" -c "print(1)"'


def test_single_quote_path_with_arg():
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix(r"'C:\a b\p.py' arg", "powershell")
    assert out == r"& 'C:\a b\p.py' arg"


def test_powershell_51_same_as_pwsh():
    from utils import ps_quote_call_fix

    cmd = r'"C:\x y\z.exe" --version'
    assert ps_quote_call_fix(cmd, "powershell") == ps_quote_call_fix(cmd, "pwsh")


def test_leading_whitespace_preserved():
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix('   "C:\\a b\\x.exe" -a', "pwsh")
    assert out == '   & "C:\\a b\\x.exe" -a'


def test_trailing_redirection_still_fixed():
    """`2>&1` 是参数形态（不是字符串重定向），补 & 后语义正确。"""
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix(r'"C:\a b\x.exe" 2>&1', "pwsh")
    assert out == r'& "C:\a b\x.exe" 2>&1'


def test_backtick_escaped_quote_inside_path():
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix(r'"C:\O`"Brien\x.exe" -a', "pwsh")
    assert out == r'& "C:\O`"Brien\x.exe" -a'


def test_doubled_quote_inside_string():
    from utils import ps_quote_call_fix

    out = ps_quote_call_fix(r'"C:\a""b\x.exe" -a', "pwsh")
    assert out == r'& "C:\a""b\x.exe" -a'


# ── 原样返回的形态 ──────────────────────────────────────


def test_bare_string_literal_untouched():
    from utils import ps_quote_call_fix

    cmd = r'"C:\Program Files\App\app.exe"'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_pipeline_untouched():
    from utils import ps_quote_call_fix

    cmd = '"中文" | findstr "中文"'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_string_operator_untouched():
    from utils import ps_quote_call_fix

    for cmd in ('"a" -eq "b"', '"a" -like "*b*"', '"a" -match "b"'):
        assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_redirection_of_string_untouched():
    from utils import ps_quote_call_fix

    cmd = '"abc" > out.txt'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_comment_after_string_untouched():
    from utils import ps_quote_call_fix

    cmd = '"abc" # note'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_unterminated_quote_untouched():
    from utils import ps_quote_call_fix

    cmd = r'"C:\a b\x.exe'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_normal_command_untouched():
    from utils import ps_quote_call_fix

    for cmd in ("dir", "git status", "& \"x.exe\" -a", "echo 'a' b"):
        assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_non_powershell_shell_untouched():
    from utils import ps_quote_call_fix

    cmd = r'"C:\a b\x.exe" -a'
    for shell in ("cmd", "bash", "sh", "zsh", None, ""):
        assert ps_quote_call_fix(cmd, shell) == cmd


def test_empty_command():
    from utils import ps_quote_call_fix

    assert ps_quote_call_fix("", "pwsh") == ""
    assert ps_quote_call_fix("   ", "pwsh") == "   "


# ── 接线：exec 真的把补丁接上了 ──────────────────────────


def test_exec_quoted_path_with_args_runs():
    """引号包住的 exe 路径 + 参数：走 pwsh 不再 ParserError。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows PowerShell 专属")
    from helpers import chunk_text, is_error, run
    from tools.exec import process_tools_exec

    cmd = '"{}" -c "print(\'QPATH-OK\')"'.format(sys.executable)
    chunk = run(process_tools_exec(cmd, shell="pwsh"))
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "QPATH-OK" in text and "ParserError" not in text


def test_exec_string_pipeline_still_untouched():
    """合法写法不被改写：字符串管道照旧走 PowerShell 语义（不补 &）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows PowerShell 专属")
    from helpers import chunk_text, is_error, run
    from tools.exec import process_tools_exec

    chunk = run(process_tools_exec('"abc" | Write-Output', shell="pwsh"))
    assert not is_error(chunk)
    text = chunk_text(chunk)
    assert "abc" in text and "无法将" not in text


# ── 字符串表达式不得被误补 &（测试员 D 发现）──────────


def test_string_expression_concat_untouched():
    """'A'+'B' 是合法 PS 表达式，不能被当成命令补 &。"""
    from utils import ps_quote_call_fix

    for cmd in ("'A'+'B'", '"a" + "b"', '"abc" * 3', '"abc"[0]', '"abc".Length'):
        assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_path_with_operator_still_not_fixed():
    """路径 + 比较运算符仍是合法表达式（运算符优先于路径判定）。"""
    from utils import ps_quote_call_fix

    cmd = r'"C:\a b\x.exe" -eq "C:\a b\x.exe"'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd


def test_bare_name_with_exe_extension_is_fixed():
    """不含分隔符但以程序扩展名结尾：仍按命令处理（PATH 查找）。"""
    from utils import ps_quote_call_fix

    assert ps_quote_call_fix('"pwsh.exe" -NoProfile', "pwsh") == '& "pwsh.exe" -NoProfile'


def test_plain_string_with_args_untouched():
    """普通字符串 + 非运算符参数：不补 &（不是程序路径）。"""
    from utils import ps_quote_call_fix

    cmd = '"hello world" -x'
    assert ps_quote_call_fix(cmd, "pwsh") == cmd
