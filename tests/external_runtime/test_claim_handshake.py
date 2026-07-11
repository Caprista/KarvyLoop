"""test_claim_handshake — 认领码配对握手(反向接入:建壳发码 → runtime 回连认领 → 激活)。

覆盖三层:
① 注册表层(ExternalCitizenRegistry):create_pending 建壳发码 / claim 校验激活 /
   一次性(重放拒)/ 过期拒 / 错秘钥拒 / 绑单壳(别的壳的码认不了)/ cancel_pending /
   ticket 只落摘要不落明文(秘钥纪律)/ 持久化(重启后仍可认领)。
② HTTP 端点层(routes_external):create_pending / claim / cancel_pending 全链 +
   来源门(公网拒)+ 自报能力 untrusted 登记不提权 + 秘钥不进响应外的地方。
③ 连接器脚本(connector.py):自报载荷组装 / 缺秘钥 fail-loud / 认领往返(打真实 TestClient)。

**秘钥 fixture 纪律**:测试里出现的认领秘钥明文一律带 FAKE / DO-NOT-LEAK 字样,配防泄露断言。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.external_runtime import (  # noqa: E402
    STATUS_ACTIVE,
    STATUS_PENDING,
    ClaimTicket,
    ClaimTicketStore,
    ExternalCitizen,
    ExternalCitizenRegistry,
    ExternalCitizenStore,
    mint_claim_ticket,
    split_claim_secret,
)
from karvyloop.external_runtime import connector as _connector  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _reg(tmp_path):
    return ExternalCitizenRegistry(
        store=ExternalCitizenStore(tmp_path / "citizens.json"),
        ticket_store=ClaimTicketStore(tmp_path / "tickets.json"))


# ============ ① 注册表层 ============

def test_create_pending_builds_shell_and_issues_secret(tmp_path):
    reg = _reg(tmp_path)
    pending, secret, err = reg.create_pending("cc")
    assert err == ""
    assert pending is not None
    assert pending.status == STATUS_PENDING
    assert pending.citizen_id == "cc"
    # 明文秘钥形态 = "<ticket_id>.<secret>"(前段公开定位、后段密文)
    tid, sec = split_claim_secret(secret)
    assert tid and sec
    # 壳进了注册表(pending 态)
    assert reg.resolve_in("", "cc").status == STATUS_PENDING


def test_create_pending_rejects_duplicate(tmp_path):
    reg = _reg(tmp_path)
    reg.create_pending("cc")
    pending, secret, err = reg.create_pending("cc")
    assert pending is None and secret == "" and err   # 复合键已存在 → 拒


def test_claim_activates_shell_and_records_self_report(tmp_path):
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    # 外部 runtime 自报身份/能力(untrusted)
    res = reg.claim(secret, reported={
        "runtime_kind": "generic_cli", "bin_path": "/opt/ext/cli",
        "version": "vFAKE", "capabilities": ["code", "web"]})
    assert res["ok"] is True
    activated = reg.resolve_in("", "cc")
    assert activated.status == STATUS_ACTIVE
    assert activated.runtime_kind == "generic_cli"
    assert activated.bin_path == "/opt/ext/cli"
    card = activated.capability_card
    assert card["self_reported"] is True   # 明标 untrusted 自报,非我们探的
    assert card["reported_capabilities"] == ["code", "web"]
    assert card["version"] == "vFAKE"


def test_claim_is_one_time_replay_rejected(tmp_path):
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    assert reg.claim(secret)["ok"] is True
    # 重放同一把秘钥 → 拒(一次性,已 used)
    replay = reg.claim(secret)
    assert replay["ok"] is False


def test_claim_expired_secret_rejected(tmp_path):
    reg = _reg(tmp_path)
    # 建壳时 now=1000,ttl=600 → 过期墙 1600;认领时 now=2000(远超墙)→ 过期拒
    _, secret, _ = reg.create_pending("cc", ttl_s=600, now=1000.0)
    res = reg.claim(secret, now=2000.0)
    assert res["ok"] is False
    # 壳仍 pending(没被激活)
    assert reg.resolve_in("", "cc").status == STATUS_PENDING
    # 但在过期墙之内认领仍成功(证明是过期拦的,不是别的)
    assert reg.claim(secret, now=1500.0)["ok"] is True


def test_claim_wrong_secret_rejected(tmp_path):
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    tid, _sec = split_claim_secret(secret)
    # 对的 ticket_id + 错的 secret 段
    forged = f"{tid}.FAKE-DO-NOT-LEAK-WRONG"
    assert reg.claim(forged)["ok"] is False
    # 真秘钥仍能用(错的那次没作废它)
    assert reg.claim(secret)["ok"] is True


def test_claim_garbage_secret_rejected(tmp_path):
    reg = _reg(tmp_path)
    reg.create_pending("cc")
    for bad in ("", "no-dot-here", ".", "unknownid.FAKE-DO-NOT-LEAK"):
        assert reg.claim(bad)["ok"] is False


def test_ticket_bound_to_single_shell(tmp_path):
    """一把码只认它绑定的那个壳:壳被删/换名后,这把码认不了别的。"""
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    # 删掉 pending 壳(cancel)→ 码悬空 → 认领拒
    assert reg.cancel_pending("", "cc") is True
    assert reg.claim(secret)["ok"] is False


def test_cancel_pending_removes_shell_and_voids_ticket(tmp_path):
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    assert reg.cancel_pending("", "cc") is True
    assert reg.resolve_in("", "cc") is None
    assert reg.claim(secret)["ok"] is False   # 秘钥也作废


def test_cancel_pending_only_pending(tmp_path):
    """cancel_pending 只撤 pending 壳,不误删已激活的正式公民。"""
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    reg.claim(secret)   # 激活
    assert reg.cancel_pending("", "cc") is False   # 已 active → cancel_pending 不动它
    assert reg.resolve_in("", "cc").status == STATUS_ACTIVE


def test_ticket_store_never_persists_plaintext_secret(tmp_path):
    """秘钥纪律:落盘的 ticket 文件里只有盐+摘要,绝无明文秘钥。"""
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    tid, sec = split_claim_secret(secret)
    raw = (tmp_path / "tickets.json").read_text(encoding="utf-8")
    # 明文 secret 段绝不在落盘文件里(只该有摘要 secret_hash)
    assert sec not in raw, "落盘 ticket 文件泄露了明文秘钥段!"
    assert secret not in raw, "落盘 ticket 文件泄露了完整明文秘钥!"
    # 但 secret_hash / salt 该在(校验用)
    assert "secret_hash" in raw and "salt" in raw


def test_ticket_persistence_survives_restart(tmp_path):
    """重启(新建 registry 从同一 store 加载)后,pending 壳仍能被认领。"""
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")
    # 模拟重启:新 registry 从同路径 store 加载
    reg2 = _reg(tmp_path)
    assert reg2.resolve_in("", "cc").status == STATUS_PENDING
    assert reg2.claim(secret)["ok"] is True
    assert reg2.resolve_in("", "cc").status == STATUS_ACTIVE


def test_claim_cannot_escalate_tier_via_self_report(tmp_path):
    """自提权防御:外部自报里塞 tier/domain 也改不了建壳侧所定(untrusted 不提权)。"""
    reg = _reg(tmp_path)
    reg.create_pending("cc", tier="guest")   # 建壳侧定 guest
    _, secret, _ = reg.create_pending("cc2", domain_id="", tier="guest")
    # 自报里塞 tier=scoped / domain=secret-domain —— claim 只认 runtime_kind/bin/version/caps,别的忽略
    res = reg.claim(secret, reported={"tier": "scoped", "domain_id": "secret-domain",
                                      "runtime_kind": "generic_cli"})
    assert res["ok"] is True
    c = reg.resolve_in("", "cc2")
    assert c.tier == "guest"        # 没被自报提权
    assert c.domain_id == ""        # 没被自报改域


# ============ ClaimTicket 单元 ============

def test_claim_persist_failure_does_not_lock_out(tmp_path):
    """半写防御:公民落盘失败时,claim 不作废秘钥、不假报成功 —— 用户能拿同一把码重试。"""
    reg = _reg(tmp_path)
    _, secret, _ = reg.create_pending("cc")

    # 让公民 store 落盘失败(_persist 返回 False),但 ticket store 正常
    class _BoomStore:
        def load_all(self):
            return []
        def save_all(self, records):
            raise OSError("disk full FAKE")
    reg._store = _BoomStore()

    res = reg.claim(secret)
    assert res["ok"] is False                      # 假报成功被拦
    assert reg.resolve_in("", "cc").status == STATUS_PENDING   # 内存激活已回滚
    # 秘钥没被作废 → 修好盘后同一把码仍能认领
    reg._store = ExternalCitizenStore(tmp_path / "citizens2.json")
    assert reg.claim(secret)["ok"] is True
    assert reg.resolve_in("", "cc").status == STATUS_ACTIVE


def test_expired_tickets_reaped_at_load(tmp_path):
    """台账跨重启不无限增长:startup 反收已过期票(真时钟)。"""
    reg = _reg(tmp_path)
    # 直接写一把很久以前就过期的票进 store(模拟历史遗留)
    ancient = ClaimTicket(ticket_id="old1", secret_hash="x", salt="y", citizen_id="gone",
                          issued_at=1.0, expires_at=2.0, used_at=0.0)
    tickets_path = tmp_path / "tickets.json"
    import json
    tickets_path.write_text(json.dumps([ancient.to_dict()]), encoding="utf-8")
    # 新 registry 从 store 加载 → startup 反收过期票
    reg2 = _reg(tmp_path)
    assert "old1" not in reg2._tickets
    # 落盘也不再含它
    assert "old1" not in tickets_path.read_text(encoding="utf-8")


def test_claim_ticket_verify_semantics():
    ticket, full = mint_claim_ticket("cc", "", ttl_s=600, now=1000.0)
    _tid, sec = split_claim_secret(full)
    assert ticket.verify(sec, now=1000.0) is True
    assert ticket.verify("FAKE-DO-NOT-LEAK-wrong", now=1000.0) is False   # 错 secret
    assert ticket.verify(sec, now=1000.0 + 601) is False                  # 过期
    used = ticket.__class__(**{**ticket.to_dict(), "used_at": 1000.0})
    assert used.verify(sec, now=1000.0) is False                          # 已用


# ============ ② HTTP 端点层 ============

def _app(reg):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.citizen_registry = reg
    return app


def test_http_create_pending_then_claim_then_active(tmp_path):
    reg = _reg(tmp_path)
    # 本机来源(loopback)→ 过来源门
    client = TestClient(_app(reg), client=("127.0.0.1", 50000))
    # 建壳发码
    r = client.post("/api/external/create_pending", json={"citizen_id": "cc"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["citizen"]["pending"] is True
    assert body["claim_url"].endswith("/api/external/claim")
    secret = body["claim_secret"]
    assert secret and "." in secret
    # 复制指令含连接器 + curl
    assert "karvyloop.external_runtime.connector" in body["connector_cmd"]
    assert body["curl_cmd"].startswith("curl -X POST")
    # 壳在列表里,pending
    lst = client.get("/api/external/citizens").json()["citizens"]
    assert any(c["citizen_id"] == "cc" and c["pending"] for c in lst)
    # 外部 runtime 回连认领
    rc = client.post("/api/external/claim", json={
        "secret": secret, "runtime_kind": "generic_cli", "version": "vFAKE"})
    assert rc.status_code == 200 and rc.json()["ok"] is True
    # 翻 active
    lst2 = client.get("/api/external/citizens").json()["citizens"]
    cc = [c for c in lst2 if c["citizen_id"] == "cc"][0]
    assert cc["pending"] is False and cc["status"] == "active"
    assert cc["self_reported"] is True
    # 重放认领 → 拒
    assert client.post("/api/external/claim", json={"secret": secret}).json()["ok"] is False


def test_http_claim_url_ignores_spoofed_public_host(tmp_path):
    """安全:伪造的公网 Host 头不得把认领回调 URL(连同明文秘钥)引到攻击者端点。

    只信本地/私网权威 Host;公网 Host 一律退回真实连接基址(testserver)。
    """
    reg = _reg(tmp_path)
    client = TestClient(_app(reg), client=("127.0.0.1", 50000))
    # 攻击者引导浏览器发一个 Host: evil.com 的请求
    r = client.post("/api/external/create_pending", json={"citizen_id": "cc"},
                    headers={"host": "evil.com"}).json()
    assert r["ok"] is True
    secret = r["claim_secret"]
    # 回调 URL / 复制指令绝不含攻击者域;秘钥没被引到 evil.com(退回服务器真实绑定地址)
    assert "evil.com" not in r["claim_url"]
    assert "evil.com" not in r["connector_cmd"]
    assert "evil.com" not in r["curl_cmd"]
    assert r["claim_url"].endswith("/api/external/claim")
    assert secret not in r["claim_url"]   # url 本身不含秘钥(秘钥只在 secret/命令字段)
    # 本地权威 Host(带端口)仍被如实反射(可达性)
    r2 = client.post("/api/external/create_pending", json={"citizen_id": "cc2"},
                     headers={"host": "192.168.1.50:8766"}).json()
    assert "192.168.1.50:8766" in r2["claim_url"]


def test_http_create_pending_rejects_public_origin(tmp_path):
    reg = _reg(tmp_path)
    client = TestClient(_app(reg), client=("8.8.8.8", 50000))   # 公网
    r = client.post("/api/external/create_pending", json={"citizen_id": "cc"})
    assert r.json()["ok"] is False
    assert reg.resolve_in("", "cc") is None   # 没建壳


def test_http_claim_rejects_public_origin(tmp_path):
    reg = _reg(tmp_path)
    # 先本机建壳
    inside = TestClient(_app(reg), client=("127.0.0.1", 50000))
    secret = inside.post("/api/external/create_pending", json={"citizen_id": "cc"}).json()["claim_secret"]
    # 公网拿泄露秘钥认领 → 来源门挡(纵深防御)
    outside = TestClient(_app(reg), client=("8.8.8.8", 50000))
    assert outside.post("/api/external/claim", json={"secret": secret}).json()["ok"] is False
    assert reg.resolve_in("", "cc").status == STATUS_PENDING   # 没被激活


def test_http_claim_secret_not_in_server_logs(tmp_path, caplog):
    reg = _reg(tmp_path)
    client = TestClient(_app(reg), client=("127.0.0.1", 50000))
    secret = client.post("/api/external/create_pending", json={"citizen_id": "cc"}).json()["claim_secret"]
    import logging
    with caplog.at_level(logging.DEBUG):
        # 触发一次坏 claim(错秘钥)→ 走 warning 路径,确认 secret 不进日志
        client.post("/api/external/claim", json={"secret": "badid.FAKE-DO-NOT-LEAK"})
        client.post("/api/external/claim", json={"secret": secret})   # 成功那次也不该 log secret
    _, sec = split_claim_secret(secret)
    for rec in caplog.records:
        assert sec not in rec.getMessage(), "认领秘钥进了服务端日志!"
        assert secret not in rec.getMessage()


def test_http_cancel_pending(tmp_path):
    reg = _reg(tmp_path)
    client = TestClient(_app(reg), client=("127.0.0.1", 50000))
    secret = client.post("/api/external/create_pending", json={"citizen_id": "cc"}).json()["claim_secret"]
    r = client.post("/api/external/cancel_pending", json={"citizen_id": "cc"})
    assert r.json()["ok"] is True
    assert reg.resolve_in("", "cc") is None
    # 取消后秘钥作废
    assert client.post("/api/external/claim", json={"secret": secret}).json()["ok"] is False


def test_http_no_registry_degrades(tmp_path):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.citizen_registry = None
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.post("/api/external/create_pending", json={"citizen_id": "cc"}).json()["ok"] is False
    assert client.post("/api/external/claim", json={"secret": "x.y"}).json()["ok"] is False


# ============ ③ 连接器脚本 ============

def test_connector_self_report_shape():
    rep = _connector._self_report("generic_cli", "/bin/x", "", ["code"])
    assert rep["runtime_kind"] == "generic_cli"
    assert rep["bin_path"] == "/bin/x"
    assert rep["capabilities"] == ["code"]
    assert rep["version"]   # 空 version 时退回环境事实(OS/py),非空


def test_connector_missing_secret_fails_loud(monkeypatch, capsys):
    monkeypatch.delenv("KARVYLOOP_CLAIM_SECRET", raising=False)
    rc = _connector.main(["--claim-url", "http://127.0.0.1:1/api/external/claim"])
    assert rc == 2   # 缺秘钥 → fail-loud
    err = capsys.readouterr().err
    assert "秘钥" in err


def test_connector_claims_via_real_endpoint(tmp_path):
    """连接器真往返:用 TestClient 的 URL 打真 claim 端点(通过 monkeypatch urlopen 转发)。"""
    reg = _reg(tmp_path)
    client = TestClient(_app(reg), client=("127.0.0.1", 50000))
    secret = client.post("/api/external/create_pending", json={"citizen_id": "cc"}).json()["claim_secret"]
    # connector.claim 用 urllib;直接调 claim() 但把 urlopen 换成打 TestClient
    import json as _json

    class _FakeResp:
        def __init__(self, data, status):
            self._data = data
            self.status = status
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        payload = _json.loads(req.data.decode("utf-8"))
        r = client.post("/api/external/claim", json=payload)
        return _FakeResp(r.content, r.status_code)

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        result = _connector.claim("http://testserver/api/external/claim", secret,
                                  runtime_kind="generic_cli", version="vFAKE")
    finally:
        urllib.request.urlopen = orig
    assert result["ok"] is True
    assert reg.resolve_in("", "cc").status == STATUS_ACTIVE


def test_connector_redacts_secret_from_argv():
    argv = ["prog", "--secret", "abc.FAKE-DO-NOT-LEAK", "--claim-url", "http://x"]
    _connector._redact_secret_from_argv(argv)
    assert argv[2] == "***"   # 秘钥被抹,别留在 ps 里
    assert "FAKE-DO-NOT-LEAK" not in " ".join(argv)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
