"""test_proposal_chain — 决策卡同链合并·刀1(docs/92):chain_id 贯通(确定性,零 LLM)。

覆盖(设计定稿的三个后端点):
① Proposal.chain_id 字段:to_dict/from_dict 往返;老卡 dict 无此键 → ""(前端兼容);
② handler 派生透传:route_to_role 处理 A 卡过程中注册的新卡(confirm_result /
   infeasible_report)继承 A 的链 —— A 没 chain_id 则 A.proposal_id 当链根;
③ 同任务兜底:register 时提案带 context_ref.kind=="task" 且已有待决提案同 task_id →
   自动同链(在 registry.register 内做,不靠 handler 自觉);
④ chain_intent(chain_id):链上最早那张的人话摘要(route 卡取 requirement,截 ~60 字),
   register 时算好存好(有界,查询 O(1));落盘跨重启存活;链上最后一张离开随之清;
⑤ 出口口径 proposal_wire_payload:chain_intent + high_risk(silence.HIGH_RISK_KINDS)。

红线:不丢拍板粒度 —— 链只是视觉收纳,每张卡仍独立拍(本文件不动 decide 语义,回归靠
test_proposal_registry.py 既有套件)。
"""
from __future__ import annotations

import json
import time
import types
from pathlib import Path

from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import (
    CHAIN_INTENT_MAX,
    KIND_CONFIRM_RESULT,
    KIND_INFEASIBLE_REPORT,
    KIND_ROUTE_TO_ROLE,
    PendingProposalRegistry,
    chain_root_of,
    proposal_for_route,
    with_chain,
)


def _prop(pid: str, *, kind: str = "crystallize_skill", summary: str = "s",
          chain_id: str = "", context_ref: dict | None = None, ts: float = 1.0) -> Proposal:
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.5,
                    evidence_refs=(), habit_id=0, model_ref="", ts=ts, kind=kind,
                    proposal_id=pid, chain_id=chain_id, context_ref=dict(context_ref or {}))


# ---- ① chain_id 字段 + 序列化兼容 ----

def test_chain_id_roundtrip_and_legacy_dict():
    p = _prop("p-1", chain_id="chain-x")
    d = p.to_dict()
    assert d["chain_id"] == "chain-x"
    assert Proposal.from_dict(d).chain_id == "chain-x"
    # 老落盘文件/老广播 payload 无 chain_id 键 → ""(无链老卡兼容,0 回归)
    legacy = {k: v for k, v in d.items() if k != "chain_id"}
    assert Proposal.from_dict(legacy).chain_id == ""


def test_chain_root_of_and_with_chain():
    root = _prop("p-root")
    assert chain_root_of(root) == "p-root"          # 没链 → 自己就是链根
    derived = with_chain(_prop("p-d"), chain_root_of(root))
    assert derived.chain_id == "p-root" and chain_root_of(derived) == "p-root"
    assert _prop("p-d").chain_id == ""              # 原对象不动(frozen replace)
    assert with_chain(derived, "") is derived        # 空链 → 原样返回


# ---- ③ 同任务兜底(registry.register 内做) ----

def test_task_fallback_links_same_task_pending():
    reg = PendingProposalRegistry()
    a = _prop("p-a", context_ref={"kind": "task", "id": "t1"}, ts=1.0)
    b = _prop("p-b", context_ref={"kind": "task", "id": "t1"}, ts=2.0)
    reg.register(a, now=1.0)
    reg.register(b, now=2.0)
    # 都没 chain_id → 先来的 proposal_id 当链根;继承发生在 registry 存的那份上
    assert reg.get("p-b").chain_id == "p-a"
    assert reg.get("p-a").chain_id == ""            # 链根卡不回填(chain_root_of 兜底)
    # 已有链的待决卡 → 新同任务卡继承它的链(不是它的 pid)
    c = _prop("p-c", context_ref={"kind": "task", "id": "t2"}, chain_id="chain-z", ts=3.0)
    d = _prop("p-d", context_ref={"kind": "task", "id": "t2"}, ts=4.0)
    reg.register(c, now=3.0)
    reg.register(d, now=4.0)
    assert reg.get("p-d").chain_id == "chain-z"


def test_task_fallback_does_not_touch_unrelated():
    reg = PendingProposalRegistry()
    reg.register(_prop("p-a", context_ref={"kind": "task", "id": "t1"}))
    reg.register(_prop("p-b", context_ref={"kind": "task", "id": "OTHER"}))
    reg.register(_prop("p-c"))                              # 无 context_ref
    reg.register(_prop("p-e", context_ref={"kind": "conversation", "id": "t1"}))  # 非 task
    assert reg.get("p-b").chain_id == ""
    assert reg.get("p-c").chain_id == ""
    assert reg.get("p-e").chain_id == ""
    # handler 已透传链的卡:兜底不覆盖
    reg.register(_prop("p-f", context_ref={"kind": "task", "id": "t1"}, chain_id="keep-me"))
    assert reg.get("p-f").chain_id == "keep-me"


# ---- ④ chain_intent:链根摘要 + 截断 + 持久化 + 清理 ----

def test_chain_intent_takes_route_requirement_and_truncates():
    reg = PendingProposalRegistry()
    long_req = "帮我调研三只消费类基金,横向对比费率与近三年回撤,再按我的风险偏好给出建议和加仓节奏" * 2
    a = proposal_for_route(domain_id="d1", role="分析师", agent_id="ag", domain_name="基金",
                           requirement=long_req, ts=1.0)
    reg.register(a)
    reg.register(with_chain(_prop("p-d", summary="确认结果", ts=2.0), a.proposal_id))
    intent = reg.chain_intent(a.proposal_id)
    assert intent and intent in (long_req[:CHAIN_INTENT_MAX - 1] + "…")
    assert len(intent) <= CHAIN_INTENT_MAX
    # 链源意图 = 链上最早那张(ts 更早者胜):后注册但 ts 更早的卡应接管
    reg.register(with_chain(_prop("p-e", summary="更早的根摘要", ts=0.5), a.proposal_id))
    assert reg.chain_intent(a.proposal_id) == "更早的根摘要"


def test_chain_intent_survives_restart_and_root_removal(tmp_path: Path):
    persist = tmp_path / "pending.json"
    reg = PendingProposalRegistry(persist_path=persist)
    a = _prop("p-root", summary="链源意图原话", ts=1.0)
    reg.register(a)
    reg.register(with_chain(_prop("p-d", summary="派生卡", ts=2.0), "p-root"))
    # 落盘含 chains(docs/92 刀1 v3)且卡带 chain_id
    data = json.loads(persist.read_text(encoding="utf-8"))
    assert data["version"] == 3 and "p-root" in (data.get("chains") or {})
    # 重启还原:chain_id + 链意图都在
    reg2 = PendingProposalRegistry(persist_path=persist)
    assert reg2.get("p-d").chain_id == "p-root"
    assert reg2.chain_intent("p-root") == "链源意图原话"
    # 链根先被拍掉 → 意图仍在(派生卡还要靠它显示组头);全链清空 → 意图随之清
    reg2.remove("p-root")
    assert reg2.chain_intent("p-root") == "链源意图原话"
    reg2.remove("p-d")
    assert reg2.chain_intent("p-root") == ""
    # 清理也落盘(重启后不带僵尸链)
    reg3 = PendingProposalRegistry(persist_path=persist)
    assert reg3.chain_intent("p-root") == ""


# ---- ② handler 派生透传(route_to_role 的两个注册点) ----

class _FakeVerdict:
    inconclusive = True
    passed = False
    feedback = ""


class _FakeResult:
    def __init__(self, text="done", error=""):
        self.text = text
        self.error = error


class _FakeChecked:
    def __init__(self):
        self.result = _FakeResult()
        self.verdict = _FakeVerdict()
        self.rounds = 0


class _FakeOutcome:
    def __init__(self, *, infeasible=False, attempts=None):
        self.checked = _FakeChecked()
        self.infeasible = infeasible
        self.infra_dead = False
        self.attempts = list(attempts or [])


def _route_app(preg, *, areg=None):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        main_loop=types.SimpleNamespace(),
        runtime_kwargs={"gateway": None, "workspace_root": "/tmp"},
        proposal_registry=preg,
        atom_registry=areg,
        role_registry=None,
        domain_registry=None,
        memory=None,
    ))


def _run_route_handler(monkeypatch, app, proposal, *, outcome, minted=None):
    """跑真 _route_to_role_handler,只桩掉 LLM 执行内核(pursue/forge)。"""
    import karvyloop.cli.pursuit_loop as pl
    import karvyloop.runtime.main_loop as ml_mod
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import KIND_ROUTE_TO_ROLE as _K

    def _fake_forge(**kw):
        for m in (minted or []):
            (kw.get("self_create_minted") if kw.get("self_create_minted") is not None else []).append(m)
        return "sb"

    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory", _fake_forge)
    monkeypatch.setattr(pl, "pursue", lambda *a, **kw: outcome)
    handlers = build_proposal_handlers(app)
    return handlers[_K](proposal)


def test_route_handler_passes_chain_to_infeasible_card(monkeypatch):
    """A 卡无 chain_id → 派生的不可行报告卡 chain_id = A.proposal_id(A 当链根)。"""
    preg = PendingProposalRegistry()
    app = _route_app(preg)
    a = proposal_for_route(domain_id="d1", role="设计师", agent_id="设计师",
                           domain_name="设计", requirement="出一版海报", ts=1.0)
    preg.register(a)   # 真实流:A 卡在 broadcast 咽喉先进待决表,ACCEPT 兑现完才 remove
    ok, detail = _run_route_handler(
        monkeypatch, app, a,
        outcome=_FakeOutcome(infeasible=True,
                             attempts=[{"attempt": 1, "terminal": "budget", "note": ""}]))
    assert ok
    cards = [p for p in preg.pending() if p.kind == KIND_INFEASIBLE_REPORT]
    assert len(cards) == 1 and cards[0].chain_id == a.proposal_id
    # 链源意图可查(route 卡的 requirement 直引)—— 前端组头用
    assert preg.chain_intent(a.proposal_id) == "出一版海报"


def test_route_handler_passes_chain_to_confirm_result_card(monkeypatch):
    """A 卡已带 chain_id(上游透传)→ 派生的结果确认卡继承同一条链(不是 A 的 pid)。"""
    from karvyloop.atoms.registry import AtomRegistry
    areg = AtomRegistry()
    areg.create("zh_en", "task", "把中文翻成英文", provisional=True, origin="self_created")
    preg = PendingProposalRegistry()
    app = _route_app(preg, areg=areg)
    a = with_chain(proposal_for_route(domain_id="d1", role="译者", agent_id="译者",
                                      domain_name="翻译", requirement="翻译这篇文档", ts=1.0),
                   "chain-upstream")
    ok, detail = _run_route_handler(monkeypatch, app, a,
                                    outcome=_FakeOutcome(), minted=["zh_en"])
    assert ok
    cards = [p for p in preg.pending() if p.kind == KIND_CONFIRM_RESULT]
    assert len(cards) == 1 and cards[0].chain_id == "chain-upstream"


# ---- ⑤ 出口口径:chain_intent + high_risk ----

def test_wire_payload_attaches_chain_intent_and_high_risk():
    from karvyloop.console.proposals import proposal_wire_payload
    reg = PendingProposalRegistry()
    a = proposal_for_route(domain_id="d1", role="r", agent_id="r", domain_name="n",
                           requirement="办一件事", ts=1.0)
    reg.register(a)
    derived = with_chain(_prop("p-d", kind="confirm_result", summary="确认", ts=2.0),
                         a.proposal_id)
    reg.register(derived)
    d = proposal_wire_payload(reg, reg.get("p-d"))
    assert d["chain_id"] == a.proposal_id
    assert d["chain_intent"] == "办一件事"
    assert d["high_risk"] is True                    # confirm_result ∈ HIGH_RISK_KINDS
    d2 = proposal_wire_payload(reg, a)
    assert d2["high_risk"] is False                  # route_to_role 不在高风险表
    assert d2["chain_intent"] == "办一件事"           # 链根卡自己也带(组头可用)
    # registry 缺失 → 只少 chain_intent,不炸不丢卡(fail-soft)
    d3 = proposal_wire_payload(None, a)
    assert "chain_intent" not in d3 and d3["proposal_id"] == a.proposal_id


def test_broadcast_uses_registry_stored_proposal_with_chain():
    """broadcast 咽喉:register 兜底补链后,推给前端的 payload 必须是**登记后的那份**。"""
    import asyncio
    from karvyloop.console.proposals import broadcast_proposal

    sent = []

    class _WS:
        async def send_json(self, msg):
            sent.append(msg)

    reg = PendingProposalRegistry()
    reg.register(_prop("p-a", context_ref={"kind": "task", "id": "t1"}, ts=1.0))
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        proposal_registry=reg, ws_clients={_WS()}, runtime_kwargs={},
        taste_predictions=None, decision_log=None, main_loop=None))
    b = _prop("p-b", context_ref={"kind": "task", "id": "t1"}, ts=2.0)
    n = asyncio.run(broadcast_proposal(app, b, allow_silence=False))
    assert n == 1
    payload = sent[0]["payload"]
    assert payload["chain_id"] == "p-a"              # 兜底补的链上了 wire
    assert payload["chain_intent"]                   # 组头意图同行
