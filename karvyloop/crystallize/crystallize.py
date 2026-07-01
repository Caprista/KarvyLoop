"""crystallize — 结晶引擎（crystallize/crystallize.py）。

规格:docs/modules/crystallize.md §3 promote.py + §4 关 1 / 关 2 + §4 阈值是旋钮非真理
- 两道关顺序不能反:
    关 1(资格):has_verify_gate(sig) AND success_count >= 1
    关 2(价值):score >= PROMOTE_SCORE AND success_rate >= MIN_SUCCESS_RATE
                AND (generalized OR high_freq)
- 7天半衰期公式同驱动 promote 与 evict
- v1 简版:不实现 4 轮访谈异步生成(M1 v1 接受手工提供 name/manifest/body,
  后续 P1 接 AskUserQuestion);保证判定逻辑本身正确
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from karvyloop.schemas import Skill, UsageStats

from .signature import compute_signature
from .store import UsageStore
from .verify import VerifyStore


# ---- 阈值(§4 旋钮非真理)----
PROMOTE_SCORE = 3.0
MIN_SUCCESS_RATE = 0.8
HALFLIFE_DAYS = 7.0
MIN_RECENCY_FACTOR = 0.1
EVICT_SCORE = 0.5
STALE_DAYS = 30.0
HIGH_FREQ = 5  # usage_count >= HIGH_FREQ 也算价值够(无需参数泛化)
GENERALIZED_DISTINCT = 2  # 至少 2 种不同参数变体才判为可泛化


@dataclass(frozen=True)
class CrystallizeThresholds:
    """结晶灵敏度旋钮(9.4:从硬编码常量改为可配置;config.yaml `crystallize.*`)。

    docs §4"阈值是旋钮非真理":这几个值决定"技能库长多快/多严",真实用户测试期要
    能不改代码就调。默认 = 原硬编码值(向后兼容)。
      - min_usage_count:用够几次算高频价值(原 HIGH_FREQ=5)
      - min_success_rate:成功率下限(原 MIN_SUCCESS_RATE=0.8)
      - usage_debounce_sec:同 sig 去抖窗口,防单次爆发灌计数(原 USAGE_DEBOUNCE_SEC=60)
      - promote_score / generalized_distinct:价值分门槛 / 泛化所需变体数
    """
    min_usage_count: int = HIGH_FREQ
    min_success_rate: float = MIN_SUCCESS_RATE
    usage_debounce_sec: float = 60.0
    promote_score: float = PROMOTE_SCORE
    generalized_distinct: int = GENERALIZED_DISTINCT
    # 9.4:token-overlap 累积聚类门槛(0=关=精确签名;>0=同任务不同说法按 intent 重叠归并)。
    # 真机标定 0.2:6 种"平方"说法塌缩成 1 簇,而"爬虫/邮件"(只共享 python)不误并。
    # 这是 M1 测试期 #1 要调的旋钮(偏"结晶宽松"→调低更易长技能库,但过低会误并不同任务)。
    cluster_overlap_threshold: float = 0.2


DEFAULT_THRESHOLDS = CrystallizeThresholds()


# ---- 判定结果 ----

class DecisionKind(str, Enum):
    NOT_ELIGIBLE = "not_eligible"   # 关 1 没过
    NOT_YET = "not_yet"             # 关 1 过了,关 2 还不够
    READY = "ready"                 # 两关都过


@dataclass
class PromoteDecision:
    kind: DecisionKind
    reason: str = ""
    score: float = 0.0
    success_rate: float = 0.0
    generalized: bool = False
    high_freq: bool = False

    @classmethod
    def NotEligible(cls, reason: str) -> "PromoteDecision":
        return cls(kind=DecisionKind.NOT_ELIGIBLE, reason=reason)

    @classmethod
    def NotYet(cls, reason: str) -> "PromoteDecision":
        return cls(kind=DecisionKind.NOT_YET, reason=reason)

    @classmethod
    def Ready(cls, reason: str = "", **kw) -> "PromoteDecision":
        return cls(kind=DecisionKind.READY, reason=reason, **kw)


# ---- 7天半衰期评分(同驱动 promote 与 evict)----

def usage_score(stats: UsageStats, *, now: float, halflife_days: float = HALFLIFE_DAYS) -> float:
    """7天半衰期:usage_count × 0.5^(days/7),保底 0.1(不是截底到 0)。

    days 上限不限;长时间不用 → score 趋近 usage_count × 0,但 MIN_RECENCY 兜住 0.1。
    注意:不是把整个 score 兜住 0.1,而是 recency 因子兜住 0.1,再 × usage_count。
    当 usage_count==0 时,score 自然是 0(规范里的 0.1 兜底只对 recency)。
    """
    if stats.usage_count <= 0:
        return 0.0
    if not stats.last_used_at:
        return 0.0
    days = max(0.0, (now - stats.last_used_at) / 86400.0)
    recency = max(0.5 ** (days / halflife_days), MIN_RECENCY_FACTOR)
    return stats.usage_count * recency


def success_rate(stats: UsageStats) -> float:
    """成功率 = success_count / max(1, usage_count)。"""
    denom = max(1, stats.usage_count)
    return stats.success_count / denom


def _is_generalized(param_variants: list[dict], *, distinct: int = GENERALIZED_DISTINCT) -> bool:
    """同 schema 至少 `distinct` 种取值 → 判为可泛化(参数化)。

    v1 保守:key 集合一致 + 值组合有 ≥ distinct 种不同。
    整体 pv 转成 sorted tuple 做哈希;完全相同的 pv 算 1 种。
    """
    if len(param_variants) < distinct:
        return False
    # 先检查 schema 形状(所有 pv 的 key 集合一致)
    key_set = frozenset(param_variants[0].keys())
    for pv in param_variants[1:]:
        if not isinstance(pv, dict):
            return False
        if frozenset(pv.keys()) != key_set:
            return False
    # 值组合去重计数
    value_signatures = set()
    for pv in param_variants:
        sig = tuple(sorted(pv.items(), key=lambda kv: kv[0]))
        value_signatures.add(sig)
    return len(value_signatures) >= distinct


def _is_high_freq(stats: UsageStats) -> bool:
    return stats.usage_count >= HIGH_FREQ


# ---- 关 1 + 关 2 判定 ----

def maybe_promote(
    sig: str,
    store: UsageStore,
    verify: VerifyStore,
    *,
    now: Optional[float] = None,
    thresholds: "CrystallizeThresholds" = DEFAULT_THRESHOLDS,
) -> PromoteDecision:
    """两关判定。顺序不能反(spec §4 硬约束)。

    返回 PromoteDecision —— 调用方按 kind 分支(READY → 触发访谈 / 写入;
    NOT_YET → 等下次;NOT_ELIGIBLE → 不该结晶)。

    `thresholds`:结晶灵敏度旋钮(9.4 可配置;默认 = 原硬编码值,向后兼容)。
    """
    now = now if now is not None else time.time()
    stats = store.get(sig)
    if stats is None:
        return PromoteDecision.NotEligible("no usage stats yet")

    # 关 1(资格):可验证 + 至少成功 1 次
    if not verify.has_gate(sig):
        return PromoteDecision.NotEligible("no verify gate (关1)")
    if stats.success_count < 1:
        return PromoteDecision.NotEligible("never succeeded (关1)")

    # 关 2(价值):用够 + 泛化 + 成功率(均走可配置 thresholds)
    score = usage_score(stats, now=now)
    sr = success_rate(stats)
    generalized = _is_generalized(stats.param_variants, distinct=thresholds.generalized_distinct)
    high_freq = stats.usage_count >= thresholds.min_usage_count
    if score < thresholds.promote_score:
        return PromoteDecision.NotYet(f"score {score:.2f} < {thresholds.promote_score}")
    if sr < thresholds.min_success_rate:
        return PromoteDecision.NotYet(f"success_rate {sr:.2f} < {thresholds.min_success_rate}")
    if not (generalized or high_freq):
        return PromoteDecision.NotYet("not generalized and not high_freq")
    return PromoteDecision.Ready(
        reason="two gates passed",
        score=score, success_rate=sr,
        generalized=generalized, high_freq=high_freq,
    )


# ---- 结晶产物写入 SKILL.md ----

_FRONT_DATE = re.compile(r"^date:\s*(.+)$", re.MULTILINE)


def build_skill_md(
    name: str,
    description: str,
    body: str,
    *,
    signature: str,
    verify_proof: dict,
    trace_refs: list[str],
    when_to_use: str = "",
    scope: str = "user",
    arguments: Optional[list[dict]] = None,
    result_reuse: str = "dynamic",
) -> str:
    """构造 SKILL.md 文本。

    - frontmatter 必含 name/signature/description/when_to_use/verify_proof/trace_refs(scope+arguments 可选)
    - signature: 用于 sig↔name 反查(SkillIndex 从磁盘重建)
    - verify_proof + trace_refs 满足 AC5(结晶产物是合法 SKILL.md,含 verify_proof + trace_refs)
    """
    args = arguments or []
    fm_lines = [
        "---",
        f"name: {name}",
        f"signature: {signature}",
        f"description: {description}",
    ]
    if when_to_use:
        fm_lines.append(f"when_to_use: {when_to_use}")
    fm_lines.append(f"scope: {scope}")
    fm_lines.append(f"result_reuse: {result_reuse or 'dynamic'}")   # #2 §13:dynamic=命中重跑/stable=可回放
    if args:
        fm_lines.append("arguments:")
        for a in args:
            fm_lines.append(f"  - name: {a.get('name','')}")
            if a.get("type"):
                fm_lines.append(f"    type: {a['type']}")
            if a.get("description"):
                fm_lines.append(f"    description: {a['description']}")
            if a.get("required"):
                fm_lines.append("    required: true")
    fm_lines.append("verify_proof:")
    fm_lines.append(f"  passed_at: {verify_proof.get('passed_at', 0)}")
    fm_lines.append(f"  verifier: {verify_proof.get('verifier', 'manual')}")
    if verify_proof.get("note"):
        fm_lines.append(f"  note: {verify_proof['note']}")
    fm_lines.append("trace_refs:")
    if trace_refs:
        for r in trace_refs:
            fm_lines.append(f"  - {r}")
    else:
        fm_lines.append("  []")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body.rstrip())
    fm_lines.append("")
    return "\n".join(fm_lines)


def write_skill_md(skill_dir: Path, skill_md_text: str) -> Path:
    """写到 `<skill_dir>/SKILL.md`。不存在则创建目录。

    `skill_dir` 约定是 `<skills_root>/<skill_name>/`(与 registry.load_skills_dir
    布局一致);传 skills_root 时不会自动加 skill_name 子目录,需 caller 拼好。
    """
    skill_dir = Path(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(skill_md_text, encoding="utf-8")
    return p


def crystallize(
    sig: str,
    *,
    name: str,
    description: str,
    body: str,
    when_to_use: str,
    arguments: Optional[list[dict]],
    store: UsageStore,
    verify: VerifyStore,
    skills_dir: Path,
    scope: str = "user",
    now: Optional[float] = None,
    thresholds: "CrystallizeThresholds" = DEFAULT_THRESHOLDS,
    result_reuse: str = "dynamic",   # #2 §13:dynamic=命中重跑/stable=可回放(默认 dynamic,宁重跑不投毒)
) -> Skill:
    """M1 v1 简化版 crystallize(同步,参数显式传入):

    1. 调 maybe_promote(两关)
    2. 关过 → 写 SKILL.md
    3. 返回 Skill(内存态)

    异步 4 轮访谈(AskUserQuestion)是 P1 范畴;M1 v1 接受调用方已经访谈完毕
    把结果作为参数传入。这样保证:判定逻辑可测;访谈流程可以独立迭代。

    `now`:用于评分的时间戳(默认 wall clock)。测试需传可控时间。
    `thresholds`:**必须与调用方 maybe_promote 用的同一套**——否则 drive 判 ready、
    这里用默认阈值重判 not-ready 就会抛(VM 实测:配 promote_score=1.0 时 drive 说 ready、
    本函数用默认 3.0 重判 score 2.00<3.0 抛 ValueError → 配置旋钮只被半接)。
    """
    decision = maybe_promote(sig, store, verify, now=now, thresholds=thresholds)
    if decision.kind is not DecisionKind.READY:
        raise ValueError(f"sig {sig} not ready: {decision.reason}")
    proof = verify.latest_proof(sig)
    if proof is None:
        # 关 1 既然过了,这里不该为 None;防御
        raise ValueError(f"sig {sig}: verify gate passed but no proof found")
    verify_proof = {
        "passed_at": proof.at,
        "verifier": "auto" if proof.note != "manual" else "manual",
        "note": proof.note,
    }
    trace_refs = [proof.trace_ref] if proof.trace_ref else []
    skill_md = build_skill_md(
        name=name,
        description=description,
        body=body,
        signature=sig,
        verify_proof=verify_proof,
        trace_refs=trace_refs,
        when_to_use=when_to_use,
        scope=scope,
        arguments=arguments,
        result_reuse=result_reuse,
    )
    path = write_skill_md(skills_dir / name, skill_md)
    stats = store.get(sig) or UsageStats()
    # SKILL.md frontmatter 写 "user"/"domain";Skill.scope 是 Literal["personal","domain"]。
    # M1 保守:把 "user" 译作 "personal"(域外的都是 personal),保 schema 一致。
    skill_scope = "personal" if scope == "user" else "domain"
    return Skill(
        name=name,
        manifest={"name": name, "description": description,
                  "when_to_use": when_to_use, "scope": scope,
                  "arguments": arguments or [], "result_reuse": result_reuse,
                  "verify_proof": verify_proof, "trace_refs": trace_refs,
                  "path": str(path)},
        body=body,
        from_candidate=sig,
        usage=stats,
        verify_proof=verify_proof,
        scope=skill_scope,
        created_at=time.time(),
        evolved_at=time.time(),
    )


__all__ = [
    # 阈值常量
    "PROMOTE_SCORE", "MIN_SUCCESS_RATE", "HALFLIFE_DAYS",
    "MIN_RECENCY_FACTOR", "EVICT_SCORE", "STALE_DAYS",
    "HIGH_FREQ", "GENERALIZED_DISTINCT",
    # 判定
    "DecisionKind", "PromoteDecision", "maybe_promote",
    "usage_score", "success_rate",
    # 结晶
    "build_skill_md", "write_skill_md", "crystallize",
]
