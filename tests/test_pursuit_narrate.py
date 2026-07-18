"""test_pursuit_narrate — 「让小卡讲讲」端点(docs/88 第三刀 #2 ③)。

POST /api/pursuit/{id}/narrate:LLM 叙述「我做了什么/为什么/卡在哪」,点了才烧、产出不落库。
锁三件事(招牌硬核 + 宁空勿毒):
1. gateway 桩 → 出人话 + token_source 打成 "pursuit_narrate"(账本能归因);
2. 无 gateway → 确定性兜底文本、ok True、绝不 500;
3. LLM 返回垃圾(空/纯空白)→ 退确定性兜底(宁空勿毒,不硬塞垃圾)。
另:narrate 组料只碰这条 pursuit 自己的 task(隔离),不落库(纯展示)。
"""
from __future__ import annotations

import pathlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.pursuit_store import (  # noqa: E402
    PursuitRecord, PursuitStore, new_pursuit_id,
)
from karvyloop.console.routes_pursuit import router as pursuit_router  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.llm.token_ledger import current_source  # noqa: E402
from karvyloop.schemas import Pursuit  # noqa: E402


class TextDelta:  # 名字必须叫 TextDelta(代码按 type().__name__ 收)
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeGateway:
    """吐固定 chunk;顺手记下 complete 时的 token_source(验打标)与调用次数。"""
    def __init__(self, chunks: list) -> None:
        self.chunks = chunks
        self.source: str | None = None
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        self.source = current_source()   # 断言:narrate 用 token_source("pursuit_narrate") 裹了这次调用
        for c in self.chunks:
            yield TextDelta(c)


def _app(tmp_path, *, gateway):
    app = FastAPI()
    app.include_router(pursuit_router)
    store = PursuitStore(tmp_path / "pursuits.json")
    reg = TaskRegistry()
    app.state.pursuit_store = store
    app.state.task_registry = reg
    app.state.runtime_kwargs = {"gateway": gateway, "model_ref": ""}
    # 一条 committed pursuit + 两轮派生 task(一轮跑完、一轮又跑完)
    p = Pursuit(id=new_pursuit_id("atom"), level="atom",
                statement="重构解析器直到测试全绿", commitment_condition="",
                revision_triggers=[], verify_gate={"type": "test_pass", "cmd": "pytest tests/test_parser.py"},
                status="committed")
    rec = PursuitRecord(p, title="重构解析器", advances=2, consecutive_failures=0,
                        progress_note="解析器改了一半,还差错误恢复")
    store.put(rec)
    t1 = reg.start(who="karvy", intent="第一轮推进", pursuit_id=p.id)
    reg.finish(t1, result="跑通了一部分用例")
    t2 = reg.start(who="karvy", intent="第二轮推进", pursuit_id=p.id)
    reg.finish(t2, result="又修了几个边界用例")
    return app, store, reg, rec


# ---- 1. gateway 桩 → 人话 + token_source 打标 ----
def test_narrate_with_gateway_returns_human_text_and_tags_source(tmp_path):
    gw = _FakeGateway(["我把解析器重构了两轮,", "现在卡在错误恢复那段。"])
    app, store, reg, rec = _app(tmp_path, gateway=gw)
    client = TestClient(app)
    r = client.post(f"/api/pursuit/{rec.id}/narrate", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["source"] == "llm"
    assert data["narration"] == "我把解析器重构了两轮,现在卡在错误恢复那段。"
    assert gw.calls == 1
    assert gw.source == "pursuit_narrate", f"token_source 应打成 pursuit_narrate,实际 {gw.source}"
    # 产出不落库:pursuit 记录不应因 narrate 长出"叙述历史"字段(纯展示)
    reloaded = PursuitStore(tmp_path / "pursuits.json").get(rec.id)
    assert not hasattr(reloaded, "narration"), "narrate 产出绝不落库(纯展示)"


# ---- 2. 无 gateway → 确定性兜底,不 500 ----
def test_narrate_without_gateway_falls_back_no_500(tmp_path):
    app, store, reg, rec = _app(tmp_path, gateway=None)
    client = TestClient(app)
    r = client.post(f"/api/pursuit/{rec.id}/narrate", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["source"] == "fallback"
    # 确定性兜底:从推进次数派生的人话(zero-LLM),非空、且不含内部词
    assert data["narration"].strip()
    for jargon in ("verify_gate", "H2A", "trace", "TextDelta"):
        assert jargon.lower() not in data["narration"].lower()


# ---- 3. LLM 返回垃圾(空/纯空白)→ 兜底(宁空勿毒)----
def test_narrate_empty_llm_reply_falls_back(tmp_path):
    gw = _FakeGateway(["   ", "\n\t"])   # 纯空白 = 垃圾
    app, store, reg, rec = _app(tmp_path, gateway=gw)
    client = TestClient(app)
    r = client.post(f"/api/pursuit/{rec.id}/narrate", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["source"] == "fallback", "空/纯空白回复应退兜底,不硬塞垃圾"
    assert data["narration"].strip()


# ---- 4. 未知 pursuit → 不 500(ok False)----
def test_narrate_unknown_pursuit_no_500(tmp_path):
    app, store, reg, rec = _app(tmp_path, gateway=None)
    client = TestClient(app)
    r = client.post("/api/pursuit/nope-not-real/narrate", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is False
