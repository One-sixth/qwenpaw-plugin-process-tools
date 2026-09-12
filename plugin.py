# -*- coding: utf-8 -*-
"""Process Tools 插件入口。

注册 6 个托管多进程工具到 QwenPaw Agent 工具箱。

0.2.0 起附前端伴生组件：HTTP 轻量端点（session-fp 指纹）+ 浏览器轮询
脚本（frontend/index.js），解决唤醒/后台任务落盘后 WebUI 不刷新的问题。
"""

import asyncio
import logging
import sys

from qwenpaw.plugins.api import PluginApi

from .manager import get_manager
from .web_api import create_router
from .tools.exec import process_tools_exec
from .tools.check import process_tools_check
from .tools.list import process_tools_list
from .tools.wait import process_tools_wait
from .tools.communicate import process_tools_communicate
from .tools.notice import process_tools_notice

logger = logging.getLogger(__name__)


class ProcessToolsPlugin:
    """Process Tools 插件主类。"""

    def register(self, api: PluginApi) -> None:
        logger.info("注册 Process Tools 插件（6 个工具）...")

        # Windows 上 asyncio 子进程依赖 ProactorEventLoop；若宿主跑在
        # Selector 循环上，create_subprocess_shell 会直接 NotImplementedError，
        # 这里只做提示，真正失败会体现在工具返回里。
        if sys.platform == "win32":
            try:
                loop_policy = asyncio.get_event_loop_policy()
                name = type(loop_policy).__name__
                if "Proactor" not in name and "Windows" in name:
                    logger.warning(
                        "当前事件循环策略为 %s，asyncio 子进程需要 "
                        "WindowsProactorEventLoopPolicy，托管进程可能启动失败",
                        name,
                    )
            except Exception:  # noqa: BLE001
                pass

        # ── 执行 ──
        api.register_tool(
            tool_name="process_tools_exec",
            tool_func=process_tools_exec,
            description="启动托管 shell 进程（前台等待/后台运行），输出落净化日志。",
            icon="🚀",
            tool_type="shell",
            target_param="command",
            enabled=True,
        )

        # ── 查询 ──
        api.register_tool(
            tool_name="process_tools_list",
            tool_func=process_tools_list,
            description="列出当前会话的全部托管进程（编号/状态/时长/命令）。",
            icon="📋",
            tool_type="shell",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="process_tools_check",
            tool_func=process_tools_check,
            description="查看进程状态、退出码、运行时长与输出日志尾部。",
            icon="🔍",
            tool_type="shell",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="process_tools_wait",
            tool_func=process_tools_wait,
            description="前台主动等待一个或一批后台进程结束（all 语义，超时不杀进程）。",
            icon="⏳",
            tool_type="shell",
            target_param="",
            enabled=True,
        )

        # ── 交互 ──
        api.register_tool(
            tool_name="process_tools_communicate",
            tool_func=process_tools_communicate,
            description="与进程交互：写 stdin / 增量读 stdout / 中断 / 强杀进程树。",
            icon="🔌",
            tool_type="shell",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="process_tools_notice",
            tool_func=process_tools_notice,
            description="为进程注册完成/周期通知（opt-in，结束推送状态并唤醒处理）。",
            icon="🔔",
            tool_type="shell",
            target_param="",
            enabled=True,
        )

        # ── HTTP：会话指纹端点（前端自动刷新伴生，见 web_api.py）
        #    + IM 信使端点（非 console 频道通知唤醒，messenger.py 并入
        #    同一 router——内核约束插件 HTTP prefix 插件级唯一）──
        api.register_http_router(
            create_router(), prefix="/process-tools", tags=["process-tools"],
        )

        # ── 启动钩子：清理过期日志 ──
        api.register_startup_hook(
            "process_tools_cleanup",
            self._startup_cleanup,
            priority=90,
        )
        # ── 退出钩子：终止全部托管进程（绝不留孤儿）──
        api.register_shutdown_hook(
            "process_tools_shutdown",
            self._shutdown_all,
            priority=110,
        )

        logger.info("✓ Process Tools 插件注册完成（6 个工具）")

    def _startup_cleanup(self) -> None:
        """启动清理：30 天龄日志/计数器 + 死会话（索引或文件已没）数据即除。"""
        try:
            get_manager().cleanup_old_logs(days=30)
        except Exception as e:  # noqa: BLE001
            logger.warning("process-tools 启动清理出错: %s", e)
        try:
            get_manager().cleanup_stale_sessions()
        except Exception as e:  # noqa: BLE001
            logger.warning("process-tools 死会话数据清理出错: %s", e)

    async def _shutdown_all(self) -> None:
        """应用退出时终止所有仍在运行的托管进程（防孤儿）。"""
        try:
            await get_manager().shutdown_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("process-tools 退出清理出错: %s", e)


plugin = ProcessToolsPlugin()
