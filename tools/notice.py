# -*- coding: utf-8 -*-
"""process_tools_notice — 为托管进程注册完成/周期通知。"""

import logging

try:
    from ..manager import get_manager
    from ..notifier import (
        MIN_INTERVAL_SECONDS,
        RECOMMENDED_INTERVAL_SECONDS,
        Notice,
        get_notifier,
    )
    from ..utils import make_error, make_success, parse_int, parse_process_id
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from notifier import (
        MIN_INTERVAL_SECONDS,
        RECOMMENDED_INTERVAL_SECONDS,
        Notice,
        get_notifier,
    )
    from utils import make_error, make_success, parse_int, parse_process_id

logger = logging.getLogger(__name__)


async def process_tools_notice(
    process_id,
    interval_seconds: int = 0,
    wake_agent: bool = True,
):
    """为托管进程注册通知（opt-in，不注册则零通知）。进程结束时推送一次完成通知（状态+退出码+用时+日志路径+输出末10行）；interval_seconds≥30 时运行期间每隔该秒数推送进度快照，进程退出自动停止周期通知。通知以用户消息级别送达：QwenPaw 界面弹出通知气泡，且（wake_agent=True 时）唤醒 agent 处理——空闲立即回复，忙碌自动排队。周期性轮询烧 token，长任务推荐 interval ≥ 900 或干脆只用完成通知。对已结束进程注册会立即把当前状态作为完成通知发出。

    Args:
        process_id: 进程编号 #N（支持 1、"1"、"#1" 三种写法）。
        interval_seconds: 周期通知间隔秒数。0（默认）=只在结束时通知；≥30 生效；1~29 视为非法。推荐 ≥900。
        wake_agent: 通知是否唤醒 agent 触发回复，默认 True。False 时只发用户可见的通知气泡。
    """
    num, err = parse_process_id(process_id)
    if err or num is None:
        return make_error("注册通知", err or "process_id 无效")
    interval, ierr = parse_int(interval_seconds, "interval_seconds")
    if ierr:
        return make_error("注册通知", ierr)
    interval = interval if interval is not None else 0
    if 0 < interval < MIN_INTERVAL_SECONDS:
        return make_error(
            "注册通知",
            f"周期通知间隔 {interval}s 太小（最小 {MIN_INTERVAL_SECONDS}s）",
            f"用 0 表示仅完成通知，或 ≥{RECOMMENDED_INTERVAL_SECONDS}s 做低频进度推送",
        )

    mp = get_manager().get(num)
    if mp is None:
        return make_error(
            "注册通知",
            f"本会话不存在进程 #{num}",
            "用 process_tools_list 查看当前会话的全部进程编号",
        )

    notifier = get_notifier()
    key = mp.key
    existing = notifier.get(key, num)
    if mp.status != "running":
        # 已结束：立即发送完成通知（幂等注册表防重复）
        notice = existing or Notice(process_num=num, session_key=key)
        if notice.completion_sent:
            return make_success(
                f"进程 #{num} 已结束且完成通知已发送过，无需重复注册",
            )
        if not existing:
            notifier.set_notice(key, notice)
        # S3 修复：先置幂等标记再 await 投递（检查+置位在同一同步段完成，
        # 并发双调用只会投递一次；与 _send_completion 的路径语义对齐）
        notice.completion_sent = True
        text = notifier.build_completion_text(mp)
        result = await notifier.deliver(key, text, bool(wake_agent))
        return make_success(
            f"进程 #{num} 已结束（{mp.status}），完成通知已立即发出：{result}"
        )

    if existing:
        # 重复注册：更新参数（间隔变化则重建周期任务）
        _cancel_existing(existing)
        if interval >= MIN_INTERVAL_SECONDS:
            existing.interval_seconds = interval
            existing.wake_agent = bool(wake_agent)
            notifier.register(mp, existing)
            return make_success(
                f"进程 #{num} 通知已更新：每 {interval}s 进度 +"
                f" 完成通知（wake_agent={bool(wake_agent)}）",
            )
        existing.interval_seconds = 0
        existing.wake_agent = bool(wake_agent)
        return make_success(
            f"进程 #{num} 通知已更新：仅完成通知（wake_agent={bool(wake_agent)}）",
        )

    notice = Notice(
        process_num=num,
        session_key=key,
        interval_seconds=interval,
        wake_agent=bool(wake_agent),
    )
    notifier.register(mp, notice)
    if notice.interval_active():
        desc = f"每 {interval}s 进度通知 + 完成通知"
    else:
        desc = "仅完成通知"
    return make_success(
        f"进程 #{num} 已注册通知：{desc}（wake_agent={bool(wake_agent)}）。"
        "不注册则进程结束不会有任何推送。"
    )


def _cancel_existing(notice: Notice) -> None:
    if notice.periodic_task and not notice.periodic_task.done():
        notice.periodic_task.cancel()
    notice.periodic_task = None
