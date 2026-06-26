"""test_run_title_refine — 2b:工作流/圆桌主题太长 → LLM 精炼短标题;短的原样、失败兜底截断。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.routes import _refine_run_title  # noqa: E402


class _Ev:
    def __init__(self, text): self.text = text
    # 类名必须是 TextDelta(_refine 按 type(ev).__name__ 判定)
_Ev.__name__ = "TextDelta"
TextDelta = type("TextDelta", (), {"__init__": lambda self, text: setattr(self, "text", text)})


class _GW:
    def __init__(self, title): self._title = title
    def resolve_model(self, scope): return "m"
    async def complete(self, msgs, tools, ref, *, system=None):
        for ch in self._title:
            yield TextDelta(ch)


def test_short_passthrough_no_llm():
    # 短主题:不调 LLM,原样返回(gw=None 也行)
    assert asyncio.run(_refine_run_title(None, "", "做个登录页")) == "做个登录页"


def test_long_topic_refined_by_llm():
    gw = _GW("登录页流程")
    long = "我想让产品和设计一起把整个登录注册还有找回密码的完整页面流程都做出来并对齐细节"
    out = asyncio.run(_refine_run_title(gw, "", long))
    assert out == "登录页流程"


def test_llm_failure_falls_back_to_truncation():
    class _BadGW:
        def resolve_model(self, scope): raise RuntimeError("boom")
        async def complete(self, *a, **k):
            if False: yield
    long = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六"
    out = asyncio.run(_refine_run_title(_BadGW(), "", long, max_keep=24))
    assert out == long[:24] and len(out) == 24
