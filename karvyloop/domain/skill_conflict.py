"""skill_conflict — 技能 × 业务域 冲突检测(修 D4,M3+ 拍 9.4-B2)。

设计:docs/31。修 H1 安全漏洞:9.2b 的 value.md/deontic **只注入慢脑**,快脑(结晶技能)
命中**绕过域治理** —— 在"合规至上"域里,一个全局技能可能违反该域价值观/硬护栏。

取舍(用户 2026-06-18 拍板):**运行时不拦**(token 太贵 + 跨域复用是常态)→ 改
**变化触发 + 廉价预筛 + 仅候选喂判定**的异步检测,以 role 为颗粒度,发现冲突出
resolve_conflict PROPOSE 让用户处置(禁用/改/忽略)。

不变量(docs/31 SC-1..SC-5):
- SC-1 运行时不拦:本检测器**不在 drive 热路径**(由变化触发的异步调用)。
- SC-2 变化触发:仅 ①新结晶 skill ②role 入职域 ③域 value.md 改 时查该 (role,域)。
- SC-3 role 颗粒度:按 (role, 域, value版本) 聚合判定 + 缓存。
- SC-4 cheap-gate-first:先关键词预筛挑候选,**仅候选**喂判定(judge 可注入 LLM;
  绝大多数 (skill,rule) 对 0 token —— 预筛无重叠直接跳过)。
- SC-5 发现冲突 → 出 resolve_conflict Proposal(本模块产 Conflict,karvy 侧转 Proposal)。

本模块**纯治理逻辑**:输入 duck-typed(SkillView / Rule),不 import karvy;Conflict
自带 `to_proposal_payload()` / `summary()`,由 karvy 侧 `proposal_from_conflict` 转 Proposal
(避免 domain → karvy 反向耦合)。
"""
from __future__ import annotations

import dataclasses
import re
from typing import Callable, Dict, List, Optional, Tuple

# 规则类型
RULE_FORBID = "forbid"
RULE_OBLIGE = "oblige"
RULE_VALUE = "value"

# 预筛分词:CJK/拉丁混排 → 按标点/空白切,保留 len>=2 的词条做子串命中
_SPLIT = re.compile(r"[\s,;.，；、/|:：。!！?？()（）\"'`\-]+")


@dataclasses.dataclass(frozen=True)
class SkillView:
    """检测输入:一个技能的可搜索视图(调用方从 Skill 抽 when_to_use+body)。"""
    name: str
    sig: str
    text: str  # when_to_use + body,供关键词预筛


@dataclasses.dataclass(frozen=True)
class Rule:
    """检测输入:域的一条治理规则(来自 deontic.forbid/oblige 或 value.md principle)。"""
    rule_type: str  # RULE_FORBID / RULE_OBLIGE / RULE_VALUE
    text: str


@dataclasses.dataclass(frozen=True)
class Conflict:
    """检出的 (skill × rule) 冲突 —— 转 resolve_conflict Proposal 给用户处置。"""
    role: str
    domain_id: str
    skill_name: str
    skill_sig: str
    rule_type: str
    rule: str
    reason: str
    value_version: str

    def summary(self) -> str:
        return (
            f"技能「{self.skill_name}」可能违反域「{self.domain_id}」"
            f"的{_rule_label(self.rule_type)}「{self.rule}」({self.role})"
        )

    def to_proposal_payload(self) -> dict:
        """resolve_conflict Proposal 的 payload(含处置选项)。"""
        return {
            "role": self.role,
            "domain_id": self.domain_id,
            "skill_name": self.skill_name,
            "skill_sig": self.skill_sig,
            "rule_type": self.rule_type,
            "rule": self.rule,
            "reason": self.reason,
            "value_version": self.value_version,
            # docs/31 SC-5:用户处置选项
            "options": ["disable_in_domain", "amend_skill", "ignore"],
        }


def _rule_label(rule_type: str) -> str:
    return {RULE_FORBID: "禁止项", RULE_OBLIGE: "强制项", RULE_VALUE: "价值观"}.get(rule_type, "规则")


def _is_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _terms(text: str) -> List[str]:
    """切词条做子串预筛。CJK 无空格 → 整词难命中(如"删除生产数据库"⊄"删除数据库"),
    故对 CJK 词条**补 2 字滑窗**(bigram),让"删除"这种共享子串能命中。
    宽召回 OK:预筛只挑候选,真假由 judge + 用户处置(SC-5)。"""
    base = [t for t in _SPLIT.split(text.lower()) if len(t) >= 2]
    out = set(base)
    for t in base:
        if _is_cjk(t):
            for i in range(len(t) - 1):
                bg = t[i:i + 2]
                if _is_cjk(bg):
                    out.add(bg)
    return list(out)


def _overlap(skill_text_lower: str, rule_text: str) -> bool:
    """廉价预筛(SC-4):规则任一词条是技能文本子串 → 疑似(进候选)。"""
    return any(term in skill_text_lower for term in _terms(rule_text))


# judge 协议:(skill, rule) -> (is_conflict, reason)。默认保守判定;可注入 LLM judge。
Judge = Callable[[SkillView, Rule], Tuple[bool, str]]


def _default_judge(skill: SkillView, rule: Rule) -> Tuple[bool, str]:
    """默认判定(无 LLM):预筛已过 = 技能文本沾了规则关键词 → 保守判冲突。

    SC-5 哲学:冲突只出 PROPOSE 不拦,false-positive 由用户一键忽略 —— 宁可多问一次,
    不漏掉"合规域里用了违规技能"。需要更细 → 注入 LLM judge。
    """
    return True, f"技能用途文本命中{_rule_label(rule.rule_type)}关键词,疑似冲突,请确认"


class SkillDomainConflictDetector:
    """变化触发的 (role,域) 冲突检测器(SC-1..SC-5)。

    `judge` 注入 LLM 判定核(默认保守启发式,离线可测);`detect` 按
    (role,域,value版本) 缓存 verdict —— value.md 没改就不重判(省 token,SC-3/SC-4)。
    """

    def __init__(self, judge: Optional[Judge] = None) -> None:
        self._judge: Judge = judge or _default_judge
        self._cache: Dict[Tuple[str, str, str], List[Conflict]] = {}
        self.judge_calls = 0  # SC-4 观测:候选喂判定次数(0 重叠的对不计 → 见证省 token)

    def detect(
        self,
        *,
        role: str,
        domain_id: str,
        value_version: str,
        skills: List[SkillView],
        rules: List[Rule],
        use_cache: bool = True,
    ) -> List[Conflict]:
        """检 (role,域) 下技能集 × 规则集的冲突。两段:预筛 → 候选判定。"""
        key = (role, domain_id, value_version)
        if use_cache and key in self._cache:
            return self._cache[key]

        conflicts: List[Conflict] = []
        for skill in skills:
            sk_lower = skill.text.lower()
            for rule in rules:
                if not _overlap(sk_lower, rule.text):
                    continue  # SC-4:预筛无重叠 → 0 判定(绝大多数对走这)
                self.judge_calls += 1
                is_conflict, reason = self._judge(skill, rule)
                if is_conflict:
                    conflicts.append(Conflict(
                        role=role, domain_id=domain_id,
                        skill_name=skill.name, skill_sig=skill.sig,
                        rule_type=rule.rule_type, rule=rule.text,
                        reason=reason, value_version=value_version,
                    ))
        self._cache[key] = conflicts
        return conflicts

    def invalidate(self, role: str, domain_id: str) -> None:
        """value.md 改 / role 重入职 时清该对缓存(SC-2 变化触发)。"""
        for k in [k for k in self._cache if k[0] == role and k[1] == domain_id]:
            del self._cache[k]


def rules_from_domain(deontic, value_md) -> List[Rule]:
    """从域的 deontic + value.md 抽规则集(duck-typed:deontic.forbid/oblige、value_md.principles)。"""
    rules: List[Rule] = []
    for f in getattr(deontic, "forbid", ()) or ():
        rules.append(Rule(RULE_FORBID, str(f)))
    for o in getattr(deontic, "oblige", ()) or ():
        rules.append(Rule(RULE_OBLIGE, str(o)))
    for p in getattr(value_md, "principles", ()) or ():
        rules.append(Rule(RULE_VALUE, str(p)))
    return rules


__all__ = [
    "SkillView", "Rule", "Conflict", "Judge",
    "SkillDomainConflictDetector", "rules_from_domain",
    "RULE_FORBID", "RULE_OBLIGE", "RULE_VALUE",
]
