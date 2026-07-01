"""schemas 冒烟测试：每个契约能构造 + 关键不变量成立。

不追求覆盖业务逻辑（还没有），只验证契约本身可用、且文档里写明的不变量被强制。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from karvyloop.schemas import (
    AtomRun,
    AtomSpec,
    Belief,
    Capability,
    CapabilityToken,
    DomainManifest,
    Envelope,
    EphemeralTool,
    ModelDefinition,
    Norm,
    ProviderConfig,
    Pursuit,
    RoleSpec,
    Skill,
    UsageStats,
)


def test_capability_and_token():
    cap = Capability(resource="fs:/home/u/project", ops=["read", "write"])
    tok = CapabilityToken(task_id="t1", grants=[cap], expiry=1.0)
    assert tok.sig == ""  # 进程内 M0：签名可空（#5 §4）
    assert tok.grants[0].resource.startswith("fs:")


def test_model_registry():
    prov = ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-xxx",
        models=[
            ModelDefinition(
                id="anthropic/claude-opus",
                name="Claude Opus",
                api="anthropic-messages",
                context_window=200000,
                max_tokens=64000,
            )
        ],
    )
    assert prov.models[0].role == "chat"  # 默认 chat 槽位
    emb = ModelDefinition(
        id="ollama/bge-m3", name="bge-m3", api="ollama",
        role="embedding", context_window=8192, max_tokens=0,
    )
    assert emb.role == "embedding"


def test_agent_holds_model_ref_not_config():
    # 关键：agent 只持引用串，不内嵌完整模型配置（密钥不进可分享镜像，#0 §2.1）
    atom = AtomSpec(
        id="coder", kind="task", prompt="...",
        input_schema={}, output_schema={}, model="anthropic/claude-opus",
    )
    assert isinstance(atom.model, str)
    assert atom.is_read_only is False  # fail-closed 默认
    # model 为空 → None（由网关层叠解析到 default）
    assert AtomSpec(id="x", kind="task", prompt="", input_schema={}, output_schema={}).model is None


def test_extra_fields_forbidden():
    # 契约层禁止未知字段（no silent drift）
    with pytest.raises(ValidationError):
        RoleSpec(
            id="r",
            composition_ref="pm/composition.yaml",
            soul_refs={"IDENTITY": "pm/identity.md"},
            typo_field=1,  # noqa: 故意造错
        )


def test_envelope_artifact_requires_schema_id():
    # 不变量：artifact 必带 schema_id（#7 §6）
    with pytest.raises(ValidationError):
        Envelope(id="e", channel="c", from_addr="R.A", kind="artifact", ts=1.0)
    ok = Envelope(
        id="e", channel="c", from_addr="R.A", kind="artifact",
        schema_id="prd.v1", payload={"x": 1}, ts=1.0,
    )
    assert ok.schema_id == "prd.v1"
    # chat 不需要 schema_id
    Envelope(id="e2", channel="c", from_addr="R.A", kind="chat", ts=1.0)


def test_remaining_contracts_construct():
    AtomRun(atom_id="a", input={}, output=None, success=False, trace_ref="tr", ts=1.0)
    Norm(kind="prohibition", rule="no rm -rf /", scope="guardrail")
    DomainManifest(domain_id="d", kb_ref="kb")
    Pursuit(id="p", level="role", statement="ship", commitment_condition="c", verify_gate={})
    Belief(content="x", provenance={"source": "user"}, freshness_ts=1.0, scope="personal")
    EphemeralTool(
        id="t", from_intent="整理发票", code="...",
        input_schema={}, output_schema={}, created_at=1.0, ttl=86400.0,
    )
    skill = Skill(
        name="organize-invoices", manifest={}, body="...", from_candidate="c",
        usage=UsageStats(), verify_proof={"passed": True},
        created_at=1.0, evolved_at=1.0,
    )
    assert skill.scope == "personal"  # 默认私人技能


def test_role_spec_ontology_v2_2026_06_16():
    """#0 §2.4 / §2.4.1 修正落地:RoleSpec 不再持有 `atoms` 列表（原子是公共能力池）,
    也不再有 `orchestration_graph`（编排 = COMPOSITION.yaml 文件,不是 schema 字段）+ `bdi_ref`。
    取而代之:`composition_ref`（配方引用）+ `soul_refs`（灵魂层 7 文件引用映射）。

    这是宪法层 4 段修正（2026-06-16 第 3 次会话沉淀）的代码落地,锁住防漂移。
    """
    role = RoleSpec(
        id="pm",
        composition_ref="pm/composition.yaml",
        soul_refs={
            "IDENTITY":   "pm/identity.md",
            "SOUL":       "pm/soul.md",
            "USER":       "pm/user.md",
            "COMMITMENT": "pm/commitment.md",
            "VERIFY":     "pm/verify.md",
            "MEMORY":     "pm/memory.md",
        },
    )
    # 关键不变量 1:必须能拼出 7 文件清单里的 6 个灵魂文件(第 7 个 COMPOSITION 走 composition_ref)
    assert len(role.soul_refs) == 6
    for required in ("IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY", "MEMORY"):
        assert required in role.soul_refs

    # 关键不变量 2:旧字段被撤 → 不能再构造
    import pytest as _pt
    with _pt.raises(ValidationError):
        RoleSpec(
            id="pm",
            atoms=["write_prd"],                      # noqa: 旧本体论,已撤
            orchestration_graph={"nodes": []},        # noqa: 旧本体论,已撤
            bdi_ref="bdi",                            # noqa: 旧本体论,已撤
        )

    # 关键不变量 3:composition_ref 不能为空串(否则 role 没配方 = 没法用)
    with _pt.raises(ValidationError):
        RoleSpec(id="pm", composition_ref="", soul_refs={})
