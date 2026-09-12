# -*- coding: utf-8 -*-
"""exec 新增参数测试：env / name / hide_window / detach / encoding / no_shell。"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time

from helpers import chunk_text, is_error, py_cmd, run

from tools.exec import process_tools_exec
from tools.check import process_tools_check
from tools.list import process_tools_list
from tools.communicate import process_tools_communicate


def _text(chunk):
    return chunk_text(chunk)


# ── env ────────────────────────────────────────────────


def test_env_injection():
    """dict env 注入子进程可见。"""
    cmd = py_cmd("import os;print(os.environ.get('PT_TEST_VAR','missing'))")
    chunk = run(process_tools_exec(cmd, env={"PT_TEST_VAR": "hello-env"}))
    assert not is_error(chunk)
    assert "hello-env" in _text(chunk)


def test_env_json_string():
    """通道字符串化防御：env 传 JSON 字符串照样生效。"""
    cmd = py_cmd("import os;print(os.environ.get('PT_TEST_VAR','missing'))")
    chunk = run(process_tools_exec(cmd, env='{"PT_TEST_VAR": "json-env"}'))
    assert not is_error(chunk)
    assert "json-env" in _text(chunk)


def test_env_numeric_value_coerced():
    """LLM 传数值 env 值（{"PORT": 8080}）自动转 str。"""
    cmd = py_cmd("import os;print(os.environ.get('PT_PORT','missing'))")
    chunk = run(process_tools_exec(cmd, env={"PT_PORT": 8080}))
    assert not is_error(chunk)
    assert "8080" in _text(chunk)


def test_env_merges_host():
    """env 是增量语义：宿主 PATH 不能丢。"""
    cmd = py_cmd("import os;print('HASPATH' if os.environ.get('PATH') else 'NOPATH')")
    chunk = run(process_tools_exec(cmd, env={"PT_X": "1"}))
    assert not is_error(chunk)
    assert "HASPATH" in _text(chunk)


def test_env_rejects_garbage():
    """无法解析成 dict 的 env 报三段式错误。"""
    chunk = run(process_tools_exec("echo x", env="[[不是JSON"))
    assert is_error(chunk)
    assert "env" in _text(chunk)


def test_env_rejects_scalar():
    chunk = run(process_tools_exec("echo x", env=123))
    assert is_error(chunk)
    assert "env" in _text(chunk)


# ── name ───────────────────────────────────────────────


def test_name_shown_in_returns_and_list():
    """name 出现在 exec 返回、check、list 三处。"""
    label = "夜-build"
    chunk = run(
        process_tools_exec(py_cmd("print(1)"), background=True, name=label)
    )
    text = _text(chunk)
    assert f"「{label}」" in text
    chk = run(process_tools_check(1))
    assert label in _text(chk)
    lst = run(process_tools_list())
    assert label in _text(lst)


def test_name_truncated_80():
    """超长 name 截到 80 字符（存字段，展示再截 40）。"""
    chunk = run(
        process_tools_exec(py_cmd("print(1)"), name="x" * 200)
    )
    assert not is_error(chunk)
    from manager import get_manager

    mp = get_manager().list_session()[0]
    assert len(mp.name) == 80


# ── hide_window ────────────────────────────────────────


def test_hide_window_managed_still_works():
    """hide_window=True 不影响托管链路（管道/日志/退出码照旧）。"""
    chunk = run(process_tools_exec(py_cmd("print('hw-ok')"), hide_window=True))
    assert not is_error(chunk)
    text = _text(chunk)
    assert "hw-ok" in text
    assert "exit=0" in text


# ── detach ─────────────────────────────────────────────


def _kill_pid(pid: int) -> None:
    """清理测试里 detach 出来的进程（不受托管，只能系统级自理）。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(pid, signal.SIGKILL)  # start_new_session 使 pgid==pid
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_detach_returns_pid_no_registry_no_number():
    """detach 返回 OS PID：不进注册表、不消耗 #N 编号。"""
    chunk = run(process_tools_exec(py_cmd("pass"), detach=True))
    assert not is_error(chunk)
    text = _text(chunk)
    assert "pid=" in text and "#N" not in text

    from manager import get_manager

    assert get_manager().list_session() == []
    # 编号未被消耗：之后的正常 exec 仍从 #1 起
    bg = run(process_tools_exec(py_cmd("print(1)"), background=True))
    assert "#1" in _text(bg)


def test_detach_ignores_foreground_timeout():
    """detach 语义是 fire-and-forget：timeout 参数失效、立即返回。"""
    t0 = time.time()
    chunk = run(
        process_tools_exec(
            py_cmd("import time;time.sleep(20)"), detach=True, timeout=1
        )
    )
    elapsed = time.time() - t0
    assert not is_error(chunk)
    assert elapsed < 8, "detach 不该被 timeout 拖住"
    m = re.search(r"pid=(\d+)", _text(chunk))
    assert m
    _kill_pid(int(m.group(1)))


def test_detach_invisible_to_tools():
    """list/check 对 detach 进程完全无感知。"""
    run(process_tools_exec(py_cmd("pass"), detach=True))
    lst = run(process_tools_list())
    assert "暂无托管进程" in _text(lst)
    chk = run(process_tools_check(1))
    assert is_error(chk)


def test_detach_env_takes_effect(tmp_dir):
    """detach 进程 env 注入真实生效（用文件副作用外证）。"""
    marker = os.path.join(tmp_dir, "detach_env.txt")
    code = (
        "import os;"
        f"open(r'{marker}','w').write(os.environ.get('PT_DETACH_ENV',''))"
    )
    chunk = run(
        process_tools_exec(
            py_cmd(code), detach=True, env={"PT_DETACH_ENV": "d42"}
        )
    )
    assert not is_error(chunk)
    deadline = time.time() + 15
    while time.time() < deadline and not os.path.isfile(marker):
        time.sleep(0.2)
    assert os.path.isfile(marker), "detach 子进程未写出 marker"
    with open(marker, encoding="utf-8") as f:
        assert f.read() == "d42"


# ── encoding ───────────────────────────────────────────


def test_auto_encoding_reported_in_return():
    """默认 auto：返回信息标注 encoding= 并带乱码调整提示。"""
    chunk = run(process_tools_exec(py_cmd("print('ae')")))
    assert not is_error(chunk)
    text = _text(chunk)
    assert 'encoding="' in text
    assert "auto=本机编码" in text


def test_explicit_encoding_reported_no_auto_hint():
    """显式 encoding：标注实际 codec，无 auto 提示语。"""
    chunk = run(process_tools_exec(py_cmd("print(1)"), encoding="utf-8"))
    text = _text(chunk)
    assert 'encoding="utf-8"' in text
    assert "auto=本机编码" not in text


def test_encoding_unknown_codec_rejected():
    """未知 codec 报错，错误消息带可用示例。"""
    chunk = run(process_tools_exec("echo x", encoding="no-such-codec-x"))
    assert is_error(chunk)
    assert "encoding" in _text(chunk)


def test_explicit_gbk_end_to_end():
    """端到端：子进程按 gbk 出、我们按 gbk 解，中文完整往返。

    命令行刻意纯 ASCII（chr 码组装中文），避免命令回显行混入中文
    污染断言——否则解码坏了也能"假通过"。
    """
    code = "print(''.join(chr(c) for c in [20013,25991,23383,79,75]))"
    chunk = run(
        process_tools_exec(
            py_cmd(code), encoding="gbk",
            env={"PYTHONIOENCODING": "gbk"},
        )
    )
    assert not is_error(chunk)
    assert "中文字OK" in _text(chunk)


def test_explicit_utf8_end_to_end():
    """端到端：utf-8 同理（不依赖本机 locale）。"""
    code = "print(''.join(chr(c) for c in [20013,25991,23383,79,75]))"
    chunk = run(
        process_tools_exec(
            py_cmd(code), encoding="utf-8",
            env={"PYTHONIOENCODING": "utf-8"},
        )
    )
    assert not is_error(chunk)
    assert "中文字OK" in _text(chunk)


def test_auto_decodes_native_gbk_cmd_output():
    """auto 实战钉：中文 Windows 上 cmd 原生 GBK 文案可读。

    断言目标「活动代码页」只存在于 chcp 的 GBK 输出里，命令行本身不含
    它——命令回显行无法污染，解码坏必挂。
    """
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属：控制台 GBK 码页")
    import ctypes

    cp = ctypes.windll.kernel32.GetConsoleOutputCP()
    if cp != 936:
        import pytest

        pytest.skip(f"本机控制台码页 {cp} 非 936，无法验证 GBK 文案")
    chunk = run(process_tools_exec("cmd /c chcp"))
    text = _text(chunk)
    assert "活动代码页" in text, f"auto 未正确解码 GBK：{text!r}"
    assert 'encoding="gbk"' in text or 'encoding="cp936"' in text


def test_stdin_encoding_roundtrip_gbk():
    """write_stdin 用进程 encoding 编码：中文经 gbk 子进程原样回显。"""
    async def _t():
        await process_tools_exec(
            py_cmd(
                "import sys;[print('GOT:'+l.strip(),flush=True) for l in sys.stdin]"
            ),
            background=True,
            encoding="gbk",
            env={"PYTHONIOENCODING": "gbk"},
        )
        await process_tools_communicate(1, "write_stdin", text="中文测试")
        await asyncio.sleep(1.0)
        return await process_tools_communicate(1, "read_stdout")

    text = _text(run(_t()))
    assert "GOT:中文测试" in text


# ── no_shell ───────────────────────────────────────────


def test_no_shell_argv_list():
    """list argv 原生直启，不经 cmd。"""
    chunk = run(
        process_tools_exec(
            [sys.executable, "-u", "-c", "print('NS-OK')"], no_shell=True,
        )
    )
    assert not is_error(chunk)
    text = _text(chunk)
    assert "NS-OK" in text and "exit=0" in text


def test_no_shell_json_string():
    """通道把 list 字符串化成 JSON 也能用。"""
    argv = [sys.executable, "-u", "-c", "print('NS-J')"]
    chunk = run(
        process_tools_exec(json.dumps(argv), no_shell=True)
    )
    assert not is_error(chunk)
    assert "NS-J" in _text(chunk)


def test_no_shell_metachars_are_literal():
    """元字符零转义：argv 里的 < > & | 原样进程序（cmd 模式必炸）。"""
    chunk = run(
        process_tools_exec(
            [sys.executable, "-u", "-c", "print('<LITERAL>&|>')"],
            no_shell=True,
        )
    )
    assert not is_error(chunk)
    assert "<LITERAL>&|>" in _text(chunk)


def test_no_shell_list_without_flag_rejected():
    """list command 但没设 no_shell → 拒绝并提示。"""
    chunk = run(process_tools_exec([sys.executable, "-c", "print(1)"]))
    assert is_error(chunk)
    assert "no_shell" in _text(chunk)


def test_no_shell_string_rejected():
    """no_shell 但 command 是普通字符串 → 拒绝并提示 argv。"""
    chunk = run(process_tools_exec("echo hi", no_shell=True))
    assert is_error(chunk)
    assert "argv" in _text(chunk)


def test_no_shell_detach(tmp_dir):
    """no_shell + detach 组合：原生直启脱离进程，文件副作用外证。"""
    marker = os.path.join(tmp_dir, "ns_detach.txt")
    code = f"open(r'{marker}','w').write('nsd')"
    chunk = run(
        process_tools_exec(
            [sys.executable, "-c", code], no_shell=True, detach=True,
        )
    )
    assert not is_error(chunk)
    assert "pid=" in _text(chunk)
    deadline = time.time() + 15
    while time.time() < deadline and not os.path.isfile(marker):
        time.sleep(0.2)
    assert os.path.isfile(marker)
    with open(marker, encoding="utf-8") as f:
        assert f.read() == "nsd"


# ── env 框架惯例对齐 ───────────────────────────────────


def test_framework_python_path_prefixed():
    """对齐 execute_shell_command：子进程 PATH 首位 = 宿主 python 目录。"""
    chunk = run(
        process_tools_exec(
            py_cmd("import os,sys;print(os.environ['PATH'].split(os.pathsep)[0])")
        )
    )
    assert not is_error(chunk)
    assert os.path.dirname(sys.executable) in _text(chunk)


def test_auto_codec_uses_console_codepage():
    """auto 钉：Windows 上取控制台码页（不受 PYTHONUTF8 谎报影响）。

    宿主常设 PYTHONUTF8=1 → locale.getpreferredencoding(False) 谎报
    utf-8，但 cmd 原生输出仍是 GBK；_auto_codec 必须走 ctypes 控制台码页。
    """
    from utils import _auto_codec, resolve_encoding

    name, is_auto, err = resolve_encoding("auto")
    assert err is None and is_auto
    assert name == _auto_codec()
    if os.name == "nt":
        import ctypes

        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        if cp:
            import codecs

            assert _auto_codec() == codecs.lookup(f"cp{cp}").name
    else:
        import codecs
        import locale

        assert _auto_codec() == codecs.lookup(
            locale.getpreferredencoding(False)
        ).name


# ── shell 枚举 ─────────────────────────────────────────


def _which(*names):
    import shutil

    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def test_shell_auto_is_pwsh_on_windows():
    """Windows auto 走 pwsh：PS 专属命令 Write-Output 成功。"""
    if os.name != "nt" or not _which("pwsh", "powershell"):
        import pytest

        pytest.skip("Windows + PowerShell 专属")
    chunk = run(process_tools_exec("Write-Output 'PS-DEFAULT'"))
    assert not is_error(chunk)
    text = _text(chunk)
    assert "PS-DEFAULT" in text and "exit=0" in text


def test_shell_auto_is_bash_on_posix():
    """POSIX auto 走 bash：$BASH_VERSION 非空。"""
    if os.name == "nt" or not _which("bash"):
        import pytest

        pytest.skip("POSIX + bash 专属")
    chunk = run(
        process_tools_exec("echo BVER:$BASH_VERSION")
    )
    assert not is_error(chunk)
    text = _text(chunk)
    assert "BVER:4" in text or "BVER:5" in text


def test_shell_explicit_bash_arithmetic():
    """shell="bash"：$(( )) 算术展开出 42（cmd/pwsh 都出不了这个）。"""
    if not _which("bash"):
        import pytest

        pytest.skip("无 bash")
    chunk = run(process_tools_exec("echo $((6*7))", shell="bash"))
    assert not is_error(chunk)
    text = _text(chunk)
    assert "42" in text and "exit=0" in text


def test_shell_unknown_choice_rejected():
    chunk = run(process_tools_exec("echo x", shell="zsh"))
    assert is_error(chunk)
    assert "shell" in _text(chunk)


def test_shell_missing_explicit_rejected_not_silent():
    """显式选 pwsh 但机器没有 → 报错引导，不静默换壳。"""
    if _which("pwsh", "powershell"):
        import pytest

        pytest.skip("本机装了 PowerShell，构造不出缺失场景")
    chunk = run(process_tools_exec("Write-Output 1", shell="pwsh"))
    assert is_error(chunk)
    assert "pwsh" in _text(chunk)


def test_no_shell_ignores_shell_param():
    """no_shell=True 时 shell 参数被忽略（argv 直启不看壳）。"""
    chunk = run(
        process_tools_exec(
            [sys.executable, "-u", "-c", "print('IGN-SH')"],
            no_shell=True,
            shell="bash",
        )
    )
    assert not is_error(chunk)
    assert "IGN-SH" in _text(chunk)


def test_shell_wrap_keeps_display_clean():
    """pwsh 包装不进命令回显：命令行是用户原文，不见 -NoProfile；
    返回信息单独一行报告实际使用的 shell（0.4.4 起）。"""
    chunk = run(
        process_tools_exec(py_cmd("print(1)"), background=True),
    )
    text = _text(chunk)
    assert "NoProfile" not in text
    assert 'shell="' in text
    assert sys.executable in text
