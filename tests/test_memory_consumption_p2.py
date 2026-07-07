"""test_memory_consumption_p2 — 记忆消费侧 P2 三件(docs/69 Q4/Q5/Q6)。

底座全在(invalid_at/invalid_reason/recall_count/last_recalled_ts + recall_block(as_of=)),
本组测的是**接出来的消费面**:

- 件①记忆考古层(Q5):GET /api/memory?include_invalid=1 返回失效条 + 失效原因 + 取代者内容。
- 件②读写审计(Q6 薄版):GET /api/memory 每条带 recall_count / last_recalled_ts
  (Trace 没记 belief 级召回事件 → 退用 memory.py 已有的这两个使用信号字段,不硬造)。
- 件③as_of 时点查询(Q4):GET /api/memory/recall?q=&as_of= → recall_block(as_of=) 结果 +
  标注 as_of。

纪律:复现先行(先红后绿)。纯只读端点(recall 会轻量刷使用信号 = memory.py 既有行为)。
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from karvyloop.cognition.memory import MemoryManager
from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.schemas import Belief


def _b(content: str, ts: float, *, scope: str = "personal", source: str = "ingest",
       valid_from: float | None = None) -> Belief:
    prov = {"source": source, "agent": "karvy", "ts": ts}
    if valid_from is not None:
        prov["valid_from"] = valid_from
    return Belief(content=content, provenance=prov, freshness_ts=ts, scope=scope)


def _client_with(mem: MemoryManager) -> TestClient:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = mem
    return TestClient(app)


# ---- 件① 记忆考古层(Q5:你曾经怎么看我?)----

def test_list_excludes_invalid_by_default():
    """默认 GET /api/memory 不列失效条(考古层折叠,不污染"当前知道的")。"""
    mem = MemoryManager()
    live = _b("我在 A 公司", 100.0)
    dead = _b("我在旧公司", 90.0)
    mem.write(live)
    mem.write(dead)
    mem.invalidate(dead, reason="superseded(update) by newer belief [ingest]: 我在 A 公司")
    lst = _client_with(mem).get("/api/memory").json()["beliefs"]
    contents = {b["content"] for b in lst}
    assert "我在 A 公司" in contents
    assert "我在旧公司" not in contents   # 失效条默认不出现在"当前"


def test_list_include_invalid_returns_archaeology_layer():
    """include_invalid=1 → 失效条带出来,含 invalid_at + invalid_reason + 取代者内容(superseded_by)。"""
    mem = MemoryManager()
    live = _b("我在 A 公司", 100.0)
    dead = _b("我在旧公司", 90.0)
    mem.write(live)
    mem.write(dead)
    now = 12345.0
    mem.invalidate(dead, reason="superseded(update) by newer belief [ingest]: 我在 A 公司", now=now)
    lst = _client_with(mem).get("/api/memory?include_invalid=1").json()["beliefs"]
    by_content = {b["content"]: b for b in lst}
    # 活条:invalid_at 为空
    assert by_content["我在 A 公司"]["invalid_at"] in (None, 0, 0.0)
    # 失效条:带失效时刻 + 原因 + 取代者内容(供面板渲染"✗ 已失效(被『…』取代 · 失效于 X)")
    d = by_content["我在旧公司"]
    assert d["invalid_at"] == now
    assert "superseded" in d["invalid_reason"]
    assert d["superseded_by"] == "我在 A 公司"   # 从 reason 解析出取代者内容


# ---- 件② 读写审计(Q6 薄版:谁在读我的记忆?)----

def test_list_carries_usage_signals():
    """GET /api/memory 每条带 recall_count / last_recalled_ts(使用信号,退用现字段不硬造)。"""
    mem = MemoryManager()
    b = _b("我偏好直接沟通", 100.0)
    mem.write(b)
    # recall_block 命中会轻量刷 recall_count/last_recalled_ts(memory.py 既有行为)
    mem.recall_block("直接沟通", scope="personal", limit=5)
    lst = _client_with(mem).get("/api/memory").json()["beliefs"]
    row = next(b for b in lst if b["content"] == "我偏好直接沟通")
    assert row["recall_count"] >= 1
    assert row["last_recalled_ts"] > 0
    # 从没被召回的条:recall_count=0(诚实,不硬造)
    mem.write(_b("从没被用过", 100.0))
    lst2 = _client_with(mem).get("/api/memory").json()["beliefs"]
    never = next(b for b in lst2 if b["content"] == "从没被用过")
    assert never["recall_count"] == 0


# ---- 件③ as_of 时点查询(Q4:上个月你以为我在哪家公司?)----

def test_recall_as_of_endpoint_filters_by_time_point():
    """GET /api/memory/recall?q=&as_of= → recall_block(as_of=) 的结果 + 标注 as_of。"""
    mem = MemoryManager()
    # T=100 学到"在 A 公司";T=200 换到"在 B 公司",旧条 T=200 失效
    old = _b("我在 A 公司工作", 100.0, valid_from=100.0)
    new = _b("我在 B 公司工作", 200.0, valid_from=200.0)
    mem.write(old)
    mem.write(new)
    mem.invalidate(old, reason="superseded(update) by newer belief [ingest]: 我在 B 公司工作", now=200.0)
    c = _client_with(mem)
    # as_of=150(A 公司时期):应召回 A、不召回 B(B 那时还没成立)
    r = c.get("/api/memory/recall?q=公司&as_of=150").json()
    assert r["ok"] is True
    assert r["as_of"] == 150.0
    assert "我在 A 公司工作" in r["block"]
    assert "我在 B 公司工作" not in r["block"]
    # as_of=250(当下):召回 B、不召回已失效的 A
    r2 = c.get("/api/memory/recall?q=公司&as_of=250").json()
    assert "我在 B 公司工作" in r2["block"]
    assert "我在 A 公司工作" not in r2["block"]


def test_recall_as_of_no_memory_honest():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/memory/recall?q=x&as_of=100").json()
    assert r["ok"] is False


def test_recall_without_as_of_is_current():
    """不给 as_of → 当下召回(失效条不出现),as_of 字段回 null。"""
    mem = MemoryManager()
    live = _b("当前事实", 100.0)
    dead = _b("过时事实", 90.0)
    mem.write(live)
    mem.write(dead)
    mem.invalidate(dead, reason="superseded(update) by newer belief [ingest]: 当前事实")
    r = _client_with(mem).get("/api/memory/recall?q=事实").json()
    assert r["ok"] is True
    assert r["as_of"] is None
    assert "当前事实" in r["block"]
    assert "过时事实" not in r["block"]
