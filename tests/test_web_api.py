# -*- coding: utf-8 -*-
"""web_api 会话活动状态端点测试（0.3.2 run 身份键版）。

迭代史：0.2.0 文件指纹（太晚+误刷）→ 0.3.0 DOM 静止（等待吐字期
误判）→ 0.3.2 running×非loading×run键 三重限——本文件测后端转发面。
"""

import datetime

import pytest

import web_api as wa
from helpers import run


class _Tracker:
    def __init__(self, status="running", global_boom=False):
        self.status = status
        self.global_boom = global_boom
        self.seen_keys = []

    async def get_status(self, run_key):
        self.seen_keys.append(run_key)
        return self.status

    async def get_global_status(self):
        if self.global_boom:
            raise RuntimeError("global boom")
        return {
            "status": self.status,
            "last_run_at": datetime.datetime(2026, 9, 5, 4, 0, 0),
            "last_finish_at": None,
        }


class _Workspace:
    def __init__(self, tracker):
        self.task_tracker = tracker


def _patch(monkeypatch, workspace=None, boom=False):
    ac = pytest.importorskip("qwenpaw.app.agent_context")

    async def fake_get(request, agent_id=None):
        if boom or workspace is None:
            raise RuntimeError("no workspace")
        return workspace

    monkeypatch.setattr(ac, "get_agent_for_request", fake_get)


def test_probe_running_with_run_key(monkeypatch):
    tracker = _Tracker(status="running")
    _patch(monkeypatch, _Workspace(tracker))
    out = run(wa.chat_probe(object(), "chat-1"))
    assert out["status"] == "running"
    assert out["run_at"] == datetime.datetime(
        2026, 9, 5, 4, 0, 0,
    ).timestamp()
    assert tracker.seen_keys == ["chat-1"]  # run_key 就是 chat UUID


def test_probe_idle_passthrough(monkeypatch):
    _patch(monkeypatch, _Workspace(_Tracker(status="idle")))
    assert run(wa.chat_probe(object(), "c"))["status"] == "idle"


def test_probe_empty_chat_id(monkeypatch):
    _patch(monkeypatch, _Workspace(_Tracker()))
    out = run(wa.chat_probe(object(), ""))
    assert out == {"status": "unknown", "run_at": None}


def test_probe_no_workspace(monkeypatch):
    _patch(monkeypatch, boom=True)
    assert run(wa.chat_probe(object(), "c")) == {
        "status": "unknown", "run_at": None,
    }


def test_probe_global_status_error_keeps_main(monkeypatch):
    # run_at 是附属信号：get_global_status 炸不影响 status 主判定
    _patch(monkeypatch, _Workspace(_Tracker(global_boom=True)))
    out = run(wa.chat_probe(object(), "c"))
    assert out["status"] == "running"
    assert out["run_at"] is None


def test_endpoint_shape(monkeypatch):
    pytest.importorskip("fastapi")

    async def fake_probe(request, chat_id):
        return {"status": "running", "run_at": 123.5}

    monkeypatch.setattr(wa, "chat_probe", fake_probe)
    router = wa.create_router()
    endpoint = next(
        r.endpoint for r in router.routes if r.path == "/chat-status"
    )
    out = run(endpoint(request=object(), chat_id="abc"))
    assert out == {"chat_id": "abc", "status": "running", "run_at": 123.5}
