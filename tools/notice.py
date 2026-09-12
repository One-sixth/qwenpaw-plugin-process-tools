# -*- coding: utf-8 -*-
"""process_tools_notice — 为托管进程注册完成/周期通知（单/批，逐个独立校验）。"""

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
        parse_process_ids,
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
        parse_process_ids,
    )

logger = logging.getLogger(__name__)


async def process_tools_notice(
    process_id,
    interval_seconds: int = 0,
):
    """若要动态接收后台进程状态的周期通知或结束通知，必须调用本工具注册进程状态通知：进程结束时推送一次完成通知；interval_seconds≥30 时每隔该秒数推送进度快照，长任务推荐 ≥900。可为单个或一批进程注册，编号逐个独立校验，无效编号不影响其他编号注册。注册后无需再用 wait 等待进程结束——系统会自动把通知发到用户侧并唤醒 agent。只能给运行中的进程注册；不存在/已结束的编号在结果中单独报告，已结束进程用 process_tools_check 获取结果。

    Args:
        process_id: 进程编号：单个（1 / "1" / "#1"）或编号列表（如 [1, 2]），逐个独立校验，任一无效不影响其他编号。
        interval_seconds: 周期通知间隔秒数。0（默认）=仅完成通知；1~29 非法；≥30 生效（对整批共享）。
    """
    nums, err = parse_process_ids(process_id)
    if err or not nums:
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

    # 逐个归档（部分成功语义，作者拍板 0.6.1）：有效编号照常注册，
    # 不存在/已结束的单独报告、不整批连坐。
    manager = get_manager()
    oks, missing, ended = [], [], []
    for n in nums:
        mp = manager.get(n)
        if mp is None:
            missing.append(n)
        elif mp.status != "running":
            # 已结束不注册也不立即投递：投递唤醒必然撞上当前前台 run
            # 的「会话忙」自锁（0.4.2 定谳）。结果获取归 check 管。
            ended.append(mp)
        else:
            oks.append(mp)

    # 归档无 await 点，注册段同样无 await——单线程 asyncio 无并发缝隙
    notifier = get_notifier()
    for mp in oks:
        _apply_one(mp, interval, notifier)

    text = _build_text(oks, missing, ended, interval)
    # 有生效即 success（部分成功也是成功）；全失败才 error
    return make_success(text) if oks else make_error(text)


def _apply_one(mp, interval: int, notifier) -> None:
    """注册或更新单个运行中进程的通知（校验已过），无返回。"""
    existing = notifier.get(mp.key, mp.num)
    if existing:
        # 重复注册：更新参数（间隔变化则重建周期任务）
        _cancel_existing(existing)
        existing.interval_seconds = interval
        if interval >= MIN_INTERVAL_SECONDS:
            notifier.register(mp, existing)  # 重启周期任务
        return

    notice = Notice(
        process_num=mp.num,
        session_key=mp.key,
        interval_seconds=interval,
        wake_channel=current_channel(),
    )
    notifier.register(mp, notice)


def _build_text(oks: list, missing: list, ended: list, interval: int) -> str:
    """三段式结果消息（定稿）：已生效 → 不存在 → 已结束，空段跳过。

    单个/批量/混合全部共用本规则，零特例；失败语义由 ToolResultState
    携带（全失败=error，部分成功=success），段落本身即原因。
    """
    parts = []
    if oks:
        parts.append(f"已生效：{_enum_nums(oks)}（{_mode_text(interval)}）")
    if missing:
        parts.append(
            f"不存在：{_enum_ints(missing)}"
            "（用 process_tools_list 查看全部编号）"
        )
    if ended:
        parts.append(
            f"已结束：{'、'.join(_ended_item(m) for m in ended)}"
            "（用 process_tools_check 查看结果）"
        )
    return "\n".join(parts)


def _enum_nums(mps: list) -> str:
    return "、".join(f"#{m.num}" for m in mps)


def _enum_ints(nums: list) -> str:
    return "、".join(f"#{n}" for n in nums)


def _mode_text(interval: int) -> str:
    if interval >= MIN_INTERVAL_SECONDS:
        return f"每 {interval}s 进度通知 + 完成通知"
    return "仅完成通知"


def _ended_item(mp) -> str:
    label = f"#{mp.num}「{mp.name}」" if mp.name else f"#{mp.num}"
    return f"{label} {mp.status} exit={mp.exit_code}"


def _cancel_existing(notice: Notice) -> None:
    if notice.periodic_task and not notice.periodic_task.done():
        notice.periodic_task.cancel()
    notice.periodic_task = None
