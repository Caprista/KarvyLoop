"""主循环 driver — e2e 验收（test_main_loop.py）。

覆盖:
  1. 冷启动 → 慢脑 → 多次调用 → 触发结晶 → 下一次召回命中(快脑)
  2. 同一 intent 反复调用,快脑命中率随时间上升(M1 北极星指标)
  3. 慢脑失败不结晶(success_count 不增,关 1/关 2 都不会过)
  4. archive/restore 经主循环:auto-restore 计入 stats
  5. 后台 background_review 跑得动(evict)
  6. forge_slow_brain_factory 在没有真实 LLM 的环境也能 import(不实际调)

设计原则:
  - 用可控时钟(每次调用都把 clock 推 +100s,绕开 60s 去抖)
  - slow_brain 是 sync stub,返回 (text, AtomRun) 二元组 —— 不依赖 forge
  - AtomRun 的 `input` 必带 `intent`,这样 compute_signature 能算
  - 至少 3 次不同 params 的成功调用才触发结晶(generalized=True + score≥3)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from karvyloop.runtime.main_loop import (
    Brain,
    DriveResult,
    DriveStats,
    MainLoop,
    forge_slow_brain_factory,
)
from karvyloop.schemas import AtomRun


# ---- 工具 ----

class Clock:
    """可控时钟 —— 每次 tick() 推 +100s,绕开 60s 去抖。"""
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self) -> None:
        self.t += 100.0


def make_slow_brain(
    *,
    text_factory: Callable[[str], str] = lambda i: f"ok-{i}",
    success: bool = True,
    counter: list[int] | None = None,
    fixed_params: dict | None = None,
    tool_calls: list | None = None,   # brick3:默认带一个工具调用(代表真干活→可结晶);[] = 纯对话不结晶
) -> Callable[[str], tuple[str, AtomRun]]:
    """建一个 sync slow_brain stub。

    `fixed_params=None` 时,每次给一个新 x 计数(让 param_variants 多样化,触发
    "generalized" 关 2)。
    `counter` 列表被 append 一次/每次调用(供测试断言次数)。
    """
    n = [0]

    def slow_brain(intent: str) -> tuple[str, AtomRun]:
        n[0] += 1
        if counter is not None:
            counter.append(n[0])
        params = dict(fixed_params) if fixed_params is not None else {"x": n[0]}
        run = AtomRun(
            atom_id=f"run-{n[0]}",
            input={"intent": intent, **params},
            output={"text": text_factory(intent)},
            success=success,
            tool_calls=(tool_calls if tool_calls is not None else [{"name": "run_command"}]),
            trace_ref=f"trace-{n[0]}",
            ts=0.0,  # observe 会回退到 clock()
        )
        return text_factory(intent), run

    return slow_brain


def fresh_loop(tmp_path: Path, *, clk: Clock | None = None, classifier=lambda *_a: "stable") -> MainLoop:
    """一个空 skill 库 + 可控时钟的主循环实例。

    §13:这些 AC 用**确定性桩**(slow_brain 每次返回固定输出)→ 语义上就是 `stable` 任务,
    故默认注入 stable 判定器,走"回放"路径(正是这些 AC 要验的:重复同任务→快脑命中)。
    动态(命中重跑不回放)的行为由 test_drive_fresh + test_drive_dynamic_* 专门锁。
    """
    return MainLoop(
        skills_dir=tmp_path / "skills",
        clock=(clk if clk is not None else Clock()),
        result_classifier=classifier,
    )


# ============ AC1: 冷启动 + 多次调用触发结晶 + 召回命中 ============
def test_drive_cold_then_warmup_then_crystallize_then_fast_brain(tmp_path: Path):
    """0→3 次慢脑,触发结晶;第 4 次同 intent 走快脑。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    counter: list[int] = []
    sb = make_slow_brain(counter=counter)

    # 冷启动:3 次相同 intent + 不同 params
    # usage_score 表: usage=1→score=1(<3), usage=2→score=2(<3), usage=3→score=3(≥3)
    # 第 1, 2 次关 2 不过(score<3);第 3 次 usage=3 + 3 个不同 variants → 关 2 过
    expected_cryst_per_call = [False, False, True]
    for i in range(3):
        clk.tick()
        r = ml.drive("summarize report", slow_brain=sb)
        assert r.brain == Brain.SLOW
        assert r.fast_brain_hit is False
        assert r.crystallized is expected_cryst_per_call[i], (
            f"call {i+1}: expected cryst={expected_cryst_per_call[i]} "
            f"got {r.crystallized}"
        )

    # 第 3 次跑完 → 关 1+关 2 应过 → 结晶
    # (重跑一次 drive 检查:第 4 次同样 intent,recall 应命中快脑)
    clk.tick()
    r4 = ml.drive("summarize report", slow_brain=sb)
    # 此时前 3 次已触发结晶 → SkillIndex 已有 → 召回命中
    assert r4.brain == Brain.FAST
    assert r4.fast_brain_hit is True
    assert r4.skill_name.startswith("skill_")
    # slow_brain 不应再被叫
    assert len(counter) == 3  # 前 3 次都跑了,第 4 次没跑

    # stats
    assert ml.stats.drive_calls == 4
    assert ml.stats.fast_brain_hits == 1
    assert ml.stats.slow_brain_runs == 3
    assert ml.stats.crystallizations == 1
    # hit rate: 0 → 0 → 0 → 25%
    assert ml.stats.fast_brain_hit_rate == pytest.approx(0.25)


# ============ AC2: 北极星指标 — 同一 intent 用得越多,快脑命中率上升 ============
def test_drive_fast_brain_hit_rate_increases_with_repeated_intent(tmp_path: Path):
    """7 次同 intent:前 3 次慢(结晶前),后 4 次快 → 4/7 ≈ 57%。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    sb = make_slow_brain()

    for i in range(7):
        clk.tick()
        ml.drive("translate 中文 to english", slow_brain=sb)
    # 3 次慢脑,1 次结晶,3 次快脑(第 4,5,6,7 次都命中)
    assert ml.stats.slow_brain_runs == 3
    assert ml.stats.fast_brain_hits == 4
    assert ml.stats.drive_calls == 7
    assert ml.stats.fast_brain_hit_rate == pytest.approx(4 / 7)


# ============ AC3: 慢脑失败 → 不结晶,快脑永远不会命中 ============
def test_drive_slow_brain_failure_does_not_crystallize(tmp_path: Path):
    """5 次失败调用 → 永远没 verify gate(关 1 没过) → 不结晶 → 全部走慢脑。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    counter: list[int] = []
    sb = make_slow_brain(success=False, counter=counter)

    for _ in range(5):
        clk.tick()
        r = ml.drive("do something hard", slow_brain=sb)
        assert r.brain == Brain.SLOW
        assert r.crystallized is False

    assert ml.stats.crystallizations == 0
    assert ml.stats.slow_brain_runs == 5
    assert ml.stats.fast_brain_hits == 0
    # 慢脑跑了 5 次(slow_brain 每次都跑,因为永远没结晶)
    assert len(counter) == 5


# ============ brick3: 纯对话(没动工具)永不结晶,即使重复 ============
def test_no_tool_conversational_reply_never_crystallizes(tmp_path: Path):
    """问候/"你是谁"这类没用工具的回复重复 N 次也不结晶(否则会被快脑跨场 replay 污染)。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    sb = make_slow_brain(tool_calls=[])   # 没动工具 = 纯对话
    for _ in range(5):
        clk.tick()
        r = ml.drive("你是谁?", slow_brain=sb)
        assert r.brain == Brain.SLOW and r.crystallized is False
    assert ml.stats.crystallizations == 0   # 一个技能都没凝


# ============ AC4: archive/restore 经主循环:auto-restore 计数 ============
def test_drive_auto_restores_archived_skill_and_counts_it(tmp_path: Path):
    """手动把已有技能的 sig 归档,下次 drive 命中 → auto-restore,stats 计数。"""
    from karvyloop.crystallize import (
        InMemoryUsageStore,
        SkillIndex,
        VerifyStore,
        crystallize as crystallize_skill,
    )
    from karvyloop.crystallize.signature import compute_signature
    from karvyloop.schemas import UsageStats

    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()

    # 直接手工结晶一个 skill(简化:不走 3 次调用)
    sig = compute_signature(AtomRun(
        atom_id="seed",
        input={"intent": "translate foo to bar", "x": 1},
        output={}, success=True, tool_calls=[],
        trace_ref="seed-t", ts=clk.t,
    ))
    # 给 store 喂 5 个不同的 param_variants + 5 次 success,score 会过
    pv = [{"x": i} for i in range(5)]
    ml.store.put(sig, UsageStats(usage_count=5, success_count=5, last_used_at=clk.t,
                                 param_variants=pv))
    ml.verify.mark_verified(sig, "seed-t", note="seed", clock=clk)
    s = crystallize_skill(
        sig, name="trans_foo", description="translate foo to bar",
        body="## body\ntranslate", when_to_use="translate foo to bar",
        arguments=None, store=ml.store, verify=ml.verify,
        skills_dir=ml.skills_dir, scope="user", now=clk.t,
        result_reuse="stable",   # §13:这条测的是"回放+auto-restore",故标 stable(确定性翻译桩)
    )
    ml.skill_index.register(
        name=s.name, sig=sig, scope="user",
        when_to_use="translate foo to bar", description="translate foo to bar",
        path=s.manifest.get("path", ""),
    )
    # 归档(模拟 evict)
    ml.store.archive(sig)
    assert ml.store.is_archived(sig)

    # 重新 bootstrap(把 sig 重新装进 SkillIndex)—— 实际生产里 SKILL.md 在
    # 归档时不会被删,rebuild_from_disk 仍能取到 sig
    ml.skill_index.register(
        name=s.name, sig=sig, scope="user",
        when_to_use="translate foo to bar", description="translate foo to bar",
        path=s.manifest.get("path", ""),
    )

    # drive 一次
    clk.tick()
    counter: list[int] = []
    sb = make_slow_brain(counter=counter)
    r = ml.drive("translate foo to bar", slow_brain=sb)

    # 应命中快脑 + restored=True + stats.auto_restores=1
    assert r.brain == Brain.FAST
    assert r.restored is True
    assert r.skill_name == "trans_foo"
    assert ml.stats.auto_restores == 1
    # 慢脑没跑
    assert counter == []


# ============ AC5: background_review 可调用,evict 跑得动 ============
def test_drive_background_review_evicts_stale(tmp_path: Path):
    """background_review() 不抛,返回归档数(0 或更多)。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    # 没 skill 时跑 → 不抛,返回 0
    n = ml.background_review()
    assert n == 0
    assert ml.stats.drive_calls == 0  # background_review 不计 drive


# ============ AC6: 不同 intent 不互相命中(冷启动每次都慢) ============
def test_drive_different_intents_each_take_3_calls_to_crystallize(tmp_path: Path):
    """两个真正不相干的 intent(无 token 重叠)各自冷启动 → 各自 3 次慢脑后才结晶。

    注意:recall 用 token overlap(不算 embedding),所以两个 intent 必须用
    **完全不相干的词**(像 "alpha"/"beta" 这种有公共 token 仍会撞上)。
    """
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    counter: list[int] = []
    sb = make_slow_brain(counter=counter)

    # intent A 3 次
    for _ in range(3):
        clk.tick()
        ml.drive("translate foo to chinese", slow_brain=sb)
    # intent B 3 次
    for _ in range(3):
        clk.tick()
        ml.drive("summarize quarterly report", slow_brain=sb)
    # 6 次都该走慢脑(A、B 词集无交集,谁都没法召回对方的 skill)

    # 现在第 7 次 A(应快)+ 第 8 次 B(应快)
    clk.tick()
    rA = ml.drive("translate foo to chinese", slow_brain=sb)
    assert rA.brain == Brain.FAST
    clk.tick()
    rB = ml.drive("summarize quarterly report", slow_brain=sb)
    assert rB.brain == Brain.FAST

    assert ml.stats.slow_brain_runs == 6
    assert ml.stats.fast_brain_hits == 2
    assert ml.stats.crystallizations == 2


# ============ AC7: forge_slow_brain_factory 存在 + import 不抛 ============
def test_forge_slow_brain_factory_importable():
    """forge_slow_brain_factory 是个 callable(不实际调 forge —— 那需要 LLM)。"""
    assert callable(forge_slow_brain_factory)
    # 调一下要 token/sandbox/gateway —— 至少签名能拿到
    import inspect
    sig = inspect.signature(forge_slow_brain_factory)
    params = list(sig.parameters.keys())
    assert "token" in params
    assert "sandbox" in params
    assert "gateway" in params
    assert "workspace_root" in params


# ============ AC8: 结晶产物能被 recall 命中 + SkillIndex 重建后仍能命中 ============
def test_drive_crystallized_skill_persists_across_bootstrap(tmp_path: Path):
    """一次 MainLoop 跑出结晶后,新建一个 MainLoop(同 skills_dir)+bootstrap,
    recall 仍能命中 → 证明 SKILL.md 落盘 + SkillIndex.rebuild_from_disk 通路。
    """
    clk = Clock()
    ml1 = fresh_loop(tmp_path, clk=clk)
    ml1.bootstrap()
    sb = make_slow_brain()
    for _ in range(3):
        clk.tick()
        ml1.drive("summarize a long doc", slow_brain=sb)
    assert ml1.stats.crystallizations == 1

    # 另起一个 MainLoop + 重建
    from karvyloop.crystallize.skill_index import SkillIndex
    base = SkillIndex().rebuild_from_disk(tmp_path / "_none")  # 包内系统技能基线(动态)
    ml2 = fresh_loop(tmp_path, clk=clk)
    n = ml2.bootstrap()
    assert n == base + 1  # 从磁盘读回了 1 个用户 skill(+ 系统基线)

    # drive 一次同 intent → 应命中快脑
    clk.tick()
    r = ml2.drive("summarize a long doc", slow_brain=make_slow_brain())
    assert r.brain == Brain.FAST
    assert r.skill_name.startswith("skill_")


# ============ brick3+: 场-scoped 技能隔离(业务域技能不污染私聊) ============
def test_scope_isolation_domain_skill_not_recalled_in_user_scope(tmp_path: Path):
    """domain 场结晶的技能,私聊(user 场)召回不该命中 —— 业务技能跨场隔离。"""
    clk = Clock()
    ml = fresh_loop(tmp_path, clk=clk)
    ml.bootstrap()
    sb = make_slow_brain()  # 默认带 tool_calls → 可结晶
    for _ in range(3):      # 在 domain 场跑同 intent → 结晶成 domain 技能
        clk.tick()
        ml.drive("生成报表", slow_brain=sb, scope="domain")
    assert ml.stats.crystallizations == 1
    # 切到私聊(user 场)跑同 intent → 不命中那条 domain 技能 → 走慢脑(隔离住了)
    clk.tick()
    r = ml.drive("生成报表", slow_brain=sb, scope="user")
    assert r.brain == Brain.SLOW and r.fast_brain_hit is False


def test_background_review_writes_role_critique_from_satisfaction(tmp_path: Path):
    """docs/02 §14(slice-b 拆接反点):atom improve 由 **role 的质量评语**(满意度 critique)驱动,
    不再由人的纠正(steered_by_user 那条接反问责链 + 本就是死路,已拆)。幂等:后台反复跑不重复写。"""
    import types
    from karvyloop.crystallize import record_run
    from karvyloop.schemas.skill import UsageStats
    ml = fresh_loop(tmp_path)
    sd = ml.skills_dir / "daily-report"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text("---\nname: daily-report\n---\n\n# 日报技能\n", encoding="utf-8")
    sig = "sig-daily"
    ml.skill_index.register(name="daily-report", sig=sig, scope="user",
                            when_to_use="日报", description="日报", path=str(sd / "SKILL.md"))
    ml.store.put(sig, UsageStats(usage_count=2))            # 该 sig 在 usage store 里(background 才遍历到)
    # role 评判积累了两条评语(满意度 critique)
    run = types.SimpleNamespace(success=True, tool_calls=[{"name": "x"}])
    record_run(ml.satisfaction, run, sig, has_proof=True, quality=0.7, critique="少读一个文件")
    record_run(ml.satisfaction, run, sig, has_proof=True, quality=0.8, critique="先查缓存")

    ml.background_review()
    md = (sd / "SKILL.md").read_text(encoding="utf-8")
    assert "Role critique" in md and "少读一个文件" in md and "先查缓存" in md  # role 评语真写回了

    ml.background_review()                                   # 再跑一次
    md2 = (sd / "SKILL.md").read_text(encoding="utf-8")
    assert md2.count("少读一个文件") == 1                    # 幂等:按内容去重,没重复写


def test_slow_brain_records_bare_intent_not_governance(monkeypatch):
    """结晶身份按**裸意图**:governance/ctx 前缀进 LLM,但不进 run.input['intent']。

    VM 真机抓到的 moat bug:forge_slow_brain_factory 把 governance/prealign/ctx 拼进
    effective_intent 并被 forge 记成 run.input['intent'] → compute_signature 按 governance
    聚类 → 所有同 governance 的 drive collapse 成一个 sig(usage=16 全压在 prealign 块上)
    → 真技能永不结晶 + 技能库被污染。修:LLM 收 effective_intent,run.input['intent'] 还原裸意图。
    """
    import types
    import karvyloop.coding.forge as forge_mod
    cap = {}

    async def fake_gen(effective_intent, *a, **k):
        cap["llm"] = effective_intent
        return types.SimpleNamespace(text="ok",
                                     run=types.SimpleNamespace(input={"intent": effective_intent}))

    monkeypatch.setattr(forge_mod, "generate_and_run", fake_gen)
    from karvyloop.runtime.main_loop import forge_slow_brain_factory
    sb = forge_slow_brain_factory(token=None, sandbox=None, gateway=None,
                                  workspace_root="/ws",
                                  governance="【你的决策偏好】- 总用表格")
    text, run = sb("把月度报表整理一下")
    assert "【你的决策偏好】" in cap["llm"]                       # LLM 确实收到 governance 前缀
    assert "当前请求:把月度报表整理一下" in cap["llm"]
    assert run.input["intent"] == "把月度报表整理一下"           # 但结晶身份 = 裸意图


def test_slow_brain_no_governance_is_noop():
    """无 governance / ctx → effective_intent 就是裸意图,不动 run.input(0 回归)。"""
    import types
    import karvyloop.coding.forge as forge_mod
    import karvyloop.runtime.main_loop as ml_mod

    async def fake_gen(effective_intent, *a, **k):
        return types.SimpleNamespace(text="ok",
                                     run=types.SimpleNamespace(input={"intent": effective_intent}))

    orig = forge_mod.generate_and_run
    forge_mod.generate_and_run = fake_gen
    try:
        sb = ml_mod.forge_slow_brain_factory(token=None, sandbox=None, gateway=None,
                                             workspace_root="/ws")  # governance=""
        _t, run = sb("列出文件")
        assert run.input["intent"] == "列出文件"
    finally:
        forge_mod.generate_and_run = orig


def test_slow_brain_surfaces_max_turns_truncation(monkeypatch):
    """fail-loud:max_turns 被切 → 结果后追加诚实"未完成"提示(原 run_loop P1;大任务实测)。"""
    import types
    import karvyloop.coding.forge as forge_mod
    from karvyloop.atoms.terminal import Terminal

    async def fake_gen(effective_intent, *a, **k):
        return types.SimpleNamespace(text="写了 3 个文件", terminal=Terminal.MAX_TURNS,
                                     run=types.SimpleNamespace(input={"intent": effective_intent}))

    monkeypatch.setattr(forge_mod, "generate_and_run", fake_gen)
    from karvyloop.runtime.main_loop import forge_slow_brain_factory
    sb = forge_slow_brain_factory(token=None, sandbox=None, gateway=None, workspace_root="/ws")
    text, _run = sb("做个大项目")
    assert "写了 3 个文件" in text and "步数上限" in text and "继续" in text   # 老实说没做完


def test_slow_brain_completed_no_truncation_note(monkeypatch):
    """COMPLETED → 不加提示(0 回归)。"""
    import types
    import karvyloop.coding.forge as forge_mod
    from karvyloop.atoms.terminal import Terminal

    async def fake_gen(effective_intent, *a, **k):
        return types.SimpleNamespace(text="done", terminal=Terminal.COMPLETED,
                                     run=types.SimpleNamespace(input={"intent": effective_intent}))

    monkeypatch.setattr(forge_mod, "generate_and_run", fake_gen)
    from karvyloop.runtime.main_loop import forge_slow_brain_factory
    sb = forge_slow_brain_factory(token=None, sandbox=None, gateway=None, workspace_root="/ws")
    text, _run = sb("小活")
    assert text == "done"
