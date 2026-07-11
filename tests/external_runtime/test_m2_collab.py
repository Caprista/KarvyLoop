"""M2:外部公民进圆桌/workflow 当客人供稿席(#71 §7)。三条红线锁死:

- 恒 untrusted:外部产出 provenance 恒 untrusted、绝不自动采纳(H2A 才采纳)。
- 不占决策席:外部产出不进 role 讨论主线、不直接触发别人;每条升 external_adopt 采纳门。
- 确定性域边界:scoped 只进绑定域(跨域拒);guest 任意域当纯客人;够不到域私有认知。

测的是真接缝:external_collab 纯层 + external_adopt 采纳门 handler + workflow 外部 step 执行。
不 mock 掉被测逻辑——桥用可注入的假 runner(不起真子进程),走真 SubprocessBridge 解析路径。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.external_runtime import (  # noqa: E402
    ExternalCitizen, ExternalCitizenRegistry, STATUS_ACTIVE, STATUS_UNREACHABLE,
    TIER_GUEST, TIER_SCOPED, bridge_factory, builtin_recipe, compute_manifest_hash,
)
from karvyloop.karvy.external_collab import (  # noqa: E402
    PROVENANCE_UNTRUSTED, SOURCE_EXTERNAL, build_external_adopt_proposal,
    can_join_domain, drive_external_contribution, find_external_target,
)


# ---------- 帮手:造一个 tier 指定、指纹对得上的 active 公民 ----------

# 用一个**真实存在**的 bin(sys.executable)当外部 runtime 的 bin_path —— use-time 复验里
# verify_manifest_hash 会 _which(bin) 查存在性(不起进程,真进程由注入的假 runner 拦)。
_REAL_BIN = sys.executable


def _make_citizen(citizen_id="cc", domain_id="", tier=TIER_GUEST, kind="raw_text_sidecar"):
    recipe = builtin_recipe(kind)
    # citizen 的 bin_path 用真实 bin;manifest_hash 按同一 bin 算(与 use-time 复验口径一致)。
    h = compute_manifest_hash(bin_path=_REAL_BIN, version="",
                              argv_template=recipe.argv_template,
                              blocked_entrypoints=recipe.blocked_entrypoints)
    return ExternalCitizen(citizen_id=citizen_id, runtime_kind=kind,
                           bin_path=_REAL_BIN, domain_id=domain_id, tier=tier,
                           manifest_hash=h, status=STATUS_ACTIVE)


def _fake_runner(stdout="external analysis result", exit_code=0):
    """假子进程 runner:走真 SubprocessBridge 解析,但不起真进程(注入 stdout/退出码)。"""
    def runner(argv, *, env, timeout, cwd, **kw):
        return types.SimpleNamespace(returncode=exit_code, stdout=stdout, stderr="")
    return runner


def _bf(stdout="external analysis result", exit_code=0):
    return lambda recipe: bridge_factory(recipe, runner=_fake_runner(stdout, exit_code))


# ============ 1. 域约束(确定性边界,#71 §2.6.5)============

def test_scoped_only_joins_bound_domain():
    c = _make_citizen("cc", domain_id="d1", tier=TIER_SCOPED)
    assert can_join_domain(c, "d1") is True        # 绑定域可进
    assert can_join_domain(c, "d2") is False       # 跨域拒(deny-by-default)
    assert can_join_domain(c, "") is False


def test_guest_joins_any_domain_as_pure_guest():
    c = _make_citizen("cc", domain_id="", tier=TIER_GUEST)
    assert can_join_domain(c, "d1") is True
    assert can_join_domain(c, "") is True


def test_no_tier_can_read_domain_private():
    # 域私有认知:任何 tier 都不可读不可写(T2 绝不实现)
    for tier in (TIER_GUEST, TIER_SCOPED):
        c = _make_citizen("cc", domain_id="d1", tier=tier)
        assert c.can_read_domain_private("d1") is False
        assert c.can_write_domain_private("d1") is False


# ============ 2. 复合键解析 ============

def test_find_external_target_composite_key():
    reg = ExternalCitizenRegistry()
    reg.add(_make_citizen("cc", domain_id="d1", tier=TIER_SCOPED))
    assert find_external_target(reg, "d1", "cc") is not None
    # 原生 role 名(注册表没有)→ None
    assert find_external_target(reg, "d1", "analyst") is None
    # registry 未接 → None(零回归)
    assert find_external_target(None, "d1", "cc") is None


# ============ 3. 供稿恒 untrusted、不自动采纳 ============

def test_contribution_is_always_untrusted():
    c = _make_citizen("cc", domain_id="d1", tier=TIER_SCOPED)
    reg = ExternalCitizenRegistry()
    reg.add(c)
    out = asyncio.run(drive_external_contribution(
        c, "analyze this", bridge_factory=_bf("my analysis"), citizen_registry=reg,
        seed_id="seed-1"))
    assert out["ok"] is True
    assert out["provenance"] == PROVENANCE_UNTRUSTED   # 恒 untrusted(红线1)
    assert out["source"] == SOURCE_EXTERNAL
    assert out["origin"] == "external:cc"
    assert out["is_external"] is True
    assert out["text"] == "my analysis"
    # 供稿登记进账本、adopted=False(未采纳临时数据,detach 会清)
    contribs = reg._contributions.get(("d1", "cc"))
    assert contribs and contribs[0]["adopted"] is False


def test_contribution_failure_does_not_raise():
    c = _make_citizen("cc", tier=TIER_GUEST)
    # 退非 0 → bridge failed → contribution ok=False(不抛,客人席失败不拖垮整桌)
    out = asyncio.run(drive_external_contribution(
        c, "task", bridge_factory=_bf("err", exit_code=1), seed_id="s1"))
    assert out["ok"] is False
    assert out["is_external"] is True


def test_unreachable_citizen_not_dispatched():
    import dataclasses
    c = dataclasses.replace(_make_citizen("cc", tier=TIER_GUEST), status=STATUS_UNREACHABLE)
    out = asyncio.run(drive_external_contribution(
        c, "task", bridge_factory=_bf(), seed_id="s1"))
    assert out["ok"] is False   # 非 active 不派(fail-loud)


# ============ 4. H2A 采纳门(唯一升级门)============

def test_adopt_proposal_untrusted_and_idempotent():
    p = build_external_adopt_proposal(citizen_id="cc", domain_id="d1", seed_id="seed-1",
                                      output="result", ts=time.time())
    assert p.kind == "external_adopt"
    assert p.options == ("ACCEPT", "DEFER", "REJECT")
    assert p.payload["provenance"] == PROVENANCE_UNTRUSTED   # 卡上恒 untrusted
    assert p.payload["output"] == "result"
    # 幂等:同 (citizen, seed) 一张卡
    p2 = build_external_adopt_proposal(citizen_id="cc", domain_id="d1", seed_id="seed-1",
                                       output="DIFFERENT", ts=time.time() + 9)
    assert p.proposal_id == p2.proposal_id


def test_adopt_gate_registered_in_handlers():
    # KIND_EXTERNAL_ADOPT 必须在 handler 表里,否则 ACCEPT 空转吞卡(断③)
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import KIND_EXTERNAL_ADOPT
    handlers = build_proposal_handlers(app=None)
    assert KIND_EXTERNAL_ADOPT in handlers


def test_adopt_handler_accept_writes_memory_and_marks_adopted():
    """ACCEPT → untrusted 供稿升记忆(标来源 external_runtime)+ 账本标 adopted(detach 不级联删)。"""
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import KIND_EXTERNAL_ADOPT

    # 假 memory(记 write)+ 真 registry(先登记一条未采纳供稿)
    written = []
    mem = types.SimpleNamespace(write=lambda b: written.append(b))
    reg = ExternalCitizenRegistry()
    reg.add(_make_citizen("cc", domain_id="d1", tier=TIER_SCOPED))
    reg.record_contribution("d1", "cc", seed_id="seed-1", note="x", adopted=False)
    app = types.SimpleNamespace(state=types.SimpleNamespace(memory=mem, citizen_registry=reg))

    handlers = build_proposal_handlers(app)
    prop = build_external_adopt_proposal(citizen_id="cc", domain_id="d1", seed_id="seed-1",
                                         output="the external result", ts=time.time())
    ok, detail = handlers[KIND_EXTERNAL_ADOPT](prop)
    assert ok is True
    # 升了记忆,来源标 external_runtime(untrusted 经 H2A 才进,标签跟着走)
    assert len(written) == 1
    assert written[0].provenance.get("source") == "external_runtime"
    assert written[0].provenance.get("adopted_via") == "h2a"
    # 账本标 adopted → detach 不级联删这条
    assert reg._contributions[("d1", "cc")][0]["adopted"] is True
    reg.detach("d1", "cc")
    assert "seed-1" in reg.last_detach_trace["kept_adopted"]


def test_unadopted_contribution_cleared_on_detach():
    """未采纳供稿(没过 H2A)→ detach 清理(它没进用户数据,撤人即撤稿)。"""
    reg = ExternalCitizenRegistry()
    reg.add(_make_citizen("cc", domain_id="d1", tier=TIER_SCOPED))
    reg.record_contribution("d1", "cc", seed_id="unadopted-1", note="x", adopted=False)
    reg.detach("d1", "cc")
    assert "unadopted-1" in reg.last_detach_trace["cleared_unadopted"]
    assert reg.last_detach_trace["kept_adopted"] == []


# ============ 5. workflow 外部 step:untrusted 数据流,不自动采纳 ============

@pytest.mark.asyncio
async def test_workflow_external_step_produces_untrusted_and_gate():
    """workflow 一步指派给外部公民 → 走 bridge,产出 untrusted 数据流给下游 + 升采纳门。"""
    from karvyloop.console.workflow_engine import _maybe_run_external_step

    reg = ExternalCitizenRegistry()
    reg.add(_make_citizen("cc", domain_id="d1", tier=TIER_SCOPED))
    registered = []
    proposal_reg = types.SimpleNamespace(register=lambda p: registered.append(p))
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        citizen_registry=reg, proposal_registry=proposal_reg,
        external_bridge_factory=_bf("cc did the work"),
        external_token_recorder=None, runtime_kwargs={}))
    # broadcast_proposal 会 import 真模块;它对 app=SimpleNamespace 可能失败——用 monkeypatch 兜。
    import karvyloop.console.proposals as _pp
    _orig = _pp.broadcast_proposal

    async def _noop(*a, **k):
        return None
    _pp.broadcast_proposal = _noop
    try:
        step = {"id": "s1", "agent_id": "cc", "domain_id": "d1", "task": "analyze"}
        res = await _maybe_run_external_step(app, step, {}, {"s1": "🔌 cc"},
                                             goal="g", run_id="run1", task_id=None)
    finally:
        _pp.broadcast_proposal = _orig
    assert res is not None                      # 被当外部步处理(不是原生 role)
    assert res["output"] == "cc did the work"   # untrusted 产出,memoize 给下游
    # 升了 external_adopt 采纳门(H2A 才穿来源边界,不自动采纳)
    assert len(registered) == 1
    assert registered[0].kind == "external_adopt"
    assert registered[0].payload["provenance"] == PROVENANCE_UNTRUSTED


@pytest.mark.asyncio
async def test_workflow_native_role_step_returns_none():
    """原生 role step(注册表没这个 citizen)→ _maybe_run_external_step 返 None(交回原生路径)。"""
    from karvyloop.console.workflow_engine import _maybe_run_external_step
    reg = ExternalCitizenRegistry()   # 空注册表
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        citizen_registry=reg, proposal_registry=None, runtime_kwargs={}))
    step = {"id": "s1", "agent_id": "analyst", "domain_id": "d1", "task": "x"}
    res = await _maybe_run_external_step(app, step, {}, {}, goal="g", run_id="r", task_id=None)
    assert res is None


@pytest.mark.asyncio
async def test_workflow_scoped_cross_domain_rejected():
    """scoped 公民绑 d1,workflow step 却在 d2 → fail-loud 拒(deny-by-default),不静默跑。"""
    from karvyloop.console.workflow_engine import _maybe_run_external_step
    reg = ExternalCitizenRegistry()
    reg.add(_make_citizen("cc", domain_id="d1", tier=TIER_SCOPED))
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        citizen_registry=reg, proposal_registry=None,
        external_bridge_factory=_bf(), runtime_kwargs={}))
    step = {"id": "s1", "agent_id": "cc", "domain_id": "d2", "task": "x"}   # 跨域!
    res = await _maybe_run_external_step(app, step, {}, {}, goal="g", run_id="r", task_id=None)
    assert res is not None
    assert res["output"] == ""
    assert "scoped 跨域" in res["error"] or "不能进域" in res["error"]
