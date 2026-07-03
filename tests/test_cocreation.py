"""test_cocreation — 【共创模式】v1(docs/47 落地清单 ②③④⑤)。

锁的不变量:
② prompt 合同:采集协议(四维/每轮≤2 问/带候选/≤3 轮/复述)真进了 self_knowledge 五步法
   第 1 步 + persona 低质 intent 反问模板。
③ fuzzy_dispatch build action:LLM 分类兜住关键词门漏掉的建 agent 意图;向后兼容
   (旧 4 action 语义不变;build 不可编排 = 不走委派);classify_build_intent 允许空 roster。
④ 会话粘性:OFFERED/S1/S2 期间第二轮**无关键词**照样进状态机;"就这样吧/退出"清态;
   换话题自动摘态(不纠缠)。
⑤ 状态机:S1 澄清纪律 harness 强制(每轮≤2 问带候选、3 轮硬停);S2 草案卡 = 唯一事实源
   (diff 式修改+字段锁+每次≤3 字段,绝不整卡重生成);S1/S2 **零副作用**(无任何 registry
   写);S5 H2A ACCEPT 真建域+角色(COMMITMENT 统一 seed);跳车落地(缺项 default);
   payload 全字符串(过「改了再批」apply_payload_edits 白名单)+ 过 registry 校验。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy import cocreation as coc  # noqa: E402
from karvyloop.karvy.cocreation import (  # noqa: E402
    KIND_COCREATE_FINALIZE, MAX_CLARIFY_ROUNDS, CocreationSession,
    cocreation_take_turn, finalize_custom_draft, get_store, is_cocreation_active,
    make_cocreate_finalize_handler, maybe_offer_cocreation,
    proposal_for_cocreate_finalize, validate_draft)


# ---- 公共桩:gateway(类名必须正好 TextDelta)/ 会话 app ----

class TextDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeGateway:
    """按调用序返回预置 JSON(共创一轮可能连调:澄清器→起草器)。"""

    def __init__(self, *payloads: str) -> None:
        self._payloads = list(payloads)
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        i = min(self.calls, len(self._payloads) - 1)
        self.calls += 1
        yield TextDelta(self._payloads[i] if self._payloads else "{}")


class ExplodingRegistry:
    """零副作用哨兵:S1/S2 期间任何写入 = 立即炸。"""

    def list_active(self):
        return []

    def create(self, *a, **k):
        raise AssertionError("S1/S2 期间不许写 registry(零副作用不变量被破)")

    def get(self, *a, **k):
        return None


def _mk_app(**over):
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    state = types.SimpleNamespace(
        proposal_registry=PendingProposalRegistry(),
        ws_clients=set(),
        domain_registry=over.pop("domain_registry", ExplodingRegistry()),
        role_registry=over.pop("role_registry", ExplodingRegistry()),
        domain_store=None,
    )
    for k, v in over.items():
        setattr(state, k, v)
    return types.SimpleNamespace(state=state)


def _mk_mgr(tmp=None):
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    tmp = tmp or pathlib.Path(tempfile.mkdtemp())
    mgr = ConversationManager(ConversationStore(pathlib.Path(tmp) / "conv"))
    mgr.start()   # 默认私聊小卡(l0)
    return mgr


def _offer(app, mgr, intent="我要做个帮我管健身计划的agent", gateway=None):
    out = asyncio.run(maybe_offer_cocreation(app, mgr, intent, gateway=gateway))
    assert out, "建 agent 意图没递口"
    return out


def _turn(app, mgr, text, gateway=None):
    return asyncio.run(cocreation_take_turn(app, mgr, text, gateway=gateway))


# =========================================================================
# ② prompt 合同:采集协议 + 低质 intent 反问模板
# =========================================================================

def test_clarify_protocol_in_self_knowledge_block():
    from karvyloop.karvy.self_knowledge import self_knowledge_block
    block = self_knowledge_block()
    # 四维齐
    for dim in ("目标物", "节奏", "口味", "边界"):
        assert dim in block, f"采集协议缺维度:{dim}"
    # 问法规则:每轮≤2 问、带候选、≤3 轮硬停、复述确认
    assert "每轮最多 2 个问题" in block
    assert "候选答案" in block
    assert "最多问 3 轮" in block
    assert "复述" in block
    assert "第 4 轮" in block   # "不许第 4 轮还在问"


def test_low_quality_intent_probe_template_in_persona():
    from karvyloop.coding.persona import build_karvy_persona_prompt
    text = build_karvy_persona_prompt(cwd="/w").to_text()
    assert "澄清纪律" in text
    assert "最多 2 个反问" in text and "候选答案" in text
    assert "3 轮" in text
    assert "帮我搞一下" in text   # 低质 intent 例子在(反问模板有锚)


# =========================================================================
# ③ fuzzy_dispatch build action(向后兼容 + 空 roster 分类)
# =========================================================================

def test_parse_build_action_not_actionable():
    from karvyloop.karvy.fuzzy_dispatch import parse_fuzzy_plan
    p = parse_fuzzy_plan('{"action":"build","topic":"帮我盯论文的助手"}', [])
    assert p is not None and p.action == "build" and p.topic == "帮我盯论文的助手"
    assert not p.is_actionable(), "build 绝不能落成委派/圆桌编排"


def test_old_four_actions_unchanged():
    """向后兼容:旧 4 action 语义不破(self/ops 照旧;垃圾照拒)。"""
    from karvyloop.karvy.fuzzy_dispatch import parse_fuzzy_plan
    assert parse_fuzzy_plan('{"action":"self","topic":"x"}', []).action == "self"
    assert parse_fuzzy_plan('{"action":"ops","topic":"x"}', []).is_actionable()
    assert parse_fuzzy_plan('{"action":"weird","topic":"x"}', []) is None


def test_classify_build_intent_with_empty_roster():
    """建第一个 agent 的用户是零业务域 —— build 分类必须允许空 roster。"""
    from karvyloop.karvy.fuzzy_dispatch import classify_build_intent
    gw = FakeGateway('{"action":"build","topic":"盯论文"}')
    assert asyncio.run(classify_build_intent("我想让电脑帮我盯论文", gateway=gw)) is True
    assert gw.calls == 1
    gw2 = FakeGateway('{"action":"self","topic":"闲聊"}')
    assert asyncio.run(classify_build_intent("今天天气", gateway=gw2)) is False
    assert asyncio.run(classify_build_intent("x", gateway=None)) is False   # 宁空勿毒


def test_decompose_dispatch_empty_roster_still_none():
    """0 回归:decompose_dispatch 空 roster 快路不变(routes 不多烧 token)。"""
    from karvyloop.karvy.fuzzy_dispatch import decompose_dispatch
    gw = FakeGateway('{"action":"build","topic":"x"}')
    assert asyncio.run(decompose_dispatch("x", roster=[], gateway=gw)) is None
    assert gw.calls == 0


def test_capability_wish_heuristic():
    from karvyloop.karvy.fuzzy_dispatch import looks_like_capability_wish
    assert looks_like_capability_wish("我想让电脑每天帮我整理新闻")
    assert looks_like_capability_wish("帮我盯着这个论文方向")
    assert not looks_like_capability_wish("你好呀")
    assert not looks_like_capability_wish("")


# =========================================================================
# ④ 会话粘性
# =========================================================================

def test_offer_sets_state_and_second_turn_needs_no_keyword():
    """递口挂 OFFERED;第二轮"好啊,一起深挖"(无任何建 agent 关键词)仍在共创态。"""
    app, mgr = _mk_app(), _mk_mgr()
    _offer(app, mgr)
    assert is_cocreation_active(app, mgr)
    gw = FakeGateway(json.dumps({"enough": False, "questions": [
        {"q": "最核心要盯什么?", "candidates": ["健身打卡", "饮食记录"]}]}, ensure_ascii=False))
    reply = _turn(app, mgr, "好啊,一起深挖", gateway=gw)
    assert reply is not None, "粘性失效:第二轮无关键词掉线了"
    assert "最核心要盯什么" in reply
    assert is_cocreation_active(app, mgr)


def test_exit_clears_state():
    app, mgr = _mk_app(), _mk_mgr()
    _offer(app, mgr)
    reply = _turn(app, mgr, "算了,不建了")
    assert reply is not None and not is_cocreation_active(app, mgr)


def test_unrelated_turn_after_offer_falls_through():
    """递口后换话题 → 摘态 + None(走正常 drive,不纠缠)。"""
    app, mgr = _mk_app(), _mk_mgr()
    _offer(app, mgr)
    assert _turn(app, mgr, "今天天气怎么样") is None
    assert not is_cocreation_active(app, mgr)


def test_no_offer_when_intent_ordinary_or_session_active():
    app, mgr = _mk_app(), _mk_mgr()
    assert asyncio.run(maybe_offer_cocreation(app, mgr, "今天天气怎么样")) == ""
    _offer(app, mgr)
    assert asyncio.run(maybe_offer_cocreation(app, mgr, "再做个agent")) == "", "激活期间不重复递口"


def test_offer_via_l1_build_classification():
    """L0 关键词漏掉的说法("我想让电脑每天帮我盯论文"无 agent/角色词)→ L1 分类兜住。"""
    from karvyloop.karvy.self_knowledge import wants_build_guidance
    intent = "我想让电脑每天帮我盯最新论文"
    assert not wants_build_guidance(intent)   # 前提:L0 真漏
    app, mgr = _mk_app(), _mk_mgr()
    gw = FakeGateway('{"action":"build","topic":"盯最新论文"}')
    out = asyncio.run(maybe_offer_cocreation(app, mgr, intent, gateway=gw))
    assert out and is_cocreation_active(app, mgr)
    assert gw.calls == 1


# =========================================================================
# ⑤ 状态机:澄清纪律 harness 强制
# =========================================================================

def _enter_clarify(app, mgr, gw):
    _offer(app, mgr)
    return _turn(app, mgr, "共创", gateway=gw)


def test_questions_capped_at_two_per_round():
    """LLM 想问 5 个 → harness 截成 2 个(prompt 合同不是靠模型自觉)。"""
    app, mgr = _mk_app(), _mk_mgr()
    five = json.dumps({"enough": False, "questions": [
        {"q": f"问题{i}", "candidates": ["a", "b", "c", "d"]} for i in range(5)]},
        ensure_ascii=False)
    reply = _enter_clarify(app, mgr, FakeGateway(five))
    assert reply.count("问题") <= 3   # "1. 问题0" "2. 问题1"(+轮次行);绝无 5 问
    assert "问题2" not in reply and "问题3" not in reply
    assert "d" not in reply.split("问题1")[-1].split("\n")[1] if "问题1" in reply else True


def test_clarify_hard_stop_at_three_rounds():
    """3 轮硬停:第 4 轮绝不再问,必须出草案卡。"""
    app, mgr = _mk_app(), _mk_mgr()
    q = json.dumps({"enough": False, "questions": [
        {"q": "还想问", "candidates": ["a"]}]}, ensure_ascii=False)
    draft = json.dumps({"domain_name": "健身管理部", "values": ["以坚持为先"],
                        "forbid": [], "oblige": [], "roles": [
                            {"role_id": "fitness-coach", "nickname": "教练", "title": "健身教练",
                             "identity": "我是健身教练,负责管健身计划。", "soul": "严格但暖"}],
                        "pursuit": "排一周训练表"}, ensure_ascii=False)
    # 澄清器永远说"还想问" —— harness 必须在 3 轮后强制切草案
    gw = FakeGateway(q, q, q, q, q, draft)
    r1 = _enter_clarify(app, mgr, gw)              # 第 1 轮
    assert "还想问" in r1
    r2 = _turn(app, mgr, "答一", gateway=gw)        # 第 2 轮
    assert "还想问" in r2
    r3 = _turn(app, mgr, "答二", gateway=gw)        # 第 3 轮
    assert "还想问" in r3
    sess = list(get_store(app).values())[0]
    assert sess.rounds == MAX_CLARIFY_ROUNDS
    gw2 = FakeGateway(draft)                        # 第 4 轮:只许起草,不许再问
    sess_reply = _turn(app, mgr, "答三", gateway=gw2)
    assert "草案卡" in sess_reply, "3 轮硬停失效:第 4 轮还在问"
    assert "还想问" not in sess_reply


def test_impatient_answer_short_circuits_to_draft():
    app, mgr = _mk_app(), _mk_mgr()
    q = json.dumps({"enough": False, "questions": [{"q": "问啥", "candidates": ["a"]}]},
                   ensure_ascii=False)
    gw = FakeGateway(q, "{}")   # 第二个 payload 是垃圾 → 草案走确定性兜底也要出
    _enter_clarify(app, mgr, gw)
    reply = _turn(app, mgr, "你看着办", gateway=gw)
    assert "草案卡" in reply, "不耐烦没有立即出草案"


def test_fallback_questions_when_llm_dead():
    """无 gateway → 确定性四维兜底问句,仍守"≤2 问 + 带候选"纪律。"""
    app, mgr = _mk_app(), _mk_mgr()
    reply = _enter_clarify(app, mgr, None)
    assert "盯什么" in reply and "「" in reply   # 有候选
    assert reply.count("\n1. ") + reply.count("\n2. ") <= 2


# ---- 模板短路 ----

def test_template_goal_short_circuits_to_instantiate_card():
    app, mgr = _mk_app(), _mk_mgr()
    _offer(app, mgr, intent="帮我开个理财研究所盯行情")
    reply = _turn(app, mgr, "好,一起共创")
    assert "理财研究所" in reply and "就这样吧" in reply
    sess = list(get_store(app).values())[0]
    assert sess.draft.get("template_id") == "finance-research"
    # 确认 → 出卡,payload 只带 template_id(短路到 instantiate)
    reply2 = _turn(app, mgr, "就这样吧")
    pend = app.state.proposal_registry.pending()
    assert pend and pend[-1].kind == KIND_COCREATE_FINALIZE
    assert pend[-1].payload["template_id"] == "finance-research"
    assert not is_cocreation_active(app, mgr)   # 出卡即收口
    assert "H2A" in reply2


# =========================================================================
# ⑤ 草案卡:唯一事实源 / diff 式修改 / 字段锁 / 全字符串 payload
# =========================================================================

_DRAFT_JSON = json.dumps({
    "domain_name": "健身管理部",
    "values": ["坚持比强度重要", "身体信号优先于计划", "第四条会被截掉吗", "第五条"],
    "forbid": ["不许推销补剂"], "oblige": ["每周给一次复盘"],
    "roles": [{"role_id": "Fitness Coach!!", "nickname": "阿铁", "title": "健身教练",
               "identity": "我是健身教练,负责训练计划。", "soul": "严格但暖"}],
    "pursuit": "先排一周训练表"}, ensure_ascii=False)


def _to_draft_state(app, mgr):
    q = json.dumps({"enough": True, "questions": []}, ensure_ascii=False)
    gw = FakeGateway(q, _DRAFT_JSON)
    _offer(app, mgr)
    reply = _turn(app, mgr, "共创", gateway=gw)
    assert "草案卡" in reply
    return list(get_store(app).values())[0]


def test_draft_payload_all_strings_and_valid():
    """payload 全字符串(过「改了再批」白名单前提)+ 过 registry 校验 + harness 上限。"""
    app, mgr = _mk_app(), _mk_mgr()
    sess = _to_draft_state(app, mgr)
    assert all(isinstance(v, str) for v in sess.draft.values())
    assert validate_draft(sess.draft) == [], validate_draft(sess.draft)
    assert sess.draft["value_md"].startswith("# 价值观")
    assert sess.draft["value_md"].count("- ") <= 3      # values 截到 3 条
    assert sess.draft["role1_id"] == "fitness-coach"    # role_id 消毒成合法 id
    # 「改了再批」白名单真吃这个 payload
    from karvyloop.karvy.proposal_registry import apply_payload_edits
    prop = proposal_for_cocreate_finalize(draft=sess.draft, ts=time.time())
    edited = apply_payload_edits(prop, {"domain_name": "健身司令部", "不存在的键": "x"})
    assert edited.payload["domain_name"] == "健身司令部"
    assert "不存在的键" not in edited.payload


def test_draft_edit_is_diff_with_field_lock_never_regenerated():
    """S2 修改 = 逐字段 diff(≤3 字段/次,改过即锁);没提的字段一个不动。"""
    app, mgr = _mk_app(), _mk_mgr()
    sess = _to_draft_state(app, mgr)
    before = dict(sess.draft)
    gw = FakeGateway(json.dumps({"set": {
        "role1_soul": "先说风险,再谈计划",
        "domain_name": "健身司令部",
        "role1_identity": "改身份", "pursuit": "改第一单",   # 第 4 个字段必须被 harness 拦
        "幽灵字段": "不许注入"}}, ensure_ascii=False))
    reply = asyncio.run(cocreation_take_turn(app, mgr, "性情改成先说风险,域名叫健身司令部",
                                             gateway=gw))
    assert "🔒" in reply or "锁定" in reply
    changed = {k for k in sess.draft if sess.draft[k] != before[k]}
    assert changed <= {"role1_soul", "domain_name", "role1_identity"}   # ≤3 字段
    assert len(changed) <= 3 and "幽灵字段" not in sess.draft
    assert sess.draft["pursuit"] == before["pursuit"], "第 4 个字段被偷改 = 整卡重生成风险"
    assert {"role1_soul", "domain_name"} <= sess.locked                 # 改过即锁


def test_draft_edit_garbage_llm_changes_nothing():
    """宁空勿毒:diff 解析失败 → 草案一个字段都不动。"""
    app, mgr = _mk_app(), _mk_mgr()
    sess = _to_draft_state(app, mgr)
    before = dict(sess.draft)
    reply = asyncio.run(cocreation_take_turn(app, mgr, "帮我改得更好一点",
                                             gateway=FakeGateway("我觉得可以把性情改成…(散文)")))
    assert sess.draft == before
    assert "没解析" in reply or "不动" in reply


# =========================================================================
# ⑤ 零副作用(S1/S2)+ S5 真建 + 跳车
# =========================================================================

def test_s1_s2_zero_side_effects():
    """整个 S1/S2(递口→澄清→草案→改草案)期间:registry 零写入(哨兵注册即炸)、
    proposal 表零卡。"""
    app, mgr = _mk_app(), _mk_mgr()   # ExplodingRegistry 当哨兵
    sess = _to_draft_state(app, mgr)
    asyncio.run(cocreation_take_turn(
        app, mgr, "域名改一下", gateway=FakeGateway('{"set":{"domain_name":"新名"}}')))
    assert sess.draft["domain_name"] == "新名"
    assert app.state.proposal_registry.pending() == [], "S1/S2 就出了卡(应到 S5 才出)"
    # ExplodingRegistry.create 从没被触发(触发即 AssertionError)= 零 registry 写入


def test_s5_accept_really_creates_domain_and_roles(tmp_path):
    """定稿卡 ACCEPT → 真建出域+角色(tmp registry);COMMITMENT 由 create 统一 seed。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains, roles = BusinessDomainRegistry(), RoleRegistry(tmp_path / "roles")
    app, mgr = _mk_app(domain_registry=domains, role_registry=roles), _mk_mgr()
    _to_draft_state(app, mgr)
    reply = _turn(app, mgr, "就这样吧")
    assert "H2A" in reply and not is_cocreation_active(app, mgr)
    pend = app.state.proposal_registry.pending()
    assert len(pend) == 1 and pend[0].kind == KIND_COCREATE_FINALIZE
    assert not domains.list_active(), "出卡阶段就建域了(必须 ACCEPT 才建)"

    res = app.state.proposal_registry.decide(
        pend[0].proposal_id, "ACCEPT",
        handlers={KIND_COCREATE_FINALIZE: make_cocreate_finalize_handler(app)})
    assert res.ok, res.detail
    doms = domains.list_active()
    assert len(doms) == 1 and doms[0].name == "健身管理部"
    rv = roles.get("fitness-coach")
    assert rv is not None and "健身教练" in rv.identity
    # 三入口统一 seed:COMMITMENT 不是空 stub,是系统默认尽责契约(与 seed_commitment_md 同源)
    from karvyloop.paradigm.contract import seed_commitment_md
    commitment = (rv.path / "COMMITMENT.md").read_text(encoding="utf-8")
    assert commitment == seed_commitment_md()
    # deontic 真落地
    assert "不许推销补剂" in doms[0].deontic.forbid


def test_s5_accept_with_edits_lands_edited_values(tmp_path):
    """「改了再批」端到端:ACCEPT 时带 edits → 落地的是改后的值(字段级终改)。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains, roles = BusinessDomainRegistry(), RoleRegistry(tmp_path / "roles")
    app, mgr = _mk_app(domain_registry=domains, role_registry=roles), _mk_mgr()
    _to_draft_state(app, mgr)
    _turn(app, mgr, "就这样吧")
    pid = app.state.proposal_registry.pending()[0].proposal_id
    res = app.state.proposal_registry.decide(
        pid, "ACCEPT", edits={"domain_name": "改名后的部"},
        handlers={KIND_COCREATE_FINALIZE: make_cocreate_finalize_handler(app)})
    assert res.ok, res.detail
    assert domains.list_active()[0].name == "改名后的部"


def test_bail_out_anywhere_lands_with_default_seed(tmp_path):
    """跳车:澄清第 1 轮就"就这样吧" → 立即出卡;LLM 全挂也有确定性草案(缺项 default);
    ACCEPT 真建出来,角色 COMMITMENT 有统一 seed。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains, roles = BusinessDomainRegistry(), RoleRegistry(tmp_path / "roles")
    app, mgr = _mk_app(domain_registry=domains, role_registry=roles), _mk_mgr()
    _offer(app, mgr, intent="我要做个帮我管健身计划的agent")
    reply = _turn(app, mgr, "共创", gateway=None)    # 无 LLM:确定性兜底问句
    assert reply and is_cocreation_active(app, mgr)
    reply2 = _turn(app, mgr, "别问了,就这样吧", gateway=None)   # 跳车(LLM 也没有)
    assert "H2A" in reply2, reply2
    pend = app.state.proposal_registry.pending()
    assert len(pend) == 1
    res = app.state.proposal_registry.decide(
        pend[0].proposal_id, "ACCEPT",
        handlers={KIND_COCREATE_FINALIZE: make_cocreate_finalize_handler(app)})
    assert res.ok, res.detail
    assert len(domains.list_active()) == 1
    # 兜底草案的角色真建出来 + COMMITMENT 统一 seed
    from karvyloop.paradigm.contract import seed_commitment_md
    rid = pend[0].payload["role1_id"]
    rv = roles.get(rid)
    assert rv is not None
    assert (rv.path / "COMMITMENT.md").read_text(encoding="utf-8") == seed_commitment_md()


def test_finalize_refuses_duplicate_domain(tmp_path):
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains, roles = BusinessDomainRegistry(), RoleRegistry(tmp_path / "roles")
    payload = {"domain_name": "重复域", "value_md": "", "deontic_forbid": "",
               "deontic_oblige": "", "pursuit": "x",
               "role1_id": "dup-role", "role1_nickname": "", "role1_title": "",
               "role1_identity": "我是角色", "role1_soul": ""}
    r1 = finalize_custom_draft(dict(payload), domain_registry=domains, role_registry=roles)
    assert r1["ok"], r1
    r2 = finalize_custom_draft(dict(payload), domain_registry=domains, role_registry=roles)
    assert not r2["ok"] and "同名" in r2["reason"]


def test_validate_draft_catches_bad_role_id_and_value_md():
    bad = {"domain_name": "x", "value_md": "随便写的不带头", "deontic_forbid": "",
           "deontic_oblige": "", "pursuit": "", "role1_id": "有 空格!", "role1_identity": "i",
           "role1_nickname": "", "role1_title": "", "role1_soul": ""}
    problems = validate_draft(bad)
    assert any("价值观" in p for p in problems)
    assert any("role1_id" in p for p in problems)
    assert validate_draft({"domain_name": "x", "role1_id": "ok-id", "role1_identity": "i"}) == []


# =========================================================================
# ④+⑤ ws 接缝:粘性门 + 递口真挂进 /ws intent 路径
# =========================================================================

def test_ws_intent_cocreation_sticky_turn(monkeypatch):
    """WS 路径:共创态激活时 intent 整轮进状态机(drive 不跑),回复 drive_done 且 record_turn。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = _mk_mgr()
    app.state.conversation_manager = mgr
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    app.state.proposal_registry = PendingProposalRegistry()
    # 预置共创会话(OFFERED)—— main_loop=None 的 stub 路径在共创门**之后**,
    # 若粘性门没接进 ws,这条会掉进 "MainLoop 未注入" stub。
    store = get_store(app)
    key = coc._conv_key(mgr)
    store[key] = CocreationSession(conv_key=key, state=coc.STATE_OFFERED,
                                   intent="我要做个帮我管健身计划的agent")
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()   # snapshot
        ws.send_json({"type": "intent", "payload": {"intent": "退出"}})
        msg = ws.receive_json()
    assert msg["type"] == "drive_done"
    assert msg["payload"].get("cocreation") is True
    assert "先不建了" in msg["payload"]["text"]
    assert not is_cocreation_active(app, mgr)
    # record_turn 真记了(早返回不记 = ctx 串台)
    turns = mgr.context_view()
    assert turns and turns[-1].user_intent == "退出"


def test_ws_intent_no_session_stub_unchanged():
    """0 回归:无共创会话时 /ws intent 行为不变(main_loop=None → stub error)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "intent", "payload": {"intent": "hello"}})
        msg = ws.receive_json()
    assert msg["type"] == "drive_done" and "MainLoop" in msg["payload"]["error"]
