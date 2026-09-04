# -*- coding: utf-8 -*-
"""测试辅助函数（无状态纯工具，conftest 与各测试共用）。"""

import asyncio
import sys


def chunk_text(chunk) -> str:
    """从 ToolChunk 提取纯文本。"""
    parts = []
    for block in chunk.content or []:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(getattr(block, "text", str(block)))
    return "\n".join(parts)


def is_error(chunk) -> bool:
    import agentscope.message as m

    return chunk.state == m.ToolResultState.ERROR


def run(coro):
    """驱动一个测试协程：无论断言成败，退出前终止全部残留托管进程，
    保证事件循环干净收尾（Windows 上残留活体会干扰 loop 关闭）。"""

    async def _wrapper():
        try:
            return await coro
        finally:
            try:
                from manager import get_manager

                await get_manager().shutdown_all()
            except Exception:
                pass

    return asyncio.run(_wrapper())


def py_cmd(code: str) -> str:
    """把 python 单行代码包装成跨平台 shell 命令字符串。"""
    quoted = code.replace('"', '\\"')
    return f'"{sys.executable}" -u -c "{quoted}"'
