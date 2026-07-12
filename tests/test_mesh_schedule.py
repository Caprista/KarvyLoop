"""test_mesh_schedule — 异构调度地基:feasibility 硬过滤 + 删除能力 delta + 定时预警(docs/74 §6)。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.mesh.registry import DeviceRecord  # noqa: E402
from karvyloop.mesh.schedule import (  # noqa: E402
    capability_delta_on_remove, feasible, feasible_devices, scheduled_task_at_risk,
)


def _dev(did, caps, last_seen=1000.0):
    return DeviceRecord(device_id=did, capabilities=list(caps), last_seen=last_seen)


# ---- feasibility 布尔硬过滤 ----

def test_feasible_boolean():
    assert feasible(["coding"], ["coding", "shell"]) is True       # 能力覆盖 → 可行
    assert feasible(["coding", "camera"], ["coding"]) is False     # 缺 camera → 不可行
    assert feasible([], ["anything"]) is True                      # 无要求 → 人人可行


def test_feasible_devices_capability_and_online():
    pc = _dev("pc", ["coding", "shell", "big-task", "sandbox"], last_seen=1000.0)
    phone = _dev("phone", ["camera", "location", "voice"], last_seen=1000.0)
    devs = [pc, phone]
    # coding 活 → 只 PC 可行(手机能力不匹配,天然不硬派)
    assert [d.device_id for d in feasible_devices(["coding"], devs, now=1000.0)] == ["pc"]
    # 拍照活 → 只手机
    assert [d.device_id for d in feasible_devices(["camera"], devs, now=1000.0)] == ["phone"]


def test_feasible_devices_excludes_offline():
    pc_off = _dev("pc", ["coding"], last_seen=1000.0)
    devs = [pc_off]
    assert feasible_devices(["coding"], devs, now=1000.0 + 30) == [pc_off]     # 在线
    assert feasible_devices(["coding"], devs, now=1000.0 + 200) == []          # 离线 → 无可行
    # require_online=False(定时提前查):不看在线,看全体能力
    assert [d.device_id for d in feasible_devices(["coding"], devs, now=1000.0 + 200,
                                                  require_online=False)] == ["pc"]


# ---- 删除能力 delta(能力边界收窄 → 警告+再确认)----

def test_capability_delta_on_remove():
    mac = _dev("mac", ["coding", "camera"])          # 唯一有 camera 的
    linux = _dev("linux", ["coding", "shell", "big-task"])
    devs = [mac, linux]
    # 删 mac → 失去 camera(独占)+ coding 不失去(linux 也有)
    assert capability_delta_on_remove(mac, devs) == {"camera"}
    # 删 linux → 失去 shell/big-task(独占);coding 不失去
    assert capability_delta_on_remove(linux, devs) == {"shell", "big-task"}


def test_capability_delta_empty_when_covered():
    a = _dev("a", ["coding"])
    b = _dev("b", ["coding"])                          # 另一台也有 coding
    assert capability_delta_on_remove(a, [a, b]) == set()   # 删 a 不收窄(b 覆盖)→ 低风险


# ---- 定时任务提前预警 ----

def test_scheduled_task_at_risk():
    linux = _dev("linux", ["coding", "big-task"], last_seen=1000.0)
    devs = [linux]
    # linux 在线 → 备份(needs coding)不 at-risk
    assert scheduled_task_at_risk(["coding"], devs, now=1000.0 + 30) is False
    # linux 离线 → 唯一能跑的没了 → at-risk(该提前警告)
    assert scheduled_task_at_risk(["coding"], devs, now=1000.0 + 200) is True
    # 需要 camera 但根本没设备有 → at-risk
    assert scheduled_task_at_risk(["camera"], devs, now=1000.0 + 30) is True


def test_fingerprint_advertises_capabilities(tmp_path):
    """指纹带 execution 能力集(PC 三平台 → coding/shell/big-task;extra 可自报)。"""
    from karvyloop.mesh.fingerprint import device_fingerprint
    fp = device_fingerprint(tmp_path, extra_capabilities=["camera"])
    caps = set(fp["capabilities"])
    assert "camera" in caps                            # 自报的额外能力进指纹
    # 本机是 PC(测试跑在三平台之一)→ 该有 coding
    assert "coding" in caps or fp["os"] not in ("linux", "darwin", "windows")
