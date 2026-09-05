# -*- coding: utf-8 -*-
"""process_tools_check — 查看托管进程状态与输出。"""

import logging

try:
    from ..manager import get_manager
    from ..utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_id,
        truncate_line,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_id,
        truncate_line,
    )

logger = logging.getLogger(__name__)


async def process_tools_check(process_id, tail_lines: int = 20):
    """查看本会话某个托管进程的当前状态：运行中/已完成/失败/已终止、退出码、已运行时长、命令、日志路径，以及净化输出日志的末尾若干行。已结束进程同样可查（历史保留在会话内）。

    Args:
        process_id: 进程编号 #N（exec 返回的编号，支持 1、"1"、"#1" 三种写法）。
        tail_lines: 返回输出日志的末尾行数，默认 20，最大 200；0 表示不看输出只看状态。
    """
    num, err = parse_process_id(process_id)
    if err or num is None:
        return make_error("查看进程", err or "process_id 无效")
    tail, terr = _parse_tail(tail_lines)
    if terr:
        return make_error("查看进程", terr)

    mp = get_manager().get(num)
    if mp is None:
        return make_error(
            "查看进程",
            f"本会话不存在进程 #{num}",
            "用 process_tools_list 查看当前会话的全部进程编号",
        )

    mins, secs = divmod(int(mp.elapsed()), 60)
    code = f" exit={mp.exit_code}" if mp.exit_code is not None else ""
    label = f"「{mp.name}」" if mp.name else ""
    head = (
        f"#{mp.num}{label} [{mp.status}{code}] "
        f"已运行 {mins // 60:02d}:{mins % 60:02d}:{secs:02d}\n"
        f"命令：{truncate_line(mp.command, 120)}\n"
        f"日志：{mp.log_path}"
    )
    if tail == 0:
        return make_success(head)
    lines, size = mp.get_log_tail(tail)
    if not lines:
        return make_success(head + "\n（暂无输出）")
    body = "\n".join(lines)
    more = ""
    if size > len(body.encode("utf-8", errors="replace")):
        more = f"\n（日志共 {size} 字节，可用 file_tools_read_lines 读全文）"
    return make_success(f"{head}\n输出末尾：\n{body}{more}")


def _parse_tail(value):
    tail, err = parse_int(value, "tail_lines")
    if err:
        return None, err
    tail = max(min(tail if tail is not None else 20, 200), 0)
    return tail, None
