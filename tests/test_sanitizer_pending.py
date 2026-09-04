# -*- coding: utf-8 -*-
"""Sanitizer 增量输出：CRLF 跨 chunk 切分回归。"""

from sanitizer import Sanitizer


def test_pending_is_purified_view():
    s = Sanitizer()
    s.feed(b"partial\x1b[31m text")
    assert s.get_pending() == "partial text"


def test_dangling_cr_pending_keeps_latest_segment():
    s = Sanitizer()
    s.feed(b"50%\r75%")
    assert s.get_pending() == "75%"
    assert s.flush() == "75%\n"
