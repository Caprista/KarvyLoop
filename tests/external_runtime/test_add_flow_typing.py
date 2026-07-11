"""test_add_flow_typing — fix#1:外部 runtime「添加」流定型(runtime_kind + agent 解析)。

病根:添加流只传 citizen_id、不传 runtime_kind → 建的是没定型的空壳(取不到配方、驱动不了),
多 runtime 时也无法区分接哪个。本套验证壳定型后能真取到配方、agent_id 进 argv、detect 端点形态、
连接器预填 --runtime-kind/--agent-id、以及类型未选时的兜底。

覆盖:
① 注册表/HTTP:create_pending 带 runtime_kind → 壳 recipe() 取到配方(能驱动);带 agent_id →
   盖进 capability_card.configured_agent_id → 进 argv 的 {agent_id} 槽。
② detect 端点:只返 {runtime_kind, bin} 且只探有 probe_bins 的配方(中性名、不硬编码产品名)。
③ 类型未选(runtime_kind 空)兜底:壳建成但 recipe()=None(诚实"待定型",不硬崩)。
④ 连接器:--runtime-kind / --agent-id 预填能解析并进自报载荷(untrusted,不提权)。

**秘钥 fixture 纪律**:出现的认领秘钥明文一律带 FAKE / DO-NOT-LEAK 字样。
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
    STATUS_PENDING,
    ClaimTicketStore,
    ExternalCitizenRegistry,
    ExternalCitizenStore,
)
from karvyloop.external_runtime import connector as _connector  # noqa: E402
from karvyloop.external_runtime.bridge import _build_argv  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _reg(tmp_path):
    return ExternalCitizenRegistry(
        store=ExternalCitizenStore(tmp_path / "citizens.json"),
        ticket_store=ClaimTicketStore(tmp_path / "tickets.json"))


def _app(reg):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.citizen_registry = reg
    return app


def _client(reg):
    return TestClient(_app(reg), client=("127.0.0.1", 50000))


# ============ ① 定型:runtime_kind 让壳取到配方 ============

def test_create_pending_with_runtime_kind_shell_resolves_recipe(tmp_path):
    """壳定型(runtime_kind)后 recipe() 取到配方 —— 能真驱动(不再是空类型壳)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    r = client.post("/api/external/create_pending",
                    json={"citizen_id": "cc", "runtime_kind": "single_json_cli"})
    assert r.status_code == 200 and r.json()["ok"] is True
    shell = reg.resolve_in("", "cc")
    assert shell is not None and shell.status == STATUS_PENDING
    assert shell.runtime_kind == "single_json_cli"
    recipe = shell.recipe()
    assert recipe is not None, "定型壳应能取到配方(否则驱动不了)"
    assert recipe.runtime_kind == "single_json_cli"


def test_empty_runtime_kind_is_undtyped_shell_recipe_none(tmp_path):
    """类型未选(runtime_kind 空)兜底:壳建成但 recipe()=None(诚实'待定型',不硬崩)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    r = client.post("/api/external/create_pending", json={"citizen_id": "cc"})
    assert r.status_code == 200 and r.json()["ok"] is True
    shell = reg.resolve_in("", "cc")
    assert shell is not None
    assert shell.runtime_kind == ""
    assert shell.recipe() is None, "空类型壳取不到配方(前端应逼选类型)"


# ============ ② 多 agent:agent_id 盖进壳 + 进 argv ============

def test_agent_id_stamped_into_capability_card(tmp_path):
    """single_json 型 + 填 agent_id → 盖进 capability_card.configured_agent_id(持久)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    r = client.post("/api/external/create_pending",
                    json={"citizen_id": "cc", "runtime_kind": "single_json_cli",
                          "agent_id": "worker-3"})
    assert r.json()["ok"] is True
    shell = reg.resolve_in("", "cc")
    assert shell.capability_card.get("configured_agent_id") == "worker-3"
    # 重启后仍在(持久)
    reg2 = _reg(tmp_path)
    assert reg2.resolve_in("", "cc").capability_card.get("configured_agent_id") == "worker-3"


def test_agent_id_reaches_argv_via_recipe(tmp_path):
    """壳定型 single_json + agent_id → 配方的 {agent_id} 槽被填成选定的 agent(进 argv)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    client.post("/api/external/create_pending",
                json={"citizen_id": "cc", "runtime_kind": "single_json_cli",
                      "agent_id": "worker-3"})
    shell = reg.resolve_in("", "cc")
    recipe = shell.recipe()
    aid = shell.capability_card.get("configured_agent_id", "main")
    argv = _build_argv(recipe, prompt="hi", session_key="s1", agent_id=aid)
    # single_json argv 形态:... --agent {agent_id} ... —— agent_id 应真进 argv
    assert "--agent" in argv
    assert argv[argv.index("--agent") + 1] == "worker-3", f"agent_id 没进 argv: {argv}"
    # 未选 agent 时(空)→ 退回 main(默认单 agent)
    argv_default = _build_argv(recipe, prompt="hi", agent_id="main")
    assert argv_default[argv_default.index("--agent") + 1] == "main"


def test_no_agent_id_leaves_card_clean(tmp_path):
    """不填 agent_id → 不往 capability_card 塞 configured_agent_id(零污染)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    client.post("/api/external/create_pending",
                json={"citizen_id": "cc", "runtime_kind": "single_json_cli"})
    shell = reg.resolve_in("", "cc")
    assert "configured_agent_id" not in (shell.capability_card or {})


# ============ ③ detect 端点形态 ============

def test_detect_endpoint_shape(tmp_path, monkeypatch):
    """detect 只返 {runtime_kind, bin};只探有 probe_bins 的配方(有真 bin 名的)。"""
    reg = _reg(tmp_path)
    # 让 generic_cli 的 probe_bin("claude")探到,验证形态
    from karvyloop.console import routes_external as rx

    def _fake_detect(which=None):
        return [{"runtime_kind": "generic_cli", "bin": "claude"}]

    monkeypatch.setattr(rx, "_detect_local_runtimes", _fake_detect)
    client = _client(reg)
    r = client.get("/api/external/detect")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 1
    assert body["detected"] == [{"runtime_kind": "generic_cli", "bin": "claude"}]
    assert body["we_bundle_it"] is False   # 探到≠我们分发它


def test_detect_only_probes_recipes_with_probe_bins():
    """detect 只探配置了 probe_bins 的配方(shape-only 配方无 bin 名 → 不探,不画饼)。"""
    from karvyloop.console.routes_external import _detect_local_runtimes
    # which 恒 True(假装每个候选都装了)—— 只有有 probe_bins 的 kind 会出现
    got = _detect_local_runtimes(which=lambda name: True)
    kinds = {d["runtime_kind"] for d in got}
    # generic_cli 有 probe_bins=("claude",) → 会探到;single_json/raw_text 无 probe_bins → 不探
    assert "generic_cli" in kinds
    assert "single_json_cli" not in kinds
    assert "raw_text_sidecar" not in kinds
    # 每项都是 {runtime_kind, bin} 形态
    for d in got:
        assert set(d.keys()) == {"runtime_kind", "bin"}


def test_detect_nothing_installed_empty(tmp_path):
    """本机啥都没装(which 恒 False)→ detected 空,不影响主流程(纯形态自选)。"""
    from karvyloop.console.routes_external import _detect_local_runtimes
    assert _detect_local_runtimes(which=lambda name: False) == []


# ============ ④ 连接器预填 --runtime-kind / --agent-id ============

def test_connector_cmd_prefilled_with_runtime_kind_and_agent(tmp_path):
    """create_pending 返回的 connector_cmd 预填 --runtime-kind(+ single_json 时 --agent-id)。"""
    reg = _reg(tmp_path)
    client = _client(reg)
    body = client.post("/api/external/create_pending",
                       json={"citizen_id": "cc", "runtime_kind": "single_json_cli",
                             "agent_id": "worker-3"}).json()
    cmd = body["connector_cmd"]
    assert "--runtime-kind \"single_json_cli\"" in cmd
    assert "--agent-id \"worker-3\"" in cmd
    # 无 agent 型不预填 --agent-id
    body2 = client.post("/api/external/create_pending",
                        json={"citizen_id": "cc2", "runtime_kind": "raw_text_sidecar"}).json()
    cmd2 = body2["connector_cmd"]
    assert "--runtime-kind \"raw_text_sidecar\"" in cmd2
    assert "--agent-id" not in cmd2


def test_connector_parses_agent_id_flag_into_self_report():
    """连接器 --agent-id 解析进自报载荷(untrusted:后端登记不提权,建壳侧选定为准)。"""
    rep = _connector._self_report("single_json_cli", "/bin/x", "vFAKE", ["code"], "worker-3")
    assert rep["runtime_kind"] == "single_json_cli"
    assert rep["agent_id"] == "worker-3"
    # 空 agent_id 不塞 agent_id 字段(零污染)
    rep2 = _connector._self_report("generic_cli", "", "", [], "")
    assert "agent_id" not in rep2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
