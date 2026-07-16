"""test_routes_pair — 📱 /api/pair/* 配对管理端点 + 特权分离锁(docs/74 配对身份切片)。

三层鉴权里的"管理权=本地":经隧道请求带 `x-karvy-via-relay`(relay/client.py 咽喉注入,
远端伪造不进)→ 管理端点见标即拒。偷来的手机经隧道永远造不出新授权、吊销不了别的设备。
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _client(relay_url: str = "wss://relay.test") -> TestClient:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.relay_url = relay_url
    return TestClient(app)


def _patch_store(monkeypatch, tmp_path):
    """让端点的 PairingStore 落 tmp(不碰真 ~/.karvyloop)。"""
    from karvyloop.relay.pairing import PairingStore
    import karvyloop.console.routes_pair as rp
    monkeypatch.setattr(rp, "_store", lambda: PairingStore(tmp_path))


def test_issue_returns_one_time_invite(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue").json()
    assert r["ok"] is True
    assert r["relay"] == "wss://relay.test"            # BYO:来自运行时配置,非硬编码
    assert r["room"].startswith("r") and len(r["room"]) >= 8
    assert r["fingerprint"].count("-") == 3
    assert len(r["code"]) == 9 and "-" in r["code"]    # XXXX-XXXX
    r2 = _client().post("/api/pair/issue").json()
    assert r2["code"] != r["code"]                     # 每次新签(重出二维码=新鲜邀请)


def test_issue_includes_mesh_room(monkeypatch, tmp_path):
    """配对邀请带 mesh 房(docs/74):新设备既知主房(远程访问)也知 mesh 房(设备间同步拨入,
    不跟 away 浏览器抢主房 client 位)。稳定房号,重签邀请不变。"""
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue").json()
    assert r["ok"] is True
    assert r["mesh_room"].startswith("m") and len(r["mesh_room"]) >= 8
    assert r["mesh_room"] != r["room"]                 # 与主房分离,才解 room_busy 抢坑
    r2 = _client().post("/api/pair/issue").json()
    assert r2["mesh_room"] == r["mesh_room"]           # 稳定(码一次性,房号持久)


def test_issue_without_relay_is_honest(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    r = _client(relay_url="").post("/api/pair/issue").json()
    assert r["ok"] is False and "relay" in r["reason"]


def test_issue_default_full_no_role(monkeypatch, tmp_path):
    """不带 body(旧前端)= 原行为:自有设备 full 码、无 role 绑定(零回归)。"""
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue").json()
    assert r["ok"] is True and r["scope"] == "full" and r["role"] == ""


def test_issue_with_role_signs_read_share_code(monkeypatch, tmp_path):
    """带 role = 分享码(给顾问看,docs/78 §4.3):scope 收到 read + role 绑定;
    真配对后 paired 记录带 role(咽喉据此注 x-karvy-audience-role,外部召回只放该角色兵法)。"""
    _patch_store(monkeypatch, tmp_path)
    from karvyloop.relay import e2e
    from karvyloop.relay.pairing import PairingStore
    r = _client().post("/api/pair/issue", json={"role": "写作助手"}).json()
    assert r["ok"] is True
    assert r["scope"] == "read" and r["role"] == "写作助手"   # 分享码=只读,绝不发全权
    store = PairingStore(tmp_path)
    _, pub = e2e.gen_keypair()
    assert store.verify_and_consume(pub, e2e.pair_mac(r["code"], pub)) is True
    assert store.scope_for(pub.hex()) == "read"
    assert store.role_for(pub.hex()) == "写作助手"
    assert store.list_paired()[0]["role"] == "写作助手"


def test_issue_explicit_read_scope_without_role(monkeypatch, tmp_path):
    """分享 UI 的"不选角色=纯只读":POST {scope:"read"} → read 码、无 role 绑定
    (对方能看不能改;外部召回 deny-by-default 全拒)。真配对后 scope 如实落 read。"""
    _patch_store(monkeypatch, tmp_path)
    from karvyloop.relay import e2e
    from karvyloop.relay.pairing import PairingStore
    r = _client().post("/api/pair/issue", json={"scope": "read"}).json()
    assert r["ok"] is True and r["scope"] == "read" and r["role"] == ""
    store = PairingStore(tmp_path)
    _, pub = e2e.gen_keypair()
    assert store.verify_and_consume(pub, e2e.pair_mac(r["code"], pub)) is True
    assert store.scope_for(pub.hex()) == "read"
    assert store.role_for(pub.hex()) == ""


def test_issue_scope_only_narrows_never_escalates(monkeypatch, tmp_path):
    """方向只许收窄:带 role 的码请求 scope=full 也一律 read(分享意图绝不发全权);
    未知 scope deny-by-default 降 read。"""
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue", json={"scope": "full", "role": "写作助手"}).json()
    assert r["ok"] is True and r["scope"] == "read" and r["role"] == "写作助手"
    r2 = _client().post("/api/pair/issue", json={"scope": "admin"}).json()
    assert r2["ok"] is True and r2["scope"] == "read"


def test_issue_explicit_full_scope_is_own_device(monkeypatch, tmp_path):
    """显式 scope=full 且无 role = 自有设备码(与不带 body 同义,不是新权限)。"""
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue", json={"scope": "full"}).json()
    assert r["ok"] is True and r["scope"] == "full" and r["role"] == ""


def test_issue_with_garbage_role_stays_read_unbound(monkeypatch, tmp_path):
    """坏 role(控制字符,会破 HTTP 头)宁空勿毒 → 仍是 read 分享码但**无绑定**(召回全拒);
    绝不能因消毒失败反向掉成 full 全权码。响应 role="" 让调用方看得见绑定没生效。"""
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/issue", json={"role": "bad\r\nrole"}).json()
    assert r["ok"] is True and r["role"] == "" and r["scope"] == "read"


def test_devices_and_revoke_roundtrip(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    from karvyloop.relay import e2e
    from karvyloop.relay.pairing import PairingStore
    store = PairingStore(tmp_path)
    code = store.new_code("full")
    priv, pub = e2e.gen_keypair()
    assert store.verify_and_consume(pub, e2e.pair_mac(code, pub))   # 真配对一台
    c = _client()
    devs = c.get("/api/pair/devices").json()
    assert devs["ok"] is True and len(devs["devices"]) == 1
    fp = devs["devices"][0]["fingerprint"]
    rv = c.post("/api/pair/revoke", json={"ident": fp}).json()
    assert rv["ok"] is True
    assert c.get("/api/pair/devices").json()["devices"] == []       # 吊销即除名


def test_revoke_unknown_is_honest(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    r = _client().post("/api/pair/revoke", json={"ident": "dead-beef-dead-beef"}).json()
    assert r["ok"] is False


def test_management_rejects_via_relay_requests(monkeypatch, tmp_path):
    """特权分离命门:带 x-karvy-via-relay(=经隧道)的请求,三个管理端点全拒。"""
    _patch_store(monkeypatch, tmp_path)
    c = _client()
    h = {"x-karvy-via-relay": "1"}
    assert c.post("/api/pair/issue", headers=h).json()["ok"] is False
    assert c.get("/api/pair/devices", headers=h).json()["devices"] == []
    assert c.post("/api/pair/revoke", json={"ident": "abcd-abcd"}, headers=h).json()["ok"] is False


def test_relay_client_choke_injects_via_relay_marker():
    """咽喉注入锁:relay/client.py 转发头必带 via-relay 标,且远端头只透传白名单三样
    (伪造/剥除都不可能)——直接读源码断言两条纪律都在。"""
    src = (ROOT / "karvyloop" / "relay" / "client.py").read_text(encoding="utf-8")
    assert 'headers["x-karvy-via-relay"] = "1"' in src
    assert '_FWD_REQ_HEADERS = ("content-type", "accept", "accept-language")' in src


def test_access_url_returns_qr_targets(monkeypatch):
    """📱 扫码入口取码端点:runtime 在 → 给带 token 的 console/m 双链接(QR 渲染源)。"""
    import karvyloop.console.routes_pair as rp_mod  # noqa: F401  (确认模块可导入)
    import karvyloop.console.access as access
    monkeypatch.setattr(access, "read_runtime",
                        lambda: {"host": "0.0.0.0", "port": 8766, "token": "FAKE-TOKEN-DO-NOT-LEAK"})
    monkeypatch.setattr(access, "_lan_ip", lambda: "192.168.1.5")
    r = _client().get("/api/access_url").json()
    assert r["ok"] is True
    assert r["console"] == "http://192.168.1.5:8766/?token=FAKE-TOKEN-DO-NOT-LEAK"
    assert r["m"] == "http://192.168.1.5:8766/m?token=FAKE-TOKEN-DO-NOT-LEAK"


def test_access_url_local_only_binding_is_honest(monkeypatch):
    """绑 localhost → remote 为空 + local_only 标(前端引导改绑 0.0.0.0,不出坏码)。"""
    import karvyloop.console.access as access
    monkeypatch.setattr(access, "read_runtime",
                        lambda: {"host": "127.0.0.1", "port": 8766, "token": "FAKE-DO-NOT-LEAK"})
    r = _client().get("/api/access_url").json()
    assert r["ok"] is True and r["m"] == "" and r["local_only"] is True


def test_access_url_rejects_via_relay(monkeypatch):
    """管理权=本地:经隧道的请求拿不到 LAN 令牌(偷来的手机造不出新入口)。"""
    import karvyloop.console.access as access
    monkeypatch.setattr(access, "read_runtime",
                        lambda: {"host": "0.0.0.0", "port": 8766, "token": "FAKE-DO-NOT-LEAK"})
    r = _client().get("/api/access_url", headers={"x-karvy-via-relay": "1"}).json()
    assert r["ok"] is False
    assert "FAKE" not in str(r)   # 防泄露断言:拒绝响应里绝不带 token


def test_config_relay_roundtrip(tmp_path):
    from karvyloop.config_relay import read_relay, write_relay
    cfg = tmp_path / "config.yaml"
    assert read_relay(cfg) is None
    assert write_relay("wss://my.relay", cfg) is True
    assert read_relay(cfg) == "wss://my.relay"
    cfg_text = cfg.read_text(encoding="utf-8")
    assert write_relay("", cfg) is True                # 清掉
    assert read_relay(cfg) is None
    assert "relay" in cfg_text                          # 写入时真落过盘
