"""test_concept_tag_wiring — P0③:概念标签链路的断接可见性。

事实核查(2026-07-07,修正审计):belief_tags_tick **已接**(console/app.py 慢侧维护 loop,
daily 随 knowledge/skill/tag_merge 各 tick 同节奏跑);生产两入口(console/entry.py、
cli/chat.py)都给 MemoryManager 传了 concept_cache。审计的"断接"真形态只剩半件:
MemoryManager 持久化形态(store 接了 = 生产样)却没接 concept_cache 时 ——
新条永无标签、同义改写召回**静默**退化纯词面、daily 回填无处可写,而系统一声不响;
且 belief_tags_tick 在这种形态下把原因**误报**成 "memory/gateway 未接"。

本文件锁三件:
① 构造时响一次 warning(不刷屏:仅"store 有而 cache 无"的生产样形态;纯内存测试形态不响);
② daily tick 的 reason 精确报 "gateway/concept_cache 未接"(复用 tag_merge_tick 既有 i18n 串,
   不另造用户可见新串);
③ 维护 loop 对 belief_tags_tick 的接线事实(源码扫描,防未来真断接回归)。
"""
from __future__ import annotations

import logging
import pathlib
import sys
import time
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scan import grep_py  # noqa: E402
from karvyloop.cognition.belief_store import BeliefStore  # noqa: E402
from karvyloop.cognition.concepts import ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.belief_tags_tick import belief_tags_tick  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

_MEM_LOGGER = "karvyloop.cognition.memory"


def _belief(content: str) -> Belief:
    return Belief(content=content, provenance={"source": "test", "ts": time.time()},
                  freshness_ts=time.time(), scope="personal")


# ---------- ① 生产样形态缺 concept_cache → 构造时响一次(修前红:全程静默) ----------

def test_store_without_concept_cache_warns_once(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=_MEM_LOGGER):
        mem = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
        # 摄入若干条:除构造时那一次,不许每写一条刷一条(不刷屏)
        mem.write(_belief("深色主题的偏好"))
        mem.write(_belief("周报要 Markdown"))
    hits = [r for r in caplog.records if "concept_cache 未接" in r.message]
    assert len(hits) == 1, f"应恰好响一次,实际 {len(hits)} 次"


def test_no_warning_when_cache_wired_or_ephemeral(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=_MEM_LOGGER):
        MemoryManager(store=BeliefStore(tmp_path / "b.json"),
                      concept_cache=ConceptCache(tmp_path / "cc.json"))   # 接全了:生产正形
        MemoryManager()                                                   # 纯内存(测试形):不响
        MemoryManager(concept_cache=ConceptCache(tmp_path / "cc2.json"))  # 无 store:不响
    assert not [r for r in caplog.records if "concept_cache 未接" in r.message]


# ---------- ② daily tick 精确报因(修前红:误报 memory/gateway 未接) ----------

def _app(mem, gw):
    return SimpleNamespace(state=SimpleNamespace(
        memory=mem, runtime_kwargs={"gateway": gw, "model_ref": ""}))


async def test_tick_reason_names_concept_cache(tmp_path):
    mem = MemoryManager()          # memory 在、gateway 在,单缺 concept_cache
    res = await belief_tags_tick(_app(mem, gw=object()), state_path=tmp_path / "st.json")
    assert res["ran"] is False and res["tagged"] == 0
    # 复用 tag_merge_tick 的既有 i18n 串(i18n 表已有,零新串)
    assert res["reason"] == "gateway/concept_cache 未接(--no-llm?)"


async def test_tick_reason_memory_gateway_unchanged(tmp_path):
    """回归锁:真缺 memory/gateway 时,原 reason 一字不变。"""
    res = await belief_tags_tick(SimpleNamespace(state=SimpleNamespace(
        memory=None, runtime_kwargs={})), state_path=tmp_path / "st.json")
    assert res["ran"] is False
    assert res["reason"] == "memory/gateway 未接(--no-llm?)"


# ---------- ③ 接线事实锁(核查结论:tick 已接;防未来真断接) ----------

def test_belief_tags_tick_wired_in_maintenance_loop():
    app_py = ROOT / "karvyloop" / "console" / "app.py"
    assert grep_py(r"belief_tags_tick\(app", app_py), \
        "console/app.py 维护 loop 必须调用 belief_tags_tick(存量标签回填断接)"
