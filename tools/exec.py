# -*- coding: utf-8 -*-
"""process_tools_exec — 启动托管 shell 进程（前台等待 / 后台运行）。"""

import asyncio
import logging
import time
from typing import Optional

try:
    from ..manager import get_manager
    from ..utils import (
        get_workspace_dir,
        make_error,
        make_success,
        parse_int,
        resolve_cwd,
        truncate_line,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import (
        get_workspace_dir,
        make_error,
        make_success,
        parse_int,
        resolve_cwd,
        truncate_line,
    )

logger = logging.getLogger(__name__)

OUTPUT_HARD_CAP = 16384  # 前台输出字符上限（超出落日志给路径）


async def process_tools_exec(
    command: str,
    background: bool = False,
    timeout: int = 60,
    cwd: str = "",
    max_output_chars: int = 4096,
):
    """启动一条 shell 命令作为托管进程运行，进程归当前会话所有、编号 #N 单调分配。background=False 时前台等待命令结束后返回完整净化输出（超时自动杀进程并返回超时前输出末尾）；background=True 时立即返回进程编号，进程在后台持续运行，用 process_tools_check 查状态、process_tools_notice 挂完成通知、process_tools_communicate 交互。命令在系统默认 shell（POSIX /bin/sh、Windows cmd.exe）中以管道方式执行，无 TTY，进度条类程序会自行降级为普通输出。每个会话同时最多有 5 个运行中进程，已结束的不占名额。

    Args:
        command: 要执行的 shell 命令字符串，跨平台写法自行负责（如需要 PowerShell 就写 powershell -Command "..."）。
        background: 是否后台运行。False=前台等待结束再返回（默认），True=启动后立即返回进程编号。
        timeout: 前台模式等待秒数，超时后强杀进程并返回超时前输出末尾倒数 20 行。0 或负数表示永不超时一直等。后台模式忽略此参数。
        cwd: 工作目录，相对路径相对于 workspace。空串表示使用 workspace 目录。
        max_output_chars: 前台模式返回输出的字符上限，超出则截断并提示完整日志路径，上限 16384。
    """
    if not command or not str(command).strip():
        return make_error("启动进程", "command 为空", "请提供要执行的命令")

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
    try:
        mp = await manager.start(str(command), cwd=resolved_cwd)
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
            f"进程 #{mp.num} 已启动（pid={mp.pid}）\n"
            f"命令：{truncate_line(mp.command, 120)}\n"
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
    header = (
        f"进程 #{mp.num} 结束（exit={mp.exit_code}，用时 {mins}m{secs:02d}s）\n"
        f"命令：{truncate_line(mp.command, 120)}"
    )
    if len(output) > mo_chars:
        return make_success(
            f"{header}\n（输出超出 {mo_chars} 字符，以下截断，"
            f"完整日志：{mp.log_path}）\n{output[:mo_chars]}\n<<truncated>>"
        )
    text = f"{header}\n{output}" if output else f"{header}\n（无输出）"
    return make_success(text)
