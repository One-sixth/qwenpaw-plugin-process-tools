# -*- coding: utf-8 -*-
"""Windows Python UTF-8 补丁测试：显式开关 + env 注入 + PS 控制台编码前缀。

补丁三件事（仅 Windows 且 `patch_win_py_utf8=True` 时）：
1. 子进程 env 补 PYTHONUTF8=1（只补不盖，不改宿主 os.environ）；
2. PowerShell 壳把 [Console] 与 $OutputEncoding 钉成 UTF-8（不再挂 chcp）；
3. 工具侧强制按 utf-8 解码并忽略 encoding 参数。
默认关闭：原流程（auto 本机编码）一字不改。
"""

import inspect
import os

import pytest

from helpers import chunk_text, is_error, py_cmd, run

from tools.exec import process_tools_exec


def _text(chunk):
    return chunk_text(chunk)


# ── 参数默认值 ──────────────────────────────────────────


def test_defaults_are_auto_and_patch_off():
    """默认：encoding=auto、patch_win_py_utf8=False（老行为）。"""
    params = inspect.signature(process_tools_exec).parameters
    assert params["encoding"].default == "auto"
    assert params["patch_win_py_utf8"].default is False


# ── 前缀构造 ────────────────────────────────────────────


def test_prefix_powershell_sets_console_encoding():
    from utils import windows_utf8_prefix

    prefix = windows_utf8_prefix("pwsh")
    if os.name != "nt":
        assert prefix == ""
        return
    assert "[Console]::OutputEncoding=[Text.Encoding]::UTF8" in prefix
    assert "[Console]::InputEncoding=[Text.Encoding]::UTF8" in prefix
    assert "$OutputEncoding=[Console]::OutputEncoding" in prefix
    assert "chcp" not in prefix, "chcp 已移除：Python 输出与码页无关"


def test_prefix_powershell_51_same_as_pwsh():
    """Windows PowerShell 5.1 与 pwsh 同款初始化（语法兼容）。"""
    from utils import windows_utf8_prefix

    assert windows_utf8_prefix("powershell") == windows_utf8_prefix("pwsh")


def test_prefix_empty_for_cmd_and_others():
    """cmd 壳与 bash/None 都不需要前缀。"""
    from utils import windows_utf8_prefix

    assert windows_utf8_prefix("cmd") == ""
    assert windows_utf8_prefix("bash") == ""
    assert windows_utf8_prefix("sh") == ""
    assert windows_utf8_prefix(None) == ""
    assert windows_utf8_prefix("") == ""


# ── env 注入 ────────────────────────────────────────────


def test_build_env_injects_pythonutf8_on_windows():
    from manager import build_subprocess_env

    env = build_subprocess_env(None, python_utf8=True)
    if os.name == "nt":
        assert env.get("PYTHONUTF8") == "1"
    else:
        assert "PYTHONUTF8" not in env


def test_build_env_disabled_without_flag():
    from manager import build_subprocess_env

    env = build_subprocess_env(None, python_utf8=False)
    if "PYTHONUTF8" not in os.environ:
        assert "PYTHONUTF8" not in env


def test_build_env_user_pythonutf8_wins():
    """只补不盖：用户显式传的值（含大小写变体键）优先。"""
    from manager import build_subprocess_env

    env = build_subprocess_env({"PYTHONUTF8": "0"}, python_utf8=True)
    assert env["PYTHONUTF8"] == "0"
    env2 = build_subprocess_env({"pythonutf8": "0"}, python_utf8=True)
    assert [k for k in env2 if k.upper() == "PYTHONUTF8"] == ["pythonutf8"]
    assert env2["pythonutf8"] == "0"


# ── 实机端到端 ──────────────────────────────────────────


def test_flag_python_child_reports_utf8_mode():
    """开关打开：子进程 sys.flags.utf8_mode == 1。"""
    if os.name != "nt":
        pytest.skip("Windows 专属补丁，其他系统忽略")
    code = "import sys;print('UTF8MODE:%d' % sys.flags.utf8_mode)"
    chunk = run(process_tools_exec(py_cmd(code), patch_win_py_utf8=True))
    assert not is_error(chunk)
    assert "UTF8MODE:1" in _text(chunk)


def test_no_flag_child_keeps_locale_encoding():
    """默认不开：子进程仍是本机 locale 编码（原流程）。"""
    if os.name != "nt":
        pytest.skip("Windows 专属：locale 默认 GBK")
    if os.environ.get("PYTHONUTF8"):
        pytest.skip("宿主已设 PYTHONUTF8，无法验证默认路径")
    code = "import sys;print('UTF8MODE:%d' % sys.flags.utf8_mode)"
    chunk = run(process_tools_exec(py_cmd(code)))
    assert not is_error(chunk)
    assert "UTF8MODE:0" in _text(chunk)


def test_flag_ignored_on_posix():
    """非 Windows：开关被忽略（不强制改解码、不注入）。"""
    if os.name == "nt":
        pytest.skip("反向用例：仅 POSIX")
    chunk = run(
        process_tools_exec(py_cmd("print(1)"), patch_win_py_utf8=True)
    )
    text = _text(chunk)
    assert not is_error(chunk)
    assert "patch_win_py_utf8" not in text


# ── 乱码提示仅 Windows ──────────────────────────────────


def test_mojibake_hint_windows_only(monkeypatch):
    """提示指向 patch_win_py_utf8（Windows 专属），非 Windows 不弹。"""
    import types

    import utils

    dirty = "坏\ufffd字节"
    expected = utils.MOJIBAKE_HINT if os.name == "nt" else ""
    assert utils.mojibake_hint(dirty) == expected

    monkeypatch.setattr(utils, "os", types.SimpleNamespace(name="posix"))
    assert utils.mojibake_hint(dirty) == "", "POSIX 上该提示无意义，必须静默"


def test_mojibake_hint_clean_text_always_empty(monkeypatch):
    """干净输出任何平台都不弹。"""
    import types

    import utils

    assert utils.mojibake_hint("clean") == ""
    assert utils.mojibake_hint("") == ""
    monkeypatch.setattr(utils, "os", types.SimpleNamespace(name="posix"))
    assert utils.mojibake_hint("clean") == ""
