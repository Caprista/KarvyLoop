"""test_skills_curve_api — GET /api/skills/curve(结晶裸分/成长曲线,docs/57 P1 护城河可感知)。

契约(前端 sparkline/成长曲线在接,形状别改):
  {"bucket": "day", "promote_score", "min_success_rate",
   "skills": [{"sig", "name", "crystallized_ts", "points": [{"day", "ts", "usage_count",
     "success_count", "usage_score", "success_rate", "promote_progress", "reruns",
     "crystallized"}]}],
   "growth": {"points": [{"day", "ts", "skills_total", "promotions", "revisions",
     "runs_total", "avg_success_rate", "hit_rate"}]}}

铁律验证:全部从 Trace 回放推导(eval_fact / crystallize / skill_revision),只读;
分数用 crystallize.usage_score **同一个公式**(测试直接拿生产函数对账,不写魔法浮点);
去抖口径同 observe()(60s 窗口内重复不重复计数);checker_verdict 是"评"不是使用。

造数纪律:真 trace.append 走生产 payload 形态(main_loop.drive / record_verdict 同款字段),
可控时钟锚"今天正午"(同 test_tokens_query_api 模式),不 mock 数据形状。
"""
from __future__ import annotations

import pathlib
import sys
import time
import types

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceEntry, TraceStore  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.crystallize.crystallize import (  # noqa: E402
    MIN_SUCCESS_RATE, PROMOTE_SCORE, usage_score,
)
from karvyloop.crystallize.curve import build_curves  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.schemas import UsageStats  # noqa: E402

# 固定基准(同 test_tokens_query_api):今天本地正午,封顶墙钟 now-5s,保证"今天"锚点
# 永不在未来;过去时段的记录稳落各自本地日历日桶。
_NOW = time.time()
_TODAY_NOON = min(
    time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.localtime(_NOW)), "%Y-%m-%d")) + 12 * 3600,
    _NOW - 5.0,
)
_DAY = 86400.0


def _label(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _day_end(ts: float) -> float:
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) + _DAY


def _client_with_trace(trace) -> TestClient:
    ml = types.SimpleNamespace(trace=trace)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=ml)
    return TestClient(app)


def _seed(trace) -> None:
    """真生产 payload 形态:两个 sig 跨 3 个本地日 + 各类噪声。

    s-a(结晶技能):前天 1 成功 + 1 条 30s 内去抖重复;昨天 1 失败 + 1 次召回重跑成功
    + 晋级(crystallize);今天 1 条独立验收回流(checker_verdict,不算使用)。
    s-b(未晋级候选):昨天 2 次成功(间隔 >60s)。
    噪声:无 sig 的 eval_fact / atom_run 原文 —— 都不进曲线。
    """
    d2, d1 = _TODAY_NOON - 2 * _DAY, _TODAY_NOON - 1 * _DAY
    # --- s-a 前天:1 次成功 + 30s 内重复(去抖:不计数,只刷新 last_used)---
    trace.append(TraceEntry(task_id="t1", kind="eval_fact", ts=d2, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 2, "trace_ref": "trace://a1"}))
    trace.append(TraceEntry(task_id="t2", kind="eval_fact", ts=d2 + 30.0, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 2, "trace_ref": "trace://a2"}))
    # --- s-a 昨天:失败 1 + 召回重跑成功 1 + 晋级 ---
    trace.append(TraceEntry(task_id="t3", kind="eval_fact", ts=d1, source="main_loop",
                            payload={"sig": "s-a", "success": False, "verified": False,
                                     "steps": 5, "trace_ref": "trace://a3"}))
    trace.append(TraceEntry(task_id="t4", kind="eval_fact", ts=d1 + 120.0, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 2, "trace_ref": "trace://a4",
                                     "skill_name": "skill_report", "skill_rerun": True}))
    trace.append(TraceEntry(task_id="t4", kind="crystallize", ts=d1 + 180.0, source="main_loop",
                            payload={"sig": "s-a", "name": "skill_report",
                                     "when_to_use": "写周报", "trace_ref": "trace://a4"}))
    # --- s-a 今天:独立验收回流(record_verdict 形态)——是"评",不是一次使用 ---
    trace.append(TraceEntry(task_id="t5", kind="eval_fact", ts=_TODAY_NOON,
                            source="main_loop.verdict",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 0, "trace_ref": "verdict://x/1",
                                     "checker_verdict": True, "feedback": "ok"}))
    # --- s-b 候选:昨天 2 次成功(>60s,不去抖),未晋级 ---
    trace.append(TraceEntry(task_id="t6", kind="eval_fact", ts=d1 + 3600.0, source="main_loop",
                            payload={"sig": "s-b", "success": True, "verified": True,
                                     "steps": 3, "trace_ref": "trace://b1"}))
    trace.append(TraceEntry(task_id="t7", kind="eval_fact", ts=d1 + 7200.0, source="main_loop",
                            payload={"sig": "s-b", "success": True, "verified": True,
                                     "steps": 3, "trace_ref": "trace://b2"}))
    # --- 噪声:无 sig / 原文事件,不进曲线 ---
    trace.append(TraceEntry(task_id="t8", kind="eval_fact", ts=d1, source="main_loop",
                            payload={"success": True, "trace_ref": "trace://noise"}))
    trace.append(TraceEntry(task_id="t8", kind="atom_run", ts=d1, source="main_loop",
                            payload={"atom_id": "a1", "success": True, "tool_calls": [],
                                     "trace_ref": "trace://noise2", "ts": d1}))


# ---- 契约形状 ----

def test_curve_api_shape():
    trace = TraceStore()
    _seed(trace)
    r = _client_with_trace(trace).get("/api/skills/curve")
    assert r.status_code == 200
    j = r.json()
    assert set(j.keys()) == {"bucket", "promote_score", "min_success_rate", "skills", "growth"}
    assert j["bucket"] == "day"
    assert j["promote_score"] == PROMOTE_SCORE and j["min_success_rate"] == MIN_SUCCESS_RATE
    assert {s["sig"] for s in j["skills"]} == {"s-a", "s-b"}
    for s in j["skills"]:
        assert set(s.keys()) == {"sig", "name", "crystallized_ts", "points"}
        for p in s["points"]:
            assert set(p.keys()) == {"day", "ts", "usage_count", "success_count",
                                     "usage_score", "success_rate", "promote_progress",
                                     "reruns", "crystallized"}
    assert set(j["growth"].keys()) == {"points"}
    for p in j["growth"]["points"]:
        assert set(p.keys()) == {"day", "ts", "skills_total", "promotions", "revisions",
                                 "runs_total", "avg_success_rate", "hit_rate"}


# ---- 每技能时间桶:本地日历日、累计计数、去抖、verdict 不算使用 ----

def test_skill_series_day_buckets_and_counts():
    trace = TraceStore()
    _seed(trace)
    j = _client_with_trace(trace).get("/api/skills/curve").json()
    sa = next(s for s in j["skills"] if s["sig"] == "s-a")
    assert sa["name"] == "skill_report"
    assert sa["crystallized_ts"] == _TODAY_NOON - _DAY + 180.0
    days = [p["day"] for p in sa["points"]]
    # 3 个本地日历日桶,升序:前天 / 昨天 / 今天(今天没使用也补点 → 衰减可见)
    assert days == [_label(_TODAY_NOON - 2 * _DAY), _label(_TODAY_NOON - _DAY),
                    _label(_TODAY_NOON)], f"日桶不对: {days}"
    p2, p1, p0 = sa["points"]
    # 前天:2 条 eval_fact 但 30s 内重复被去抖(同 observe 口径)→ usage 1
    assert p2["usage_count"] == 1 and p2["success_count"] == 1 and p2["reruns"] == 0
    assert p2["success_rate"] == 1.0 and p2["crystallized"] is False
    # 昨天:+失败 1 +重跑成功 1 → 累计 3;晋级发生在当天 → crystallized 翻 true
    assert p1["usage_count"] == 3 and p1["success_count"] == 2 and p1["reruns"] == 1
    assert p1["success_rate"] == round(2 / 3, 4) and p1["crystallized"] is True
    # 今天:只有 checker_verdict(是"评"不是使用)→ 计数不变,点仍在(诚实衰减)
    assert p0["usage_count"] == 3 and p0["success_count"] == 2
    # 未晋级候选 s-b:曲线也要有(看着它爬向晋级线,这正是护城河可感知)
    sb = next(s for s in j["skills"] if s["sig"] == "s-b")
    assert sb["crystallized_ts"] is None
    assert all(p["crystallized"] is False for p in sb["points"])
    assert sb["points"][0]["usage_count"] == 2 and sb["points"][0]["success_rate"] == 1.0


def test_usage_score_uses_production_formula():
    """分数**必须**等于生产函数 crystallize.usage_score 在同一时刻的输出(7 天半衰期),
    不许另造公式 —— 点自带 ts(评估时刻),直接对账。"""
    trace = TraceStore()
    _seed(trace)
    j = _client_with_trace(trace).get("/api/skills/curve").json()
    sa = next(s for s in j["skills"] if s["sig"] == "s-a")
    p2, p1, p0 = sa["points"]
    d2, d1 = _TODAY_NOON - 2 * _DAY, _TODAY_NOON - 1 * _DAY
    # 前天桶在"当日结束"评估;去抖只刷新 last_used(d2+30)不计数
    assert p2["ts"] == _day_end(d2)
    exp2 = usage_score(UsageStats(usage_count=1, last_used_at=d2 + 30.0), now=p2["ts"])
    assert p2["usage_score"] == round(exp2, 4)
    exp1 = usage_score(UsageStats(usage_count=3, last_used_at=d1 + 120.0), now=p1["ts"])
    assert p1["usage_score"] == round(exp1, 4)
    assert p1["promote_progress"] == round(min(1.0, exp1 / PROMOTE_SCORE), 4)
    # 今天桶在 now 评估(封顶墙钟)→ 用点自带 ts 对账,长期不用曲线诚实衰减
    exp0 = usage_score(UsageStats(usage_count=3, last_used_at=d1 + 120.0), now=p0["ts"])
    assert p0["usage_score"] == round(exp0, 4)
    assert exp0 < exp1, "隔天没用,分数应该衰减(用进废退)"


# ---- 全库成长曲线 ----

def test_growth_curve_totals_over_time():
    trace = TraceStore()
    _seed(trace)
    j = _client_with_trace(trace).get("/api/skills/curve").json()
    pts = j["growth"]["points"]
    assert [p["day"] for p in pts] == [_label(_TODAY_NOON - 2 * _DAY),
                                       _label(_TODAY_NOON - _DAY), _label(_TODAY_NOON)]
    g2, g1, g0 = pts
    # 前天:只有 s-a 用了 1 次;还没晋级
    assert g2["skills_total"] == 0 and g2["promotions"] == 0
    assert g2["runs_total"] == 1 and g2["avg_success_rate"] == 1.0 and g2["hit_rate"] == 0.0
    # 昨天:s-a 晋级;全库 run 3+2=5,重跑 1 → 命中率 0.2;宏平均成功率 (2/3+1)/2
    assert g1["skills_total"] == 1 and g1["promotions"] == 1 and g1["revisions"] == 0
    assert g1["runs_total"] == 5 and g1["hit_rate"] == 0.2
    assert g1["avg_success_rate"] == round((2 / 3 + 1.0) / 2, 4)
    # 今天:没有新使用 → 累计不变
    assert g0["skills_total"] == 1 and g0["runs_total"] == 5


def test_revision_events_counted_in_growth():
    trace = TraceStore()
    _seed(trace)
    trace.append(TraceEntry(task_id="revision:s-a", kind="skill_revision",
                            ts=_TODAY_NOON - _DAY + 300.0, source="revision",
                            payload={"sig": "s-a", "skill_name": "skill_report",
                                     "mode": "auto", "note": "调整步骤顺序"}))
    j = _client_with_trace(trace).get("/api/skills/curve").json()
    g1 = j["growth"]["points"][1]
    assert g1["revisions"] == 1


# ---- sig 筛选 / 优雅空 ----

def test_sig_filter_returns_single_skill_growth_stays_library_wide():
    trace = TraceStore()
    _seed(trace)
    j = _client_with_trace(trace).get("/api/skills/curve", params={"sig": "s-b"}).json()
    assert [s["sig"] for s in j["skills"]] == ["s-b"]
    assert len(j["growth"]["points"]) == 3   # 顶部成长曲线不随筛选变(全库)
    j2 = _client_with_trace(trace).get("/api/skills/curve", params={"sig": "nope"}).json()
    assert j2["skills"] == [] and len(j2["growth"]["points"]) == 3


def test_empty_graceful_no_main_loop_no_trace_empty_library():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    j = TestClient(app).get("/api/skills/curve").json()
    assert j["skills"] == [] and j["growth"] == {"points": []}
    assert j["bucket"] == "day" and j["promote_score"] == PROMOTE_SCORE
    # 有 main_loop 但无 trace 属性
    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=types.SimpleNamespace())
    j2 = TestClient(app2).get("/api/skills/curve").json()
    assert j2["skills"] == [] and j2["growth"] == {"points": []}
    # 有 trace 但空库
    j3 = _client_with_trace(TraceStore()).get("/api/skills/curve").json()
    assert j3["skills"] == [] and j3["growth"] == {"points": []}


# ---- build_curves 直测(显式 now,完全确定) ----

def test_build_curves_explicit_now_decay_point_appended():
    """固定 now:最后一个使用日之后隔了 3 天 → 自动补"今天"点,分数按半衰期衰减。"""
    day0 = time.mktime(time.strptime("2026-06-15", "%Y-%m-%d")) + 12 * 3600
    trace = TraceStore()
    trace.append(TraceEntry(task_id="t1", kind="eval_fact", ts=day0, source="main_loop",
                            payload={"sig": "s-x", "success": True, "verified": True,
                                     "steps": 1, "trace_ref": "trace://x1"}))
    now = day0 + 3 * _DAY
    out = build_curves(trace, now=now)
    pts = out["skills"][0]["points"]
    assert [p["day"] for p in pts] == ["2026-06-15", _label(now)]
    assert pts[1]["ts"] == now
    assert pts[1]["usage_score"] == round(
        usage_score(UsageStats(usage_count=1, last_used_at=day0), now=now), 4)
    assert pts[1]["usage_score"] < pts[0]["usage_score"]


def test_build_curves_future_events_ignored():
    """now 之后的记录不进曲线(回放只回放到 now,不预支未来)。"""
    day0 = time.mktime(time.strptime("2026-06-15", "%Y-%m-%d")) + 12 * 3600
    trace = TraceStore()
    trace.append(TraceEntry(task_id="t1", kind="eval_fact", ts=day0, source="main_loop",
                            payload={"sig": "s-x", "success": True, "verified": True,
                                     "steps": 1, "trace_ref": "trace://x1"}))
    trace.append(TraceEntry(task_id="t2", kind="eval_fact", ts=day0 + 5 * _DAY, source="main_loop",
                            payload={"sig": "s-x", "success": True, "verified": True,
                                     "steps": 1, "trace_ref": "trace://x2"}))
    out = build_curves(trace, now=day0 + 3600)
    pts = out["skills"][0]["points"]
    assert len(pts) == 1 and pts[0]["usage_count"] == 1


# ---- 前端源断言(TS 源;static 构建由协调者统一做) ----

FRONTEND = ROOT / "karvyloop" / "console" / "frontend" / "src"


def test_frontend_ts_wires_curve_endpoint_and_svg():
    ts_src = (FRONTEND / "skills_panel.ts").read_text(encoding="utf-8")
    assert "/api/skills/curve" in ts_src, "skills_panel.ts 应调曲线端点"
    assert "createElementNS" in ts_src, "sparkline 应是纯 SVG 手画(不引第三方图表库)"
    assert "skill-growth" in ts_src and "skill-spark" in ts_src
    assert "skills.growth_title" in ts_src and "skills.spark_title" in ts_src
    for lib in ("chart.js", "echarts", "d3"):
        assert lib not in ts_src, f"不许引第三方图表库({lib})"


def test_frontend_i18n_growth_keys_both_locales_and_branding():
    i18n_src = (FRONTEND / "i18n.ts").read_text(encoding="utf-8")
    for key in ("skills.growth_title", "skills.growth_legend",
                "skills.growth_empty", "skills.spark_title"):
        assert i18n_src.count(f'"{key}"') == 2, f"{key} 应在 en/zh 两表各出现一次"
    # 招牌文案纪律:必须"越用越像你",绝不"越用越懂你"
    assert "越用越像你" in i18n_src
    assert "越用越懂你" not in i18n_src
    ts_src = (FRONTEND / "skills_panel.ts").read_text(encoding="utf-8")
    assert "懂你" not in ts_src
