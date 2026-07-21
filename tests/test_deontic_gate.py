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
⑧ apply_deontic(mode="enforce") 真抛 DeonticViolationError(此前只有 docstring 声称);
⑨ C-03 点名工具硬拦:forbid 原文点名真实工具名 → per-tool 精确阻断(named_tool),
   只读豁免不适用(用户指名道姓优先);点名不存在的工具/子串近似 → 诚实留软,绝不误硬;
⑩ 唯一许可句定向豁免(对抗验收非阻塞项):「只准用 X」「仅允许 X」「除了 X 其他都不许」
   「don't use anything except X」里点名的工具是要**留**的 → 不进阻断集(条目照旧降软);
   「除 X 外随便用」是禁 X 本身,照拦;成语覆盖不到的边缘形态退回误硬(安全侧)。
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
    CATEGORY_NAMED_TOOL,
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


# ============ ⑨ C-03:forbid 点名工具 = 确定性硬拦(named_tool) ============

def test_named_tool_forbid_enforceable_and_denied_real_authorize(tmp_path):
    """「禁止调用 edit_file」→ enforceable(named_tool)+ edit_file 在真 authorize 链被拒、
    其它工具照跑(per-tool 阻断,不殃及邻居)。"""
    entry = "禁止调用 edit_file"
    split = classify_forbid((entry,))
    assert (CATEGORY_NAMED_TOOL, entry) in split.enforceable
    assert split.named == ((entry, ("edit_file",)),)
    assert split.soft == ()                      # 点名条目不再降软
    with deontic_scope(build_scope((entry,), domain="内容组")):
        d = authorize(PermissionContext(tool="edit_file", input={"path": str(tmp_path / "a.md")},
                                        mode=Mode.WORKSPACE_WRITE, workspace_root=str(tmp_path)))
        assert isinstance(d, Deny), f"点名的 edit_file 该被硬拦,实际 {d}"
        assert d.reason == f"deontic:forbid:{CATEGORY_NAMED_TOOL}"
        # 拒因说清:forbid 原文(条目 X)+ 域名 + 点名禁止
        assert entry in d.message and "内容组" in d.message and "点名" in d.message
        # 其它工具照跑(write_file/run_command/web_search 都不受牵连)
        assert check_active("write_file", {"path": "a.md"}) is None
        assert check_active("run_command", {"command": "echo hi"}) is None
        assert check_active("web_search", {"query": "x"}) is None
        d2 = authorize(PermissionContext(tool="run_command", input={"command": "echo hi"},
                                         mode=Mode.WORKSPACE_WRITE, workspace_root=str(tmp_path)))
        assert isinstance(d2, Allow), f"未点名工具被误拦: {d2}"
        # FULL 模式也拦(step 6.5 免疫 FULL)
        d3 = authorize(_ctx("edit_file", {"path": "b.md"}, mode=Mode.FULL))
        assert isinstance(d3, Deny) and d3.reason == f"deontic:forbid:{CATEGORY_NAMED_TOOL}"


def test_named_nonexistent_tool_stays_soft():
    """点名不存在的工具 → 诚实降 soft(不硬拦也不炸;声称硬了拦不到任何东西=假接线)。"""
    entry = "禁止调用 frobnicate_tool"
    split = classify_forbid((entry,))
    assert split.enforceable == () and split.named == ()
    assert split.soft == (entry,)
    assert build_scope((entry,)) is None         # 不武装
    # 未武装下调用同名工具也不炸、不拦(它根本不存在,防御性锁行为)
    assert check_active("frobnicate_tool", {}) is None


def test_named_no_substring_or_fuzzy_match():
    """精确 token 匹配锁死:「editor」不命中 edit_file;「edit」也不命中(edit_file 才是
    工具名);点名 edit_file 也绝不拦叫别的名字的工具。"""
    split = classify_forbid(("不要用 editor 乱改",))
    assert split.named == () and split.enforceable == ()
    assert split.soft == ("不要用 editor 乱改",)
    assert classify_forbid(("禁止 edit",)).named == ()
    with deontic_scope(build_scope(("禁止调用 edit_file",), domain="d")):
        assert check_active("editor_file", {}) is None
        assert check_active("edit", {}) is None
        assert check_active("edit_file2", {}) is None
        assert check_active("edit_file", {}) is not None   # 本尊才拦


def test_named_plus_category_keywords_double_gate():
    """一条 forbid 点名工具 + 又含 3 类关键词 → 两种硬闸都挂(不互斥)。"""
    entry = "禁止调用 edit_file,也不得删除任何数据"
    split = classify_forbid((entry,))
    cats = {c for c, s in split.enforceable if s == entry}
    assert cats == {CATEGORY_NAMED_TOOL, CATEGORY_DELETE}
    assert split.soft == ()
    with deontic_scope(build_scope((entry,), domain="ops")):
        hit = check_active("edit_file", {"path": "x"})
        assert hit is not None and hit.category == CATEGORY_NAMED_TOOL
        hit2 = check_active("run_command", {"command": "rm -rf cache"})
        assert hit2 is not None and hit2.category == CATEGORY_DELETE
        hit3 = check_active("delete_file", {"path": "x"})
        assert hit3 is not None and hit3.category == CATEGORY_DELETE
        # 没点名、也非删除类 → 照跑
        assert check_active("write_file", {"path": "x"}) is None


def test_named_read_only_tool_still_blocked_tradeoff_locked():
    """取舍锁死:被点名的工具**哪怕只读也拦**(用户指名道姓,意图明确 > 读语义豁免);
    只读豁免对三类闸照旧成立(0 回归)。"""
    with deontic_scope(build_scope(("don't use web_search", "禁止调用 read_file"), domain="d")):
        hit = check_active("web_search", {"query": "x"})
        assert hit is not None and hit.category == CATEGORY_NAMED_TOOL
        hit2 = check_active("read_file", {"path": "a"})
        assert hit2 is not None and hit2.category == CATEGORY_NAMED_TOOL
        d = authorize(_ctx("web_search", {"query": "x"}, mode=Mode.FULL))   # FULL 也不豁免
        assert isinstance(d, Deny) and d.reason == f"deontic:forbid:{CATEGORY_NAMED_TOOL}"
        # 没被点名的只读工具不拦(只读豁免对三类闸的既有语义不动)
        assert check_active("web_fetch", {"url": "u"}) is None
    # 反向锁:只挂三类闸(无点名)时,只读工具豁免仍在
    with deontic_scope(build_scope(FINANCE_FORBID, domain="fin")):
        assert check_active("web_search", {"query": "转账手续费"}) is None


def test_named_mixed_language_entry():
    """中英混写条目:「don't use run_command 也不许联网外发」→ run_command 点名硬拦
    (连 echo 也拦:点的是工具本身)+ 外发类硬闸同挂。"""
    entry = "don't use run_command 也不许联网外发"
    split = classify_forbid((entry,))
    cats = {c for c, s in split.enforceable if s == entry}
    assert CATEGORY_NAMED_TOOL in cats and CATEGORY_EXTERNAL_SEND in cats
    assert split.soft == ()
    with deontic_scope(build_scope((entry,), domain="pr")):
        hit = check_active("run_command", {"command": "echo hi"})
        assert hit is not None and hit.category == CATEGORY_NAMED_TOOL
        assert check_active("send_email", {"to": "a@b.c"}) is not None
        assert check_active("edit_file", {"path": "x"}) is None


def test_named_runtime_toolset_is_source_of_truth():
    """点名闸以运行时真实工具集为准(forge 武装时传本次 run 的 tools.keys(),含 MCP):
    内置目录不认识的名字默认 soft;传 known_tools 后才升硬。"""
    entry = "禁止调用 mcp_broker_place_order"
    assert build_scope((entry,)) is None          # 内置目录没有 → 诚实留软
    scope = build_scope((entry,), domain="fin",
                        known_tools=("mcp_broker_place_order", "run_command"))
    assert scope is not None and scope.named == ((entry, ("mcp_broker_place_order",)),)
    with deontic_scope(scope):
        hit = check_active("mcp_broker_place_order", {"symbol": "AAPL"})
        assert hit is not None and hit.category == CATEGORY_NAMED_TOOL
        assert check_active("run_command", {"command": "echo hi"}) is None   # 在工具集≠被点名

    # scope_from_system 的 known_tools 接线口(forge 用的那条路)
    class _Sys:
        deontic_forbid = (entry,)
        deontic_domain = "fin"
    s2 = scope_from_system(_Sys(), known_tools=("mcp_broker_place_order",))
    assert s2 is not None and s2.named
    assert scope_from_system(_Sys()) is None      # 不传 = 内置目录里没有 → 不武装


# ============ ⑩ 唯一许可句定向豁免(验收判决表 3c 五句) ============

def test_whitelist_idioms_verdict_table_3c():
    """判决表 3c 五句逐一锁行为:唯一许可句点名的工具**放行**(那是用户要留的);
    「除 X 外随便用」是禁 X 本身 → 照拦。"""
    # 句1:「除了 edit_file 其他工具都不许用」= 只留 edit_file → edit_file 放行
    s1 = "除了 edit_file 其他工具都不许用"
    split1 = classify_forbid((s1,))
    assert split1.named == () and s1 in split1.soft
    assert build_scope((s1,)) is None
    # 句2:「只准用 read_file 查资料」→ read_file 放行
    s2 = "只准用 read_file 查资料"
    split2 = classify_forbid((s2,))
    assert split2.named == () and s2 in split2.soft
    # 句3:「仅允许 web_search」→ web_search 放行
    s3 = "仅允许 web_search"
    assert classify_forbid((s3,)).named == ()
    # 句4:"don't use anything except read_file" → read_file 放行
    s4 = "don't use anything except read_file"
    split4 = classify_forbid((s4,))
    assert split4.named == () and s4 in split4.soft
    # 句5:「除 edit_file 外随便用」= 禁的是 edit_file 本身(禁用语义)→ 正确拦
    s5 = "除 edit_file 外随便用"
    split5 = classify_forbid((s5,))
    assert split5.named == ((s5, ("edit_file",)),)
    with deontic_scope(build_scope((s5,), domain="d")):
        hit = check_active("edit_file", {"path": "x"})
        assert hit is not None and hit.category == CATEGORY_NAMED_TOOL
        assert check_active("write_file", {"path": "x"}) is None


def test_whitelist_idiom_more_forms_and_plain_bans_unchanged():
    """成语族其余形态放行;普通禁用句(「禁止 X」「别用 X」「don't use X」)行为不变。"""
    # 放行族:只允许 / 只许 / 仅限 / 只能用 / only use / use only
    for s in ("只允许 web_search 查资料", "只许 read_file", "仅限 read_file",
              "只能用 web_search", "only use read_file for research",
              "use only read_file here"):
        assert classify_forbid((s,)).named == (), f"唯一许可句被误硬: {s}"
    # 禁用句照拦(0 放松):
    for s, tool in (("禁止调用 edit_file", "edit_file"),
                    ("别用 run_command", "run_command"),
                    ("don't use web_search", "web_search")):
        split = classify_forbid((s,))
        assert split.named == ((s, (tool,)),), f"普通禁用句被误豁免: {s}"


def test_whitelist_idiom_edge_forms_fall_back_to_hard():
    """成语覆盖不到的边缘形态**退回误硬**(安全侧,宁紧勿松)——在此锁死并注明:
    这些句偏许可/带例外语义,但不在定向成语集内,点名工具仍拦。"""
    # 「never use edit_file except for typos」:禁的对象是点名工具本身(except 后无
    # anything/other)→ 主体是禁用,拦 edit_file 正确
    s1 = "never use edit_file except for typo fixes"
    assert classify_forbid((s1,)).named == ((s1, ("edit_file",)),)
    # 变体 "do not invoke any tool except X":动词 invoke 不在成语集 → 误硬(安全侧退回)
    s2 = "do not invoke any tool except web_search"
    assert classify_forbid((s2,)).named == ((s2, ("web_search",)),)
    # 中文变体「除了 X 之外的工具一律不用」:「不用」不在禁用动词集 → 误硬(安全侧退回)
    s3 = "除了 read_file 之外的工具一律不用"
    assert classify_forbid((s3,)).named == ((s3, ("read_file",)),)


def test_whitelist_idiom_compound_entry_and_category_interplay():
    """复合句取舍(锁 spec):一条里既显式禁用又唯一许可 → 点名闸整体退软
    (一条规则写一件事,禁用请单列条目);类别闸不受豁免影响。"""
    # 复合句:点名闸整体退软(edit_file 不再硬拦 —— 换单列条目就硬)
    s = "不许调用 edit_file,只准用 read_file"
    split = classify_forbid((s,))
    assert split.named == () and s in split.soft
    # 单列写法两条都如预期:禁用条硬、许可条软
    split2 = classify_forbid(("不许调用 edit_file", "只准用 read_file"))
    assert split2.named == (("不许调用 edit_file", ("edit_file",)),)
    # 唯一许可句 + 三类关键词并存:豁免只免点名闸,类别闸照挂
    s3 = "只允许 web_search,不许转账"
    split3 = classify_forbid((s3,))
    assert split3.named == ()
    assert (CATEGORY_TRANSACTION, s3) in split3.enforceable
    with deontic_scope(build_scope((s3,), domain="fin")):
        assert check_active("web_search", {"query": "x"}) is None      # 留的工具真放行
        assert check_active("transfer_funds", {}) is not None           # 类别闸照拦
    # 「删除」词内的「除」不误触发白名单(负向后顾):禁删除+点名条目仍全硬
    s4 = "禁止删除任何数据,也不许调用 edit_file,其他不禁"
    split4 = classify_forbid((s4,))
    assert split4.named == ((s4, ("edit_file",)),)
    assert (CATEGORY_DELETE, s4) in split4.enforceable


@pytest.mark.asyncio
async def test_forge_end_to_end_named_tool_forbid_blocks_real_run(tmp_path):
    """C-03 端到端(真 forge 咽喉,验 known_tools=tools.keys() 接线):域 forbid 点名
    edit_file → 模型试图 edit_file 被确定性拦(文件从未被改)+ 收到诚实 reason;
    未点名的 run_command 照跑。"""
    from tests.test_forge import FakeSandbox, _gw, _tok
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
    from karvyloop.coding.forge import generate_and_run
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry

    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("editor-role", identity="我是编辑", soul="尊重原稿", atom_ids=[])
    reg = BusinessDomainRegistry()
    domain = reg.create(
        name="内容组", created_by="user:h",
        value_md_raw="# 价值观\n- 不越权改稿",
        deontic=Deontic(forbid=("禁止调用 edit_file",)),
        member_query="user:h AND agent:editor-role",
    )
    cp = build_role_paradigm_prompt(rv, domain, intent="校对一下", cwd=str(tmp_path))
    assert cp is not None
    sb = FakeSandbox(str(tmp_path))
    sb.files[str(tmp_path / "draft.md")] = "v1".encode("utf-8")
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "edit_file", {"file_path": str(tmp_path / "draft.md"),
                                       "old_string": "v1", "new_string": "v2"}),
        tool_round("c2", "run_command", {"command": "echo checked"}),
        text_round("好的,不动稿子,只做了检查。"),
    ])
    gw = _gw(adapter)
    res = await generate_and_run("帮我改稿", _tok(), sb, gateway=gw,
                                 workspace_root=str(tmp_path), model_ref="p/a",
                                 system_prompt=cp)
    assert res.terminal.value == "completed"
    # 点名的 edit_file 被拦:文件内容原封不动
    assert sb.files[str(tmp_path / "draft.md")] == b"v1", "点名禁止的 edit_file 居然真改了文件"
    # 未点名的 run_command 照跑(exec_log 记 argv dict)
    assert len(sb.exec_log) == 1 and "echo checked" in str(sb.exec_log[0]), \
        f"未点名工具被误伤: {sb.exec_log}"
    # 模型收到诚实 reason(capability_denied + forbid 原文)
    fed_back = json.dumps(adapter.last_request["messages"], ensure_ascii=False)
    assert "capability_denied" in fed_back and "禁止调用 edit_file" in fed_back
    assert active_scope() is None   # scope 不泄漏


def test_named_tool_normalization_matches_catalog_convention():
    """归一与 atoms/tool_catalog 同规:'web-search' 认出 web_search(既有产品归一,非模糊);
    其余不做任何别名/翻译。"""
    split = classify_forbid(("don't use web-search",))
    assert split.named == (("don't use web-search", ("web_search",)),)
    with deontic_scope(build_scope(("don't use web-search",), domain="d")):
        assert check_active("web_search", {"query": "x"}) is not None
        assert check_active("web_fetch", {"url": "u"}) is None


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
