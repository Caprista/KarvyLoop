"""test_tag_parse_no_compute_loop — 标签解析器不许 O(n²) 烧 CPU(生产事故回归)。

**背景**:daily 慢侧 `belief_tags_tick`/`tag_merge_tick` 给存量知识补语义标签,走
`assign_tags` → LLM → `_extract_json_array` 解析。再打标 lin-en(中→英翻译内容、多条堆叠)
时命中一次**算力死循环**:CPU 烧 30+ 分钟、零输出、零进度,只能杀进程。

**病根**:`_extract_json_array` / `_salvage_truncated_array` 对**每个 `[` 起点**各
`raw_decode` 一次(各 O(剩余长度)),故总功 ≈ O(起点数 × 文本长度) = 平方级。正常 LLM
输出 `[` 少、体量小,碰不到;但**病态输出**(散文里方括号海、思考型模型烧掉整窗口、
翻译内容把多条堆叠成超长带括号 prose)会把 n 和起点数一起顶上去 → 平方爆炸。

**修复**:对扫描窗口(`_MAX_PARSE_CHARS`)和起点数(`_MAX_BRACKET_STARTS`)各设确定性
硬上限——超限即退化成"只看头部前若干起点",绝不无界自旋(fail-safe:解析退化=退回词面,
不投毒)。另外把 `RecursionError`(方括号海把 json 递归打爆)按解析失败吞掉,别炸调用方。

**本测**(修复前会跑几十秒~数分钟,修复后毫秒级):
① 解析器直连:病态 bracket-dense 输入必须在时间预算内返回(锁住不再平方爆炸)。
② 端到端:`belief_tags_tick` 喂一个返回病态输出的 mock LLM,整轮必须在预算内终止。
③ 修复不伤正常输出:合法数组/散文夹数组/截断尾 仍照旧正确解析(锚回归)。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.concepts import ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.belief_tags_tick import belief_tags_tick  # noqa: E402
from karvyloop.gateway.events import TextDelta  # noqa: E402
from karvyloop.karvy.fastbrain.trace_habit import (  # noqa: E402
    _MAX_BRACKET_STARTS,
    _extract_json_array,
    _salvage_truncated_array,
)
from karvyloop.schemas.cognition import Belief  # noqa: E402

_NOW = 1_700_000_000.0

# 时间预算:病态输入的解析/整轮 tick 都必须远低于它。修复前同样输入是几十秒~数分钟,
# 修复后是毫秒级,所以 2s 既能可靠区分"已修/未修",又不会在慢 CI 上假红。
_BUDGET_S = 2.0


def _pathological_prose(n_brackets: int) -> str:
    """病态 LLM 输出:密布 `[` 的超长散文,且**没有**任何全-dict 合法数组 →
    解析器必须对每个 `[` 起点都试解一遍(修复前 = O(n²) 烧 CPU)。
    形态对应「中→英翻译、多条知识堆叠、带 [ref]/[[wiki]] 标记」的真实产物。"""
    return "the translated note [ref] was recorded here again and again. " * n_brackets


# ---- ① 解析器直连:病态输入必须在预算内返回(不再平方爆炸) ----


def test_extract_json_array_bounded_on_bracket_dense_prose() -> None:
    text = _pathological_prose(40_000)   # ~1.4MB,含 ~4 万个 `[`;修复前实测 ~50s
    t0 = time.perf_counter()
    out = _extract_json_array(text)
    dt = time.perf_counter() - t0
    assert dt < _BUDGET_S, f"解析器在病态输入上耗时 {dt:.2f}s(疑似 O(n²) 回归)"
    # 宁空勿毒:没有合法 dict 数组 → 返回的东西 json.loads 不出一个真数组
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(out)


def test_extract_json_array_survives_all_brackets_without_crash() -> None:
    """纯 `[` 海:json 递归会抛 RecursionError —— 必须被当解析失败吞掉,不炸调用方,也不自旋。"""
    t0 = time.perf_counter()
    out = _extract_json_array("[" * 100_000)
    dt = time.perf_counter() - t0
    assert dt < _BUDGET_S, f"全括号输入耗时 {dt:.2f}s"
    assert isinstance(out, str)   # 没抛,返回了个串(交给上层 json.loads 报错 → 宁空勿毒)


def test_salvage_truncated_array_bounded_on_bracket_dense() -> None:
    text = _pathological_prose(40_000)
    t0 = time.perf_counter()
    _salvage_truncated_array(text)
    dt = time.perf_counter() - t0
    assert dt < _BUDGET_S, f"截断兜底在病态输入上耗时 {dt:.2f}s(疑似 O(n²) 回归)"


def test_bracket_start_cap_is_real() -> None:
    """护栏常量必须存在且合理(挡"以后有人删掉上限")。"""
    assert isinstance(_MAX_BRACKET_STARTS, int) and 0 < _MAX_BRACKET_STARTS <= 100_000


# ---- ② 端到端:belief_tags_tick 喂病态 mock LLM,整轮必须在预算内终止 ----


class _PathologicalGW:
    """返回病态 bracket-dense 输出的 mock 网关(确定性、无真 key)。"""

    def __init__(self, text: str) -> None:
        self._text = text
        self.n_calls = 0

    def resolve_model(self, scope):
        return "m"

    async def complete(self, messages, tools, ref, *, system=None):
        self.n_calls += 1
        yield TextDelta(text=self._text)


def _belief(content: str) -> Belief:
    return Belief(content=content, provenance={"source": "ingest", "ts": _NOW},
                  freshness_ts=_NOW, scope="personal")


def _app(mem, gw):
    return SimpleNamespace(state=SimpleNamespace(
        memory=mem, runtime_kwargs={"gateway": gw, "model_ref": ""}))


@pytest.mark.asyncio
async def test_belief_tags_tick_terminates_on_pathological_llm_output(tmp_path) -> None:
    """生产路径回归:LLM 回了一坨病态方括号散文(翻译内容常态),整轮 tick 必须在预算内终止。
    修复前 `assign_tags` → `_extract_json_array` 会在这坨输出上烧几十秒~数分钟 CPU。"""
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    mem.write(_belief("用户偏好把每周工作整理成周报要点"))
    gw = _PathologicalGW(_pathological_prose(40_000))

    t0 = time.perf_counter()
    res = await belief_tags_tick(_app(mem, gw), state_path=tmp_path / "st.json", now=_NOW)
    dt = time.perf_counter() - t0

    assert dt < _BUDGET_S, f"belief_tags_tick 在病态 LLM 输出上耗时 {dt:.2f}s(算力死循环回归)"
    assert gw.n_calls == 1                 # 确实走了 LLM 路径(不是被前置跳过而侥幸快)
    assert res["ran"] is True             # 跑了这一轮
    assert res["tagged"] == 0             # 病态输出解析不出标签 → 宁空勿毒(记冷却,不投毒)


# ---- ③ 修复不伤正常输出(锚回归:合法/散文夹数组/截断尾 仍正确) ----


def test_fix_preserves_normal_parsing() -> None:
    # 纯数组
    assert json.loads(_extract_json_array('[{"pattern":"p","strength":1.0}]')) \
        == [{"pattern": "p", "strength": 1.0}]
    # 散文里夹一个真数组
    prose = '分析[要点]:\n[{"pattern":"整理周报","strength":0.8}]\n(评分[0-1])'
    assert json.loads(_extract_json_array(prose)) == [{"pattern": "整理周报", "strength": 0.8}]
    # 截断尾:完整前项捞回,半个尾项丢掉
    truncated = '[{"pattern":"a","strength":0.9}, {"pattern":"b","stre'
    assert json.loads(_extract_json_array(truncated)) == [{"pattern": "a", "strength": 0.9}]
    # 二维标签数组(assign_tags 的真实产物形态)照旧
    assert json.loads(_extract_json_array('[["夜间模式","界面"]]')) == [["夜间模式", "界面"]]
