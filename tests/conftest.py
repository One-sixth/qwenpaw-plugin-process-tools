# -*- coding: utf-8 -*-
"""测试夹具。

通过 sys.path 添加项目根支持顶层导入（utils/manager/notifier/tools），
与插件内相对导入双模式对应。异步工具统一在测试内 asyncio.run 驱动，
不依赖 pytest-asyncio。
"""

import os
import shutil
import sys
import tempfile
from typing import Generator

import pytest

# 将项目根目录添加到 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_dir: str) -> None:
    """每个测试独立的插件数据目录 + 重置单例。

    注意值绑定陷阱：各模块通过 ``from .utils import get_data_dir`` 绑定了
    引用，需要逐个模块覆盖。
    """
    import manager as manager_mod
    import notifier as notifier_mod
    import utils as utils_mod

    data_root = os.path.join(tmp_dir, "_process_tools")
    utils_mod.get_data_dir = lambda: data_root
    manager_mod.get_data_dir = lambda: data_root

    manager_mod.reset_manager()
    notifier_mod.reset_notifier()
    yield
    manager_mod.reset_manager()
    notifier_mod.reset_notifier()


@pytest.fixture(autouse=True)
def _fixed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境固定一个会话上下文（不依赖 QwenPaw 内核 contextvar）。"""
    import manager as manager_mod

    key = ("test-agent", "test-user", "test-session")
    monkeypatch.setattr(manager_mod, "session_key", lambda *a, **k: key)


@pytest.fixture
def tmp_dir() -> Generator[str, None, None]:
    tmp = tempfile.mkdtemp(prefix="process_tools_test_")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
