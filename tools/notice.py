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
        escape_ctrl,
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
        escape_ctrl,
        make_error,
        make_success,
        parse_int,
        parse_process_ids,
    )

logger = logging.getLogger(__name__)


async def process_tools_notice(
    process_id,
    interval_seconds: int = 0,
    cancel: bool = False,
):
    """注册后台进程的完成/周期通知（进程结束推送状态并唤醒处理）；cancel=True 取消注册并撤回尚未投出的通知。
⚠️ 通知只在会话空闲（你停止调用工具、回合结束）时投出：连续调工具期间挂起不出现、停手后补发。
仅运行中进程可注册，已结束→报错并引导 process_tools_check。

Args:
    process_id: 进程编号：单个（1 / "1" / "#1"）或编号列表（如 [1, 2]），逐个独立校验，任一无效不影响其他编号。
    interval_seconds: 周期通知间隔秒数。0（默认）=仅完成通知；1~29 非法；≥30 生效（整批共享，长任务推荐 ≥900）。cancel=True 时忽略。
    cancel: True=取消模式（默认 False=注册/更新通知）。
    """
    nums, err = parse_process_ids(process_id)
    action = "取消通知" if cancel else "注册通知"
    if err or not nums:
        return make_error(action, err or "process_id 无效")

    if cancel:
        return _cancel_notices(nums)

    interval, ierr = parse_int(interval_seconds, "interval_seconds")
    if ierr:
        return make_error(action, ierr)
    interval = interval if interval is not None else 0
    if 0 < interval < MIN_INTERVAL_SECONDS:
        return make_error(
            action,
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


def _cancel_notices(nums: list):
    """取消模式：逐个取消注册并撤回待发通知（同步原子段，无 await）。

    三态归档：已取消 / 无注册 / 不存在；部分成功语义同注册模式
    （有已取消即 success）。已结束进程的完成通知若尚在聚合投递
    窗口内，撤回正是本模式的核心价值场景（撤回条数不进报告——
    作者定稿：知道取消成功即可）。
    """
    manager = get_manager()
    notifier = get_notifier()
    cancelled, unregistered, missing = [], [], []
    for n in nums:
        mp = manager.get(n)
        if mp is None:
            missing.append(n)
            continue
        if notifier.cancel(mp.key, mp.num) < 0:
            unregistered.append(mp)
        else:
            cancelled.append(mp)

    parts = []
    if cancelled:
        parts.append(
            "已取消：" + "、".join(f"#{m.num}" for m in cancelled)
        )
    if unregistered:
        parts.append(
            f"无注册：{_enum_ints([m.num for m in unregistered])}"
            "（没有已注册的通知）"
        )
    if missing:
        parts.append(
            f"不存在：{_enum_ints(missing)}"
            "（用 process_tools_list 查看全部编号）"
        )
    text = "\n".join(parts)
    return make_success(text) if cancelled else make_error(text)


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
    label = f"#{mp.num}「{escape_ctrl(mp.name)}」" if mp.name else f"#{mp.num}"
    return f"{label} {mp.status} exit={mp.exit_code}"


def _cancel_existing(notice: Notice) -> None:
    if notice.periodic_task and not notice.periodic_task.done():
        notice.periodic_task.cancel()
    notice.periodic_task = None
