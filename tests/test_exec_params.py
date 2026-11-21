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


def test_default_auto_encoding_reported():
    """默认 auto：标注实际 codec 并带乱码调整提示（老行为）。"""
    chunk = run(process_tools_exec(py_cmd("print('ae')")))
    assert not is_error(chunk)
    text = _text(chunk)
    assert 'encoding="' in text
    assert "auto=本机编码" in text


def test_explicit_encoding_reported_without_auto_hint():
    """显式 encoding：标注实际 codec，无 auto 提示语。"""
    chunk = run(process_tools_exec(py_cmd("print(1)"), encoding="utf-8"))
    text = _text(chunk)
    assert 'encoding="utf-8"' in text
    assert "auto=本机编码" not in text


def test_local_removed():
    """local 已移除：报错并指向默认 auto（两者同义，不重复保留）。"""
    chunk = run(process_tools_exec("echo x", encoding="local"))
    assert is_error(chunk)
    text = _text(chunk)
    assert "已移除" in text and "auto" in text


def test_encoding_unknown_codec_rejected():
    """未知 codec 报错，错误消息带可用示例。"""
    chunk = run(process_tools_exec("echo x", encoding="no-such-codec-x"))
    assert is_error(chunk)
    assert "encoding" in _text(chunk)


def test_patch_flag_forces_utf8_and_ignores_encoding():
    """patch_win_py_utf8=True：强制 utf-8 并显式声明 encoding 被忽略。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属补丁，其他系统忽略")
    chunk = run(
        process_tools_exec(
            py_cmd("print('P')"), encoding="gbk", patch_win_py_utf8=True,
        )
    )
    assert not is_error(chunk)
    text = _text(chunk)
    assert 'encoding="utf-8"' in text
    assert "patch_win_py_utf8=true" in text
    assert "参数被忽略" in text


def test_patch_flag_python_utf8_end_to_end():
    """开关打开：3.13 子进程（本机管道默认 GBK）中文 + emoji 原样可读。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属补丁，其他系统忽略")
    code = (
        "import sys;print(''.join(chr(c) for c in [20013,25991])"
        " + chr(0x1F600) + '|' + (sys.stdout.encoding or ''))"
    )
    chunk = run(process_tools_exec(py_cmd(code), patch_win_py_utf8=True))
    assert not is_error(chunk)
    assert "中文😀|utf-8" in _text(chunk)


def test_patch_flag_off_keeps_local_locale():
    """默认不开：子进程仍按本机编码出（原流程），返回信息无补丁标注。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属：locale 默认 GBK")
    if os.environ.get("PYTHONUTF8"):
        import pytest

        pytest.skip("宿主已设 PYTHONUTF8，无法验证默认路径")
    code = "import sys;print('ENC:' + (sys.stdout.encoding or 'none'))"
    chunk = run(process_tools_exec(py_cmd(code)))
    text = _text(chunk)
    assert "enc:gbk" in text.lower(), f"默认路径未走本机 locale：{text!r}"
    assert "patch_win_py_utf8" not in text


def test_mojibake_hint_shown_on_decode_failure():
    """真的解坏（U+FFFD）时给出提示，且提示在分隔线之上（不混进程序输出）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("提示仅 Windows 弹（patch_win_py_utf8 是 Windows 专属参数）")
    code = "import sys;sys.stdout.buffer.write(bytes([255,254,253]))"
    chunk = run(process_tools_exec(py_cmd(code), encoding="utf-8"))
    text = _text(chunk)
    assert "\ufffd" in text
    assert "patch_win_py_utf8=true" in text
    sep = "————以下为stdout/stderr输出————"
    assert text.index("疑似发现乱码") < text.index(sep)


def test_mojibake_hint_silent_on_clean_output():
    """正常输出零噪声：不出现提示。"""
    chunk = run(process_tools_exec(py_cmd("print('clean-ascii-output')")))
    assert "疑似发现乱码" not in _text(chunk)


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


def test_stdin_patch_utf8_roundtrip_emoji():
    """开关打开：write_stdin 的中文/emoji 经子进程 UTF-8 stdio 原样回显。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属补丁，其他系统忽略")

    async def _t():
        await process_tools_exec(
            py_cmd(
                "import sys;[print('GOT:'+l.strip(),flush=True) for l in sys.stdin]"
            ),
            background=True,
            patch_win_py_utf8=True,
        )
        await process_tools_communicate(1, "write_stdin", text="中文测试😀")
        await asyncio.sleep(1.0)
        return await process_tools_communicate(1, "read_stdout")

    text = _text(run(_t()))
    assert "GOT:中文测试😀" in text


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
        import codecs
        import ctypes

        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        if cp:
            assert _auto_codec() == codecs.lookup(f"cp{cp}").name
    else:
        import codecs
        import locale

        assert _auto_codec() == codecs.lookup(
            locale.getpreferredencoding(False)
        ).name


def test_local_alias_removed_from_resolver():
    """local 不再被解析器接受：返回错误并指向默认 auto。"""
    from utils import resolve_encoding

    name, is_auto, err = resolve_encoding("local")
    assert name == "" and not is_auto
    assert err and "auto" in err




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

def test_bash_wsl_launcher_warning():
    """Windows 上 bash 落到 WSL 启动器时必须提示（它会把 $变量/引号吞掉）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("仅 Windows")
    import shutil

    exe = (shutil.which("bash") or "").lower()
    if "system32" not in exe:
        import pytest

        pytest.skip("本机 bash 不是 WSL 启动器（Git Bash 不受影响）")
    chunk = run(process_tools_exec("echo BASH-WARN-OK", shell="bash"))
    text = _text(chunk)
    assert "WSL 启动器" in text
    assert "BASH-WARN-OK" in text



def test_no_shell_missing_exe_is_named():
    """no_shell 传了不存在的程序：报错要点出是哪个 exe，而不是裸 WinError 2。"""
    chunk = run(
        process_tools_exec([r"D:\no_such_dir_D22\no_such.exe", "x"], no_shell=True)
    )
    assert is_error(chunk)
    text = _text(chunk)
    assert "no_such.exe" in text


def test_write_stdin_warns_when_codec_lossy():
    """目标 codec 表示不了的字符会被替换成 ?：写入回执要明确说出来。"""
    import re

    async def main():
        started = await process_tools_exec(
            py_cmd(
                "import sys;print('RDY',flush=True);"
                "l=sys.stdin.readline();print('GOT:'+l.strip(),flush=True)"
            ),
            background=True,
            encoding="gbk",
        )
        text = chunk_text(started)
        m = re.search(r"#(\d+)", text)
        assert m, text
        num = int(m.group(1))
        try:
            chunk = await process_tools_communicate(
                num, "write_stdin", text="emoji😀"
            )
            out = chunk_text(chunk)
            assert "无法用 gbk 编码" in out, out
            assert "已替换为" in out, out
            # 作者定稿：只报事实——不附「改用 patch_win_py_utf8 / 换 codec」建议，
            # 也不再带「未回显，可用 read_stdout 查看响应」
            assert "patch_win_py_utf8" not in out, out
            assert "换 codec" not in out, out
            assert "未回显" not in out, out
        finally:
            await process_tools_communicate(num, "send_sigkill")

    asyncio.run(main())



def test_list_command_json_string_without_no_shell():
    """通道把 list 参数字符串化后，应识别为 argv 并引导加 no_shell（而非
    直接喂 shell 报莫名其妙的语法错）。"""
    chunk = run(process_tools_exec('["echo", "ARGV-HINT"]'))
    assert is_error(chunk)
    text = _text(chunk)
    assert "no_shell" in text and "argv" in text.lower()


def test_list_object_without_no_shell_rejected():
    """真 list 直传（同进程调用）走类型检查，同样引导 no_shell。"""
    chunk = run(process_tools_exec(["echo", "x"]))
    assert is_error(chunk)
    assert "no_shell" in _text(chunk)


def test_sigint_reports_observed_result():
    """sigint 文案必须带实测结果：对 shell 包装的命令 Windows 常无效，
    不能谎称已终止。"""
    if os.name != "nt":
        import pytest

        pytest.skip("CTRL_BREAK 语义仅 Windows")
    import re

    async def main():
        started = await process_tools_exec(
            'cmd /c "ping -n 300 127.0.0.1"', background=True, name="sig-obs",
        )
        text = chunk_text(started)
        assert "已启动" in text
        m = re.search(r"#(\d+)", text)
        assert m, text
        num = int(m.group(1))
        try:
            chunk = await process_tools_communicate(num, "send_sigint")
            out = chunk_text(chunk)
            assert "CTRL_BREAK" in out
            assert "实测：" in out
        finally:
            await process_tools_communicate(num, "send_sigkill")

    asyncio.run(main())

def test_shell_cmd_explicit_runs_builtins():
    """显式 shell="cmd"：cmd 内建命令可用（echo 是内建，不走 PATHEXT）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("cmd 仅 Windows")
    chunk = run(process_tools_exec("echo CMD-E2E-OK", shell="cmd"))
    assert not is_error(chunk)
    text = _text(chunk)
    assert 'shell="cmd"' in text
    assert "CMD-E2E-OK" in text and "exit=0" in text


def test_shell_cmd_var_expansion():
    """cmd 的 %VAR% 展开（pwsh/bash 都不认这种写法）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("cmd 仅 Windows")
    chunk = run(process_tools_exec("echo CMDVAR=%ComSpec%", shell="cmd"))
    text = _text(chunk)
    assert "CMDVAR=" in text and "cmd.exe" in text.lower()


def test_shell_cmd_quoted_path_with_args():
    """cmd 原生认引号路径 + 参数，不需要 & 调用符（补丁只动 PowerShell）。"""
    if os.name != "nt":
        import pytest

        pytest.skip("cmd 仅 Windows")
    cmd = '"{}" -c "print(\'CMD-QPATH-OK\')"'.format(sys.executable)
    chunk = run(process_tools_exec(cmd, shell="cmd"))
    assert not is_error(chunk)
    text = _text(chunk)
    assert "CMD-QPATH-OK" in text and "exit=0" in text


def test_shell_cmd_with_utf8_patch():
    """cmd 壳 + patch_win_py_utf8：UTF-8 走 env（前缀对 cmd 为空），中文完好。"""
    if os.name != "nt":
        import pytest

        pytest.skip("Windows 专属补丁")
    chunk = run(
        process_tools_exec(
            py_cmd("print('CMD-UTF8 中文😀')"), shell="cmd", patch_win_py_utf8=True,
        )
    )
    text = _text(chunk)
    assert not is_error(chunk)
    assert "CMD-UTF8 中文😀" in text


def test_shell_cmd_rejected_on_posix():
    """cmd 仅 Windows：POSIX 上明确报错引导，不静默换成别的壳。"""
    if os.name == "nt":
        import pytest

        pytest.skip("反向用例：仅 POSIX")
    chunk = run(process_tools_exec("echo x", shell="cmd"))
    assert is_error(chunk)
    assert "Windows" in _text(chunk)

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


# ── cwd 对齐内核（0.6.8）────────────────────────────────


def test_exec_relative_cwd_mounts_project_dir(monkeypatch, tmp_dir):
    """E2E：相对 cwd 挂第一个项目目录，子进程真实工作目录命中。

    mock utils.get_project_dir_paths（resolve_cwd 在 utils 模块内查全局，
    monkeypatch 模块属性有效；exec 经 from-import 拿到的 resolve_cwd 是
    同一函数对象）。"""
    import utils as utils_mod

    proj_sub = os.path.join(tmp_dir, "proj", "sub")
    os.makedirs(proj_sub)
    monkeypatch.setattr(
        utils_mod,
        "get_project_dir_paths",
        lambda: [os.path.dirname(proj_sub)],
    )
    chunk = run(
        process_tools_exec(py_cmd("import os;print(os.getcwd())"), cwd="sub")
    )
    assert not is_error(chunk)
    text = _text(chunk)
    assert os.path.normcase(proj_sub) in os.path.normcase(text)


def test_exec_empty_cwd_uses_base_dir(monkeypatch, tmp_dir):
    """E2E：cwd 空串回落 get_tool_base_dir()（exec 模块命名空间绑定，
    需对 tools.exec 逐模块 setattr——值绑定陷阱）。"""
    import tools.exec as exec_mod

    base = os.path.join(tmp_dir, "wsroot")
    os.makedirs(base)
    monkeypatch.setattr(exec_mod, "get_tool_base_dir", lambda: base)
    chunk = run(process_tools_exec(py_cmd("import os;print(os.getcwd())")))
    assert not is_error(chunk)
    assert os.path.normcase(base) in os.path.normcase(_text(chunk))


def test_exec_bad_relative_cwd_under_base_reports_error(monkeypatch, tmp_dir):
    """相对 cwd 拼出不存在的目录 → 启动失败返回错误（不崩溃、不静默）。"""
    import utils as utils_mod

    proj = os.path.join(tmp_dir, "proj")
    os.makedirs(proj)
    monkeypatch.setattr(utils_mod, "get_project_dir_paths", lambda: [proj])
    chunk = run(
        process_tools_exec(py_cmd("print(1)"), cwd="no_such_sub_dir_xyz")
    )
    assert is_error(chunk)
    text = _text(chunk)
    # manager.start 内部捕 OSError 转 RuntimeError（带编号消耗提示）；
    # Windows 文案「目录名称无效」，POSIX 是 Not a directory
    assert "已消耗" in text or "目录名称无效" in text or "Not a directory" in text
