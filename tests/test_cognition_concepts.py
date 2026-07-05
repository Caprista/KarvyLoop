"""test_cognition_concepts — 认知图谱语义边(B:LLM 抽概念 + wiki 互链)。

AC:
- concept_graph:两条共享概念 → 语义边(semantic=True);没概念 → 回退词面(≥2 token)
- extract_concepts_batch:合法 JSON 二维数组 → 解析;垃圾/长度不符 → 全空(宁空勿毒)
- ConceptCache:put/resolve + 跨实例持久化
- /api/memory/graph:假 gw 抽概念 → 缓存 → 概念边
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.graph import concept_graph  # noqa: E402
from karvyloop.cognition.concepts import ConceptCache, extract_concepts_batch  # noqa: E402
from karvyloop.gateway.events import TextDelta  # noqa: E402


class _B:
    def __init__(self, content, kind="fact"):
        self.content = content
        self.provenance = {"kind": kind, "source": "test"}


# ---- concept_graph:语义边 vs 词面回退 ----
def test_concept_graph_semantic_edge():
    beliefs = [_B("我在做 KarvyLoop"), _B("KarvyLoop 用 Python")]
    g = concept_graph(beliefs, [["KarvyLoop", "项目"], ["KarvyLoop", "Python"]])
    assert len(g["edges"]) == 1
    e = g["edges"][0]
    assert e["semantic"] is True and "KarvyLoop" in e["via"]   # 共享 1 个概念就连 + 标注


def test_concept_graph_token_fallback():
    # 没概念(空)→ 回退词面,需 ≥2 共享 token
    beliefs = [_B("Python 后端 开发 项目"), _B("Python 后端 经验")]
    g = concept_graph(beliefs, [[], []])
    assert len(g["edges"]) == 1 and g["edges"][0]["semantic"] is False
    # 词面只共享 1 个 → 不连
    g2 = concept_graph([_B("Python 项目"), _B("Java 经验")], [[], []])
    assert g2["edges"] == []


# ---- extract:严解析,宁空勿毒 ----
class _FakeGW:
    def __init__(self, text):
        self._text = text
    def resolve_model(self, scope):
        return "m"
    async def complete(self, messages, tools, ref, *, system=None):
        yield TextDelta(text=self._text)


@pytest.mark.asyncio
async def test_extract_parses_valid_json():
    gw = _FakeGW('[["Python","后端"],["周报","自动化"]]')
    out = await extract_concepts_batch(["a", "b"], gateway=gw)
    assert out == [["Python", "后端"], ["周报", "自动化"]]


@pytest.mark.asyncio
async def test_extract_garbage_returns_empty():
    gw = _FakeGW("抱歉我不太确定怎么抽,这是一段散文……")   # 不是 JSON
    out = await extract_concepts_batch(["a", "b"], gateway=gw)
    assert out == [[], []]                       # 宁空勿毒(回退词面)
    # 长度对不上也全空
    gw2 = _FakeGW('[["x"]]')                      # 只 1 条,输入 2 条
    assert await extract_concepts_batch(["a", "b"], gateway=gw2) == [[], []]


# ---- cache:持久化 ----
def test_concept_cache_persists(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    concepts, missing = cc.resolve(["foo", "bar"])
    assert missing == [0, 1]
    cc.put("foo", ["Foo概念"])
    # 跨实例(模拟重启)命中
    cc2 = ConceptCache(tmp_path / "cc.json")
    concepts2, missing2 = cc2.resolve(["foo", "bar"])
    assert concepts2[0] == ["Foo概念"] and missing2 == [1]


# ---- tags_for:召回热路径的只读口(零 LLM,memo 化)----
def test_tags_for_reads_and_memo_invalidates_on_put(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    assert cc.tags_for("没抽过的") == []              # 未命中 → 空(调用方退词面)
    cc.put("foo", ["Foo概念"])
    assert cc.tags_for("foo") == ["Foo概念"]
    cc.put("foo", ["新概念"])                          # put 后 memo 失效,读到新标签
    assert cc.tags_for("foo") == ["新概念"]
    # 跨实例(重启)也读得到
    assert ConceptCache(tmp_path / "cc.json").tags_for("foo") == ["新概念"]


# ---- tag_beliefs:写入路径批量打标(watermark + 抽空不落缓存)----
class _Bf:
    def __init__(self, content):
        self.content = content


class _CountingGW(_FakeGW):
    def __init__(self, text):
        super().__init__(text)
        self.n_calls = 0

    async def complete(self, messages, tools, ref, *, system=None):
        self.n_calls += 1
        yield TextDelta(text=self._text)


@pytest.mark.asyncio
async def test_tag_beliefs_caches_and_watermarks(tmp_path):
    from karvyloop.cognition.concepts import tag_beliefs
    cc = ConceptCache(tmp_path / "cc.json")
    gw = _CountingGW('[["夜间模式"],["烘焙点心"]]')
    n = await tag_beliefs([_Bf("深色主题"), _Bf("烤箱面包")], cache=cc, gateway=gw)
    assert n == 2 and gw.n_calls == 1                 # 一次 batch 打两条
    assert cc.tags_for("深色主题") == ["夜间模式"]
    # watermark:都抽过 → 第二次零 LLM
    n2 = await tag_beliefs([_Bf("深色主题"), _Bf("烤箱面包")], cache=cc, gateway=gw)
    assert n2 == 0 and gw.n_calls == 1


@pytest.mark.asyncio
async def test_tag_beliefs_empty_result_not_cached(tmp_path):
    # 抽空/失败(宁空勿毒解析返全空)→ **不落缓存**,留给 daily 回填重试
    from karvyloop.cognition.concepts import tag_beliefs
    cc = ConceptCache(tmp_path / "cc.json")
    n = await tag_beliefs([_Bf("深色主题")], cache=cc, gateway=_FakeGW("散文垃圾输出"))
    assert n == 0
    _, missing = cc.resolve(["深色主题"])
    assert missing == [0]                             # 仍算"没抽过"


# ---- 端点:抽概念 → 缓存 → 概念边 ----
def test_memory_graph_endpoint(tmp_path):
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    class _Mem:
        class _Idx:
            def all(self, scope):
                return [_B("我在做 KarvyLoop 项目"), _B("KarvyLoop 用 Python 写")]
        index = _Idx()

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = _Mem()
    app.state.concept_cache = ConceptCache(tmp_path / "cc.json")
    app.state.runtime_kwargs = {"gateway": _FakeGW('[["KarvyLoop","项目"],["KarvyLoop","Python"]]'),
                                "model_ref": "m"}
    g = TestClient(app).get("/api/memory/graph").json()
    assert len(g["nodes"]) == 2
    assert len(g["edges"]) == 1 and g["edges"][0]["semantic"] is True   # 共享 KarvyLoop
