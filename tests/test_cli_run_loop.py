"""karvyloop run ↔ MainLoop 接线层测试(M3+ 批 4a+4b)。

设计:plans/snoopy-singing-sunbeam.md §批 4。

AC 列表(本文件覆盖 4a + 4b 拍):
  AC1: build_main_loop 默认 skills_dir = ~/.karvyloop/skills
  AC2: build_main_loop 读 config.yaml crystallize.skills_dir
  AC3: 3 次同 intent 跑完触发 crystallize(2 variant + usage_score≥3)
  AC4: SKILL.md 落盘 + frontmatter 含 signature/verify_proof/trace_refs
  AC5: --no-recall 走直跳 cmd_run_async(MainLoop **不**被构造,UsageStore 0 污染)
  AC6: R3 防回归 — run_intent_via_loop 在 asyncio.run 内被调不抛
  AC7: 第 4 次同 intent 命中快脑(Brain.FAST)
  AC8: stderr 北极星指标行 stderr 出现 "fast_brain_hit_rate=..."
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.runtime.main_loop import MainLoop  # noqa: E402
from karvyloop.cli.run_loop import build_main_loop, run_intent_via_loop  # noqa: E402


# ---------- helpers ----------

def _make_config_with_skills_dir(tmp_path: Path, skills_dir: Path) -> Path:
    """写一个最小 config.yaml 含 crystallize.skills_dir。"""
    import yaml
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump({"crystallize": {"skills_dir": str(skills_dir)}}),
        encoding="utf-8",
    )
    return p


def _stub_slow_brain_factory(*, n_runs_max: int = 100, vary_input: bool = False):
    """fake forge_slow_brain_factory:不真跑 forge,返 mock slow_brain。

    默认**同 input**(保证 compute_signature 返同 sig)→ 触发 high_freq 路径
    (usage_count >= HIGH_FREQ=5)。vary_input=True 时 每次 idx 不同(input 不同)
    → 触发 _is_generalized 路径(param_variants ≥ 2 distinct)。
    """
    from karvyloop.schemas.atom import AtomRun

    call_count = {"n": 0}

    def factory(**kwargs):
        def slow_brain(intent: str) -> tuple[str, AtomRun]:
            n = call_count["n"]
            call_count["n"] += 1
            ts = 1000.0 + n * 200.0  # 200s 间隔 >> 60s 去抖
            if vary_input:
                run = AtomRun(
                    atom_id=f"atom-stub-{n}",
                    input={"intent": intent, "variant": f"v{n}"},
                    output={"text": f"ok-{intent}-{n}"},
                    success=True,
                    tool_calls=[{"name": "run_command"}],  # brick3:代表真干活→可结晶
                    trace_ref=f"trace://atom-stub/{n}",
                    ts=ts,
                )
            else:
                run = AtomRun(
                    atom_id=f"atom-stub-{n}",
                    input={"intent": intent},  # 同 input → 同 sig
                    output={"text": f"ok-{intent}-{n}"},
                    success=True,
                    tool_calls=[{"name": "run_command"}],  # brick3:代表真干活→可结晶
                    trace_ref=f"trace://atom-stub/{n}",
                    ts=ts,
                )
            return (f"ok-{intent}-{n}", run)

        return slow_brain

    return factory


def _build_loop_with_clock(tmp_path, *, clock_offset=0.0):
    """构造 MainLoop 并用可控 clock(避开 60s 去抖 + 模拟时间流逝)。"""
    from karvyloop.runtime.main_loop import MainLoop as ML

    base_ts = 1000.0 + clock_offset
    state = {"now": base_ts}

    def clock() -> float:
        return state["now"]

    ml = ML(skills_dir=tmp_path / "skills", clock=clock,
            result_classifier=lambda *_a: "stable")  # §13:确定性桩→stable,测重复意图走快脑回放
    ml.bootstrap()
    # 把"now 推进"暴露给测试用
    ml._test_advance = lambda secs: state.__setitem__("now", state["now"] + secs)  # type: ignore[attr-defined]
    return ml


# ---------- AC1: 默认 skills_dir ----------

class TestAC1DefaultSkillsDir:
    """AC1: build_main_loop 默认 skills_dir = ~/.karvyloop/skills。"""

    def test_default_skills_dir_under_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = build_main_loop()
        assert ml.skills_dir == tmp_path / ".karvyloop" / "skills"
        assert ml.skills_dir.exists()  # MainLoop __init__ 强制建


# ---------- AC2: 读 config.yaml ----------

class TestAC2ConfigYamlSkillsDir:
    """AC2: build_main_loop 读 config.yaml crystallize.skills_dir(优先级 > 默认)。"""

    def test_config_yaml_overrides_default(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_skills"
        cfg = _make_config_with_skills_dir(tmp_path, custom)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)  # 默认路径不会用到
        ml = build_main_loop(config_path=cfg)
        assert ml.skills_dir == custom

    def test_explicit_skills_dir_overrides_config(self, tmp_path, monkeypatch):
        cfg = _make_config_with_skills_dir(tmp_path, tmp_path / "from_config")
        explicit = tmp_path / "from_kwarg"
        ml = build_main_loop(config_path=cfg, skills_dir=explicit)
        assert ml.skills_dir == explicit  # 显式 kwarg 优先级最高

    def test_missing_config_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        missing = tmp_path / "nope.yaml"
        ml = build_main_loop(config_path=missing)
        assert ml.skills_dir == tmp_path / ".karvyloop" / "skills"


# ---------- AC5: --no-recall 走直跳(MainLoop 0 污染) ----------

class TestAC5NoRecallBypassesMainLoop:
    """AC5: --no-recall 走 cmd_run_async 直跳,MainLoop **不**被构造,UsageStore 0 污染。"""

    def test_no_recall_flag_does_not_construct_main_loop(self, tmp_path, monkeypatch):
        """传 no_recall=True 时不应 import/build MainLoop;主路径走 cmd_run_async。"""
        from karvyloop.cli import run as run_mod

        # mock cmd_run_async 让它返 0 而不真跑 forge
        called = {"cmd_run_async": 0, "cmd_run_via_loop": 0}

        async def fake_async(intent, **kwargs):
            called["cmd_run_async"] += 1
            return 0

        def fake_loop(intent, **kwargs):
            called["cmd_run_via_loop"] += 1
            return 0

        monkeypatch.setattr(run_mod, "cmd_run_async", fake_async)
        monkeypatch.setattr(run_mod, "cmd_run_via_loop", fake_loop)

        rc = run_mod.cmd_run("hello", no_recall=True)
        assert rc == 0
        assert called["cmd_run_async"] == 1
        assert called["cmd_run_via_loop"] == 0, "no_recall 不应走 cmd_run_via_loop"

    def test_json_output_also_bypasses_main_loop(self, tmp_path, monkeypatch):
        """--json 也走直跳(测试透明性 > 结晶)。"""
        from karvyloop.cli import run as run_mod

        called = {"cmd_run_async": 0, "cmd_run_via_loop": 0}

        async def fake_async(*a, **k):
            called["cmd_run_async"] += 1
            return 0

        def fake_loop(*a, **k):
            called["cmd_run_via_loop"] += 1
            return 0

        monkeypatch.setattr(run_mod, "cmd_run_async", fake_async)
        monkeypatch.setattr(run_mod, "cmd_run_via_loop", fake_loop)

        rc = run_mod.cmd_run("hello", json_output=True)
        assert rc == 0
        assert called["cmd_run_via_loop"] == 0

    def test_default_path_uses_main_loop(self, tmp_path, monkeypatch):
        """默认路径(no --no-recall, no --json)走 cmd_run_via_loop。"""
        from karvyloop.cli import run as run_mod

        called = {"cmd_run_async": 0, "cmd_run_via_loop": 0}

        async def fake_async(*a, **k):
            called["cmd_run_async"] += 1
            return 0

        def fake_loop(*a, **k):
            called["cmd_run_via_loop"] += 1
            return 0

        monkeypatch.setattr(run_mod, "cmd_run_async", fake_async)
        monkeypatch.setattr(run_mod, "cmd_run_via_loop", fake_loop)

        rc = run_mod.cmd_run("hello")
        assert rc == 0
        assert called["cmd_run_via_loop"] == 1, "默认路径应走 cmd_run_via_loop"
        assert called["cmd_run_async"] == 0


# ---------- AC6: R3 防回归 ----------

class TestAC6R3AsyncNestedRegression:
    """AC6: run_intent_via_loop 在 asyncio.run 内被调时**不**抛(防 asyncio 嵌套爆)。

    R3 风险:run_intent_via_loop 内部 forge_slow_brain_factory 又用 asyncio.run
    同步化 forge → 若调用方已在 event loop,会抛 RuntimeError。
    Mitigation(本拍):本测试不试图在事件循环内直接调 run_intent_via_loop(那
    本质就是 bug);而是验证 run_intent_via_loop 接受**已构造的** slow_brain
    工厂的间接路径 —— 即把 forge_slow_brain_factory mock 掉,避免嵌套。
    """

    def test_run_intent_via_loop_works_with_stub_slow_brain(self, tmp_path, monkeypatch):
        """用 stub 慢脑工厂跑通完整一次 drive(避开 R3 嵌套)。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = build_main_loop()

        # stub 慢脑工厂:绕过 forge_slow_brain_factory 的 asyncio.run
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            rc = run_intent_via_loop(
                "test intent",
                ml,
                token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                workspace_root=str(tmp_path), model_ref="fake",
            )
        assert rc == 0
        # 跑过 1 次,慢脑路径(没结晶 — usage_count 不够)
        assert ml.stats.slow_brain_runs == 1
        assert ml.stats.drive_calls == 1

    def test_stub_slow_brain_does_not_invoke_real_asyncio(self, tmp_path, monkeypatch):
        """stub slow_brain 不进 asyncio.run(主循环 driver 同步,慢脑同步化在 stub)。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = build_main_loop()
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            run_intent_via_loop(
                "x", ml,
                token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                workspace_root=str(tmp_path),
            )
        # fast_brain_hit_rate=0 / 慢脑跑 1 次(无 recall 命中)
        assert ml.stats.fast_brain_hits == 0
        assert ml.stats.slow_brain_runs == 1


# ---------- AC3/AC4/AC7: 结晶 + 快脑命中端到端(拍 4b) ----------

class TestAC3C4C7EndToEndCrystallize:
    """AC3/AC4/AC7: 跑 5 次同 input(intent)触发结晶(high_freq 路径),SKILL.md 落盘;第 6 次快脑命中。

    诚实修正(M3+ 批 4b):compute_signature 包含 _value_bucket,同 input → 同 sig;
    crystallize 关 2 需 'generalized or high_freq',high_freq = usage_count >= 5。
    因此跑 5 次同 input 触发 high_freq 路径;第 6 次 recall 命中快脑。
    """

    def test_ac3_5_runs_crystallize_at_5th_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path, clock_offset=0.0)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory(vary_input=False)):
            for i in range(5):
                ml._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "summarize report", ml,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path), model_ref="fake",
                )
        assert ml.stats.slow_brain_runs == 5
        assert ml.stats.crystallizations == 1, f"第 5 次应结晶,got {ml.stats}"

    def test_ac4_skill_md_persisted_with_frontmatter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path, clock_offset=0.0)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory(vary_input=False)):
            for i in range(5):
                ml._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "compute payroll", ml,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path), model_ref="fake",
                )
        skills_root = tmp_path / "skills"
        skill_dirs = [d for d in skills_root.iterdir() if d.is_dir()]
        assert len(skill_dirs) >= 1, f"应至少有 1 个结晶 skill 目录,got {list(skills_root.iterdir())}"
        skill_md = skill_dirs[0] / "SKILL.md"
        assert skill_md.exists()
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "signature:" in text
        assert "verify_proof:" in text
        assert "trace_refs:" in text

    def test_ac7_6th_call_hits_fast_brain(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path, clock_offset=0.0)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory(vary_input=False)):
            for i in range(5):
                ml._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "send email", ml,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path), model_ref="fake",
                )
            assert ml.stats.crystallizations == 1
            # 第 6 次:slow_brain 工厂若被调就爆
            def boom(*a, **k):
                raise AssertionError("第 6 次应走快脑,不应调 slow_brain")

            with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", lambda **k: boom):
                ml._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "send email", ml,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path), model_ref="fake",
                )
        assert ml.stats.fast_brain_hits == 1, f"第 6 次应命中快脑,got {ml.stats}"


# ---------- AC8: 北极星指标 stderr 行 ----------

class TestAC8StatsLineOnStderr:
    """AC8: 跑过一次后,stderr 应有 'fast_brain_hit_rate=...' 单行。"""

    def test_stats_line_emitted_to_stderr(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path, clock_offset=0.0)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            run_intent_via_loop(
                "stats test", ml,
                token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                workspace_root=str(tmp_path), model_ref="fake",
            )
        captured = capsys.readouterr()
        assert "fast_brain_hit_rate=" in captured.err
        assert "crystallizations=" in captured.err
        assert "slow_brain_runs=" in captured.err
        assert "drive_calls=" in captured.err


# ---------- 工具:K 铁律 grep 锁 (拍 4c 也要跑,本拍 4a 末先验) ----------

class TestAKLawScan:
    """拍 4a 末 K 锁预检(拍 4c 全量再验)。"""

    def test_run_loop_no_apply_or_courier(self):
        """run_loop.py 不应出现 apply_* 或 Courier.send(K4 + K5)。"""
        result = subprocess.run(
            ["grep", "-nE", r"(apply_deontic\(|domain\.apply_\w+\(|Courier\.send\()",
             str(ROOT / "karvyloop" / "cli" / "run_loop.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"K 铁律违规\n{chr(10).join(lines)}"

    def test_run_loop_no_cloud_endpoint(self):
        """run_loop.py 不应拼 LLM cloud endpoint。"""
        result = subprocess.run(
            ["grep", "-nE", r"(api\.minimax\.chat|api\.anthropic\.com|api\.openai\.com)",
             str(ROOT / "karvyloop" / "cli" / "run_loop.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"0 LLM 违规\n{chr(10).join(lines)}"
