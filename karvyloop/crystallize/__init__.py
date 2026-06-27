"""crystallize — 技能结晶楔子（#2 / 唯一做深的护城河）。

规格：docs/modules/crystallize.md
里程碑：M1。状态：实现 + 等待 self-acceptance。

模块结构:
  signature  — 稳定指纹(同能力不同参数 → 同 sig,M1.5 v1.1 加月份/同义词/值分桶)
  store      — UsageStore 抽象 + InMemoryUsageStore
  observe    — 查 Trace 投影到 UsageStats(HR-7 不另埋点)
  verify     — 验证门(关 1 的关)
  crystallize — 两关判定 + 写 SKILL.md
  skill_index — sig↔name 双向索引(M1.5,recall + auto-restore 依赖)
  recall     — 快脑/慢脑 路由 + auto-restore
  improve    — 每 5 轮检测纠正写回
  evict      — 7天半衰期归档(可逆)
"""

from __future__ import annotations

from .crystallize import (
    DecisionKind,
    PromoteDecision,
    build_skill_md,
    crystallize,
    maybe_promote,
    success_rate,
    usage_score,
    write_skill_md,
    # 阈值常量
    PROMOTE_SCORE,
    MIN_SUCCESS_RATE,
    HALFLIFE_DAYS,
    MIN_RECENCY_FACTOR,
    EVICT_SCORE,
    STALE_DAYS,
    HIGH_FREQ,
    GENERALIZED_DISTINCT,
)
from .evict import days_since, evict_stale, restore
from .improve import (
    TURN_BATCH_SIZE,
    ClassifiedCorrection,
    CorrectionKind,
    KIND_TO_HEADER,
    ROLE_CRITIQUE_HEADER,
    ROLE_LESSON_HEADER,
    classify_batch,
    classify_correction,
    maybe_improve,
    remove_lesson_from_skill_md,
    write_corrections_to_skill_md,
    write_critiques_to_skill_md,
    write_lessons_to_skill_md,
)
from .lessons import (
    LESSON_KIND,
    LESSON_SYSTEM,
    distill_lessons,
    parse_lesson,
    validate_lessons,
)
from .observe import observe
from .recall import RecallHit, recall
from .signature import compute_signature, same_signature
from .skill_index import IndexEntry, SkillIndex
from .store import USAGE_DEBOUNCE_SEC, InMemoryUsageStore, UsageStore
from .verify import VerifyResult, VerifyStore
from .sqlite_store import SqliteUsageStore, SqliteVerifyStore
from .auto_suggest import SuggestHit, auto_suggest
from .atom_critic import (
    AtomSatisfaction,
    SatisfactionStore,
    evaluate_run,
    judge_quality,
    parse_quality,
    record_facts,
    record_run,
    score_achievement,
    score_efficiency,
)
from .trace_eval import (
    EVAL_FACT_KIND,
    QUALITY_KIND,
    SATISFACTION_KIND,
    evaluate_pending,
    judge_pending_quality,
    rehydrate,
)


__all__ = [
    # signature
    "compute_signature", "same_signature",
    # store
    "UsageStore", "InMemoryUsageStore", "SqliteUsageStore", "USAGE_DEBOUNCE_SEC",
    # observe
    "observe",
    # verify
    "VerifyResult", "VerifyStore", "SqliteVerifyStore",
    # crystallize
    "DecisionKind", "PromoteDecision", "maybe_promote", "crystallize",
    "build_skill_md", "write_skill_md",
    "usage_score", "success_rate",
    "PROMOTE_SCORE", "MIN_SUCCESS_RATE", "HALFLIFE_DAYS",
    "MIN_RECENCY_FACTOR", "EVICT_SCORE", "STALE_DAYS",
    "HIGH_FREQ", "GENERALIZED_DISTINCT",
    # recall
    "RecallHit", "recall",
    # skill_index (M1.5: sig↔name 双向索引)
    "SkillIndex", "IndexEntry",
    # auto_suggest (M1.5: 旁路建议)
    "SuggestHit", "auto_suggest",
    # improve
    "maybe_improve", "TURN_BATCH_SIZE",
    "CorrectionKind", "KIND_TO_HEADER", "ClassifiedCorrection",
    "classify_correction", "classify_batch",
    "write_corrections_to_skill_md",
    # evict
    "evict_stale", "restore", "days_since",
    # atom_critic (docs/02 §14: atom 层结晶裁判 = role 多维分级满意度)
    "AtomSatisfaction", "SatisfactionStore", "evaluate_run", "record_run", "record_facts",
    "score_achievement", "score_efficiency", "judge_quality", "parse_quality",
    # trace_eval (docs/40 §3: Trace-派生异步评价器,跑评分离 + §1 学回写 Trace/重启重建)
    "EVAL_FACT_KIND", "SATISFACTION_KIND", "QUALITY_KIND",
    "evaluate_pending", "judge_pending_quality", "rehydrate",
    # lessons (docs/40 §6 丙 跨-run 蒸馏 + §5 戊 忠实自进化:provisional→验证→confirm/reject)
    "LESSON_KIND", "LESSON_SYSTEM", "distill_lessons", "validate_lessons", "parse_lesson",
    "ROLE_LESSON_HEADER", "write_lessons_to_skill_md", "remove_lesson_from_skill_md",
    # improve from role critique (slice-b: 取代死的 steered_by_user 路)
    "write_critiques_to_skill_md",
]
