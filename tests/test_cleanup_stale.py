# -*- coding: utf-8 -*-
"""死会话数据清理（cleanup_stale_sessions）测试。

双判据（chats.json 条目 ∧ 真实会话文件）、宽限期、不可读跳过、
token 解析边界、多工作区扫描。夹具全部走注入 workspaces 参数，
不依赖真实 ~/.qwenpaw 布局。
"""

import json
import os
import time

from manager import get_manager
from utils import sanitize_session_token, session_file_candidates

AGENT = "agentA"


def _mk_ws(root, name, *, agent_id=AGENT, chats=None, write_chats=True,
           corrupt=False):
    """造一个 agent 工作区骨架（含 process_tools_data/{counters,logs}）。"""
    ws = os.path.join(root, name)
    os.makedirs(os.path.join(ws, "process_tools_data", "counters"), exist_ok=True)
    os.makedirs(os.path.join(ws, "process_tools_data", "logs"), exist_ok=True)
    if agent_id is not None:
        with open(os.path.join(ws, "agent.json"), "w", encoding="utf-8") as f:
            json.dump({"id": agent_id}, f)
    if write_chats:
        path = os.path.join(ws, "chats.json")
        if corrupt:
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not-json!!")
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"chats": chats or []}, f)
    return ws


def _chat(sid, uid="user1", channel="console"):
    return {"session_id": sid, "user_id": uid, "channel": channel}


def _touch_session(ws, sid, uid="user1", channel="console"):
    """按现行 channel 布局落真实会话文件。"""
    paths = session_file_candidates(os.path.join(ws, "sessions"), sid, uid, channel)
    target = paths[0]  # 第一条 = channel 子目录形态
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write("{}")
    return target


def _mk_counter(ws, sid, uid="user1", *, agent=AGENT, num=3, age=3600.0):
    token = sanitize_session_token(f"{agent}|{uid}|{sid}")
    path = os.path.join(ws, "process_tools_data", "counters", f"{token}.cnt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(num))
    past = time.time() - age
    os.utime(path, (past, past))
    return token, path


def _mk_log(ws, token, n, age=3600.0):
    path = os.path.join(
        ws, "process_tools_data", "logs", f"proc_{token}_{n}.log"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")
    past = time.time() - age
    os.utime(path, (past, past))
    return path


def test_live_session_survives(tmp_dir):
    ws = _mk_ws(tmp_dir, "wsA", chats=[_chat("s1")])
    _touch_session(ws, "s1")
    _, cnt = _mk_counter(ws, "s1")
    log = _mk_log(ws, sanitize_session_token(f"{AGENT}|user1|s1"), 1)
    assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
    assert os.path.exists(cnt) and os.path.exists(log)


def test_ui_deleted_chat_counter_and_log_removed(tmp_dir):
    """死法一：UI 删 chat——索引条目没了、文件还在（内核实况）。"""
    ws = _mk_ws(tmp_dir, "wsA", chats=[])  # 条目已删
    _touch_session(ws, "s1")               # 文件未删（内核不删）
    _, cnt = _mk_counter(ws, "s1")
    token = sanitize_session_token(f"{AGENT}|user1|s1")
    log = _mk_log(ws, token, 2)
    assert get_manager().cleanup_stale_sessions([ws]) == (1, 1)
    assert not os.path.exists(cnt) and not os.path.exists(log)


def test_manually_deleted_file_removed(tmp_dir):
    """死法二：手动删/挪会话文件——条目还在、文件没了。"""
    ws = _mk_ws(tmp_dir, "wsA", chats=[_chat("s1")])
    # 故意不落文件
    _, cnt = _mk_counter(ws, "s1")
    assert get_manager().cleanup_stale_sessions([ws]) == (1, 0)
    assert not os.path.exists(cnt)


def test_grace_period_keeps_fresh_files(tmp_dir):
    ws = _mk_ws(tmp_dir, "wsA", chats=[])
    _, cnt = _mk_counter(ws, "s1", age=10.0)  # 死亡判定成立但太新
    assert get_manager().cleanup_stale_sessions([ws], grace=600) == (0, 0)
    assert os.path.exists(cnt)


def test_missing_or_corrupt_chats_json_skips(tmp_dir):
    for corrupt in (False, True):
        ws = _mk_ws(tmp_dir, f"ws{'c' if corrupt else 'm'}",
                    chats=[], write_chats=corrupt, corrupt=corrupt)
        _, cnt = _mk_counter(ws, "s1")
        assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
        assert os.path.exists(cnt)


def test_unparseable_names_never_touched(tmp_dir):
    ws = _mk_ws(tmp_dir, "wsA", chats=[])
    data = os.path.join(ws, "process_tools_data")
    keep = []
    for d, name in (
        ("counters", "stray.txt"),          # 非 .cnt
        ("counters", "whatever.cnt.tmp"),   # 原子写残留
        ("logs", "notes.log"),              # 非 proc_ 前缀
        ("logs", "proc_deadbeef_abc.log"),  # N 非数字
        ("logs", "proc_12345678.log"),      # 无 _N 段
    ):
        path = os.path.join(data, d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        past = time.time() - 3600
        os.utime(path, (past, past))
        keep.append(path)
    assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
    assert all(os.path.exists(p) for p in keep)


def test_agent_alias_dirname_matches_live(tmp_dir):
    """contextvar agent 用了目录名而非 agent.json id——双别名都要认活。"""
    ws = _mk_ws(tmp_dir, "wsB", agent_id="oneSixth", chats=[_chat("s2")])
    _touch_session(ws, "s2")
    _, cnt = _mk_counter(ws, "s2", agent="wsB")
    assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
    assert os.path.exists(cnt)


def test_wechat_sanitized_filename_and_legacy_layout(tmp_dir):
    """channel 非法字符净化一致性 + 旧版根目录布局兜底。"""
    sid, uid, ch = "wechat:room42", "user42", "wechat"
    ws = _mk_ws(tmp_dir, "wsC", chats=[_chat(sid, uid, ch)])
    # 现行布局：sessions/wechat/user42_wechat--room42.json
    cur = os.path.join(ws, "sessions", "wechat", f"{uid}_{sid.replace(':', '--')}.json")
    os.makedirs(os.path.dirname(cur), exist_ok=True)
    open(cur, "w").close()
    _, cnt = _mk_counter(ws, sid, uid)
    assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
    os.remove(cur)
    # 旧版布局：sessions/<fname> 根目录
    leg = os.path.join(ws, "sessions", os.path.basename(cur))
    open(leg, "w").close()
    assert get_manager().cleanup_stale_sessions([ws]) == (0, 0)
    os.remove(leg)
    assert get_manager().cleanup_stale_sessions([ws]) == (1, 0)
    assert not os.path.exists(cnt)


def test_multi_workspace_only_dead_removed(tmp_dir):
    ws1 = _mk_ws(tmp_dir, "ws1", agent_id="a1", chats=[_chat("x")])
    _touch_session(ws1, "x")
    _, cnt1 = _mk_counter(ws1, "x", agent="a1")
    ws2 = _mk_ws(tmp_dir, "ws2", agent_id="a2", chats=[])
    _, cnt2 = _mk_counter(ws2, "y", agent="a2")
    bare = os.path.join(tmp_dir, "ws-no-data")  # 无 process_tools_data → 跳过
    os.makedirs(bare)
    assert get_manager().cleanup_stale_sessions([ws1, ws2, bare]) == (1, 0)
    assert os.path.exists(cnt1) and not os.path.exists(cnt2)


def test_default_scan_via_workspaces_root(tmp_dir, monkeypatch):
    import manager as manager_mod

    root = os.path.join(tmp_dir, "workspaces")
    ws = _mk_ws(root, "wsD", chats=[])
    _, cnt = _mk_counter(ws, "z")
    monkeypatch.setattr(manager_mod, "workspaces_root", lambda: root)
    assert get_manager().cleanup_stale_sessions() == (1, 0)
    assert not os.path.exists(cnt)


def test_default_scan_no_root_is_noop(tmp_dir, monkeypatch):
    import manager as manager_mod

    monkeypatch.setattr(manager_mod, "workspaces_root", lambda: None)
    assert get_manager().cleanup_stale_sessions() == (0, 0)


def test_session_file_candidates_shape():
    cands = session_file_candidates("/S", "sid1", "uid1", "console")
    assert cands == [os.path.join("/S", "console", "uid1_sid1.json"),
                     os.path.join("/S", "uid1_sid1.json")]
    # uid==sid 省段
    assert os.path.basename(session_file_candidates("/S", "u", "u", "")[0]) == "u.json"
    # 非法 channel 不产生穿越路径
    bad = session_file_candidates("/S", "s", "u", "..")
    assert all(os.path.dirname(p) == "/S" for p in bad)
