"""test_roundtable_structured_close — 圆桌结构化收口(A)+ 高风险结论过 H2A(B)+ REJECT 回执穿透(C)。

AC(任务验收):
- A1 杠精场景:1 个 agent 每轮孤立反对 → **轮数上限内**收口(少数派报告)+ dissent 留档
- A2 consensus 达阈值 → 提前收口
- A3 主持人返回坏 JSON → 退回旧词法("没到就再一轮")不崩
- A4 轮数上限可配(默认仍 3;硬顶夹住;任何情况到顶必停)
- B1 kind ∈ HIGH_RISK_KINDS:grant 硬地板拒 + 伪造授权 try_silence 仍不接管(earned-silence 证明)
- B2 高风险结论(共享层/带 dissent/未收敛)→ 升 H2A 卡**不直写**认知库;routine → 直写不弹卡
- B3 ACCEPT handler 才落认知库(dissent 随 provenance 留档;内容形状与直写一致)
- C1 pursuit-revise REJECT → 「接着追…」人话回执穿透到 DispatchResult.detail
- C2 其他 kind REJECT 回执语义不变(无钩子/空回执 → 通用 "rejected";钩子炸 → 不崩仍丢弃)
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.roundtable_engine import (  # noqa: E402
    _MAX_ROUNDS_CAP, _conclusion_risk, _effective_max_rounds, _host_moderate_call,
    _parse_moderation_json, _roundtable_result_doc, RESULT_DOC_DISSENT_HEADER,
)
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_MEMORY_CONFLICT, KIND_ROUNDTABLE_CONCLUSION, KIND_RUN_TASK,
    PendingProposalRegistry, proposal_for_pursuit_commit,
    proposal_for_roundtable_conclusion,
)
from karvyloop.karvy.roundtable import (  # noqa: E402
    CONSENSUS_THRESHOLD, DEFAULT_MAX_ROUNDS, normalize_moderation,
    run_roundtable_session,
)


# ---------------------------------------------------------------- helpers
async def _member(m, topic, transcript):
    return {"speaker": str(m), "text": f"{m} 的看法"}


class _GW:
    """假 gateway:complete 只吐一段固定文本(类名必须正好 TextDelta 由 events 提供)。"""

    def __init__(self, text: str) -> None:
        self._t = text

    def resolve_model(self, scope):  # noqa: ANN001
        return "m"

    async def complete(self, messages, tools, ref, *, system=None):  # noqa: ANN001
        from karvyloop.gateway.events import TextDelta
        yield TextDelta(text=self._t)


class _FakeMem:
    """最小认知库替身:只收 write(engine 其余 mem 调用都在 try/except 里)。"""

    def __init__(self) -> None:
        self.writes: list = []

    def write(self, belief) -> None:  # noqa: ANN001
        self.writes.append(belief)


# ================================================================ A:结构化收口(纯层)
@pytest.mark.asyncio
async def test_a1_troll_minority_report_closes_within_cap():
    """杠精每轮孤立反对 → 第 2 轮少数派报告收口(远早于轮数上限)+ dissent 留档。"""
    async def host(topic, transcript, *, final):
        if final:
            return {"text": "结论:按多数意见走"}
        # 共识没到阈值(杠精拉低),但只剩他一条孤立反对
        return {"consensus": 0.6, "open_dissents": ["杠精: 全都反对"],
                "recommendation": "综合多数收口"}
    out = await run_roundtable_session("怎么定价", ["a", "b", "杠精"],
                                       member_reply=_member, host_moderate=host, max_rounds=6)
    assert out["converged"] is True and out["rounds"] == 2      # 第 2 轮收口,没烧到 6
    assert out["dissents"] == ["杠精: 全都反对"]                  # 少数派报告留档
    assert out["consensus"] == 0.6
    assert "结论" in out["conclusion"]


@pytest.mark.asyncio
async def test_a2_consensus_threshold_early_close():
    async def host(topic, transcript, *, final):
        if final:
            return {"text": "结论:定 99"}
        return {"consensus": 0.9, "open_dissents": [], "recommendation": "够了"}
    out = await run_roundtable_session("定价", ["a", "b"],
                                       member_reply=_member, host_moderate=host, max_rounds=5)
    assert out["rounds"] == 1 and out["converged"] is True       # 0.9 ≥ 阈值 → 第 1 轮收
    assert out["consensus"] == 0.9 and out["dissents"] == []
    assert 0.0 < CONSENSUS_THRESHOLD <= 0.9                      # 阈值常量在合理区间


@pytest.mark.asyncio
async def test_a4_round_cap_configurable_and_hard_stop():
    """共识永远不到 + 多条分歧(少数派规则不触发)→ 烧满可配上限硬停,分歧如实留档。"""
    calls = {"n": 0}

    async def host(topic, transcript, *, final):
        if final:
            return {"text": "勉强收个尾"}
        calls["n"] += 1
        return {"consensus": 0.1, "open_dissents": ["a: 不同意", "b: 也不同意"],
                "recommendation": "再聊"}
    out = await run_roundtable_session("X", ["a", "b"],
                                       member_reply=_member, host_moderate=host, max_rounds=5)
    assert out["rounds"] == 5 and out["converged"] is False      # 可配 5 轮 → 到顶必停
    assert calls["n"] == 5
    assert out["dissents"] == ["a: 不同意", "b: 也不同意"]
    # 默认仍 3(不传 max_rounds)
    out2 = await run_roundtable_session("X", ["a"], member_reply=_member, host_moderate=host)
    assert out2["rounds"] == DEFAULT_MAX_ROUNDS == 3


def test_normalize_moderation_poison_resistant():
    """宁空勿毒:坏类型字段一律丢;无信号 → continue(没到就再一轮)。"""
    v = normalize_moderation({"consensus": 2.5, "open_dissents": "not-a-list"})
    assert v["structured"] and v["consensus"] == 1.0             # 数值夹回 [0,1]
    assert v["open_dissents"] == [] and v["action"] == "converge"
    v2 = normalize_moderation({"consensus": True})               # bool 不算数
    assert not v2["structured"] and v2["action"] == "continue"
    v3 = normalize_moderation({"open_dissents": [1, "  ", "ok: 真分歧"]})
    assert v3["open_dissents"] == ["ok: 真分歧"]                  # 非 str/空白剔除
    assert normalize_moderation(None)["action"] == "continue"
    assert normalize_moderation({"action": "converge"})["action"] == "converge"  # 旧式兼容


# ---------------- A3:engine 侧严格 JSON 解析 + 坏 JSON 退回旧词法 ----------------
def test_a3_host_moderate_parses_strict_json():
    d = asyncio.run(_host_moderate_call(
        _GW('{"consensus": 0.85, "open_dissents": ["风控: 预算超"], "recommendation": "收"}'),
        "", "定价", [], final=False))
    assert d == {"consensus": 0.85, "open_dissents": ["风控: 预算超"], "recommendation": "收"}
    # 只剥最外层 fence 也认
    d2 = asyncio.run(_host_moderate_call(
        _GW('```json\n{"consensus": 0.2, "open_dissents": [], "recommendation": ""}\n```'),
        "", "定价", [], final=False))
    assert d2["consensus"] == 0.2


def test_a3_bad_json_falls_back_to_word_logic_no_crash():
    # 坏 JSON → 旧词法:无 CONVERGE 词 → 再一轮
    d = asyncio.run(_host_moderate_call(_GW("{{ not json !!"), "", "t", [], final=False))
    assert d == {"action": "continue"}
    # 老式一词输出仍认(向后兼容)
    d2 = asyncio.run(_host_moderate_call(_GW("CONVERGE"), "", "t", [], final=False))
    assert d2 == {"action": "converge"}
    # 解析器本身:非 dict / bool / 越界 → None
    assert _parse_moderation_json("[1, 2]") is None
    assert _parse_moderation_json('{"consensus": true}') is None
    assert _parse_moderation_json('{"consensus": 1.5}') is None
    assert _parse_moderation_json("") is None


def test_effective_max_rounds_resolution():
    assert _effective_max_rounds({}) == DEFAULT_MAX_ROUNDS
    assert _effective_max_rounds({"max_rounds": 5}) == 5          # 待办态可配
    assert _effective_max_rounds({"max_rounds": 5}, 2) == 2       # 调用方覆盖优先
    assert _effective_max_rounds({"max_rounds": "junk"}) == DEFAULT_MAX_ROUNDS
    assert _effective_max_rounds({}, 99) == _MAX_ROUNDS_CAP       # 硬顶夹住,配置烧不穿
    assert _effective_max_rounds({}, 0) == 1


# ================================================================ B:高风险结论过 H2A
def test_b_conclusion_risk_boundary():
    """高风险边界:共享层一律 / 带遗留 dissent / 未达共识收口;域内干净共识 = routine。"""
    clean = {"converged": True, "dissents": []}
    assert _conclusion_risk(clean, shared_layer=True) == "shared_layer"
    assert _conclusion_risk({"converged": True, "dissents": ["x: 反对"]},
                            shared_layer=False) == "unresolved_dissent"
    assert _conclusion_risk({"converged": False, "dissents": []},
                            shared_layer=False) == "no_consensus"
    assert _conclusion_risk(clean, shared_layer=False) == ""      # routine:不弹卡


def test_b1_kind_high_risk_never_silenced(tmp_path):
    """earned-silence 证明:grant 硬地板授不出权;伪造授权塞进桶,try_silence 仍不接管。"""
    from karvyloop.karvy.silence import (
        HIGH_RISK_KINDS, SilenceGrantStore, bucket_key, try_silence,
    )
    assert KIND_ROUNDTABLE_CONCLUSION in HIGH_RISK_KINDS
    gstore = SilenceGrantStore(tmp_path / "grants.json")
    assert gstore.grant(KIND_ROUNDTABLE_CONCLUSION) is None       # ① 硬地板:授不出权
    import time as _t
    b = bucket_key(KIND_ROUNDTABLE_CONCLUSION, "")
    gstore._grants[b] = {"kind": KIND_ROUNDTABLE_CONCLUSION, "domain": "",
                         "granted_at": _t.time(), "expires_at": _t.time() + 99999,
                         "n": 99, "hits": 99, "revoked_at": None, "revoke_reason": ""}
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        silence_grants=gstore, proposal_handlers={}, proposal_registry=None,
        runtime_kwargs={"gateway": object()}, ws_clients=set()))
    card = proposal_for_roundtable_conclusion(
        topic="定价", conclusion="结论:99", domain_id="", applies={},
        dissents=[], risk="shared_layer", ts=1.0)
    assert try_silence(app, card) is False                        # ② 绝不被静音自动兑现


# ---------------- B2:engine 真路径 —— 高风险升卡不直写 / routine 直写不弹卡 ----------------
@pytest.fixture
def rt_app(tmp_path):
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    d1 = reg.create(name="装修", created_by="user:ch", value_md_raw="",
                    member_query="user:ch AND agent:设计师")
    app.state.task_registry = TaskRegistry()
    app.state.main_loop = object()
    app.state.runtime_kwargs = {"gateway": object(), "model_ref": "x", "workspace_root": "/"}
    app.state.memory = _FakeMem()
    app.state.proposal_registry = PendingProposalRegistry()
    mgr.set_peer(Address(domain_id=d1.id, role="group", agent_id=""))
    return app, mgr, d1


def _run_discussion(app, monkeypatch, result_patch):
    """走 /start + /discuss 真路径(session 打桩,不打真 LLM),返回 discuss 响应。"""
    import karvyloop.karvy.roundtable as rt_mod

    async def fake_session(goal, members, **kw):
        r = {"topic": goal, "rounds": 1, "converged": True, "conclusion": "结论:就按 A 方案",
             "transcript": [{"round": 1, "speaker": "设计师", "text": "我选 A"}],
             "consensus": 0.9, "dissents": [], "cancelled": False}
        r.update(result_patch)
        return r
    monkeypatch.setattr(rt_mod, "run_roundtable_session", fake_session)
    client = TestClient(app)
    start = client.post("/api/roundtable/start",
                        json={"intent": "客厅怎么改", "participants": ["设计师"]}).json()
    assert start["ok"] is True
    body = client.post("/api/roundtable/discuss",
                       json={"conversation_id": start["conversation_id"]}).json()
    assert body["ok"] is True
    return body


def test_b2_routine_conclusion_writes_directly_no_card(rt_app, monkeypatch):
    """域内 + 干净收敛 + 无分歧 = routine → 直写域私有认知,不弹卡(不打扰)。"""
    app, mgr, d1 = rt_app
    _run_discussion(app, monkeypatch, {})
    assert len(app.state.memory.writes) == 1                      # 直写照旧
    b = app.state.memory.writes[0]
    assert b.content.startswith("圆桌「") and "就按 A 方案" in b.content
    assert b.provenance["applies"] == {"domain": d1.id, "role": "group"}
    kinds = [getattr(p, "kind", "") for p in app.state.proposal_registry.pending()]
    assert KIND_ROUNDTABLE_CONCLUSION not in kinds                # 没弹卡


def test_b2_dissent_conclusion_raises_card_not_written(rt_app, monkeypatch):
    """收口带未解决 dissent = 高风险 → 升 H2A 卡,认知库一字不写;dissent 进卡 payload + 结果文档。"""
    app, mgr, d1 = rt_app
    _run_discussion(app, monkeypatch,
                    {"dissents": ["风控: 预算超了没人回应"], "consensus": 0.8})
    assert app.state.memory.writes == []                          # 不直写
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_ROUNDTABLE_CONCLUSION]
    assert len(cards) == 1
    pl = cards[0].payload
    assert pl["risk"] == "unresolved_dissent"
    assert pl["dissents"] == ["风控: 预算超了没人回应"]
    assert pl["applies"] == {"domain": d1.id, "role": "group"}
    # 少数派报告进结果文档(结构规则,不靠模型自觉)
    tk = [t for t in app.state.task_registry.list() if "圆桌" in (t.get("who") or "")][0]
    assert RESULT_DOC_DISSENT_HEADER in (tk.get("result") or "")
    assert "风控: 预算超了没人回应" in (tk.get("result") or "")


def test_b2_no_consensus_close_raises_card(rt_app, monkeypatch):
    """轮数到顶未达共识收口 = 高风险 → 升卡不直写。"""
    app, mgr, d1 = rt_app
    _run_discussion(app, monkeypatch, {"converged": False, "dissents": []})
    assert app.state.memory.writes == []
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_ROUNDTABLE_CONCLUSION]
    assert len(cards) == 1 and cards[0].payload["risk"] == "no_consensus"


def test_b2_max_rounds_flows_from_state_to_session(rt_app, monkeypatch):
    """待办态里的 max_rounds 真传进 session(调用方可配;默认 3)。"""
    app, mgr, d1 = rt_app
    import karvyloop.karvy.roundtable as rt_mod
    seen = {}

    async def fake_session(goal, members, **kw):
        seen["max_rounds"] = kw.get("max_rounds")
        return {"topic": goal, "rounds": 1, "converged": True, "conclusion": "",
                "transcript": [], "consensus": None, "dissents": [], "cancelled": False}
    monkeypatch.setattr(rt_mod, "run_roundtable_session", fake_session)
    client = TestClient(app)
    start = client.post("/api/roundtable/start",
                        json={"intent": "客厅怎么改", "participants": ["设计师"]}).json()
    from karvyloop.console.roundtable_engine import _roundtable_state
    _roundtable_state(app)[start["conversation_id"]]["max_rounds"] = 5
    client.post("/api/roundtable/discuss", json={"conversation_id": start["conversation_id"]})
    assert seen["max_rounds"] == 5


def test_b3_accept_handler_writes_belief_with_dissent_provenance():
    """ACCEPT 才落库:内容形状与直写一致,dissent/consensus 随 provenance 留档。"""
    from karvyloop.console.proposal_handlers import _roundtable_conclusion_handler
    mem = _FakeMem()
    app = types.SimpleNamespace(state=types.SimpleNamespace(memory=mem))
    prop = proposal_for_roundtable_conclusion(
        topic="客厅怎么改", conclusion="结论:就按 A 方案", domain_id="d1",
        applies={"domain": "d1", "role": "group"},
        dissents=["风控: 预算超了没人回应"], consensus=0.8, rounds=3,
        converged=True, risk="unresolved_dissent", ts=1.0)
    ok, detail = _roundtable_conclusion_handler(app)(prop)
    assert ok and "dissent" in detail
    assert len(mem.writes) == 1
    b = mem.writes[0]
    assert b.content == "圆桌「客厅怎么改」结论:结论:就按 A 方案"
    assert b.provenance["dissents"] == ["风控: 预算超了没人回应"]
    assert b.provenance["consensus"] == 0.8
    assert b.provenance["adopted_via"] == "h2a"
    assert b.provenance["applies"] == {"domain": "d1", "role": "group"}
    # 无 memory → 诚实失败不假装写了
    app2 = types.SimpleNamespace(state=types.SimpleNamespace(memory=None))
    ok2, _ = _roundtable_conclusion_handler(app2)(prop)
    assert ok2 is False


def test_b_card_id_idempotent_same_conclusion():
    a = proposal_for_roundtable_conclusion(topic="T", conclusion="C", risk="shared_layer", ts=1.0)
    b = proposal_for_roundtable_conclusion(topic="T", conclusion="C", risk="shared_layer", ts=2.0)
    c = proposal_for_roundtable_conclusion(topic="T", conclusion="C2", risk="shared_layer", ts=1.0)
    assert a.proposal_id == b.proposal_id != c.proposal_id        # 同结论收敛一张,不刷屏


# ================================================================ C:REJECT 回执穿透
def _prop(kind, payload=None, summary="s"):
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.5,
                    evidence_refs=(), habit_id=0, model_ref="m", ts=1.0,
                    kind=kind, payload=payload or {})


def test_c1_reject_hook_receipt_passes_through():
    reg = PendingProposalRegistry()
    p = _prop("kx")
    reg.register(p)
    res = reg.decide(p.proposal_id, "REJECT",
                     handlers={"kx:reject": lambda pr: (True, "human receipt: still chasing")})
    assert res.ok and res.detail == "human receipt: still chasing"  # 人话回执到用户
    assert reg.get(p.proposal_id) is None                           # 丢弃语义不变


def test_c1_pursuit_revise_reject_resumes_with_receipt(tmp_path):
    """真路径:pursuit-revise REJECT → 记录 resume 回 committed + 「接着追…」回执穿透。"""
    from karvyloop import i18n
    from karvyloop.cognition.pursuit_store import PursuitRecord, PursuitStore, new_pursuit_id
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import proposal_for_pursuit_revise
    from karvyloop.schemas import Pursuit
    store = PursuitStore(tmp_path / "pursuits.json")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pursuit_store=store, task_registry=TaskRegistry(),
        proposal_registry=PendingProposalRegistry(),
        main_loop=None, memory=None, trace=None,
        runtime_kwargs={"gateway": None, "model_ref": "", "workspace_root": str(tmp_path)},
        taste_predictions=None, decision_log=None, ws_clients=set()))
    app.state.proposal_handlers = build_proposal_handlers(app)
    p = Pursuit(id=new_pursuit_id("atom"), level="atom", statement="重构直到测试全绿",
                commitment_condition="", revision_triggers=[],
                verify_gate={"type": "file_exists", "path": str(tmp_path / "x")},
                status="active")
    rec = PursuitRecord(p)
    rec.pursuit = p.model_copy(update={"status": "committed"})
    rec.suspended = True                                          # 修订触发挂起中
    rec.revision_reason = "trigger fired"
    store.put(rec)
    card = proposal_for_pursuit_revise(pursuit_id=rec.id, statement=p.statement,
                                       revision_reason="trigger fired", ts=1.0)
    app.state.proposal_registry.register(card)
    res = app.state.proposal_registry.decide(card.proposal_id, "REJECT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.ok
    assert res.detail == i18n.t("receipt.pursuit_revise.resumed",
                                statement=p.statement[:60])       # 「接着追…」真的到用户
    rec2 = store.get(rec.id)
    assert rec2.status == "committed" and rec2.suspended is False  # 真的接着追


def test_c2_other_kinds_reject_receipt_unchanged(tmp_path):
    """无自定义回执的 kind(run_task / memory_conflict)REJECT 仍显通用 "rejected"。"""
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pursuit_store=None, task_registry=TaskRegistry(),
        proposal_registry=PendingProposalRegistry(),
        main_loop=None, memory=None, trace=None,
        runtime_kwargs={"gateway": None, "model_ref": "", "workspace_root": str(tmp_path)},
        taste_predictions=None, decision_log=None, ws_clients=set()))
    handlers = build_proposal_handlers(app)
    for kind in (KIND_RUN_TASK, KIND_MEMORY_CONFLICT):
        p = _prop(kind, payload={"intent": "x"}, summary=f"reject-{kind}")
        app.state.proposal_registry.register(p)
        res = app.state.proposal_registry.decide(p.proposal_id, "REJECT", handlers=handlers)
        assert res.ok and res.detail == "rejected"
        assert app.state.proposal_registry.get(p.proposal_id) is None
    # 亲手建的 pursuit_commit(origin 空):钩子返回空回执 → 仍通用 "rejected"(0 回归)
    card = proposal_for_pursuit_commit(pursuit_id="atom:x", statement="s", gate_desc="g", ts=1.0)
    app.state.proposal_registry.register(card)
    res = app.state.proposal_registry.decide(card.proposal_id, "REJECT", handlers=handlers)
    assert res.ok and res.detail == "rejected"


def test_c2_reject_hook_exception_keeps_generic_and_discards():
    reg = PendingProposalRegistry()
    p = _prop("kx")
    reg.register(p)

    def boom(pr):
        raise RuntimeError("hook down")
    res = reg.decide(p.proposal_id, "REJECT", handlers={"kx:reject": boom})
    assert res.ok and res.detail == "rejected"                    # 钩子炸不改变驳回语义
    assert reg.get(p.proposal_id) is None
    # 钩子返回 None(旧式)→ 也保持通用
    p2 = _prop("ky")
    reg.register(p2)
    res2 = reg.decide(p2.proposal_id, "REJECT", handlers={"ky:reject": lambda pr: None})
    assert res2.ok and res2.detail == "rejected"
