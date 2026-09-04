# -*- coding: utf-8 -*-
"""process_tools_list — 列出当前会话的全部托管进程。"""

import logging

try:
    from ..manager import MAX_RUNNING_PER_SESSION, get_manager
    from ..utils import make_success
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import MAX_RUNNING_PER_SESSION, get_manager
    from utils import make_success

logger = logging.getLogger(__name__)


async def process_tools_list():
    """列出当前会话创建的全部托管进程（每行：编号、状态、退出码、运行时长、命令摘要）。进程按会话隔离，看不到也管不了其他会话的进程。运行中进程超过 5 个时无法再启动新进程，已结束的不占名额。

    Args:
    """
    procs = get_manager().list_session()
    if not procs:
        return make_success(
            "本会话暂无托管进程。用 process_tools_exec 启动（background=True 后台运行）。"
        )
    running = sum(1 for m in procs if m.status == "running")
    lines = [
        f"本会话共 {len(procs)} 个进程"
        f"（运行中 {running}/{MAX_RUNNING_PER_SESSION}）：",
    ]
    lines.extend(m.summary_line() for m in procs)
    return make_success("\n".join(lines))
