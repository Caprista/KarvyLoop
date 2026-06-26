"""test_main_loop_ctx — drive 的上下文感知(M3+ 拍 9.1b)。

设计:docs/26 §B(CV-9/CV-11)+ docs/25 §6.5(FB-9)。

AC 矩阵:
- AC1-AC6: context_gate.is_context_dependent(无上下文恒 False / 指代 / 极短应答 / 承接 / 独立句 / 空)
- AC7-AC9: drive ctx 向后兼容(ctx=None 行为不变)+ ctx-dependent 跳快脑 + 不结晶
- AC10: DriveResult.ctx_dependent 标记
- AC11: FB-5(context_gate 不依赖 karvy.atoms)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.karvy.fastbrain.context_gate import is_context_dependent, DEPENDENT_MARKERS


# ---- AC1-AC6: 门控检测器 ----


def test_no_context_always_false() -> None:
    """无上下文 → 永远 False(第一句没有'它'可指)。"""
    assert is_context_dependent("删掉它", has_context=False) is False
    assert is_context_dependent("好", has_context=False) is False
    assert is_context_dependent("继续", has_context=False) is False


@pytest.mark.parametrize("intent", ["删掉它", "把它改一下", "那个文件呢", "这个不对", "上面说的"])
def test_pronoun_markers_dependent(intent: str) -> None:
    assert is_context_dependent(intent, has_context=True) is True


@pytest.mark.parametrize("intent", ["好", "好的", "对", "是的", "嗯", "行", "可以", "删", "继续", "算了"])
def test_short_replies_dependent(intent: str) -> None:
    assert is_context_dependent(intent, has_context=True) is True


@pytest.mark.parametrize("intent", ["接着", "再来", "换一个", "下一个"])
def test_continuation_markers_dependent(intent: str) -> None:
    assert is_context_dependent(intent, has_context=True) is True


@pytest.mark.parametrize("intent", [
    "帮我写快速排序",
    "查 git 状态",          # "git" 不该命中英文 "it"(词边界)
    "创建新的 Python 项目",
    "总结 README 文件",
])
def test_independent_sentences_not_dependent(intent: str) -> None:
    """完整独立句(无指代/承接,即便有上下文)→ False,正常走快脑。"""
    assert is_context_dependent(intent, has_context=True) is False


@pytest.mark.parametrize("intent", ["帮我总结这个 README", "那个文件呢", "把它改一下"])
def test_demonstrative_with_context_is_dependent(intent: str) -> None:
    """含指代词(这个/那个/它)+ 有上下文 → 依赖(路由慢脑是安全的)。"""
    assert is_context_dependent(intent, has_context=True) is True


def test_empty_intent_not_dependent() -> None:
    assert is_context_dependent("", has_context=True) is False
    assert is_context_dependent("   ", has_context=True) is False


def test_dependent_markers_exposed() -> None:
    assert "它" in DEPENDENT_MARKERS
    assert "继续" in DEPENDENT_MARKERS


# ---- drive 集成(用最小 MainLoop fixture)----


def _build_loop(tmp_path: Path):
    from karvyloop.cli.main_loop import MainLoop

    return MainLoop(
        skills_dir=tmp_path / "skills",
        scope="private",
    )


def _ok_slow_brain(text: str = "done"):
    """返成功 AtomRun 的假慢脑。"""
    from karvyloop.schemas import AtomRun

    def sb(intent: str):
        run = AtomRun(
            atom_id="test", input={"intent": intent}, output={"text": text},
            success=True, tool_calls=[], trace_ref="tr-1", ts=1.0,
        )
        return text, run
    return sb


# ---- AC7: ctx=None 向后兼容 ----


def test_drive_ctx_none_behaves_as_before(tmp_path: Path) -> None:
    """ctx=None(默认)→ 行为同旧路径(慢脑跑,可结晶)。"""
    loop = _build_loop(tmp_path)
    calls = {"n": 0}

    def sb(intent: str):
        from karvyloop.schemas import AtomRun
        calls["n"] += 1
        run = AtomRun(atom_id="a", input={"intent": intent}, output={"text": "ok"},
                      success=True, tool_calls=[], trace_ref="t", ts=1.0)
        return "ok", run

    r = loop.drive("帮我写个脚本", slow_brain=sb)
    assert calls["n"] == 1  # 慢脑跑了
    assert r.ctx_dependent is False


# ---- AC8: ctx-dependent 跳快脑(强制慢脑)----


def test_ctx_dependent_skips_fast_brain(tmp_path: Path, monkeypatch) -> None:
    """有上下文 + 指代句 → 跳过 recall(快脑),直接慢脑。"""
    loop = _build_loop(tmp_path)
    # 监视 recall 是否被调
    import karvyloop.cli.main_loop as ml
    recall_calls = {"n": 0}
    real_recall = ml.recall

    def spy_recall(*a, **k):
        recall_calls["n"] += 1
        return real_recall(*a, **k)

    monkeypatch.setattr(ml, "recall", spy_recall)

    sb = _ok_slow_brain()
    # ctx 为非空(模拟有前文)
    r = loop.drive("删掉它", slow_brain=sb, ctx=("prev turn",))
    assert r.ctx_dependent is True
    assert recall_calls["n"] == 0  # 快脑被跳过
    assert r.brain.value == "slow"


def test_ctx_dependent_false_when_no_ctx(tmp_path: Path, monkeypatch) -> None:
    """同样的指代句,但 ctx=None(无前文)→ 不判依赖,正常走快脑 recall。"""
    loop = _build_loop(tmp_path)
    import karvyloop.cli.main_loop as ml
    recall_calls = {"n": 0}
    real_recall = ml.recall
    monkeypatch.setattr(ml, "recall", lambda *a, **k: (recall_calls.__setitem__("n", recall_calls["n"] + 1) or real_recall(*a, **k)))

    sb = _ok_slow_brain()
    r = loop.drive("删掉它", slow_brain=sb, ctx=None)
    assert r.ctx_dependent is False
    assert recall_calls["n"] == 1  # 无上下文 → 正常走 recall


# ---- AC9: ctx-dependent 不结晶(CV-11)----


def test_ctx_dependent_does_not_crystallize(tmp_path: Path) -> None:
    """上下文依赖句即便慢脑成功,也**不**结晶(临时映射不进永久库)。"""
    loop = _build_loop(tmp_path)
    sb = _ok_slow_brain()
    # 跑很多次同一个指代句 —— 正常会触发结晶,但 ctx-dependent 应阻止
    for _ in range(6):
        r = loop.drive("删掉它", slow_brain=sb, ctx=("prev",))
    assert r.ctx_dependent is True
    assert r.crystallized is False
    assert loop.stats.crystallizations == 0


def test_independent_intent_still_crystallizes(tmp_path: Path) -> None:
    """对照:独立句(有 ctx)正常路径,结晶不受影响。"""
    loop = _build_loop(tmp_path)
    sb = _ok_slow_brain()
    last = None
    for _ in range(6):
        last = loop.drive("帮我把项目打包成 wheel", slow_brain=sb, ctx=("prev",))
    assert last.ctx_dependent is False
    # 独立句正常进结晶判定(crystallizations 至少没被门拦)
    assert loop.stats.slow_brain_runs == 6


# ---- AC11: FB-5 ----


def test_context_gate_no_karvy_atoms_dependency() -> None:
    import karvyloop.karvy.fastbrain.context_gate as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    import_lines = [l for l in src.splitlines() if l.strip().startswith(("import ", "from "))]
    blob = "\n".join(import_lines)
    assert "karvy.atoms" not in blob
    assert "IntentAnalyst" not in blob


# ---- 9.3b: 对话 ctx token 预算裁剪(D2 / docs/28 TK-2)----


def test_render_ctx_prefix_budget_keeps_recent_drops_old() -> None:
    from karvyloop.cli.main_loop import _render_ctx_prefix
    from karvyloop.cognition.conversation import Turn

    # 20 轮,每轮内容较长;小预算 → 只保留最近几轮 + 标"更早已省略"
    turns = tuple(
        Turn(user_intent=f"问题{i} " + "字" * 40, agent_response=f"回答{i} " + "答" * 40, ts=float(i))
        for i in range(20)
    )
    out = _render_ctx_prefix(turns, token_budget=200)
    assert "更早的已省略" in out
    # 最近一轮在,最早一轮不在
    assert "问题19" in out
    assert "问题0 " not in out


def test_render_ctx_prefix_no_truncate_when_under_budget() -> None:
    from karvyloop.cli.main_loop import _render_ctx_prefix
    from karvyloop.cognition.conversation import Turn

    turns = (Turn("短问", "短答", ts=1.0),)
    out = _render_ctx_prefix(turns, token_budget=2000)
    assert "更早的已省略" not in out
    assert "短问" in out


def test_render_ctx_prefix_empty() -> None:
    from karvyloop.cli.main_loop import _render_ctx_prefix
    assert _render_ctx_prefix(None) == ""
    assert _render_ctx_prefix(()) == ""


def test_governance_value_md_capped() -> None:
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.domain.registry import Address
    import tempfile, pathlib

    class _VM:
        def __init__(self, t): self.text = t
    class _Dom:
        def __init__(self): self.name = "X"; self.value_md = _VM("# 价值观\n\n" + "原则" * 2000)
    class _Reg:
        def get(self, i): return _Dom()
        def list_active(self): return [_Dom()]
        def resolve_members(self, i): return ()

    with tempfile.TemporaryDirectory() as d:
        mgr = ConversationManager(ConversationStore(pathlib.Path(d) / "c"), domain_registry=_Reg())
        mgr.set_peer(Address(domain_id="dom-x", role="agent", agent_id="a"))
        gov = mgr.governance_text()
        # 封顶 1500 + 框架文字,远小于 4000+ 原长
        assert len(gov) < 1700
        assert gov.endswith("…")
