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


def _belief(content, *, ts, source_ref):
    from karvyloop.schemas.cognition import Belief
    return Belief(content=content, provenance={"source": "fed", "ts": ts, "source_ref": source_ref},
                  freshness_ts=ts, scope="personal")


class _FakeMem:
    """够用的假 mem:支持 write + count_source_ref/purge_source_ref(镜像真实语义,给 Bug1 supersede 测)。"""
    def __init__(self):
        self.written = []
    def recall_block(self, q, *, scope="personal", limit=8, domain=""):
        return ""
    def write(self, b, **k):
        self.written.append(b)
    def count_source_ref(self, sref):
        return sum(1 for b in self.written if (b.provenance or {}).get("source_ref", "") == sref) if sref else 0
    def purge_source_ref(self, sref, *, before_ts=None):
        if not sref:
            return 0
        keep, removed = [], 0
        for b in self.written:
            p = b.provenance or {}
            if p.get("source_ref", "") == sref and (before_ts is None or (p.get("ts") is not None and p.get("ts") < before_ts)):
                removed += 1
            else:
                keep.append(b)
        self.written = keep
        return removed


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


# ---- Bug1:同一资料喂两遍 → supersede 换新版,不叠加重复(Hardy)----
def test_persist_supersedes_same_source(app, monkeypatch):
    """同一 URL 喂两遍:第二次沉淀**替换**第一次的旧 belief(按 source_ref + before_ts),不重复堆积。"""
    from karvyloop.cognition.ingest import IngestResult
    import karvyloop.cognition.ingest as ing_mod
    # P2-② routes 拆分:memory 端点(含 _source_ref)搬到 routes_memory;patch 目标改指新家,
    # 否则 patch routes.py 的 re-export 穿不过生产端点在 routes_memory 里的本地引用(monkeypatch 陷阱)。
    import karvyloop.console.routes_memory as routes_mem_mod

    mem = app.state.memory
    # 第一次:该源写 2 条(带 source_ref + 旧 ts)
    monkeypatch.setattr(routes_mem_mod, "_source_ref", lambda url, mat: "http://x")
    calls = {"n": 0}

    async def fake_ingest(material, *, source_ref="", now=None, **kw):
        calls["n"] += 1
        import time as _t
        ts = now if now is not None else _t.time()
        for i in range(2):
            mem.write(_belief(f"v{calls['n']}-{i}", ts=ts, source_ref=source_ref))
        return IngestResult(written=2, beliefs=[], raw="ok")
    monkeypatch.setattr(ing_mod, "ingest_material", fake_ingest)
    # 让 mem.count_source_ref/purge 真跑(_FakeMem 要支持)—— 用真 MemoryManager
    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "http://x 第一次"})
    r1 = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r1["ok"] and r1["written"] == 2
    n_after_1 = mem.count_source_ref("http://x")
    assert n_after_1 == 2
    # 第二次喂同一源 → feed 报 already_fed=2;persist supersede → 仍是 2 条(换新,不是 4)
    fed = c.post("/api/memory/feed", json={"material": "http://x 第二次"}).json()
    assert fed["already_fed"] == 2
    r2 = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r2["ok"] and r2["written"] == 2 and r2["superseded"] == 2
    assert mem.count_source_ref("http://x") == 2   # 换新版,不是叠成 4


# ---- persist 写 0 绝不静默(Hardy bug:点确认后不进知识库、也没反馈)----
def test_decide_persist_zero_is_loud_not_silent(app, monkeypatch):
    """抽出 0 条(抓取失败/模型输出不可解析/全去重)→ ok=False + 说清原因 + **待办保留**(不悄悄关掉当成功)。"""
    from karvyloop.cognition.ingest import IngestResult
    import karvyloop.cognition.ingest as ing_mod

    async def fake_zero(material, **kw):
        return IngestResult(written=0, beliefs=[], raw="compiled 0 fact(s)")
    monkeypatch.setattr(ing_mod, "ingest_material", fake_zero)

    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "抓不到正文的料"})
    r = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r["ok"] is False and r["written"] == 0        # 不再报成功
    assert "0 条" in r["reason"]                          # 说清楚为什么没进库
    assert c.get("/api/memory/distill").json()["pending"] is not None   # 待办保留 → 可补要点重试


# ---- 边界材料抽取有随机性:首轮 0 → 自动重试一发,别把"再点一次又成功"甩给用户(Hardy)----
def test_persist_retries_once_on_zero(app, monkeypatch):
    from karvyloop.cognition.ingest import IngestResult
    import karvyloop.cognition.ingest as ing_mod
    calls = {"n": 0}

    async def flaky(material, **kw):
        calls["n"] += 1
        return IngestResult(written=(0 if calls["n"] == 1 else 2), beliefs=[], raw="")
    monkeypatch.setattr(ing_mod, "ingest_material", flaky)

    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "边界料(第一抽会是 0)"})
    r = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r["ok"] is True and r["written"] == 2 and calls["n"] == 2   # 首轮 0 → 自动重试成功(不甩锅给用户)
    assert c.get("/api/memory/distill").json()["pending"] is None


# ---- 沉淀前补充的关键点(transcript 里 you 轮)必须进摄入材料,否则"聊两句补充再重试"形同虚设(Hardy)----
def test_persist_includes_chat_notes(app, monkeypatch):
    from karvyloop.cognition.ingest import IngestResult
    import karvyloop.cognition.ingest as ing_mod
    seen = {}

    async def capture(material, **kw):
        seen["material"] = material
        return IngestResult(written=1, beliefs=[], raw="")
    monkeypatch.setattr(ing_mod, "ingest_material", capture)

    c = TestClient(app)
    c.post("/api/memory/feed", json={"material": "一份薄料"})
    app.state.distill_store.append_turn(who="you", text="关键点:X 通过 Y 实现 Z")   # 你补充的关键点
    app.state.distill_store.append_turn(who="karvy", text="明白了")                  # 小卡回应(不该当你补充的点)
    r = c.post("/api/memory/distill/decide", json={"decision": "persist"}).json()
    assert r["ok"] is True
    assert "X 通过 Y 实现 Z" in seen["material"] and "你补充的关键点" in seen["material"]   # 补充点进了材料
    assert "明白了" not in seen["material"]   # 只收你补充的、不收小卡的客套
