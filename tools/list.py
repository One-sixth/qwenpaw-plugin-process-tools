# -*- coding: utf-8 -*-
"""process_tools_list — 列出当前会话的全部托管进程。"""

import logging

try:
    from ..manager import get_manager
    from ..notifier import get_notifier
    from ..utils import make_error, make_success, parse_int
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from notifier import get_notifier
    from utils import make_error, make_success, parse_int

logger = logging.getLogger(__name__)


def _notice_tag(notice) -> str:
    """running 进程的通知注册标记（0.6.9）：周期与完成通知并存都显示。"""
    if notice.interval_active():
        return f" 🔔周期{notice.interval_seconds}s+完成通知"
    return " 🔔完成通知"


async def process_tools_list(limit: int = 10):
    """列出当前会话的托管进程，每行：编号、状态、退出码、运行时长、命令摘要。
    已注册通知的运行中进程带 🔔 标记。

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
    notifier = get_notifier()
    lines = [header]
    for m in shown:
        tag = ""
        if m.status == "running":
            # 仅活进程显示注册标记：已结束进程的注册记录在完成通知发出后
            # 仍残留于 _notices（completion_sent 只置位不弹出），必须按
            # 状态过滤；cancel 后记录已弹出，get 返回 None 天然不显示。
            notice = notifier.get(m.key, m.num)
            if notice is not None:
                tag = _notice_tag(notice)
        lines.append(m.summary_line(state_tag=tag))
    return make_success("\n".join(lines))
