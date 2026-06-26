"""test_schedule_parser — NL→定时任务:有效解析 / 非法 cron 拒 / 无意图拒 / 异常→None / 无 gateway→None。"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.schedule_parser import make_schedule_parser  # noqa: E402

TextDelta = type("TextDelta", (), {"__init__": lambda self, text: setattr(self, "text", text)})


class _GW:
    def __init__(self, payload, boom=False): self._p = payload; self._boom = boom
    def resolve_model(self, scope): return "m"
    async def complete(self, msgs, tools, ref, *, system=None):
        if self._boom:
            raise RuntimeError("x")
        for ch in self._p:
            yield TextDelta(ch)


def test_no_gateway_none():
    assert make_schedule_parser(None) is None


def test_parses_valid():
    gw = _GW(json.dumps({"cron": "0 8 * * *", "intent": "汇总昨天进展",
                         "title": "每日进展", "target_role": "产品经理"}, ensure_ascii=False))
    r = make_schedule_parser(gw, "m")("每天早上8点把昨天进展汇总给我", "2026-06-25 09:00")
    assert r["cron"] == "0 8 * * *" and r["intent"] == "汇总昨天进展"
    assert r["title"] == "每日进展" and r["target_role"] == "产品经理"


def test_invalid_cron_rejected():
    gw = _GW(json.dumps({"cron": "瞎编", "intent": "干活"}))
    assert make_schedule_parser(gw, "m")("随便说说", "") is None


def test_empty_cron_means_not_understood():
    gw = _GW(json.dumps({"cron": "", "intent": "干活"}))
    assert make_schedule_parser(gw, "m")("没有时间规律的话", "") is None


def test_llm_failure_none():
    assert make_schedule_parser(_GW("", boom=True), "m")("每天8点", "") is None
