"""P0 修复⑤:extends 升卡素材可丢 —— 三条不变量:

① **产生即留痕**:conflict.run_supersede_pass 产出 extends 素材时就落 Trace
  (kind=belief_extends_found,payload 带幂等键+素材摘要)—— 升卡在 console 侧,
  handler 异常/进程崩,素材至少可审计(Trace=唯一数据源院规);
② **幂等键与卡 id 同一派生**:extends_idem_key == merge_knowledge 卡 proposal_id
  (本测试锁两边不漂移);同对待决期间重复出现 → registry 同 id 覆盖(现成去重,只一张卡);
③ **REJECT 记忆**:用户拒过的同对 extends,下次摄入不再弹 —— "已拒"状态住在
  decision_log(H2A 拍板回看流水,已落盘),不新造存储。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.conflict import (  # noqa: E402
    _extends_record,
    extends_idem_key,
    run_supersede_pass,
)
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.cognition.trace import TraceStore  # noqa: E402
from karvyloop.console import routes_memory as RM  # noqa: E402
from karvyloop.console.decision_log import DecisionLog  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_MERGE_KNOWLEDGE,
    PendingProposalRegistry,
    proposal_for_merge_knowledge,
)
from karvyloop.schemas.cognition import Belief  # noqa: E402

T1, T2 = 1000.0, 2000.0


class TextDelta:
    def __init__(self, t):
        self.text = t


class ScriptedGW:
    def __init__(self, *, supersede_reply='{"pairs":[]}'):
        self.supersede_reply = supersede_reply

    async def complete(self, messages, tools, model_ref, *, system=None):
        yield TextDelta(self.supersede_reply)


def _belief(content: str, *, ts: float = T1) -> Belief:
    return Belief(content=content, provenance={"source": "ingest", "agent": "t",
                                               "ts": ts, "trace_ref": ""},
                  freshness_ts=ts, scope="personal")


def _app(*, decision_log=None):
    return SimpleNamespace(state=SimpleNamespace(
        proposal_registry=PendingProposalRegistry(),
        ws_clients=set(),
        decision_log=decision_log,
    ))


# ============ ① 产生即留痕:extends → Trace belief_extends_found ============

def test_extends_material_traced_at_source():
    mem = MemoryManager()
    old = _belief("用户住在北京", ts=T1)
    mem.write(old)
    new = _belief("用户住在北京朝阳区", ts=T2)
    mem.write(new)
    trace = TraceStore()
    gw = ScriptedGW(supersede_reply=(
        '{"pairs":[{"new":0,"old":0,"relation":"extends",'
        '"merged":"用户住在北京朝阳区"}]}'))
    out = asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T2, trace=trace))
    assert len(out["extends"]) == 1
    rec = out["extends"][0]
    assert rec["idem_key"].startswith("merge_knowledge-")     # 素材从产生起就带幂等键
    ents = trace.query("memory_reconcile", kind="belief_extends_found")
    assert len(ents) == 1
    p = ents[0].payload
    assert p["idem_key"] == rec["idem_key"]
    assert "北京" in p["old"] and "朝阳区" in p["new"] and p["merged"]
    # 两边原文都还在库里没被动过(extends 不动库,素材可从库+Trace 恢复)
    assert old.invalid_at is None and new.invalid_at is None


def test_low_confidence_duplicate_downgrade_also_traced():
    # LLM 判 duplicate 但词面佐证不足(只 1 个 bigram 命中 < 2.0)→ 降级 extends,同样要留痕
    mem = MemoryManager()
    old = _belief("喜欢拿铁", ts=T1)
    mem.write(old)
    new = _belief("偏好拿铁风味", ts=T2)
    mem.write(new)
    trace = TraceStore()
    gw = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"duplicate"}]}')
    out = asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T2, trace=trace))
    assert out["auto_merged"] == 0 and len(out["extends"]) == 1   # 没自动动库,降级升卡
    ents = trace.query("memory_reconcile", kind="belief_extends_found")
    assert len(ents) == 1 and ents[0].payload["idem_key"] == out["extends"][0]["idem_key"]


def test_console_handler_crash_does_not_lose_material(monkeypatch):
    """审计原病:_raise_extends 的 handler 抛异常 → 素材无任何持久痕迹。
    修后:痕在产生端(Trace),console 崩不崩都在。"""
    mem = MemoryManager()
    old = _belief("用户住在北京", ts=T1)
    mem.write(old)
    new = _belief("用户住在北京朝阳区", ts=T2)
    mem.write(new)
    trace = TraceStore()
    gw = ScriptedGW(supersede_reply=(
        '{"pairs":[{"new":0,"old":0,"relation":"extends","merged":"合并表述"}]}'))
    out = asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T2, trace=trace))
    res = SimpleNamespace(extends=out["extends"])

    async def boom(app, ext, **kw):
        raise RuntimeError("升卡链路炸了")
    import karvyloop.console.proposals as P
    monkeypatch.setattr(P, "raise_extends_cards", boom)

    app = _app()
    asyncio.run(RM._raise_extends(app, res))            # 不冒泡(摄入回执不受影响)
    assert app.state.proposal_registry.pending() == []  # 卡确实没升上去
    # 但素材痕迹还在 Trace(可审计/可恢复)—— 修前这里是 0 条,素材蒸发
    ents = trace.query("memory_reconcile", kind="belief_extends_found")
    assert len(ents) == 1 and ents[0].payload["old"]


# ============ ② 幂等键锁 + 待决期间同对只一张卡(复用 registry 现成去重) ============

def test_idem_key_matches_merge_knowledge_card_pid():
    old, new = "用户住在北京", "用户住在北京朝阳区"
    card = proposal_for_merge_knowledge(member_contents=[old, new],
                                        merged_content="合并", ts=0.0)
    assert extends_idem_key(old, new) == card.proposal_id
    assert extends_idem_key(new, old) == card.proposal_id   # 顺序无关(sorted)
    # _extends_record 带出的键就是它(升卡后的 proposal_id,一路可追)
    rec = _extends_record(SimpleNamespace(content=new, provenance={}),
                          SimpleNamespace(content=old, provenance={}), "合并")
    assert rec["idem_key"] == card.proposal_id


def test_same_pair_pending_yields_single_card():
    app = _app()
    rec = _extends_record(SimpleNamespace(content="用户住在北京朝阳区", provenance={}),
                          SimpleNamespace(content="用户住在北京", provenance={}), "合并")
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec])))
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec])))   # 同对重复出现
    pend = app.state.proposal_registry.pending()
    assert len(pend) == 1                                   # registry 同 id 覆盖 = 只一张
    assert pend[0].kind == KIND_MERGE_KNOWLEDGE
    assert pend[0].proposal_id == rec["idem_key"]


# ============ ③ REJECT 记忆:拒过的同对不再弹;别的对不受影响 ============

def test_rejected_pair_not_reraised_but_others_are():
    log = DecisionLog()          # 生产里带落盘路径;内存版足够验行为
    app = _app(decision_log=log)
    rec = _extends_record(SimpleNamespace(content="用户住在北京朝阳区", provenance={}),
                          SimpleNamespace(content="用户住在北京", provenance={}), "合并")
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec])))
    reg = app.state.proposal_registry
    assert len(reg.pending()) == 1
    pid = reg.pending()[0].proposal_id

    # 用户拍 REJECT(生产路径:registry.decide 移除 + record_decision_signals→decision_log)
    reg.decide(pid, "REJECT")
    log.record(decision="REJECT", proposal_id=pid, kind=KIND_MERGE_KNOWLEDGE)
    assert reg.pending() == []

    # 同一对再次出现 → 不再弹(修前:卡又回来了)
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec])))
    assert reg.pending() == []

    # 不同的对不受影响,照常升卡
    rec2 = _extends_record(SimpleNamespace(content="偏好深色主题的界面", provenance={}),
                           SimpleNamespace(content="偏好深色主题", provenance={}), "合并2")
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec, rec2])))
    pend = reg.pending()
    assert len(pend) == 1 and pend[0].proposal_id == rec2["idem_key"]


def test_no_decision_log_filter_is_noop():
    # decision_log 未接(--no-llm/裸 console)→ 不过滤,照常升卡(宁多弹勿丢)
    app = _app(decision_log=None)
    rec = _extends_record(SimpleNamespace(content="用户住在北京朝阳区", provenance={}),
                          SimpleNamespace(content="用户住在北京", provenance={}), "合并")
    asyncio.run(RM._raise_extends(app, SimpleNamespace(extends=[rec])))
    assert len(app.state.proposal_registry.pending()) == 1
