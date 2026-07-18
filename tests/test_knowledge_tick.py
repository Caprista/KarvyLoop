"""test_knowledge_tick — 知识库自动整理(daily 慢侧;Bug2 后台版)。

不变量:① 库没变(watermark)→ 零 LLM 跳过(REJECT 不会次日重来)② 库变了 → 聚类升 merge_knowledge
卡(H2A,不自动合)③ 冷却窗内同簇不重复升 ④ 单轮限卡数 ⑤ handler ACCEPT → apply_belief_merge
真合并 ⑥ 知识 < 阈值不跑 ⑦ 坏状态文件 fail-safe。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.knowledge_tick import knowledge_consolidate_tick, MIN_BELIEFS  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_MERGE_KNOWLEDGE, PendingProposalRegistry, proposal_for_merge_knowledge,
)


def _belief(content):
    return SimpleNamespace(content=content, provenance={"source": "fed", "kind": "knowledge"})


class _FakeIndex:
    def __init__(self, beliefs):
        self._b = beliefs
    def all(self, scope):
        return list(self._b)


def _app(beliefs, *, gateway=object()):
    state = SimpleNamespace(
        memory=SimpleNamespace(index=_FakeIndex(beliefs)),
        runtime_kwargs={"gateway": gateway, "model_ref": "m"},
        proposal_registry=PendingProposalRegistry(),
        ws_clients=set(),
    )
    return SimpleNamespace(state=state)


def _patch_suggest(monkeypatch, clusters):
    calls = {"n": 0}
    async def fake(beliefs, *, gateway, model_ref=""):
        calls["n"] += 1
        return clusters
    import karvyloop.cognition.consolidate as mod
    monkeypatch.setattr(mod, "suggest_consolidation", fake)
    return calls


_CLUSTER = {"member_contents": ["loop 是自运转的", "loop 无人参与"],
            "member_titles": ["loop A", "loop B"],
            "merged_title": "loop 工程", "merged_content": "loop 自运转、无人参与", "reason": "同一件事"}


def test_factory_shape_and_stable_pid():
    p1 = proposal_for_merge_knowledge(member_contents=_CLUSTER["member_contents"],
                                      merged_content=_CLUSTER["merged_content"],
                                      merged_title="loop 工程", ts=1.0)
    p2 = proposal_for_merge_knowledge(member_contents=list(reversed(_CLUSTER["member_contents"])),
                                      merged_content="换个合并写法", ts=2.0)
    assert p1.kind == KIND_MERGE_KNOWLEDGE and "loop 工程" in p1.summary
    assert p1.proposal_id == p2.proposal_id          # 同簇(成员集合)→ 稳定 id(幂等)
    with pytest.raises(ValueError):
        proposal_for_merge_knowledge(member_contents=["只有一条"], merged_content="x", ts=1.0)


def test_tick_suggests_then_watermark_skips(tmp_path, monkeypatch):
    beliefs = [_belief(f"知识点{i}") for i in range(MIN_BELIEFS)]
    app = _app(beliefs)
    calls = _patch_suggest(monkeypatch, [_CLUSTER])
    sp = tmp_path / "tick.json"
    r1 = asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=1000.0))
    assert r1["ran"] is True and r1["suggested"] == 1 and calls["n"] == 1
    pend = app.state.proposal_registry.pending()
    assert len(pend) == 1 and pend[0].kind == KIND_MERGE_KNOWLEDGE   # 升了 H2A 卡,没自动合
    # 库没变 → watermark 直接跳过,零 LLM(REJECT 过的不会次日重来)
    r2 = asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=2000.0))
    assert r2["ran"] is False and "watermark" in r2["reason"] and calls["n"] == 1


def test_tick_excludes_invalid_from_candidate_pool(tmp_path, monkeypatch):
    # P1c:失效条不进整理候选池 —— 否则死版+新版一起聚类 → 合并复活失效知识 + consolidate 毁墓碑。
    captured = {}
    async def fake(beliefs, *, gateway, model_ref=""):
        captured["contents"] = [getattr(b, "content", "") for b in beliefs]
        return []
    import karvyloop.cognition.consolidate as mod
    monkeypatch.setattr(mod, "suggest_consolidation", fake)
    beliefs = [_belief(f"活知识{i}") for i in range(MIN_BELIEFS)]
    dead = SimpleNamespace(content="失效的旧知识",
                           provenance={"source": "fed", "kind": "knowledge"}, invalid_at=123.0)
    beliefs.append(dead)
    app = _app(beliefs)
    asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "t.json", now=1000.0))
    assert "contents" in captured
    assert "失效的旧知识" not in captured["contents"]     # 死条被排除
    assert len(captured["contents"]) == MIN_BELIEFS       # 只喂活条


def test_tick_cooldown_no_nag(tmp_path, monkeypatch):
    """库变了、但同一簇冷却窗内建议过 → 不重复升卡。"""
    beliefs = [_belief(f"知识点{i}") for i in range(MIN_BELIEFS)]
    app = _app(beliefs)
    _patch_suggest(monkeypatch, [_CLUSTER])
    sp = tmp_path / "tick.json"
    asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=1000.0))
    app.state.proposal_registry = PendingProposalRegistry()          # 用户 REJECT 了(registry 清了)
    beliefs.append(_belief("新知识"))                                  # 库变了 → watermark 失效
    r = asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=1000.0 + 3600))
    assert r["ran"] is True and r["suggested"] == 0                  # 冷却窗内 → 不唠叨
    assert len(app.state.proposal_registry.pending()) == 0
    # 冷却过了 → 允许再建议
    beliefs.append(_belief("又一条"))
    r2 = asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=1000.0 + 8 * 86400))
    assert r2["suggested"] == 1


def test_tick_caps_cards_per_round(tmp_path, monkeypatch):
    beliefs = [_belief(f"知识点{i}") for i in range(MIN_BELIEFS)]
    clusters = [{**_CLUSTER, "member_contents": [f"a{i}", f"b{i}"]} for i in range(6)]
    app = _app(beliefs)
    _patch_suggest(monkeypatch, clusters)
    r = asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "t.json", now=1.0))
    assert r["suggested"] == 3                                       # MAX_CARDS_PER_TICK 封顶


def test_tick_skips_small_library_and_missing_wiring(tmp_path):
    r = asyncio.run(knowledge_consolidate_tick(_app([_belief("x")]), state_path=tmp_path / "t.json"))
    assert r["ran"] is False and "不值得" in r["reason"]
    app = _app([_belief(f"k{i}") for i in range(MIN_BELIEFS)], gateway=None)
    r2 = asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "t2.json"))
    assert r2["ran"] is False and "未接" in r2["reason"]


def test_broken_state_file_failsafe(tmp_path, monkeypatch):
    sp = tmp_path / "tick.json"
    sp.write_text("{ bad json", encoding="utf-8")
    app = _app([_belief(f"k{i}") for i in range(MIN_BELIEFS)])
    _patch_suggest(monkeypatch, [_CLUSTER])
    r = asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=1.0))
    assert r["ran"] is True and r["suggested"] == 1                  # 坏文件当空,不炸不锁死


def test_cooldown_ledger_evicts_expired(tmp_path, monkeypatch):
    # ③ suggested 台账驱逐早于 冷却窗×N 的过期项(仍在冷却期的保留),防长跑无界(docs/87 §五)。
    import json
    from karvyloop.console.knowledge_tick import COOLDOWN_EVICT_FACTOR, SUGGEST_COOLDOWN_S
    sp = tmp_path / "tick.json"
    now = 5_000_000.0
    old_ts = now - SUGGEST_COOLDOWN_S * (COOLDOWN_EVICT_FACTOR + 1)   # 该清
    fresh_ts = now - SUGGEST_COOLDOWN_S / 2                           # 仍在冷却期 → 该留
    sp.write_text(json.dumps({"lib_hash": "",
                              "suggested": {"old_pid": old_ts, "fresh_pid": fresh_ts}}),
                  encoding="utf-8")
    beliefs = [_belief(f"知识点{i}") for i in range(MIN_BELIEFS)]
    app = _app(beliefs)
    _patch_suggest(monkeypatch, [])   # 无新簇,但库变(lib_hash "" != 真hash)→ 跑并落盘
    asyncio.run(knowledge_consolidate_tick(app, state_path=sp, now=now))
    saved = json.loads(sp.read_text(encoding="utf-8"))
    assert "old_pid" not in saved["suggested"]    # 过期项被驱逐
    assert "fresh_pid" in saved["suggested"]      # 冷却期内的绝不误删


def test_accept_handler_applies_merge():
    """ACCEPT merge_knowledge 卡 → apply_belief_merge 真合并(先写后删)。"""
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    written, removed = [], []
    class _Mem:
        index = _FakeIndex([])
        def write(self, b, **k): written.append(b)
        def count_beliefs_by_content(self, s): return 2
        def remove_by_content(self, s): removed.append(set(s)); return len(s)
    app = SimpleNamespace(state=SimpleNamespace(memory=_Mem()))
    card = proposal_for_merge_knowledge(member_contents=_CLUSTER["member_contents"],
                                        merged_content=_CLUSTER["merged_content"],
                                        merged_title="loop 工程", ts=1.0)
    reg = PendingProposalRegistry()
    reg.register(card)
    res = reg.decide(card.proposal_id, "ACCEPT", handlers=build_proposal_handlers(app))
    assert res.ok is True and "合并" in res.detail
    assert written and written[0].content == _CLUSTER["merged_content"]   # 先写合并条
    assert removed and removed[0] == set(_CLUSTER["member_contents"])     # 再删旧条
    assert len(reg) == 0
