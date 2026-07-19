"""pursuit 验收测试 — 逐条对应 docs/modules/pursuit.md §5。

6 条 AC:
  1. commitment 条件成立 → active→committed;committed 下不每轮重规划
  2. revision 触发器命中 → should_revise=True → status=revised
  3. verify_gate 是确定性判定(文件存在/测试通过/谓词),不调模型
  4. is_done=True → status=done
  5. 私人 Pursuit 存 Belief、域 Pursuit 存域 KB(持久化路径正确)
  6. 闭环测试:revision 触发后能修订而非盲目坚持
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.cognition import (
    GateError,
    MemoryManager,
    PursuitManager,
    eval_condition,
    eval_verify_gate,
)
from karvyloop.schemas import Pursuit


# ---- 工具 ----

def pursuit(
    *,
    id: str = "p1",
    level: str = "atom",
    statement: str = "do x",
    commitment_condition: str = "",
    revision_triggers: list[str] = None,  # type: ignore
    verify_gate: dict = None,  # type: ignore
    status: str = "active",
) -> Pursuit:
    return Pursuit(
        id=id,
        level=level,  # type: ignore[arg-type]
        statement=statement,
        commitment_condition=commitment_condition,
        revision_triggers=revision_triggers or [],
        verify_gate=verify_gate or {"type": "predicate", "expr": "done in context"},
        status=status,  # type: ignore[arg-type]
    )


# ============ AC1: commitment 条件成立 → active→committed;committed 防 thrashing ============
def test_ac1_commitment_activates():
    """commitment 条件成立 → status=committed(提升为 Intention)。"""
    pm = PursuitManager()
    p = pursuit(commitment_condition="phase == build", status="active")
    out = pm.step(p, {"phase": "build"})
    assert out.status == "committed"


def test_ac1b_committed_does_not_redo_planning_each_step():
    """committed 状态下条件暂时不成立 → 不降级,坚持(spec §4 防 thrashing)。"""
    pm = PursuitManager()
    p = pursuit(commitment_condition="phase == build", status="committed")
    # 上下文里 phase 不再是 build(可能外部状态波动)
    out = pm.step(p, {"phase": "rollback"})
    # 防 thrashing:不退回 active;若 verify 没满足且无 revision → 维持 committed
    assert out.status == "committed"


def test_ac1c_done_and_dropped_are_terminal():
    """done/dropped 不参与状态机(commit 不会改它们)。"""
    pm = PursuitManager()
    p_done = pursuit(commitment_condition="phase == build", status="done")
    assert pm.step(p_done, {"phase": "build"}).status == "done"
    p_drop = pursuit(commitment_condition="phase == build", status="dropped")
    assert pm.step(p_drop, {"phase": "build"}).status == "dropped"


# ============ AC2: revision 触发器命中 → should_revise=True → status=revised ============
def test_ac2_revision_trigger_marks_revised():
    """revision_trigger 命中 → step 返回 status=revised(上层据此重规划)。"""
    pm = PursuitManager()
    p = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],  # 一旦 env 变 test → 该重规划
        verify_gate={"type": "predicate", "expr": "false"},
        status="committed",
    )
    out = pm.step(p, {"phase": "build", "env": "test"})
    assert out.status == "revised"


def test_ac2b_revised_triggers_only_apply_to_active_or_committed():
    """revised/done/dropped 状态不参与 revision(终态或已处理)。"""
    pm = PursuitManager()
    p = pursuit(
        commitment_condition="true", revision_triggers=["x == 1"],
        verify_gate={"type": "predicate", "expr": "false"},
        status="revised",
    )
    # 再次命中触发器 → 仍 revised(不再翻一次)
    assert pm.step(p, {"x": 1}).status == "revised"


# ============ AC3: verify_gate 确定性判定 ============
def test_ac3_verify_gate_file_exists(tmp_path: Path):
    """file_exists 门:文件存在 → True;不存在 → False。确定性,无模型调用。"""
    f = tmp_path / "out.txt"
    f.write_text("ok", encoding="utf-8")
    # 存在
    assert eval_verify_gate({"type": "file_exists", "path": str(f)}, {}) is True
    # 不存在
    assert eval_verify_gate({"type": "file_exists", "path": str(tmp_path / "missing.txt")}, {}) is False


def test_ac3b_verify_gate_file_exists_literal_no_format():
    """真伤4:file_exists path **不再**做 `{var}` 替换,按**字面**判定。

    旧实现 `path.format(**context)` 有两坑:未知占位符 `{date}` → KeyError / 单花括号 → ValueError
    冒穿 tick(节流戳写不进 → 每 10min 重炸、6h 节流永不生效);且 `{x.__class__}` = 信息泄露面。
    含 `{...}` 的 path 是**创建期**该拒的坏门(path_has_placeholder),run 期按字面判永不满足、绝不抛。
    """
    import tempfile, os
    from karvyloop.cognition.pursuit import path_has_placeholder
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "alice_test.txt")
        open(target, "w").close()
        templ = os.path.join(d, "{name}_test.txt")
        # 不再替换:`{name}` 当字面 → 匹配不到 alice_test.txt → False,且**不抛**
        assert eval_verify_gate({"type": "file_exists", "path": templ},
                                {"name": "alice"}) is False
        # 字面存在的路径仍 True(正常绝对/相对路径不受影响)
        assert eval_verify_gate({"type": "file_exists", "path": target}, {}) is True
    # 占位符指纹:含花括号 → 创建期该拒;正常路径不含
    assert path_has_placeholder("/x/{date}/o.md") is True
    assert path_has_placeholder("/x/reports/o.md") is False


def test_ac3c_verify_gate_predicate_is_deterministic():
    """predicate 门 = eval_condition 子集;不调模型,纯字符串/字典型判定。"""
    # 等价"key in context"
    g = {"type": "predicate", "expr": "phase == build"}
    assert eval_verify_gate(g, {"phase": "build"}) is True
    assert eval_verify_gate(g, {"phase": "test"}) is False
    # 复合
    g2 = {"type": "predicate", "expr": "phase == build, env != prod"}
    assert eval_verify_gate(g2, {"phase": "build", "env": "test"}) is True
    assert eval_verify_gate(g2, {"phase": "build", "env": "prod"}) is False


def test_ac3d_verify_gate_test_pass_runs_subprocess():
    """test_pass 门(沙箱化,第三刀):**有真隔离后端** → 沙箱内跑子进程,exit 0→True / 非0→False;
    **无后端**(如 CI Linux 无 bwrap / degraded 档)→ fail-closed 拒跑(False + no_isolation 人话码),
    绝不裸跑不可信命令。availability 决定行为——两条分支都是正确态,不是跳过。"""
    import sys
    from karvyloop.sandbox import default_sandbox
    py = sys.executable
    if default_sandbox().available():
        assert eval_verify_gate({"type": "test_pass", "cmd": f'"{py}" -c "exit(0)"'}, {}) is True
        assert eval_verify_gate({"type": "test_pass", "cmd": f'"{py}" -c "exit(1)"'}, {}) is False
    else:
        # 无真隔离后端:拒跑不可信 gate(fail-closed),留人话原因码给用户可见处(真伤7)。
        ctx: dict = {}
        assert eval_verify_gate({"type": "test_pass", "cmd": f'"{py}" -c "exit(0)"'}, ctx) is False
        assert ctx.get("_gate_note_code") == "no_isolation"


def test_ac3e_verify_gate_unknown_type_raises():
    """未知 type → GateError(spec §4 拒绝隐式默认,不调模型兜底)。"""
    with pytest.raises(GateError, match="unknown verify_gate type"):
        eval_verify_gate({"type": "ask_llm"}, {})  # 典型反例
    with pytest.raises(GateError):
        eval_verify_gate({"type": ""}, {})
    with pytest.raises(GateError):
        eval_verify_gate("not a dict", {})


def test_ac3f_verify_gate_does_not_call_llm():
    """AC3 守门:verify_gate 路径里没有 LLM 调用的痕迹(源码 grep)。"""
    import inspect, karvyloop.cognition.pursuit as p
    src = inspect.getsource(p)
    forbidden = ["client.messages", "openai.", "anthropic.", "GatewayClient",
                 "inference", "chat(", "complete("]
    for f in forbidden:
        assert f not in src, f"verify_gate 实现含 LLM 调用 {f!r}(AC3 越界)"


# ============ AC4: is_done=True → status=done ============
def test_ac4_is_done_marks_done():
    """is_done=True → step 把 status 翻成 done(终态)。"""
    pm = PursuitManager()
    p = pursuit(
        commitment_condition="phase == build",
        verify_gate={"type": "file_exists", "path": "nope"},  # 不会存在
    )
    # 用 file_exists 临时文件证明 done 路径
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".done", delete=False) as f:
        path = f.name
    p_done = pursuit(verify_gate={"type": "file_exists", "path": path})
    assert pm.step(p_done, {}).status == "done"
    import os; os.unlink(path)


def test_ac4b_step_order_done_takes_precedence():
    """step:先判 done,再判 revised,再 commit(spec §3 顺序不可反)。"""
    pm = PursuitManager()
    p = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        verify_gate={"type": "predicate", "expr": "phase == done"},
        status="committed",
    )
    out = pm.step(p, {"phase": "done", "env": "test"})
    # done 优先 → done(不是 revised)
    assert out.status == "done"


# ============ AC5: 持久化路径 ============
def test_ac5a_personal_pursuit_persists_to_belief():
    """私人 Pursuit(level=atom)→ 存为 Belief(私人记忆)。"""
    mgr = MemoryManager()
    pm = PursuitManager(memory=mgr)
    p = pursuit(id="atom:1", level="atom", statement="写完 README",
                commitment_condition="phase == build",
                status="committed")
    pm.persist(p)
    # Belief 已写
    hits = mgr.index.all("personal")
    assert any("写完 README" in b.content for b in hits)
    # provenance 必带
    b = next(b for b in hits if "写完 README" in b.content)
    assert "pursuit_id" in b.provenance
    assert b.provenance["pursuit_id"] == "atom:1"
    # 摘冒档回归锁(docs/89 ⑥):Pursuit 状态条是机器投影,**绝不冒充 user_explicit**(人审受保护档);
    # 用 trace_verified(机器派生·不受保护 → 你的原话不被它压过 + 陈旧条能被日常整理清掉);ts 真实非 0。
    from karvyloop.cognition.conflict import HUMAN_REVIEWED_SOURCES
    assert b.provenance["source"] == "trace_verified"
    assert b.provenance["source"] not in HUMAN_REVIEWED_SOURCES   # 不再受保护、不再冒充你
    assert not mgr.is_protected_memory(b)
    assert float(b.provenance["ts"]) > 0.0                        # 修死值 0.0


def test_ac5b_domain_pursuit_persists_to_domain_kb(tmp_path: Path):
    """真伤3:域 Pursuit(level=domain)→ 落盘到 <domain_root>/<domain_id>/pursuits.md。

    真域从**运营层线程进来**(persist 的 domain_id 参数,由 pursuit_tick 传 rec.domain_id),
    **不再**从 p.id 拆(p.id 是 `domain:<随机 12hex>`,拆它会把域级完成归档进随机 uuid 目录、真域丢失)。
    """
    pm = PursuitManager(domain_root=tmp_path)
    # 真实生产:p.id 是 domain:<随机hex>,真域 = 传入的 domain_id="invest"
    p = pursuit(id=f"domain:{'a1b2c3d4e5f6'}", level="domain", statement="完成 SSO",
                commitment_condition="phase == build", status="committed")
    pm.persist(p, domain_id="invest")
    path = tmp_path / "invest" / "pursuits.md"
    assert path.exists(), "域级完成没落到真域(invest)目录 —— 真伤3 回归"
    # 绝不落到 p.id 的随机 hex 目录
    assert not (tmp_path / "a1b2c3d4e5f6").exists()
    text = path.read_text(encoding="utf-8")
    assert "domain:a1b2c3d4e5f6" in text
    assert "完成 SSO" in text
    assert "status=committed" in text
    assert "verify_gate" in text


def test_ac5b2_domain_pursuit_missing_domain_id_goes_to_unassigned(tmp_path: Path):
    """真伤3:domain 级 Pursuit 缺 domain_id → 落到显式「_unassigned」目录(**绝不**落随机 uuid)。"""
    pm = PursuitManager(domain_root=tmp_path)
    p = pursuit(id="domain:deadbeef1234", level="domain", statement="无主域目标",
                status="committed")
    pm.persist(p)   # 不传 domain_id
    assert (tmp_path / "_unassigned" / "pursuits.md").exists()
    assert not (tmp_path / "deadbeef1234").exists()   # 不落 p.id 的随机段


def test_ac5c_persist_personal_without_memory_is_noop():
    """没接 MemoryManager 时 → noop,不抛。"""
    pm = PursuitManager()
    p = pursuit(level="atom")
    # 不应抛
    pm.persist(p)


def test_ac5d_persist_domain_without_root_is_noop():
    """没接 domain_root 时 → noop,不抛。"""
    pm = PursuitManager()
    p = pursuit(level="domain")
    pm.persist(p)


# ============ AC6: 闭环测试 — revision 触发后能修订而非盲目坚持 ============
def test_ac6_drift_scenario_revises_instead_of_blind_commitment():
    """模拟 drift:一个长任务跑了 5 步,中途 env 变 test(触发 revision);
    期望:不再盲目 committed → revised,而不是跑完才发现早该重规划。
    """
    pm = PursuitManager()
    p = pursuit(
        id="long:1",
        statement="实现 X",
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        # 验证门永远不满足(否则直接 done 了)
        verify_gate={"type": "predicate", "expr": "phase == never"},
    )
    # 步骤 1-3:phase=build,env=prod → committed
    ctx1 = {"phase": "build", "env": "prod"}
    p1 = pm.step(p, ctx1)
    assert p1.status == "committed"
    # 步骤 4:env 突然变 test(release 部署中)→ 触发 revision
    ctx2 = {"phase": "build", "env": "test"}
    p2 = pm.step(p1, ctx2)
    assert p2.status == "revised"
    # 后续:环境恢复 prod,phase 仍是 build → 重新 committed
    p3 = pm.step(p2, {"phase": "build", "env": "prod"})
    assert p3.status == "committed"


# ============ 额外:eval_condition 子句解析 ============
def test_extra_eval_condition_v1_syntax():
    """v1 条件语法:key == value / key != value / key in / key not in。"""
    assert eval_condition("phase == build", {"phase": "build"}) is True
    assert eval_condition("phase == build", {"phase": "test"}) is False
    assert eval_condition("phase != test", {"phase": "build"}) is True
    assert eval_condition("env in context", {"env": "x"}) is True
    assert eval_condition("env not in context", {}) is True
    # 复合
    assert eval_condition("phase == build, env != prod",
                          {"phase": "build", "env": "test"}) is True
    assert eval_condition("phase == build, env != prod",
                          {"phase": "build", "env": "prod"}) is False
    # 空 → False 兜底
    assert eval_condition("", {}) is False
    assert eval_condition("   ", {}) is False


def test_extra_step_priority_done_over_revised_over_commit():
    """step 内部优先级:done > revised > commit(已 AC4b 验证;这里是穷尽)."""
    pm = PursuitManager()
    # 三条件都满足 → done
    p = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        verify_gate={"type": "predicate", "expr": "phase == done"},
    )
    assert pm.step(p, {"phase": "done", "env": "test"}).status == "done"
    # 只满足 commit + revise → revised
    p2 = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        verify_gate={"type": "predicate", "expr": "phase == never"},
    )
    assert pm.step(p2, {"phase": "build", "env": "test"}).status == "revised"
    # 只满足 commit → committed
    p3 = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        verify_gate={"type": "predicate", "expr": "phase == never"},
    )
    assert pm.step(p3, {"phase": "build", "env": "prod"}).status == "committed"
    # 啥都不满足 → active 保持
    p4 = pursuit(
        commitment_condition="phase == build",
        revision_triggers=["env == test"],
        verify_gate={"type": "predicate", "expr": "phase == never"},
    )
    assert pm.step(p4, {"phase": "init", "env": "prod"}).status == "active"
