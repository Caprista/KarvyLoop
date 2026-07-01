"""instance —— M2.0 拍 7 Instance Manager 测试(8 个:7 AC + 1 协议)。

设计:docs/17 §7 AC。
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.instance import (  # noqa: E402
    SOUL_SUBSETS,
    Health,
    HealthStatus,
    Instance,
    ScheduleError,
    ScheduleRequest,
    Scheduler,
    default_scheduler,
    demote_if_drifting,
    get_soul_subset,
)
from karvyloop.instance.context import (  # noqa: E402
    SCENARIO_DM,
    SCENARIO_SHARE,
    SCENARIO_TRANSFER,
    SCENARIO_WORK,
)
from karvyloop.ethos import (  # noqa: E402
    AuditorAttestation,
    AuditorFinding,
    AuditorReport,
    ERROR,
    INFO,
    WARNING,
)


# ---------- fixtures ----------

def _empty_report(ok: bool = True) -> AuditorReport:
    """空 report(0 findings)——Attestation 仍走流程。"""
    return AuditorReport(agent_id="a1", findings=(), checks_run=8, checks_passed=8 if ok else 0)


def _att_ok(agent_id: str = "a1", info_marker: str = "v1") -> AuditorAttestation:
    """ok=True 的 attestation,加 1 个 info finding 当 hash 差异源。"""
    report = AuditorReport(
        agent_id=agent_id,
        findings=(
            AuditorFinding(
                check_id=f"soul.identity_{info_marker}",
                severity=INFO,
                message=f"info-{info_marker}",
            ),
        ),
        checks_run=8,
        checks_passed=8,
    )
    return AuditorAttestation.from_report(report, attested_at="2026-06-16T00:00:00+00:00")


def _att_bad(agent_id: str = "a1") -> AuditorAttestation:
    return AuditorAttestation.from_report(
        AuditorReport(
            agent_id=agent_id,
            findings=(
                AuditorFinding(
                    check_id="soul.slot_present",
                    severity=ERROR,
                    message="IDENTITY missing",
                    slot="IDENTITY",
                ),
            ),
            checks_run=8,
            checks_passed=0,
        ),
        attested_at="2026-06-16T00:00:00+00:00",
    )


# ---------- AC1:Scenario → 灵魂层子集 ----------

def test_ac1_scenario_to_subset():
    """AC1:4 scenario 各返正确子集(transfer=7/work=5/dm=1/share=2)。"""
    assert len(get_soul_subset(SCENARIO_TRANSFER)) == 7
    assert len(get_soul_subset(SCENARIO_WORK)) == 5
    assert len(get_soul_subset(SCENARIO_DM)) == 1
    assert len(get_soul_subset(SCENARIO_SHARE)) == 2
    # transfer = 全 7
    assert "IDENTITY" in get_soul_subset(SCENARIO_TRANSFER)
    assert "COMPOSITION" in get_soul_subset(SCENARIO_TRANSFER)
    # dm = 只 IDENTITY
    assert get_soul_subset(SCENARIO_DM) == ("IDENTITY",)
    # share = SOUL + COMPOSITION
    assert set(get_soul_subset(SCENARIO_SHARE)) == {"SOUL", "COMPOSITION"}


# ---------- AC2:schedule() 绑 attestation_hash ----------

def test_ac2_schedule_binds_attestation_hash():
    """AC2:实例化后 attestation_hash == 注入值(M1)。"""
    s = default_scheduler()
    att = _att_ok()
    req = ScheduleRequest(agent_id="a1", scenario=SCENARIO_WORK, attestation=att)
    inst = s.schedule(req)
    assert inst.attestation_hash == att.attestation_hash
    assert inst.scenario == SCENARIO_WORK
    assert inst.state == "active"
    assert inst.soul_subset == get_soul_subset(SCENARIO_WORK)


# ---------- AC3:dismissed instance 不接受新请求 ----------

def test_ac3_dismissed_instance_rejects_transfer():
    """AC3:state=dismissed 时调 schedule 抛 ScheduleError(M4)。"""
    s = default_scheduler()
    inst = s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_WORK, attestation=_att_ok()))
    s.dismiss(inst.instance_id)
    # 调岗:新 instance_id 用旧的(应拒)
    with pytest.raises(ScheduleError):
        s.schedule(ScheduleRequest(
            agent_id="a1",
            scenario=SCENARIO_TRANSFER,
            attestation=_att_ok(),
            instance_id=inst.instance_id,
        ))


# ---------- AC4:调岗新 Attestation ----------

def test_ac4_transfer_uses_new_attestation():
    """AC4:transfer 场景新 instance attestation_hash ≠ 旧 instance(M6)。"""
    s = default_scheduler()
    old = s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_WORK, attestation=_att_ok()))
    # 新 attestation(找 hash 不同)
    new_att = _att_ok(info_marker="v2")
    assert new_att.attestation_hash != old.attestation_hash
    new = s.schedule(ScheduleRequest(
        agent_id="a1",
        scenario=SCENARIO_TRANSFER,
        attestation=new_att,
        instance_id=old.instance_id,
    ))
    assert new.attestation_hash == new_att.attestation_hash
    assert new.attestation_hash != old.attestation_hash
    # 旧 instance 应被标 dismissed
    assert s.get(old.instance_id).state == "dismissed"


# ---------- AC5:drift 升高 → 强制降级 ----------

def test_ac5_drift_high_triggers_demote():
    """AC5:drift_ratio ≥ 阈值 → demote 强制 dismiss(M5)。"""
    s = default_scheduler()
    inst = s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_WORK, attestation=_att_ok()))
    # 把 drift_ratio 升到 0.5(超过默认 0.3)
    s.update_drift(inst.instance_id, 0.5)
    demoted = demote_if_drifting(s, inst.instance_id)
    assert demoted is not None
    assert demoted.state == "dismissed"
    # Health 报告
    h = Health(scheduler=s)
    status = h.check(inst.instance_id)
    assert status.is_healthy is False
    assert status.recommended_action == "dismiss"


# ---------- AC6:Auditor 不健康 → 不允许 new instance ----------

def test_ac6_auditor_not_ok_rejects_schedule():
    """AC6:ok=False → schedule 抛 ScheduleError(M1 反面)。"""
    s = default_scheduler()
    bad = _att_bad()
    assert bad.ok is False
    with pytest.raises(ScheduleError):
        s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_WORK, attestation=bad))


# ---------- AC7:4 scenario 全有时才协议完整 ----------

def test_ac7_protocol_has_all_4_scenarios():
    """AC7:SOUL_SUBSETS 锁住 4 键(M3)。"""
    assert set(SOUL_SUBSETS.keys()) == {
        SCENARIO_TRANSFER,
        SCENARIO_WORK,
        SCENARIO_DM,
        SCENARIO_SHARE,
    }
    # 缺一个就破协议
    assert len(SOUL_SUBSETS) == 4


# ---------- AC8:协议不变量(综合)----------

def test_ac8_invariants_frozen():
    """AC8 协议:Instance 不可变 / 全注入 / 0 LLM。"""
    inst = Instance(
        instance_id="i1",
        agent_id="a1",
        scenario=SCENARIO_DM,
        soul_subset=("IDENTITY",),
        attestation_hash="deadbeef",
        created_at="2026-06-16T00:00:00+00:00",
    )
    # frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        inst.state = "dismissed"  # type: ignore[misc]

    # 0 LLM 引用:扫源码
    instance_dir = ROOT / "karvyloop" / "instance"
    for src in instance_dir.glob("*.py"):
        content = src.read_text(encoding="utf-8")
        assert "openai" not in content.lower()
        assert "anthropic" not in content.lower()
        assert "litellm" not in content.lower()

    # 全注入:id_factory / timestamp_fn / scheduler 注入
    custom_ids = iter(["x1", "x2"])
    s = Scheduler(id_factory=lambda: next(custom_ids), timestamp_fn=lambda: "2026-06-16")
    inst1 = s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_DM, attestation=_att_ok()))
    inst2 = s.schedule(ScheduleRequest(agent_id="a1", scenario=SCENARIO_DM, attestation=_att_ok()))
    assert inst1.instance_id == "x1"
    assert inst2.instance_id == "x2"
    assert inst1.created_at == "2026-06-16"
