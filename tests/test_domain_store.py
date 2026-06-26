"""test_domain_store — 业务域定义持久化(M3+ 拍 9.2c-持久化)。

设计:docs/18 + docs/26 §C(domain_id 必须跨重启稳定)。

AC:
- AC1: save_all → load_all 往返保真(name/value_md/member_query/deontic/parent_id)
- AC2: **domain_id 跨重启稳定**(对话按 domain_id 分区的硬要求)
- AC3: restore 放回 registry(get/list_active 能查到)
- AC4: 文件不存在 / 坏 JSON → load_all 返空(不阻塞启动)
- AC5: console 端到端 — 建域存盘 → 新进程 load 回来 → peers 仍有它
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.domain.deontic import Deontic  # noqa: E402
from karvyloop.domain.registry import BusinessDomainRegistry  # noqa: E402
from karvyloop.domain.store import DomainStore, domain_from_dict, domain_to_dict  # noqa: E402


def _make_domain(reg: BusinessDomainRegistry, name="装修工作室", agent="设计师"):
    return reg.create(
        name=name,
        created_by="user:ch",
        value_md_raw="# 价值观\n\n- 诚实第一\n- 用户利益至上",
        deontic=Deontic(forbid=("夸大宣传",), oblige=("如实告知",)),
        member_query=f"user:ch AND agent:{agent}",
    )


# ---- AC1: 往返保真 ----


def test_save_load_roundtrip(tmp_path):
    reg = BusinessDomainRegistry()
    d = _make_domain(reg)
    store = DomainStore(tmp_path / "domains.json")
    store.save_all(reg.list_all())

    loaded = store.load_all()
    assert len(loaded) == 1
    r = loaded[0]
    assert r.name == "装修工作室"
    assert "诚实第一" in r.value_md.text
    assert r.member_query == "user:ch AND agent:设计师"
    assert r.deontic.forbid == ("夸大宣传",)
    assert r.deontic.oblige == ("如实告知",)


# ---- AC2: domain_id 稳定(硬要求)----


def test_domain_id_stable_across_reload(tmp_path):
    reg = BusinessDomainRegistry()
    d = _make_domain(reg)
    original_id = d.id
    store = DomainStore(tmp_path / "domains.json")
    store.save_all(reg.list_all())
    # 新 store(模拟重启)
    reloaded = DomainStore(tmp_path / "domains.json").load_all()
    assert reloaded[0].id == original_id  # id 必须原样(对话按 id 分区对得上)


# ---- AC3: restore 放回 registry ----


def test_restore_into_registry(tmp_path):
    reg1 = BusinessDomainRegistry()
    d = _make_domain(reg1)
    DomainStore(tmp_path / "domains.json").save_all(reg1.list_all())

    # 新 registry(模拟重启)+ load + restore
    reg2 = BusinessDomainRegistry()
    for dom in DomainStore(tmp_path / "domains.json").load_all():
        reg2.restore(dom)
    assert reg2.get(d.id) is not None
    assert reg2.get(d.id).name == "装修工作室"
    assert len(reg2.list_active()) == 1
    # resolve_members 仍能解析(member_query 持久 + 中文 agent)
    members = reg2.resolve_members(d.id)
    assert any(m.role == "agent" and m.agent_id == "设计师" for m in members)


# ---- AC4: 缺失 / 坏文件 ----


def test_load_missing_returns_empty(tmp_path):
    assert DomainStore(tmp_path / "nope.json").load_all() == []


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "domains.json"
    p.write_text("{坏 json", encoding="utf-8")
    assert DomainStore(p).load_all() == []


def test_to_from_dict_unit():
    reg = BusinessDomainRegistry()
    d = _make_domain(reg, name="财务", agent="会计")
    rec = domain_to_dict(d)
    assert isinstance(json.dumps(rec, ensure_ascii=False), str)  # JSON-able
    back = domain_from_dict(rec)
    assert back.id == d.id and back.name == "财务"


# ---- AC5: console 端到端(建域存盘 → 新进程 load 回来 → peers 有它)----


def test_console_create_persists_and_reloads(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.karvy.observer import WorkbenchObserver

    domains_path = tmp_path / "domains.json"

    # 进程 1:建域 + 存盘
    reg1 = BusinessDomainRegistry()
    store1 = DomainStore(domains_path)
    mgr1 = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg1)
    mgr1.start()
    app1 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app1.state.domain_registry = reg1
    app1.state.domain_store = store1
    app1.state.conversation_manager = mgr1
    c1 = TestClient(app1)
    r = c1.post("/api/domain/create", json={"name": "装修", "value_md": "诚实第一", "agent": "设计师"})
    assert r.json()["ok"] is True
    assert domains_path.exists()  # 存盘了

    # 进程 2(模拟重启):新 registry load 回来
    reg2 = BusinessDomainRegistry()
    for dom in DomainStore(domains_path).load_all():
        reg2.restore(dom)
    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app2.state.domain_registry = reg2
    c2 = TestClient(app2)
    peers = c2.get("/api/peers").json()["peers"]
    biz = [p for p in peers if not p["is_private"] and not p.get("is_group")]
    assert len(biz) == 1
    assert biz[0]["domain_name"] == "装修"  # 重启后业务域还在
