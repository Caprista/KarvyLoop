"""external_runtime 五步接线断言 + V1 寻址验收 + 公民注册表持久化。

- 命名漂移断言(R1):三个工厂 build_tool(...).name 逐字命中 DEFAULT_TOOL_REQUIREMENTS 键,
  否则默认落回 FULL(HR-1),只读列举工具会被误判 capability_denied。
- V1 寻址(R2):派活 TASK_ASSIGN to=(域, external, citizen_id) 经 citizen-aware resolver
  route → rejected is False and reason != REJECT_NO_TARGET。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from karvyloop.a2a import (  # noqa: E402
    AuditChain, EnvelopeRouter, Inbox, REJECT_NO_TARGET,
)
from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES  # noqa: E402
from karvyloop.capability.deontic_gate import _READ_ONLY_TOOLS  # noqa: E402
from karvyloop.capability.policy import DEFAULT_TOOL_REQUIREMENTS, Mode, required_mode  # noqa: E402
from karvyloop.external_runtime import (  # noqa: E402
    ExternalCitizen, ExternalCitizenRegistry, ExternalCitizenStore,
    STATUS_ACTIVE, bridge_factory, citizen_address, make_citizen_aware_resolver,
)
from karvyloop.karvy.tools import (  # noqa: E402
    _build_task_assign, make_attach_external_agent_tool, make_external_agent_tool,
    make_list_external_agents_tool,
)


@pytest.fixture
def fake_deps():
    reg = ExternalCitizenRegistry()
    return dict(external=dict(citizen_registry=reg, bridge_factory=bridge_factory),
                attach=dict(citizen_registry=reg),
                lst=dict(citizen_registry=reg))


# ---- 步3 命名漂移断言(R1):三工厂名逐字命中下限表 ----

def test_external_tool_names_registered_in_policy(fake_deps):
    t_ext = make_external_agent_tool(**fake_deps["external"])
    t_att = make_attach_external_agent_tool(**fake_deps["attach"])
    t_lst = make_list_external_agents_tool(**fake_deps["lst"])
    for t in (t_ext, t_att, t_lst):
        assert t.name in DEFAULT_TOOL_REQUIREMENTS, (
            f"{t.name} 未在下限表登记 → required_mode 落回 FULL(HR-1),只读工具会被误判 capability_denied")
    # 钉死复数:list 工厂产的是复数名
    assert t_lst.name == "list_external_agents"


def test_external_tool_required_modes(fake_deps):
    assert required_mode(make_external_agent_tool(**fake_deps["external"]).name) == Mode.FULL
    assert required_mode(make_attach_external_agent_tool(**fake_deps["attach"]).name) == Mode.WORKSPACE_WRITE
    assert required_mode(make_list_external_agents_tool(**fake_deps["lst"]).name) == Mode.READ_ONLY


# ---- 步4 deontic 只读豁免 ----

def test_list_tool_read_only_exempt_in_deontic():
    assert "list_external_agents" in _READ_ONLY_TOOLS


# ---- 步5 catalog 归一防 unresolved 误判 ----

def test_external_tools_in_catalog():
    for n in ("external_agent", "attach_external_agent", "list_external_agents"):
        assert n in BUILTIN_TOOL_NAMES


# ---- V1 寻址(R2):citizen-aware resolver 让复合键解析到桥,route 不 REJECT_NO_TARGET ----

def _domain_only_resolver(to):
    # 生产态域成员解析器:只认域内 role/agent,不认外部公民
    return None if getattr(to, "role", "") == "external" else to


def test_v1_addressing_rejected_without_citizen_aware():
    reg = ExternalCitizenRegistry()
    c = ExternalCitizen(citizen_id="helper", runtime_kind="raw_text_sidecar",
                        bin_path="ext-cli", domain_id="dom-1", status=STATUS_ACTIVE)
    reg.add(c)
    router = EnvelopeRouter(inbox=Inbox(), audit_chain=AuditChain(),
                            address_resolver=_domain_only_resolver)
    env, _ = _build_task_assign(c, "1+2*3")
    r = router.route(env)
    assert r.rejected is True and r.reason == REJECT_NO_TARGET


def test_v1_addressing_routes_with_citizen_aware():
    reg = ExternalCitizenRegistry()
    c = ExternalCitizen(citizen_id="helper", runtime_kind="raw_text_sidecar",
                        bin_path="ext-cli", domain_id="dom-1", status=STATUS_ACTIVE)
    reg.add(c)
    resolver = make_citizen_aware_resolver(_domain_only_resolver, reg)
    router = EnvelopeRouter(inbox=Inbox(), audit_chain=AuditChain(), address_resolver=resolver)
    env, _ = _build_task_assign(c, "1+2*3")
    r = router.route(env)
    assert r.rejected is False and r.reason != REJECT_NO_TARGET
    assert r.target == citizen_address("dom-1", "helper")


def test_citizen_aware_still_prefers_domain_role():
    # 内层解析到域内 role → citizen-aware 不覆盖(先内层)
    reg = ExternalCitizenRegistry()
    from karvyloop.domain import Address
    resolver = make_citizen_aware_resolver(lambda to: to, reg)  # 恒等内层
    addr = Address(domain_id="d", role="engineer", agent_id="e1")
    assert resolver(addr) == addr


# ---- 公民注册表:复合键 + 持久化 ----

def test_registry_composite_key_isolation():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="rt", bin_path="x", domain_id="d1"))
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="rt", bin_path="y", domain_id="d2"))
    assert reg.resolve_in("d1", "cc").bin_path == "x"
    assert reg.resolve_in("d2", "cc").bin_path == "y"
    assert len(reg.list_all()) == 2  # 同花名跨域是不同挂载点


def test_registry_persists_and_reloads(tmp_path):
    path = tmp_path / "external_citizens.json"
    store = ExternalCitizenStore(path)
    reg = ExternalCitizenRegistry(store=store)
    ok = reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="raw_text_sidecar",
                                 bin_path="ext-cli", domain_id="", status=STATUS_ACTIVE))
    assert ok is True and path.exists()
    # 重启:新 registry 从盘恢复
    reg2 = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    c = reg2.resolve("cc")
    assert c is not None and c.runtime_kind == "raw_text_sidecar" and c.status == STATUS_ACTIVE


def test_store_never_persists_raw_key(tmp_path):
    # 公民记录只存元信息,绝不含真 key —— 落盘文件里不能出现 key 形态
    path = tmp_path / "external_citizens.json"
    reg = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id=""))
    from karvyloop.external_runtime import contains_secret
    assert not contains_secret(path.read_text(encoding="utf-8"))
