"""test_card_lang — K(内测产品条③)决策卡中英混杂修复。

锁两件事:
① 三处硬编码中文 f-string(委派/重跑 report approach、聊天路由提示)走 i18n(en+zh 同键);
   源码扫描锁住不回潮。
② 卡侧 LLM prompt 注入界面语言(i18n 进程 locale):
   - 决策卡追问(decision_card_ask)system prompt 带应答语言指令;
   - 违背检测(check_violations)system prompt 带 "why" 字段语言指令;
   - 标题精炼(_refine_run_title)system prompt 带主题名语言指令。
   locale 未设 → 跟 i18n 现行默认(en),不硬编码 zh。
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import i18n  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402
from tests._scan import grep_py  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_locale(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)
    yield
    i18n.set_locale(None)


# ---- ① 硬编码 f-string 走 i18n(en+zh 都有、占位插值可用) ----

def test_report_and_route_keys_bilingual():
    i18n.set_locale("en")
    en_route = i18n.t("report.approach_route", role="Analyst")
    en_rerun = i18n.t("report.approach_rerun", intent="weekly report")
    en_rt = i18n.t("route.roundtable_hint", who="A、B", group="G", topic="T")
    en_dg = i18n.t("route.delegate_hint", domain_name="Sales", role="Analyst")
    assert "Analyst" in en_route and "domain governance" in en_route
    assert "Re-run" in en_rerun and "weekly report" in en_rerun
    assert "roundtable" in en_rt and "G" in en_rt and "T" in en_rt
    assert "Sales" in en_dg and "H2A" in en_dg
    i18n.set_locale("zh")
    assert "域治理" in i18n.t("report.approach_route", role="分析师")
    assert "重跑" in i18n.t("report.approach_rerun", intent="周报")
    assert "圆桌" in i18n.t("route.roundtable_hint", who="甲、乙", group="群", topic="题")
    assert "业务域" in i18n.t("route.delegate_hint", domain_name="销售", role="分析师")


def test_source_no_longer_hardcodes_route_strings():
    """源码扫描锁:旧硬编码中文 f-string 不许回潮(字符串已搬 i18n 表)。"""
    ph = ROOT / "karvyloop" / "console" / "proposal_handlers.py"
    rt = ROOT / "karvyloop" / "console" / "routes.py"
    assert not grep_py(r"approach=f\"由「", ph, skip_comments=False)
    assert not grep_py(r"approach=f\"重跑「", ph, skip_comments=False)
    assert not grep_py(r"这是开\*\*圆桌\*\*(几个人坐一起)", rt, skip_comments=False)
    assert not grep_py(r"要不要转给「\{match", rt, skip_comments=False)


# ---- ② 卡侧 LLM prompt 注入界面语言 ----

class _CaptureGateway:
    """记录喂给模型的 messages + system(验证 lang 指令真进了 prompt)。"""

    def __init__(self, reply: str = "[]") -> None:
        self._reply = reply
        self.seen_prompt = ""
        self.seen_system = ""

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, messages, tools, ref, *, system=None, response_schema=None):
        self.seen_prompt = messages[0]["content"] if messages else ""
        self.seen_system = " ".join(getattr(system, "static", []) or []) if system else ""

        class TextDelta:
            def __init__(self, text): self.text = text
        yield TextDelta(self._reply)


def _app_with_gw(gw, proposal=None):
    reg = PendingProposalRegistry()
    if proposal is not None:
        reg.register(proposal)
    return types.SimpleNamespace(state=types.SimpleNamespace(
        proposal_registry=reg, main_loop=None, memory=None,
        runtime_kwargs={"gateway": gw, "model_ref": ""}))


def _proposal():
    return Proposal(summary="部署到预发环境", options=("ACCEPT", "DEFER", "REJECT"), strength=0.9,
                    evidence_refs=(), habit_id=0, model_ref="x/y", ts=0.0,
                    kind="run_task", payload={}, basis="改动只动了文案")


@pytest.mark.asyncio
async def test_check_violations_injects_ui_lang_en():
    from karvyloop.console.decision_card_wire import check_violations
    i18n.set_locale("en")
    gw = _CaptureGateway("[]")
    app = _app_with_gw(gw)
    out = await check_violations(app, "买入某股票", ["不碰单一个股"])
    assert out == []
    assert 'Write the "why" values in English.' in gw.seen_system
    # 守线员本体 prompt 仍在(只追加语言指令,不改判定逻辑)
    assert "违背" in gw.seen_system


@pytest.mark.asyncio
async def test_check_violations_injects_ui_lang_zh():
    from karvyloop.console.decision_card_wire import check_violations
    i18n.set_locale("zh")
    gw = _CaptureGateway("[]")
    app = _app_with_gw(gw)
    await check_violations(app, "买入某股票", ["不碰单一个股"])
    assert '"why" 字段用中文写' in gw.seen_system


@pytest.mark.asyncio
async def test_decision_card_ask_injects_ui_lang():
    from karvyloop.console.decision_card_wire import decision_card_ask
    i18n.set_locale("en")
    gw = _CaptureGateway("ok")
    p = _proposal()
    app = _app_with_gw(gw, proposal=p)
    r = await decision_card_ask(app, proposal_id=p.proposal_id, question="风险大吗?", transcript=[])
    assert r["ok"] is True
    assert "Answer in English." in gw.seen_system
    i18n.set_locale("zh")
    await decision_card_ask(app, proposal_id=p.proposal_id, question="风险大吗?", transcript=[])
    assert "用中文回答" in gw.seen_system


@pytest.mark.asyncio
async def test_refine_run_title_injects_ui_lang():
    from karvyloop.console.routes import _refine_run_title
    i18n.set_locale("en")
    gw = _CaptureGateway("Competitive analysis")
    long_text = "帮我把这个季度所有竞品的产品动态和定价变化整理成一份完整的对比分析报告"
    out = await _refine_run_title(gw, "", long_text)
    assert out  # 精炼出了标题
    assert "Write the topic name in English." in gw.seen_system
    i18n.set_locale("zh")
    await _refine_run_title(gw, "", long_text)
    assert "主题名用中文" in gw.seen_system
