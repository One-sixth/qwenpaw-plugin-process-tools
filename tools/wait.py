# -*- coding: utf-8 -*-
"""process_tools_wait — 前台主动等待后台进程结束（单/多，all 语义，超时不杀）。"""

import asyncio
import logging
from typing import Optional

try:
    from ..manager import STATUS_RUNNING, get_manager
    from ..utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_ids,
        truncate_line,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import STATUS_RUNNING, get_manager
    from utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_ids,
        truncate_line,
    )

logger = logging.getLogger(__name__)


async def process_tools_wait(
    process_id,
    timeout: int = 60,
    tail_lines: int = 20,
):
    """前台等待一个或一批后台进程结束（all 语义：全部结束才返回）。适用「background 启动后先干别的，快结束时回来收口」。与前台 exec 的本质区别：本工具只是等待者——超时只报「仍运行中」，绝不杀进程；被取消也只放弃等待，进程不受影响。已结束进程立即幂等返回终态。detach 脱离进程对本插件不可见，无法用本工具等待（需按 PID 用系统命令自理）。

    Args:
        process_id: 进程编号：单个（1 / "1" / "#1"）或编号列表（如 [1, 2] 或 JSON 数组字符串 "[1,2]"），列表时全部结束才返回（all 语义）。
        timeout: 总等待预算秒数（对整批共享），0 或负数表示一直等到全部结束。超时返回「仍运行中」，进程不受影响，可再次 wait 或挂 process_tools_notice 等异步通知。
        tail_lines: 每个进程返回的日志尾部行数，默认 20，最大 200；0 = 只回状态行（批量收口时最省 token）。
    """
    nums, err = parse_process_ids(process_id)
    if err:
        return make_error("等待进程", err)
    t, terr = parse_int(timeout, "timeout")
    if terr:
        return make_error("等待进程", terr)
    timeout_s: Optional[float] = float(t) if t and t > 0 else None
    tl, tlerr = _parse_tail(tail_lines)
    if tlerr:
        return make_error("等待进程", tlerr)

    manager = get_manager()
    mps = []
    for n in nums:
        mp = manager.get(n)
        if mp is None:
            return make_error(
                "等待进程",
                f"本会话不存在进程 #{n}（detach 脱离进程不可等待）",
                "用 process_tools_list 查看当前会话的全部进程编号",
            )
        mps.append(mp)

    running = [m for m in mps if m.status == STATUS_RUNNING]
    waited = 0.0
    if running:
        waited = await _await_all(running, timeout_s)
        if waited is None:
            # 超时：报「仍运行中」，不杀、不给任何杀进程引导（W1/W3 拍板）
            still = "、".join(
                f"#{m.num}「{m.name}」" if m.name else f"#{m.num}"
                for m in mps if m.status == STATUS_RUNNING
            )
            finished = [m for m in mps if m.status != STATUS_RUNNING]
            done_part = ""
            if finished:
                done_part = "\n已结束：" + "、".join(
                    f"#{m.num} {m.status} exit={m.exit_code}" for m in finished
                )
            return make_error(
                "等待进程",
                f"进程 {still} 仍运行中（等待 {int(timeout_s or 0)} 秒超时，"
                f"进程不受影响）{done_part}",
                "可再次 process_tools_wait，或挂 process_tools_notice 等异步通知",
            )

    header = (
        f"{len(mps)} 个进程全部结束（本次等待 {int(waited)}s）："
        if len(mps) > 1
        else ""
    )
    blocks = [_describe(m, tl) for m in mps]
    return make_success("\n\n".join(x for x in [header] + blocks if x))


async def _await_all(running: list, timeout_s: Optional[float]) -> Optional[float]:
    """等全部 running 进程结束；返回实际等待秒数，超时返回 None。

    每个进程包一层 task 等 shield 化的 mp.wait()——超时/取消时只毁包装
    task，共享的 _exit_future 毫发无损（多等待者安全）。
    """
    import time

    t0 = time.time()
    tasks = [asyncio.create_task(m.wait()) for m in running]
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        if pending:
            for tk in pending:
                tk.cancel()
            return None
        # 收集退出码（异常路径也照常收尾，让调用方看到终态）
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for tk in tasks:
            tk.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        for tk in tasks:
            if not tk.done():
                tk.cancel()
    return time.time() - t0


def _describe(mp, tail_lines: int) -> str:
    """单进程结果块（check 同风格，多进程时作为小节）。"""
    mins, secs = divmod(int(mp.elapsed()), 60)
    code = "" if mp.exit_code is None else f" exit={mp.exit_code}"
    label = f"「{mp.name}」" if mp.name else ""
    head = (
        f"#{mp.num}{label} [{mp.status}{code}] "
        f"{mins // 60:02d}:{mins % 60:02d}:{secs:02d}  "
        f"$ {truncate_line(mp.command.replace(chr(10), ' '), 120)}"
    )
    if tail_lines <= 0:
        return head
    lines, _size = mp.get_log_tail(tail_lines)
    if not lines:
        return head + "\n（暂无输出）"
    return head + "\n输出末尾：\n" + "\n".join(lines)


def _parse_tail(value):
    tail, err = parse_int(value, "tail_lines")
    if err:
        return None, err
    tail = max(min(tail if tail is not None else 20, 200), 0)
    return tail, None
