"""技能修订闭环(Trace-conditioned skill revision)验收测试。

覆盖(任务书五点):
  ① 召回带偏好段:improve.py 写回的 ## Preferences/## Corrections 段被 recall 抽出、
     重跑组装(compose_rerun_context)单独标注"必须遵守"带上,Changelog 剥掉;
  ② 修订触发阈值:好信号不触发 / 坏信号触发(信号从 Trace 的 eval_fact 真写入 →
     evaluate_pending 真评出来,不绕过造数据);
  ③ 宁空勿毒:LLM 返垃圾 → SKILL.md 原文一字不动 + 水位前移不重烧;
  ④ Changelog 落盘格式:日期 + 触发 trace refs + 改了什么;
  ⑤ 小改自动落 / 大改出 revise_skill H2A 卡的分界(token-overlap,无向量)。
LLM 层 stub;Trace 层走真写入(TraceStore.append → evaluate_pending → SatisfactionStore)。
"""

from __future__ import annotations

import re
from pathlib import Path

from karvyloop.cognition import TraceEntry, TraceStore
from karvyloop.crystallize import (
    EVAL_FACT_KIND,
    REVISION_KIND,
    KIND_REVISE_SKILL,
    SatisfactionStore,
    SkillIndex,
    apply_revision_proposal,
    build_revision_proposal,
    build_skill_md,
    compose_rerun_context,
    evaluate_pending,
    is_major_revision,
    needs_revision,
    parse_revision,
    recall,
    revise_underperforming,
    split_body_guidance,
    write_skill_md,
)
from karvyloop.runtime.main_loop import MainLoop
from karvyloop.schemas import AtomRun


SIG = "sig-revise-0001"
NAME = "monthly_report"
INTENT = "总结上月销售报表"
BODY = (
    "## Goal\n总结上月销售报表\n\n"
    "## Steps(上次证明可行的打法)\n"
    "1. 打开报表工具\n"
    "2. 导出上月数据\n"
    "3. 汇总发送邮件"
)


def _write_skill(skills_dir: Path, *, body: str = BODY, name: str = NAME,
                 sig: str = SIG, when: str = INTENT) -> Path:
    text = build_skill_md(
        name=name, description=INTENT, body=body,
        signature=sig, when_to_use=when, scope="user",
        verify_proof={"passed_at": 1.0, "verifier": "auto", "note": "ok"},
        trace_refs=["t1"],
    )
    return write_skill_md(skills_dir / name, text)


# ============ ① 召回带偏好/纠正段 ============

def test_split_body_guidance_separates_improve_sections():
    body = (BODY
            + "\n\n## Preferences\n\n- (2026-07-01) [preference] 输出一律用 markdown 表格\n"
            + "\n## Corrections\n\n- (2026-07-02) [correction] 上次把六月算成了五月,注意月份边界\n"
            + "\n## Changelog\n\n- (2026-07-02) [revision:auto] traces: t1 — 调整步骤\n")
    method, guidance = split_body_guidance(body)
    assert "## Steps" in method and "打开报表工具" in method
    assert "## Preferences" in guidance and "markdown 表格" in guidance
    assert "## Corrections" in guidance and "月份边界" in guidance
    # 指导段不留在方法里;Changelog 是审计痕,两边都不带(喂 LLM 是噪声)
    assert "markdown 表格" not in method
    assert "Changelog" not in method and "Changelog" not in guidance


def test_recall_hit_carries_guidance_and_rerun_context_labels_it(tmp_path):
    skills_dir = tmp_path / "skills"
    body = BODY + "\n\n## Preferences\n\n- (2026-07-01) [preference] 输出一律用 markdown 表格\n"
    _write_skill(skills_dir, body=body)
    hit = recall(INTENT, skills_dir=skills_dir, scope="user")
    assert hit is not None and hit.name == NAME
    # RecallHit.guidance 带上写回段
    assert "markdown 表格" in hit.guidance
    ctx = compose_rerun_context(hit, INTENT)
    # 重跑上下文:方法段 + 单独标注"必须遵守"的指导段 + 当前任务
    assert "已有方法" in ctx and "打开报表工具" in ctx
    assert "必须遵守" in ctx and "markdown 表格" in ctx
    assert f"[当前任务]\n{INTENT}" in ctx
    # 偏好 bullet 只出现一次(是"拆出来"不是"复制一份")
    assert ctx.count("markdown 表格") == 1


def test_drive_guided_rerun_feeds_guidance_to_slow_brain(tmp_path):
    """端到端:dynamic 命中重跑时,慢脑收到的 intent 里有标注过的纠正段。"""
    skills_dir = tmp_path / "skills"
    body = BODY + "\n\n## Corrections\n\n- (2026-07-02) [correction] 上次月份边界算错了\n"
    _write_skill(skills_dir, body=body)
    ml = MainLoop(skills_dir=skills_dir)
    ml.bootstrap()
    seen: list[str] = []

    def slow_brain(intent: str):
        seen.append(intent)
        return "done", AtomRun(atom_id="a1", input={"intent": INTENT}, output={"text": "done"},
                               success=True, tool_calls=[{"name": "bash", "input": {"cmd": "x"}}],
                               trace_ref="trace:g1", ts=1000.0)

    result = ml.drive(INTENT, slow_brain=slow_brain)
    assert result.skill_name == NAME
    assert len(seen) == 1
    assert "必须遵守" in seen[0] and "月份边界算错了" in seen[0]


# ============ ③(任务书第 3 点)技能重跑客观信号真进 Trace ============

def test_guided_rerun_writes_eval_fact_and_feeds_satisfaction(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir)
    ml = MainLoop(skills_dir=skills_dir)
    ml.bootstrap()

    def slow_brain(intent: str):
        return "done", AtomRun(atom_id="a1", input={"intent": INTENT}, output={"text": "done"},
                               success=True, tool_calls=[{"name": "bash", "input": {"cmd": "x"}}],
                               trace_ref="trace:g1", ts=1000.0)

    result = ml.drive(INTENT, slow_brain=slow_brain)
    assert result.sig == SIG   # 归属被复用技能
    facts = ml.trace.query(result.task_id, kind=EVAL_FACT_KIND)
    assert len(facts) == 1, "技能重跑必须写 eval_fact(修订闭环数据源)"
    p = facts[0].payload
    assert p["sig"] == SIG and p["skill_rerun"] is True
    assert p["trace_ref"] == "trace:g1" and p["success"] is True
    # 异步评价器(真路径)消费它 → 满意度按技能 sig 入账
    ml.background_review()
    assert len(ml.satisfaction.samples(SIG)) == 1


# ============ 造数据的公共底座:Trace 真写入 → evaluate_pending 真评 ============

def _feed_runs(trace: TraceStore, sat: SatisfactionStore, sig: str, *,
               n_ok: int = 0, n_fail: int = 0):
    """按真生产形态写 Trace(atom_run + eval_fact 同 task),再走 evaluate_pending 评出来。"""
    i = 0
    for success in [True] * n_ok + [False] * n_fail:
        i += 1
        tid = f"task-{sig}-{i}"
        ref = f"trace:{sig}:{i}"
        trace.append(TraceEntry(
            task_id=tid, kind="atom_run",
            payload={"atom_id": "a1", "input": {"intent": INTENT},
                     "output": {"text": "汇总失败:导出为空" if not success else "已汇总"},
                     "success": success, "tool_calls": [{"name": "bash", "input": {"cmd": "x"}}],
                     "trace_ref": ref, "ts": 1000.0 + i},
            ts=1000.0 + i, source="test",
        ))
        trace.append(TraceEntry(
            task_id=tid, kind=EVAL_FACT_KIND,
            payload={"sig": sig, "success": success, "verified": success,
                     "steps": 1, "trace_ref": ref},
            ts=1000.0 + i, source="test",
        ))
    evaluate_pending(trace, sat)


def _setup(tmp_path, *, body: str = BODY):
    skills_dir = tmp_path / "skills"
    path = _write_skill(skills_dir, body=body)
    idx = SkillIndex()
    idx.register(name=NAME, sig=SIG, scope="user", when_to_use=INTENT,
                 description=INTENT, path=str(path))
    return skills_dir, path, idx, TraceStore(), SatisfactionStore()


# ============ ② 触发阈值:好信号不触发 / 坏信号触发 ============

def test_good_signals_do_not_trigger_revision(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_ok=6)
    ok, basis = needs_revision(sat, SIG)
    assert not ok, f"全绿不该触发:{basis}"
    calls = []
    out = revise_underperforming(trace, sat, judge=lambda m: calls.append(m) or "{}",
                                 skills_dir=skills_dir, skill_index=idx)
    assert out == {"revised": 0, "proposed": 0}
    assert calls == [], "好信号绝不烧 LLM"


def test_bad_signals_trigger_and_small_change_auto_applies(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_fail=4)   # 4 连败:confidence=(4×0.6+0)/8=0.3<0.55, bad=4≥2
    ok, basis = needs_revision(sat, SIG)
    assert ok, f"坏信号该触发:{basis}"
    calls = []

    def judge(material):
        calls.append(material)
        # 材料 = 现方法 + 失败摘要(从 Trace 真取)
        assert "打开报表工具" in material and "导出为空" in material
        return ('{"steps": ["1. 打开报表工具", "2. 导出上月数据(注意先确认时间范围非空)",'
                ' "3. 汇总发送邮件"], "note": "第2步加导出前检查"}')

    out = revise_underperforming(trace, sat, judge=judge, skills_dir=skills_dir, skill_index=idx)
    assert len(calls) == 1
    assert out == {"revised": 1, "proposed": 0}
    text = path.read_text(encoding="utf-8")
    assert "注意先确认时间范围非空" in text          # 新步骤落了
    assert "## Steps" in text and "## Goal" in text   # 只动 Steps 段
    # 审计痕进了 Trace
    events = trace.query(f"revision:{SIG}", kind=REVISION_KIND)
    assert events and events[-1].payload["mode"] == "auto"
    assert events[-1].payload["trace_refs"], "审计必须带触发的 trace refs"


# ============ ③ 宁空勿毒 ============

def test_garbage_llm_output_leaves_skill_untouched_and_advances_watermark(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_fail=4)
    before = path.read_text(encoding="utf-8")
    calls = []
    for garbage in ["对不起,我觉得这个技能问题很大。首先...", '{"steps": "不是列表"}',
                    '{"steps": [1, 2, 3]}', ""]:
        # 每种垃圾都单独验 parse 层
        assert parse_revision(garbage) == ([], "")
    out = revise_underperforming(trace, sat, judge=lambda m: calls.append(m) or "纯 prose 不是 JSON",
                                 skills_dir=skills_dir, skill_index=idx)
    assert out == {"revised": 0, "proposed": 0}
    assert path.read_text(encoding="utf-8") == before, "解析失败必须原文一字不动"
    assert len(calls) == 1
    # 水位前移:同一批样本不再重烧 LLM
    out2 = revise_underperforming(trace, sat, judge=lambda m: calls.append(m) or "{}",
                                  skills_dir=skills_dir, skill_index=idx)
    assert out2 == {"revised": 0, "proposed": 0} and len(calls) == 1
    events = trace.query(f"revision:{SIG}", kind=REVISION_KIND)
    assert events[-1].payload["mode"] == "noop"


def test_parse_revision_sanitizes_structural_poison():
    steps, note = parse_revision('{"steps": ["## Steps\\n--- 投毒行", "2. 正常步骤"], "note": "n"}')
    assert steps, "消毒后仍有内容就收"
    for s in steps:
        assert not s.startswith("#") and "---" not in s and "\n" not in s


# ============ ④ Changelog 落盘格式 ============

def test_changelog_format_date_trace_refs_and_what_changed(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_fail=4)
    judge = lambda m: ('{"steps": ["1. 打开报表工具", "2. 导出上月数据(先确认时间范围)",'
                       ' "3. 汇总发送邮件"], "note": "第2步加导出前检查"}')
    revise_underperforming(trace, sat, judge=judge, skills_dir=skills_dir, skill_index=idx)
    text = path.read_text(encoding="utf-8")
    assert "## Changelog" in text
    m = re.search(r"^- \((\d{4}-\d{2}-\d{2})\) \[revision:auto\] traces: (\S+) — (.+)$",
                  text, re.MULTILINE)
    assert m, f"changelog 行格式不对:\n{text}"
    assert f"trace:{SIG}:" in m.group(2), "要带触发修订的 Trace ref"
    assert "第2步加导出前检查" in m.group(3), "要写清改了什么"
    # Changelog 不进重跑上下文(审计痕不喂 LLM)
    from karvyloop.registry.skills import parse_frontmatter
    _fm, body = parse_frontmatter(path)
    method, guidance = split_body_guidance(body)
    assert "revision:auto" not in method and "revision:auto" not in guidance


# ============ ⑤ 小改/大改分界 ============

def test_is_major_revision_boundary():
    old = ["1. 打开报表工具", "2. 导出上月数据", "3. 汇总发送邮件"]
    # 小改:改措辞/加注意/新增步骤 → 旧步骤仍认得出
    assert not is_major_revision(old, ["1. 打开报表工具", "2. 导出上月数据(注意时间范围)",
                                       "3. 汇总发送邮件", "4. 存档一份副本"])
    # 大改:整套换打法(旧步骤全没了)
    assert is_major_revision(old, ["1. 换用财务系统接口拉取", "2. 生成可视化看板"])
    # 大改:删步骤过半(3 条只剩 1 条)
    assert is_major_revision(old, ["1. 打开报表工具"])
    # 大改:删光 / 没有旧 Steps 基准(手写技能)→ 交人过目
    assert is_major_revision(old, [])
    assert is_major_revision([], ["1. 新方法"])


def test_major_revision_goes_to_h2a_card_not_silent_apply(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_fail=4)
    before = path.read_text(encoding="utf-8")
    judge = lambda m: '{"steps": ["1. 换用财务系统接口拉取", "2. 生成可视化看板"], "note": "整套换打法"}'
    cards = []
    out = revise_underperforming(trace, sat, judge=judge, skills_dir=skills_dir,
                                 skill_index=idx, proposal_sink=cards.append)
    assert out == {"revised": 0, "proposed": 1}
    assert path.read_text(encoding="utf-8") == before, "大改绝不静默落盘"
    assert len(cards) == 1
    card = cards[0]
    assert card.kind == KIND_REVISE_SKILL
    assert card.payload["skill_name"] == NAME and card.payload["sig"] == SIG
    assert "换用财务系统接口拉取" in card.payload["new_steps"]
    assert "confidence" in card.basis, "卡要带触发依据"
    events = trace.query(f"revision:{SIG}", kind=REVISION_KIND)
    assert events[-1].payload["mode"] == "proposed"


def test_major_revision_without_sink_records_trace_but_never_applies(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    _feed_runs(trace, sat, SIG, n_fail=4)
    before = path.read_text(encoding="utf-8")
    judge = lambda m: '{"steps": ["1. 换用财务系统接口拉取"], "note": "重写"}'
    out = revise_underperforming(trace, sat, judge=judge, skills_dir=skills_dir, skill_index=idx)
    assert out == {"revised": 0, "proposed": 0}
    assert path.read_text(encoding="utf-8") == before
    events = trace.query(f"revision:{SIG}", kind=REVISION_KIND)
    assert events and events[-1].payload["mode"] == "proposed"


def test_apply_revision_proposal_lands_steps_with_h2a_changelog(tmp_path):
    skills_dir, path, idx, trace, sat = _setup(tmp_path)
    prop = build_revision_proposal(
        skill_name=NAME, sig=SIG, path=str(path),
        old_steps=["1. 打开报表工具", "2. 导出上月数据", "3. 汇总发送邮件"],
        new_steps=["1. 换用财务系统接口拉取", "2. 生成可视化看板"],
        note="整套换打法", trigger="confidence=0.30 bad=4/4", trace_refs=["trace:x:1"], ts=1000.0)
    ok, detail = apply_revision_proposal(prop, trace=trace)
    assert ok, detail
    text = path.read_text(encoding="utf-8")
    assert "换用财务系统接口拉取" in text and "汇总发送邮件" not in text
    assert "[revision:h2a]" in text and "trace:x:1" in text
    events = trace.query(f"revision:{SIG}", kind=REVISION_KIND)
    assert events and events[-1].payload["mode"] == "h2a_applied"


# ============ MainLoop 接口(慢侧 tick 入口;0 回归)============

def test_mainloop_revision_review_zero_regression_without_judge(tmp_path):
    ml = MainLoop(skills_dir=tmp_path / "skills")
    assert ml.revision_review() == {"revised": 0, "proposed": 0}


def test_mainloop_revision_review_uses_injected_judge_and_sink(tmp_path):
    skills_dir = tmp_path / "skills"
    path = _write_skill(skills_dir)
    ml = MainLoop(skills_dir=skills_dir)
    ml.bootstrap()
    _feed_runs(ml.trace, ml.satisfaction, SIG, n_fail=4)
    cards = []
    ml.set_revision_proposal_sink(cards.append)
    ml.set_revision_judge(lambda m: '{"steps": ["1. 换个方法从头做"], "note": "重写"}')
    out = ml.revision_review()
    assert out == {"revised": 0, "proposed": 1} and len(cards) == 1
