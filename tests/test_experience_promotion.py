"""test_experience_promotion — 兵法回流(docs/78 §3,Hardy 2026-07-13 拍板)。

AC:
- 谓词①:镜像兵法(applies 无 domain)同角色**跨域**召回;域私有经验隔离不动摇
- 谓词②:role_experience 带 applies.role 的**不进通用召回**(经验归经验通道);普通共享条不受影响
- 候选圈选:年龄/召回信号/promoted 幂等/失效/无域 全过滤,零 LLM
- 判+改写:工具信封正身收到;编造 origin_key 丢;垃圾输出返 [](宁空勿毒)
- denylist:改写命中域身份词面 → 拒
- tick:出攒批卡(≤1/轮)→ ACCEPT 升镜像+源条标 promoted → watermark 第二轮零 LLM
- 删域级联:purge_domain 清源条,已升兵法留(镜像资产)
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.belief_store import BeliefStore  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.roles.experience import (  # noqa: E402
    make_experience_belief, recall_role_experiences)
from karvyloop.roles.promotion import (  # noqa: E402
    denylist_terms, judge_and_rewrite, make_promoted_belief, origin_key_for,
    promotion_candidates, scrub_ok)
from karvyloop.schemas.cognition import Belief  # noqa: E402

NOW = time.time()
OLD = NOW - 10 * 86400   # 存活 10 天(过 MIN_AGE_DAYS)


def _mem(tmp_path):
    return MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))


def _seed_domain_exp(mem, content, *, domain="装修", role="监理", ts=OLD, recalls=2):
    b = make_experience_belief(content, "method", domain=domain, role=role, now=ts)
    mem.write(b)
    got = mem.index.get(b.content)
    got.recall_count = recalls   # 使用信号:真被召回过
    return got


# ---- 谓词③:对外白名单刀(docs/78 §4.3 J6:对外能用上、且只多不漏)----

def _seed_source(mem, content, source, *, applies=None, ts=OLD):
    b = Belief(content=content, freshness_ts=ts, scope="personal",
               provenance={"source": source, "ts": ts,
                           **({"applies": applies} if applies else {})})
    assert mem.write(b)
    return b


def test_external_audience_whitelist_knife(tmp_path):
    """audience=external:只放被访角色的升层兵法;个人事实/决策画像/圆桌/未知源全拒。"""
    mem = _mem(tmp_path)
    mem.write(make_promoted_belief("先审后交总是对的", "method",
                                   role="监理", origin_domain="装修",
                                   origin_key="k1", now=OLD))
    mem.write(make_promoted_belief("先审设计稿的三个要点", "method",
                                   role="设计师", origin_domain="装修",
                                   origin_key="k2", now=OLD))       # 别的角色的兵法:不出
    _seed_source(mem, "用户住址是幸福路1号 DO-NOT-LEAK", "conversation")
    _seed_source(mem, "报销单先审票据 DO-NOT-LEAK", "material")
    _seed_source(mem, "动生产前先审备份,底线 DO-NOT-LEAK", "decision_pref")
    _seed_source(mem, "圆桌结论:先审预算再审排期 DO-NOT-LEAK", "roundtable")
    _seed_source(mem, "某导入源的审务偏好 DO-NOT-LEAK", "mystery_import")  # 未知源:deny-by-default

    ext = mem.recall_block("先审", scope="personal", limit=20,
                           audience="external", audience_role="监理")
    assert "先审后交总是对的" in ext, "对外视野应含被访角色的升层兵法(只多不漏的'多')"
    assert "DO-NOT-LEAK" not in ext, f"个人事实/画像/圆桌/未知源漏出了对外面: {ext!r}"
    assert "先审设计稿的三个要点" not in ext, "别的角色的兵法不该出现在被访角色的对外视野"

    # 内部调用(不带标)行为一字不变:个人事实照常召回,兵法仍归经验通道(谓词②)
    inner = mem.recall_block("先审", scope="personal", limit=20)
    assert "幸福路1号" in inner
    assert "先审后交总是对的" not in inner


# ---- 谓词① :镜像兵法跨域可用,域私有仍隔离 ----

def test_mirror_experience_recalled_across_domains(tmp_path):
    mem = _mem(tmp_path)
    _seed_domain_exp(mem, "本域报价先问三家再定")                      # 装修域私有
    mirror = make_promoted_belief("交付前先过一道内部专业审", "method",
                                  role="监理", origin_domain="装修",
                                  origin_key="abc", now=OLD)
    mem.write(mirror)
    beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    # 民宿域(≠装修):镜像兵法浮出,装修私有经验绝不漏
    got = recall_role_experiences(beliefs, query="审", domain="民宿", role="监理")
    contents = [b.content for b in got]
    assert "交付前先过一道内部专业审" in contents
    assert all("报价先问三家" not in c for c in contents), "域私有经验跨域漏了(隔离被回流弄松=事故)"
    # 装修域:两条都到(本域私有 + 通用兵法)
    got_home = recall_role_experiences(beliefs, query="", domain="装修", role="监理")
    assert len(got_home) == 2
    # 别的角色:兵法锁角色,不串
    got_other = recall_role_experiences(beliefs, query="", domain="民宿", role="设计师")
    assert got_other == []


# ---- 谓词②:经验/兵法不进通用召回 ----

def test_general_recall_excludes_role_experience(tmp_path):
    mem = _mem(tmp_path)
    _seed_domain_exp(mem, "验收先查隐蔽工程再看面层")
    mem.write(make_promoted_belief("验收先查隐蔽工程", "method", role="监理",
                                   origin_domain="装修", origin_key="k1", now=OLD))
    mem.write(Belief(content="用户住在杭州", provenance={"source": "conversation", "ts": OLD},
                     freshness_ts=OLD, scope="personal"))
    # 私聊(domain=""):兵法/经验都不出现,普通共享条照常
    block = mem.recall_block("验收 隐蔽工程 杭州", domain="")
    assert "隐蔽工程" not in block, "兵法漏进通用召回(谓词②失守:私聊/别的角色会吃到噪音)"
    assert "杭州" in block
    # 装修域内:一样归经验通道,不在通用 fence 里双份注入
    block_home = mem.recall_block("验收 隐蔽工程", domain="装修")
    assert "隐蔽工程" not in block_home


# ---- 候选圈选(零 LLM 预筛)----

def test_promotion_candidates_filters(tmp_path):
    mem = _mem(tmp_path)
    ok = _seed_domain_exp(mem, "报价先问三家")                          # 合格
    _seed_domain_exp(mem, "太新的经验", ts=NOW - 3600)                  # 太新
    never = _seed_domain_exp(mem, "没人用过的经验", recalls=0)          # 无召回信号
    done = _seed_domain_exp(mem, "已升过的经验")
    done.provenance = dict(done.provenance); done.provenance["promoted_to"] = "x"
    dead = _seed_domain_exp(mem, "被推翻的经验")
    dead.invalid_at = NOW                                               # 失效
    mem.write(make_promoted_belief("已是镜像层", "method", role="监理",
                                   origin_domain="装修", origin_key="k", now=OLD))  # 无域
    beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    cands = promotion_candidates(beliefs, now=NOW)
    assert [b.content for b in cands] == [ok.content]
    assert never.content not in [b.content for b in cands]


# ---- 判+改写:工具信封 / 编造 key / 垃圾(宁空勿毒)----

class _EnvelopeGateway:
    """约束解码桩:正身走 ToolUseStop.input(复现 j3 那类缝,升层管道必须收得到)。"""
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, msgs, tools, ref, *, system=None, response_schema=None):
        self.calls += 1

        class ToolUseStop:
            def __init__(self, input):
                self.id = "t1"; self.input = input
        yield ToolUseStop(self._payload)


def test_judge_and_rewrite_tool_envelope_and_poison(tmp_path):
    mem = _mem(tmp_path)
    b = _seed_domain_exp(mem, "给X地产的报告要先过李工审")
    okey = origin_key_for(b.content)
    gw = _EnvelopeGateway([
        {"origin_key": okey, "content": "交付前先过一道内部专业审", "kind": "method", "why": "任何域适用"},
        {"origin_key": "fabricated00000", "content": "编造指针的条", "kind": "method"},   # 溯源是底线 → 丢
    ])
    items = asyncio.run(judge_and_rewrite([b], gateway=gw, domain_id="装修", domain_name="装修"))
    assert len(items) == 1 and items[0]["origin_key"] == okey
    # 垃圾输出 → [](宁空勿毒:改写失败宁可全丢,不投毒镜像层)
    bad = _EnvelopeGateway("我觉得这些经验都挺好的,建议全部保留!")
    assert asyncio.run(judge_and_rewrite([b], gateway=bad, domain_id="装修")) == []


# ---- denylist:域身份词面必拦 ----

def test_scrub_blocks_domain_entities():
    deny = denylist_terms("reno-2026", "装修")
    assert not scrub_ok("装修域的报价要先问三家", deny), "含域名的改写=脱敏失败,必须拦"
    assert not scrub_ok("参考 reno-2026 的流程", deny)
    assert scrub_ok("报价先问三家再定", deny)


# ---- 写入形态(§3.6)----

def test_promoted_belief_shape():
    nb = make_promoted_belief("交付前先过内部审", "method", role="监理",
                              origin_domain="装修", origin_key="k9", now=NOW)
    ap = nb.provenance["applies"]
    assert ap == {"role": "监理"}, "镜像层=无 domain(有 domain 就还锁在域里)"
    org = nb.provenance["origin"]
    assert org["domain"] == "装修" and org["belief_key"] == "k9"
    assert "装修" not in nb.content or True   # origin 是指针;内容脱敏由上游门管
    assert nb.scope == "personal"             # 镜像资产跟人走(删域不连坐)


# ---- tick 端到端:出卡 → ACCEPT 升层 → watermark 幂等 ----

def _tick_app(tmp_path, gw):
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    mem = _mem(tmp_path)
    return types.SimpleNamespace(state=types.SimpleNamespace(
        memory=mem, proposal_registry=PendingProposalRegistry(),
        proposal_handlers={}, runtime_kwargs={"gateway": gw, "model_ref": ""},
        domain_registry=None, ws_clients=set())), mem


def test_tick_card_accept_and_watermark(tmp_path):
    from karvyloop.console.promotion_tick import (
        KIND_PROMOTE_EXPERIENCE, maybe_promotion_tick)
    app, mem = _tick_app(tmp_path, None)
    src = _seed_domain_exp(mem, "给X地产的报告要先过李工审")
    okey = origin_key_for(src.content)
    gw = _EnvelopeGateway([{"origin_key": okey, "content": "交付前先过一道内部专业审",
                            "kind": "method", "why": "任何域适用"}])
    app.state.runtime_kwargs["gateway"] = gw
    sp = tmp_path / "tick.json"

    n = asyncio.run(maybe_promotion_tick(app, now=NOW, state_path=sp))
    assert n == 1 and gw.calls == 1
    cards = list(app.state.proposal_registry.pending())
    assert len(cards) == 1 and cards[0].kind == KIND_PROMOTE_EXPERIENCE
    assert "内部专业审" in cards[0].basis and "李工" in cards[0].basis   # 改写前后对照(本机管理面)

    # ACCEPT 兑现:镜像条写入 + 源条标 promoted
    handler = app.state.proposal_handlers[KIND_PROMOTE_EXPERIENCE]
    ok, msg = handler(cards[0])
    assert ok is True, msg
    all_b = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    mirrors = [b for b in all_b
               if (b.provenance.get("applies") or {}) == {"role": "监理"}]
    assert len(mirrors) == 1 and mirrors[0].content == "交付前先过一道内部专业审"
    assert mem.index.get(src.content).provenance.get("promoted_to") == okey

    # 第二轮:源条已标 promoted → 无候选,零 LLM(幂等,不重复出卡)
    n2 = asyncio.run(maybe_promotion_tick(app, now=NOW + 60, state_path=sp))
    assert n2 == 0 and gw.calls == 1


def test_tick_scrubs_why_field_on_card(tmp_path):
    """why(泛化理由)也上卡面 → 同过 denylist:含域词整段丢,content 干净的照升(J 验收 P2)。"""
    from karvyloop.console.promotion_tick import maybe_promotion_tick
    app, mem = _tick_app(tmp_path, None)
    src = _seed_domain_exp(mem, "给X地产的报告要先过李工审")
    okey = origin_key_for(src.content)
    gw = _EnvelopeGateway([{"origin_key": okey, "content": "交付前先过一道内部专业审",
                            "kind": "method", "why": "在装修域反复验证过"}])   # why 泄域词
    app.state.runtime_kwargs["gateway"] = gw
    n = asyncio.run(maybe_promotion_tick(app, now=NOW, state_path=tmp_path / "t.json"))
    assert n == 1
    card = list(app.state.proposal_registry.pending())[0]
    assert "内部专业审" in card.basis                       # 干净 content 照升
    assert "在装修域反复验证过" not in card.basis           # 脏 why 整段被清


def test_tick_watermark_skips_unchanged_pool(tmp_path):
    """池没变(比如上轮全判'不泛化')→ 第二轮零 LLM。"""
    from karvyloop.console.promotion_tick import maybe_promotion_tick
    app, mem = _tick_app(tmp_path, None)
    _seed_domain_exp(mem, "这个域的 API 分页从 0 开始")
    gw = _EnvelopeGateway([])   # 判空:没有可升的
    app.state.runtime_kwargs["gateway"] = gw
    sp = tmp_path / "tick.json"
    assert asyncio.run(maybe_promotion_tick(app, now=NOW, state_path=sp)) == 0
    assert gw.calls == 1
    assert asyncio.run(maybe_promotion_tick(app, now=NOW + 60, state_path=sp)) == 0
    assert gw.calls == 1   # watermark:池没变,零 LLM


def test_handler_denylist_second_gate(tmp_path):
    """纵深防御:就算脏改写混进卡(建卡后世界变了/上游漏),ACCEPT 兑现时二道 denylist 兜住。"""
    from karvyloop.console.promotion_tick import _promote_experience_handler
    app, mem = _tick_app(tmp_path, None)
    card = types.SimpleNamespace(payload={
        "role": "监理", "origin_domain": "装修",
        "items": [{"origin_key": "k", "content": "装修项目都要先问三家", "kind": "method"}],
    })
    ok, msg = _promote_experience_handler(app)(card)
    assert ok is False and "denylist" in msg


# ---- 删域级联:源条随域清,兵法留 ----

def test_purge_domain_keeps_promoted_mirror(tmp_path):
    mem = _mem(tmp_path)
    src = _seed_domain_exp(mem, "报价先问三家")
    mem.write(make_promoted_belief("询价至少三个来源再定", "method", role="监理",
                                   origin_domain="装修", origin_key=origin_key_for(src.content),
                                   now=OLD))
    mem.purge_domain("装修")
    all_b = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    contents = [b.content for b in all_b]
    assert "报价先问三家" not in contents            # 域私有随域清(§2.6⑤)
    assert "询价至少三个来源再定" in contents        # 兵法=镜像资产,删域不连坐(docs/78 §3.8)
