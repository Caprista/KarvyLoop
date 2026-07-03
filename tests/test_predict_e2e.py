"""test_predict_e2e — 「你可能想做」(predict)全链真路径验收。

病根(2026-07-03 实测确诊,Hardy 投诉页签永远空):
  1. drive 事件只落 Trace **原文**层(main_loop._emit_funnel_event → append_raw),
     analyst 读的是**摘要**层 —— raw→summary 提炼器在生产路径无人调用(孤儿函数)
     → 摘要层永远空 → can_propose 永 False → analyst 永远沉默。
  2. 即使提炼跑了,产物 kind="distilled_summary" 不在 _SIGNAL_KINDS → 门控照样拒。
  3. 启动后没人触发 boot_poll,第一条建议要等 24h daily → 用户体感 = 永远空。

本文件锁全链(**trace 写入必须走真 drive 路径**,不许 append_summary 手塞):
  main_loop.drive → _emit_funnel_event → TraceIndex(raw)
  → pump.boot 内建 raw→summary 提炼 → IntentAnalyst(LLM stub 在 client 层)
  → broadcast h2a_proposal(WS)
外加:启动 boot_poll(lifespan)+ 确定性兜底(失败任务→重试提议)+ 提炼幂等 watermark。
"""
from __future__ import annotations

import pathlib
import sys
import time

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cli.intent_pump import build_proposal_pump  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.runtime.main_loop import MainLoop  # noqa: E402
from karvyloop.schemas import AtomRun  # noqa: E402


# ---- 工具:真 drive 用的 sync slow_brain 桩(套用 test_main_loop 惯例)----


def _make_slow_brain():
    n = [0]

    def slow_brain(intent: str):
        n[0] += 1
        run = AtomRun(
            atom_id=f"run-{n[0]}",
            input={"intent": intent, "x": n[0]},
            output={"text": f"ok-{intent}"},
            success=True,
            tool_calls=[{"name": "run_command"}],
            trace_ref=f"trace-{n[0]}",
            ts=0.0,
        )
        return f"ok-{intent}", run

    return slow_brain


class _FakeLlmClient:
    """stub 在 BehaviorPatternAnalyzer 的 LLM client 层(允许);记录进 prompt 的内容。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, model, messages, *, temperature=0.3):
        self.prompts.append(messages[0]["content"])
        return '[{"pattern": "用户常整理周报 — 可以固化成技能", "strength": 0.9}]'


# ---- 全链:真 drive → 漏斗 raw → 提炼 → analyst → h2a_proposal 广播 ----


def test_full_chain_drive_to_predict_proposal(tmp_path, monkeypatch) -> None:
    import karvyloop.cli.intent_pump as ip_mod

    fake = _FakeLlmClient()
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: fake)

    workbench = WorkbenchObserver()
    ml = MainLoop(skills_dir=tmp_path / "skills")
    ml.bootstrap()
    app = build_console_app(workbench=workbench, main_loop=ml)
    app.state.boot_poll_delay_s = -1  # 本测手动触发,关掉 lifespan 自动 boot
    bundle = build_proposal_pump(
        app, workbench=workbench,
        trace_db=tmp_path / "t.db", habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    # 生产同款接线(entry.py):drive 事件落共享漏斗原文层
    ml.set_trace_funnel(bundle.trace_index)

    try:
        # 真 drive 路径写 trace(绝不 append_summary 手塞)
        sb = _make_slow_brain()
        ml.drive("整理本周周报", slow_brain=sb)
        ml.drive("汇总项目进度", slow_brain=sb)
        ml.drive("整理本周周报再来一次", slow_brain=sb)

        # drive 只写原文层;摘要层此刻必须还是空(证明没绕道手塞)
        assert len(bundle.trace_index.list_raw()) >= 3
        assert bundle.trace_index.list_summary() == []

        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # snapshot
            ws.send_json({"type": "propose", "payload": {}})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_proposal"
            assert msg["payload"] is not None, "全链修复后 propose 不该再沉默"
            assert "周报" in msg["payload"]["summary"]
            assert msg["payload"]["strength"] == 0.9

        # 提炼真跑了:摘要层有 distilled_summary,且 recent_intents 来自真 drive
        summaries = bundle.trace_index.list_summary()
        distilled = [r for r in summaries
                     if isinstance(r.payload, dict) and r.payload.get("kind") == "distilled_summary"]
        assert distilled, "pump.boot 应先跑 raw→summary 提炼"
        assert any("周报" in i for i in distilled[0].payload.get("recent_intents", []))
        # LLM 真吃到了 drive 来的行为(不是空 prompt)
        assert fake.prompts and "周报" in fake.prompts[0]
    finally:
        bundle.close()


def test_distill_is_idempotent_without_new_raw(tmp_path, monkeypatch) -> None:
    """watermark:没有新原文事件时,反复 propose 不堆重复摘要。"""
    import karvyloop.cli.intent_pump as ip_mod
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: _FakeLlmClient())

    workbench = WorkbenchObserver()
    ml = MainLoop(skills_dir=tmp_path / "skills")
    ml.bootstrap()
    app = build_console_app(workbench=workbench, main_loop=ml)
    app.state.boot_poll_delay_s = -1
    bundle = build_proposal_pump(
        app, workbench=workbench,
        trace_db=tmp_path / "t.db", habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    ml.set_trace_funnel(bundle.trace_index)
    try:
        ml.drive("查天气", slow_brain=_make_slow_brain())
        client = TestClient(app)
        for _ in range(3):
            r = client.post("/api/propose")
            assert r.status_code == 200
        n_distilled = sum(
            1 for rec in bundle.trace_index.list_summary()
            if isinstance(rec.payload, dict) and rec.payload.get("kind") == "distilled_summary"
        )
        assert n_distilled == 1, f"同一批原文重复提炼了 {n_distilled} 次(watermark 失效)"
    finally:
        bundle.close()


# ---- 启动 boot_poll(lifespan)----


def test_lifespan_boot_poll_calls_pump(tmp_path) -> None:
    """console 起来后自动跑一次 pump.boot(不等 24h daily)。"""
    calls = {"boot": 0}

    class _SpyPump:
        async def boot(self, recent_n: int = 20):
            calls["boot"] += 1
            return None, 0

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_pump = _SpyPump()
    app.state.boot_poll_delay_s = 0  # 立即
    with TestClient(app):
        deadline = time.time() + 5
        while calls["boot"] == 0 and time.time() < deadline:
            time.sleep(0.05)
    assert calls["boot"] == 1, "lifespan 应在启动后自动触发一次 pump.boot"


def test_lifespan_boot_poll_deterministic_fallback(tmp_path) -> None:
    """pump 未接(无 LLM)时,启动 boot_poll 走确定性兜底:失败任务 → 重试提议进待决表。

    (前端开机 fetchPendingProposals 从待决表捞,页签至少有确定性内容。)"""
    from karvyloop.console.tasks import TaskRegistry, TaskStore
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    reg = TaskRegistry(store=TaskStore(tmp_path / "tasks.json"))
    tid = reg.start(who="小卡", intent="每周汇总销售数据")
    reg.finish(tid, error="网络中断,没跑完")
    app.state.task_registry = reg
    app.state.proposal_registry = PendingProposalRegistry(
        persist_path=tmp_path / "pending.json")
    app.state.boot_poll_delay_s = 0

    with TestClient(app):
        deadline = time.time() + 5
        while len(app.state.proposal_registry) == 0 and time.time() < deadline:
            time.sleep(0.05)
    pending = app.state.proposal_registry.pending()
    assert pending, "启动兜底应把 run_task 重试提议登记进待决表"
    assert any(getattr(p, "kind", "") == "run_task" for p in pending)
    assert any("销售数据" in getattr(p, "summary", "") for p in pending)


def test_lifespan_boot_poll_disabled_by_negative_delay(tmp_path) -> None:
    """delay < 0 → 不排程(测试/嵌入方可关)。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.boot_poll_delay_s = -1
    with TestClient(app):
        assert getattr(app.state, "boot_poll_task", None) is None


# ---- 解析:真模型(思考型)输出的散文含方括号,不许把 parse 搞死 ----


def test_extract_json_array_survives_prose_with_brackets() -> None:
    """2026-07-03 真跑确诊:MiniMax-M3 散文里有 [x] 之类,旧「首[…末]」切片必败。"""
    import json

    from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array

    prose = (
        "分析如下[要点1]:用户常整理周报[证据 #3]。\n"
        '结论:[{"pattern": "用户常整理周报", "strength": 0.8}]\n'
        "(以上评分[0-1]仅供参考)"
    )
    arr = json.loads(_extract_json_array(prose))
    assert arr == [{"pattern": "用户常整理周报", "strength": 0.8}]

    # 纯数组 / 带 fence 仍照常
    assert json.loads(_extract_json_array('[{"pattern": "p", "strength": 1.0}]'))
    assert json.loads(_extract_json_array('```json\n[{"pattern": "p", "strength": 1.0}]\n```'))

    # 完全没有合法数组 → 原文返回,交给 json.loads 报错(宁空勿毒,不抽 prose)
    import pytest

    with pytest.raises(json.JSONDecodeError):
        json.loads(_extract_json_array("没有数组,只有散文[未闭合"))


def test_extract_json_array_prefers_all_dict_array_over_prose_array() -> None:
    """对抗验收边缘:散文里的合法数组(`看了 [1,2] 条`)不许遮蔽后面的真数组 ——
    第一遍扫描优先返回"非空且全 dict 项"的数组,否则建议整批丢失。"""
    import json

    from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array

    prose = '看了 [1,2] 条。结论:[{"pattern": "用户常整理周报", "strength": 0.8}]'
    arr = json.loads(_extract_json_array(prose))
    assert arr == [{"pattern": "用户常整理周报", "strength": 0.8}]

    # 真数组被截断时,散文数组同样不许上位(截断兜底只认 dict 项,优先级在其后)
    truncated = '看了 [1,2] 条。结论:[{"pattern": "整理周报", "strength": 0.9}, {"pat'
    assert json.loads(_extract_json_array(truncated)) == [
        {"pattern": "整理周报", "strength": 0.9}]

    # 全文只有非 dict 数组 → 仍退回第一个合法数组(不比旧行为更空)
    assert json.loads(_extract_json_array("只有 [1,2] 没别的")) == [1, 2]


def test_extract_json_array_salvages_truncated_tail() -> None:
    """2026-07-03 真跑取证:思考型模型把 max_tokens 烧在思考上,数组尾被截在半个对象。

    完整的前项要捞回来(严格 JSON 逐项 decode),被截的尾项丢掉;
    散文里 [0-1] 这类非 dict 数组不许被误捞。"""
    import json

    from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array

    # 真实 MiniMax-M3 输出形态(截断,无闭合 ]):
    truncated = (
        '[{"pattern":"用户高频地将本周工作内容整理成周报要点或大纲", "strength":0.95},'
        ' {"pattern":"用户习惯用一句话或简短清单的形式获取信'
    )
    arr = json.loads(_extract_json_array(truncated))
    assert arr == [{"pattern": "用户高频地将本周工作内容整理成周报要点或大纲", "strength": 0.95}]

    # 非 dict 项(散文 [0-1])不算数
    assert json.loads(_extract_json_array('评分范围[0-1]。[{"pattern":"p","strength":0.5},{"x"')) \
        == [{"pattern": "p", "strength": 0.5}]


# ---- 门控:摘要层真实产物 kind 要能过 can_propose ----


def test_can_propose_accepts_distilled_and_conversation_summary() -> None:
    from karvyloop.karvy.atoms import IntentAnalyst, TraceChunk, TRIGGER_BOOT

    class _Rec:
        def __init__(self, kind: str) -> None:
            self.payload = {"kind": kind}
            self.seq = 1

    analyst = IntentAnalyst(
        workbench=WorkbenchObserver(),
        habit_store=None, trace_index=None, behavior_analyzer=None,
    )
    for kind in ("distilled_summary", "conversation_summary", "intent"):
        chunk = TraceChunk(summaries=(_Rec(kind),), source=TRIGGER_BOOT, ts=0.0)
        assert analyst.can_propose(chunk) is True, f"kind={kind} 应过门控"
    chunk = TraceChunk(summaries=(_Rec("noise"),), source=TRIGGER_BOOT, ts=0.0)
    assert analyst.can_propose(chunk) is False
