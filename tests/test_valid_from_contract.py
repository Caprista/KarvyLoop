"""P0 修复④:valid_from 时间语义契约(docs/66 §技术底)—— 判定 + 锁。

审计原报:「ingest 不写 valid_from;supersede 不回填『何时被推翻』」。核实结论:
- **摄入不写 valid_from 是契约本身,不是病**:valid_from = 事实在世界里"何时起为真",
  **只有明确来源才填、绝不猜**(docs/66:"可选、默认 = ts、只对话明说才另填");
  recall 的 as_of 谓词缺省退 provenance.ts(memory.recall_block),契约由缺省退化闭合。
  补写 valid_from=ts 纯冗余,且把"记录时刻"冒充"世界时刻"= 编时间(红线)。
- **supersede 有一个真要修的最小点**:打失效时 invalid_at 一律 = 发现时刻(now);
  但当新条 provenance 带**明确来源**的 valid_from(仅 converge.sediment_confirmed 从
  用户明说的绝对日期解析而来)且早于 now 时,旧条"世界里不再为真"的时刻**有据可依**——
  invalid_at 回填成它,否则 as_of 在 [valid_from, 发现) 窗口新旧两真并存。
  没有明确来源 → 保持发现时刻,绝不编世界时刻。

本文件锁上面每一条(含反面:未来时刻/坏值不回填)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition import ingest as I  # noqa: E402
from karvyloop.cognition.conflict import run_supersede_pass  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

T1, T2, T3 = 1000.0, 2000.0, 3000.0


class TextDelta:  # 收集器按 type name 认
    def __init__(self, t):
        self.text = t


class ScriptedGW:
    """按 system prompt 路由:摄入编译器一份、supersede 审查器一份(同 test_memory_conflict_supersede)。"""

    def __init__(self, *, ingest_reply="[]", supersede_reply='{"pairs":[]}'):
        self.ingest_reply = ingest_reply
        self.supersede_reply = supersede_reply
        self.calls = []

    async def complete(self, messages, tools, model_ref, *, system=None):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        if "一致性审查" in sys_text:
            self.calls.append("supersede")
            yield TextDelta(self.supersede_reply)
        else:
            self.calls.append("ingest")
            yield TextDelta(self.ingest_reply)


# 默认用低权威来源(conversation):这些是 supersede 时间语义(invalid_at/valid_from 回填)的测试,
# 不是 D2 人审保护的测试——D2 只保护钉住/人审记忆(fed/ingest/user_explicit…),低权威条照旧默默失效。
def _belief(content: str, *, source: str = "conversation", ts: float = T1,
            valid_from=None) -> Belief:
    prov = {"source": source, "agent": "t", "ts": ts, "trace_ref": ""}
    if valid_from is not None:
        prov["valid_from"] = valid_from
    return Belief(content=content, provenance=prov, freshness_ts=ts, scope="personal")


# ============ (a) 摄入:不写 valid_from 是契约,as_of 缺省退 ts 闭合 ============

def test_ingest_never_invents_valid_from_and_as_of_falls_back_to_ts():
    gw = ScriptedGW(ingest_reply='[{"content":"用户在用 KarvyLoop","kind":"fact"}]')
    mem = MemoryManager()
    res = asyncio.run(I.ingest_material("我在用 KarvyLoop", gateway=gw, mem=mem, now=T2))
    assert res.written == 1
    b = res.beliefs[0]
    # 摄入只知道"何时学到"(ts),不知道"世界里何时起为真" → 绝不编 valid_from
    assert "valid_from" not in b.provenance
    assert b.provenance["ts"] == T2
    # as_of 谓词缺省退 ts:学到之前看不见、之后看得见 —— 契约由缺省闭合,无需写字段
    assert mem.recall_block("KarvyLoop", as_of=T1) == ""
    assert "KarvyLoop" in mem.recall_block("KarvyLoop", as_of=T3)


# ============ (b) supersede 默认:invalid_at = 发现时刻,谁也不编时间 ============

def test_supersede_without_sourced_time_uses_discovery_time():
    mem = MemoryManager()
    old = _belief("用户住在北京", ts=T1)
    mem.write(old)
    new = _belief("用户现在住在上海", ts=T3)
    mem.write(new)
    gw = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}')
    asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T3))
    # 没有明确来源的世界时刻 → invalid_at 就是发现时刻(不回填、不标注回填)
    assert old.invalid_at == T3
    assert "superseded(update)" in old.invalid_reason
    assert "discovered@" not in old.invalid_reason
    # 两边 provenance 都没被塞时间字段
    assert "valid_from" not in old.provenance and "valid_from" not in new.provenance


# ============ (b) supersede 最小修:新条带明确 valid_from → 回填(有据的世界时刻) ============

def test_supersede_backfills_invalid_at_from_sourced_valid_from():
    mem = MemoryManager()
    old = _belief("用户住在北京", ts=T1)
    mem.write(old)
    # 用户明说"从(绝对日期=T2)起住上海" → converge 写进 provenance.valid_from(有来源,非猜)
    new = _belief("用户现在住在上海", source="user_explicit", ts=T3, valid_from=T2)
    mem.write(new)
    gw = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}')
    asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T3))
    # 旧条"世界里不再为真"的时刻有据可依 → invalid_at 回填;发现时刻留在 reason 里可审计
    assert old.invalid_at == T2
    assert "superseded(update)" in old.invalid_reason
    assert "discovered@3000" in old.invalid_reason
    # as_of 在 [T2, T3) 窗口:新旧两真不再并存(修的就是这个)
    block = mem.recall_block("用户 住在 北京 上海", as_of=(T2 + T3) / 2)
    assert "上海" in block and "北京" not in block
    # 窗口之前:旧真相仍可见、新真相还没生效
    block_before = mem.recall_block("用户 住在 北京 上海", as_of=(T1 + T2) / 2)
    assert "北京" in block_before and "上海" not in block_before


def test_supersede_ignores_future_or_garbage_valid_from():
    # 未来的 valid_from(还没发生)→ 不回填,保持发现时刻
    mem = MemoryManager()
    old = _belief("用户住在北京", ts=T1)
    mem.write(old)
    new = _belief("用户现在住在上海", source="user_explicit", ts=T3, valid_from=T3 + 500.0)
    mem.write(new)
    gw = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}')
    asyncio.run(run_supersede_pass([new], mem=mem, gateway=gw, now=T3))
    assert old.invalid_at == T3

    # 坏值(解析不了的字符串)→ 当不可判,保持发现时刻,不崩
    mem2 = MemoryManager()
    old2 = _belief("用户住在北京", ts=T1)
    mem2.write(old2)
    new2 = _belief("用户现在住在上海", source="user_explicit", ts=T3, valid_from="年初吧大概")
    mem2.write(new2)
    gw2 = ScriptedGW(supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}')
    asyncio.run(run_supersede_pass([new2], mem=mem2, gateway=gw2, now=T3))
    assert old2.invalid_at == T3
