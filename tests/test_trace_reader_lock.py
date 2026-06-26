"""test_trace_reader_lock — TR-4 提炼器专读锁(修 D1 尾,M3+ 拍 9.4-B1)。

设计:docs/27 TR-4 —— trace 漏斗(fastbrain.TraceIndex 原文/摘要层)**只有提炼器读**;
任何 human-facing surface(console UI / workbench / CLI 输出 / forge 干活 agent)
**都不读 trace** —— 否则就出现"小卡帮人把 trace 念出来"的泄露 bug(用户原话质疑)。

为什么这是真不变量(而非装样子):
- 漏斗读面 = `TraceIndex.list_raw()` / `list_summary()`。
- 合法读者只有两类**提炼器**:
  ① `karvyloop/karvy/fastbrain/trace_poll.py`(原文→摘要蒸馏)
  ② `karvyloop/karvy/atoms.py` 的 IntentAnalyst(小卡 K1 observer:读摘要→凝习惯→PROPOSE)
     —— 小卡读摘要是为了**产出建议**,从不把 trace 内容回吐给 human(courier 只递 Proposal)。
- human-facing 层(console/workbench/cli/coding)读到 trace = 违反 TR-4。

注:`karvyloop replay` 读的是 **cognition.TraceStore(replay 库)**,不是本漏斗;那是
机器主人 debug 自己的盘(TR-6 诚实碳出口),不在本锁范围。两套库分工见 docs/27 §TR-4 附注。

AC:
- AC1: console/ 不读漏斗 trace
- AC2: workbench/ 不读漏斗 trace
- AC3: cli/ 不读漏斗 trace
- AC4: coding/(forge 干活 agent)不读漏斗 trace
- AC5: 合法读者只在 karvy/(提炼器)—— 正向确认锁没把提炼器也误杀
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "karvyloop"
READ_SURFACE = re.compile(r"\.list_raw\s*\(|\.list_summary\s*\(")


def _py_files(subpkg: str) -> list[Path]:
    d = ROOT / subpkg
    return [p for p in d.rglob("*.py") if "__pycache__" not in p.parts] if d.exists() else []


def _readers_in(subpkg: str) -> list[str]:
    hits = []
    for p in _py_files(subpkg):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            # 跳过注释/docstring 行里出现的字面量(只抓真调用)
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if READ_SURFACE.search(line):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{i}: {line.strip()}")
    return hits


@pytest.mark.parametrize("subpkg", ["console", "workbench", "cli", "coding"])
def test_human_facing_surface_does_not_read_funnel_trace(subpkg):
    """TR-4:human-facing 层一律不读漏斗 trace(list_raw/list_summary)。"""
    hits = _readers_in(subpkg)
    assert not hits, (
        f"TR-4 违反:{subpkg}/ 读了漏斗 trace(应只有 karvy/ 提炼器读):\n"
        + "\n".join(hits)
    )


def test_only_distiller_reads_funnel_trace():
    """AC5 正向:合法读者全在 karvy/(提炼器),且确实存在(防锁空转)。"""
    readers = _readers_in("karvy")
    assert readers, "提炼器读面消失了?(trace_poll/IntentAnalyst 应读 list_raw/list_summary)"
    # 全部落在提炼器文件(trace_poll 蒸馏 / atoms IntentAnalyst)
    allowed = ("fastbrain/trace_poll.py", "atoms.py")
    for r in readers:
        assert any(a in r for a in allowed), f"karvy/ 内非提炼器文件读了 trace: {r}"
