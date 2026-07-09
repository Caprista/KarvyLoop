"""test_residents_invite — 「请原住民进来」常驻门(Hardy 2026-07-09:引荐卡一生一次,之后加的
原住民如报销员再没门可进、没处浏览 → 补 /api/residents 浏览 + /api/residents/invite 随时请进来)。

用**随包真原住民**(residents_dir=None)锁:报销员 expense 能被列出、能请进来、幂等。
"""
from __future__ import annotations

import types

from karvyloop.capability.fs_grants import FsGrantsStore
from karvyloop.console.routes import (
    ResidentInviteRequest,
    api_residents,
    api_residents_invite,
)
from karvyloop.roles.registry import RoleRegistry


def _app(tmp_path):
    state = types.SimpleNamespace(
        role_registry=RoleRegistry(tmp_path / "roles"),
        fs_grants=FsGrantsStore(tmp_path / "fs_grants.json"),
        residents_dir=None,                 # 随包真原住民(含报销员 expense)
        residents_home=tmp_path / "home",
    )
    return types.SimpleNamespace(state=state)


def _req(app):
    return types.SimpleNamespace(app=app)


def test_lists_expense_resident_browsable(tmp_path):
    """报销员必须出现在可浏览清单里(否则又是'没门')——带本地化花名 + pitch,初始未入住。"""
    app = _app(tmp_path)
    out = api_residents(_req(app))
    by_id = {r["id"]: r for r in out["residents"]}
    assert "expense" in by_id, "报销员 expense 必须可被浏览到"
    exp = by_id["expense"]
    assert exp["instantiated"] is False, "还没请进来 → instantiated False"
    assert exp["name"] and exp["pitch"], "要有花名 + 一句话介绍(给人判断请不请)"


def test_invite_instantiates_into_registry(tmp_path):
    """请进来 = 真实例化成角色(在线注册表直接有),之后浏览显示 instantiated。"""
    app = _app(tmp_path)
    assert app.state.role_registry.get("expense") is None
    res = api_residents_invite(ResidentInviteRequest(id="expense"), _req(app))
    assert res["ok"] is True and res.get("created") is True, res
    assert app.state.role_registry.get("expense") is not None, "请进来后注册表里要有报销员"
    # 再浏览:报销员标记已入住
    exp = {r["id"]: r for r in api_residents(_req(app))["residents"]}["expense"]
    assert exp["instantiated"] is True


def test_invite_unknown_rejected(tmp_path):
    out = api_residents_invite(ResidentInviteRequest(id="not-a-resident"), _req(_app(tmp_path)))
    assert out["ok"] is False and "镜像不在" in out["reason"]


def test_invite_idempotent(tmp_path):
    """重复请进来不覆写你的实例(幂等复用)。"""
    app = _app(tmp_path)
    api_residents_invite(ResidentInviteRequest(id="expense"), _req(app))
    again = api_residents_invite(ResidentInviteRequest(id="expense"), _req(app))
    assert again["ok"] is True and again.get("created") is False, "第二次应复用,不重建"
