"""test_skill_process_vs_result — #2 §13 回归门:过程优先 / 命中重跑不回放 / 方法制导 / stable 才回放。

锁死 Hardy 的核心要求:
- dynamic(默认):含 search/调研/diff 这类,**两次结果必须不同**(绝不回放 stale)。
- 命中 dynamic 技能:把**方法**喂慢脑制导(省 token),不是吐旧答案。
- 结晶产物存的是**方法(Steps)**,不是答案正文。
- stable:才回放缓存结果。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.runtime.main_loop import MainLoop, Brain  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter  # noqa: E402
from karvyloop.schemas.atom import AtomRun  # noqa: E402


class _Clock:
    """可控时钟:每次 tick 前进 200s(跨过 60s usage 去抖,让 usage 真累积 → 能结晶)。"""
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def tick(self):
        self.t += 200.0


def _loop(tmp_path, clk, classifier=None):
    return MainLoop(skills_dir=tmp_path / "skills", clock=clk.now, result_classifier=classifier)


def _slow(seq):
    """slow_brain:第 i 次产出 seq[i](模拟动态任务每次不同),记录收到的 intent。"""
    state = {"i": 0, "seen": []}

    def sb(intent, *, ctx=None):
        state["seen"].append(intent)
        i = state["i"]
        out = seq[min(i, len(seq) - 1)]
        state["i"] += 1
        # input 带**变化的 param** → param_variants 累积 → 触发"已泛化"关2(否则永不结晶)
        return out, AtomRun(atom_id=f"a{i}", input={"intent": intent, "x": i}, output={"text": out},
                            success=True, tool_calls=[{"name": "web_search", "input": {"query": "x"}}],
                            trace_ref=f"t{i}", ts=0.0)
    return sb, state


def _run(ml, clk, intent, sb, n):
    outs = []
    for _ in range(n):
        clk.tick()
        outs.append(ml.drive(intent, slow_brain=sb).text)
    return outs


def test_dynamic_default_reruns_and_never_replays(tmp_path):
    clk = _Clock(); ml = _loop(tmp_path, clk)   # 无判定器 → 默认 dynamic
    sb, st = _slow([f"实时结果#{i}" for i in range(8)])
    outs = _run(ml, clk, "调研竞品最新动态并联网搜索", sb, 6)
    assert len(set(outs)) == 6          # 每次不同 —— 绝无 stale 回放
    assert st["i"] == 6                  # 慢脑每次都真跑


def test_dynamic_recall_injects_method_guidance(tmp_path):
    clk = _Clock(); ml = _loop(tmp_path, clk)
    sb, st = _slow([f"结果#{i}" for i in range(8)])
    _run(ml, clk, "联网搜索行情并汇总", sb, 5)
    assert ml.stats.crystallizations >= 1            # 已结晶(过程技能)
    # 命中后的那次重跑,收到的 intent 应带**方法制导**(Steps),而不是直接吐旧答案
    last_intent = st["seen"][-1]
    assert "Steps" in last_intent and "当前任务" in last_intent


def test_crystallized_body_is_method_not_answer(tmp_path):
    clk = _Clock(); ml = _loop(tmp_path, clk)   # dynamic
    sb, _ = _slow(["这是一坨很具体的答案正文不该被存进技能" for _ in range(8)])
    _run(ml, clk, "把数据联网查一下再汇总", sb, 5)
    assert ml.stats.crystallizations >= 1
    smd = next((tmp_path / "skills").glob("*/SKILL.md"))
    fm, body = parse_frontmatter(smd)
    assert fm.result_reuse == "dynamic"
    assert "Steps" in body and "web_search" in body          # 存的是方法
    assert "答案正文不该被存进技能" not in body                 # 不是答案


def test_stable_replays_cached_result(tmp_path):
    clk = _Clock(); ml = _loop(tmp_path, clk, classifier=lambda *_a: "stable")
    sb, st = _slow(["确定的稳定结果" for _ in range(8)])
    _run(ml, clk, "把华氏 100 度换算成摄氏", sb, 5)
    assert ml.stats.crystallizations >= 1
    n_before = st["i"]
    clk.tick()
    r = ml.drive("把华氏 100 度换算成摄氏", slow_brain=sb)
    assert r.brain == Brain.FAST and r.fast_brain_hit and st["i"] == n_before  # 回放,慢脑没再跑
    assert r.text.strip() == "确定的稳定结果"
