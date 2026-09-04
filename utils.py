# -*- coding: utf-8 -*-
"""公共工具函数。

提供会话上下文定位、workspace 解析、ToolChunk 结果封装、
整数参数兼容解析、日志尾部读取等工具共享的底层能力。
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

LINE_CONTENT_TRUNCATE = 160
DEFAULT_MAX_CHARS = 2048

# ── 会话上下文定位 ────────────────────────────────────────


def get_current_context() -> Tuple[str, str, str]:
    """获取当前工具调用的 (agent_id, user_id, session_id) 上下文。

    读取 QwenPaw 内核 contextvar（由 ContextVarsSetupHook 每次请求注入）。
    工具必须在请求处理协程内调用本函数；后台任务通过 asyncio.Task 创建时的
    context 快照继承，同样可读。取不到时回落为 "default"。
    """
    try:
        from qwenpaw.app.agent_context import (
            get_current_agent_id,
            get_current_session_id,
            get_current_user_id,
        )

        agent_id = get_current_agent_id() or "default"
        session_id = get_current_session_id() or "default"
        user_id = get_current_user_id() or "default"
        return str(agent_id), str(user_id), str(session_id)
    except Exception:
        return "default", "default", "default"


def session_key(
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, str, str]:
    """构建进程注册表的会话隔离键。

    不传参时自动读当前 contextvar。键内任一维度缺失回 "default"，
    保证不同会话的 #N 编号与进程列表互不可见。
    """
    if agent_id is None or user_id is None or session_id is None:
        d_agent, d_user, d_session = get_current_context()
        agent_id = agent_id or d_agent
        user_id = user_id or d_user
        session_id = session_id or d_session
    return (str(agent_id), str(user_id), str(session_id))


def current_channel() -> str:
    """读当前工具调用上下文的频道名（contextvar），取不到回落 "console"。

    用于唤醒投递的频道守卫：/console/chat/task 只服务 console 会话。
    """
    try:
        from qwenpaw.app.agent_context import get_current_channel

        return str(get_current_channel() or "console")
    except Exception:  # noqa: BLE001
        return "console"


def sanitize_session_token(text: str) -> str:
    """把会话标识压成可作文件名的短标记（非字母数字→_，截断+crc32 防冲突）。

    用 zlib.crc32 而非内置 hash()：后者随 PYTHONHASHSEED 每进程随机化，
    重启后同一会话会算出不同文件名，历史日志就对不上了。
    """
    import zlib

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:40]
    digest = format(zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return f"{safe}_{digest}" if safe else digest


# ── workspace 目录 ────────────────────────────────────────


def get_workspace_dir() -> Optional[str]:
    """获取 QwenPaw 默认工作区目录。

    优先 kernel contextvar，其次 tool_config，兜底 agent profiles 配置。
    """
    try:
        from qwenpaw.config.context import current_workspace_dir

        ws = current_workspace_dir.get()
        if ws:
            return str(Path(ws).expanduser().resolve())
    except Exception:
        pass

    try:
        from qwenpaw.plugins.api import get_tool_config

        config = get_tool_config("process_tools_exec")
        if config and isinstance(config, dict):
            ws = config.get("workspace_dir")
            if ws:
                return str(ws)
    except Exception:
        pass

    try:
        from qwenpaw.config.utils import load_config

        cfg = load_config()
        profiles = cfg.agents.profiles
        if "default" in profiles:
            return str(Path(profiles["default"].workspace_dir).expanduser().resolve())
        if profiles:
            first = next(iter(profiles.values()))
            return str(Path(first.workspace_dir).expanduser().resolve())
    except Exception:
        pass
    return None


def resolve_cwd(path: str) -> str:
    """解析工作目录参数。空串表示不指定（由调用方决定默认值）。"""
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    return os.path.normpath(os.path.abspath(expanded))


def get_data_dir() -> str:
    """插件数据根目录 {ws}/process_tools_data。测试时被 mock。"""
    ws = get_workspace_dir() or os.path.join(os.path.expanduser("~"), ".qwenpaw")
    return os.path.join(ws, "process_tools_data")


# ── 截断 ──────────────────────────────────────────────────


def truncate_line(line: str, max_len: int = LINE_CONTENT_TRUNCATE) -> str:
    """截断超长行内容。"""
    if len(line) > max_len:
        return line[:max_len] + "<<truncated>>"
    return line


def make_result(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """构建结果文本，超出 max_chars 时截断并提示。"""
    if len(text) > max_chars:
        return text[:max_chars] + "\n<<truncated>> 内容已超出 max_chars 限制。"
    return text


# ── 错误与结果封装 ────────────────────────────────────────


def format_error(action: str, reason: str = "", suggestion: str = "") -> str:
    """三段式错误：操作失败 + 原因 + 建议。"""
    parts = [f"{action}失败" + (f"：{reason}" if reason else "")]
    if suggestion:
        parts.append(suggestion)
    return "\n".join(p for p in parts if p)


_ToolChunk = None
_ToolResultState = None
_TextBlock = None


def _import_toolchunk():
    """延迟导入 agentscope 类型，避免模块级依赖。"""
    global _ToolChunk, _ToolResultState, _TextBlock
    if _ToolChunk is None:
        from agentscope.message import ToolResultState, TextBlock
        from agentscope.tool import ToolChunk

        _ToolChunk = ToolChunk
        _ToolResultState = ToolResultState
        _TextBlock = TextBlock


def make_success(text: str) -> "ToolChunk":
    """构建成功结果 ToolChunk。

    Args:
        text: 显示文本内容
    """
    _import_toolchunk()
    return _ToolChunk(
        is_last=True,
        state=_ToolResultState.SUCCESS,
        content=[_TextBlock(type="text", text=text)],
    )


def make_error(text: str, reason: str = "", suggestion: str = "") -> "ToolChunk":
    """构建错误结果 ToolChunk。支持两种调用方式：
    - make_error("错误文本")
    - make_error("操作名", "原因", "建议")  # 自动调用 format_error 拼接

    Args:
        text: 错误文本，或 format_error 的 action 参数
        reason: 当提供时，与 text 一起传给 format_error
        suggestion: 当提供时，传给 format_error
    """
    _import_toolchunk()
    if reason:
        text = format_error(text, reason, suggestion)
    return _ToolChunk(
        is_last=True,
        state=_ToolResultState.ERROR,
        content=[_TextBlock(type="text", text=text)],
    )


# ── 参数兼容解析 ──────────────────────────────────────────


def parse_int(value, param_name: str) -> Tuple[Optional[int], Optional[str]]:
    """将 int 或十进制数字字符串转换为 int。

    兼容 LLM 将数字参数传成字符串（如 "123"）及 "#12" 带井号的情况。
    - int → 原样返回
    - str → strip、可去前导 "#"，匹配十进制（可选负号），合法转 int
    - None → 原样返回（用于 Optional 参数未指定）
    - 其他 / 非法字符串 → 返回错误消息

    Args:
        value: 原始值，int / str / None
        param_name: 参数名，用于错误消息

    Returns:
        (int_or_None, None) 成功；(None, 错误消息) 非法输入
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{param_name} 参数应当输入整数，收到「{value}」"
    if isinstance(value, int):
        return value, None
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if re.fullmatch(r"-?\d+", s):
            return int(s), None
    return None, f"{param_name} 参数应当输入整数，收到「{value}」"


def parse_process_id(value) -> Tuple[Optional[int], Optional[str]]:
    """解析 process_id 参数（支持 1 / "1" / "#1"）。"""
    return parse_int(value, "process_id")


# ── 日志尾部读取 ──────────────────────────────────────────


def read_log_tail(path: str, tail_lines: int = 20, max_bytes: int = 65536) -> Tuple[list, int]:
    """反向读取日志文件尾部若干行。

    从文件末尾向前按块定位（默认最多读 64KB），按行拆分并截断超长行。

    Args:
        path: 日志文件路径
        tail_lines: 需要的行数
        max_bytes: 反向读取的字节上限

    Returns:
        (lines, file_size)；文件不存在时 lines 为空列表
    """
    if not os.path.isfile(path):
        return [], 0
    size = os.path.getsize(path)
    read_len = min(size, max_bytes)
    drop_first = False
    with open(path, "rb") as f:
        if read_len < size:
            # 多探一字节：窗口起点的前一个字符若是 \n，首行是完整的，不能丢
            f.seek(size - read_len - 1)
            probe = f.read(1)
            drop_first = probe != b"\n"
            raw = f.read(read_len)
        else:
            raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if drop_first and "\n" in text:
        text = text.split("\n", 1)[1]
    elif drop_first:
        text = ""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    lines = [truncate_line(ln) for ln in lines[-tail_lines:] if tail_lines > 0]
    return lines, size
