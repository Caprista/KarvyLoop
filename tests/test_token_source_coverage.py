"""P0-9(内部审计 docs/68)覆盖回归:此前几处会发起 LLM 调用的用户面路径没打
`token_source` 标 → 账本 by_source 记成 "unknown"(看板"谁烧钱"失真)。

本测试用一个记录 `current_source()` 的桩网关,断言这些入口在调 gateway.complete
时上下文里的 source 是**具体来源名**、不是 "unknown"。复现证据:去掉对应
`with token_source(...)` 包裹,断言立刻回落 "unknown"(下方注释说明,不写进产品码)。

覆盖:知识馆员(distill)、决策偏好抽取+调和(楔子进料口)、圆桌主持。
"""
import asyncio

from karvyloop.llm.token_ledger import current_source


class TextDelta:
    """调用方按 `type(ev).__name__ == "TextDelta"` 判流事件 → 类名必须真叫 TextDelta。"""

    def __init__(self, text):
        self.text = text


class _RecordingGateway:
    """桩:complete() 时把当时的 token_source 记进 seen;产出一个最小 TextDelta 流。"""

    def __init__(self):
        self.seen: list[str] = []

    def resolve_model(self, _scope):
        return "fake/model"

    async def complete(self, _messages, _tools, _ref, system=None, **_kw):
        self.seen.append(current_source())
        yield TextDelta("ok")


def test_knowledge_distill_labels_source():
    from karvyloop.console.distill_engine import _distill_analyze, _distill_chat_reply
    gw = _RecordingGateway()
    asyncio.run(_distill_analyze(gw, "", "一段材料"))
    session = {"transcript": [], "fetched": "料", "summary": "总结"}
    asyncio.run(_distill_chat_reply(gw, "", session, "追问"))
    assert gw.seen == ["knowledge_distill", "knowledge_distill"]
    assert "unknown" not in gw.seen


def test_decision_pref_extraction_labels_source():
    from karvyloop.crystallize.decision_pref import (
        DecisionSample, compile_decisions, reconcile_decisions,
    )
    gw = _RecordingGateway()
    samples = [DecisionSample(decision="STATE", context="对外邮件先过目", reason="")]
    asyncio.run(compile_decisions(samples, gateway=gw))
    asyncio.run(reconcile_decisions(samples, existing=["旧偏好"], gateway=gw))
    # 楔子进料口:两次都必须记 decision_pref(此前无标 → unknown,by_source 看不到楔子在烧)
    assert gw.seen == ["decision_pref", "decision_pref"]


def test_roundtable_host_labels_source():
    from karvyloop.console.roundtable_engine import _host_moderate_call
    gw = _RecordingGateway()
    transcript = [{"speaker": "甲", "text": "建议 A"}, {"speaker": "乙", "text": "支持"}]
    asyncio.run(_host_moderate_call(gw, "", "定价", transcript, final=False))
    assert gw.seen == ["roundtable"]
    assert "unknown" not in gw.seen
