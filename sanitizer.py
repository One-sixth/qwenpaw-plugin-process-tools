# -*- coding: utf-8 -*-
"""输出净化器（Sanitizer）。

把子进程管道输出的原始字节流净化成干净的日志文本（三路数据流之
「净化日志」）：

- 剥除 ANSI 转义序列（CSI / OSC / 两字符转义）
- CRLF 归一：`\r\n` 是行尾而非覆写，先归一成 `\n`（跨平台关键一步）
- `\r` 重绘折叠：真正的回车覆写 `1%\\r2%\\r...\\r100%` 只保留最后一段
- `\\b` 退格删除模拟（行级处理）
- 增量 UTF-8 解码：多字节字符跨 chunk 截断不产生乱码

与 PTY 场景不同，纯管道输出通常 ANSI 较少，但很多带颜色的 CLI
在管道下仍会发转义序列，净化逻辑仍然必要。
"""

import re
from typing import Optional

# CSI: ESC [ ...最终字节；中间含参数节与中间字节
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# OSC: ESC ] ... BEL 或 ST
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# 两字符转义：ESC + 单字符（如 ESC = / ESC >）
_ESC2_RE = re.compile(r"\x1b[@-Z\\-_]")
# 其它 C1 控制符保留 \n \t \b——\x08 要留给 apply_backspace 做退格模拟，
# 在这里剥掉会让退格语义整体失效（审查 S4 教训）
_CTRL_RE = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """剥除 ANSI/OSC/两字符转义与多余控制符（保留 \\n \\t）。"""
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC2_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text


def collapse_cr(text: str) -> str:
    r"""折叠 `\r` 重绘。

    第一步先把 `\r\n`（CRLF 行尾，Windows 管道的正常换行）归一为
    `\n`——这一步不做，CRLF 会被误判成"光标回列 0 等待覆写"而吃掉整行。
    第二步对剩余的 `\r` 按覆写语义处理：每个物理行只保留最后一个
    `\r` 段。例："progress 1%\rprogress 2%\n" → "progress 2%\n"
    """
    text = text.replace("\r\n", "\n")
    out_lines = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        out_lines.append(line)
    return "\n".join(out_lines)


def apply_backspace(text: str) -> str:
    """处理 `\\b` 退格：逐字符模拟，退格删除前一个字符。"""
    if "\b" not in text:
        return text
    buf = []
    for ch in text:
        if ch == "\b":
            if buf:
                buf.pop()
        else:
            buf.append(ch)
    return "".join(buf)


class Sanitizer:
    """增量输出净化器。

    feed() 接收原始 bytes，返回本次可安全落盘的净化文本；
    行尾未收到 `\n` 的残段留在内部缓冲，等待后续字节拼完整行
    （flush()/get_pending() 可随时取走残段）。残段以悬空 `\r`
    结尾时视为"等待被覆写"，新到的字节会接在同一行里由
    collapse_cr 处理。

    encoding 决定原始字节的解码方式（默认 utf-8，托管进程可按
    exec 的 encoding 参数指定，如中文 Windows 原生输出的 gbk）。
    """

    def __init__(self, encoding: str = "utf-8") -> None:
        import codecs

        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self._pending = ""  # 未换行的残段（未净化）

    def feed(self, data: bytes) -> str:
        """喂入原始字节，返回净化后的完整行文本（含行尾 \\n）。

        以 `\n` 为界，未完整的尾段暂存内部。
        """
        text = self._decoder.decode(data)
        if not text:
            return ""
        buf = self._pending + text
        cut = buf.rfind("\n")
        if cut < 0:
            self._pending = buf
            return ""
        complete, rest = buf[: cut + 1], buf[cut + 1 :]
        self._pending = rest
        complete = strip_ansi(complete)
        complete = collapse_cr(complete)
        return apply_backspace(complete)

    def flush(self) -> str:
        """取走并清空残段（进程退出时调用，保证末尾无 \\n 的输出不落盘丢失）。"""
        rest = self._pending
        self._pending = ""
        if not rest:
            return ""
        complete = strip_ansi(rest + "\n")
        complete = collapse_cr(complete)
        return apply_backspace(complete)

    def get_pending(self) -> str:
        """查看当前残段的净化形态（不清空），用于实时尾部展示。"""
        text = strip_ansi(self._pending)
        return apply_backspace(collapse_cr_pending(text))


def collapse_cr_pending(text: str) -> str:
    r"""残段专用的 \r 处理：悬空 \r 表示正被覆写，只保留最后一段。"""
    if "\r" in text.rstrip("\n"):
        # 悬空 \r 之后的内容覆写之前
        seg = text.rstrip("\n")
        return seg.rsplit("\r", 1)[-1] + ("\n" if text.endswith("\n") else "")
    return text


def sanitize_full(data: bytes, final: bool = True, encoding: str = "utf-8") -> str:
    """一次性净化整段字节（用于读取环形缓冲/日志等非增量场景）。

    Args:
        data: 原始字节
        final: 是否按已结束处理（折叠最后一段）
        encoding: 解码 codec 名
    """
    s = Sanitizer(encoding)
    text = s.feed(data)
    if final:
        text += s.flush()
    return text
