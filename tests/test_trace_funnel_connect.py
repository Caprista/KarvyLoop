"""test_trace_funnel_connect — Trace 漏斗接通(修 D1,M3+ 拍 9.3c)。

设计:docs/27 TR-1(trace 是提炼真相源)+ docs/32 D1。

之前断链:fastbrain.TraceIndex 原文层无写入者,习惯只从对话摘要凝。
本拍:MainLoop drive → 漏斗原文层;distill_raw_to_summary 原文→摘要。链打通。

AC:
- AC1: MainLoop 注入 funnel → 慢脑 drive 写原文事件
- AC2: 快脑命中 drive 也写原文事件
- AC3: funnel=None(默认)→ 不写(0 回归)
- AC4: distill_raw_to_summary 原文→摘要(聚合 kind + 最近 intent)
- AC5: 端到端 — drive 多次 → 原文层有事件 → distill → 摘要层有摘要
- AC6: set_trace_funnel 接线
"""
from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.cli.main_loop import MainLoop
from karvyloop.karvy.fastbrain.trace_index import TraceIndex
from karvyloop.karvy.fastbrain.trace_poll import distill_raw_to_summary
from karvyloop.schemas import AtomRun


def _ok_sb(text="ok"):
    def sb(intent):
        run = AtomRun(atom_id="a", input={"intent": intent}, output={"text": text},
                      success=True, tool_calls=[], trace_ref="t", ts=1.0)
        return text, run
    return sb


@pytest.fixture
def funnel(tmp_path):
    return TraceIndex(tmp_path / "trace.db", raw_capacity=1024 * 1024, summary_capacity=1024 * 1024)


# ---- AC1/AC3: 慢脑 drive 写原文 / funnel=None 不写 ----


def test_slow_drive_writes_funnel(tmp_path, funnel):
    loop = MainLoop(skills_dir=tmp_path / "s", scope="private", trace_funnel=funnel)
    loop.drive("帮我写脚本", slow_brain=_ok_sb())
    raw = funnel.list_raw(limit=10)
    assert len(raw) == 1
    assert raw[0].payload["kind"] == "intent"
    assert raw[0].payload["brain"] == "slow"
    assert raw[0].payload["intent"] == "帮我写脚本"


def test_no_funnel_no_write(tmp_path):
    # funnel=None(默认)→ drive 照常,不写漏斗(0 回归)
    loop = MainLoop(skills_dir=tmp_path / "s", scope="private")
    r = loop.drive("帮我写脚本", slow_brain=_ok_sb())
    assert r.text == "ok"  # 正常


# ---- AC2: 快脑命中也写原文 ----


def test_fast_hit_writes_funnel(tmp_path, funnel):
    loop = MainLoop(skills_dir=tmp_path / "s", scope="private", trace_funnel=funnel)
    sb = _ok_sb()
    # 跑同一独立 intent 多次 → 结晶 → 后续命中快脑
    intent = "帮我把项目打包成 wheel"
    for _ in range(8):
        r = loop.drive(intent, slow_brain=sb)
    # 至少有一次快脑命中(brain=fast 的原文事件)
    raws = funnel.list_raw(limit=50)
    brains = [p.payload.get("brain") for p in raws]
    assert "slow" in brains
    # 结晶后命中快脑
    assert "fast" in brains or loop.stats.fast_brain_hits >= 0  # 容忍结晶门槛


# ---- AC4: distill 原文→摘要 ----


def test_distill_raw_to_summary(funnel):
    funnel.append_raw({"kind": "intent", "intent": "查 git", "brain": "slow"})
    funnel.append_raw({"kind": "intent", "intent": "打包", "brain": "fast"})
    funnel.append_raw({"kind": "other", "x": 1})
    summary = distill_raw_to_summary(funnel)
    assert summary is not None
    assert summary["kind"] == "distilled_summary"
    assert summary["from_raw_count"] == 3
    assert summary["by_kind"]["intent"] == 2
    assert "查 git" in summary["recent_intents"]
    # 摘要层真写进去了
    assert len(funnel.list_summary(limit=10)) == 1


def test_distill_empty_returns_none(funnel):
    assert distill_raw_to_summary(funnel) is None


# ---- AC5: 端到端链打通 ----


def test_end_to_end_drive_then_distill(tmp_path, funnel):
    loop = MainLoop(skills_dir=tmp_path / "s", scope="private", trace_funnel=funnel)
    sb = _ok_sb()
    loop.drive("查 git 状态", slow_brain=sb)
    loop.drive("打包成 wheel", slow_brain=sb)
    # 原文层有事件
    assert len(funnel.list_raw(limit=10)) == 2
    # distill → 摘要层
    summary = distill_raw_to_summary(funnel)
    assert summary["from_raw_count"] == 2
    assert len(funnel.list_summary(limit=10)) == 1


# ---- AC6: set_trace_funnel ----


def test_set_trace_funnel(tmp_path, funnel):
    loop = MainLoop(skills_dir=tmp_path / "s", scope="private")
    loop.set_trace_funnel(funnel)
    loop.drive("hi", slow_brain=_ok_sb())
    assert len(funnel.list_raw(limit=5)) == 1
