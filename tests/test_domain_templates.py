"""test_domain_templates — 开箱域模板「一键开公司」(docs/42 优化④)。

不变量:① 每个模板自身合法:value.md 过 ValueMd.parse、deontic 建得起来、角色灵魂非空
② 实例化真建:角色进角色库(带 COMMITMENT 契约 seed)+ 域进注册表(value/deontic/成员齐)
③ 幂等与拒绝:角色已存在→复用;同名活跃域→拒(明确 reason)④ 列表接口轻量(不带全文)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.domain.registry import BusinessDomainRegistry  # noqa: E402
from karvyloop.domain.templates import (  # noqa: E402
    TEMPLATES, get_template, instantiate_template, list_templates)
from karvyloop.domain.value import ValueMd  # noqa: E402
from karvyloop.roles.registry import RoleRegistry  # noqa: E402


def test_all_templates_are_valid():
    assert len(TEMPLATES) >= 5
    ids = [t["id"] for t in TEMPLATES]
    assert len(ids) == len(set(ids))                      # id 不重
    assert "finance-research" in ids                      # 骑浪 demo 在
    for t in TEMPLATES:
        vm = ValueMd.parse(t["value_md"])                 # value.md 合规范
        assert vm.principles, t["id"]
        assert t["deontic"]["forbid"], t["id"]            # 每家至少一条硬规矩
        assert len(t["roles"]) >= 2, t["id"]
        for r in t["roles"]:
            assert r["identity"].strip() and r["soul"].strip(), (t["id"], r["role_id"])
        assert t.get("seed_intents"), t["id"]             # 示例开场白(演示脚本用)


def test_list_is_light():
    lst = list_templates()
    assert len(lst) == len(TEMPLATES)
    assert "value_md" not in lst[0] and lst[0]["roles"][0]["title"]
    assert get_template("nope") is None


def test_instantiate_creates_roles_and_domain(tmp_path):
    roles = RoleRegistry(tmp_path / "roles")
    domains = BusinessDomainRegistry()
    res = instantiate_template("finance-research", domain_registry=domains, role_registry=roles)
    assert res["ok"], res
    assert set(res["roles_created"]) == {"macro-analyst", "risk-officer"}
    d = domains.get(res["domain_id"])
    assert d.name == "理财研究所"
    assert "直接执行任何交易或转账操作" in d.deontic.forbid   # 硬规矩真进 deontic(P2-a 会进运行时护栏)
    assert "agent:macro-analyst" in d.member_query
    assert "数据" in " ".join(d.value_md.principles)
    # 角色真物化:七文件目录 + 尽责下属契约 seed(三入口统一)
    rv = roles.get("macro-analyst")
    commitment = (pathlib.Path(rv.path) / "COMMITMENT.md").read_text(encoding="utf-8")
    assert commitment.strip(), "COMMITMENT 契约未 seed"


def test_instantiate_idempotent_roles_and_dup_domain_refused(tmp_path):
    roles = RoleRegistry(tmp_path / "roles")
    domains = BusinessDomainRegistry()
    roles.create("researcher", identity="已有的研究员", soul="旧灵魂")   # 预置同名角色
    res = instantiate_template("personal-research", domain_registry=domains, role_registry=roles)
    assert res["ok"]
    assert "researcher" in res["roles_reused"] and "reviewer" in res["roles_created"]
    # 已有的角色灵魂不被覆盖
    assert "已有的研究员" in (pathlib.Path(roles.get("researcher").path) / "IDENTITY.md").read_text(encoding="utf-8")
    # 同名域再开 → 拒
    res2 = instantiate_template("personal-research", domain_registry=domains, role_registry=roles)
    assert not res2["ok"] and "同名" in res2["reason"]


def test_instantiate_unknown_and_unwired():
    assert not instantiate_template("nope", domain_registry=None, role_registry=None)["ok"]
    assert "未接" in instantiate_template("job-hunt", domain_registry=None, role_registry=None)["reason"]


def test_api_templates_and_instantiate(tmp_path):
    """API 全链路:列模板 → 一键开公司 → 域和角色真在;前端接线在位(非 self-hype)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.domain_registry = BusinessDomainRegistry()
    app.state.role_registry = RoleRegistry(tmp_path / "roles")
    app.state.domain_store = None
    client = TestClient(app)
    lst = client.get("/api/domain/templates").json()["templates"]
    assert any(t["id"] == "home-ops" for t in lst)
    r = client.post("/api/domain/templates/instantiate", json={"template_id": "home-ops"}).json()
    assert r["ok"], r
    assert any(getattr(d, "name", "") == "家庭运营部" for d in app.state.domain_registry.list_active())
    # 未接 registry → 诚实 reason
    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r2 = TestClient(app2).post("/api/domain/templates/instantiate", json={"template_id": "home-ops"}).json()
    assert not r2["ok"]
    # 前端接线
    src = (ROOT / "karvyloop" / "console" / "frontend" / "src" / "domains_panel.ts").read_text(encoding="utf-8")
    # domtpl.use(旧 domtpl.open 已按 Hardy 语义纠正:模板是"用此模板新建"不是"打开已有")
    assert "/api/domain/templates" in src and "domtpl.use" in src
