"""test_skill_composition — 技能组合 + 角色绑定接通 + 可读命名(模块雷达 A 技能组 top1)。

锁三件事(全走真 recall / 真 skill index / 真 SKILL.md,LLM 层 stub):

1. 角色绑定接通:load_bound_skills 真被 drive 用上(prefer 透传 → recall)——
   绑定技能弱匹配也加权胜出;prefer 参数真到 recall(桩断言收到)。
2. Top-K 有界组合:主命中 + ≤2 支持技能。
   - 造主 + 语义**互补**支持 → 召回返回带 supports;
   - 造覆盖**重复**同一意图的次命中 → **不带** support,退回纯 Top-1;
   - 弱相关(覆盖度不够)→ 不带(保守);
   - compose_rerun_context 组装含支持技能标注。
3. 可读命名:hash 名 → kebab(注入 namer 时);无 namer → 老 skill_<hash> 兜底(0 回归);
   老可读名不动(加性)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import (  # noqa: E402
    compose_rerun_context,
    load_bound_skills,
    recall,
)
from karvyloop.crystallize.crystallize import (  # noqa: E402
    is_hash_skill_name,
    readable_skill_name,
)
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402


# ---- 真造 SKILL.md ----

def _write_skill(dir_, name, *, desc, when, tags=(), scope="user", body="# body\n"):
    d = pathlib.Path(dir_) / name
    d.mkdir(parents=True, exist_ok=True)
    tag_line = f"tags: [{', '.join(tags)}]\n" if tags else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nwhen_to_use: {when}\n"
        f"signature: sig-{name}\nscope: {scope}\nresult_reuse: dynamic\n{tag_line}---\n{body}",
        encoding="utf-8")
    return d


def _index(user_dir) -> SkillIndex:
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


# ============ 1. 角色绑定接通 ============

def test_prefer_makes_bound_skill_win_on_weak_match(tmp_path):
    """绑定技能弱匹配也应因 prefer 加权胜出(绑定优先于碰运气,但不绕 scope)。"""
    # strong 技能与意图强匹配;bound 技能弱匹配(只碰一个词)但被角色绑定
    _write_skill(tmp_path, "invoice-report-builder",
                 desc="build monthly invoice financial report tables",
                 when="build invoice report tables financial monthly summary")
    # bound 技能弱匹配(overlap 非空但比强匹配低) → 靠 prefer +0.5 翻盘胜出
    _write_skill(tmp_path, "my-bound-helper",
                 desc="a generic invoice monthly helper the role always carries",
                 when="invoice monthly helper generic assistant")
    idx = _index(tmp_path)
    intent = "build the monthly invoice report"
    # 无 prefer:强匹配技能胜出
    hit_no = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit_no is not None
    # 有 prefer 绑定 my-bound-helper:+0.5 加权让绑定技能胜出
    hit_pref = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx,
                      prefer=["my-bound-helper"])
    assert hit_pref is not None and hit_pref.name == "my-bound-helper", \
        f"绑定技能没因 prefer 胜出: {hit_pref and hit_pref.name}"


def test_load_bound_skills_direct_fetch(tmp_path):
    """load_bound_skills 按名直取绑定技能(绑定即在场,不靠模糊召回);缺名静默跳过。"""
    _write_skill(tmp_path, "bound-a", desc="alpha", when="alpha tasks")
    _write_skill(tmp_path, "bound-b", desc="beta", when="beta tasks")
    idx = _index(tmp_path)
    hits = load_bound_skills(["bound-a", "bound-b", "does-not-exist"],
                             skills_dir=tmp_path, skill_index=idx)
    names = {h.name for h in hits}
    assert names == {"bound-a", "bound-b"}
    assert all(h.score == 1.0 for h in hits)  # 绑定=满信


def test_drive_folds_bound_skill_as_guaranteed_support(tmp_path):
    """drive:角色绑定但**未成主命中/未被 overlap 选中**的技能,经 load_bound_skills 折叠成保证在场的支持。

    绑定技能与意图零匹配(recall 挑不到它),但因角色绑定应仍出现在重跑上下文的 supports 里。
    """
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.schemas import AtomRun

    skills_dir = tmp_path / "skills"
    # 主技能:强匹配当前意图(dynamic → 命中重跑)
    _write_skill(skills_dir, "email-parser",
                 desc="parse email extract fields", when="parse email extract fields sender subject")
    # 绑定技能:与意图零匹配,但角色随身带
    _write_skill(skills_dir, "team-style-guide",
                 desc="apply the team writing style guide", when="apply team writing style guide tone")
    idx = SkillIndex()
    idx.rebuild_from_disk(skills_dir)

    captured = {}

    def slow_brain(intent):
        captured["intent"] = intent   # drive 把重跑上下文喂进来
        run = AtomRun(atom_id="r1", input={"intent": intent}, output={"text": "ok"},
                      success=True, tool_calls=[{"name": "run_command"}], trace_ref="t1", ts=0.0)
        return "ok", run

    ml = MainLoop(skills_dir=skills_dir, skill_index=idx)
    ml.drive("parse email extract fields sender subject", slow_brain=slow_brain,
             prefer=["team-style-guide"])
    # 绑定技能的方法应被折叠进重跑上下文(保证在场)
    assert "team-style-guide" in captured.get("intent", ""), \
        f"绑定技能没被折叠成保证在场支持: {captured.get('intent','')[:200]}"


def test_pursue_threads_prefer_to_drive(tmp_path):
    """pursue 把 prefer 透传给 ml.drive(委派路径角色绑定技能接通的一环)。"""
    from karvyloop.cli.pursuit_loop import pursue, ReplanBudget

    seen = {}

    class _StubResult:
        terminal = "completed"
        error = ""
        text = "done"
        sig = ""
        task_id = ""

    class _StubML:
        def drive(self, intent, *, slow_brain=None, prefer=None):
            seen["prefer"] = prefer
            return _StubResult()

    # 无验收能力 rk → pursue 在跑完后 inconclusive 收(不影响 prefer 断言)
    pursue("do the delegated work", ml=_StubML(), slow_brain=lambda i: None,
           rk={}, budget=ReplanBudget(max_attempts=1), prefer=["bound-x", "bound-y"])
    assert seen.get("prefer") == ["bound-x", "bound-y"]


def test_pursue_no_prefer_uses_legacy_drive_signature(tmp_path):
    """无 prefer 时 pursue 不传 prefer kwarg → 老 drive 桩(不接 prefer)仍工作(0 回归)。"""
    from karvyloop.cli.pursuit_loop import pursue, ReplanBudget

    class _StubResult:
        terminal = "completed"
        error = ""
        text = "done"
        sig = ""
        task_id = ""

    class _LegacyML:
        def drive(self, intent, slow_brain=None):   # 老签名:不接 prefer
            return _StubResult()

    out = pursue("work", ml=_LegacyML(), slow_brain=lambda i: None,
                 rk={}, budget=ReplanBudget(max_attempts=1))
    assert out.checked.result.text == "done"


def test_drive_threads_prefer_to_recall(tmp_path):
    """drive() 把 prefer 真透传给 recall(桩 recall 断言收到 prefer)。"""
    from karvyloop.runtime import main_loop as ml_mod
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.schemas import AtomRun

    seen = {}

    def _spy_recall(intent, **kw):
        seen["prefer"] = kw.get("prefer")
        return None  # miss → 走慢脑,不影响断言

    ml = MainLoop(skills_dir=tmp_path / "skills")

    def slow_brain(intent):
        run = AtomRun(atom_id="r1", input={"intent": intent}, output={"text": "ok"},
                      success=True, tool_calls=[{"name": "run_command"}], trace_ref="t1", ts=0.0)
        return "ok", run

    orig = ml_mod.recall
    ml_mod.recall = _spy_recall
    try:
        ml.drive("do something", slow_brain=slow_brain, prefer=["bound-skill-x"])
    finally:
        ml_mod.recall = orig
    assert seen.get("prefer") == ["bound-skill-x"]


# ============ 2. Top-K 有界组合 ============

def test_complementary_support_included(tmp_path):
    """主命中 + 语义**互补**支持技能 → 召回返回带 supports(组合)。"""
    # 主技能:数据清洗;支持技能:图表可视化(与意图相关、但覆盖**新**面 chart/visualize/plot)
    _write_skill(tmp_path, "data-cleaner",
                 desc="clean and dedupe raw data rows",
                 when="clean dedupe raw data rows preprocess")
    _write_skill(tmp_path, "chart-plotter",
                 desc="plot charts and visualize data trends",
                 when="plot chart visualize data trends graph")
    idx = _index(tmp_path)
    intent = "clean the raw data rows and plot chart to visualize trends"
    hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit is not None
    sup_names = {s.name for s in hit.supports}
    # 主命中 + 互补支持都在场(哪个是主取决于覆盖度,但两者应组合)
    assert hit.name in {"data-cleaner", "chart-plotter"}
    other = "chart-plotter" if hit.name == "data-cleaner" else "data-cleaner"
    assert other in sup_names, f"互补支持技能没进组合: main={hit.name} supports={sup_names}"


def test_redundant_candidate_not_supported(tmp_path):
    """次命中只**重复覆盖**主技能已覆盖的同一意图 → 不带 support,退回纯 Top-1。"""
    # 两个技能覆盖几乎相同的意图面(都只吃 "report" 系词),次命中不带新面 → 冗余
    _write_skill(tmp_path, "report-a",
                 desc="generate financial report", when="generate financial report summary")
    _write_skill(tmp_path, "report-b",
                 desc="produce financial report", when="produce financial report summary")
    idx = _index(tmp_path)
    intent = "generate financial report summary"
    hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit is not None
    assert hit.supports == [], \
        f"冗余次命中不该进组合(应退 Top-1): supports={[s.name for s in hit.supports]}"


def test_weakly_related_candidate_not_supported(tmp_path):
    """弱相关候选(意图覆盖度不够阈值)→ 不带(保守,宁空勿滥)。"""
    _write_skill(tmp_path, "email-parser",
                 desc="parse email into structured fields extract sender subject date attachments",
                 when="parse email extract sender subject date attachments structured fields")
    # 只碰一个通用词 "data" 的弱相关技能
    _write_skill(tmp_path, "vague-tool",
                 desc="some tool touching data occasionally",
                 when="data")
    idx = _index(tmp_path)
    intent = "parse this email and extract the sender subject date attachments fields"
    hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit is not None and hit.name == "email-parser"
    assert "vague-tool" not in {s.name for s in hit.supports}


def test_supports_bounded_to_two(tmp_path):
    """支持技能最多 2 个(有界组合)。"""
    _write_skill(tmp_path, "main-etl",
                 desc="extract transform load pipeline orchestrate", when="extract transform load etl pipeline orchestrate data")
    _write_skill(tmp_path, "sup-validate",
                 desc="validate schema constraints", when="validate schema constraints check data")
    _write_skill(tmp_path, "sup-visualize",
                 desc="visualize dashboard charts", when="visualize dashboard charts render data")
    _write_skill(tmp_path, "sup-notify",
                 desc="notify alert send message", when="notify alert send message slack data")
    idx = _index(tmp_path)
    intent = ("extract transform load data pipeline then validate schema constraints "
              "visualize dashboard charts and notify alert send message")
    hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit is not None
    assert len(hit.supports) <= 2, f"支持技能没有被界到 2 个: {[s.name for s in hit.supports]}"


def test_compose_rerun_context_annotates_supports(tmp_path):
    """compose_rerun_context 把支持技能作"另外可参考"附加并标注技能名。"""
    from karvyloop.crystallize.recall import RecallHit
    main = RecallHit(name="main-skill", body="## Goal\nmain goal\n\n## Steps\n1. do main\n",
                     path="", score=0.9, manifest={})
    sup = RecallHit(name="support-skill", body="## Goal\nsup goal\n\n## Steps\n1. do support\n",
                    path="", score=0.5, manifest={})
    main.supports = [sup]
    ctx = compose_rerun_context(main, "some current task")
    assert "support-skill" in ctx
    assert "支持技能" in ctx
    assert "1. do support" in ctx        # 支持方法段真被带进上下文
    assert "some current task" in ctx    # 当前任务仍在


def test_compose_rerun_context_no_supports_unchanged(tmp_path):
    """无支持技能时组装与旧行为一致(0 回归)。"""
    from karvyloop.crystallize.recall import RecallHit
    main = RecallHit(name="m", body="## Goal\ng\n\n## Steps\n1. x\n", path="", score=0.9, manifest={})
    ctx = compose_rerun_context(main, "task")
    assert "支持技能" not in ctx
    assert "task" in ctx


# ============ 3. 可读命名 ============

def test_readable_name_from_namer(tmp_path):
    """注入 namer → hash 兜底名被换成 kebab 可读名。"""
    name = readable_skill_name("summarize the weekly sales report", "abcd1234ef",
                               namer=lambda hint: "Summarize Weekly Report")
    assert name == "summarize-weekly-report"
    assert not is_hash_skill_name(name)


def test_readable_name_falls_back_to_kebab_intent(tmp_path):
    """namer 返回空 → 回退确定性 kebab(intent)。"""
    name = readable_skill_name("convert csv to json", "abcd1234ef", namer=lambda h: "")
    assert name == "convert-csv-to-json"


def test_readable_name_falls_back_to_hash_when_unusable(tmp_path):
    """intent kebab 化后为空(纯中文/纯符号)+ 无 namer → 回退 skill_<hash>(永不裸奔)。"""
    name = readable_skill_name("帮我分析这个表格", "abcd1234ef99", namer=None)
    assert name == "skill_abcd1234"
    assert is_hash_skill_name(name)


def test_readable_name_avoids_collision(tmp_path):
    """同名已占用 → 加数字后缀,不覆盖老技能(加性)。"""
    name = readable_skill_name("build report", "sigsigsig",
                               namer=lambda h: "build-report", taken={"build-report"})
    assert name == "build-report-2"


def test_is_hash_skill_name():
    assert is_hash_skill_name("skill_abcd1234")
    assert not is_hash_skill_name("summarize-report")
    assert not is_hash_skill_name("data-analyst")


def test_drive_no_namer_keeps_hash_name(tmp_path):
    """无注入 namer 的 drive → 结晶名仍是 skill_<hash>(0 回归)。"""
    from karvyloop.runtime.main_loop import MainLoop, Brain
    from karvyloop.schemas import AtomRun

    class Clock:
        def __init__(self): self.t = 1000.0
        def __call__(self): return self.t
        def tick(self): self.t += 100.0

    clk = Clock()
    ml = MainLoop(skills_dir=tmp_path / "skills", clock=clk,
                  result_classifier=lambda *_a: "stable")
    ml.bootstrap()
    n = [0]

    def slow_brain(intent):
        n[0] += 1
        run = AtomRun(atom_id=f"r{n[0]}", input={"intent": intent, "x": n[0]},
                      output={"text": "ok"}, success=True,
                      tool_calls=[{"name": "run_command"}], trace_ref=f"t{n[0]}", ts=0.0)
        return "ok", run

    last = None
    for _ in range(4):
        clk.tick()
        last = ml.drive("summarize a long doc", slow_brain=slow_brain)
    # 结晶后 skill_name 应是 hash 兜底(无 namer)
    assert last.skill_name.startswith("skill_"), f"无 namer 却没用 hash 名: {last.skill_name}"


def test_drive_with_namer_produces_readable_name(tmp_path):
    """注入 namer 的 drive → 结晶名是 kebab 可读名。"""
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.schemas import AtomRun

    class Clock:
        def __init__(self): self.t = 1000.0
        def __call__(self): return self.t
        def tick(self): self.t += 100.0

    clk = Clock()
    ml = MainLoop(skills_dir=tmp_path / "skills", clock=clk,
                  result_classifier=lambda *_a: "stable")
    ml.set_skill_namer(lambda hint: "summarize-long-doc")
    ml.bootstrap()
    n = [0]

    def slow_brain(intent):
        n[0] += 1
        run = AtomRun(atom_id=f"r{n[0]}", input={"intent": intent, "x": n[0]},
                      output={"text": "ok"}, success=True,
                      tool_calls=[{"name": "run_command"}], trace_ref=f"t{n[0]}", ts=0.0)
        return "ok", run

    last = None
    for _ in range(4):
        clk.tick()
        last = ml.drive("summarize a long doc", slow_brain=slow_brain)
    assert last.skill_name == "summarize-long-doc", f"namer 没生效: {last.skill_name}"
