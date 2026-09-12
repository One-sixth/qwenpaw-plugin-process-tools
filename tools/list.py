# -*- coding: utf-8 -*-
"""process_tools_list — 列出当前会话的全部托管进程。"""

import logging

try:
    from ..manager import get_manager
    from ..utils import make_error, make_success, parse_int
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import make_error, make_success, parse_int

logger = logging.getLogger(__name__)


async def process_tools_list(limit: int = 10):
    """列出当前会话创建的全部托管进程（每行：编号、状态、退出码、运行时长、命令摘要）。进程按会话隔离，看不到也管不了其他会话的进程。默认列出最近创建的 10 个，新进程在前；limit≤0 列出全部。

    Args:
        limit: 最多列出的进程数，默认 10（新进程在前）；≤0 = 全部列出。
    """
    lmt, lerr = parse_int(limit, "limit")
    if lerr:
        return make_error("列出进程", lerr)

    procs = get_manager().list_session()
    if not procs:
        return make_success(
            "本会话暂无托管进程。用 process_tools_exec 启动（background=True 后台运行）。"
        )
    running = sum(1 for m in procs if m.status == "running")
    # 新进程在前：编号单调分配永不复用，num 降序即创建顺序新→旧
    ordered = sorted(procs, key=lambda m: m.num, reverse=True)
    total = len(ordered)
    shown = ordered[:lmt] if (lmt is not None and lmt > 0) else ordered
    header = f"本会话共 {total} 个进程（运行中 {running}）"
    if len(shown) < total:
        header += f"，显示最新 {len(shown)} 个（limit=0 看全部）"
    header += "："
    lines = [header]
    lines.extend(m.summary_line() for m in shown)
    return make_success("\n".join(lines))
