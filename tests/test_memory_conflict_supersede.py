"""test_memory_conflict_supersede — 记忆冲突消解接线 + 时效(模块雷达 A 记忆组)。

不变量:
① 写入矛盾 → 旧条被打 invalid_at、新条生效、召回不再返旧(真 MemoryManager 路径,LLM 层 stub)
② provenance_rank 真接上:user_explicit(人明说)vs distill(蒸馏猜)矛盾时,人明说的赢——
   哪怕蒸馏条更新;低权威新条反被打失效
③ 失效**不物理删**:invalid 条仍在库(recent/index 查得到,invalid_reason 可读)= 可审计/可翻案
④ 召回默认过滤 invalid;include_invalid=True 才带上
⑤ LLM 判矛盾输出垃圾 → 宁空勿毒:原库一条不动
⑥ 使用信号:recall_block 命中刷 last_recalled_ts/recall_count(不写盘),flush_usage 批量落
⑦ auto_distill 质量门:蒸馏材料剔除 <memory-context> 召回块复述;auto 蒸的标 provisional
⑧ daily knowledge_tick:一年没用 → 升"归档?"H2A 卡;ACCEPT=失效不删
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

from karvyloop.cognition import ingest as I  # noqa: E402
from karvyloop.cognition.belief_store import BeliefStore  # noqa: E402
from karvyloop.cognition.conflict import (  # noqa: E402
    find_supersede_candidates,
    parse_supersede_pairs,
    provenance_rank,
    run_supersede_pass,
)
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


# ---- 桩:LLM 层 stub,memory 层走真写入 ----

class TextDelta:
    def __init__(self, t):
        self.text = t


class ScriptedGW:
    """按 system prompt 路由回复:摄入编译器一份、supersede 审查器一份。记录调用供断言。"""

    def __init__(self, *, ingest_reply="[]", supersede_reply='{"pairs":[]}'):
        self.ingest_reply = ingest_reply
        self.supersede_reply = supersede_reply
        self.calls = []   # [(kind, material)]

    async def complete(self, messages, tools, model_ref, *, system=None):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        material = messages[0]["content"] if messages else ""
        if "一致性审查" in sys_text:
            self.calls.append(("supersede", material))
            yield TextDelta(self.supersede_reply)
        else:
            self.calls.append(("ingest", material))
            yield TextDelta(self.ingest_reply)


def belief(content, *, source="ingest", ts=1000.0, provisional=False, scope="personal"):
    prov = {"source": source, "agent": "test", "ts": ts, "trace_ref": ""}
    if provisional:
        prov["provisional"] = True
    return Belief(content=content, provenance=prov, freshness_ts=ts, scope=scope)


# ============ ② provenance_rank:真实 source 别名 + provisional 封顶 ============

def test_provenance_rank_maps_real_sources():
    assert provenance_rank({"source": "ingest"}) == 100        # 用户显式喂料
    assert provenance_rank({"source": "knowledge"}) == 100
    assert provenance_rank({"source": "conversation"}) == 40   # 对话蒸馏(猜的)
    assert provenance_rank({"source": "consolidated"}) == 80   # 人 ACCEPT 过的合并条
    assert provenance_rank({"source": "user_explicit"}) == 100  # 抽象档位名照旧
    # provisional(auto 蒸没过人审)→ 封顶蒸馏档,哪怕 source 写得再高
    assert provenance_rank({"source": "ingest", "provisional": True}) == 40


# ============ 解析:宁空勿毒 ============

def test_parse_supersede_pairs_strict():
    ok = parse_supersede_pairs('{"pairs":[{"new":0,"old":1,"relation":"contradict"}]}', 1, 2)
    assert ok == [{"new": 0, "old": 1, "relation": "contradict"}]
    # fence 剥外层
    assert parse_supersede_pairs('```json\n{"pairs":[{"new":0,"old":0,"relation":"update"}]}\n```', 1, 1)
    # 垃圾/prose/坏 JSON/形状不对 → []
    assert parse_supersede_pairs("我觉得第 1 条和第 2 条矛盾。", 2, 2) == []
    assert parse_supersede_pairs('{"pairs": "not a list"}', 1, 1) == []
    assert parse_supersede_pairs('{"pairs":[{"new":0,"old":9,"relation":"contradict"}]}', 1, 2) == []  # 越界丢
    assert parse_supersede_pairs('{"pairs":[{"new":0,"old":0,"relation":"unrelated"}]}', 1, 1) == []   # 无关不列
    assert parse_supersede_pairs("", 1, 1) == []


def test_find_candidates_overlap_no_vectors():
    olds = [belief("用户是素食主义者,平时吃素"), belief("用户住在杭州"), belief("Rust 是系统语言")]
    idx = find_supersede_candidates("用户现在开始吃肉了,不再吃素", olds)
    assert idx and idx[0] == 0            # 词面最像的旧条排第一
    # 与三条中文旧信念零词面/bigram 重叠的英文查询 → 无候选(零 LLM),不误判相似
    assert find_supersede_candidates("全是英文 tokens nothing matches", olds) == []
    # 零命中 → [](零 LLM)
    assert find_supersede_candidates("kubernetes operator", [belief("完全无关的一条")]) == []


# ============ ①③④ 写入矛盾 → 旧失效不删 + 召回过滤(真 MemoryManager) ============

def test_supersede_invalidates_old_keeps_audit_and_recall_filters(tmp_path):
    store = BeliefStore(tmp_path / "beliefs.json")
    mem = MemoryManager(store=store)
    mem.write(belief("用户是素食主义者,平时吃素", source="ingest", ts=1000.0))
    gw = ScriptedGW(
        ingest_reply='[{"content":"用户现在吃肉了,不再吃素","kind":"fact"}]',
        supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}',
    )
    res = asyncio.run(I.ingest_material("我最近开始吃肉了", gateway=gw, mem=mem,
                                        model_ref="m", now=2000.0))
    assert res.written == 1
    assert [k for k, _ in gw.calls] == ["ingest", "supersede"]   # 一次编译 + 一次审查
    old = mem.index.get("用户是素食主义者,平时吃素")
    new = mem.index.get("用户现在吃肉了,不再吃素")
    # 旧条:失效但**没被物理删**(仍在 index,invalid_reason 可读 = 可审计)
    assert old is not None and old.invalid_at == 2000.0
    assert "superseded(update)" in old.invalid_reason
    assert new is not None and new.invalid_at is None
    # 召回默认不返旧条,返新条
    block = mem.recall_block("用户 吃素 吃肉 饮食", limit=8)
    assert "吃肉" in block and "素食主义者" not in block
    # include_invalid=True 审计面还召得到
    block_all = mem.recall_block("用户 吃素 吃肉 饮食", limit=8, include_invalid=True)
    assert "素食主义者" in block_all
    # recent(管理面)也查得到失效条
    assert any(b.content == old.content for b in mem.recent(limit=10))
    # 落盘 round-trip:invalid 状态重启不丢
    mem2 = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
    old2 = mem2.index.get("用户是素食主义者,平时吃素")
    assert old2 is not None and old2.invalid_at == 2000.0 and "superseded" in old2.invalid_reason
    assert "素食主义者" not in mem2.recall_block("用户 吃素 饮食", limit=8)


# ============ ② user_explicit 压过 distill:低权威新条掀不翻高权威旧条 ============

def test_provenance_gate_distill_cannot_topple_user_explicit():
    mem = MemoryManager()
    explicit = belief("用户对花生过敏", source="ingest", ts=1000.0)          # 人明说 rank=100
    mem.write(explicit)
    guessed = belief("用户不对任何食物过敏", source="conversation", ts=5000.0, provisional=True)
    mem.write(guessed)                                                        # 蒸馏猜的 rank=40,更新
    gw = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"contradict"}]}')
    out = asyncio.run(run_supersede_pass([guessed], mem=mem, gateway=gw, now=5000.0))
    # 人明说的站住;**蒸馏新条反被打失效**(留库可审计)
    assert explicit.invalid_at is None
    assert guessed.invalid_at == 5000.0 and "lower provenance" in guessed.invalid_reason
    assert out["invalidated_new"] == 1 and out["invalidated_old"] == 0
    # 反方向:人明说的新条 vs 蒸馏旧条 → 旧条失效
    mem2 = MemoryManager()
    old_guess = belief("用户喜欢咖啡", source="conversation", ts=1000.0, provisional=True)
    mem2.write(old_guess)
    new_explicit = belief("用户不喝咖啡,只喝茶", source="ingest", ts=2000.0)
    mem2.write(new_explicit)
    gw2 = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"contradict"}]}')
    asyncio.run(run_supersede_pass([new_explicit], mem=mem2, gateway=gw2, now=2000.0))
    assert old_guess.invalid_at == 2000.0 and new_explicit.invalid_at is None


# ============ ⑤ 宁空勿毒:审查器输出垃圾 → 原库不动 ============

def test_garbage_judge_output_leaves_library_untouched():
    mem = MemoryManager()
    old = belief("用户吃素", source="conversation", ts=1000.0)
    mem.write(old)
    new = belief("用户吃肉", source="ingest", ts=2000.0)
    mem.write(new)
    for garbage in ("咳咳,我认为它们矛盾", '{"pairs":[{bad', '{"pairs":[{"new":0,"old":99,"relation":"contradict"}]}'):
        gw = ScriptedGW(supersede_reply=garbage)
        out = asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=2000.0))
        assert out["invalidated_old"] == 0 and out["invalidated_new"] == 0
        assert old.invalid_at is None and new.invalid_at is None   # 一条不动


def test_no_candidates_means_zero_llm():
    mem = MemoryManager()
    mem.write(belief("kubernetes operator pattern", source="ingest", ts=1000.0))
    new = belief("用户喜欢猫", source="ingest", ts=2000.0)
    mem.write(new)
    gw = ScriptedGW()
    asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=2000.0))
    assert gw.calls == []   # 一个字面都不搭 → 不烧 LLM


# ============ ⑥ 使用信号:召回刷 + 批量落盘 ============

def test_recall_refreshes_usage_and_flush_persists(tmp_path):
    store = BeliefStore(tmp_path / "b.json")
    mem = MemoryManager(store=store)
    b = belief("用户喜欢 Python 脚本", source="ingest", ts=1000.0)
    mem.write(b)
    assert b.recall_count == 0 and b.last_recalled_ts == 0.0
    t0 = time.time()
    assert "Python" in mem.recall_block("python 脚本")
    assert b.recall_count == 1 and b.last_recalled_ts >= t0
    mem.recall_block("python")
    assert b.recall_count == 2
    # 热路径没落盘(store 里还是 0)→ flush_usage 批量落
    raw = [(bb, p) for bb, p in BeliefStore(tmp_path / "b.json").load_all()]
    assert raw and raw[0][0].recall_count == 0
    assert mem.flush_usage() is True
    raw2 = BeliefStore(tmp_path / "b.json").load_all()
    assert raw2[0][0].recall_count == 2 and raw2[0][0].last_recalled_ts >= t0


def test_invalid_filtered_from_low_level_recall_too():
    from karvyloop.cognition.recall import recall
    mem = MemoryManager()
    b1 = belief("vegetarian 用户吃素", source="conversation", ts=1000.0)
    mem.write(b1)
    mem.invalidate(b1, reason="superseded", now=2000.0)
    hits = recall("vegetarian", mem.index)
    assert hits == []                                        # 默认过滤
    hits_all = recall("vegetarian", mem.index, include_invalid=True)
    assert len(hits_all) == 1                                # 审计面可带上
    # prefetch_all(async provider 缝)同规则
    ctx = asyncio.run(mem.prefetch_all("vegetarian"))
    assert ctx.beliefs == []


# ============ ⑦ auto_distill 质量门:召回块复述剔除 + provisional ============

def test_format_turns_strips_recall_fence_echo():
    from karvyloop.cognition.auto_distill import format_turns, strip_recall_echo
    echo = ("好的。<memory-context>\n用户是素食主义者\n</memory-context>\n"
            "（以上是召回的记忆背景，非新用户输入）我记得你吃素")
    turn = SimpleNamespace(user_intent="随便聊聊", agent_response=echo)
    material = format_turns([turn])
    assert "素食主义者" not in material          # 已召回记忆不再进蒸馏材料(防自反馈)
    assert "memory-context" not in material
    assert "我记得你吃素" in material            # 真回复保留
    assert strip_recall_echo("干净文本") == "干净文本"


@pytest.mark.asyncio
async def test_auto_distill_marks_provisional():
    from karvyloop.cognition.auto_distill import distill_turns
    mem = MemoryManager()
    gw = ScriptedGW(ingest_reply='[{"content":"用户在做 KarvyLoop","kind":"fact"}]')
    res = await distill_turns([SimpleNamespace(user_intent="我在做 KarvyLoop", agent_response="收到")],
                              gateway=gw, mem=mem, now=1000.0)
    assert res.written == 1
    b = mem.index.get("用户在做 KarvyLoop")
    assert b is not None and b.provenance.get("provisional") is True
    assert provenance_rank(b.provenance) == 40   # 不与人审沉淀同权


@pytest.mark.asyncio
async def test_distill_with_decisions_marks_provisional_and_superedes():
    from karvyloop.cognition.auto_distill import distill_turns_with_decisions

    class GW(ScriptedGW):
        async def complete(self, messages, tools, model_ref, *, system=None):
            sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
            if "一致性审查" in sys_text:
                self.calls.append(("supersede", ""))
                yield TextDelta('{"pairs":[]}')
            else:
                self.calls.append(("combined", ""))
                yield TextDelta('{"facts":[{"content":"用户偏好深色主题","kind":"preference"}],"decisions":[]}')

        def resolve_model(self, scope):
            return "m"

    mem = MemoryManager()
    gw = GW()
    res, decisions = await distill_turns_with_decisions(
        [SimpleNamespace(user_intent="我喜欢深色主题", agent_response="好")],
        gateway=gw, mem=mem, now=1000.0)
    assert res.written == 1 and decisions == []
    b = mem.index.get("用户偏好深色主题")
    assert b is not None and b.provenance.get("provisional") is True


# ============ ⑧ knowledge_tick:一年没用 → 归档卡;ACCEPT=失效不删 ============

def test_stale_belief_raises_h2a_card_and_accept_invalidates(tmp_path, monkeypatch):
    from karvyloop.console.knowledge_tick import (
        KIND_ARCHIVE_STALE, MIN_BELIEFS, knowledge_consolidate_tick,
    )
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    now = 1_000_000_000.0
    mem = MemoryManager()
    stale = belief("三年前的旧项目用的是 SVN", source="ingest", ts=now - 400 * 86400)
    mem.write(stale)
    for i in range(MIN_BELIEFS):
        mem.write(belief(f"新鲜知识 {i}", source="ingest", ts=now - 3600))

    async def fake_suggest(beliefs, *, gateway, model_ref=""):
        return []
    import karvyloop.cognition.consolidate as cons
    monkeypatch.setattr(cons, "suggest_consolidation", fake_suggest)

    handlers: dict = {}
    app = SimpleNamespace(state=SimpleNamespace(
        memory=mem, runtime_kwargs={"gateway": object(), "model_ref": "m"},
        proposal_registry=PendingProposalRegistry(), proposal_handlers=handlers,
        ws_clients=set(),
    ))
    r = asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "t.json", now=now))
    assert r["stale_suggested"] == 1
    pend = [p for p in app.state.proposal_registry.pending() if p.kind == KIND_ARCHIVE_STALE]
    assert len(pend) == 1
    card = pend[0]
    assert "三年前的旧项目用的是 SVN" in card.payload["member_contents"]
    assert KIND_ARCHIVE_STALE in handlers            # handler 已注入,ACCEPT 不会空转
    # 冷却:同一批候选次日不重复升卡
    r2 = asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "t.json", now=now + 86400))
    assert r2["stale_suggested"] == 0
    # ACCEPT → 失效不删(库里还在,召回不返)
    res = app.state.proposal_registry.decide(card.proposal_id, "ACCEPT", handlers=handlers)
    assert res.ok is True
    assert stale.invalid_at is not None and "stale-archived" in stale.invalid_reason
    assert mem.index.get(stale.content) is not None       # 没物理删
    assert "SVN" not in mem.recall_block("旧项目 SVN")     # 召回不返


def test_fresh_and_pinned_not_flagged_stale():
    from karvyloop.console.knowledge_tick import _stale_candidates
    now = 1_000_000_000.0
    mem = MemoryManager()
    fresh = belief("上周的新知识", source="ingest", ts=now - 7 * 86400)
    pinned = belief("永远要记得的生日", source="ingest", ts=now - 400 * 86400)
    recently_recalled = belief("很老但最近召回过", source="ingest", ts=now - 400 * 86400)
    recently_recalled.last_recalled_ts = now - 86400
    old = belief("真过时", source="ingest", ts=now - 400 * 86400)
    mem.write(fresh); mem.write(pinned, pinned=True)
    mem.write(recently_recalled); mem.write(old)
    cands = _stale_candidates([fresh, pinned, recently_recalled, old], mem, now)
    assert [b.content for b in cands] == ["真过时"]
