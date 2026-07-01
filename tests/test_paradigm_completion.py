"""docs/02 §14 ③ — 范式对话式补全引擎:检测缺层 + LLM 起草 + 人确认才落(不一股脑落库)。

不变量:① 检测出空/stub 的层 ② LLM 只为缺层起草、宁空勿毒(垃圾→{}不投毒)③ 起草不直接落库
④ 补全闭环:起草→人确认(update)→缺层减少 ⑤ complete 标志=范式齐没齐。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from karvyloop.roles.completion import detect_paradigm_gaps, suggest_paradigm_completion
from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.atoms.registry import AtomRegistry
from karvyloop.roles.registry import RoleRegistry


class TextDelta:
    def __init__(self, t):
        self.text = t


class FakeGateway:
    def __init__(self, out):
        self.out = out

    def resolve_model(self, scope):
        return "m"

    async def complete(self, m, t, mr, *, system=None):
        yield TextDelta(self.out)


# ============ 检测缺层 ============

def test_detect_gaps_only_identity_filled():
    pm = {"identity": "数据分析师", "soul": "(待充实)", "user": "(待充实)", "verify": "(待充实)"}
    assert detect_paradigm_gaps(pm) == ["SOUL", "USER", "VERIFY"]


def test_detect_gaps_none_when_filled():
    pm = {"identity": "x", "soul": "严谨", "user": "数据团队", "verify": "带出处"}
    assert detect_paradigm_gaps(pm) == []


# ============ LLM 起草(宁空勿毒)============

def test_suggest_drafts_only_gap_slots():
    pm = {"identity": "数据分析师", "soul": "(待充实)", "user": "(待充实)", "verify": "(待充实)"}
    gaps = ["SOUL", "USER", "VERIFY"]
    gw = FakeGateway('{"SOUL":"严谨求证，先验证再下结论","USER":"为数据团队服务","VERIFY":"产出必须带出处和数字"}')
    out = asyncio.run(suggest_paradigm_completion(pm, gaps, gateway=gw))
    assert out["SOUL"].startswith("严谨") and out["USER"] and out["VERIFY"]


def test_suggest_garbage_is_empty_not_poison():
    pm = {"identity": "x", "soul": "(待充实)"}
    for bad in ("我觉得可以这样哦", "not json {", '{"SOUL": 123}', "[]"):
        assert asyncio.run(suggest_paradigm_completion(pm, ["SOUL"], gateway=FakeGateway(bad))) == {}


def test_suggest_no_gateway_or_no_gaps_empty():
    assert asyncio.run(suggest_paradigm_completion({}, ["SOUL"], gateway=None)) == {}
    assert asyncio.run(suggest_paradigm_completion({"soul": "x"}, [], gateway=FakeGateway("{}"))) == {}


# ============ API + 补全闭环 ============

@pytest.fixture
def client(tmp_path):
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.console.tasks import TaskRegistry
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.atom_registry = AtomRegistry()
    app.state.role_registry = RoleRegistry(tmp_path / "roles", atom_registry=app.state.atom_registry)
    app.state.domain_registry = BusinessDomainRegistry()
    app.state.task_registry = TaskRegistry()
    app.state.runtime_kwargs = {"gateway": FakeGateway('{"SOUL":"严谨求证","USER":"为数据团队","VERIFY":"带出处"}'),
                                "model_ref": "m"}
    return TestClient(app)


def test_gaps_endpoint_detects_and_drafts(client):
    client.app.state.role_registry.create("analyst", identity="数据分析师")  # 只填 identity
    r = client.get("/api/role/paradigm/gaps", params={"role_id": "analyst"}).json()
    assert r["ok"] is True
    assert set(r["gaps"]) == {"SOUL", "USER", "VERIFY"} and r["complete"] is False
    # LLM 起草了建议(给前端做问答补全),但**没落库**
    assert r["suggestions"]["SOUL"] == "严谨求证"
    # 确认起草没偷偷落库:范式里 SOUL 还是 stub
    pm = client.get("/api/role/paradigm", params={"role_id": "analyst"}).json()["paradigm"]
    assert pm["soul"] == "(待充实)"


def test_completion_loop_closes_after_user_accepts(client):
    client.app.state.role_registry.create("analyst", identity="x")
    # 起草 → 人确认(把建议落进去,走 ③A 的 update)→ 缺层减少
    sug = client.get("/api/role/paradigm/gaps", params={"role_id": "analyst"}).json()["suggestions"]
    client.post("/api/role/paradigm/update", json={"role_id": "analyst", "slot": "SOUL", "text": sug["SOUL"]})
    client.post("/api/role/paradigm/update", json={"role_id": "analyst", "slot": "USER", "text": sug["USER"]})
    client.post("/api/role/paradigm/update", json={"role_id": "analyst", "slot": "VERIFY", "text": sug["VERIFY"]})
    r2 = client.get("/api/role/paradigm/gaps", params={"role_id": "analyst"}).json()
    assert r2["gaps"] == [] and r2["complete"] is True       # 闭环:补全后范式齐了


def test_gaps_missing_role(client):
    assert client.get("/api/role/paradigm/gaps", params={"role_id": "ghost"}).json()["ok"] is False
