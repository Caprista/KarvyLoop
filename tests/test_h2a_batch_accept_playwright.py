"""test_h2a_batch_accept_playwright — 刀3 组批的真浏览器行为(docs/92)。

契约测试(test_h2a_batch_accept.py)锁字符串;这里验**浏览器运行时**三件事:
  1. 出钮边界:同 kind 低风险 3 卡组 → 组头有「全部接受」钮(rect>0 真可见);
     混 kind 组 / 含高风险组 → 永不出钮(hidden);
  2. 全批 = 逐卡发 ACCEPT 带 batch 标:桩 WebSocket.send 断言 **3 条独立 h2a_decision**
     (proposal_id 各异 + batch=chain_id 全同,不是一个合并消息),且服务端 decision_log
     真落 3 条(每卡一条、可逐卡回溯);
  3. 中止语义:第 2 张卡闸(反投降 confirm)触发、用户取消 → **中止剩余**(只发了 1 条,
     其余 2 张卡还在);已拍的不回滚。

守卫:playwright/chromium 未装 → 干净跳过(契约层照跑);console 起不来 → fail。
截图存 KARVY_SHOT_DIR(缺省 tests/_artifacts/batch_accept)。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import socket
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pw_ready() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pw_ready(),
    reason="playwright/chromium 未装(`pip install playwright && playwright install chromium`)→ 跳过浏览器层")

_SHOTS = os.environ.get("KARVY_SHOT_DIR",
                        os.path.join(os.path.dirname(__file__), "_artifacts", "batch_accept"))


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mk_proposal(i: int, kind: str, chain_id: str):
    from karvyloop.karvy.atoms import Proposal
    return Proposal(summary=f"[{kind}] 建议 {i}", options=("ACCEPT", "DEFER", "REJECT"),
                    strength=0.7, evidence_refs=(), habit_id=100 + i, model_ref="m", ts=1.0,
                    kind=kind, payload={"merged_content": f"内容 {i}"},
                    chain_id=chain_id, basis="测试链")


@pytest.fixture
def console(tmp_path):
    """真起 console + 三条链的待决卡(A=3×crystallize_skill 低风险同 kind → 出钮;
    B=混 kind 低风险 → 不出;C=merge_atoms 高风险 → 不出)。yield (base_url, app)。

    kind 选型注:docs/92 原文例子"一组 merge_knowledge"已过时 —— docs/52 §2 语义审查把
    merge_knowledge(删除语义)补进了 HIGH_RISK_KINDS。判定源唯一(silence.HIGH_RISK_KINDS),
    真·低风险同 kind 用 crystallize_skill(纯新增、provisional、可删,非破坏性)。"""
    import uvicorn

    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.console.decision_log import DecisionLog
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app.state.conversation_manager = mgr
    app.state.no_llm = True

    reg = PendingProposalRegistry()
    pids_a = []
    for i in range(3):                                  # 链 A:低风险同 kind → 出钮
        p = _mk_proposal(i, "crystallize_skill", "chain-A")
        reg.register(p)
        pids_a.append(p.proposal_id)
    reg.register(_mk_proposal(10, "crystallize_skill", "chain-B"))  # 链 B:混 kind → 不出
    reg.register(_mk_proposal(11, "route_to_role", "chain-B"))      # (两个都低风险,只混 kind;
    #  注:run_task/schedule_suggest 走预判列不进 #h2a-list,混 kind 样本必须选决策列 kind)
    reg.register(_mk_proposal(20, "merge_atoms", "chain-C"))        # 链 C:高风险 → 不出
    reg.register(_mk_proposal(21, "merge_atoms", "chain-C"))
    app.state.proposal_registry = reg
    app.state.decision_log = DecisionLog()

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# 桩 WS:捕获前端发出的每条 h2a_decision(捕获后照转真 send —— 服务端行为不变)
_WS_TAP = """
(() => {
  window.__h2aSent = [];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    try {
      const m = JSON.parse(data);
      if (m && m.type === "h2a_decision") window.__h2aSent.push(m.payload);
    } catch (e) {}
    return orig.call(this, data);
  };
})();
"""

# 决策卡 detail 桩:测试确定性(建卡不吃 LLM/耗时);needs_recheck 卡用于中止语义
_NO_CARD = {"ok": False}


def _route_decision_card(page, recheck_pid: str | None = None):
    def handler(route):
        url = route.request.url
        if recheck_pid and recheck_pid in url:
            route.fulfill(json=({"ok": True, "card": {
                "needs_recheck": True, "high_value": False, "violations": [],
                "resolvable": "unverifiable", "criteria": [], "narrated_warning": False,
                "problem": "", "approach": "", "aligned_prefs": []}}))
        else:
            route.fulfill(json=_NO_CARD)
    page.route("**/api/decision_card?*", handler)


def _open_page(pw, base):
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return browser, page, errors


def test_batch_button_only_on_low_risk_same_kind_group(console):
    """出钮边界:A 组有钮且真可见(rect>0);B(混 kind)/C(高风险)组钮 hidden。"""
    from playwright.sync_api import sync_playwright
    base, _app = console
    os.makedirs(_SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        browser, page, errors = _open_page(pw, base)
        _route_decision_card(page)
        page.goto(base, wait_until="commit", timeout=10000)
        page.wait_for_selector('#h2a-list .h2a-chain-group[data-chain="chain-A"]',
                               timeout=10000)
        btn_a = page.query_selector(
            '.h2a-chain-group[data-chain="chain-A"] .chain-batch-accept')
        assert btn_a is not None
        box = btn_a.bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, "A 组钮必须真可见(rect>0)"
        assert "Accept all (3)" in btn_a.inner_text()
        for chain in ("chain-B", "chain-C"):
            btn = page.query_selector(
                f'.h2a-chain-group[data-chain="{chain}"] .chain-batch-accept')
            assert btn is not None, f"{chain} 组壳里连钮骨架都没有(结构变了)"
            cls = btn.get_attribute("class") or ""
            assert "hidden" in cls, f"{chain} 组不许出全批钮(拍板边界)"
            assert not btn.bounding_box(), f"{chain} 组钮必须不可见"
        page.screenshot(path=os.path.join(_SHOTS, "group-head-with-button.png"),
                        full_page=True)
        assert not errors, "\n".join(errors)
        browser.close()


def test_batch_accept_sends_n_independent_decisions(console):
    """全批:二次确认后逐卡发 ACCEPT 带 batch=chain_id —— 3 条独立消息 + 流水 3 条。"""
    from playwright.sync_api import sync_playwright
    base, app = console
    os.makedirs(_SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        browser, page, errors = _open_page(pw, base)
        _route_decision_card(page)
        page.add_init_script(_WS_TAP)
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(base, wait_until="commit", timeout=10000)
        page.wait_for_selector('#h2a-list .h2a-chain-group[data-chain="chain-A"]',
                               timeout=10000)
        page.click('.h2a-chain-group[data-chain="chain-A"] .chain-batch-accept')
        page.wait_for_function("window.__h2aSent && window.__h2aSent.length >= 3",
                               timeout=15000)
        # A 组 3 张全清(组壳解散);其他两组还在
        page.wait_for_function(
            "!document.querySelector('.h2a-chain-group[data-chain=\\'chain-A\\']')",
            timeout=10000)
        sent = page.evaluate("window.__h2aSent")
        assert len(sent) == 3, f"全批必须逐卡发消息(得到 {len(sent)} 条)"
        assert all(m["decision"] == "ACCEPT" for m in sent)
        assert all(m.get("batch") == "chain-A" for m in sent), "每条都要带批次标"
        assert len({m["proposal_id"] for m in sent}) == 3, "3 条必须是 3 张不同的卡"
        # 二次轻确认真弹过,kind 用 #6 同一套人话映射(en 表 proposal.kind.crystallize_skill)
        assert any("Accept all 3" in d and "Crystallize skill" in d for d in dialogs), dialogs
        # 服务端流水:每卡一条、batch 可回溯
        rows = app.state.decision_log.recent(10)
        batch_rows = [r for r in rows if r.get("batch") == "chain-A"]
        assert len(batch_rows) == 3
        assert len({r["proposal_id"] for r in batch_rows}) == 3
        page.wait_for_timeout(400)   # 退场动画结束后再截"批后"态
        page.screenshot(path=os.path.join(_SHOTS, "after-batch-accept.png"), full_page=True)
        assert not errors, "\n".join(errors)
        browser.close()


def test_batch_aborts_remaining_when_gate_cancelled(console):
    """中止语义:第 2 张卡反投降闸弹 confirm、用户取消 → 只发 1 条,剩 2 张不动。"""
    from playwright.sync_api import sync_playwright
    base, app = console
    # 第 2 张(注册序)= 链 A 第二张卡:detail 桩回 needs_recheck=true → ACCEPT 前弹反投降 confirm
    reg = app.state.proposal_registry
    pids_a = [p.proposal_id for p in reg.pending() if p.chain_id == "chain-A"]
    assert len(pids_a) == 3
    recheck_pid = pids_a[1]
    with sync_playwright() as pw:
        browser, page, errors = _open_page(pw, base)
        _route_decision_card(page, recheck_pid=recheck_pid)
        page.add_init_script(_WS_TAP)

        def on_dialog(d):
            # 批量确认(Accept all …)→ 接;反投降闸(accepting without changes)→ 取消
            if "Accept all" in d.message:
                d.accept()
            else:
                d.dismiss()
        page.on("dialog", on_dialog)
        page.goto(base, wait_until="commit", timeout=10000)
        page.wait_for_selector('#h2a-list .h2a-chain-group[data-chain="chain-A"]',
                               timeout=10000)
        page.click('.h2a-chain-group[data-chain="chain-A"] .chain-batch-accept')
        page.wait_for_function("window.__h2aSent && window.__h2aSent.length >= 1",
                               timeout=15000)
        page.wait_for_timeout(1500)   # 给"若未中止会继续发"的错误路径留出现形时间
        sent = page.evaluate("window.__h2aSent")
        assert len(sent) == 1, f"第 2 张闸被取消必须中止剩余(实际发了 {len(sent)} 条)"
        assert sent[0]["proposal_id"] == pids_a[0]     # 已拍的第 1 张不回滚
        assert sent[0].get("batch") == "chain-A"
        # 剩余 2 张还在(组壳仍在,成员 2)
        remaining = page.evaluate(
            "document.querySelectorAll('.h2a-chain-group[data-chain=\\'chain-A\\'] .h2a-card').length")
        assert remaining == 2, f"剩余卡必须原地不动(剩 {remaining})"
        rows = [r for r in app.state.decision_log.recent(10) if r.get("batch") == "chain-A"]
        assert len(rows) == 1 and rows[0]["proposal_id"] == pids_a[0]
        assert not errors, "\n".join(errors)
        browser.close()
