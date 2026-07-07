"""test_recall_explain — Q1 召回解释:回答垫了哪几条记忆、每条为什么被想起。

复现先行:此前召回完全不可见 —— drive_done payload 里没有任何召回信息(本文件的
契约测试在实现前必红)。设计:
- cognition 层:`spreading_activation_recall` / `recall_block` 加**可选** `explain_sink`,
  给了就往里 append 每条入选的解释(命中词面 / 语义标签交集 / 是否图谱扩散 + 跳数 + 分);
  不给 = 行为一字不变(现有全部调用零回归)。
- 契约层:REST /api/intent 与 WS intent 的 drive_done payload 带 `recall_used`
  (空列表 = 没垫记忆);每条形状 {content_preview(≤80 字,不塞全文), provenance_ts,
  source, belief_key, surface_terms, concept_tags, via_spread, hops, score}。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.concepts import ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.cognition.spread import spreading_activation_recall  # noqa: E402
from karvyloop.context.relevance import overlap_score  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

_NOW = 1_700_000_000.0

EXPLAIN_KEYS = {"content_preview", "provenance_ts", "source", "belief_key",
                "surface_terms", "concept_tags", "via_spread", "hops", "score"}


def _belief(content: str, ts: float = _NOW, source: str = "distill") -> Belief:
    return Belief(content=content, provenance={"source": source, "ts": ts},
                  freshness_ts=ts, scope="personal")


# ============ cognition 层:spread.explain_sink ============

def test_spread_explain_direct_lexical_hit():
    """词面直接命中:surface_terms 非空、种子 hops=0、via_spread=False、score>0。"""
    beliefs = [_belief("用户偏好深色主题的界面配色"),
               _belief("档案室编号每季度轮换一次")]
    sink: list = []
    ranked = spreading_activation_recall(beliefs, "深色主题怎么配", top_k=8,
                                         explain_sink=sink)
    assert len(ranked) == 1 and "深色主题" in ranked[0].content
    assert len(sink) == 1
    e = sink[0]
    assert e["surface_terms"], "词面命中却没给出命中词"
    assert all(t in "深色主题怎么配" for t in e["surface_terms"])
    assert e["via_spread"] is False
    assert e["hops"] == 0
    assert e["score"] > 0
    assert len(e["surface_terms"]) <= 5


def test_spread_explain_via_spread_marks_hops():
    """靠图谱扩散抬上来的(与 query 零词面交集,但与命中条强关联)→ via_spread=True、hops≥1。"""
    a = "深色主题的界面配色偏好记录"       # 直接命中 query
    b = "界面配色要搭配高对比度字体"       # 与 query 零交集,但与 a 共享「界面配色」词面边
    beliefs = [_belief(a), _belief(b)]
    assert overlap_score("深色主题", b) == 0   # 自证:b 只能靠扩散被想起
    sink: list = []
    ranked = spreading_activation_recall(beliefs, "深色主题", top_k=8, explain_sink=sink)
    contents = [x.content for x in ranked]
    assert a in contents and b in contents
    by_content = {c: e for c, e in zip(contents, sink)}
    assert by_content[a]["via_spread"] is False and by_content[a]["hops"] == 0
    assert by_content[b]["via_spread"] is True and by_content[b]["hops"] >= 1
    assert by_content[b]["surface_terms"] == []   # 零词面交集,别编命中词


def test_spread_explain_concept_tag_hit():
    """同义改写(零词面交集)靠语义标签被想起 → concept_tags 给出命中的标签。"""
    target = "用户偏好深色主题的界面配色"
    beliefs = [_belief(target), _belief("档案室编号每季度轮换一次")]
    assert overlap_score("夜间模式", target) == 0
    tags = [["夜间模式", "界面外观"], []]
    sink: list = []
    ranked = spreading_activation_recall(beliefs, "夜间模式", concepts=tags,
                                         top_k=8, explain_sink=sink)
    assert len(ranked) == 1 and ranked[0].content == target
    e = sink[0]
    assert "夜间模式" in e["concept_tags"]
    assert "界面外观" not in e["concept_tags"]   # 没命中的标签不冒充理由
    assert e["surface_terms"] == []
    assert e["via_spread"] is False and e["hops"] == 0


def test_spread_no_sink_zero_regression():
    """不给 explain_sink → 返回结果与给了 sink 时逐条一致(顺序也一致)。"""
    beliefs = [_belief("深色主题的界面配色偏好记录"),
               _belief("界面配色要搭配高对比度字体"),
               _belief("档案室编号每季度轮换一次")]
    plain = spreading_activation_recall(beliefs, "深色主题", top_k=8)
    sink: list = []
    with_sink = spreading_activation_recall(beliefs, "深色主题", top_k=8,
                                            explain_sink=sink)
    assert [b.content for b in plain] == [b.content for b in with_sink]
    assert len(sink) == len(with_sink)


def test_spread_explain_empty_on_no_hit():
    """无任何命中(返空不投毒)→ sink 也是空,不编解释。"""
    beliefs = [_belief("档案室编号每季度轮换一次")]
    sink: list = []
    assert spreading_activation_recall(beliefs, "夜间模式", top_k=8,
                                       explain_sink=sink) == []
    assert sink == []


# ============ cognition 层:recall_block(explain_sink=) ============

def test_recall_block_explain_sink_shape(tmp_path):
    """recall_block 的 explain:每条带定位/溯源字段,preview 截断 80 字不塞全文。"""
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    long_tail = "细节" * 60
    target = "用户偏好深色主题的界面配色," + long_tail   # > 80 字
    mem.write(_belief(target, source="ingest"))
    mem.write(_belief("档案室编号每季度轮换一次"))
    cc.put(target, ["夜间模式"])
    sink: list = []
    block = mem.recall_block("深色主题的配色", scope="personal", limit=8,
                             explain_sink=sink)
    assert "深色主题" in block
    assert len(sink) == 1
    e = sink[0]
    assert EXPLAIN_KEYS <= set(e.keys())
    assert len(e["content_preview"]) <= 80
    assert e["content_preview"] == target[:80]
    assert e["source"] == "ingest"
    assert e["provenance_ts"] == _NOW
    assert e["belief_key"]                      # 稳定标识非空(定位这条 belief)
    assert e["surface_terms"]                   # 词面命中
    assert isinstance(e["via_spread"], bool) and isinstance(e["hops"], int)


def test_recall_block_default_behavior_unchanged():
    """默认(不传 explain_sink)→ 块内容与传了 sink 时一字不差(零回归)。"""
    mem = MemoryManager()
    mem.write(_belief("用户偏好深色主题的界面配色"))
    plain = mem.recall_block("深色主题", scope="personal", limit=8)
    sink: list = []
    with_sink = mem.recall_block("深色主题", scope="personal", limit=8,
                                 explain_sink=sink)
    assert plain == with_sink
    assert len(sink) == 1


def test_recall_block_explain_empty_when_nothing_recalled():
    mem = MemoryManager()
    mem.write(_belief("档案室编号每季度轮换一次"))
    sink: list = []
    assert mem.recall_block("夜间模式", scope="personal", limit=8,
                            explain_sink=sink) == ""
    assert sink == []


# ============ 契约层:drive_done payload 带 recall_used ============

def _stub_drive(monkeypatch):
    """假 drive_in_tui:REST(routes)与 WS(ws)两个 import 点都打(WS 顶层 import)。"""
    import karvyloop.console.routes as routes_mod
    import karvyloop.console.ws as ws_mod
    from karvyloop.runtime.main_loop import Brain

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="回应:" + intent,
                            skill_name="", fast_brain_hit=False, crystallized=False,
                            task_id="t1")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    monkeypatch.setattr(ws_mod, "drive_in_tui", fake_drive)


def _console_app(tmp_path, mem):
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.memory = mem
    return app


def test_rest_intent_payload_has_recall_used(tmp_path, monkeypatch):
    """【复现靶】REST /api/intent 的响应必须带 recall_used(此前召回完全不可见)。"""
    _stub_drive(monkeypatch)
    mem = MemoryManager()
    mem.write(_belief("用户偏好深色主题的界面配色"))
    client = TestClient(_console_app(tmp_path, mem))

    r = client.post("/api/intent", json={"intent": "深色主题的界面配色怎么选"})
    assert r.status_code == 200
    body = r.json()
    assert "recall_used" in body, "drive_done payload 无召回信息 —— Q1 现状"
    used = body["recall_used"]
    assert isinstance(used, list) and len(used) == 1
    e = used[0]
    assert EXPLAIN_KEYS <= set(e.keys())
    assert e["content_preview"].startswith("用户偏好深色主题")
    assert e["surface_terms"]
    assert e["via_spread"] is False and e["hops"] == 0


def test_rest_intent_recall_used_empty_when_no_memory_hit(tmp_path, monkeypatch):
    """没垫记忆 → recall_used 是空列表(字段仍在,前端好判断,不是缺字段)。"""
    _stub_drive(monkeypatch)
    mem = MemoryManager()
    mem.write(_belief("档案室编号每季度轮换一次"))
    client = TestClient(_console_app(tmp_path, mem))

    r = client.post("/api/intent", json={"intent": "totally unrelated zzqx query"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("recall_used") == []


def test_ws_intent_drive_done_has_recall_used(tmp_path, monkeypatch):
    """WS 路径:drive_done payload 同样带 recall_used(REST/WS 双路径不漂移)。"""
    _stub_drive(monkeypatch)
    mem = MemoryManager()
    mem.write(_belief("用户偏好深色主题的界面配色"))
    client = TestClient(_console_app(tmp_path, mem))

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()   # 首次 snapshot
        ws.send_json({"type": "intent", "payload": {"intent": "深色主题的界面配色怎么选"}})
        for _ in range(20):   # 中间可能插 ambient_recall / task_status 等广播
            msg = ws.receive_json()
            if msg["type"] == "drive_done":
                break
        else:
            pytest.fail("没等到 drive_done")
        payload = msg["payload"]
        assert "recall_used" in payload
        assert len(payload["recall_used"]) == 1
        assert EXPLAIN_KEYS <= set(payload["recall_used"][0].keys())
