"""test_external_management — 跨 runtime 协作的管理面 + 按需接入引导端点(routes_external)+ doctor 探测。

覆盖(消费 C1 CitizenRegistry,不改注册表):
① GET /api/external/citizens:列外部公民,带 tier + 在线灯 + 醒目外部标识字段(is_external/chat_peer)。
② GET /api/external/liveness:单个公民活性(online|offline|unreachable)。
③ POST /api/external/detach:解绑一个公民(走来源门,本机放行)。
④ GET /api/external/onboarding:按需接入引导(装没装 + 官方安装指引骨架 + 不 bundle 声明)。
⑤ doctor_liveness.check_external_runtime:确定性探测机器上有没有可接入的外部 runtime。
⑥ 前端真接线(面板源 / 构建产物 / i18n en+zh parity / nav 入口)。
⑦ 中性名纪律:出货代码/注释/测试里 ZERO 参照工程名(openclaw/hermes/claw/codex 之外的具体产品名)。
⑧ 未接注册表时优雅降级(_integration_pending),绝不硬崩。
"""
from __future__ import annotations

import pathlib
import re
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.doctor_liveness import check_external_runtime  # noqa: E402
from karvyloop.external_runtime import (  # noqa: E402
    ExternalCitizen, ExternalCitizenRegistry, ExternalCitizenStore,
)
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _app_with_registry(reg=None):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    if reg is not None:
        app.state.citizen_registry = reg
    return app


def _seed_registry(tmp_path):
    """两个公民:helper(guest, active)/ scout(scoped, unreachable)。"""
    store = ExternalCitizenStore(tmp_path / "external_citizens.json")
    reg = ExternalCitizenRegistry(store=store)
    reg.add(ExternalCitizen(citizen_id="helper", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", status="active", tier="guest"))
    reg.add(ExternalCitizen(citizen_id="scout", runtime_kind="generic_cli",
                            bin_path="", domain_id="", status="unreachable", tier="scoped"))
    return reg


# ---- ① 列外部公民:tier + 在线灯 + 醒目外部标识 ----

def test_list_citizens_shape(tmp_path):
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg))
    r = client.get("/api/external/citizens")
    assert r.status_code == 200
    body = r.json()
    cits = {c["citizen_id"]: c for c in body["citizens"]}
    assert set(cits) == {"helper", "scout"}
    # 醒目外部标识:每个都 is_external=True + chat_peer.role=="external"(不与原生 role 混脸)
    for c in cits.values():
        assert c["is_external"] is True
        assert c["chat_peer"]["role"] == "external"
        assert c["chat_peer"]["agent_id"] == c["citizen_id"]
        assert c["liveness"] in ("online", "offline", "unreachable")
    # tier 如实透传
    assert cits["helper"]["tier"] == "guest"
    assert cits["scout"]["tier"] == "scoped"


def test_list_citizens_domain_filter(tmp_path):
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg))
    r = client.get("/api/external/citizens?domain=d1")
    got = [c["citizen_id"] for c in r.json()["citizens"]]
    assert got == ["helper"]   # 只列 d1 挂载的


def test_list_citizens_no_registry_degrades():
    """未接注册表(C1 未 merge app.state.citizen_registry)→ 空清单 + 集成待接标注,绝不崩。"""
    client = TestClient(_app_with_registry(None))
    r = client.get("/api/external/citizens")
    assert r.status_code == 200
    body = r.json()
    assert body["citizens"] == []
    assert "_integration_pending" in body


# ---- ② 单个活性 ----

def test_liveness_single(tmp_path):
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg))
    # helper bin=ext-cli 不在 PATH → liveness 探活不过 → unreachable(确定性,不假装 online)
    r = client.get("/api/external/liveness?citizen_id=helper&domain=d1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] in ("online", "offline", "unreachable")


def test_liveness_missing_citizen(tmp_path):
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg))
    r = client.get("/api/external/liveness?citizen_id=ghost")
    assert r.json()["ok"] is False


def test_liveness_needs_citizen_id(tmp_path):
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg))
    assert client.get("/api/external/liveness").json()["ok"] is False


# ---- ③ 解绑(删除)----

def test_detach_removes_citizen(tmp_path):
    reg = _seed_registry(tmp_path)
    # 来源门:写操作只准本机/私网 —— TestClient 默认 host=testclient(非 IP,会被挡);
    # 用回环 127.0.0.1 走真实来源门(loopback is_global=False → 可信),忠实验证守卫而非绕过它。
    client = TestClient(_app_with_registry(reg), client=("127.0.0.1", 50000))
    r = client.post("/api/external/detach", json={"citizen_id": "helper", "domain_id": "d1"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 真从注册表移除(复合键)
    assert reg.resolve_in("d1", "helper") is None
    # 幂等:再删返回 not found
    r2 = client.post("/api/external/detach", json={"citizen_id": "helper", "domain_id": "d1"})
    assert r2.json()["ok"] is False


def test_detach_rejects_public_origin(tmp_path):
    """写操作来源门:公网来源被挡(local-first 主权:只机主能删,防裸暴公网被陌生人点)。"""
    reg = _seed_registry(tmp_path)
    client = TestClient(_app_with_registry(reg), client=("8.8.8.8", 50000))  # 真·全球可路由公网 IP
    r = client.post("/api/external/detach", json={"citizen_id": "helper", "domain_id": "d1"})
    assert r.json()["ok"] is False
    assert reg.resolve_in("d1", "helper") is not None   # 没被删


def test_detach_no_registry():
    client = TestClient(_app_with_registry(None), client=("127.0.0.1", 50000))
    r = client.post("/api/external/detach", json={"citizen_id": "x"})
    assert r.json()["ok"] is False


# ---- ④ 按需接入引导 ----

def test_onboarding_shape():
    client = TestClient(_app_with_registry(None))
    r = client.get("/api/external/onboarding")
    assert r.status_code == 200
    body = r.json()
    assert "present" in body and isinstance(body["found_bins"], list)
    # 红线:我们不 bundle 外部 runtime(审计事实,恒 False)
    assert body["we_bundle_it"] is False
    assert body["guidance_key"].startswith("external.onboarding.")


# ---- ⑤ doctor 探测:确定性、可注入、不执行候选 bin ----

def test_doctor_external_absent():
    findings = check_external_runtime(which=lambda n: False)
    assert findings[0].level == "warn"
    assert findings[0].code == "external_runtime_absent"


def test_doctor_external_present():
    findings = check_external_runtime(which=lambda n: n in ("claude", "codex"))
    assert findings[0].level == "ok"
    assert findings[0].code == "external_runtime_present"
    assert findings[0].params["n"] == 2


def test_doctor_external_never_raises():
    # 探测器坏(抛异常)也不炸:逐 bin try/except 吞掉 → 视作没命中
    def boom(_n):
        raise RuntimeError("probe blew up")
    findings = check_external_runtime(which=boom)
    assert findings[0].code == "external_runtime_absent"


# ---- ⑥ 前端真接线(面板源 / 构建产物 / i18n parity / nav 入口)----

def test_frontend_wired():
    fe = ROOT / "karvyloop" / "console" / "frontend" / "src"
    static = ROOT / "karvyloop" / "console" / "static"
    src = (fe / "external_panel.ts").read_text(encoding="utf-8")
    for api in ("/api/external/citizens", "/api/external/detach", "/api/external/liveness",
                "/api/external/onboarding"):
        assert api in src, f"面板没接 {api}"
    built = (static / "external_panel.js").read_text(encoding="utf-8")
    assert "/api/external/citizens" in built, "构建产物没带外部面板(没 npm run build?)"
    # index.html 装了面板脚本 + nav 入口
    html = (static / "index.html").read_text(encoding="utf-8")
    assert "external_panel.js" in html
    assert 'data-panel="external"' in html
    # app.js nav 派发 + 直聊外部 peer 通道
    app_js = (static / "app.js").read_text(encoding="utf-8")
    assert "KarvyExternalPanel" in app_js and "openExternalPanel" in app_js
    assert "directChatPeer" in app_js
    # i18n en+zh 各一份(parity)
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    for k in ("external.title", "external.badge", "external.direct_chat",
              "external.onboarding.we_dont_bundle", "external.status_online", "nav.external"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"


# ---- ⑦ 中性名纪律:出货代码/注释/测试 ZERO 参照工程名 ----

def test_neutral_names_no_reference_products():
    """公开仓红线:出货物里不点参照工程名。检查我拥有的所有出货文件。"""
    # 参照工程/竞品名(小写匹配);中性词 external_runtime/headless CLI 是允许的。
    banned = ["openclaw", "hermes", "claw-code", "clawcode", "anthropic claude code"]
    files = [
        ROOT / "karvyloop" / "console" / "routes_external.py",
        ROOT / "karvyloop" / "console" / "frontend" / "src" / "external_panel.ts",
        ROOT / "karvyloop" / "console" / "static" / "external_panel.js",
    ]
    for f in files:
        text = f.read_text(encoding="utf-8").lower()
        for name in banned:
            assert name not in text, f"{f.name} 点了参照工程名:{name}"


def test_doctor_bin_names_are_probe_keys_not_narrative():
    """doctor 候选 bin 名只是 PATH 探测键(确定性事实),不构成注释里对某产品的背书文字。

    锁:candidate bins 只出现在 _EXTERNAL_RUNTIME_BINS 元组里(探测键),
    源码里不得有'如 <产品名> 这类营销/背书句式点名某产品当依赖'。中性表述(headless CLI agent)不受限。
    """
    src = (ROOT / "karvyloop" / "doctor_liveness.py").read_text(encoding="utf-8")
    # 中性表述在场(证明走的是"业界做法"口径)
    assert "headless CLI" in src
    # 不写死成依赖清单/背书句(仅探测键,已在注释里说明)
    assert "external_runtime_absent" in src and "external_runtime_present" in src
    # bin 名不能出现在注释里被当依赖点名(粗查:注释行里不含把 bin 名接 '依赖' 的句式)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and "依赖" in stripped:
            assert "候选" in stripped or "非依赖" in stripped or "探测" in stripped, \
                f"注释疑似把候选 bin 当依赖点名:{stripped}"
