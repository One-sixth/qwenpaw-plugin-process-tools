# -*- coding: utf-8 -*-
"""process_tools_communicate — 与托管进程交互：stdin / stdout / 信号。"""

import logging
from typing import Optional

try:
    from ..manager import RING_LIMIT, get_manager
    from ..sanitizer import sanitize_full
    from ..utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_id,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import RING_LIMIT, get_manager
    from sanitizer import sanitize_full
    from utils import (
        make_error,
        make_success,
        parse_int,
        parse_process_id,
    )

logger = logging.getLogger(__name__)

ACTIONS = ("write_stdin", "read_stdout", "send_sigint", "send_sigkill")


async def process_tools_communicate(
    process_id,
    action: str,
    text: str = "",
    append_newline: bool = True,
    read_offset: int = 0,
    max_bytes: int = 8192,
    group: bool = False,
):
    """与托管进程交互。action 四选一：write_stdin=向进程标准输入写文本（应答交互提示、给 REPL 送代码等）；read_stdout=从 512KB 环形缓冲读取原始输出（净化后文本，按 read_offset 增量续读，适合轮询长时间运行的进度）；send_sigint=发送中断信号（等价 Ctrl+C，程序可做优雅退出/checkpoint）；send_sigkill=强杀进程及其全部子孙进程。环形缓冲只保留最近 512KB，更早的输出用 process_tools_check 或直接读日志文件。

    Args:
        process_id: 进程编号 #N（支持 1、"1"、"#1" 三种写法）。
        action: 操作类型，write_stdin / read_stdout / send_sigint / send_sigkill 四选一。
        text: write_stdin 时要写入的文本；其他 action 忽略。
        append_newline: write_stdin 时自动在文本末尾补换行符（模拟回车提交），text 已以换行结尾则不重复补。
        read_offset: read_stdout 的起始偏移，传上次返回的『下一偏移』增量续读，0 表示从缓冲现存最旧处开始。注意按原始字节切片：边界可能落在半行/半个多字节字符上，边界处可能出现残行或替换符，完整准确内容以日志文件（check 返回路径）为准。
        max_bytes: read_stdout 单次最多返回的字节数，默认 8192，上限 65536。
        group: send_sigint 时是否对整进程组发信号（True=连子进程一起中断，False=只中断主进程）。Windows 下 sigint 走 CTRL_BREAK，总是整组生效，此参数无差别。send_sigkill 本来就杀整棵树，忽略此参数。
    """
    num, err = parse_process_id(process_id)
    if err or num is None:
        return make_error("进程交互", err or "process_id 无效")
    if action not in ACTIONS:
        return make_error(
            "进程交互",
            f"action「{action}」无效，仅支持 {' / '.join(ACTIONS)}",
        )

    mp = get_manager().get(num)
    if mp is None:
        return make_error(
            "进程交互",
            f"本会话不存在进程 #{num}",
            "用 process_tools_list 查看当前会话的全部进程编号",
        )

    if action == "write_stdin":
        return await _do_write_stdin(mp, text, append_newline)
    if action == "read_stdout":
        return await _do_read_stdout(mp, read_offset, max_bytes)
    if action == "send_sigint":
        if mp.status != "running":
            return make_error(
                "发送中断", f"进程 #{num} 已不存在（状态 {mp.status}）",
            )
        note = mp.signal_interrupt(group=bool(group))
        return make_success(note)
    # send_sigkill
    if mp.status != "running":
        return make_success(
            f"进程 #{num} 已经是结束状态（{mp.status}），无需强杀"
        )
    note = await mp.kill()
    return make_success(f"{note}，进程 #{num} 状态 → {mp.status}")


async def _do_write_stdin(mp, text: str, append_newline: bool):
    if not text:
        return make_error(
            "写入 stdin", "text 为空", "write_stdin 需要提供要写入的文本",
        )
    payload = text
    if append_newline and not payload.endswith("\n"):
        payload += "\n"
    error = await mp.write_stdin(payload)
    if error:
        return make_error("写入 stdin", error)
    return make_success(
        f"已向进程 #{mp.num} 的 stdin 写入 {len(payload)} 字符"
        "（未回显，可用 read_stdout 查看响应）"
    )


async def _do_read_stdout(mp, read_offset: Optional[int], max_bytes: Optional[int]):
    offset, oerr = parse_int(read_offset, "read_offset")
    if oerr:
        return make_error("读取 stdout", oerr)
    size, serr = parse_int(max_bytes, "max_bytes")
    if serr:
        return make_error("读取 stdout", serr)
    size = min(max(size if size is not None else 8192, 1), 65536)
    offset = max(offset or 0, 0)

    start, data, next_offset = mp.get_buffer(offset, size)
    text = sanitize_full(data)
    if not data:
        return make_success(
            f"偏移 {offset} 起无新输出"
            f"（缓冲范围 {mp.dropped_bytes}~{mp.total_out}，"
            f"进程状态 {mp.status}）"
        )
    dropped_note = ""
    if start > offset:
        dropped_note = f"（请求偏移已滑出环形缓冲，从 {start} 起读）"
    hit_cap = len(data) >= size
    return make_success(
        f"[{start},{next_offset}) 共 {len(data)} 字节净化输出{dropped_note}：\n"
        f"{text}"
        + (
            f"\n（未读完，续读传 read_offset={next_offset}）"
            if hit_cap or next_offset < mp.total_out
            else ""
        )
    )
