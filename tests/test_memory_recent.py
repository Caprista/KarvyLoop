"""test_memory_recent — P1.5 灵魂后端口③:GET /api/memory/recent(最近沉淀,只读)。

契约(形状冻结):{"items":[{"id","content","ts","source","domain"}]},
按 provenance.ts(缺则 freshness_ts)降序;content 超 300 字截断;纯只读。

AC:
- AC1 排序:provenance.ts 降序(最新沉淀在前);缺 ts 的退 freshness_ts
- AC2 截断:content 不带全文,超 300 字截到 300
- AC3 limit / scope / domain 过滤
- AC4 无 memory(--no-llm)→ {"items":[]},不崩
- AC5 MemoryManager.recent 零副作用(不落盘不改 index)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from karvyloop.cognition.memory import MemoryManager, belief_recency_ts
from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.schemas import Belief


def _b(content: str, ts: float, *, scope: str = "personal", domain: str = "",
       source: str = "ingest", bid: str = "") -> Belief:
    prov = {"source": source, "agent": "karvy", "ts": ts}
    if bid:
        prov["id"] = bid
    if domain:
        prov["applies"] = {"domain": domain}
    return Belief(content=content, provenance=prov, freshness_ts=ts, scope=scope)


def _client_with(mem: MemoryManager) -> TestClient:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = mem
    return TestClient(app)


def test_recent_sorted_desc_by_provenance_ts():
    mem = MemoryManager()
    mem.write(_b("最旧", 100.0, bid="b1"))
    mem.write(_b("最新", 300.0, bid="b3"))
    mem.write(_b("中间", 200.0, bid="b2"))
    items = _client_with(mem).get("/api/memory/recent?limit=20").json()["items"]
    assert [i["content"] for i in items] == ["最新", "中间", "最旧"]
    assert [i["ts"] for i in items] == [300.0, 200.0, 100.0]
    assert items[0]["id"] == "b3" and items[0]["source"] == "ingest"
    assert set(items[0]) == {"id", "content", "ts", "source", "domain"}


def test_recent_missing_provenance_ts_falls_back_to_freshness():
    mem = MemoryManager()
    b = Belief(content="无 ts 的老条", provenance={"source": "manual", "agent": "u"},
               freshness_ts=250.0, scope="personal")
    mem.write(b)
    mem.write(_b("有 ts", 100.0))
    assert belief_recency_ts(b) == 250.0
    items = _client_with(mem).get("/api/memory/recent").json()["items"]
    assert items[0]["content"] == "无 ts 的老条"   # 250 > 100


def test_recent_truncates_content_to_300():
    mem = MemoryManager()
    mem.write(_b("长" * 500, 100.0))
    items = _client_with(mem).get("/api/memory/recent").json()["items"]
    assert len(items[0]["content"]) == 300


def test_recent_limit_scope_domain_filters():
    mem = MemoryManager()
    for i in range(5):
        mem.write(_b(f"个人{i}", 100.0 + i))
    mem.write(_b("A域机密", 999.0, scope="domain", domain="dom-a"))
    mem.write(_b("B域机密", 998.0, scope="domain", domain="dom-b"))
    c = _client_with(mem)
    # limit
    assert len(c.get("/api/memory/recent?limit=3").json()["items"]) == 3
    # scope=personal → 不带域层
    per = c.get("/api/memory/recent?scope=personal").json()["items"]
    assert all(not i["domain"] for i in per) and len(per) == 5
    # scope=domain&domain=dom-a → 只看 A 域(B 不漏过来)
    da = c.get("/api/memory/recent?scope=domain&domain=dom-a").json()["items"]
    assert [i["content"] for i in da] == ["A域机密"]
    assert da[0]["domain"] == "dom-a"
    # 不给 scope → 两层都看,最新在前
    allitems = c.get("/api/memory/recent").json()["items"]
    assert allitems[0]["content"] == "A域机密"


def test_recent_no_memory_honest_empty():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    assert TestClient(app).get("/api/memory/recent").json() == {"items": []}


def test_manager_recent_is_read_only():
    mem = MemoryManager()
    mem.write(_b("一条", 100.0))
    before = len(mem.index.all("personal"))
    got = mem.recent(limit=10)
    assert len(got) == 1 and got[0].content == "一条"
    assert len(mem.index.all("personal")) == before   # 零副作用
    assert mem.persist_error is None
