# -*- coding: utf-8 -*-
"""process_tools_exec — 启动托管 shell 进程（前台等待 / 后台运行）。"""

import asyncio
import json
import logging
import os
import time
from typing import Optional, Union

try:
    from ..manager import get_manager
    from ..utils import (
        escape_ctrl,
        get_tool_base_dir,
        make_error,
        make_success,
        mojibake_hint,
        parse_argv,
        parse_env,
        parse_int,
        ps_quote_call_fix,
        resolve_cwd,
        resolve_encoding,
        resolve_shell_argv,
        truncate_line,
        windows_utf8_prefix,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import (
        escape_ctrl,
        get_tool_base_dir,
        make_error,
        make_success,
        mojibake_hint,
        parse_argv,
        parse_env,
        parse_int,
        ps_quote_call_fix,
        resolve_cwd,
        resolve_encoding,
        resolve_shell_argv,
        truncate_line,
        windows_utf8_prefix,
    )

logger = logging.getLogger(__name__)

OUTPUT_HARD_CAP = 16384  # 前台输出字符上限（超出落日志给路径）


async def process_tools_exec(
    command: Union[str, list],
    background: bool = False,
    timeout: int = 60,
    cwd: str = "",
    max_output_chars: int = 4096,
    name: str = "",
    env: dict = {},
    hide_window: bool = False,
    detach: bool = False,
    encoding: str = "auto",
    patch_win_py_utf8: bool = False,
    no_shell: bool = False,
    shell: str = "auto",
):
    """启动一条 shell 命令作为托管进程运行。background=False 前台等待结束后返回净化输出（超时自动杀进程）；background=True 立即返回进程编号，进程后台持续运行。detach=True 启动不受托管的 OS 进程（只回 PID，托管能力全部失效）；no_shell=True 时 command 传 argv 列表原生直启。

    Args:
        command: 要执行的 shell 命令字符串（语法随 shell 参数，跨平台自行负责）；no_shell=True 时改传 argv 列表（如 ["python", "-c", "print(1)"]，或其 JSON 数组字符串）。
        background: 是否后台运行。False=前台等待结束再返回（默认），True=启动后立即返回进程编号。
        timeout: 前台模式等待秒数，超时后强杀进程并返回超时前输出末尾倒数 20 行。0 或负数表示永不超时一直等。后台模式忽略此参数。
        cwd: 工作目录，相对路径相对于第一个项目目录（未配置项目目录时相对于 workspace）。空串表示使用项目目录（未配置时 workspace）。
        max_output_chars: 前台模式返回输出的字符上限，超出则截断并提示完整日志路径，上限 16384。
        name: 给人看的进程标签（≤80 字符），出现在 exec/check/list 与通知里，多进程并跑时方便认亲；纯展示不影响执行。
        env: 增量环境变量，传 JSON 字典（如 {"PYTHONUNBUFFERED": "1"}），在宿主环境之上叠加、只对本进程生效。
        hide_window: Windows 下隐藏子进程的控制台窗口（CREATE_NO_WINDOW），跑不该弹窗的脚本时用；POSIX 忽略，与 detach=True 无叠加意义。
        detach: True 时启动完全不受本插件控制的脱离进程：立即返回 OS 级 PID（不是 #N 编号），无 stdin/stdout/stderr 管道（输出丢弃，需要就自己在 command 里重定向）、无超时、不进注册表（list/check/communicate/notice 都看不到它）、宿主退出不被清理、不占名额。之后管理需用系统命令按 PID 自理（Windows taskkill /F /T /PID、POSIX kill；注意 PID 是 shell 进程，杀整树要 /T）。background/timeout/max_output_chars/encoding 参数失效。
        encoding: 管道编解码 codec（输出解码与 stdin 编码共用）。auto（默认）=本机原生编码（Windows 取控制台码页，中文系统即 GBK，返回信息会写明实际值）；输出乱码或 stdin 中文失配时显式指定（如 utf-8 / gbk / cp437），未知 codec 会报错。
        patch_win_py_utf8: 仅在 Windows 有效，其他系统忽略。跑 Python 程序时推荐启用：向子进程注入 PYTHONUTF8=1、把 PowerShell 控制台 IO 编码钉成 UTF-8，并强制按 utf-8 解码（为 true 时忽略 encoding 参数，返回信息会注明）。
        no_shell: True=不经 shell 的原生直启（create_subprocess_exec），command 必须传 argv 列表；无重定向/管道/&&/通配符等 shell 语义（想要就得自己套一层 shell 命令），换来零引号地狱与元字符误伤。注意直启目标本身就是 cmd/sh 类解释器时，它会再解析自己的命令行，元字符免疫只对 argv 传参的真程序（python/git 等）有效。
        shell: 命令解释器：auto（默认——Windows pwsh > powershell > cmd、Linux bash > sh、macOS zsh > bash > sh，按可用性自动回落；旧值 default 等同 auto）/ pwsh（PowerShell 语法，Windows PowerShell 5.1 兜底）/ bash / cmd（Windows 原生 cmd.exe /c，仅 Windows：cmd 内建命令与 %VAR% 展开原样可用，引号路径直接写、不需要 & 调用符）。显式选定的 shell 不适用或找不到时报错引导，不静默换壳。no_shell=True 时忽略。PowerShell 下以引号开头的 exe 路径会自动补 `&` 调用运算符，$var 是 PS 插值语法。
    """
    # ── Windows Python UTF-8 补丁（非 Windows 忽略）──
    patch_utf8 = bool(patch_win_py_utf8) and os.name == "nt"
    if patch_utf8:
        enc_name = "utf-8"
        enc_note = (
            'stdin/stdout/stderr encoding="utf-8" '
            "(patch_win_py_utf8=true：已强制 UTF-8，encoding 参数被忽略)"
        )
    else:
        enc_name, enc_auto, enc_err = resolve_encoding(encoding)
        if enc_err:
            return make_error("启动进程", enc_err)
        if enc_auto:
            enc_note = (
                f'stdin/stdout/stderr encoding="{enc_name}" '
                '(auto=本机编码；若输出很多 "??" 或 乱码请显式调整 encoding，如 utf-8)'
            )
        else:
            enc_note = f'stdin/stdout/stderr encoding="{enc_name}"'

    # ── 参数规范化 ──
    display_command = ""
    shell_used: Optional[str] = None
    prefix: Optional[list] = None  # no_shell 分支不赋值，shell_line 段会读
    if no_shell:
        argv, argv_err = parse_argv(command)
        if argv_err:
            return make_error("启动进程", argv_err)
        spawn_target: Union[str, list] = argv
        display_command = " ".join(argv)
    else:
        if isinstance(command, (list, tuple)):
            return make_error(
                "启动进程", "command 传 argv 列表需同时设 no_shell=True",
            )
        if not command or not str(command).strip():
            return make_error("启动进程", "command 为空", "请提供要执行的命令")
        display_command = str(command)
        # 通道会把 list 参数 JSON 字符串化（schema 声明 command 是 string）：
        # 用户传 ["echo","x"] 时这里收到 '["echo","x"]'，直接喂 shell 只会报
        # 莫名其妙的语法错（PowerShell: “[”后缺少类型名称）。识别出来给正确用法。
        if display_command.lstrip().startswith("["):
            try:
                parsed = json.loads(display_command)
            except ValueError:
                parsed = None
            if (
                isinstance(parsed, list)
                and parsed
                and all(isinstance(x, str) for x in parsed)
            ):
                return make_error(
                    "启动进程",
                    "command 收到 argv 列表形态（列表或其 JSON 字符串）",
                    "传 argv 列表需同时设 no_shell=True；或改传普通命令字符串",
                )
        # shell 枚举：auto→按平台可用性选壳，包装成 argv 直启
        # （不经 create_subprocess_shell 的系统默认壳）
        prefix, shell_used, shell_err = resolve_shell_argv(shell)
        if shell_err:
            return make_error("启动进程", shell_err)
        if shell_used == "cmd":
            # cmd 的 /c 引号规则：命令以引号开头且引号多于两个时，会把首尾各
            # 剥掉一个（旧行为）——`"C:\a b\x.exe" -c "print(1)"` 会被拆坏。
            # 而 argv 形式还会叠一层 list2cmdline 的 `\"` 转义，cmd 不认。
            # 故交原始命令行给 create_subprocess_shell：Windows 上它拼成
            # `COMSPEC /c "<命令>"`，外层引号被剥离后正好还原用户原文。
            spawn_target = str(command)
        else:
            utf8_prefix = windows_utf8_prefix(shell_used) if patch_utf8 else ""
            # 引号开头的路径命令：PowerShell 会当字符串表达式解析（后跟参数即
            # ParserError），补 `&` 调用运算符；cmd/bash 与其他写法不动。
            shell_command = ps_quote_call_fix(str(command), shell_used)
            spawn_target = [*prefix, utf8_prefix + shell_command]
    shell_line = f'shell="{shell_used}"\n' if shell_used else ""
    # Windows 上 bash 常解析到 System32\bash.exe（WSL 启动器）：实测它会把
    # `$变量` 与引号提前吞掉（`X=42; echo "VAR-$X"` 输出 VAR-，exit=0），
    # 属静默错果——这里必须显式提示，不能让人当正常结果用。
    bash_exe = str(prefix[0]) if prefix else ""
    if (
        os.name == "nt"
        and shell_used == "bash"
        and "system32" in bash_exe.lower()
    ):
        shell_line += (
            "⚠️ Windows 的 bash 解析到 WSL 启动器（System32\\bash.exe）："
            "$变量与引号可能被提前吞掉（实测 `X=42; echo $X` 输出空值）。"
            "要可靠脚本请用 no_shell 原生直启，或改用 pwsh / cmd。\n"
        )

    clean_env, env_err = parse_env(env)

    if env_err:
        return make_error("启动进程", env_err)

    parsed_timeout, err = parse_int(timeout, "timeout")
    if err:
        return make_error("启动进程", err)
    timeout = parsed_timeout or 0
    parsed_max, err = parse_int(max_output_chars, "max_output_chars")
    if err:
        return make_error("启动进程", err)
    mo_chars = min(max(parsed_max if parsed_max is not None else 4096, 1),
                   OUTPUT_HARD_CAP)

    # resolve() 真实走文件系统（断连网络盘可阻塞数十秒），丢线程防卡事件
    # 循环（内核 execute_shell_command 同款处理）；空串回落基准目录。
    resolved_cwd = await asyncio.to_thread(resolve_cwd, cwd)
    if not resolved_cwd:
        resolved_cwd = get_tool_base_dir() or ""

    manager = get_manager()

    # ── detach：脱离托管，一次性 spawn，不进注册表 ──
    if detach:
        try:
            pid = await asyncio.to_thread(
                manager.spawn_detached, spawn_target, resolved_cwd, clean_env,
                patch_utf8,
            )
        except RuntimeError as e:
            return make_error("启动进程", str(e))
        except OSError as e:
            return make_error("启动进程", f"无法启动：{e}")
        except Exception as e:  # noqa: BLE001
            logger.exception("process_tools_exec detach 启动失败")
            return make_error("启动进程", f"启动出错：{e}")
        label = f"「{escape_ctrl(str(name or '').strip()[:80])}」" if str(name or "").strip() else ""
        shell_part = f'shell="{shell_used}"，' if shell_used else ""
        return make_success(
            f"脱离进程 {label}已启动（pid={pid}，{shell_part}不受本插件托管）\n"
            f"命令：{escape_ctrl(truncate_line(display_command, 120))}\n"
            "该进程无管道无日志、不占名额、宿主退出后继续运行；"
            "list/check/communicate/notice 对它全部无效。"
            f"管理请用系统命令按 PID 自理（如 taskkill /F /T /PID {pid}）。"
        )

    try:
        mp = await manager.start(
            spawn_target,
            cwd=resolved_cwd,
            name=name,
            env=clean_env,
            hide_window=hide_window,
            encoding=enc_name,
            python_utf8=patch_utf8,
            display=display_command,
        )
    except RuntimeError as e:
        # 典型场景：no_shell 传了内建命令（echo）或路径写错。manager 把
        # 启动异常统一包成 RuntimeError，原始类型在 __cause__ 里——点出是
        # 哪个可执行文件找不到，比裸 [WinError 2] 有用。
        cause = getattr(e, "__cause__", None)
        if (
            isinstance(cause, FileNotFoundError)
            and isinstance(spawn_target, list)
            and spawn_target
        ):
            return make_error(
                "启动进程", f"找不到可执行文件「{spawn_target[0]}」", str(e),
            )
        return make_error("启动进程", str(e))
    except OSError as e:
        # Windows 坏 cwd 抛 NotADirectoryError（与 FileNotFoundError 是兄弟，
        # 只捕 FileNotFoundError 永不命中——审查 R2），统一收 OSError
        return make_error("启动进程", f"无法启动：{e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("process_tools_exec 启动失败")
        return make_error("启动进程", f"启动出错：{e}")

    # ── 后台模式：立即返回编号 ──
    if background:
        return make_success(
            f"进程 #{mp.num}{'「' + escape_ctrl(mp.name) + '」' if mp.name else ''} "
            f"已启动（pid={mp.pid}）\n"
            f"命令：{escape_ctrl(truncate_line(mp.command, 120))}\n"
            f"{shell_line}"
            f"{enc_note}\n"
            f"日志：{mp.log_path}\n"
            f"后续可用 process_tools_check({mp.num}) 查状态、"
            f"process_tools_communicate({mp.num}, ...) 交互、"
            f"process_tools_notice({mp.num}) 挂完成通知。"
        )

    # ── 前台模式 ──
    start_ts = time.time()
    wait_timeout: Optional[float] = float(timeout) if timeout > 0 else None
    try:
        rc = await mp.wait(timeout=wait_timeout)
    except asyncio.CancelledError:
        # 被外部取消（用户点停止等）：尽力 kill + 短等，绝不留孤儿
        try:
            await asyncio.shield(mp.kill())
        except Exception:  # noqa: BLE001
            pass
        raise

    if rc is None and wait_timeout is not None:
        # 超时 → 杀进程 → 返回超时前输出末尾 20 行 + 自解释建议。
        # 插件文本（超时说明/建议/乱码提示）全部留在元信息区（分隔线之前），
        # 输出段只放程序输出——建议文案绝不能贴在输出末尾被当成程序 stdout。
        note = await mp.kill()
        lines, _size = mp.get_log_tail(20)
        tail = "\n".join(lines) if lines else ""
        hint = mojibake_hint(tail)
        out_part = (
            f"\n————以下为输出末尾————\n{tail}" if lines else "\n（无输出）"
        )
        return make_error(
            f"命令执行失败：等待 {timeout} 秒超时，进程已被终止（{note}）。"
            "如果是长时间任务，可加大 timeout 或改用 background=True 后台运行；"
            f"完整日志：{mp.log_path}。"
            + (f"\n{hint}" if hint else "")
            + out_part,
        )

    elapsed = time.time() - start_ts
    mins, secs = divmod(int(elapsed), 60)
    output = mp.get_log_text()
    hint = mojibake_hint(output)
    label = f"「{escape_ctrl(mp.name)}」" if mp.name else ""
    header = (
        f"进程 #{mp.num}{label} 结束（exit={mp.exit_code}，用时 {mins}m{secs:02d}s）\n"
        f"命令：{escape_ctrl(truncate_line(mp.command, 120))}\n"
        f"{shell_line}"
        f"{enc_note}"
        + (f"\n{hint}" if hint else "")
    )
    if len(output) > mo_chars:
        return make_success(
            f"{header}\n（输出超出 {mo_chars} 字符，以下截断，"
            f"完整日志：{mp.log_path}）\n"
            "————以下为stdout/stderr输出————\n"
            f"{output[:mo_chars]}\n<<<TRUNCATED>>>"
        )
    text = (
        f"{header}\n————以下为stdout/stderr输出————\n{output}"
        if output
        else f"{header}\n（无输出）"
    )
    return make_success(text)


