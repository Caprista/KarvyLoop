"""周报卡(weekly Trace digest)——契约测试。

锁五件事:
1. Trace query 时间窗:窗内命中/窗外不命中/边界闭区间;**旧调用不带窗行为不变**(InMemory + Sqlite 同款)。
2. 空周诚实:无数据出「这周很安静」卡,不崩、不吹数字;数据源缺席(None)标 available=False。
3. 聚合数字对得上真数据:全部走真生产路径造数(trace.append / TokenLedger.record /
   DecisionLog.record / TastePredictionStore 押注对账 / registry.register),不 mock 数据形态。
4. 水位幂等:同周(< 7 天)重调不重发;≥ 7 天再发;register 成功才推水位。
5. 卡 payload 形状:kind / 结构化 digest / markdown 字符串 / proposal_id 同周稳定。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.sqlite_trace import SqliteTraceStore  # noqa: E402
from karvyloop.cognition.trace import TraceEntry, TraceStore  # noqa: E402
from karvyloop.cognition.weekly_digest import (  # noqa: E402
    KIND_WEEKLY_DIGEST,
    build_weekly_digest,
    build_weekly_digest_proposal,
    render_digest_markdown,
    weekly_digest_tick,
)
from karvyloop.console.decision_log import DecisionLog  # noqa: E402
from karvyloop.crystallize.taste_eval import TastePredictionStore  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402
from karvyloop.llm.token_ledger import TokenLedger  # noqa: E402


NOW = 1_800_000_000.0          # 固定"现在"
T_IN = NOW - 3 * 86400.0       # 窗内(7 天窗)
T_OUT = NOW - 10 * 86400.0     # 窗外


# ---------------------------------------------------------------- 1. 时间窗查询

def _window_contract(trace) -> None:
    for ts in (10.0, 20.0, 30.0):
        trace.append(TraceEntry(task_id="t", kind="atom_run", payload={"ts": ts}, ts=ts))
    # 窗内命中、窗外不命中
    hit = trace.query("t", start_ts=15.0, end_ts=25.0)
    assert [e.ts for e in hit] == [20.0]
    assert trace.query("t", start_ts=100.0) == []
    assert trace.query("t", end_ts=5.0) == []
    # 边界闭区间(与 DecisionLog.query 同口径)
    assert [e.ts for e in trace.query("t", start_ts=20.0, end_ts=20.0)] == [20.0]
    # 旧调用不带窗:行为一字不变
    assert len(trace.query("t")) == 3
    assert len(trace.query("t", kind="atom_run")) == 3
    assert trace.query("t", kind="nope") == []
    # kind + 窗组合
    assert len(trace.query("t", kind="atom_run", start_ts=15.0)) == 2


def test_query_time_window_inmemory():
    _window_contract(TraceStore())


def test_query_time_window_sqlite(tmp_path):
    trace = SqliteTraceStore(tmp_path / "trace.sqlite")   # 真落盘路径
    _window_contract(trace)
    trace.close()


# ---------------------------------------------------------------- 真路径造数 helper

def _seed_trace(trace) -> None:
    """真 trace.append 造一周数据(照 main_loop / revision 的真实 payload 形态)。"""
    trace.append(TraceEntry(task_id="t1", kind="atom_run", ts=T_IN, source="main_loop",
                            payload={"atom_id": "atom.report", "input": {}, "output": {"ok": 1},
                                     "success": True, "tool_calls": ["fs.read"],
                                     "trace_ref": "trace://a", "ts": T_IN, "terminal": ""}))
    trace.append(TraceEntry(task_id="t2", kind="atom_run", ts=T_IN + 60, source="main_loop",
                            payload={"atom_id": "atom.scrape", "input": {}, "output": None,
                                     "success": False, "tool_calls": [],
                                     "trace_ref": "trace://b", "ts": T_IN + 60,
                                     "terminal": "budget_exhausted"}))
    # 窗外的不该被算进来
    trace.append(TraceEntry(task_id="t3", kind="atom_run", ts=T_OUT, source="main_loop",
                            payload={"atom_id": "atom.old", "success": True, "tool_calls": [],
                                     "trace_ref": "trace://old", "ts": T_OUT}))
    trace.append(TraceEntry(task_id="t4", kind="fast_brain_hit", ts=T_IN,
                            payload={"intent": "查天气", "sig": "s-f", "skill_name": "weather",
                                     "restored": False}, source="main_loop"))
    trace.append(TraceEntry(task_id="t5", kind="eval_fact", ts=T_IN, source="main_loop",
                            payload={"sig": "s-r", "success": True, "verified": True, "steps": 2,
                                     "trace_ref": "trace://r", "skill_name": "report",
                                     "skill_rerun": True}))
    trace.append(TraceEntry(task_id="t1", kind="eval_fact", ts=T_IN, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True, "steps": 1,
                                     "trace_ref": "trace://a"}))
    trace.append(TraceEntry(task_id="t1", kind="crystallize", ts=T_IN, source="main_loop",
                            payload={"sig": "s-a", "name": "skill_abc", "when_to_use": "写报告",
                                     "trace_ref": "trace://a"}))
    trace.append(TraceEntry(task_id="revision:s-r", kind="skill_revision", ts=T_IN,
                            source="revision",
                            payload={"sig": "s-r", "skill_name": "report", "mode": "auto",
                                     "n_samples": 5, "trigger": "confidence=0.4",
                                     "trace_refs": ["trace://r"], "note": "小改已落"}))
    trace.append(TraceEntry(task_id="revision:s-x", kind="skill_revision", ts=T_IN,
                            source="revision",
                            payload={"sig": "s-x", "skill_name": "scraper", "mode": "proposed",
                                     "n_samples": 6, "trigger": "confidence=0.3",
                                     "trace_refs": [], "note": "大改出卡"}))


def _seed_ledger(tmp_path) -> TokenLedger:
    """真 TokenLedger.record 造数(tokens.db 真落盘;clock 可控)。"""
    current = [T_OUT]
    led = TokenLedger(tmp_path / "tokens.db", clock=lambda: current[0])
    led.record(source="drive", model="m", input=999, output=1)          # 窗外
    current[0] = T_IN
    led.record(source="drive", model="m", input=100, output=50)
    led.record(source="forge", model="m", input=10, output=5)
    return led


def _seed_decisions() -> DecisionLog:
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="卡1", proposal_id="p1", now=T_IN)
    log.record(decision="ACCEPT", summary="卡2", proposal_id="p2", now=T_IN + 1)
    log.record(decision="REJECT", summary="卡3", proposal_id="p3", now=T_IN + 2)
    log.record(decision="DEFER", summary="老卡", proposal_id="p0", now=T_OUT)   # 窗外
    return log


def _seed_taste(n_outcomes: int) -> TastePredictionStore:
    """真押注→真对账(前瞻:先 record_prediction 再 resolve)。"""
    store = TastePredictionStore()
    for i in range(n_outcomes):
        store.record_prediction(f"tp{i}", "ACCEPT", 0.8, now=T_IN)
        store.resolve(f"tp{i}", "ACCEPT" if i % 2 == 0 else "REJECT", now=T_IN + 1)
    return store


def _pending_registry() -> PendingProposalRegistry:
    reg = PendingProposalRegistry()
    reg.register(Proposal(summary="要不要把周报接进日程", options=("ACCEPT", "DEFER", "REJECT"),
                          strength=0.7, evidence_refs=(), habit_id=7, model_ref="",
                          ts=NOW - 2 * 86400.0, kind="run_task"))
    return reg


# ---------------------------------------------------------------- 2. 空周诚实

def test_empty_week_is_honestly_quiet():
    d = build_weekly_digest(TraceStore(), None, None, None, NOW)
    assert d["quiet"] is True
    assert d["tasks"]["atom_runs"] == 0
    assert d["tasks"]["success_rate"] is None          # 0/0 不是 100%
    assert d["tasks"]["recall_hit_rate"] is None
    assert d["tokens"]["available"] is False and d["tokens"]["total"] == 0
    assert d["decisions"]["available"] is False
    assert d["decisions"]["taste"]["available"] is False
    assert d["pending"]["count"] == 0 and d["pending"]["oldest_age_days"] is None
    from karvyloop import i18n
    md = render_digest_markdown(d)
    # 正文骨架走 i18n(en+zh 双表)→ 按当前 locale 取表断言(locale 无关)
    assert i18n.t("weekly.md.quiet") in md
    # 空周也能成卡(不崩、不吹);卡文案走 i18n → 按当前 locale 取表断言(locale 无关)
    card = build_weekly_digest_proposal(d, now=NOW)
    assert i18n.t("proposal.weekly_digest.gist_quiet") in card.summary


def test_empty_stores_but_wired_are_available_and_zero(tmp_path):
    led = TokenLedger(tmp_path / "tokens.db")
    d = build_weekly_digest(TraceStore(), led, TastePredictionStore(),
                            PendingProposalRegistry(), NOW, decision_log=DecisionLog())
    assert d["quiet"] is True
    assert d["tokens"]["available"] is True and d["tokens"]["calls"] == 0
    assert d["decisions"]["available"] is True and d["decisions"]["total"] == 0
    assert d["decisions"]["taste"]["enough"] is False   # 样本不足如实写
    assert "样本不足" in d["decisions"]["taste"]["note"]


# ---------------------------------------------------------------- 3. 聚合对得上真数据

def test_aggregation_matches_seeded_data(tmp_path):
    trace = SqliteTraceStore(tmp_path / "trace.sqlite")   # 生产同款后端
    _seed_trace(trace)
    led = _seed_ledger(tmp_path)
    d = build_weekly_digest(trace, led, _seed_taste(4), _pending_registry(), NOW,
                            decision_log=_seed_decisions())
    # 任务:窗内 2 跑(1 成 1 败);窗外 t3 不计
    t = d["tasks"]
    assert t["atom_runs"] == 2 and t["succeeded"] == 1 and t["failed"] == 1
    assert t["success_rate"] == 0.5
    assert t["eval_facts"] == 2 and t["skill_reruns"] == 1 and t["fast_brain_hits"] == 1
    assert abs(t["recall_hit_rate"] - 2 / 3) < 1e-9      # (1 stable + 1 rerun) / (1 + 2 runs)
    # 失败清单可回链
    assert len(t["failures"]) == 1
    f = t["failures"][0]
    assert f["trace_ref"] == "trace://b" and f["atom_id"] == "atom.scrape"
    assert f["terminal"] == "budget_exhausted" and f["entry_ref"] == "t2:0"
    # token:窗内 165(drive 150 + forge 15);窗外 1000 不计
    assert d["tokens"]["total"] == 165 and d["tokens"]["calls"] == 2
    src = {s["source"]: s["total"] for s in d["tokens"]["by_source"]}
    assert src == {"drive": 150, "forge": 15}
    assert d["tokens"]["by_source"][0]["source"] == "drive"   # 烧得多在前
    # 技能:1 新结晶 + 修订落地 1 / 出卡 1,全带回链
    sk = d["skills"]
    assert sk["crystallized_count"] == 1
    assert sk["crystallized"][0]["name"] == "skill_abc"
    assert sk["crystallized"][0]["trace_ref"] == "trace://a"
    assert sk["revisions_landed"] == 1 and sk["revisions_proposed"] == 1
    # 决策:窗内 ACCEPT 2 / REJECT 1;窗外 DEFER 不计;口味 n=4 < 10 → 如实"样本不足"
    dec = d["decisions"]
    assert dec["accept"] == 2 and dec["reject"] == 1 and dec["defer"] == 0 and dec["total"] == 3
    assert dec["taste"]["n"] == 4 and dec["taste"]["hit_rate"] is None
    assert "样本不足" in dec["taste"]["note"]
    # 挂着的:1 张,挂龄 ≈ 2 天
    p = d["pending"]
    assert p["count"] == 1 and abs(p["oldest_age_days"] - 2.0) < 0.01
    assert p["oldest"]["kind"] == "run_task"
    assert d["quiet"] is False
    from karvyloop import i18n
    md = render_digest_markdown(d)
    assert i18n.t("weekly.md.quiet") not in md
    assert "trace://b" in md and "skill_abc" in md      # 回链/事实进卡面
    trace.close()


def test_taste_enough_reports_rate():
    d = build_weekly_digest(TraceStore(), None, _seed_taste(12), None, NOW)
    taste = d["decisions"]["taste"]
    assert taste["enough"] is True and taste["n"] == 12
    assert abs(taste["hit_rate"] - 0.5) < 1e-9          # 偶数位 ACCEPT 命中(6/12)


def test_summarize_fn_is_optional_and_fail_safe():
    trace = TraceStore()
    _seed_trace(trace)
    d = build_weekly_digest(trace, None, None, None, NOW, summarize_fn=lambda dig: "这周稳。")
    assert d["summary"] == "这周稳。"
    assert "这周稳。" in render_digest_markdown(d)

    def boom(_):
        raise RuntimeError("llm down")
    d2 = build_weekly_digest(trace, None, None, None, NOW, summarize_fn=boom)
    assert d2["summary"] is None                        # 宁空勿毒:总结失败数字照发
    assert d2["tasks"]["atom_runs"] == 2


# ---------------------------------------------------------------- 4. 水位幂等

def test_tick_watermark_idempotent_within_week(tmp_path):
    trace = TraceStore()
    _seed_trace(trace)
    reg = PendingProposalRegistry()
    wm = tmp_path / "weekly_digest_tick.json"

    r1 = asyncio.run(weekly_digest_tick(trace=trace, registry=reg, state_path=wm, now=NOW))
    assert r1["ran"] is True and r1["proposal_id"]
    assert len(reg) == 1
    # 同周重调:幂等跳过,不重发
    r2 = asyncio.run(weekly_digest_tick(trace=trace, registry=reg, state_path=wm, now=NOW + 3600))
    assert r2["ran"] is False and "幂等" in r2["reason"]
    assert len(reg) == 1
    # 7 天后:再发一张(窗口日期变了 → 不同 proposal_id)
    r3 = asyncio.run(weekly_digest_tick(trace=trace, registry=reg, state_path=wm,
                                        now=NOW + 7 * 86400.0 + 1))
    assert r3["ran"] is True and r3["proposal_id"] != r1["proposal_id"]
    assert len(reg) == 2


def test_tick_corrupt_state_treated_as_empty(tmp_path):
    wm = tmp_path / "weekly_digest_tick.json"
    wm.write_text("{not json", encoding="utf-8")
    r = asyncio.run(weekly_digest_tick(trace=TraceStore(), registry=PendingProposalRegistry(),
                                       state_path=wm, now=NOW))
    assert r["ran"] is True                              # 坏文件当空(fail-safe),照发
    assert r["quiet"] is True                            # 空周照实说安静


def test_tick_register_failure_keeps_watermark(tmp_path):
    """register 抛出 → 水位不推,下轮重试(不静默丢周报)。"""
    class BadReg:
        def pending(self):
            return []
        def register(self, p):
            raise OSError("disk full")
    wm = tmp_path / "weekly_digest_tick.json"
    try:
        asyncio.run(weekly_digest_tick(trace=TraceStore(), registry=BadReg(),
                                       state_path=wm, now=NOW))
        raised = False
    except OSError:
        raised = True
    assert raised
    assert not wm.exists()                               # 水位没推
    ok = asyncio.run(weekly_digest_tick(trace=TraceStore(), registry=PendingProposalRegistry(),
                                        state_path=wm, now=NOW))
    assert ok["ran"] is True                             # 下轮(修好后)照发


# ---------------------------------------------------------------- 5. 卡 payload 形状

def test_card_payload_shape(tmp_path):
    trace = TraceStore()
    _seed_trace(trace)
    d = build_weekly_digest(trace, _seed_ledger(tmp_path), _seed_taste(4),
                            _pending_registry(), NOW, decision_log=_seed_decisions())
    card = build_weekly_digest_proposal(d, now=NOW)
    assert card.kind == KIND_WEEKLY_DIGEST == "weekly_digest"
    assert card.options == ("ACCEPT", "DEFER", "REJECT")
    assert card.proposal_id.startswith("weekly_digest-0-")
    assert isinstance(card.payload["digest"], dict)
    assert card.payload["digest"]["window"]["days"] == 7
    for section in ("tasks", "tokens", "skills", "decisions", "pending"):
        assert section in card.payload["digest"]
    from karvyloop import i18n
    assert isinstance(card.payload["markdown"], str)
    assert card.payload["markdown"].startswith("## " + i18n.t("weekly.md.title"))
    assert card.model_ref == "" and card.strength == 1.0   # 零 LLM、确定性
    assert card.basis                                       # 决策卡必带依据(ch4)
    # 同周同数据 → proposal_id 稳定(幂等 register 覆盖);持久化 roundtrip 不丢
    card2 = build_weekly_digest_proposal(d, now=NOW)
    assert card2.proposal_id == card.proposal_id
    back = Proposal.from_dict(card.to_dict())
    assert back.payload["digest"]["tasks"]["atom_runs"] == 2


# ---------------------------------------------------------------- 6. 正文 i18n(双语硬规则)

# 中文骨架 headers/固定措辞(zh 表专有;英文正文里绝不该出现,否则 = 漏翻)。
# 用「小节标题 + 完整措辞」而非裸词 —— 裸词(如「周报」)可能出现在动态数据里(用户提案
# summary),不能据此判漏翻;完整骨架短语只可能来自骨架本身。
_ZH_SKELETON_HEADERS = ("## 周报 ·", "### 任务", "### 技能", "### 你拍的板", "### 还挂着的")
_ZH_SKELETON_PHRASES = ("这周很安静", "账本未接线", "决策流水未接线", "口味押注未接线",
                        "样本不足", "没有挂着的卡", "跑了 ", "成功率 ", "快脑/召回命中率",
                        "失败清单", "新结晶 ", "张卡等你拍")


def test_markdown_skeleton_localizes_en_zh(tmp_path):
    """正文骨架按 locale 定稿:en → 英文骨架、零中文骨架短语;zh → 中文骨架。
    动态数据(回链 trace_ref / 技能名 / 用户提案 summary)两语都原样保留(是数据,不翻)。"""
    from karvyloop import i18n
    trace = SqliteTraceStore(tmp_path / "trace.sqlite")
    _seed_trace(trace)
    d = build_weekly_digest(trace, _seed_ledger(tmp_path), _seed_taste(4),
                            _pending_registry(), NOW, decision_log=_seed_decisions())
    try:
        # --- en:正文骨架出英文,不含任何硬编码中文骨架短语 ---
        i18n.set_locale("en")
        md_en = render_digest_markdown(d)
        assert md_en.startswith("## Weekly digest ·")
        for section in ("### Tasks", "### Skills", "### Your calls", "### Still pending"):
            assert section in md_en, f"英文正文缺小节 {section}"
        for zh in _ZH_SKELETON_HEADERS + _ZH_SKELETON_PHRASES:
            assert zh not in md_en, f"英文正文漏中文骨架「{zh}」(违双语硬规则)"
        # 动态数据是数据,不翻 → 两语都在(回链 + 技能名)
        assert "trace://b" in md_en and "skill_abc" in md_en

        # --- zh:正文骨架回中文 ---
        i18n.set_locale("zh")
        md_zh = render_digest_markdown(d)
        for zh in _ZH_SKELETON_HEADERS:
            assert zh in md_zh, f"中文正文缺骨架「{zh}」"
        assert "trace://b" in md_zh and "skill_abc" in md_zh
    finally:
        i18n.set_locale(None)
    trace.close()


def test_quiet_and_unwired_lines_localize():
    """空周 + 未接线三条固定措辞也走 i18n(en 无中文骨架短语)。"""
    from karvyloop import i18n
    d = build_weekly_digest(TraceStore(), None, None, None, NOW)  # 全 None → 各源未接线
    try:
        i18n.set_locale("en")
        md = render_digest_markdown(d)
        assert i18n.t("weekly.md.quiet") in md
        assert "Ledger not wired" in md and "Decision ledger not wired" in md
        assert "taste betting not wired" in md
        # 空周无动态中文 → 完整骨架短语也全不该出现
        for zh in _ZH_SKELETON_HEADERS + _ZH_SKELETON_PHRASES:
            assert zh not in md, f"英文空周正文漏中文骨架「{zh}」"
    finally:
        i18n.set_locale(None)
