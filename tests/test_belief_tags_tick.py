"""test_belief_tags_tick — 知识概念标签回填(daily 慢侧;#61 研判①b)。

锁四件(镜像 test_skill_tags 的纪律):
① 存量无标签条被批量补进 ConceptCache(一次 batch);失效条(invalid_at)不烧。
② watermark:缓存命中即跳过 —— 第二轮零 LLM。
③ 抽空记冷却:窗口内不反复烧同一条;冷却过后可重试。
④ 没接 memory/gateway/cache → ran=False 不炸。
"""
from __future__ import annotations

import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.concepts import ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.belief_tags_tick import EMPTY_COOLDOWN_S, belief_tags_tick  # noqa: E402
from karvyloop.gateway.events import TextDelta  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

_NOW = 1_700_000_000.0


def _belief(content, invalid=False):
    b = Belief(content=content, provenance={"source": "ingest", "ts": _NOW},
               freshness_ts=_NOW, scope="personal")
    if invalid:
        b.invalid_at = _NOW
    return b


class _GW:
    def __init__(self, text):
        self._text = text
        self.n_calls = 0

    def resolve_model(self, scope):
        return "m"

    async def complete(self, messages, tools, ref, *, system=None):
        self.n_calls += 1
        yield TextDelta(text=self._text)


def _app(mem, gw):
    return SimpleNamespace(state=SimpleNamespace(
        memory=mem, runtime_kwargs={"gateway": gw, "model_ref": ""}))


@pytest.mark.asyncio
async def test_backfills_missing_and_skips_invalid(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    mem.write(_belief("深色主题的偏好"))
    mem.write(_belief("过时的旧条", invalid=True))    # 失效条召回不看 → 不烧
    gw = _GW('[["夜间模式"]]')                         # 只该有 1 条待打
    res = await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW)
    assert res["ran"] is True and res["tagged"] == 1 and gw.n_calls == 1
    assert cc.tags_for("深色主题的偏好") == ["夜间模式"]
    _, missing = cc.resolve(["过时的旧条"])
    assert missing == [0]                              # 失效条没被烧


@pytest.mark.asyncio
async def test_watermark_second_run_zero_llm(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    mem.write(_belief("深色主题的偏好"))
    gw = _GW('[["夜间模式"]]')
    await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW)
    res2 = await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW + 60)
    assert res2["ran"] is False and gw.n_calls == 1    # 缓存命中 = watermark,零 LLM


@pytest.mark.asyncio
async def test_empty_extraction_cools_down_then_retries(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    mem.write(_belief("抽不出标签的条"))
    gw = _GW("散文垃圾")                                # 宁空勿毒 → 全空
    await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW)
    assert gw.n_calls == 1
    # 冷却窗内:不再烧
    res2 = await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW + 3600)
    assert res2["ran"] is False and gw.n_calls == 1
    # 冷却过后:重试
    res3 = await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json",
                                  now=_NOW + EMPTY_COOLDOWN_S + 1)
    assert res3["ran"] is True and gw.n_calls == 2


@pytest.mark.asyncio
async def test_missing_wiring_is_graceful(tmp_path):
    res = await belief_tags_tick(SimpleNamespace(state=SimpleNamespace(
        memory=None, runtime_kwargs={})), state_path=tmp_path / "st.json")
    assert res["ran"] is False
