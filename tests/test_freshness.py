"""时效性智能(atoms/freshness)验收 —— Hardy 碎碎念④:实时信息必须联网查,不许凭记忆编。

覆盖四层:
  1. 信号检测单测(正例:今天股价/最新版本/实时;反例:历史问题/本地文件/概念解释)
  2. 任务标注(有 web=必须查证;无 web=如实说查不到;无信号=原样;幂等)
  3. 合同测试:web_search/web_fetch description 含时效指引;coding prompt 含时效纪律
  4. forge 接缝:时效 intent → 下发给 executor 的任务文本带【时效提示】(0 回归:无信号原样)
"""

from __future__ import annotations

import time

import pytest

from karvyloop.atoms.freshness import (
    FRESHNESS_DISCIPLINE,
    FRESHNESS_NOTE_MARKER,
    annotate_task,
    freshness_note,
    freshness_signals,
    has_freshness_signal,
)


# ============ 1. 信号检测 ============

@pytest.mark.parametrize("text", [
    "今天美元兑人民币汇率是多少",
    "查一下 numpy 的最新版本",
    "现在北京天气怎么样",
    "昨晚湖人比分多少",
    "帮我看下实时油价",
    "本周有什么科技新闻",
    "what's the latest Node.js release",
    "today's weather in Beijing",
    "current price of bitcoin",
    "breaking news about the election",
    f"{time.localtime().tm_year}年世界杯赛程",   # 今年及以后的年份 = 时效信号
])
def test_positive_freshness_signals(text):
    assert has_freshness_signal(text), f"应检出时效信号: {text}"
    assert freshness_signals(text), f"signals 不应为空: {text}"


@pytest.mark.parametrize("text", [
    "把 utils.py 里的函数改成 async",
    "读取本地文件 README.md 并总结",
    "解释一下快速排序的原理",
    "1994年发生了什么大事",          # 历史年份不是时效信号
    "写一个 python for 循环示例",
    "美国宪法是哪一年签署的",
    "explain special relativity like I'm five",
    "refactor the parser in src/parser.py",
    "",
])
def test_negative_freshness_signals(text):
    assert not has_freshness_signal(text), f"不应检出时效信号: {text}"


def test_historical_year_vs_current_year():
    this_year = time.localtime().tm_year
    assert not has_freshness_signal("2010年的论文综述")
    assert has_freshness_signal(f"{this_year}年的新品发布")


# ============ 2. 任务标注 ============

def test_annotate_adds_note_with_web():
    out = annotate_task("今天美元兑人民币汇率是多少", has_web=True)
    assert FRESHNESS_NOTE_MARKER in out
    assert "web_search" in out
    assert "不得凭训练记忆" in out
    assert out.startswith("今天美元兑人民币汇率是多少")   # 原任务在前,提示是追加


def test_annotate_no_web_is_honest_not_fabricating():
    """无 web 工具时:提示必须要求「如实说查不到」而不是编 —— 诚实回执。"""
    out = annotate_task("今天美元兑人民币汇率是多少", has_web=False)
    assert FRESHNESS_NOTE_MARKER in out
    assert "没有可用的联网工具" in out
    assert "如实告知" in out
    assert "绝不编造" in out


def test_annotate_no_signal_returns_unchanged():
    raw = "把 utils.py 里的函数改成 async"
    assert annotate_task(raw, has_web=True) == raw
    assert annotate_task(raw, has_web=False) == raw


def test_annotate_idempotent():
    once = annotate_task("今天天气如何", has_web=True)
    twice = annotate_task(once, has_web=True)
    assert twice == once
    assert twice.count(FRESHNESS_NOTE_MARKER) == 1


def test_freshness_note_two_variants_differ():
    assert freshness_note(has_web=True) != freshness_note(has_web=False)
    assert "web_search" in freshness_note(has_web=True)


# ============ 3. 合同测试:工具描述 + system prompt ============

def test_web_search_description_has_freshness_guidance():
    """description 是模型「知道该搜」的第一层:必须点名时效场景 + 何时别用 + 失败要诚实。"""
    from karvyloop.coding.tools.web import WebSearchTool
    d = WebSearchTool.description
    for kw in ("time-sensitive", "news", "prices", "weather", "versions",
               "today", "latest"):
        assert kw in d, f"web_search description 缺时效触发词: {kw}"
    assert "Do NOT" in d, "缺「何时别用」(本地文件/稳定知识)"
    assert "local files" in d
    assert "honestly" in d, "缺「搜失败要诚实」指引"


def test_web_fetch_description_mentions_verification():
    from karvyloop.coding.tools.web import WebFetchTool
    d = WebFetchTool.description
    assert "time-sensitive" in d
    assert "memory" in d


def test_coding_prompt_contains_freshness_discipline(tmp_path):
    """执行器默认 system prompt(build_coding_prompt)必须带时效纪律 + 列出 web 工具。"""
    from karvyloop.coding.prompt import build_coding_prompt
    p = build_coding_prompt(str(tmp_path))
    text = p.to_text()
    assert FRESHNESS_DISCIPLINE in text
    assert "web_search" in text, "工具集说明须列出 web_search(工具在,prompt 也得知道)"
    # 纪律在静态段(可被 prompt cache 复用,不进动态段)
    assert any(FRESHNESS_DISCIPLINE in s for s in p.static)


def test_coding_prompt_static_still_stable(tmp_path):
    """回归:加纪律后静态段仍字节稳定(AC11 缓存前提)。"""
    from karvyloop.coding.prompt import build_coding_prompt
    assert build_coding_prompt(str(tmp_path)).static == build_coding_prompt(str(tmp_path)).static


# ============ 4. forge 接缝:时效任务下发带提示(真走 forge 主路径) ============

@pytest.mark.asyncio
async def test_forge_annotates_fresh_intent(tmp_path):
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round
    from karvyloop.coding.forge import generate_and_run
    from tests.test_forge import FakeSandbox, _gw, _tok

    sb = FakeSandbox(str(tmp_path))
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("查证后:7.1")]))
    res = await generate_and_run(
        "今天美元兑人民币汇率是多少", _tok(), sb,
        gateway=gw, workspace_root=str(tmp_path), model_ref="p/a",
    )
    got = res.run.input.get("intent", "")
    assert FRESHNESS_NOTE_MARKER in got, "时效 intent 下发给 executor 时须带【时效提示】"
    assert "web_search" in got   # forge 工具集带 web → 「必须查证」变体
    # EphemeralTool.from_intent 保持原始意图(不带注入,结晶/审计视角干净)
    assert res.tool.from_intent == "今天美元兑人民币汇率是多少"


@pytest.mark.asyncio
async def test_forge_leaves_normal_intent_unchanged(tmp_path):
    """0 回归:无时效信号的任务,下发文本原样。"""
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round
    from karvyloop.coding.forge import generate_and_run
    from tests.test_forge import FakeSandbox, _gw, _tok

    sb = FakeSandbox(str(tmp_path))
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("ok")]))
    raw = "总结一下快速排序的原理"
    res = await generate_and_run(
        raw, _tok(), sb,
        gateway=gw, workspace_root=str(tmp_path), model_ref="p/a",
    )
    assert res.run.input.get("intent", "") == raw
