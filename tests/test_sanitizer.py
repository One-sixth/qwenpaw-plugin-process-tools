# -*- coding: utf-8 -*-
"""sanitizer 单测。"""

from sanitizer import Sanitizer, apply_backspace, collapse_cr, sanitize_full, strip_ansi


def test_strip_ansi_csi():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("\x1b[?25lhide\x1b[?25h") == "hide"


def test_strip_ansi_osc():
    assert strip_ansi("\x1b]0;window title\x07body") == "body"
    assert strip_ansi("\x1b]8;;http://x\x1b\\link") == "link"


def test_strip_ctrl_keeps_newline_tab():
    assert strip_ansi("a\x00b\tc\nd") == "ab\tc\nd"


def test_collapse_cr_progress():
    assert collapse_cr("1%\r2%\r100%\n") == "100%\n"
    assert collapse_cr("keep\rrewrite\n") == "rewrite\n"
    # 普通行不动
    assert collapse_cr("a\nb\n") == "a\nb\n"


def test_crlf_not_eaten_as_overwrite():
    r"""回归：Windows 管道 `\r\n` 是行尾，不能当覆写吃掉整行内容。"""
    assert collapse_cr("hello world\r\n") == "hello world\n"
    assert collapse_cr("a\r\nb\r\n") == "a\nb\n"
    # 覆写与 CRLF 混合：先归一 CRLF，再折叠真正的 \r 覆写
    assert collapse_cr("50%\r75%\r\n") == "75%\n"


def test_incremental_crlf_across_chunks():
    """`\r\n` 恰好被 chunk 边界切开也不能出错。"""
    s = Sanitizer()
    out = s.feed(b"hello world\r")
    assert out == ""  # 未见 \n，先攒着
    out += s.feed(b"\nnext\n")
    assert out == "hello world\nnext\n"


def test_backspace():
    assert apply_backspace("abc\b\bXY") == "aXY"
    assert apply_backspace("\b\bx") == "x"


def test_backspace_survives_full_pipeline():
    """S4 钉死：\b 必须活着走完 feed 管线（曾被 strip_ansi 提前吃掉，
    apply_backspace 沦为死代码——孤立函数单测掩盖整链失效的教训）。"""
    s = Sanitizer()
    out = s.feed(b"abc\b\bXY\n")
    assert out == "aXY\n"


def test_incremental_utf8_across_chunks():
    """多字节字符跨 chunk 截断不能出乱码。"""
    raw = "中文测试".encode("utf-8")
    s = Sanitizer()
    out = ""
    # 逐字节喂入
    for i in range(len(raw)):
        out += s.feed(raw[i : i + 1])
    out += s.flush()
    assert "中文测试" in out


def test_line_splitting():
    s = Sanitizer()
    partial = s.feed(b"hello wo")
    assert partial == ""  # 未见 \n 不落盘
    rest = s.feed(b"rld\nnext")
    assert rest == "hello world\n"
    tail = s.flush()
    assert tail == "next\n"  # flush 补全尾行


def test_sanitize_full_one_shot():
    data = "\x1b[32mgreen\x1b[0m\rprogress done\n".encode()
    text = sanitize_full(data)
    assert text.strip() == "progress done"
