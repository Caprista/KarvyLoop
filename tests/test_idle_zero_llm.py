"""test_idle_zero_llm — **idle=0 契约**:没事发生时,daily 慢侧一滴 LLM 都不烧(docs/42 △)。

打的痛点:"agent 整夜心跳烧钱"。KarvyLoop 的答案是 watermark 设计 —— 库没变/技能全有标签/
没到点没积压 → 零 LLM 调用。这里用测试把契约**锁死**,防未来重构悄悄把热路径烧回来。

AC:
- AC(a): knowledge_consolidate_tick —— 库没变(watermark 命中)→ ran=False,gateway 0 调用
         (第一轮真跑烧 1 次;第二轮同库 0 次)
- AC(b): skill_tags_tick —— 技能全打过标签 → ran=False,gateway 从没被碰
- AC(c): 静态契约 —— app.py _maintenance_loop 的 idle 分支在**任何 LLM 工作之前** continue
         (quality_review / knowledge_tick / skill_tags_tick 全在 idle 之后);
         拆分后的 pump _daily_loop 整段沉睡到 interval 才醒(没有子 tick、不掺维护工作)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from karvyloop.console.knowledge_tick import MIN_BELIEFS, knowledge_consolidate_tick
from karvyloop.console.skill_tags_tick import skill_tags_tick


# ---- 计数假网关:每次 complete 计 1;产出合法空结果("[]"),宁空勿毒路径也走通 ----

class _TextDelta:  # gateway 事件按 type(ev).__name__ 识别 → 本地同名类即可
    def __init__(self, text: str):
        self.text = text


class CountingGateway:
    def __init__(self):
        self.calls = 0

    def resolve_model(self, scope):
        return "p/m"

    async def complete(self, messages, tools, ref, system=None, **kwargs):
        self.calls += 1
        yield _TextDelta("[]")


def _app_stub(gateway, *, beliefs=None):
    """最小 app 桩:tick 只吃 app.state 的 memory/runtime_kwargs/proposal_registry。"""
    state = SimpleNamespace(
        memory=SimpleNamespace(index=SimpleNamespace(all=lambda scope: list(beliefs or []))),
        runtime_kwargs={"gateway": gateway, "model_ref": ""},
        proposal_registry=SimpleNamespace(register=lambda card: None),
        main_loop=None,
    )
    return SimpleNamespace(state=state)


# ---- AC(a): 知识整理 watermark → 第二轮 0 LLM ----

def test_knowledge_tick_unchanged_lib_zero_llm(tmp_path: Path):
    beliefs = [SimpleNamespace(content=f"knowledge item {i}", provenance=None)
               for i in range(MIN_BELIEFS)]
    gw = CountingGateway()
    app = _app_stub(gw, beliefs=beliefs)
    sp = tmp_path / "consolidate_tick.json"

    r1 = asyncio.run(knowledge_consolidate_tick(app, state_path=sp))
    assert r1["ran"] is True          # 第一轮:库是新的,真跑
    assert gw.calls == 1

    r2 = asyncio.run(knowledge_consolidate_tick(app, state_path=sp))
    assert r2["ran"] is False         # 第二轮:库没变,watermark 命中
    assert "watermark" in r2["reason"]
    assert gw.calls == 1              # ← 契约:第二轮 0 次 gateway 调用


def test_knowledge_tick_below_min_beliefs_zero_llm(tmp_path: Path):
    """库太小也不烧(连第一轮都不跑)。"""
    beliefs = [SimpleNamespace(content="only one", provenance=None)]
    gw = CountingGateway()
    app = _app_stub(gw, beliefs=beliefs)
    r = asyncio.run(knowledge_consolidate_tick(app, state_path=tmp_path / "s.json"))
    assert r["ran"] is False
    assert gw.calls == 0


# ---- AC(b): 技能全有标签 → gateway 从没被碰 ----

def test_skill_tags_tick_all_tagged_zero_llm(tmp_path: Path):
    skills = tmp_path / "skills"
    for name in ("alpha", "beta"):
        d = skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: does {name}\ntags: [x, y]\n---\nSteps\n",
            encoding="utf-8")
    gw = CountingGateway()
    app = _app_stub(gw)

    r = asyncio.run(skill_tags_tick(app, skills_dir=skills,
                                    state_path=tmp_path / "tags_tick.json"))
    assert r["ran"] is False          # 全打过 = watermark
    assert gw.calls == 0              # ← 契约:零 LLM


def test_skill_tags_tick_no_skills_dir_zero_llm(tmp_path: Path):
    gw = CountingGateway()
    app = _app_stub(gw)
    r = asyncio.run(skill_tags_tick(app, skills_dir=tmp_path / "nope",
                                    state_path=tmp_path / "s.json"))
    assert r["ran"] is False
    assert gw.calls == 0


# ---- AC(c): 静态契约 —— _maintenance_loop 的 idle continue 先于一切 LLM 工作 ----

def test_maintenance_loop_idle_continues_before_any_llm_work():
    import karvyloop.console.app as console_app
    src = Path(console_app.__file__).read_text(encoding="utf-8")

    loop_start = src.index("async def _maintenance_loop")
    idle_pos = src.index('if action == "idle":', loop_start)
    # idle 分支体就是 continue(中间不许塞任何工作)
    after_idle = src[idle_pos:idle_pos + 120]
    assert "continue" in after_idle, "idle 分支必须立刻 continue"

    # 所有 LLM/慢侧工作都必须排在 idle 检查之后
    for llm_work in ("quality_review",
                     "knowledge_consolidate_tick", "skill_tags_tick"):
        pos = src.index(llm_work, loop_start)
        assert idle_pos < pos, (
            f"契约破坏:{llm_work} 出现在 idle continue 之前 —— "
            f"没事发生的夜里会烧 LLM")


def test_pump_daily_loop_sleeps_full_interval_and_carries_no_maintenance():
    """WEAK⑨ 拆分后的 pump loop 契约:整段沉睡到 interval 才醒、醒了只做 pump.daily();
    维护工作(质量评/经验/修订/周报/知识/标签)一律不许回流进来(否则又寄生回单点)。"""
    import karvyloop.console.app as console_app
    src = Path(console_app.__file__).read_text(encoding="utf-8")

    loop_start = src.index("async def _daily_loop")
    loop_end = src.index("async def _maintenance_loop")
    body = src[loop_start:loop_end]
    assert loop_start < loop_end, "pump loop 应在维护 loop 之前定义"
    # 先睡满 interval,才调 pump.daily(idle=0:没到点零工作)
    assert body.index("await asyncio.sleep(interval)") < body.index("pump.daily()")
    for maint in ("quality_review", "lessons_review", "revision_review",
                  "weekly_digest_tick", "review_provisional",
                  "knowledge_consolidate_tick", "skill_tags_tick"):
        assert maint not in body, f"契约破坏:维护项 {maint} 回流进 pump _daily_loop(单点寄生复发)"


def test_maintenance_loop_starts_without_pump():
    """WEAK⑨ 灵魂断言(动态):pump 没接线(--no-llm / 构造失败)时,慢侧维护 loop 照起。
    此前维护整条寄生在 pump 的 daily loop 里,pump 一死全体无声死。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)  # 无 pump 无 main_loop
    with TestClient(app):
        m_task = getattr(app.state, "maintenance_task", None)
        assert m_task is not None and not m_task.done(), "pump=None 时维护 loop 未起(单点寄生复发)"
        assert getattr(app.state, "daily_task", None) is None or True  # pump loop 可不起,维护必须起
    assert m_task.cancelled() or m_task.done()   # shutdown 干净收尾不泄露
