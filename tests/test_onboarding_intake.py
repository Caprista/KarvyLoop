"""test_onboarding_intake — 人格采集器(旅程开头 4 问 → 决策偏好种子)。

锁的合同:
1. 问题清单:4 题、id 唯一、每题 ≥2 选项、en/zh 双语齐全、同题选项内容**必须不同**
   (区分度:不是星座测试,不同选项导向可观察差异)。
2. 种子 = 真决策偏好 Belief(explicit/confirmed,provenance 带 user_explicit +
   intake_q/intake_opt),写进 MemoryManager → **落盘 beliefs.json**;prealign 立即认它。
3. 跳过 = 零种子;重答 = 替换同题旧种子(不留自相矛盾);未知题/选项静默忽略(宁缺勿毒)。
4. 状态:与旅程同文件(onboarding.json),合并写不互踩;「重看旅程」(stage→fresh)
   连带重置采集器(可跳过可重来)。
5. 端点契约 + 前端接线 + 文案纪律(回执绝不说"我懂你了")。
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from karvyloop.onboarding import read_intake, read_stage, write_intake, write_stage
from karvyloop.onboarding_intake import (
    INTAKE_ORIGIN,
    INTAKE_QUESTIONS,
    make_seed_belief,
    questions_payload,
    seed_answers,
)

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "karvyloop" / "console" / "static"


def _mem(tmp_path):
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    return MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))


# ---- 1. 问题清单(区分度 + 双语) ----

def test_questions_shape_and_bilingual():
    assert len(INTAKE_QUESTIONS) == 4
    ids = [q["id"] for q in INTAKE_QUESTIONS]
    assert len(set(ids)) == 4, "题 id 必须唯一"
    for q in INTAKE_QUESTIONS:
        assert q["kind"] in ("taste", "standing", "constraint")
        for lang in ("en", "zh"):
            assert q["question"][lang].strip(), f"{q['id']} 缺 {lang} 题面"
        assert len(q["options"]) >= 2, f"{q['id']} 选项不足 2"
        contents = set()
        for o in q["options"]:
            for lang in ("en", "zh"):
                assert o["label"][lang].strip(), f"{q['id']}.{o['id']} 缺 {lang} 标签"
                assert o["content"][lang].strip(), f"{q['id']}.{o['id']} 缺 {lang} 偏好内容"
            contents.add(o["content"]["en"])
            contents.add(o["content"]["zh"])
        # 区分度:同题不同选项的偏好内容必须互不相同(不同答案 → 不同行为)
        assert len(contents) == 2 * len(q["options"]), f"{q['id']} 选项内容有重复(没有区分度)"


def test_filing_question_feeds_butler_lesson():
    """filing 一题是采集器与文件管家第一课的咬合点:选项 id 必须是 by_type/by_time
    (butler_lesson.filing_mode_from_memory 按 intake_opt 确定性消费)。"""
    q = next(q for q in INTAKE_QUESTIONS if q["id"] == "filing")
    assert {o["id"] for o in q["options"]} == {"by_type", "by_time"}


def test_questions_payload_for_frontend():
    qs = questions_payload()
    assert [q["id"] for q in qs] == [q["id"] for q in INTAKE_QUESTIONS]
    for q in qs:
        assert set(q["question"]) >= {"en", "zh"}
        for o in q["options"]:
            assert set(o["label"]) >= {"en", "zh"}
            assert "content" not in o, "偏好内容不必发给前端(种子在后端定稿)"


# ---- 2. 种子 = 真决策偏好 Belief ----

def test_seed_belief_is_confirmed_explicit_decision_pref():
    from karvyloop.crystallize.decision_pref import is_decision_pref, is_high_value
    q = INTAKE_QUESTIONS[0]
    opt = q["options"][0]
    b = make_seed_belief(q, opt, locale="zh", now=1000.0)
    assert is_decision_pref(b)
    p = b.provenance
    assert p["explicit"] is True and p["status"] == "confirmed"
    assert p["origin"] == INTAKE_ORIGIN
    assert p["intake_q"] == q["id"] and p["intake_opt"] == opt["id"]
    assert p["kind"] == q["kind"]
    assert b.content == opt["content"]["zh"]
    assert b.scope == "personal" and b.freshness_ts == 1000.0
    # 用户明说 → 高价值级置信(0.7);evidence 带人话 gist(卡上"来自你的拍板"可核)
    assert is_high_value(b)
    gists = [e["gist"] for e in p["evidence"] if isinstance(e, dict) and e.get("gist")]
    assert gists and opt["label"]["zh"] in gists[0]


def test_seed_answers_persists_to_beliefs_json(tmp_path):
    mem = _mem(tmp_path)
    seeded = seed_answers({"output_style": "conclusion_first", "filing": "by_time"},
                          mem=mem, locale="en")
    assert len(seeded) == 2
    raw = (tmp_path / "beliefs.json").read_text(encoding="utf-8")
    for b in seeded:
        assert b.content in raw, "种子没落盘 beliefs.json(重启会丢)"
    assert '"intake_q": "filing"' in raw or '"intake_q":"filing"' in raw


def test_seed_answers_ignores_unknown_and_skips(tmp_path):
    mem = _mem(tmp_path)
    assert seed_answers({}, mem=mem) == []                                   # 全跳过 = 零种子
    assert seed_answers({"astrology": "aries"}, mem=mem) == []               # 未知题忽略
    assert seed_answers({"tone": "shouty"}, mem=mem) == []                   # 未知选项忽略
    assert seed_answers({"tone": "direct"}, mem=None) == []                  # 无认知库不炸


def test_retake_replaces_same_question_seed(tmp_path):
    """重答替换:filing 先选 by_type 再选 by_time → 库里只剩 by_time 一条(不自相矛盾)。"""
    from karvyloop.crystallize.decision_pref import is_decision_pref
    mem = _mem(tmp_path)
    seed_answers({"filing": "by_type"}, mem=mem, locale="zh")
    seed_answers({"filing": "by_time"}, mem=mem, locale="zh")
    seeds, seen = [], set()
    for sc in ("personal", "domain"):
        for b in mem.index.all(sc):
            if id(b) in seen:
                continue
            seen.add(id(b))
            if is_decision_pref(b) and b.provenance.get("intake_q") == "filing":
                seeds.append(b)
    assert len(seeds) == 1
    assert seeds[0].provenance["intake_opt"] == "by_time"


def test_seeds_flow_into_prealign(tmp_path):
    """楔子接线:种子立即进 prealign_block(提案前预对齐)—— 不是写进抽屉里落灰。"""
    from karvyloop.crystallize.decision_pref import prealign_block
    mem = _mem(tmp_path)
    seeded = seed_answers({"unsure": "ask_first"}, mem=mem, locale="zh")
    beliefs = list(mem.index.all("personal"))
    block = prealign_block(beliefs, query="拿不准的时候怎么办")
    assert seeded[0].content in block


# ---- 3/4. 状态:与旅程同文件、合并写、重看重置 ----

def test_intake_state_coexists_with_stage(tmp_path):
    p = tmp_path / "onboarding.json"
    assert write_stage("step1", p)
    assert write_intake({"done": True, "answers": {"tone": "direct"}}, p)
    assert read_stage(p) == "step1"
    assert read_intake(p)["done"] is True
    assert write_stage("step2", p)                     # 合并写:intake 不被 stage 写踩掉
    assert read_intake(p)["done"] is True
    assert write_stage("fresh", p)                     # 重看旅程 → 采集器一起重来
    assert read_intake(p) == {}


def test_intake_state_corrupted_file(tmp_path):
    p = tmp_path / "onboarding.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_intake(p) == {}
    assert write_intake({"done": True}, p)             # 坏文件也能恢复写
    assert read_intake(p)["done"] is True


def test_concurrent_stage_and_intake_writes_keep_both_keys(tmp_path):
    """对抗验收 NIT #11:stage 与 intake 同住一文件,两个写函数都 read-modify-write。
    并发下无锁会 lost-update 掉一个兄弟键 —— 加锁后两键都必须活着。"""
    import threading

    p = tmp_path / "onboarding.json"
    write_stage("fresh", p)                            # 起点:文件存在
    errs: list = []

    def hammer_stage():
        try:
            for _ in range(50):
                write_stage("step1", p)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    def hammer_intake():
        try:
            for i in range(50):
                write_intake({"done": True, "answers": {"tone": f"v{i}"}}, p)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=hammer_stage) for _ in range(3)] + \
         [threading.Thread(target=hammer_intake) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert not errs, f"并发写抛异常: {errs}"
    # 两个兄弟键都还在(无锁时其中一个会被对方的整体覆盖写抹掉)
    assert read_stage(p) == "step1"
    assert read_intake(p).get("done") is True


# ---- 5. 端点契约(SimpleNamespace 模式,同 test_onboarding_journey)----

def _req(memory=None):
    kapp = types.SimpleNamespace(state=types.SimpleNamespace(
        main_loop=None, runtime_kwargs={}, memory=memory))
    return types.SimpleNamespace(app=kapp)


def test_journey_endpoint_carries_intake(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import api_onboarding_journey
    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    j = api_onboarding_journey(_req())
    assert j["intake"]["done"] is False
    assert [q["id"] for q in j["intake"]["questions"]] == [q["id"] for q in INTAKE_QUESTIONS]


def test_intake_endpoint_seeds_and_marks_done(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import (
        IntakeRequest, api_onboarding_intake, api_onboarding_journey)
    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    mem = _mem(tmp_path)
    req = _req(memory=mem)
    r = api_onboarding_intake(IntakeRequest(answers={"tone": "direct", "filing": "by_type"}), req)
    assert r["ok"] is True and r["seeded_n"] == 2 and len(r["seeded"]) == 2
    assert "persist_error" not in r
    raw = (tmp_path / "beliefs.json").read_text(encoding="utf-8")
    for content in r["seeded"]:
        assert content in raw
    assert api_onboarding_journey(req)["intake"]["done"] is True


def test_intake_endpoint_skip_all_zero_seeds(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import (
        IntakeRequest, api_onboarding_intake, api_onboarding_journey)
    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    r = api_onboarding_intake(IntakeRequest(answers={}), _req())   # 无 memory 也行:零种子
    assert r["ok"] is True and r["seeded_n"] == 0
    assert api_onboarding_journey(_req())["intake"]["done"] is True
    assert not (tmp_path / "beliefs.json").exists()


def test_intake_endpoint_honest_without_memory(tmp_path, monkeypatch):
    """有答案但认知库未接 → 如实拒、不标 done(绝不假装"记下了")。"""
    from karvyloop.console.routes_onboarding import (
        IntakeRequest, api_onboarding_intake, api_onboarding_journey)
    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    r = api_onboarding_intake(IntakeRequest(answers={"tone": "direct"}), _req(memory=None))
    assert r["ok"] is False and r["reason"] == "no_memory"
    assert api_onboarding_journey(_req())["intake"]["done"] is False


def test_journey_replay_resets_intake(tmp_path, monkeypatch):
    from karvyloop.console.routes_onboarding import (
        IntakeRequest, JourneyStageRequest, api_onboarding_intake,
        api_onboarding_journey, api_onboarding_journey_set)
    monkeypatch.setenv("KARVYLOOP_ONBOARDING_PATH", str(tmp_path / "onboarding.json"))
    api_onboarding_intake(IntakeRequest(answers={}), _req())
    assert api_onboarding_journey(_req())["intake"]["done"] is True
    api_onboarding_journey_set(JourneyStageRequest(stage="fresh"), _req())   # 「重看旅程」
    assert api_onboarding_journey(_req())["intake"]["done"] is False


# ---- 6. 前端接线 + 文案纪律 ----

def test_frontend_wired_to_intake():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/onboarding/intake" in app_js, "前端没接采集器提交端点"
    assert "_renderIntake" in app_js and "_intakeSubmit" in app_js
    # 采集器住在旅程 fresh 阶段、第一个 chip 前(老用户 stage=done → 整条旅程不弹,采集器天然不弹)
    assert "_journey.intake" in app_js


def test_i18n_intake_and_butler_keys_built():
    i18n_js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for key in ("intake.lead", "intake.skip_all", "intake.receipt", "intake.receipt_skip",
                "butler.lesson_offer", "butler.lesson_chip", "butler.lesson_empty",
                "butler.plan_title", "butler.plan_hint"):
        assert i18n_js.count(f'"{key}"') >= 2, f"i18n 键 {key} 没进构建产物(或缺一种语言)"


def test_receipt_copy_discipline_never_claims_understanding():
    """文案纪律(招牌是「越用越像你」):采集器回执说"记下你的标准"(预对齐),
    **绝不说"我懂你了" / "I understand you"**。直接锁构建产物里的回执文案行。"""
    i18n_js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    receipt_lines = [ln for ln in i18n_js.splitlines()
                     if '"intake.receipt"' in ln or '"intake.receipt_skip"' in ln
                     or '"intake.lead"' in ln]
    assert len(receipt_lines) >= 6, "en/zh 各三条采集器文案没都进构建产物"
    for ln in receipt_lines:
        assert "懂你" not in ln, f"文案纪律破了(说了'懂你'): {ln.strip()[:80]}"
        assert "understand you" not in ln.lower(), f"文案纪律破了: {ln.strip()[:80]}"
        assert "know you" not in ln.lower(), f"文案纪律破了: {ln.strip()[:80]}"
