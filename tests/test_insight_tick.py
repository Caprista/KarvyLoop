"""test_insight_tick — task_insight daily 慢侧 tick(docs/82 Slice B)。

不变量:
① 池指纹 watermark:执行池零变 → 零 LLM 跳过(回写的 task_insight 事件不自触发)
② 信号冷却:烧过的信号 7 天内不重烧(池变了也不烧);冷却过了才重烧
③ 封顶:单批信号 ≤5、单 tick 一次 LLM、写入 ≤3
④ no-llm/未接线 → 跳过不炸
⑤ 写走 mem.write 唯一咽喉,provenance 全字段(source/provisional/kind/trace_ref/ts,env 带
  applies.device);回写 Trace kind="task_insight"
⑥ supersede 掀不翻 user_explicit:洞察与人明说的矛盾 → 洞察反被失效,人明说的站住
⑦ 坏状态文件当空(fail-safe)
⑧ 单项写入失败不连坐(其余照写,tick 不炸)
⑨ 软观察复现关在 tick 里真生效(1 run 背书不写)
⑩ app.py 慢侧维护接线在(insight_tick 单项失败有 _maintenance_item_failed 兜)
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.cognition.trace import TraceEntry, TraceStore  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402
from karvyloop.console.insight_tick import (  # noqa: E402
    MAX_SIGNALS_PER_TICK, MAX_WRITES_PER_TICK, TICK_TASK_ID, task_insight_tick,
)


# ---- 桩:LLM 层 stub(洞察编译器 / supersede 审查器分路由);memory/trace 走真实现 ----

class TextDelta:
    """名字必须叫 TextDelta:采集器按 type(ev).__name__ 认事件(与真网关事件同名)。"""

    def __init__(self, t):
        self.text = t


class _GW:
    """insight_reply 可为 str 或 callable(material)->str(echo 材料里的 ref)。"""

    def __init__(self, insight_reply="[]", supersede_reply='{"pairs":[]}'):
        self.insight_reply = insight_reply
        self.supersede_reply = supersede_reply
        self.insight_calls = 0
        self.supersede_calls = 0
        self.last_material = ""

    def resolve_model(self, scope):
        return "stub-model"

    async def complete(self, messages, tools, model_ref, *, system=None, **kw):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        material = messages[0]["content"] if messages else ""
        if "执行洞察编译器" in sys_text:
            self.insight_calls += 1
            self.last_material = material
            r = self.insight_reply(material) if callable(self.insight_reply) else self.insight_reply
            yield TextDelta(r)
        elif "一致性审查" in sys_text:
            self.supersede_calls += 1
            yield TextDelta(self.supersede_reply)
        else:
            yield TextDelta("[]")


def _echo_env(prefix="这台机器 pip 装包要走镜像源"):
    """从材料里抄真实 ref,每个 ref 出一条 env 候选(evidence 核回必过)。"""
    def _reply(material):
        refs = re.findall(r"\[ref=([^\]]+)\]", material)
        items = [{"content": f"{prefix}{i or ''}", "kind": "env", "evidence_ref": r}
                 for i, r in enumerate(refs)]
        return json.dumps(items, ensure_ascii=False)
    return _reply


def _app(trace, mem, gw):
    state = SimpleNamespace(
        memory=mem,
        runtime_kwargs={"gateway": gw, "model_ref": "m"},
        main_loop=SimpleNamespace(trace=trace),
    )
    return SimpleNamespace(state=state)


def _seed_retry_run(trace, task_id, *, ts=1.0, name="pip_install"):
    """一条"同名工具换参数重试后成功"的 atom_run(硬信号)。"""
    return trace.append(TraceEntry(
        task_id=task_id, kind="atom_run",
        payload={"atom_id": "a1", "input": {}, "output": {"text": "装好了"}, "success": True,
                 "tool_calls": [{"id": "c0", "name": name, "input": {"index": "pypi"}},
                                {"id": "c1", "name": name, "input": {"index": "mirror"}}],
                 "trace_ref": f"trace://a1/{task_id}", "terminal": "completed"},
        ts=ts, source="main_loop"))


def _seed_calm_run(trace, task_id, *, ts=1.0):
    """一条平静的成功 run(改池指纹但不产生信号)。"""
    return trace.append(TraceEntry(
        task_id=task_id, kind="atom_run",
        payload={"atom_id": "a9", "success": True, "tool_calls": [], "terminal": "completed",
                 "output": None},
        ts=ts, source="main_loop"))


def _run(coro):
    return asyncio.run(coro)


def _insights(mem):
    return [b for b in mem.index.all("personal")
            if (b.provenance or {}).get("source") == "task_insight"]


# ============ ① watermark:池零变 → 零 LLM ============

def test_watermark_pool_unchanged_zero_llm(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    sp = tmp_path / "insight_tick.json"
    r1 = _run(task_insight_tick(app, state_path=sp, now=1000.0))
    assert r1["ran"] is False and gw.insight_calls == 0   # 空池第一轮:平静,零 LLM
    _seed_retry_run(trace, "t1", ts=10.0)
    r2 = _run(task_insight_tick(app, state_path=sp, now=2000.0))
    assert r2["ran"] is True and r2["written"] == 1 and gw.insight_calls == 1
    # 池没变(回写的 task_insight 事件不在捞料面,不自触发)→ watermark 零 LLM
    r3 = _run(task_insight_tick(app, state_path=sp, now=3000.0))
    assert r3["ran"] is False and "watermark" in r3["reason"] and gw.insight_calls == 1


def test_quiet_pool_zero_llm(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    _seed_calm_run(trace, "t1", ts=1.0)
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert r["ran"] is False and gw.insight_calls == 0   # 平静:有池无信号,零 LLM
    assert _insights(mem) == []


# ============ ② 冷却:烧过的信号 7 天不重烧;过窗才重烧 ============

def test_signal_cooldown_no_reburn_then_expiry(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    sp = tmp_path / "s.json"
    _seed_retry_run(trace, "t1", ts=10.0)
    r1 = _run(task_insight_tick(app, state_path=sp, now=1000.0))
    assert r1["written"] == 1 and gw.insight_calls == 1
    # 池变了(加平静事件)但唯一信号在冷却窗内 → 零 LLM
    _seed_calm_run(trace, "t2", ts=20.0)
    r2 = _run(task_insight_tick(app, state_path=sp, now=1000.0 + 3600))
    assert r2["ran"] is False and "冷却" in r2["reason"] and gw.insight_calls == 1
    # 冷却过了 + 池又变了 → 允许重烧
    _seed_calm_run(trace, "t3", ts=30.0)
    r3 = _run(task_insight_tick(app, state_path=sp, now=1000.0 + 8 * 86400))
    assert r3["ran"] is True and gw.insight_calls == 2


# ============ ③ 封顶:单批信号 ≤5、一次 LLM、写入 ≤3 ============

def test_caps_signals_batch_and_writes(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    for i in range(7):
        _seed_retry_run(trace, f"t{i}", ts=float(i + 1))
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert gw.insight_calls == 1                                     # 单 tick 一次 LLM
    refs = re.findall(r"\[ref=([^\]]+)\]", gw.last_material)
    assert len(refs) == MAX_SIGNALS_PER_TICK                         # 喂进材料的信号封顶 5
    assert r["written"] == MAX_WRITES_PER_TICK == len(_insights(mem))  # 写入封顶 3


# ============ ④ 未接线跳过 ============

def test_no_llm_and_no_trace_skip(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    app = _app(trace, mem, None)   # gateway=None(--no-llm)
    r = _run(task_insight_tick(app, state_path=tmp_path / "a.json"))
    assert r["ran"] is False and "未接" in r["reason"]
    app2 = SimpleNamespace(state=SimpleNamespace(
        memory=mem, runtime_kwargs={"gateway": _GW()}, main_loop=None))
    r2 = _run(task_insight_tick(app2, state_path=tmp_path / "b.json"))
    assert r2["ran"] is False and "未接" in r2["reason"]   # 无 Trace 源同样诚实跳过


# ============ ⑤ 写咽喉 provenance 全字段 + Trace 回写 ============

def test_write_choke_provenance_and_trace_echo(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    ref = _seed_retry_run(trace, "t1", ts=10.0)
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert r["written"] == 1
    b = _insights(mem)[0]
    prov = b.provenance
    assert prov["source"] == "task_insight" and prov["provisional"] is True
    assert prov["kind"] == "env" and prov["ts"] == 1000.0
    assert prov["trace_ref"] == ref                      # 证据 ref 核回真实 Trace 条目
    assert prov["applies"]["device"]                     # env 类按设备圈定(非空)
    assert b.scope == "personal" and b.freshness_ts == 1000.0
    # 回写 Trace(kind="task_insight",审计可查)
    echo = trace.query(TICK_TASK_ID, kind="task_insight")
    assert len(echo) == 1 and echo[0].payload["trace_ref"] == ref
    assert echo[0].source == "insight_tick"


# ============ ⑥ supersede 掀不翻 user_explicit ============

def test_supersede_cannot_topple_user_explicit(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    old = Belief(content="这台机器 pip 装包要走官方源",
                 provenance={"source": "ingest", "ts": 1.0}, freshness_ts=1.0, scope="personal")
    mem.write(old)
    gw = _GW(insight_reply=_echo_env(),   # 洞察内容与旧条词面强重叠 → 必进 supersede 候选
             supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"contradict"}]}')
    app = _app(trace, mem, gw)
    _seed_retry_run(trace, "t1", ts=10.0)
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert r["written"] == 1 and gw.supersede_calls == 1
    assert old.invalid_at is None                        # 人明说的站住
    ins = _insights(mem)[0]
    assert ins.invalid_at is not None                    # auto 档洞察反被失效(两条都留库可审计)
    assert "lower provenance" in ins.invalid_reason


# ============ ⑦ 坏状态文件当空 ============

def test_broken_state_file_failsafe(tmp_path):
    sp = tmp_path / "insight_tick.json"
    sp.write_text("{ bad json !!", encoding="utf-8")
    trace, mem = TraceStore(), MemoryManager()
    gw = _GW(insight_reply=_echo_env())
    app = _app(trace, mem, gw)
    _seed_retry_run(trace, "t1", ts=10.0)
    r = _run(task_insight_tick(app, state_path=sp, now=1000.0))
    assert r["ran"] is True and r["written"] == 1        # 坏文件当空,不炸不锁死


# ============ ⑧ 单项写入失败不连坐 ============

class _FlakyMem(MemoryManager):
    def write(self, belief, **kw):
        if "BOOM" in (belief.content or ""):
            raise RuntimeError("disk full")
        return super().write(belief, **kw)


def test_single_write_failure_not_contagious(tmp_path):
    trace, mem = TraceStore(), _FlakyMem()
    def _reply(material):
        refs = re.findall(r"\[ref=([^\]]+)\]", material)
        items = [{"content": "这台机器 pip 要走镜像源", "kind": "env", "evidence_ref": refs[0]},
                 {"content": "BOOM 这条写不进去", "kind": "env", "evidence_ref": refs[0]},
                 {"content": "SFTP 坏的时候传文件走 base64", "kind": "correction",
                  "evidence_ref": refs[0]}]
        return json.dumps(items, ensure_ascii=False)
    gw = _GW(insight_reply=_reply)
    app = _app(trace, mem, gw)
    _seed_retry_run(trace, "t1", ts=10.0)
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert r["ran"] is True and r["written"] == 2        # 坏的跳过,其余照写,tick 不炸
    assert {b.content for b in _insights(mem)} == {
        "这台机器 pip 要走镜像源", "SFTP 坏的时候传文件走 base64"}


# ============ ⑨ 软观察复现关在 tick 里真生效 ============

def test_soft_observation_gate_wired_in_tick(tmp_path):
    trace, mem = TraceStore(), MemoryManager()
    def _reply(material):
        refs = re.findall(r"\[ref=([^\]]+)\]", material)
        return json.dumps([{"content": "月球背面有客户在发周报",   # 与任何 run 材料零词面重叠
                            "kind": "observation", "evidence_ref": refs[0]}], ensure_ascii=False)
    gw = _GW(insight_reply=_reply)
    app = _app(trace, mem, gw)
    _seed_retry_run(trace, "t1", ts=10.0)
    r = _run(task_insight_tick(app, state_path=tmp_path / "s.json", now=1000.0))
    assert r["ran"] is True and r["candidates"] == 1 and r["written"] == 0   # 软 1 见不写
    assert _insights(mem) == []


# ============ ⑩ app.py 慢侧维护接线在 ============

def test_maintenance_loop_wired():
    src = (ROOT / "karvyloop" / "console" / "app.py").read_text(encoding="utf-8")
    assert "from karvyloop.console.insight_tick import task_insight_tick" in src
    assert '_maintenance_item_failed(app, "insight_tick"' in src   # 单项失败不连坐兜底
