"""test_distill_workflow — ch4 #2:认知库沉淀工作流(喂料→分析→交流→你拍板沉淀/拒绝)。

Hardy 工作流:一次一条、持久化(重启续)、不结束不开下一条。本测锁:
- store:open/current/append/close round-trip + 跨实例持久化
- AC1: feed → 进"待沟通"态(有结构化总结);GET /memory/distill 拿得到
- AC2: 一次一条 —— 已有待办时再 feed 被拒
- AC3: decide reject → 清掉,可开下一条
- AC4: decide persist → 编译进 Belief(复用 ingest)+ 清掉
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.distill_session import DistillSessionStore  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


# ---- store 单元 ----
def test_store_roundtrip_and_persist(tmp_path):
    p = tmp_path / "pd.json"
    st = DistillSessionStore(p, clock=lambda: 1.0)
    assert st.current() is None
    s = st.open(material="m", fetched="f", summary="sum", source_url="http://x")
    assert s["id"] and s["phase"] == "awaiting"
    st.append_turn(who="you", text="问一句")
    # 跨实例(模拟重启)仍在 → "下次打开继续聊"
    st2 = DistillSessionStore(p)
    cur = st2.current()
    assert cur is not None and cur["summary"] == "sum"
    assert cur["transcript"][0] == {"who": "you", "text": "问一句"}
    st2.close()
    assert DistillSessionStore(p).current() is None   # 结束 → 可开下一条


class _FakeMem:
    def __init__(self):
        self.written = []
    def recall_block(self, q, *, scope="personal", limit=8):
        return ""
    def write(self, b, **k):
        self.written.append(b)


@pytest.fixture
def app(tmp_path):
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a.state.memory = _FakeMem()
    a.state.runtime_kwargs = {"gateway": object(), "model_ref": "x"}   # 假 gw → 分析走 fallback
    a.state.distill_store = DistillSessionStore(tmp_path / "pd.json")
    return a


# ---- AC1: feed → 待沟通态 + 可查 ----
def test_feed_creates_pending(app):
    c = TestClient(app)
    r = c.post("/api/memory/feed", json={"material": "记住我爱用 Python"}).json()
    assert r["ok"] is True
    assert r["session"]["phase"] == "awaiting" and r["session"]["summary"]   # 有结构化总结(fallback 也算)
    got = c.get("/api/memory/distill").json()
    assert got["pending"] and got["pending"]["id"] == r["session"]["id"]     # 下次打开拿得到


# ---- AC2: 一次一条 ----
def test_feed_blocks_second(app):
    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "第一条"})
    r2 = c.post("/api/memory/feed", json={"material": "第二条"}).json()
    assert r2["ok"] is False and "没结束" in r2["reason"]   # 不结束不开下一条


# ---- AC3: reject → 清掉,可开下一条 ----
def test_decide_reject_clears(app):
    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "一条料"})
    r = c.post("/api/memory/distill/decide", json={"decision": "reject"}).json()
    assert r["ok"] is True and r["decision"] == "reject"
    assert c.get("/api/memory/distill").json()["pending"] is None
    assert c.post("/api/memory/feed", json={"material": "下一条"}).json()["ok"] is True   # 能开下一条


# ---- AC4: persist → 沉淀(编译 Belief)+ 清掉 ----
def test_decide_persist_distills(app, monkeypatch):
    import karvyloop.console.routes as routes_mod
    from karvyloop.cognition.ingest import IngestResult

    async def fake_ingest(material, **kw):
        return IngestResult(written=2, beliefs=[], raw="ok")
    monkeypatch.setattr(routes_mod, "ingest_material", fake_ingest, raising=False)
    # routes 里是 `from ...ingest import ingest_material` 局部导入 → patch 源模块
    import karvyloop.cognition.ingest as ing_mod
    monkeypatch.setattr(ing_mod, "ingest_material", fake_ingest)

    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "要沉淀的料"})
    r = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r["ok"] is True and r["decision"] == "persist" and r["written"] == 2
    assert c.get("/api/memory/distill").json()["pending"] is None   # 沉淀后清掉
