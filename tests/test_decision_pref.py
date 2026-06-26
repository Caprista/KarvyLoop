"""test_decision_pref — 决策接口结晶 P0 核心(docs/02 §11,楔子真正灵魂)。

AC:
- 解析:严格 JSON / 宁空勿毒(像 JSON 解析失败→[] / prose 不抽 / 剥外层 fence)
- Belief 约定:make/is_decision_pref provenance 形态
- 双关门:显式 1 次过 / 隐式 <K 不过 / 隐式 ≥K 过 / 空内容不过
- strength:显式 vs 隐式随支撑增长
- 预对齐:applies scope 过滤(域/角色/全局)+ 按 strength 排序 + 块格式(暂记标注)
- LLM 抽取:stub gateway → compile_decisions 走解析
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.decision_pref import (  # noqa: E402
    DECISION_PREF_SOURCE,
    DecisionSample,
    compile_decisions,
    initial_strength,
    is_decision_pref,
    is_high_value,
    make_decision_pref_belief,
    maybe_promote,
    parse_decision_prefs,
    parse_reconcile,
    prealign_block,
    qualifies,
    recall_decision_prefs,
    receipt_gists,
    reinforce,
    should_revoke,
    weaken,
)


# ---- 解析:宁空勿毒 ----


def test_parse_strict_json_array():
    out = parse_decision_prefs(
        '[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    assert len(out) == 1
    assert out[0]["content"] == "碰生产先写测试"
    assert out[0]["kind"] == "constraint" and out[0]["explicit"] is True
    assert out[0]["scope"] == "global"   # 缺省 scope=global


def test_parse_scope_field():
    out = parse_decision_prefs(
        '[{"content":"本域先审计","kind":"standing","scope":"domain"},'
        '{"content":"用表格","kind":"taste","scope":"weird"}]')
    assert out[0]["scope"] == "domain"
    assert out[1]["scope"] == "global"   # 非法 scope → global


def test_parse_strips_outer_fence():
    out = parse_decision_prefs('```json\n[{"content":"用表格","kind":"taste"}]\n```')
    assert len(out) == 1 and out[0]["content"] == "用表格"
    assert out[0]["explicit"] is False  # 缺省 explicit=False


def test_parse_garbage_json_returns_empty():
    # 像 JSON(以 [ 开头)却解析失败 → 宁空勿毒,绝不投毒决策画像
    assert parse_decision_prefs('[{"content": 这不是合法json') == []


def test_parse_prose_not_harvested():
    # 非 JSON prose 一律不抽(决策画像投毒比知识库更危险)
    assert parse_decision_prefs("我觉得这个用户喜欢简洁。也许吧。") == []


def test_parse_empty_and_unknown_kind():
    assert parse_decision_prefs("[]") == []
    out = parse_decision_prefs('[{"content":"x","kind":"weird"}]')
    assert out[0]["kind"] == "taste"   # 未知 kind 归 taste


def test_parse_dict_wrapper_key():
    out = parse_decision_prefs('{"prefs":[{"content":"移动端优先","kind":"standing"}]}')
    assert len(out) == 1 and out[0]["kind"] == "standing"


# ---- Belief 约定 ----


def test_make_decision_pref_belief_shape():
    b = make_decision_pref_belief("用 markdown 表格", "taste", scope="personal",
                                  domain="eng", role="设计师", evidence=["h2a-1"],
                                  strength=0.8, status="provisional", explicit=True, now=100.0)
    assert is_decision_pref(b)
    assert b.provenance["source"] == DECISION_PREF_SOURCE
    assert b.provenance["kind"] == "taste"
    assert b.provenance["evidence"] == ["h2a-1"]
    assert b.provenance["applies"] == {"domain": "eng", "role": "设计师"}
    assert b.provenance["status"] == "provisional"
    assert b.freshness_ts == 100.0
    assert b.scope == "personal"


def test_is_decision_pref_false_for_plain_belief():
    from karvyloop.schemas.cognition import Belief
    plain = Belief(content="x", provenance={"source": "conversation"}, freshness_ts=1.0,
                   scope="personal")
    assert not is_decision_pref(plain)


# ---- 双关门 ----


def test_qualifies_explicit_one_shot():
    assert qualifies({"content": "x", "explicit": True}, support_count=1)


def test_qualifies_implicit_needs_k():
    assert not qualifies({"content": "x", "explicit": False}, support_count=1)
    assert qualifies({"content": "x", "explicit": False}, support_count=2)


def test_maybe_promote_gate_and_status():
    # 显式 → 过门,provisional
    b = maybe_promote({"content": "先写测试", "kind": "constraint", "explicit": True},
                      support_count=1, scope="personal", now=1.0)
    assert b is not None and b.provenance["status"] == "provisional"
    # 隐式 1 次 → 不过门
    assert maybe_promote({"content": "x", "explicit": False}, support_count=1) is None
    # 空内容 → 不过门
    assert maybe_promote({"content": "  ", "explicit": True}, support_count=1) is None


def test_initial_strength():
    assert initial_strength(explicit=True, support_count=1) == 0.7
    assert initial_strength(explicit=False, support_count=1) == 0.4
    assert initial_strength(explicit=False, support_count=3) == pytest.approx(0.7)
    assert initial_strength(explicit=False, support_count=100) == 0.9  # 封顶


def test_is_high_value():
    hi = make_decision_pref_belief("x", "constraint", strength=0.7, now=1.0)
    lo = make_decision_pref_belief("y", "taste", strength=0.5, now=1.0)
    assert is_high_value(hi)
    assert not is_high_value(lo)


# ---- 预对齐:scope 过滤 + 排序 + 块格式 ----


def _prefs():
    return [
        make_decision_pref_belief("全局品味", "taste", strength=0.9, now=3.0),         # 全局
        make_decision_pref_belief("eng 约束", "constraint", domain="eng", strength=0.6, now=2.0),
        make_decision_pref_belief("设计师站位", "standing", role="设计师", strength=0.8, now=1.0),
    ]


def test_recall_applies_scope_filter():
    prefs = _prefs()
    # eng 域 + 设计师角色:全局 + eng约束 + 设计师站位 都适用
    got = recall_decision_prefs(prefs, domain="eng", role="设计师")
    assert len(got) == 3
    # 别的域 + 别的角色:只全局适用(eng约束/设计师站位被 applies 过滤掉)
    got2 = recall_decision_prefs(prefs, domain="sales", role="销售")
    assert len(got2) == 1 and got2[0].content == "全局品味"


def test_recall_sorted_by_strength():
    got = recall_decision_prefs(_prefs(), domain="eng", role="设计师")
    strengths = [b.provenance["strength"] for b in got]
    assert strengths == sorted(strengths, reverse=True)   # 0.9, 0.8, 0.6


def test_prealign_block_format_and_provisional_mark():
    block = prealign_block(_prefs(), domain="eng", role="设计师")
    assert "你的决策偏好" in block
    assert "[品味]" in block and "[约束]" in block and "[站位]" in block
    assert "(暂记)" in block   # provisional 标注
    # 空 → ""
    assert prealign_block([], domain="x", role="y") == ""


def test_prealign_ignores_plain_beliefs():
    from karvyloop.schemas.cognition import Belief
    plain = Belief(content="普通事实", provenance={"source": "conversation"},
                   freshness_ts=1.0, scope="personal")
    assert prealign_block([plain]) == ""   # 非决策偏好不进预对齐块


# ---- LLM 抽取(stub gateway) ----


class _StubGateway:
    def __init__(self, text: str) -> None:
        self._text = text

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, messages, tools, ref, *, system=None):
        class TextDelta:   # 名字必须是 TextDelta(compile_decisions 按 __name__ 认事件)
            def __init__(self, text):
                self.text = text
        yield TextDelta(self._text)


@pytest.mark.asyncio
async def test_compile_decisions_parses_llm_output():
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    samples = [DecisionSample(decision="REJECT", context="直接上线", reason="没测试", ts=1.0)]
    out = await compile_decisions(samples, gateway=gw, model_ref="")
    assert len(out) == 1 and out[0]["kind"] == "constraint" and out[0]["explicit"] is True


@pytest.mark.asyncio
async def test_compile_decisions_empty_samples():
    gw = _StubGateway("[]")
    assert await compile_decisions([], gateway=gw, model_ref="") == []


# ---- P1:强化 / 翻转 / 撤销(不固化你) ----


def test_reinforce_bumps_capped_and_refreshes():
    b = make_decision_pref_belief("用表格", "taste", strength=0.95, evidence=["e1"], now=1.0)
    r = reinforce(b, evidence_add=["e2"], now=2.0)
    assert r.provenance["strength"] == 1.0          # +0.1 封顶 1.0
    assert r.freshness_ts == 2.0
    assert r.provenance["evidence"] == ["e1", "e2"]  # evidence 累积


def test_weaken_lowers_strength():
    b = make_decision_pref_belief("x", "taste", strength=0.5, now=1.0)
    w = weaken(b, now=2.0)
    assert w.provenance["strength"] == pytest.approx(0.2)   # 0.5-0.3


def test_should_revoke_provisional_below_floor_only():
    lo_prov = make_decision_pref_belief("a", "taste", strength=0.1, status="provisional", now=1.0)
    lo_conf = make_decision_pref_belief("b", "taste", strength=0.1, status="confirmed", now=1.0)
    hi_prov = make_decision_pref_belief("c", "taste", strength=0.5, status="provisional", now=1.0)
    assert should_revoke(lo_prov)         # provisional 跌破下限 → 撤
    assert not should_revoke(lo_conf)     # confirmed 你拍过板,不静默删
    assert not should_revoke(hi_prov)     # 没跌破下限


def test_parse_reconcile_object_form():
    new, con = parse_reconcile('{"new":[{"content":"a","kind":"taste"}],"contradicts":[2,3]}')
    assert len(new) == 1 and con == [2, 3]


def test_parse_reconcile_array_back_compat():
    new, con = parse_reconcile('[{"content":"a","kind":"taste"}]')
    assert len(new) == 1 and con == []


def test_parse_reconcile_garbage_returns_empty():
    assert parse_reconcile('{"new": 坏掉的json') == ([], [])
    assert parse_reconcile("just prose") == ([], [])


# ---- P1:confirm(H2A 升 confirmed)纯逻辑 ----


def test_confirm_pref_upgrades_status_and_boosts():
    from karvyloop.crystallize.decision_pref import confirm_pref, find_decision_pref
    b = make_decision_pref_belief("x", "constraint", strength=0.7, status="provisional", now=1.0)
    c = confirm_pref(b, now=2.0)
    assert c.provenance["status"] == "confirmed"
    assert c.provenance["strength"] == pytest.approx(0.8)   # +0.1 boost
    assert c.freshness_ts == 2.0


def test_find_decision_pref_by_content_and_status():
    from karvyloop.crystallize.decision_pref import find_decision_pref
    bs = [make_decision_pref_belief("用 表格", "taste", status="provisional", now=1.0),
          make_decision_pref_belief("先写测试", "constraint", status="confirmed", now=1.0)]
    assert find_decision_pref(bs, "用表格").content == "用 表格"     # 归一匹配(空白无关)
    assert find_decision_pref(bs, "用表格", status="confirmed") is None
    assert find_decision_pref(bs, "先写测试", status="confirmed") is not None
    assert find_decision_pref(bs, "不存在") is None


# ---- Cut 1:回执(这条标准从你哪几次拍板来 —— 答用户视角 Q2)----

def _pref_with_evidence(evidence):
    return make_decision_pref_belief(
        "动生产数据前必须先备份", "constraint", scope="personal",
        evidence=evidence, strength=0.7, status="provisional", explicit=True)


def test_receipt_gists_from_rich_evidence():
    b = _pref_with_evidence([
        {"ts": 1.0, "decision": "REJECT", "gist": "没备份不许动生产"},
        {"ts": 2.0, "decision": "REJECT", "gist": "动生产前必须先备份"},
    ])
    gists = receipt_gists(b)
    assert gists == ["没备份不许动生产", "动生产前必须先备份"]


def test_receipt_backward_compat_old_timestamp_evidence():
    # 旧数据:evidence 只存时间戳(float)→ 没 gist → 返回空,绝不崩
    b = _pref_with_evidence([1.0, 2.0, 3.0])
    assert receipt_gists(b) == []


def test_prealign_block_renders_receipt():
    b = _pref_with_evidence([{"ts": 1.0, "decision": "REJECT", "gist": "没备份不许动生产"}])
    block = prealign_block([b])
    assert "动生产数据前必须先备份" in block        # 标准
    assert "来自你的拍板:没备份不许动生产" in block   # 回执(可核)


# ---- Step 0(b):决策召回相关性排序 + 不静默漏 ----

def test_recall_relevance_beats_strength():
    # 强但无关 vs 弱但相关:有 query 时,相关的该排前(规模大不被强度挤掉)
    strong_off = make_decision_pref_belief("输出默认用 markdown 表格", "taste", strength=0.95, now=2.0)
    weak_rel = make_decision_pref_belief("动生产数据前必须先备份", "constraint", strength=0.5, now=1.0)
    got = recall_decision_prefs([strong_off, weak_rel], query="要不要直接改生产数据库", limit=2)
    assert got[0].content == "动生产数据前必须先备份"   # 相关优先,即便强度低
    # 无 query → 回退强度排序(0 回归)
    got2 = recall_decision_prefs([strong_off, weak_rel], limit=2)
    assert got2[0].content == "输出默认用 markdown 表格"


def test_prealign_no_silent_drop_discloses_omitted():
    prefs = [make_decision_pref_belief(f"标准{i}", "taste", strength=0.5, now=float(i)) for i in range(8)]
    block = prealign_block(prefs, limit=3)
    assert block.count("- [") == 3            # 只展示 3 条
    assert "还有 5 条" in block                # 但明示漏了 5 条,不静默
