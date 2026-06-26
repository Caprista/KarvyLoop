"""test_group_chat — ch4 KarvyChat 多方场地基:群场可选 + 小卡当协调者。

AC:
- AC1 /api/peers 含 karvy world 大群(role=group);有业务域 → 含该域 域群
- AC2 build_group_coordinator_prompt:含"协调者" + 群名 + 成员名册
- AC3 _persona_for_current_peer:peer.role=="group" → 协调者人格(非角色/默认)
- AC4 speaker_display:群场 → ""(小卡协调者发言)
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.coding.persona import build_group_coordinator_prompt


# ---- AC1 ----
def test_peers_includes_world_group():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    peers = TestClient(app).get("/api/peers").json()["peers"]
    groups = [p for p in peers if p.get("role") == "group"]
    assert any(p["domain_id"] == "l0" and p.get("is_group") for p in groups)  # 大群
    assert any("大群" in p["label"] for p in groups)


# ---- AC2 ----
def test_coordinator_prompt():
    cp = build_group_coordinator_prompt("装修公司", ["设计师", "施工队"])
    txt = cp.to_text()
    assert "协调者" in txt and "装修公司" in txt
    assert "设计师" in txt and "施工队" in txt
    assert "别冒充" in txt  # 不冒充群里某成员


# ---- AC3/AC4 ----
def _peer(domain_id, role, agent_id=""):
    return types.SimpleNamespace(domain_id=domain_id, role=role, agent_id=agent_id)


def _app_with_mgr(peer):
    mgr = types.SimpleNamespace(current_peer=lambda: peer)
    st = types.SimpleNamespace(domain_registry=None, role_registry=None)
    app = types.SimpleNamespace(state=st)
    return app, mgr


def test_persona_for_group_is_coordinator():
    from karvyloop.console.routes import _persona_for_current_peer
    app, mgr = _app_with_mgr(_peer("l0", "group"))
    cp = _persona_for_current_peer(app, mgr, "/tmp")
    assert cp is not None and "协调者" in cp.to_text()   # 群 → 协调者人格


def test_speaker_display_group_is_karvy():
    from karvyloop.console.routes import speaker_display
    app, mgr = _app_with_mgr(_peer("dom-x", "group"))
    assert speaker_display(app, mgr) == ""   # 群场:小卡协调者发言(前端映射小卡)
