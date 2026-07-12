"""test_routes_mesh — console mesh 同步端点:frontier + sync 端点驱动的双向收敛。"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.mesh.synclog import HLC, MeshEvent, MeshLog  # noqa: E402


def _app(tmp_path, device_id="dev-a"):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.mesh_state_dir = tmp_path        # 别碰 ~/.karvyloop
    app.state.mesh_log = MeshLog(device_id)    # 预置本机(设备 A)日志
    app.state.mesh_log_store = None            # 测试不落盘
    return app


def test_frontier_endpoint(tmp_path):
    app = _app(tmp_path)
    app.state.mesh_log.append("trace", {"x": 1}, wall=1000)
    body = TestClient(app).get("/api/mesh/frontier").json()
    assert body["device_id"] == "dev-a"
    assert "dev-a" in body["frontier"]


def test_sync_endpoint_merges_and_returns_delta(tmp_path):
    app = _app(tmp_path)
    a_ev = app.state.mesh_log.append("belief-created", {"m": "A 学到"}, wall=1000)
    client = TestClient(app)
    # 设备 B 推一条它的事件 + 空 frontier(它啥都没有)→ A 合并,回 A 的 delta
    b_ev = MeshEvent(device_id="dev-b", hlc=HLC(1000, 0), kind="skill-crystallized", payload={"s": "B 做"})
    resp = client.post("/api/mesh/sync", json={"frontier": {}, "events": [b_ev.to_dict()]}).json()
    assert resp["merged"] == 1                                  # A 合并了 B 的 1 条
    got = {e["device_id"] + "@" + e["hlc"] for e in resp["events"]}
    assert a_ev.event_id in got                                # A 把自己的事件回给了 B
    # A 现在有两条(自己的 + B 的)
    assert len(app.state.mesh_log) == 2


def test_endpoint_driven_bidirectional_convergence(tmp_path):
    """真实同步流:设备 B 拉 A frontier → 推 B delta → A 合并回 A delta → B 合并 → 两边收敛。"""
    app = _app(tmp_path, device_id="dev-a")
    app.state.mesh_log.append("belief-created", {"m": "A 的记忆"}, wall=1000)
    client = TestClient(app)

    b = MeshLog("dev-b")
    b.append("skill-crystallized", {"s": "B 的技能"}, wall=1000)

    # ① B 拉 A 的 frontier
    a_fr_raw = client.get("/api/mesh/frontier").json()["frontier"]
    a_frontier = {d: HLC.parse(v) for d, v in a_fr_raw.items()}
    # ② B 算它对 A 的 delta,连自己 frontier 一起 POST
    b_delta = [e.to_dict() for e in b.delta(a_frontier)]
    b_frontier = {d: str(h) for d, h in b.frontier().items()}
    resp = client.post("/api/mesh/sync", json={"frontier": b_frontier, "events": b_delta}).json()
    # ③ B 合并 A 回的 delta
    b.merge([MeshEvent.from_dict(e) for e in resp["events"]], wall=2000)

    a_ids = {e.event_id for e in app.state.mesh_log.entries()}
    b_ids = {e.event_id for e in b.entries()}
    assert a_ids == b_ids, f"端点同步后未收敛: {a_ids} vs {b_ids}"
    assert len(a_ids) == 2                                       # A 的记忆 + B 的技能,两边都有


def _seed_devices(tmp_path):
    """种两台设备:PC(coding+shell)在线 / Phone(camera 独占)离线。"""
    import time
    from karvyloop.mesh.registry import DeviceRecord, DeviceRegistry
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="pc-1", label="PC", capabilities=["coding", "shell"],
                              last_seen=time.time()))
    reg.register(DeviceRecord(device_id="ph-1", label="Phone", capabilities=["camera"]))
    return reg


def test_devices_endpoint_lists_roster_with_presence(tmp_path):
    """花名册端点:列设备 + 在线态(last_seen 新鲜度);无 relay 身份 → has_identity=False 诚实提示。"""
    _seed_devices(tmp_path)
    body = TestClient(_app(tmp_path)).get("/api/mesh/devices").json()
    assert body["has_identity"] is False            # tmp 目录无 relay 身份 → 本机不入册,不假装
    by_id = {d["device_id"]: d for d in body["devices"]}
    assert by_id["pc-1"]["online"] is True and by_id["ph-1"]["online"] is False
    assert by_id["ph-1"]["capabilities"] == ["camera"]


def test_device_remove_narrowing_requires_confirm(tmp_path):
    """知情删除:camera 只有 Phone 提供 → 不带 confirm 先回"会永久失去 camera",不动手;
    confirm=true 才真删(docs/74 §6.2 的 H2A)。"""
    reg = _seed_devices(tmp_path)
    client = TestClient(_app(tmp_path))
    r = client.post("/api/mesh/devices/remove", json={"device_id": "ph-1"}).json()
    assert r["requires_confirm"] is True and r["narrowed"] == ["camera"]
    assert any(d.device_id == "ph-1" for d in reg.list_all())      # 没确认 → 还在
    r2 = client.post("/api/mesh/devices/remove", json={"device_id": "ph-1", "confirm": True}).json()
    assert r2["ok"] is True and r2["narrowed"] == ["camera"]
    assert not any(d.device_id == "ph-1" for d in reg.list_all())  # 确认 → 删了


def test_device_remove_covered_is_direct(tmp_path):
    """能力被覆盖(第二台 PC 的 coding/shell 是子集)→ 只降资源不降能力 → 无需确认直接删。"""
    import time
    from karvyloop.mesh.registry import DeviceRecord
    reg = _seed_devices(tmp_path)
    reg.register(DeviceRecord(device_id="pc-2", label="Laptop", capabilities=["coding"],
                              last_seen=time.time()))
    r = TestClient(_app(tmp_path)).post("/api/mesh/devices/remove",
                                        json={"device_id": "pc-2"}).json()
    assert r["ok"] is True and r["narrowed"] == []
    assert not any(d.device_id == "pc-2" for d in reg.list_all())


def test_devices_endpoint_survives_corrupt_state_file(tmp_path):
    """devices.json 内层坏形态 → 花名册端点宁空勿 500(对抗验收回归锁)。"""
    (tmp_path / "devices.json").write_text('{"devices": []}', encoding="utf-8")
    r = TestClient(_app(tmp_path)).get("/api/mesh/devices")
    assert r.status_code == 200 and r.json()["devices"] == []


def test_device_remove_unknown_is_not_found(tmp_path):
    _seed_devices(tmp_path)
    r = TestClient(_app(tmp_path)).post("/api/mesh/devices/remove",
                                        json={"device_id": "nope"}).json()
    assert r["ok"] is False and r["reason"] == "not_found"


def test_sync_endpoint_applies_remote_belief_into_memory(tmp_path):
    """接线点③(影响评估):sync 端点收到远端 belief 事件 → 幂等回放进 app.state.memory
    (store 保主真相,经现有写咽喉)→ 本设备立刻可召回"A 学的"。"""
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.mesh.cognition_bridge import K_BELIEF

    app = _app(tmp_path)
    app.state.memory = MemoryManager()
    ev = MeshEvent(device_id="dev-b", hlc=HLC(1000, 0), kind=K_BELIEF, payload={
        "content": "B 设备学到的偏好", "provenance": {"source": "t", "origin_device": "dev-b"},
        "freshness_ts": 1.0, "scope": "personal", "invalid_at": None, "invalid_reason": ""})
    client = TestClient(app)
    r = client.post("/api/mesh/sync", json={"frontier": {}, "events": [ev.to_dict()]}).json()
    assert r["merged"] == 1
    assert app.state.memory._index.get("B 设备学到的偏好") is not None, "远端认知没落进记忆库"
    # 再同步同一条 → 日志去重 + 库幂等,不重复
    r2 = client.post("/api/mesh/sync", json={"frontier": {}, "events": [ev.to_dict()]}).json()
    assert r2["merged"] == 0
