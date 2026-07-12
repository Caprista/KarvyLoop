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


# ---- 记忆主权三件套(2026-07-12 Hardy:账本不翻到人眼前=不存在):pin / 编辑=账本式取代 ----

def test_pin_toggle_reflected_in_listing():
    mem = MemoryManager()
    mem.write(_b("我偏好直接沟通", time.time()))
    c = _client_with(mem)
    assert c.get("/api/memory").json()["beliefs"][0]["pinned"] is False
    r = c.post("/api/memory/pin", json={"content": "我偏好直接沟通", "pinned": True}).json()
    assert r["ok"] is True and r["pinned"] is True
    assert c.get("/api/memory").json()["beliefs"][0]["pinned"] is True     # 列表带出 pin 态
    c.post("/api/memory/pin", json={"content": "我偏好直接沟通", "pinned": False})
    assert c.get("/api/memory").json()["beliefs"][0]["pinned"] is False    # 解锁生效


def test_pin_unknown_content_is_not_found():
    r = _client_with(MemoryManager()).post(
        "/api/memory/pin", json={"content": "没这条", "pinned": True}).json()
    assert r["ok"] is False and r["reason"] == "not_found"


def test_edit_supersedes_old_into_archaeology_layer():
    """编辑=取代不是篡改:新条生效(source=user_edit),旧条进考古层且解析出取代者。"""
    mem = MemoryManager()
    mem.write(_b("我在旧公司", time.time()))
    c = _client_with(mem)
    r = c.post("/api/memory/edit",
               json={"content": "我在旧公司", "new_content": "我在 A 公司"}).json()
    assert r["ok"] is True and r["written"] is True
    live = c.get("/api/memory").json()["beliefs"]
    assert [b["content"] for b in live] == ["我在 A 公司"]                  # 旧条不在默认列表
    assert live[0]["source"] == "user_edit"
    arch = [b for b in c.get("/api/memory?include_invalid=1").json()["beliefs"]
            if b["content"] == "我在旧公司"]
    assert arch and arch[0]["invalid_at"] is not None
    assert arch[0]["superseded_by"] == "我在 A 公司"                        # 考古层"被『…』取代"可解析


def test_edit_carries_pin_and_guards():
    mem = MemoryManager()
    mem.write(_b("吃素", time.time()))
    mem.write(_b("喝茶", time.time()))
    c = _client_with(mem)
    c.post("/api/memory/pin", json={"content": "吃素", "pinned": True})
    c.post("/api/memory/edit", json={"content": "吃素", "new_content": "现在吃肉了"})
    by = {b["content"]: b for b in c.get("/api/memory").json()["beliefs"]}
    assert by["现在吃肉了"]["pinned"] is True                               # pin 态随内容迁移
    # 守卫:改成已存在的内容 → 拒(别静默造重复);没这条 → not_found;原样 → no-op
    assert c.post("/api/memory/edit", json={"content": "现在吃肉了", "new_content": "喝茶"}
                  ).json()["reason"] == "exists"
    assert c.post("/api/memory/edit", json={"content": "没这条", "new_content": "x"}
                  ).json()["reason"] == "not_found"
    assert c.post("/api/memory/edit", json={"content": "喝茶", "new_content": "喝茶"}
                  ).json().get("unchanged") is True

def test_edit_of_invalidated_belief_is_rejected():
    """死条不编辑(对抗验收#2c):防复活矛盾对 + 防覆盖原失效审计痕。"""
    mem = MemoryManager()
    mem.write(_b("OldCo", time.time()))
    c = _client_with(mem)
    c.post("/api/memory/edit", json={"content": "OldCo", "new_content": "NewCo"})
    r = c.post("/api/memory/edit", json={"content": "OldCo", "new_content": "GhostCo"}).json()
    assert r["ok"] is False and r["reason"] == "invalidated"
    live = [b["content"] for b in c.get("/api/memory").json()["beliefs"]]
    assert live == ["NewCo"]                                    # 没有 GhostCo 复活矛盾对
    arch = [b for b in c.get("/api/memory?include_invalid=1").json()["beliefs"]
            if b["content"] == "OldCo"]
    assert arch[0]["superseded_by"] == "NewCo"                  # 原审计痕未被覆盖


def test_edit_emits_mesh_event_despite_inherited_stamp():
    """对抗验收#4:生产路径挂 mesh 发射器时,edit 的新条必须真发事件(旧 provenance 的
    origin_device 戳要剥掉,否则回声抑制误判回放 → 新内容静默不出设备)。"""
    from karvyloop.mesh.cognition_bridge import K_BELIEF, attach_memory_emitter
    from karvyloop.mesh.synclog import MeshLog

    mem = MemoryManager()
    log = MeshLog("dev-a")
    attach_memory_emitter(mem, log)
    mem.write(_b("cat likes fish", time.time()))                # 经钩子:已盖 origin_device
    before = len([e for e in log.entries() if e.kind == K_BELIEF])
    r = _client_with(mem).post("/api/memory/edit",
                               json={"content": "cat likes fish",
                                     "new_content": "cat likes tuna"}).json()
    assert r["ok"] is True
    evs = [e for e in log.entries() if e.kind == K_BELIEF]
    assert len(evs) == before + 1, "编辑的新内容没发 mesh 事件(静默不出设备)"
    assert evs[-1].payload["content"] == "cat likes tuna"