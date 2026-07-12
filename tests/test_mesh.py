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


# ---- 知情删除(Hardy §6.2:能力收窄 → 警告 + 再确认)----

def _seed_two(tmp_path):
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="aaaa1111", label="mac",
                              capabilities=["coding", "camera"], last_seen=1000.0))
    reg.register(DeviceRecord(device_id="bbbb2222", label="linux",
                              capabilities=["coding", "shell"], last_seen=1000.0))
    return reg


def test_remove_narrowing_requires_reconfirm(tmp_path, capsys):
    """删唯一有 camera 的设备:没 --yes → 警告+拒删(设备还在);--yes → 真删。"""
    from karvyloop.cli.main import main
    _seed_two(tmp_path)
    rc = main(["devices", "--remove", "mac", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1 and "camera" in out and "PERMANENTLY" in out       # 风险警告点名失去什么
    assert DeviceRegistry(tmp_path).get("aaaa1111") is not None        # 没确认 → 没删
    rc2 = main(["devices", "--remove", "mac", "--yes", "--dir", str(tmp_path)])
    assert rc2 == 0
    assert DeviceRegistry(tmp_path).get("aaaa1111") is None            # 再确认 → 真删


def test_remove_covered_capability_light_confirm(tmp_path, capsys):
    """删能力被其它设备全覆盖的(coding 两台都有→删 linux 只失 shell?不,linux 独占 shell)——
    造一台完全被覆盖的:能力子集 → 轻确认直接删,不要求 --yes。"""
    from karvyloop.cli.main import main
    reg = _seed_two(tmp_path)
    reg.register(DeviceRecord(device_id="cccc3333", label="old-pc",
                              capabilities=["coding"], last_seen=1000.0))   # coding 被两台覆盖
    rc = main(["devices", "--remove", "old-pc", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "no capability lost" in out
    assert DeviceRegistry(tmp_path).get("cccc3333") is None


def test_remove_unknown_and_ambiguous(tmp_path, capsys):
    from karvyloop.cli.main import main
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="aaaa1111", capabilities=["x"]))
    reg.register(DeviceRecord(device_id="aaaa2222", capabilities=["y"]))
    assert main(["devices", "--remove", "zzzz", "--dir", str(tmp_path)]) == 1   # 没匹配
    capsys.readouterr()
    assert main(["devices", "--remove", "aaaa", "--dir", str(tmp_path)]) == 1   # 歧义前缀
    assert "Ambiguous" in capsys.readouterr().out
    assert len(DeviceRegistry(tmp_path).list_all()) == 2                        # 都没误删


# ---- 对抗验收回归锁(2026-07-12):register_self 合并语义 + 坏形态宁空勿崩 ----

def test_register_self_merge_preserves_label_relay_room(tmp_path):
    """刷新登记(指纹不带 label/relay/room)绝不抹掉用户起的名和配对信息 —— 
    对抗验收抓的真伤:开一次设备面板就清空 label/relay_url/room。"""
    reg = DeviceRegistry(tmp_path)
    reg.register_self({"device_id": "self-1", "label": "my-desk-pc", "os": "linux"},
                      relay_url="wss://my.relay", room="room-42")
    # 再登记:无 label/relay/room(GET 花名册 / devices 不带 --label 的真实形态)
    reg.register_self({"device_id": "self-1", "os": "linux"})
    d = reg.get("self-1")
    assert d.label == "my-desk-pc" and d.relay_url == "wss://my.relay" and d.room == "room-42"
    # 显式给新值 → 覆盖(改名仍可用)
    reg.register_self({"device_id": "self-1", "label": "renamed"}, room="room-9")
    d2 = reg.get("self-1")
    assert d2.label == "renamed" and d2.room == "room-9" and d2.relay_url == "wss://my.relay"


def test_registry_corrupt_inner_shapes_never_raise(tmp_path):
    """devices.json 内层坏形态(devices=list / 单条=str / last_seen=非数)→ 宁空勿 500。"""
    reg = DeviceRegistry(tmp_path)
    p = tmp_path / "devices.json"
    p.write_text('{"devices": []}', encoding="utf-8")
    assert reg.list_all() == []                                   # 内层非 dict → 空册
    p.write_text('{"devices": {"a": "junk"}}', encoding="utf-8")
    assert reg.list_all()[0].device_id == ""                      # 单条坏 → 安全默认,不炸
    assert reg.mark_seen("a") is False                            # 坏单条不动手
    p.write_text('{"devices": {"a": {"device_id": "a", "last_seen": "yesterday"}}}',
                 encoding="utf-8")
    assert reg.list_all()[0].last_seen == 0.0                     # 非数 last_seen → 0