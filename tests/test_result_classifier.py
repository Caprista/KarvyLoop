"""test_result_classifier — §13.3 语义可缓存性判定:拿不准/异常/联网 一律 dynamic(宁重跑不投毒)。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.result_classifier import make_result_classifier  # noqa: E402

TextDelta = type("TextDelta", (), {"__init__": lambda self, text: setattr(self, "text", text)})


class _GW:
    def __init__(self, word, boom=False): self._w = word; self._boom = boom
    def resolve_model(self, scope): return "m"
    async def complete(self, msgs, tools, ref, *, system=None):
        if self._boom:
            raise RuntimeError("x")
        for ch in self._w:
            yield TextDelta(ch)


def test_no_gateway_returns_none():
    assert make_result_classifier(None) is None


def test_web_search_short_circuits_dynamic_without_llm():
    # 用过联网工具 → 直接 dynamic,不调 LLM(gateway 会 boom,但根本不该被调)
    clf = make_result_classifier(_GW("stable", boom=True), "m")
    assert clf("查最新行情", "…", [{"name": "web_search"}]) == "dynamic"


def test_llm_says_stable():
    clf = make_result_classifier(_GW("stable"), "m")
    assert clf("把华氏100换算成摄氏", "37.8", [{"name": "run_command"}]) == "stable"


def test_llm_says_dynamic():
    clf = make_result_classifier(_GW("dynamic"), "m")
    assert clf("比对两个文件差异", "…", [{"name": "read_file"}]) == "dynamic"


def test_llm_failure_defaults_dynamic():
    clf = make_result_classifier(_GW("stable", boom=True), "m")
    assert clf("纯文本任务", "…", [{"name": "read_file"}]) == "dynamic"
