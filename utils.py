# -*- coding: utf-8 -*-
"""公共工具函数。

提供会话上下文定位、workspace 解析、ToolChunk 结果封装、
整数参数兼容解析、日志尾部读取等工具共享的底层能力。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

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


# ── 会话生死判定（死会话数据清理用）─────────────────────

_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def kernel_sanitize_filename(name: str) -> str:
    """复用内核会话文件名净化（非法字符→"--"），导入失败本地同规则回落。"""
    try:
        from qwenpaw.app.chats.session import sanitize_filename as _k

        return _k(name)
    except Exception:  # noqa: BLE001
        return _FILENAME_UNSAFE_RE.sub("--", name)


def session_file_candidates(
    sessions_dir: str,
    session_id: str,
    user_id: str = "",
    channel: str = "",
) -> List[str]:
    """一条 chat 记录对应的真实会话文件候选路径。

    命名规则对齐内核 session_filename()（非法字符→"--"、uid==sid 省段），
    含现行 channel 子目录布局与旧版 sessions/ 根目录兜底。
    """
    safe_sid = kernel_sanitize_filename(str(session_id or ""))
    safe_uid = kernel_sanitize_filename(str(user_id or "")) if user_id else ""
    if safe_uid and safe_uid == safe_sid:
        safe_uid = ""
    fname = f"{safe_uid}_{safe_sid}.json" if safe_uid else f"{safe_sid}.json"
    rels: List[str] = []
    safe_ch = kernel_sanitize_filename(str(channel or ""))
    if safe_ch and safe_ch not in (".", ".."):
        rels.append(os.path.join(safe_ch, fname))
    rels.append(fname)
    return [os.path.join(sessions_dir, r) for r in rels]


def workspaces_root() -> Optional[str]:
    """全部 agent 工作区的公共父目录 …/.qwenpaw/workspaces。

    从当前 workspace 向上推导；结构不符回落 ~/.qwenpaw/workspaces；
    仍不存在返回 None（调用方据此跳过清理）。
    """
    ws = get_workspace_dir()
    if ws:
        parent = os.path.dirname(os.path.normpath(str(ws)))
        if os.path.basename(parent).lower() == "workspaces" and os.path.isdir(parent):
            return parent
    cand = os.path.join(os.path.expanduser("~"), ".qwenpaw", "workspaces")
    return cand if os.path.isdir(cand) else None


def agent_aliases(ws_dir: str) -> List[str]:
    """workspace 目录下 agent_id 的可能取值（agent.json["id"] 优先，目录名兜底）。

    运行期 contextvar 实际值必居其一；两个都作候选键是防误判措施——
    live 集合多留无害，多删有害。
    """
    aliases: List[str] = []
    try:
        with open(os.path.join(ws_dir, "agent.json"), encoding="utf-8") as f:
            aid = json.load(f).get("id")
        if isinstance(aid, str) and aid:
            aliases.append(aid)
    except (OSError, ValueError, AttributeError):
        pass
    base = os.path.basename(os.path.normpath(ws_dir))
    if base and base not in aliases:
        aliases.append(base)
    return aliases


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


def parse_env(value) -> Tuple[Optional[dict], Optional[str]]:
    """把 env 参数解析成 {str: str} 增量环境（空→None），非法给错误消息。

    兼容 dict 与 JSON 对象字符串（通道字符串化防御：LLM 常把 dict 整体
    传成字符串）；键值统一转 str——subprocess 要求如此，LLM 传
    {"PORT": 8080} 这类数值也很常见。
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, None
        import json

        # 通道/LLM 字符串化防御：参数可能被 JSON 编码一到两层（实测
        # 双层形态 '"{\"A\": \"1\"}"'，外围引号+内部转义）。逐层 loads
        # （最多 2 次），得到 dict 即用；否则报解析失败。
        obj = None
        cur = s
        for _ in range(2):
            if not cur or cur[0] not in '{["':
                break
            try:
                decoded = json.loads(cur)
            except ValueError:
                # 病态变体兜底：'"{"A": "1"}"'（外围引号+内部无转义）——
                # 手工剥一次，仅当剥完能 loads 出 dict 才接受。
                if len(cur) >= 2 and cur[0] == '"' and cur[-1] == '"':
                    inner = cur[1:-1].strip()
                    if inner.startswith("{"):
                        try:
                            cand = json.loads(inner)
                            if isinstance(cand, dict):
                                obj = cand
                                break
                        except ValueError:
                            pass
                break
            if isinstance(decoded, dict):
                obj = decoded
                break
            if isinstance(decoded, str):
                cur = decoded.strip()
            else:
                break
        if obj is not None:
            value = obj
        else:
            return None, f"env 参数应为 dict（或 JSON 对象字符串），「{truncate_line(s, 60)}」解析不了"
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                return None, "env 含空变量名"
            cleaned[key] = str(v)
        return cleaned or None, None
    return None, f"env 参数应为 dict，收到「{type(value).__name__}」"


def resolve_encoding(value) -> Tuple[str, bool, Optional[str]]:
    """encoding 参数 → (规范化 codec 名, 是否 auto, 错误消息)。

    auto/空 = 本机原生编码：Windows 取控制台输出码页（GetConsoleOutputCP，
    中文系统 936→gbk——不能用 locale.getpreferredencoding，宿主常设
    PYTHONUTF8=1 会让它谎报 utf-8，而 cmd 原生输出仍是 GBK）；POSIX 取
    locale 首选编码。显式值经 codecs.lookup 校验并取规范名。
    """
    s = str(value or "").strip().lower()
    if s in ("", "auto"):
        return _auto_codec(), True, None
    import codecs

    try:
        return codecs.lookup(s).name, False, None
    except LookupError:
        return "", False, f"未知 encoding「{value}」，可用值如 auto / utf-8 / gbk / cp936 / latin-1"


def _auto_codec() -> str:
    """auto 的实际解析：Windows 控制台码页优先，POSIX locale 兜底。"""
    import codecs

    candidates = []
    if os.name == "nt":
        try:
            import ctypes

            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                candidates.append(f"cp{cp}")
        except Exception:  # noqa: BLE001
            pass
    import locale

    candidates.append(locale.getpreferredencoding(False))
    candidates.append("utf-8")
    for c in candidates:
        if not c:
            continue
        try:
            return codecs.lookup(c).name
        except LookupError:
            continue
    return "utf-8"


def parse_argv(value) -> Tuple[Optional[list], Optional[str]]:
    """把 no_shell 模式的 command 解析成 argv 字符串列表。

    接受 list 或 JSON 数组字符串（通道字符串化防御）；元素统一 str。
    """
    v = value
    if isinstance(v, str):
        import json

        try:
            v = json.loads(v)
        except ValueError:
            return None, (
                "no_shell=True 时 command 需为 argv 列表"
                "（如 [\"python\", \"-c\", \"print(1)\"]）或其 JSON 数组字符串"
            )
    if isinstance(v, (list, tuple)) and v:
        return [str(x) for x in v], None
    return None, "no_shell=True 时 command 需为非空 argv 列表（或其 JSON 数组字符串）"


def parse_process_ids(value) -> Tuple[Optional[list], Optional[str]]:
    """把 process_id / process_ids 参数解析成去重保序的编号列表。

    接受单个编号（1 / "1" / "#1"）、编号列表（或其 JSON 数组字符串，
    通道字符串化防御）；元素逐个走 parse_int 兼容 "#N" 写法。
    """
    v = value
    if isinstance(v, str):
        s = v.strip()
        import json

        # 通道/LLM 字符串化防御：参数可能被 JSON 编码一到两层。实测通道
        # 会把 list 参数变成 '"[\"21\", \"20\"]"'（外围引号+内部转义），
        # 它本身是合法 JSON 字符串字面量——逐层 loads（最多 2 次）：
        # 得到 list 直接用；得到 str 继续解；失败且以 [ 开头报专用
        # 解析错，其余回落单编号 parse_int（'"3"' → "3" → 3）。
        decoded = None
        for _ in range(2):
            if not s or s[0] not in '["':
                break
            try:
                decoded = json.loads(s)
            except ValueError:
                # 病态变体兜底：'"["1", "2"]"'（外围引号+内部无转义，非
                # 合法 JSON）——手工剥一次，仅当剥完能 loads 出 list 才
                # 接受；'""5""' 剥完不是数组仍拒绝（维持只剥一次裁决）。
                if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
                    inner = s[1:-1].strip()
                    if inner.startswith("["):
                        try:
                            cand = json.loads(inner)
                            if isinstance(cand, list):
                                decoded = cand
                                break
                        except ValueError:
                            pass
                if s[0] == "[":
                    return None, f"process_id 参数无法解析：「{truncate_line(s, 60)}」"
                break
            if isinstance(decoded, str):
                s = decoded.strip()
            else:
                break
        if isinstance(decoded, (list, tuple, str)):
            v = decoded
        else:
            v = s
    items = list(v) if isinstance(v, (list, tuple)) else [v]
    if not items:
        return None, "process_id 列表为空"
    nums = []
    for item in items:
        n, err = parse_int(item, "process_id")
        if err or n is None:
            return None, err or "process_id 无效"
        if n not in nums:
            nums.append(n)
    return nums, None


# ── shell 选择 ──────────────────────────────────────────

SHELL_CHOICES = ("auto", "pwsh", "bash")

_PWSH_FLAGS = ["-NoProfile", "-NonInteractive", "-Command"]


def resolve_shell_argv(spec: str) -> Tuple[Optional[list], Optional[str], Optional[str]]:
    """shell 参数 → (argv 前缀, 实际使用的 shell 名, 错误消息)。

    auto：Windows pwsh > powershell > cmd.exe；Linux bash > /bin/sh；
    macOS zsh > bash > /bin/sh。显式选 pwsh/bash 但找不到 → 报错
    引导改用 auto，不静默换壳。旧值 "default" 等同 auto（升级兼容别名）。
    """
    import shutil
    import sys

    spec = str(spec or "auto").strip().lower()
    if spec == "default":
        # 0.4.4 起 default 改名 auto；旧值静默映射，防升级期调用报错
        spec = "auto"
    if spec not in SHELL_CHOICES:
        return None, None, f"shell 参数应为 {' / '.join(SHELL_CHOICES)}，收到「{spec}」"
    if os.name == "posix":
        if spec == "pwsh":
            exe = shutil.which("pwsh") or shutil.which("powershell")
            if not exe:
                return None, None, '未找到 pwsh/powershell，可改 shell="auto"(bash)'
            return [exe, *_PWSH_FLAGS], "pwsh", None
        bash = shutil.which("bash")
        if spec == "bash":
            if not bash:
                return None, None, '未找到 bash，可改 shell="auto"'
            return [bash, "-c"], "bash", None
        # auto：macOS zsh > bash > /bin/sh；Linux bash > /bin/sh
        if sys.platform == "darwin":
            zsh = shutil.which("zsh")
            if zsh:
                return [zsh, "-c"], "zsh", None
        return [bash or "/bin/sh", "-c"], ("bash" if bash else "sh"), None
    # Windows
    if spec == "bash":
        bash = shutil.which("bash")
        if not bash:
            return None, None, '未找到 bash（Git Bash?），可改 shell="auto"(pwsh)'
        return [bash, "-c"], "bash", None
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if spec == "pwsh":
        if not pwsh:
            return None, None, '未找到 pwsh/powershell，可改 shell="auto" 或 "bash"'
        return [pwsh, *_PWSH_FLAGS], "pwsh", None
    if pwsh:
        return [pwsh, *_PWSH_FLAGS], "pwsh", None
    cmd = shutil.which("cmd.exe") or "cmd.exe"
    return [cmd, "/c"], "cmd", None


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
