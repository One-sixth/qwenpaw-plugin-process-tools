# -*- coding: utf-8 -*-
"""process_tools_exec — 启动托管 shell 进程（前台等待 / 后台运行）。"""

import asyncio
import logging
import time
from typing import Optional, Union

try:
    from ..manager import get_manager
    from ..utils import (
        get_workspace_dir,
        make_error,
        make_success,
        parse_argv,
        parse_env,
        parse_int,
        resolve_cwd,
        resolve_encoding,
        resolve_shell_argv,
        truncate_line,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import (
        get_workspace_dir,
        make_error,
        make_success,
        parse_argv,
        parse_env,
        parse_int,
        resolve_cwd,
        resolve_encoding,
        resolve_shell_argv,
        truncate_line,
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
    no_shell: bool = False,
    shell: str = "auto",
):
    """启动一条 shell 命令作为托管进程运行，进程归当前会话所有、编号 #N 单调分配。background=False 时前台等待命令结束后返回完整净化输出（超时自动杀进程并返回超时前输出末尾）；background=True 时立即返回进程编号，进程在后台持续运行，用 process_tools_check 查状态、process_tools_wait 等待进程结束、process_tools_notice 挂完成通知、process_tools_communicate 交互。命令默认在 shell=auto 下由选中的解释器以管道方式执行（Windows pwsh > powershell > cmd、Linux bash > sh、macOS zsh > bash > sh，按可用性自动回落），无 TTY；进度条类程序会自行降级为普通输出。返回信息会写明实际使用的 shell 与编码。detach=True 时改为启动完全不受本插件托管的 OS 进程（只回 PID，管道/超时/通知等托管能力全部失效）；no_shell=True 时 command 传 argv 列表原生直启、不经 shell。

    Args:
        command: 要执行的 shell 命令字符串（语法随 shell 参数，跨平台自行负责）；no_shell=True 时改传 argv 列表（如 ["python", "-c", "print(1)"]，或其 JSON 数组字符串）。
        background: 是否后台运行。False=前台等待结束再返回（默认），True=启动后立即返回进程编号。
        timeout: 前台模式等待秒数，超时后强杀进程并返回超时前输出末尾倒数 20 行。0 或负数表示永不超时一直等。后台模式忽略此参数。
        cwd: 工作目录，相对路径相对于 workspace。空串表示使用 workspace 目录。
        max_output_chars: 前台模式返回输出的字符上限，超出则截断并提示完整日志路径，上限 16384。
        name: 给人看的进程标签（≤80 字符），出现在 exec/check/list 与通知里，多进程并跑时方便认亲；纯展示不影响执行。
        env: 增量环境变量，传 JSON 字典（如 {"PYTHONUNBUFFERED": "1"}），在宿主环境之上叠加、只对本进程生效。
        hide_window: Windows 下隐藏子进程的控制台窗口（CREATE_NO_WINDOW），跑不该弹窗的脚本时用；POSIX 忽略，与 detach=True 无叠加意义。
        detach: True 时启动完全不受本插件控制的脱离进程：立即返回 OS 级 PID（不是 #N 编号），无 stdin/stdout/stderr 管道（输出丢弃，需要就自己在 command 里重定向）、无超时、不进注册表（list/check/communicate/notice 都看不到它）、宿主退出不被清理、不占名额。之后管理需用系统命令按 PID 自理（Windows taskkill /F /T /PID、POSIX kill；注意 PID 是 shell 进程，杀整树要 /T）。background/timeout/max_output_chars/encoding 参数失效。
        encoding: 管道编解码 codec（输出解码与 stdin 编码共用）。auto（默认）=本机原生编码（Windows 取控制台码页，中文系统即 GBK，返回信息会写明实际值）；输出乱码或 stdin 中文失配时显式指定（如 utf-8 / gbk / cp437），未知 codec 会报错。
        no_shell: True=不经 shell 的原生直启（create_subprocess_exec），command 必须传 argv 列表；无重定向/管道/&&/通配符等 shell 语义（想要就得自己套一层 shell 命令），换来零引号地狱与元字符误伤。注意直启目标本身就是 cmd/sh 类解释器时，它会再解析自己的命令行，元字符免疫只对 argv 传参的真程序（python/git 等）有效。
        shell: 命令解释器：auto（默认——Windows pwsh > powershell > cmd、Linux bash > sh、macOS zsh > bash > sh，按可用性自动回落；旧值 default 等同 auto）/ pwsh（PowerShell 语法，Windows PowerShell 5.1 兜底）/ bash。显式选定的 shell 找不到时报错引导，不静默换壳。no_shell=True 时忽略。注意 PowerShell 下带引号的 exe 路径需 `& ` 调用运算符前缀（如 & \"C:\\Program Files\\x.exe\"），且 $var 是 PS 插值语法。
    """
    # ── 参数规范化 ──
    display_command = ""
    shell_used: Optional[str] = None
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
        # shell 枚举：auto→按平台可用性选壳，包装成 argv 直启
        # （不经 create_subprocess_shell 的系统默认壳）
        prefix, shell_used, shell_err = resolve_shell_argv(shell)
        if shell_err:
            return make_error("启动进程", shell_err)
        spawn_target = [*prefix, str(command)]
    shell_line = f'shell="{shell_used}"\n' if shell_used else ""

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

    resolved_cwd = resolve_cwd(cwd)
    if not resolved_cwd:
        resolved_cwd = get_workspace_dir() or ""

    manager = get_manager()

    # ── detach：脱离托管，一次性 spawn，不进注册表 ──
    if detach:
        try:
            pid = await asyncio.to_thread(
                manager.spawn_detached, spawn_target, resolved_cwd, clean_env,
            )
        except RuntimeError as e:
            return make_error("启动进程", str(e))
        except OSError as e:
            return make_error("启动进程", f"无法启动：{e}")
        except Exception as e:  # noqa: BLE001
            logger.exception("process_tools_exec detach 启动失败")
            return make_error("启动进程", f"启动出错：{e}")
        label = f"「{str(name or '').strip()[:80]}」" if str(name or "").strip() else ""
        shell_part = f'shell="{shell_used}"，' if shell_used else ""
        return make_success(
            f"脱离进程 {label}已启动（pid={pid}，{shell_part}不受本插件托管）\n"
            f"命令：{truncate_line(display_command, 120)}\n"
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
            display=display_command,
        )
    except RuntimeError as e:
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
            f"进程 #{mp.num}{'「' + mp.name + '」' if mp.name else ''} "
            f"已启动（pid={mp.pid}）\n"
            f"命令：{truncate_line(mp.command, 120)}\n"
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
        # 超时 → 杀进程 → 返回超时前输出末尾 20 行 + 自解释建议
        note = await mp.kill()
        lines, _size = mp.get_log_tail(20)
        tail = "\n".join(lines) if lines else "（无输出）"
        return make_error(
            "命令执行",
            f"等待 {timeout} 秒超时，进程已被终止（{note}）。输出末尾 20 行：\n{tail}",
            "如果是长时间任务，可加大 timeout 或改用 background=True 后台运行；"
            f"完整日志：{mp.log_path}",
        )

    elapsed = time.time() - start_ts
    mins, secs = divmod(int(elapsed), 60)
    output = mp.get_log_text()
    label = f"「{mp.name}」" if mp.name else ""
    header = (
        f"进程 #{mp.num}{label} 结束（exit={mp.exit_code}，用时 {mins}m{secs:02d}s）\n"
        f"命令：{truncate_line(mp.command, 120)}\n"
        f"{shell_line}"
        f"{enc_note}"
    )
    if len(output) > mo_chars:
        return make_success(
            f"{header}\n（输出超出 {mo_chars} 字符，以下截断，"
            f"完整日志：{mp.log_path}）\n{output[:mo_chars]}\n<<truncated>>"
        )
    text = f"{header}\n{output}" if output else f"{header}\n（无输出）"
    return make_success(text)
