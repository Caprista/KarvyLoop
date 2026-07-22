"""test_h2a_batch_accept — 决策卡同链合并·刀3:低风险同 kind 组批(docs/92)。

Hardy 拍的边界(一字不差):组级「全部接受」按钮**只在**「组内全部是低风险(非
HIGH_RISK)且**同一个 kind**」时出现;任一张高风险/有违背 → 永不出全批钮,只能逐张。
**全批 = 逐卡发 ACCEPT 带批次标(batch=chain_id),流水里每卡一条、可逐卡回溯** ——
不是一个合并决策。全批只是"替你连点 N 次 Accept",单卡闸(违背强制阅读/反投降/高价值
confirm)照触发;某张闸被取消 → 中止剩余(已拍的不回滚)。

锁四层(前端 grep 桩与刀1/刀2 同风格;后端走真函数/真 WS TestClient):
① 前端结构:组头小钮 + 判定(!high_risk 且 kind 全同,违背懒加载可知后即撤钮)+
   复用单卡 decide(不绕闸)+ 中止语义 + batch 随 h2a_decision 透传;
② i18n 两新键 en+zh 双表齐(TS 源 + 构建产物)+ 确认句 kind 走 #6 同一套人话映射;
③ 后端:H2ADecideRequest.batch → record_decision_signals → decision_log 流水 +
   decision_made Trace 都落 batch;单卡不带字段(老流水/老消息读侧 .get 兼容);
④ WS 真路径:h2a_decision 带 batch → decision_log 真落(逐卡一条)。

真浏览器行为(出钮/全批/中止)在 test_h2a_batch_accept_playwright.py。
红线回归锚:刀1 组壳判定/刀2 抽屉/决策卡内部渲染一字不动(只加钮)。
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceStore  # noqa: E402
from karvyloop.console.decision_log import DecisionLog  # noqa: E402
from karvyloop.console.decision_wire import record_decision_signals  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402

STATIC = ROOT / "karvyloop" / "console" / "static"
FRONTEND_SRC = ROOT / "karvyloop" / "console" / "frontend" / "src"

_BATCH_KEYS = ["chain.batch_accept", "chain.batch_confirm"]


# ---- ① 前端:组头钮 + 判定 + 复用单卡 decide + 中止语义 ----

def test_app_js_has_batch_button_structure():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    for token in ["chain-batch-accept", "_chainBatchEligible", "_refreshChainBatchBtn",
                  "_runChainBatch", "_chainBatchBusy"]:
        assert token in app_js, f"app.js 缺组批结构锚:{token}"
    # 钮文案 + 二次轻确认走 i18n
    assert '"chain.batch_accept"' in app_js and '"chain.batch_confirm"' in app_js


def test_app_js_eligibility_low_risk_same_kind_only():
    """出钮判定:组内全部 !high_risk 且 kind 全相同;任一高风险/已知违背/混 kind → 不出。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    fn = app_js.split("function _chainBatchEligible")[1].split("function _refreshChainBatchBtn")[0]
    assert 'getAttribute("data-high-risk") === "1"' in fn, "高风险排除判定丢了"
    assert 'getAttribute("data-has-violation") === "1"' in fn, "已知违背排除判定丢了"
    assert 'getAttribute("data-kind")' in fn and "k !== kind" in fn, "同 kind 判定丢了"
    # 违背懒加载后可知 → 标卡 + 撤所在组的钮(拍板边界:有违背永不出全批钮)
    assert 'setAttribute("data-has-violation", "1")' in app_js
    # 卡进出组时重判:_regroupChains 内调用刷新
    assert "_refreshChainBatchBtn(g, members)" in app_js


def test_app_js_batch_reuses_single_card_decide_path():
    """全批=替你连点 N 次 Accept:逐卡走**同一个** decide("ACCEPT"),绝不绕单卡闸。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "card._kvDecide = decide;" in app_js, "组批必须复用单卡 decide(不许另起提交路径)"
    assert 'c._kvDecide("ACCEPT", batchId)' in app_js
    # 折叠体里 IO 不触发 → 组批前手动踢懒加载(违背/无脑拍闸有料可拦,不是空转放行)
    assert "card._kvEnsureDetail = _ensureDetail;" in app_js
    assert "c._kvEnsureDetail()" in app_js
    # batch 标从 decide 一路进 h2a_decision 消息(后端透传进流水/Trace)
    assert "msg.batch = String(_batch)" in app_js
    # 组批不发合并决策:唯一的 WS 提交口仍是单卡 _commitDecision 里的 sendWS
    assert app_js.count('sendWS("h2a_decision"') >= 1
    fn = app_js.split("async function _runChainBatch")[1].split("\n  }\n")[0]
    assert "sendWS" not in fn, "_runChainBatch 不许自己 sendWS(必须经单卡 decide)"


def test_app_js_batch_abort_semantics():
    """中止语义(Hardy 拍):某张闸 confirm 被取消 → 中止剩余;已拍的不回滚。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    fn = app_js.split("async function _runChainBatch")[1].split("\n  }\n")[0]
    assert "await c._kvDecide" in fn, "必须逐卡 await(顺序拍,不机关枪并发)"
    assert "if (!committed) break" in fn, "闸被取消必须中止剩余(不是继续问)"
    # decide 的取消路径显式 resolve false(组批据此中止)
    assert "return false" in app_js.split("const decide = (decision, _batch)")[1].split("btnRow.appendChild")[0]
    # 二次轻确认在动手前(kind 人话走 #6 同一套 _kindLabel 映射)
    assert '_kindLabel(kind)' in fn and 'window.confirm(t("chain.batch_confirm"' in fn


def test_app_js_knife1_knife2_untouched():
    """红线:刀1 组壳判定/刀2 抽屉逻辑一字不动(只加钮)。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "risky ? pin : body" in app_js                      # 刀1 高风险分区照旧
    assert "members.forEach((c) => { c.style.removeProperty" in app_js  # 刀1 解散照旧
    assert "_placeCardInDrawer" in app_js and "_isDirectOut(payload)" in app_js  # 刀2 照旧


def test_styles_have_secondary_batch_button():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert ".chain-batch-accept" in css, "styles.css 缺组批钮样式"
    assert ".chain-batch-accept:disabled" in css


# ---- ② i18n:两新键 en+zh 双表齐(TS 源 + 构建产物)----

def test_i18n_batch_keys_both_locales():
    for f in (FRONTEND_SRC / "i18n.ts", STATIC / "i18n.js"):
        text = f.read_text(encoding="utf-8")
        for key in _BATCH_KEYS:
            assert text.count(f'"{key}"') >= 2, f"{f.name} 键 {key} 不在 en+zh 双表(parity 断)"
    # 确认句带 {n} + {kind} 两个槽(zh 原文照 Hardy 拍的措辞)
    i18n_js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "一次接受这 {n} 张「{kind}」卡?每张都会记独立流水。" in i18n_js


# ---- ③ 后端:batch 落 decision_log 流水 + decision_made Trace ----

class _State:
    pass


class _App:
    def __init__(self) -> None:
        self.state = _State()


def _mk_app() -> tuple[_App, Proposal]:
    app = _App()
    app.state.runtime_kwargs = {}
    app.state.memory = None
    app.state.main_loop = types.SimpleNamespace(trace=TraceStore())
    reg = PendingProposalRegistry()
    # kind 注:docs/92 原文例"merge_knowledge"已被 docs/52 §2 补进 HIGH_RISK_KINDS(删除
    # 语义)→ 真·低风险同 kind 用 crystallize_skill(纯新增非破坏性)。后端对 batch 只透传
    # 不重判风险(出钮闸在前端,判定源=silence.HIGH_RISK_KINDS 唯一)。
    p = Proposal(summary="沉淀一条技能", options=("ACCEPT", "DEFER", "REJECT"),
                 strength=0.7, evidence_refs=(), habit_id=1, model_ref="m", ts=1.0,
                 kind="crystallize_skill", payload={"sig": "s1"},
                 basis="连续三次同套路")
    reg.register(p)
    app.state.proposal_registry = reg
    app.state.decision_log = DecisionLog()
    return app, p


def test_batch_lands_in_decision_log_and_trace():
    """带 batch 拍板 → 流水条目 + decision_made Trace 都带批次标(可按批回溯)。"""
    app, p = _mk_app()
    record_decision_signals(app, decision="ACCEPT", proposal_id=p.proposal_id,
                            batch="chain-abc")
    rows = app.state.decision_log.recent(5)
    assert len(rows) == 1 and rows[0]["batch"] == "chain-abc"
    assert rows[0]["proposal_id"] == p.proposal_id   # 每卡一条、可逐卡回溯
    got = app.state.main_loop.trace.query(p.proposal_id, kind="decision_made")
    assert len(got) == 1 and got[0].payload["batch"] == "chain-abc"


def test_no_batch_means_no_field_backcompat():
    """单卡拍板(不带 batch)→ 流水/Trace 都**不落字段**(老格式兼容,读侧 .get)。"""
    app, p = _mk_app()
    record_decision_signals(app, decision="ACCEPT", proposal_id=p.proposal_id)
    rows = app.state.decision_log.recent(5)
    assert len(rows) == 1 and "batch" not in rows[0]
    got = app.state.main_loop.trace.query(p.proposal_id, kind="decision_made")
    assert len(got) == 1 and "batch" not in got[0].payload


def test_decision_log_old_entries_without_batch_still_load(tmp_path):
    """老流水(无 batch 字段)照常加载/查询;新老混存不炸。"""
    import json
    path = tmp_path / "decision_log.json"
    path.write_text(json.dumps([{"ts": 1.0, "decision": "ACCEPT", "summary": "旧",
                                 "proposal_id": "p-old", "reason": "", "kind": "run_task",
                                 "domain": "", "role": ""}]), encoding="utf-8")
    log = DecisionLog(path=path)
    log.record(decision="ACCEPT", summary="新", proposal_id="p-new", batch="chain-x")
    rows = log.recent(10)
    assert len(rows) == 2
    assert rows[0]["batch"] == "chain-x" and "batch" not in rows[1]


def test_h2a_decide_request_accepts_batch():
    """H2ADecideRequest 收 batch(默认空串;老客户端无此字段兼容)。"""
    from karvyloop.console.routes_system import H2ADecideRequest
    req = H2ADecideRequest(proposal_id="p1", decision="ACCEPT", batch="chain-1")
    assert req.batch == "chain-1"
    req2 = H2ADecideRequest(proposal_id="p1", decision="ACCEPT")
    assert req2.batch == ""


# ---- ④ WS 真路径:h2a_decision 带 batch → decision_log 真落(逐卡一条)----

def _console_app_with_chain(n: int = 3):
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    reg = PendingProposalRegistry()
    pids = []
    for i in range(n):
        p = Proposal(summary=f"沉淀技能 {i}", options=("ACCEPT", "DEFER", "REJECT"),
                     strength=0.7, evidence_refs=(), habit_id=10 + i, model_ref="m", ts=1.0,
                     kind="crystallize_skill", payload={"sig": f"s{i}"},
                     chain_id="chain-batch-1" if i else "",   # 链根不回填(刀1 约定)
                     basis="同套路")
        reg.register(p)
        pids.append(p.proposal_id)
    app.state.proposal_registry = reg
    app.state.decision_log = DecisionLog()
    return app, pids


def test_ws_h2a_decision_batch_lands_per_card():
    """WS 真路径:逐卡发 ACCEPT 带同一个 batch → 流水每卡一条(不是一个合并决策)。"""
    from fastapi.testclient import TestClient
    app, pids = _console_app_with_chain(3)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()   # 首次 snapshot
        for pid in pids:
            ws.send_json({"type": "h2a_decision", "payload": {
                "proposal_id": pid, "decision": "ACCEPT", "batch": "chain-batch-1"}})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_envelope"
            assert msg["payload"]["envelope"] is not None
            assert msg["payload"]["envelope"]["by"] == []   # K5 不变量照旧
    rows = app.state.decision_log.recent(10)
    assert len(rows) == 3, "全批必须每卡一条流水"
    assert {r["proposal_id"] for r in rows} == set(pids)   # 可逐卡回溯
    assert all(r["batch"] == "chain-batch-1" for r in rows)
    assert all(r["decision"] == "ACCEPT" for r in rows)


def test_ws_h2a_decision_without_batch_unchanged():
    """老消息(无 batch 字段)WS 路径行为一字不变,流水不落字段。"""
    from fastapi.testclient import TestClient
    app, pids = _console_app_with_chain(1)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "h2a_decision", "payload": {
            "proposal_id": pids[0], "decision": "ACCEPT"}})
        msg = ws.receive_json()
        assert msg["type"] == "h2a_envelope" and msg["payload"]["envelope"] is not None
    rows = app.state.decision_log.recent(10)
    assert len(rows) == 1 and "batch" not in rows[0]
