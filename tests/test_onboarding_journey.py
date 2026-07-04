"""test_onboarding_journey — 「第一个 10 分钟」新手旅程(零 LLM,确定性)。

锁死旅程的四条命门(改坏任何一条,10 分钟演示就哑火):
1. 薄状态机:fresh → step1 → step2 → done/skipped;老实例(有 run 无状态文件)绝不弹旅程。
2. 演示任务文案(en/zh)按**前端真实组装**(附件内联在前、问题在后)必须召回命中
   data-analyst 系统技能 —— 回执(skill_name)才出得来。零 LLM:recall 是纯 token overlap。
3. 演示任务文案不得含 context_gate 依赖标记词 —— 否则第二句被 CV-9 判上下文依赖 →
   跳过召回 → 方法复用回执消失。
4. 端点契约 + 前端接线(app.js 真调这些端点;i18n 键在建好的 static/i18n.js 里)。
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from karvyloop.onboarding import (
    JOURNEY_STAGES,
    JOURNEY_TASKS,
    SAMPLE_NAME,
    compose_task_intent,
    load_sample,
    read_stage,
    sample_data_dir,
    write_stage,
)

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "karvyloop" / "console" / "static"


# ---- 1. 薄状态机 ----

def test_stage_machine_fresh_advance_and_grandfather(tmp_path):
    p = tmp_path / "onboarding.json"
    # 新用户:无状态文件 + 零 run → fresh
    assert read_stage(p, has_runs=False) == "fresh"
    # 老实例:无状态文件 + 有 run → done(升级上来的用户绝不突然弹新手旅程)
    assert read_stage(p, has_runs=True) == "done"
    # 推进 + 重入
    assert write_stage("step1", p) is True
    assert read_stage(p, has_runs=True) == "step1"   # 有状态文件 → 状态说了算
    assert write_stage("skipped", p) is True
    assert read_stage(p, has_runs=False) == "skipped"
    assert write_stage("fresh", p) is True           # 「重看旅程」重置
    assert read_stage(p, has_runs=True) == "fresh"
    # 合法集合外一律拒
    assert write_stage("hacked", p) is False
    assert read_stage(p, has_runs=False) == "fresh"


def test_stage_corrupted_file_falls_back(tmp_path):
    p = tmp_path / "onboarding.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_stage(p, has_runs=False) == "fresh"
    assert read_stage(p, has_runs=True) == "done"
    p.write_text(json.dumps({"stage": "bogus"}), encoding="utf-8")
    assert read_stage(p, has_runs=False) == "fresh"


# ---- 2. 样例数据随包 ----

def test_sample_data_ships_with_package():
    name, text = load_sample()
    assert name == SAMPLE_NAME
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("quarter,category"), "样例 CSV 表头变了(演示文案引用它)"
    assert len(lines) >= 20, "样例数据太小,演示没东西可分析"
    assert (sample_data_dir() / SAMPLE_NAME).exists()


# ---- 3. 演示任务必须召回命中 data-analyst(回执的命根;零 LLM 纯 overlap)----

def _all_demo_intents():
    name, text = load_sample()
    for lang, tasks in JOURNEY_TASKS.items():
        for key, task in tasks.items():
            yield f"{lang}.{key}", task, compose_task_intent(
                task, sample_name=name, sample_text=text)


def test_demo_intents_recall_hit_data_analyst(tmp_path):
    from karvyloop.crystallize.recall import recall
    from karvyloop.crystallize.skill_index import SkillIndex

    idx = SkillIndex()
    n = idx.rebuild_from_disk(tmp_path / "user_skills")   # 双扫:bundled 系统区 + 空用户区
    assert n >= 1, "bundled 系统技能没进索引"
    for label, _task, intent in _all_demo_intents():
        hit = recall(intent, skills_dir=tmp_path / "user_skills",
                     scope="user", skill_index=idx)
        assert hit is not None, f"{label} 演示任务没召回命中任何技能 → 回执出不来"
        assert hit.name == "data-analyst", f"{label} 命中了别的技能: {hit.name!r}"
        # dynamic = 方法重跑(绝不回放旧答案)—— 前端回执走「方法复用」不是「快脑命中」
        assert (hit.result_reuse or "dynamic").lower() == "dynamic", \
            f"{label}: data-analyst 该是 dynamic(存方法不存答案)"


def test_demo_intents_pass_context_gate():
    """演示文案(含样例数据正文)不得踩 CV-9 依赖标记词:第二句在**有上下文**的对话里发,
    一旦被判上下文依赖,drive 会跳过召回 → 演示的方法复用回执消失。"""
    from karvyloop.karvy.fastbrain.context_gate import is_context_dependent

    for label, task, intent in _all_demo_intents():
        assert is_context_dependent(task, has_context=True) is False, \
            f"{label} 文案踩了上下文依赖标记词: {task!r}"
        assert is_context_dependent(intent, has_context=True) is False, \
            f"{label} 组装后 intent(含样例数据)踩了依赖标记词"


# ---- 4. 端点契约(直接调 handler;J10 同款 SimpleNamespace 模式)----

def _req(main_loop=None, runtime_kwargs=None):
    kapp = types.SimpleNamespace(state=types.SimpleNamespace(
        main_loop=main_loop, runtime_kwargs=runtime_kwargs or {}))
    return types.SimpleNamespace(app=kapp)


def test_journey_endpoint_contract(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import (
        JourneyStageRequest, api_onboarding_journey, api_onboarding_journey_set)

    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    req = _req()
    j = api_onboarding_journey(req)
    assert j["stage"] == "fresh"
    assert j["llm_ready"] is False          # 无 main_loop/gateway → 如实说跑不了
    assert j["sample_name"] == SAMPLE_NAME
    for lang in ("en", "zh"):
        assert set(j["tasks"][lang]) == {"task1", "task2"}
    # 推进 → 持久
    r = api_onboarding_journey_set(JourneyStageRequest(stage="step1"), req)
    assert r["ok"] is True
    assert api_onboarding_journey(req)["stage"] == "step1"
    # 非法阶段拒
    r = api_onboarding_journey_set(JourneyStageRequest(stage="evil"), req)
    assert r["ok"] is False and r["reason"] == "bad_stage"
    # llm_ready:有 main_loop + gateway → True
    ml = types.SimpleNamespace(trace=None)
    j2 = api_onboarding_journey(_req(main_loop=ml, runtime_kwargs={"gateway": object()}))
    assert j2["llm_ready"] is True


def test_journey_grandfathers_existing_instance(tmp_path, monkeypatch):
    """老实例(Trace 有 run、无状态文件)→ done:升级绝不对老用户弹新手旅程。"""
    from karvyloop.console.routes_onboarding import api_onboarding_journey

    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    ml = types.SimpleNamespace(trace=types.SimpleNamespace(all_tasks=lambda: ["t1"]))
    assert api_onboarding_journey(_req(main_loop=ml))["stage"] == "done"


def test_sample_endpoint_returns_and_seeds_workspace(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import api_onboarding_sample

    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    ws = tmp_path / "work"
    ws.mkdir()
    req = _req(runtime_kwargs={"workspace_root": str(ws)})
    r = api_onboarding_sample(req)
    assert r["ok"] is True and r["name"] == SAMPLE_NAME
    assert r["text"].startswith("quarter,category")
    seeded = ws / SAMPLE_NAME
    assert seeded.exists(), "样例没 seed 进 workspace(文件面板看不见)"
    # 已存在不覆盖(用户可能改过它)
    seeded.write_text("user-modified", encoding="utf-8")
    api_onboarding_sample(req)
    assert seeded.read_text(encoding="utf-8") == "user-modified"


# ---- 5. 前端接线(静态断言;wiring 测试另有全量门)----

def test_frontend_wired_to_journey():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/onboarding/journey" in app_js, "前端没接旅程状态端点"
    assert "/api/onboarding/sample" in app_js, "前端没接样例数据端点"
    assert "_journeyOnDriveDone" in app_js, "drive_done 没接旅程状态机"
    assert "drive.method_reuse" in app_js, "方法复用回执(Cut1 可见化)没接进聊天"
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="journey-bar"' in html
    assert 'id="journey-replay"' in html, "「重看旅程」重入口丢了(可跳过必须可重入)"


def test_i18n_journey_keys_built():
    """journey.* / drive.method_reuse 必须在**建好的** static/i18n.js 里(en/zh parity
    由 test_console_i18n 全量锁;这里锁「新键真进了构建产物」)。"""
    i18n_js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for key in ("journey.title", "journey.chip1", "journey.chip2", "journey.skip",
                "journey.done_receipt", "journey.tagline", "journey.replay.title",
                "drive.method_reuse"):
        assert i18n_js.count(f'"{key}"') >= 2, f"i18n 键 {key} 没进构建产物(或缺一种语言)"


def test_stages_constant_locked():
    assert JOURNEY_STAGES == ("fresh", "step1", "step2", "done", "skipped")
