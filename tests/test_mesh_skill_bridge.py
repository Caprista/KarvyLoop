"""test_mesh_skill_bridge — 结晶技能接 MeshLog(slice3a create-sync):发事件 + 幂等落地 + 墓碑删除。

技能是设备无关方法体 → 比 belief 更干净(无回声、无设备相对字段)。锁:
- 本地结晶发事件(user scope);system/非 user 不发;
- 远端事件幂等落地(同 name 已在跳过,不覆盖/不演进——create-only);
- 墓碑删除(化解删了又被对账复活);坏 SKILL.md 不进库(宁空勿毒)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import karvyloop.mesh.skill_bridge as sb  # noqa: E402
from karvyloop.mesh.skill_bridge import (  # noqa: E402
    K_SKILL, K_SKILL_REMOVED, apply_skill_events, attach_skill_emitter,
    notify_crystallized, reconcile_skills_from_log,
)
from karvyloop.mesh.synclog import HLC, MeshEvent, MeshLog  # noqa: E402

_MD = "---\nname: make-report\nsignature: abc123\nscope: user\n---\n\n# 做周报\n## Steps\n- 拉数据\n- 汇总\n"


def _reset():
    sb.register_emitter(None)


# ---- 本地结晶 → 发事件(scope 门)----

def test_local_crystallize_emits_user_skill():
    _reset()
    log = MeshLog("dev-a")
    attach_skill_emitter(log)
    notify_crystallized("make-report", "abc123", _MD, "user")
    evs = [e for e in log.entries() if e.kind == K_SKILL]
    assert len(evs) == 1 and evs[0].payload["name"] == "make-report"
    assert evs[0].payload["skill_md"] == _MD and evs[0].payload["origin_device"] == "dev-a"
    _reset()


def test_non_user_scope_not_emitted():
    _reset()
    log = MeshLog("dev-a")
    attach_skill_emitter(log)
    notify_crystallized("sys-skill", "s1", _MD, "domain")     # 非 user(system/域)→ 不同步
    assert len([e for e in log.entries() if e.kind == K_SKILL]) == 0
    _reset()


def test_notify_without_emitter_is_noop():
    _reset()
    notify_crystallized("x", "s", _MD, "user")                # 未接 mesh → 静默无事发生


# ---- 远端事件 → 幂等落地 + 墓碑 ----

def _skill_ev(name, sig, md=_MD, kind=K_SKILL, device="dev-a", wall=1000):
    return MeshEvent(device_id=device, hlc=HLC(wall, 0), kind=kind,
                     payload={"name": name, "signature": sig, "scope": "user",
                              "skill_md": md, "origin_device": device})


def test_apply_lands_skill_and_is_idempotent(tmp_path):
    ev = _skill_ev("make-report", "abc123")
    assert apply_skill_events([ev], tmp_path) == 1
    assert (tmp_path / "make-report" / "SKILL.md").read_text(encoding="utf-8") == _MD
    assert apply_skill_events([ev], tmp_path) == 0            # 同 name 已在 → 幂等跳过(不覆盖)


def test_apply_does_not_overwrite_existing(tmp_path):
    (tmp_path / "make-report").mkdir()
    (tmp_path / "make-report" / "SKILL.md").write_text("本地已有的版本", encoding="utf-8")
    apply_skill_events([_skill_ev("make-report", "abc123")], tmp_path)
    assert (tmp_path / "make-report" / "SKILL.md").read_text(encoding="utf-8") == "本地已有的版本"


def test_tombstone_removes_skill(tmp_path):
    apply_skill_events([_skill_ev("gone", "g1")], tmp_path)
    assert (tmp_path / "gone" / "SKILL.md").exists()
    assert apply_skill_events([_skill_ev("gone", "g1", kind=K_SKILL_REMOVED)], tmp_path) == 1
    assert not (tmp_path / "gone").exists()                  # 墓碑删除(化解复活)


def test_apply_skips_garbage(tmp_path):
    bad = MeshEvent(device_id="x", hlc=HLC(1, 0), kind=K_SKILL,
                    payload={"name": "bad", "skill_md": ""})   # 空 SKILL.md
    assert apply_skill_events([bad], tmp_path) == 0
    assert not (tmp_path / "bad").exists()


def test_apply_ignores_non_skill_kinds(tmp_path):
    ev = MeshEvent(device_id="x", hlc=HLC(1, 0), kind="belief-created", payload={"content": "x"})
    assert apply_skill_events([ev], tmp_path) == 0


def test_reconcile_from_log(tmp_path):
    log = MeshLog("dev-b")
    log.merge([_skill_ev("synced-skill", "s9")], wall=2000)
    assert reconcile_skills_from_log(log, tmp_path) == 1
    assert (tmp_path / "synced-skill" / "SKILL.md").exists()
    assert reconcile_skills_from_log(log, tmp_path) == 0      # 再对账 → 幂等


# ---- 端到端:A 结晶的技能,B 落地 ----

def test_two_device_skill_sync_end_to_end(tmp_path):
    _reset()
    log_a, log_b = MeshLog("dev-a"), MeshLog("dev-b")
    b_skills = tmp_path / "b_skills"
    attach_skill_emitter(log_a)                              # A 装发射器
    notify_crystallized("hardy-report-style", "sigX", _MD, "user")   # A 结晶

    a2b = log_a.delta(log_b.frontier())
    fresh = [e for e in a2b if not log_b.contains(e.event_id)]
    log_b.merge(a2b, wall=3000)
    assert apply_skill_events(fresh, b_skills) == 1          # B 落地 A 结晶的技能
    assert (b_skills / "hardy-report-style" / "SKILL.md").read_text(encoding="utf-8") == _MD
    _reset()
