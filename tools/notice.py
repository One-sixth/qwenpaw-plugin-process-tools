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
    from ..utils import (
        current_channel,
        make_error,
        make_success,
        parse_int,
        parse_process_id,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from notifier import (
        MIN_INTERVAL_SECONDS,
        RECOMMENDED_INTERVAL_SECONDS,
        Notice,
        get_notifier,
    )
    from utils import (
        current_channel,
        make_error,
        make_success,
        parse_int,
        parse_process_id,
    )

logger = logging.getLogger(__name__)


async def process_tools_notice(
    process_id,
    interval_seconds: int = 0,
):
    """若要动态接收后台进程状态的周期通知或结束通知，必须调用本工具注册进程状态通知：进程结束时推送一次完成通知；interval_seconds≥30 时每隔该秒数推送进度快照，长任务推荐 ≥900。注册后无需再用 wait 等待进程结束——系统会自动把通知发到用户侧并唤醒 agent。只能给运行中的进程注册，已结束进程返回错误（用 process_tools_check 获取结果）。

    Args:
        process_id: 进程编号 #N（支持 1、"1"、"#1" 三种写法）。
        interval_seconds: 周期通知间隔秒数。0（默认）=仅完成通知；1~29 非法；≥30 生效。
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
        # 已结束进程不再立即投递：投递唤醒必然撞上当前前台 run 的
        # 「会话忙」自锁（notice 同步等重试 → 自己的 run 占着会话 →
        # 20 次全 409 → 10 分钟空转后失败）。结果获取归 check 管。
        label = f"#{num}「{mp.name}」" if mp.name else f"#{num}"
        return make_error(
            "注册通知",
            f"进程 {label} 已结束（{mp.status} exit={mp.exit_code}），无事件可等",
            "用 process_tools_check(process_id) 看状态/用时/输出尾部",
        )

    if existing:
        # 重复注册：更新参数（间隔变化则重建周期任务）
        _cancel_existing(existing)
        if interval >= MIN_INTERVAL_SECONDS:
            existing.interval_seconds = interval
            notifier.register(mp, existing)
            return make_success(
                f"进程 #{num} 通知已更新：每 {interval}s 进度 + 完成通知",
            )
        existing.interval_seconds = 0
        return make_success(
            f"进程 #{num} 通知已更新：仅完成通知",
        )

    notice = Notice(
        process_num=num,
        session_key=key,
        interval_seconds=interval,
        wake_channel=current_channel(),
    )
    notifier.register(mp, notice)
    if notice.interval_active():
        desc = f"每 {interval}s 进度通知 + 完成通知"
    else:
        desc = "仅完成通知"
    return make_success(
        f"进程 #{num} 已注册通知：{desc}"
    )


def _cancel_existing(notice: Notice) -> None:
    if notice.periodic_task and not notice.periodic_task.done():
        notice.periodic_task.cancel()
    notice.periodic_task = None
