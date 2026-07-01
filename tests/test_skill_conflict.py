"""test_skill_conflict — 技能×域 冲突检测(修 D4,拍 9.4-B2)。

设计:docs/31 SC-1..SC-5。

AC:
- AC1 (SC-4): 预筛无重叠 → 0 候选、0 judge 调用(省 token 见证)
- AC2 (SC-4): 有重叠 → 候选喂 judge;默认保守 judge 判冲突
- AC3 (SC-3): 缓存按 (role,域,value版本);同 key 不重判(judge_calls 不增)
- AC4 (SC-2): value 版本变 / invalidate → 重判
- AC5 (SC-3): role 颗粒度 —— 不同 role 独立判定
- AC6: 注入 LLM judge 覆盖默认(返非冲突 → 无 Conflict)
- AC7: rules_from_domain 从 deontic+value_md 抽规则
- AC8 (SC-5→D5): Conflict → resolve_conflict Proposal(kind/payload/options)
- AC9 (SC-1): 检测器不在 drive 热路径(main_loop 不 import 冲突检测器)
"""
from __future__ import annotations

import re
from pathlib import Path

from karvyloop.domain.skill_conflict import (
    RULE_FORBID,
    RULE_OBLIGE,
    RULE_VALUE,
    Conflict,
    Rule,
    SkillDomainConflictDetector,
    SkillView,
    rules_from_domain,
)
from karvyloop.karvy.proposal_registry import KIND_RESOLVE_CONFLICT, proposal_from_conflict


def _detector(judge=None):
    return SkillDomainConflictDetector(judge=judge)


SKILL_DELETE = SkillView(name="批量删库", sig="sig-del", text="when_to_use: 用户要删除数据库 / drop table / 清空")
SKILL_PACK = SkillView(name="打包 wheel", sig="sig-pack", text="when_to_use: 把项目打包成 wheel 发布")


# ---- AC1: 预筛无重叠 0 judge ----
def test_no_overlap_no_judge_call():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    out = det.detect(role="设计师", domain_id="d1", value_version="v1",
                     skills=[SKILL_PACK], rules=rules)
    assert out == []
    assert det.judge_calls == 0  # SC-4:无重叠 → 0 token


# ---- AC2: 有重叠 → 候选 → 默认判冲突 ----
def test_overlap_triggers_conflict():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    out = det.detect(role="DBA", domain_id="d1", value_version="v1",
                     skills=[SKILL_DELETE], rules=rules)
    assert len(out) == 1
    assert det.judge_calls == 1
    c = out[0]
    assert c.skill_name == "批量删库" and c.rule_type == RULE_FORBID and c.role == "DBA"


# ---- AC3: 缓存不重判 ----
def test_cache_skips_rejudge():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    a = det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    calls = det.judge_calls
    b = det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    assert a == b
    assert det.judge_calls == calls  # 命中缓存,judge 没再调


# ---- AC4: 版本变 / invalidate 重判 ----
def test_version_change_rejudges():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    calls = det.judge_calls
    det.detect(role="DBA", domain_id="d1", value_version="v2", skills=[SKILL_DELETE], rules=rules)
    assert det.judge_calls > calls  # 新版本 → 重判


def test_invalidate_rejudges():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    calls = det.judge_calls
    det.invalidate("DBA", "d1")
    det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    assert det.judge_calls > calls


# ---- AC5: role 颗粒度 ----
def test_role_granularity_independent():
    det = _detector()
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    # 同域不同 role → 独立 key,会再判
    calls = det.judge_calls
    det.detect(role="实习生", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    assert det.judge_calls > calls


# ---- AC6: 注入 LLM judge ----
def test_injected_judge_overrides():
    det = _detector(judge=lambda s, r: (False, "LLM 判定其实没冲突"))
    rules = [Rule(RULE_FORBID, "删除 数据库")]
    out = det.detect(role="DBA", domain_id="d1", value_version="v1", skills=[SKILL_DELETE], rules=rules)
    assert out == []  # judge 说不冲突
    assert det.judge_calls == 1  # 但候选确实喂了 judge(预筛过了)


# ---- AC7: rules_from_domain ----
def test_rules_from_domain():
    class _D:
        forbid = ("删除生产库",)
        oblige = ("操作前 VERIFY",)
    class _V:
        principles = ("诚实第一", "用户利益至上")
    rules = rules_from_domain(_D(), _V())
    kinds = {r.rule_type for r in rules}
    assert kinds == {RULE_FORBID, RULE_OBLIGE, RULE_VALUE}
    assert len(rules) == 4


# ---- AC8: Conflict → resolve_conflict Proposal ----
def test_conflict_to_proposal():
    c = Conflict(role="DBA", domain_id="d1", skill_name="批量删库", skill_sig="sig-del",
                 rule_type=RULE_FORBID, rule="删除生产库", reason="疑似", value_version="v1")
    p = proposal_from_conflict(c, ts=123.0)
    assert p.kind == KIND_RESOLVE_CONFLICT
    assert p.proposal_id.startswith(KIND_RESOLVE_CONFLICT + "-")
    assert p.payload["skill_name"] == "批量删库"
    assert p.payload["options"] == ["disable_in_domain", "amend_skill", "ignore"]
    assert "批量删库" in p.summary


# ---- AC9 (SC-1): 不在 drive 热路径 ----
def test_detector_not_in_drive_hotpath():
    ml = Path(__file__).resolve().parents[1] / "karvyloop" / "cli" / "main_loop.py"
    src = ml.read_text(encoding="utf-8")
    assert "skill_conflict" not in src
    assert "SkillDomainConflictDetector" not in src
