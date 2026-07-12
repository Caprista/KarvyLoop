"""test_mesh — 同主人设备 mesh 第一刀:能力指纹 + 设备花名册 + presence(docs/74)。"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.mesh.fingerprint import device_fingerprint  # noqa: E402
from karvyloop.mesh.registry import DeviceRecord, DeviceRegistry  # noqa: E402


# ---- 能力指纹 ----

def test_device_fingerprint_shape(tmp_path):
    fp = device_fingerprint(tmp_path, label="home Linux")
    for k in ("device_id", "label", "os", "arch", "python", "karvyloop", "sandbox"):
        assert k in fp, f"指纹缺字段 {k}"
    assert fp["label"] == "home Linux"
    assert fp["os"] in ("linux", "darwin", "windows") or fp["os"]      # 三平台之一
    assert fp["sandbox"] in ("bwrap", "seatbelt", "win-restricted", "none")
    assert fp["device_id"] == "", "无 relay 密钥时 device_id 该为空(不为取指纹而生成密钥)"


def test_device_fingerprint_reuses_relay_identity(tmp_path):
    """有 relay 密钥时,device_id == relay 身份指纹(设备在 mesh 里可寻址,与 relay-pair 一致)。"""
    pytest.importorskip("cryptography")
    from karvyloop.relay.pairing import PairingStore
    store = PairingStore(tmp_path)
    store.identity()                                    # 生成 relay 密钥
    fp = device_fingerprint(tmp_path)
    assert fp["device_id"] and fp["device_id"] == store.fingerprint()


# ---- 花名册 + presence ----

def test_register_self_and_list(tmp_path):
    reg = DeviceRegistry(tmp_path)
    fp = {"device_id": "abcd-1234", "label": "mac", "os": "darwin", "sandbox": "seatbelt"}
    rec = reg.register_self(fp, now=1000.0)
    assert rec is not None and rec.is_self and rec.last_seen == 1000.0
    devs = reg.list_all()
    assert len(devs) == 1 and devs[0].device_id == "abcd-1234" and devs[0].is_self


def test_register_self_no_device_id_is_noop(tmp_path):
    reg = DeviceRegistry(tmp_path)
    assert reg.register_self({"device_id": "", "label": "x"}) is None
    assert reg.list_all() == []                          # 没身份不入册


def test_online_offline_window(tmp_path):
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="d1", last_seen=1000.0))
    d = reg.get("d1")
    assert d.online(now=1000.0 + 30) is True             # 30s 内 = 在线
    assert d.online(now=1000.0 + 200) is False           # 超窗 = 离线
    assert DeviceRecord(device_id="d2", last_seen=0.0).online(now=1000.0) is False  # 从没见过=离线


def test_persist_roundtrip_and_mark_seen(tmp_path):
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="d1", label="linux", os="linux", room="r0", last_seen=500.0))
    # 新实例重读盘 → 设备还在(持久化)
    reg2 = DeviceRegistry(tmp_path)
    assert reg2.get("d1").label == "linux" and reg2.get("d1").room == "r0"
    assert reg2.mark_seen("d1", now=2000.0) is True
    assert DeviceRegistry(tmp_path).get("d1").last_seen == 2000.0
    assert reg2.mark_seen("nope") is False


def test_remove(tmp_path):
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="d1"))
    assert reg.remove("d1") is True and reg.list_all() == []
    assert reg.remove("d1") is False


# ---- CLI ----

def test_cli_devices_lists_this_device(tmp_path, capsys):
    from karvyloop.cli.main import main
    rc = main(["devices", "--label", "test box", "--dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # 无 relay 身份 → 提示去 relay-pair;但 device_id 空 → 不入册 → "No devices"
    assert "relay-pair" in out or "device mesh" in out
    # 再次跑(建了 relay 身份后)应列出本设备
    pytest.importorskip("cryptography")
    from karvyloop.relay.pairing import PairingStore
    PairingStore(tmp_path).identity()
    main(["devices", "--label", "test box", "--dir", str(tmp_path)])
    out2 = capsys.readouterr().out
    assert "this device" in out2 and "test box" in out2
