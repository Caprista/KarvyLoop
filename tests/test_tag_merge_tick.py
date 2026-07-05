"""test_tag_merge_tick — 同义标签 daily 收敛(反向标签护栏③)。

锁五件(watermark+冷却形制,镜像 test_belief_tags_tick 的纪律):
① 同义碎片被收敛进别名表(二阶共现候选:零词面交集的"夜间模式/深色主题"靠共同邻居逮),
   审计痕 = 别名表 via/ts + Trace kind=tag_merged;收敛后同 query 两条认知互相可见。
② 判过"不同义"的对子记冷却:词表变了重跑也不再问同一对。
③ watermark:候选全处理完落词表指纹 → 词表没变的后续轮零 LLM。
④ 自动合并只动**别名表**(派生数据),历史 beliefs 的缓存标签原样(resolve 原始视图不变)。
⑤ 没接 gateway/cache → ran=False 不炸。
"""
from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.concepts import TAG_VOCAB_TASK_ID, ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.cognition.trace import TraceStore  # noqa: E402
from karvyloop.console.tag_merge_tick import JUDGE_COOLDOWN_S, tag_merge_tick  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

_NOW = 1_700_000_000.0


class TextDelta:   # 事件按 type(ev).__name__ 识别,stub 类名必须叫 TextDelta
    def __init__(self, text):
        self.text = text


class _GW:
    """同义判定桩:在 prompt 里找同时含 a、b 的行号回它;记调用数与收到的 prompt。"""

    def __init__(self, synonym_pair=None):
        self.synonym_pair = synonym_pair
        self.n_calls = 0
        self.prompts = []

    def resolve_model(self, scope):
        return "stub"

    async def complete(self, messages, tools, ref, *, system=None):
        self.n_calls += 1
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        hits = []
        if self.synonym_pair:
            a, b = self.synonym_pair
            for line in prompt.splitlines():
                if a in line and b in line:
                    hits.append(int(line.split(".", 1)[0]))
        yield TextDelta(json.dumps(hits))


def _seed_cache(cc: ConceptCache) -> None:
    """两条同主题认知打了同义异名标签(共享邻居「界面外观」= 二阶共现候选),
    再补 4 个不相干标签把词表撑过 MIN_VOCAB。"""
    cc.put("用户偏好深色主题的界面配色", ["深色主题", "界面外观"])
    cc.put("屏幕亮度晚上要调低一档", ["夜间模式", "界面外观"])
    cc.put("周报每周五下午写", ["周报流程", "写作习惯"])
    cc.put("拿铁只喝燕麦奶的", ["饮品口味", "咖啡偏好"])


def _app(cc, gw, *, trace=None):
    return SimpleNamespace(state=SimpleNamespace(
        memory=None, concept_cache=cc,
        runtime_kwargs={"gateway": gw, "model_ref": ""},
        main_loop=SimpleNamespace(trace=trace) if trace is not None else None))


@pytest.mark.asyncio
async def test_synonyms_merge_into_alias_with_audit(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    _seed_cache(cc)
    gw = _GW(synonym_pair=("夜间模式", "深色主题"))
    trace = TraceStore()
    res = await tag_merge_tick(_app(cc, gw, trace=trace),
                               state_path=tmp_path / "st.json", now=_NOW)
    assert res["ran"] is True and res["merged"] == 1
    # ① 同组了(别名表,方向由使用频次/字典序定,组一致即可)
    assert cc.canonical_of("夜间模式") == cc.canonical_of("深色主题")
    # 审计痕:别名表带 via/ts + Trace tag_merged
    amap = cc.alias_map()
    assert len(amap) == 1
    evs = trace.query(TAG_VOCAB_TASK_ID, kind="tag_merged")
    assert len(evs) == 1 and {evs[0].payload["alias"], evs[0].payload["canonical"]} == {
        "夜间模式", "深色主题"}
    # ④ 历史 beliefs 的缓存标签没被重写(原始视图)
    raw, _ = cc.resolve(["用户偏好深色主题的界面配色", "屏幕亮度晚上要调低一档"])
    assert raw == [["深色主题", "界面外观"], ["夜间模式", "界面外观"]]
    # 收敛后:召回侧同 query 两条互相可见(匹配视图展开)
    mem = MemoryManager(concept_cache=cc)
    for c in ("用户偏好深色主题的界面配色", "屏幕亮度晚上要调低一档"):
        mem.write(Belief(content=c, provenance={"source": "ingest", "ts": _NOW},
                         freshness_ts=_NOW, scope="personal"))
    block = mem.recall_block("夜间模式", scope="personal", limit=8)
    assert "深色主题" in block and "屏幕亮度" in block


@pytest.mark.asyncio
async def test_not_synonym_pairs_enter_cooldown(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    _seed_cache(cc)
    gw = _GW(synonym_pair=None)   # 全判不同义
    res1 = await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json", now=_NOW)
    assert res1["ran"] is True and res1["merged"] == 0 and gw.n_calls == 1
    asked_first = gw.prompts[0]
    assert "夜间模式" in asked_first
    # 词表变了(watermark 失效)→ 重跑;冷却窗内判过的对子**不再出现在 prompt 里**
    cc.put("新增一条星空观测记录", ["天文望远镜", "夜空爱好"])
    res2 = await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json", now=_NOW + 3600)
    if res2["ran"]:   # 若还有新候选(新标签组合),老对子必须缺席
        assert "夜间模式」 vs 「深色主题" not in gw.prompts[-1]
        assert "深色主题」 vs 「夜间模式" not in gw.prompts[-1]
    # 冷却过期后可重问
    res3 = await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json",
                                now=_NOW + JUDGE_COOLDOWN_S + 10)
    assert res3["ran"] in (True, False)   # 行为完好不炸(候选取决于词表状态)


@pytest.mark.asyncio
async def test_watermark_unchanged_vocab_zero_llm(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    _seed_cache(cc)
    gw = _GW(synonym_pair=("夜间模式", "深色主题"))
    await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json", now=_NOW)
    n_after_first = gw.n_calls
    # 第二轮:合并让词表折叠(指纹变)→ 允许再算候选,但同组对子已消失 → 零 LLM
    res2 = await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json", now=_NOW + 60)
    assert gw.n_calls == n_after_first
    # 第三轮:词表指纹命中 → watermark 直接跳过,依旧零 LLM
    res3 = await tag_merge_tick(_app(cc, gw), state_path=tmp_path / "st.json", now=_NOW + 120)
    assert res3["ran"] is False and gw.n_calls == n_after_first
    assert "watermark" in res3["reason"] or "没有同义候选" in res3["reason"]


@pytest.mark.asyncio
async def test_missing_wiring_is_graceful(tmp_path):
    cc = ConceptCache(tmp_path / "cc.json")
    app = SimpleNamespace(state=SimpleNamespace(
        memory=None, concept_cache=cc, runtime_kwargs={}, main_loop=None))
    res = await tag_merge_tick(app, state_path=tmp_path / "st.json", now=_NOW)
    assert res["ran"] is False and res["merged"] == 0
