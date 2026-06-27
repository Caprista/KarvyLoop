"""lessons — 跨-run 对比式经验蒸馏（crystallize/lessons.py，docs/40 §6 丙「学回写做深」）。

从"每条 run 单独评分"升到"**跨 run 蒸馏规律**":对同一子目标(sig),对比**满意的**几次执行
和**不满意的**几次,让 role 提炼出**一条**可复用规则(ExpeL 的对比式经验蒸馏 / Generative
Agents 的高阶反思)。规则**写回 Trace**(自反"学",`LESSON_KIND`)+ 折进技能 SKILL.md。

设计(承袭甲.1/乙.1 的纪律):
- **只从 Trace 派生**:对比材料(intent/产出/方法)从 Trace 按 trace_ref 取;**慢侧**(daily_poll)。
- **context engineering**:对比材料过 `clip_to_tokens`(token 预算 + HR-9 截断),不裸截。
- **宁空勿毒**:`parse_lesson` 严格 JSON + sanitize 成安全单行,绝不结构性投毒 SKILL.md。
- **成本封顶 + 水位**:每轮最多蒸 `LESSON_LIMIT` 个 sig;同一 sig 距上次蒸馏**新增足够样本**才再蒸
  (水位存样本数,回写进 Trace 的 lesson payload → 重启可重建,不重复蒸/不烧钱)。
- **重启安全**:lesson 在 Trace,水位由 rehydrate 从 Trace 重建。
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Optional

from .atom_critic import SatisfactionStore, sanitize_critique


# 评价结果回写 Trace 的事件类型(跨-run 规律,慢侧)
LESSON_KIND = "lesson"

# 对比阈值:overall ≥ HIGH = 满意;≤ LOW = 不满意(achievement 主导,1.0 满 / 0.5 半)
LESSON_HIGH_THRESH = 0.8
LESSON_LOW_THRESH = 0.6
# 触发:该 sig 至少这么多样本、且高/低两组都非空才值得对比蒸馏
LESSON_MIN_SAMPLES = 4
# 同一 sig 距上次蒸馏新增这么多样本才再蒸(防反复烧钱蒸同一批)
LESSON_NEW_SAMPLES = 4
# 每轮慢侧 tick 最多蒸几个 sig(成本尖峰封顶)
LESSON_LIMIT = 5
# 每组取几条代表 run 进对比材料(最近的优先)
_LESSON_RUNS_PER_GROUP = 3
# 每条 run 产出的 token 预算(走 clip_to_tokens)
_LESSON_OUTPUT_TOKENS = 300


LESSON_SYSTEM = (
    "你是 role,在复盘**同一个子任务**的多次执行——有几次做得令人满意,有几次不满意。\n"
    "对比满意组和不满意组,提炼出**一条**让这类子任务以后做得更好的、**具体可操作**的规则\n"
    "(针对做法/步骤/工具用法,不要空话、不要复述任务)。\n"
    '严格只输出一个 JSON 对象:{"lesson": "一条具体规则"};提炼不出 → {"lesson": ""}。'
)


def parse_lesson(text: str) -> str:
    """宁空勿毒:严格解析 → 安全单行规则。解析失败/非法 → ""(绝不把 prose/结构性 md 写进库)。"""
    from .atom_critic import _first_json_object
    blob = _first_json_object((text or "").strip())
    if not blob:
        return ""
    try:
        obj = json.loads(blob)
    except Exception:
        return ""
    if not isinstance(obj, dict):
        return ""
    return sanitize_critique(obj.get("lesson", ""))


async def judge_lesson(material: str, *, gateway, model_ref: str = "") -> str:
    """跨-run 蒸馏的 LLM 调用(返**原文**,distill_lessons 再 parse_lesson)。

    材料整体过 `clip_to_tokens`(context engineering 预算);token_source 打 lesson_distill 标。
    无 gateway / 失败 → ""(宁空勿毒,绝不拖垮)。
    """
    if gateway is None:
        return ""
    from karvyloop.context.budget import clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    mat, _ = clip_to_tokens(material or "", 1500)
    out = ""
    try:
        with token_source("lesson_distill"):
            async for ev in gateway.complete(
                [{"role": "user", "content": mat}], [], ref,
                system=SystemPrompt(static=[LESSON_SYSTEM]),
            ):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception:
        return ""
    return out


def _run_by_ref(trace, task_id: str, ref: str):
    """从 Trace 取回那条 atom_run。**用样本带来的 task_id 定位**(executor 发的 trace_ref 是
    `trace://atom/ts`,不含 task_id;真实 task 是它写进 Trace 时的 drive task,记在样本上),
    再在该 task 内按 trace_ref 精确匹配。这是对抗验收 CRITICAL 的修复(真实数据形态)。"""
    if not task_id or not ref:
        return None
    try:
        for run in trace.query_atom_runs(task_id):
            if getattr(run, "trace_ref", "") == ref:
                return run
    except Exception:
        return None
    return None


def _run_brief(trace, task_id: str, ref: str) -> Optional[str]:
    """一条 run 的简报(子任务 + 产出,产出走 context engineering 截断)。"""
    from karvyloop.context.budget import clip_to_tokens
    run = _run_by_ref(trace, task_id, ref)
    if run is None:
        return None
    intent = run.input.get("intent", "") if isinstance(getattr(run, "input", None), dict) else ""
    out = getattr(run, "output", None)
    out_text = str(out.get("text", "") or "") if isinstance(out, dict) else (str(out) if out else "")
    body, _ = clip_to_tokens(out_text, _LESSON_OUTPUT_TOKENS)
    intent_c, _ = clip_to_tokens(intent, 120)
    return f"子任务:{intent_c}\n产出:{body}"


def _build_material(trace, high_pairs: list, low_pairs: list) -> Optional[str]:
    """组对比材料(满意组 vs 不满意组)。pairs=[(task_id, ref), ...]。任一组取不到内容 → None。"""
    highs = [b for b in (_run_brief(trace, t, r) for t, r in high_pairs) if b]
    lows = [b for b in (_run_brief(trace, t, r) for t, r in low_pairs) if b]
    if not highs or not lows:
        return None
    h = "\n\n".join(f"[满意 {i+1}]\n{b}" for i, b in enumerate(highs))
    l = "\n\n".join(f"[不满意 {i+1}]\n{b}" for i, b in enumerate(lows))
    return f"## 满意的执行\n{h}\n\n## 不满意的执行\n{l}"


def distill_lessons(trace, satisfaction: SatisfactionStore, *, judge,
                    skills_dir: Path, skill_index=None,
                    limit: int = LESSON_LIMIT, clock=None) -> int:
    """**慢侧**:对有足够样本 + 高/低对比 + 距上次蒸馏新增足够样本的 sig,跨-run 蒸出一条规则,
    写回 Trace(LESSON_KIND)+ 折进 SKILL.md。返回本轮蒸出的规则条数。

    `judge`:同步 callable `(material) -> lesson_text`(由持 gateway 的层注入)。无 → 0。
    """
    if trace is None or satisfaction is None or judge is None:
        return 0
    clk = clock or _time.time
    n = 0
    attempts = 0
    for sig in satisfaction.sigs():
        if attempts >= limit:
            break
        samples = satisfaction.samples(sig)        # oldest → newest
        if len(samples) < LESSON_MIN_SAMPLES:
            continue
        # 水位:距上次蒸馏新增足够样本才再蒸(防反复烧同一批)
        if len(samples) - satisfaction.lesson_watermark(sig) < LESSON_NEW_SAMPLES:
            continue
        highs = [(s.task_id, s.trace_ref) for s in samples
                 if s.overall >= LESSON_HIGH_THRESH and s.trace_ref and s.task_id]
        lows = [(s.task_id, s.trace_ref) for s in samples
                if s.overall <= LESSON_LOW_THRESH and s.trace_ref and s.task_id]
        if not highs or not lows:
            continue                                # 无对比(全好或全坏)→ 不蒸
        material = _build_material(trace, highs[-_LESSON_RUNS_PER_GROUP:],
                                   lows[-_LESSON_RUNS_PER_GROUP:])
        if not material:
            continue                                # 取不到 run 内容(数据形态不对)→ 不烧 judge
        attempts += 1
        try:
            lesson = parse_lesson(judge(material))
        except Exception:
            continue
        # 水位前移:无论蒸出与否都前移,避免下轮对同一批反复尝试烧钱(没蒸出=这批没规律)。
        satisfaction.set_lesson_watermark(sig, len(samples))
        if not lesson or _latest_lesson(trace, sig) == lesson:
            continue                                # 没规律 / 和上次同一条 → 不重复回写 Trace(防膨胀)
        _writeback_lesson(trace, sig, lesson, len(samples), clk)
        _fold_into_skill(sig, lesson, skills_dir=skills_dir, skill_index=skill_index, clock=clk)
        n += 1
    return n


def _latest_lesson(trace, sig: str) -> str:
    """该 sig 在 Trace 里最近一条规律(用于去重,防同一条反复回写膨胀)。"""
    try:
        entries = trace.query(f"lesson:{sig}", kind=LESSON_KIND)
    except Exception:
        return ""
    return str(entries[-1].payload.get("lesson", "") or "") if entries else ""


def _writeback_lesson(trace, sig: str, lesson: str, n_samples: int, clk) -> None:
    """规律回写 Trace(自反"学" + 重启重建水位源)。失败不拖垮。"""
    try:
        from karvyloop.cognition.trace import TraceEntry
        # task_id 用 sig 占位(lesson 是跨-run 的,不属某次 task);重建只看 payload。
        trace.append(TraceEntry(
            task_id=f"lesson:{sig}", kind=LESSON_KIND,
            payload={"sig": sig, "lesson": lesson, "n_samples": int(n_samples)},
            ts=clk(), source="lessons",
        ))
    except Exception:
        pass


def _fold_into_skill(sig: str, lesson: str, *, skills_dir: Path, skill_index, clock) -> None:
    """把规律折进对应技能的 SKILL.md(已结晶才有);没技能 → 只在 Trace 留着。"""
    if skill_index is None:
        return
    try:
        name = skill_index.name_for_sig(sig)
    except Exception:
        name = None
    if not name:
        return
    try:
        from .improve import write_lessons_to_skill_md
        write_lessons_to_skill_md(Path(skills_dir) / name / "SKILL.md", [lesson],
                                  now=clock())
    except Exception:
        pass


__all__ = [
    "LESSON_KIND", "LESSON_SYSTEM",
    "LESSON_HIGH_THRESH", "LESSON_LOW_THRESH", "LESSON_MIN_SAMPLES",
    "LESSON_NEW_SAMPLES", "LESSON_LIMIT",
    "parse_lesson", "distill_lessons", "judge_lesson",
]
