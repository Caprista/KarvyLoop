"""recall_block(as_of=T) 时点召回 —— docs/66 §技术底(Graphiti 双时态的薄版,无向量)。

整个能力就一个谓词:valid_from ≤ T 且(未失效 或 invalid_at > T)。
None(默认)= 今天的行为一字不变(invalid 过滤照旧)。
"""
from __future__ import annotations

from karvyloop.cognition.memory import MemoryManager
from karvyloop.schemas.cognition import Belief

T0, T1, T2, T3 = 1000.0, 2000.0, 3000.0, 4000.0


def _b(content: str, *, ts: float, valid_from: float | None = None,
       invalid_at: float | None = None) -> Belief:
    prov = {"source": "user_explicit", "agent": "user", "ts": ts, "trace_ref": ""}
    if valid_from is not None:
        prov["valid_from"] = valid_from
    return Belief(content=content, provenance=prov, freshness_ts=ts,
                  scope="personal", invalid_at=invalid_at)


def _mem() -> MemoryManager:
    mem = MemoryManager()
    # T1 学到"用 React";T2 被推翻(invalid);T2 学到"用 Vue"
    mem.write(_b("技术栈用 React", ts=T1, invalid_at=T2))
    mem.write(_b("技术栈用 Vue", ts=T2))
    return mem


def test_default_behavior_unchanged():
    mem = _mem()
    out = mem.recall_block("技术栈")
    assert "Vue" in out and "React" not in out          # 失效的默认不召回(原行为)
    out_inv = mem.recall_block("技术栈", include_invalid=True)
    assert "React" in out_inv                            # 审计面照旧


def test_as_of_between_learn_and_supersede_sees_old_truth():
    mem = _mem()
    out = mem.recall_block("技术栈", as_of=(T1 + T2) / 2)  # T1.5:React 还算数,Vue 还没学到
    assert "React" in out and "Vue" not in out


def test_as_of_after_supersede_sees_new_truth():
    mem = _mem()
    out = mem.recall_block("技术栈", as_of=T3)             # T3:React 已推翻,Vue 算数
    assert "Vue" in out and "React" not in out


def test_as_of_before_anything_learned_sees_nothing():
    mem = _mem()
    assert mem.recall_block("技术栈", as_of=T0) == ""      # T0:啥都还没学到


def test_valid_from_takes_precedence_over_ts():
    mem = MemoryManager()
    # T2 才学到,但对话里明说"从 T1 起就这样"(绝对日期)→ as_of=T1.5 该看得见
    mem.write(_b("从年初开始吃素", ts=T2, valid_from=T1))
    assert "吃素" in mem.recall_block("吃素", as_of=(T1 + T2) / 2)
    assert mem.recall_block("吃素", as_of=T0) == ""        # valid_from 之前看不见


def test_bad_timestamp_does_not_crash_or_drop():
    mem = MemoryManager()
    b = _b("时间戳坏了的条", ts=T1)
    b.provenance["valid_from"] = "not-a-number"            # 坏值:当不可判,不丢条
    mem.write(b)
    assert "时间戳坏了" in mem.recall_block("时间戳", as_of=T2)
