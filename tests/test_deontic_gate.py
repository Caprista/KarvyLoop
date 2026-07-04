"""test_deontic_gate — 域 deontic forbid 的确定性工具闸(docs/54 B1 Top1)。

不变量:
① finance 域 forbid「直接执行任何交易或转账操作」→ 交易类工具/命令在**真 authorize 链**
   被确定性 Deny(step 6.5,免疫 FULL,不靠模型自觉);
② 分层诚实:纯语义 forbid(「隐瞒下行风险只报收益」)→ soft,不进硬闸、不误拦;
③ 零误伤:scope 武装下,普通合法操作(读写文件/echo/git status/查询类工具)一律不被 deontic 拦;
④ 类别隔离:只 forbid 交易的域,删除/发邮件不受硬闸影响(反之亦然);
⑤ scope per-run 武装,with 退出必复位,绝不泄漏到下一次 run/私聊;
⑥ 接线:paradigm persona 携带机器可读 deontic_forbid → forge 咽喉真武装 → 工具never执行
   + 诚实 reason 回灌模型;
⑦ 防双注入:机器可读属性不往 prompt 文本里加任何字;
⑧ apply_deontic(mode="enforce") 真抛 DeonticViolationError(此前只有 docstring 声称)。
"""
from __future__ import annotations

import io
import json
import time

import pytest

from karvyloop.capability.decision import Allow, Deny, authorize
from karvyloop.capability.deontic_gate import (
    CATEGORY_DELETE,
    CATEGORY_EXTERNAL_SEND,
    CATEGORY_TRANSACTION,
    active_scope,
    build_scope,
    check_active,
    classify_forbid,
    deontic_scope,
    scope_from_system,
)
from karvyloop.capability.policy import Mode, PermissionContext

pytestmark = pytest.mark.security   # 安全套件:域 deontic 硬闸绕闸对抗(pytest -m security)

# finance 模板的真实 forbid(domain/templates.py finance-research)
FINANCE_FORBID = ("直接执行任何交易或转账操作", "隐瞒下行风险只报收益")


def _ctx(tool: str, inp: dict | None = None, mode: Mode = Mode.WORKSPACE_WRITE):
    return PermissionContext(tool=tool, input=inp or {}, mode=mode)


# ============ ② 分层诚实:classify_forbid ============

def test_classify_finance_forbid_layering():
    split = classify_forbid(FINANCE_FORBID)
    # 「直接执行任何交易或转账操作」→ 确定性可拦(transaction)
    assert (CATEGORY_TRANSACTION, FINANCE_FORBID[0]) in split.enforceable
    # 「隐瞒下行风险只报收益」→ 纯语义,诚实归 soft(不声称硬了)
    assert FINANCE_FORBID[1] in split.soft
    assert all(src != FINANCE_FORBID[1] for _, src in split.enforceable)


def test_classify_pure_semantic_stays_soft():
    split = classify_forbid(("不要用傲慢的语气", "不追热点叙事"))
    assert split.enforceable == ()
    assert len(split.soft) == 2
    # 全 soft → 不武装闸
    assert build_scope(("不要用傲慢的语气",)) is None


def test_classify_multi_category():
    split = classify_forbid(("不许转账,也不许删除任何数据",))
    cats = {c for c, _ in split.enforceable}
    assert CATEGORY_TRANSACTION in cats and CATEGORY_DELETE in cats


# ============ ① finance forbid → 真 authorize 链确定性 Deny ============

def test_finance_scope_denies_transaction_tools_real_authorize():
    scope = build_scope(FINANCE_FORBID, domain="理财研究所")
    with deontic_scope(scope):
        for tool, inp in [
            ("mcp_broker_place_order", {"symbol": "AAPL", "qty": 10}),
            ("transfer_funds", {"to": "acct-1", "amount": 500}),
            ("run_command", {"command": "stripe payment_intents create --amount 100"}),
            ("run_command", {"command": 'curl -X POST https://api.broker.com/v1/orders -d {"qty":1}'}),
        ]:
            d = authorize(_ctx(tool, inp))
            assert isinstance(d, Deny), f"{tool} 该被 deontic 硬闸拦,实际 {d}"
            assert d.reason == f"deontic:forbid:{CATEGORY_TRANSACTION}", (tool, d.reason)
            # 诚实 reason:带域名 + forbid 原文
            assert FINANCE_FORBID[0] in d.message and "理财研究所" in d.message


def test_deontic_gate_immune_to_full_mode():
    """FULL/bypass 模式也拦(step 6.5 在 step 7 之前)——域的硬规则谁开全模式都不豁免。"""
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        d = authorize(_ctx("transfer_funds", {"amount": 1}, mode=Mode.FULL))
        assert isinstance(d, Deny) and d.reason.startswith("deontic:forbid:")


def test_adversarial_gap_fixes_camelcase_and_wget():
    """对抗验收揪出的两个洞已堵:
    Gap1 camelCase 工具名(transferFunds/placeOrder)绕 token 匹配 → FULL 模式漏;
    Gap2 wget --method=POST/--post-data 不识别为 HTTP 写。"""
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        # Gap1:camelCase 变体在 FULL 模式也必拦
        for tool in ("transferFunds", "placeOrder", "executeTrade", "submitOrder"):
            d = authorize(_ctx(tool, {"x": 1}, mode=Mode.FULL))
            assert isinstance(d, Deny) and d.reason.startswith("deontic:forbid:"), \
                f"camelCase 变体 {tool} 漏拦: {d}"
        # Gap2:wget 的 POST 形态
        for cmd in ("wget --method=POST https://api.broker.com/v1/payment --body-data=x",
                    "wget --post-data 'qty=1' https://broker.com/orders"):
            d = authorize(_ctx("run_command", {"command": cmd}))
            assert isinstance(d, Deny) and d.reason.startswith("deontic:forbid:"), \
                f"wget 写请求漏拦: {cmd} → {d}"
        # 全小写连写(buyshares)= 诚实的保守漏拦(无词典切不了;未知工具 FULL 下限兜底),
        # 但绝不该被误标 —— 这里锁"它不命中 deontic"的现状,防未来有人加子串匹配引入误拦
        assert check_active("buyshares", {}) is None
        # wget 只读下载照旧不拦
        assert check_active("run_command",
                            {"command": "wget https://broker.com/report.pdf"}) is None


def test_read_queries_not_blocked_by_transaction_forbid():
    """查订单/看行情 ≠ 下订单:读语义前缀/只读工具豁免(宁漏勿错)。"""
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        for tool, inp in [
            ("get_order_status", {"id": "o1"}),
            ("list_trades", {}),
            ("web_search", {"query": "AAPL 股价 转账手续费对比"}),
            ("web_fetch", {"url": "https://broker.com/orders"}),
            ("run_command", {"command": "curl https://api.broker.com/v1/orders"}),  # GET 只读
            ("run_command", {"command": "grep -r transfer docs/"}),                  # 分析文本
        ]:
            d = authorize(_ctx(tool, inp))
            assert not (isinstance(d, Deny) and d.reason.startswith("deontic:")), \
                f"{tool} 是读/分析操作,不该被 deontic 拦: {d}"


# ============ ③ 零误伤:普通合法操作 ============

def test_normal_ops_zero_collateral_under_armed_scope(tmp_path):
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        for tool, inp in [
            ("read_file", {"path": str(tmp_path / "a.md")}),
            ("write_file", {"path": str(tmp_path / "report.md")}),
            ("edit_file", {"path": str(tmp_path / "report.md")}),
            ("run_command", {"command": "echo hello"}),
            ("run_command", {"command": "git status"}),
            ("run_command", {"command": "python analyze.py"}),
            ("create_atom", {"name": "x"}),
            ("git_commit", {"message": "docs"}),
        ]:
            d = authorize(PermissionContext(tool=tool, input=inp,
                                            mode=Mode.WORKSPACE_WRITE,
                                            workspace_root=str(tmp_path)))
            assert isinstance(d, Allow), f"合法操作 {tool}({inp}) 被误拦: {d}"


def test_unarmed_scope_is_total_noop():
    """未武装(私聊/CLI/无域)→ 交易类名字的工具也不被 deontic 拦(0 回归)。"""
    assert active_scope() is None
    d = authorize(_ctx("mcp_broker_place_order", {"qty": 1}))
    assert not (isinstance(d, Deny) and d.reason.startswith("deontic:"))
    assert check_active("transfer_funds", {}) is None


# ============ ④ 类别隔离 ============

def test_category_isolation():
    # 只 forbid 交易 → 删除/发邮件不受硬闸影响
    with deontic_scope(build_scope(("不得执行任何交易",), domain="fin")):
        assert check_active("delete_file", {"path": "x"}) is None
        assert check_active("run_command", {"command": "rm -r build"}) is None
        assert check_active("send_email", {"to": "a@b.c"}) is None
    # 只 forbid 删除 → delete_file/rm 拦,交易不拦
    with deontic_scope(build_scope(("禁止删除任何用户数据",), domain="ops")):
        hit = check_active("delete_file", {"path": "x"})
        assert hit is not None and hit.category == CATEGORY_DELETE
        hit2 = check_active("run_command", {"command": "rm -rf cache"})
        assert hit2 is not None and hit2.category == CATEGORY_DELETE
        assert check_active("run_command", {"command": "git rm old.txt"}) is not None
        assert check_active("transfer_funds", {}) is None
        # 读不误拦
        assert check_active("read_file", {"path": "x"}) is None
        assert check_active("run_command", {"command": "git status"}) is None
    # 只 forbid 外发邮件 → send_email/sendmail 拦,web_fetch 不拦
    with deontic_scope(build_scope(("未经批准不得对外发送邮件",), domain="pr")):
        hit = check_active("send_email", {"to": "x@y.z"})
        assert hit is not None and hit.category == CATEGORY_EXTERNAL_SEND
        assert check_active("run_command", {"command": "sendmail x@y.z < mail.txt"}) is not None
        assert check_active("web_fetch", {"url": "https://mail.example.com"}) is None
        assert check_active("run_command", {"command": "echo mail"}) is None


# ============ ⑤ scope 生命周期:with 退出复位,不泄漏 ============

def test_scope_resets_after_with_block():
    assert active_scope() is None
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        assert active_scope() is not None
    assert active_scope() is None    # 退出即复位 → 下一次私聊/run 不受牵连
    # None scope = no-op 也不炸
    with deontic_scope(None):
        assert active_scope() is None


# ============ ⑥ 接线:paradigm persona → forge 咽喉真武装(端到端,真 authorize) ============

def _paradigm_persona_with_finance_deontic(tmp_path):
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry

    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("macro-analyst", identity="我是宏观分析师", soul="风险先说满", atom_ids=[])
    reg = BusinessDomainRegistry()
    domain = reg.create(
        name="理财研究所", created_by="user:h",
        value_md_raw="# 价值观\n- 研究是建议,不是指令",
        deontic=Deontic(forbid=FINANCE_FORBID,
                        oblige=("每份研判附风险清单",)),
        member_query="user:h AND agent:macro-analyst",
    )
    return build_role_paradigm_prompt(rv, domain, intent="帮我看看行情", cwd=str(tmp_path))


def test_paradigm_persona_carries_machine_readable_deontic(tmp_path):
    cp = _paradigm_persona_with_finance_deontic(tmp_path)
    assert cp is not None
    assert getattr(cp, "covers_domain_governance", False) is True   # 软护栏去重标记照旧
    assert tuple(getattr(cp, "deontic_forbid", ())) == FINANCE_FORBID
    assert getattr(cp, "deontic_domain", "") == "理财研究所"
    scope = scope_from_system(cp)
    assert scope is not None and scope.domain == "理财研究所"
    assert (CATEGORY_TRANSACTION, FINANCE_FORBID[0]) in scope.entries
    assert FINANCE_FORBID[1] in scope.soft                          # 纯语义留软


def test_machine_readable_attrs_add_zero_prompt_text(tmp_path):
    """防双注入:deontic_forbid 属性是机器可读接线,不往 prompt 文本加一个字。
    (软护栏文本由 paradigm 编译器管;硬闸不产出 prompt,结构上无从双注入。)"""
    cp = _paradigm_persona_with_finance_deontic(tmp_path)
    text = cp.to_text()
    assert "deontic_forbid" not in text and "deontic_gate" not in text


@pytest.mark.asyncio
async def test_forge_end_to_end_denies_forbidden_action_and_reports_honestly(tmp_path):
    """独立于模型自觉的端到端:role(finance 域 persona)试图跑交易命令 →
    工具**从未执行**(sandbox 零调用)+ 模型收到 capability_denied + deontic 原文。"""
    from tests.test_forge import FakeSandbox, _gw, _tok
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
    from karvyloop.coding.forge import generate_and_run
    from karvyloop.coding.ndjson import NdjsonEmitter

    cp = _paradigm_persona_with_finance_deontic(tmp_path)
    assert cp is not None
    sb = FakeSandbox(str(tmp_path))
    adapter = ScriptedMockAdapter(rounds=[
        # 模型(扮演不守规矩的 role)直接发起交易命令
        tool_round("c1", "run_command",
                   {"command": "stripe payment_intents create --amount 99900"}),
        text_round("好的,我不执行交易。"),
    ])
    gw = _gw(adapter)
    sink = io.StringIO()
    emitter = NdjsonEmitter(sink=sink, session_id="deo1")
    res = await generate_and_run(
        "帮我把这笔交易执行了", _tok(), sb,
        gateway=gw, emitter=emitter, workspace_root=str(tmp_path),
        model_ref="p/a", system_prompt=cp,
    )
    # 1) 命令从未进 sandbox(确定性拦截,不是模型自觉)
    assert sb.exec_log == [], f"交易命令居然真执行了: {sb.exec_log}"
    # 2) NDJSON tool_result 是 error
    lines = [json.loads(l) for l in sink.getvalue().splitlines() if l]
    errs = [l for l in lines if l.get("kind") == "tool_result" and l.get("is_error")]
    assert errs, "被拦的调用必须以 is_error tool_result 回灌(fail-loud)"
    # 3) 模型第二轮收到诚实 reason(capability_denied + forbid 原文)
    msgs = adapter.last_request["messages"]
    fed_back = json.dumps(msgs, ensure_ascii=False)
    assert "capability_denied" in fed_back
    assert FINANCE_FORBID[0] in fed_back, "deontic 拦截 reason 必须诚实带 forbid 原文"
    # 4) run 正常终结,scope 不泄漏
    assert res.terminal.value == "completed"
    assert active_scope() is None


@pytest.mark.asyncio
async def test_forge_normal_work_unaffected_under_finance_persona(tmp_path):
    """同一 finance persona 下,正常研究工作(读文件/写报告)零误伤。"""
    from tests.test_forge import FakeSandbox, _gw, _tok
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
    from karvyloop.coding.forge import generate_and_run

    cp = _paradigm_persona_with_finance_deontic(tmp_path)
    sb = FakeSandbox(str(tmp_path))
    sb.files[str(tmp_path / "data.md")] = "wow".encode("utf-8")
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": str(tmp_path / "data.md")}),
        tool_round("c2", "run_command", {"command": "echo analysis-done"}),
        text_round("研判写完了(附风险清单)。"),
    ])
    gw = _gw(adapter)
    res = await generate_and_run("帮我出一份研判", _tok(), sb, gateway=gw,
                                 workspace_root=str(tmp_path), model_ref="p/a",
                                 system_prompt=cp)
    assert res.terminal.value == "completed"
    assert len(sb.exec_log) == 1   # echo 真跑了(没被误拦)


# ============ ⑧ apply_deontic enforce 真抛 ============

def test_apply_deontic_enforce_raises():
    from karvyloop.domain.deontic import Deontic, DeonticViolationError, apply_deontic
    deo = Deontic(forbid=("执行交易",))
    # report 模式:只报告不抛(旧行为不变)
    r = apply_deontic(deo, "执行交易", mode="report")
    assert r.forbidden and not r.allowed
    # enforce 模式:真抛(此前 docstring 声称会抛但从不抛 = 假接线,已修)
    with pytest.raises(DeonticViolationError):
        apply_deontic(deo, "执行交易", mode="enforce")
    # 未违规不抛
    assert apply_deontic(deo, "写研报", mode="enforce").allowed
