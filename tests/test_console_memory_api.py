"""test_console_memory_api — loop step4b 地基:个人知识库 console 端点。

端点:POST /api/memory/ingest(喂材料→编译 Belief 落库)、GET /api/memory(列库)。
也验证 app.state.memory 存在时召回会注入(间接:ingest 后 GET 能看到)。

AC:
- AC1 ingest:喂材料 → 编译出的 Belief 进库,返回 written/beliefs
- AC2 list:GET /api/memory 列出已写入的 Belief(content/kind/source)
- AC3 无 memory(--no-llm 类)→ 诚实回执,不崩
- AC4 无 gateway → 诚实回执
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.cognition.memory import MemoryManager


class TextDelta:  # 名字必须叫 TextDelta(收集器按 type name 认)
    def __init__(self, t):
        self.text = t


class _FakeGW:
    def __init__(self, reply):
        self.reply = reply

    async def complete(self, messages, tools, model_ref, *, system=None):
        for ch in [self.reply]:
            yield TextDelta(ch)


@pytest.fixture
def app_with_memory():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = MemoryManager()
    app.state.runtime_kwargs = {
        "gateway": _FakeGW('[{"content":"用户叫 Hardy","kind":"fact"},'
                           '{"content":"偏好英文默认","kind":"preference"}]'),
        "model_ref": "m",
    }
    return app


def test_ingest_then_list(app_with_memory):
    client = TestClient(app_with_memory)
    r = client.post("/api/memory/ingest", json={"material": "我叫 Hardy,偏好英文。"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["written"] == 2
    assert "用户叫 Hardy" in body["beliefs"]

    # GET /api/memory 列出
    lst = client.get("/api/memory").json()["beliefs"]
    contents = {b["content"] for b in lst}
    assert contents == {"用户叫 Hardy", "偏好英文默认"}
    kinds = {b["content"]: b["kind"] for b in lst}
    assert kinds["偏好英文默认"] == "preference"
    assert all(b["source"] == "ingest" for b in lst)


def test_external_denied_on_raw_dump_endpoints(app_with_memory):
    """§9.6 对外只读裸 dump 洞:分享方(x-karvy-audience=external)一律 403 ——
    /memory 与 /memory/recent 绕过召回过滤直吐个人生活事实,audience 刀盖不到,必须直拒。
    自有设备(无标)照旧 200。"""
    client = TestClient(app_with_memory)
    client.post("/api/memory/ingest", json={"material": "我住幸福路一号。"})
    ext = {"x-karvy-audience": "external"}
    assert client.get("/api/memory", headers=ext).status_code == 403
    assert client.get("/api/memory/recent", headers=ext).status_code == 403
    # 自有设备(不带标)零回归
    assert client.get("/api/memory").status_code == 200
    assert client.get("/api/memory/recent").status_code == 200


def test_external_recall_deny_by_default(app_with_memory):
    """/memory/recall 对外走白名单刀:audience=external + audience_role 空(per-channel role
    绑定未建)→ 共享层 deny-by-default 全拒,block 空;自有设备照旧召回。"""
    client = TestClient(app_with_memory)
    client.post("/api/memory/ingest", json={"material": "我叫 Hardy,偏好英文。"})
    ext = client.get("/api/memory/recall?q=Hardy", headers={"x-karvy-audience": "external"}).json()
    assert ext["ok"] is True and ext["block"] == ""     # 对外:一条不漏
    own = client.get("/api/memory/recall?q=Hardy").json()
    assert own["ok"] is True and "Hardy" in own["block"]  # 自有设备:照常召回(零回归)


def test_ingest_no_memory_honest(app_with_memory):
    # 没接 memory → 诚实回执
    app_with_memory.state.memory = None
    client = TestClient(app_with_memory)
    r = client.post("/api/memory/ingest", json={"material": "x"})
    assert r.json()["ok"] is False and "memory" in r.json()["reason"]


def test_ingest_no_gateway_honest(app_with_memory):
    app_with_memory.state.runtime_kwargs = {}     # 无 gateway
    client = TestClient(app_with_memory)
    r = client.post("/api/memory/ingest", json={"material": "x"})
    assert r.json()["ok"] is False and "gateway" in r.json()["reason"]


def test_list_no_memory_empty(app_with_memory):
    app_with_memory.state.memory = None
    client = TestClient(app_with_memory)
    assert client.get("/api/memory").json()["beliefs"] == []


def test_list_carries_conversation_locator(app_with_memory):
    # Q2 记忆出处回链:对话蒸馏产物 provenance 带 conversation_id → 列表端点必须带出去
    # (面板据此把"对话沉淀"渲染成可点、跳回那次对话);老数据没这键 → 优雅降级 ""(不崩不骗)。
    import time as _t

    from karvyloop.schemas.cognition import Belief

    mem = app_with_memory.state.memory
    now = _t.time()
    mem.write(Belief(content="早上要黑咖啡",
                     provenance={"source": "conversation", "agent": "user", "ts": now,
                                 "trace_ref": "", "kind": "preference",
                                 "conversation_id": "cafe1234deadbeef"},
                     freshness_ts=now, scope="personal"))
    mem.write(Belief(content="旧蒸馏条目没定位",
                     provenance={"source": "conversation", "agent": "user", "ts": now,
                                 "trace_ref": "", "kind": "fact"},
                     freshness_ts=now, scope="personal"))
    client = TestClient(app_with_memory)
    lst = client.get("/api/memory").json()["beliefs"]
    by_content = {b["content"]: b for b in lst}
    assert by_content["早上要黑咖啡"]["conversation_id"] == "cafe1234deadbeef"
    assert by_content["旧蒸馏条目没定位"]["conversation_id"] == ""   # 老数据降级,不崩
