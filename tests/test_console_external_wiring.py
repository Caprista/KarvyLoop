"""test_console_external_wiring — #71 M1/M2 外部 runtime 执行面真接进 console(治"造了没接线")。

病根(critic):`build_console_app` 里 `app.state.citizen_registry`/`external_bridge_factory`/
`external_token_recorder` 默认 None(C1/M2 接线点)。虽然 cmd_console 事后设了 citizen_registry,
但**执行面 hook(external_bridge_factory)从没显式接** → app.state 上是 None(看着像没接线);
**且直接聊天 drive 路径从不 forward citizen_registry** → 小卡的 external_agent/attach/list/revoke
工具永远不挂 → M1 attach 在默认 console 里跑不了。本文件锁 console 真装配:

1. cmd_console 真装配(mock uvicorn.run 捕 app):默认 console(非 --no-llm)→
   app.state.citizen_registry 非 None + external_bridge_factory 接成内置 subprocess 桥工厂(非 None)。
2. /api/external/citizens 走真 registry(非 _integration_pending),外部管理面拿得到公民。
3. 直接聊天 drive 路径(routes._api_intent / ws)**真 forward** citizen_registry + 执行面 hook
   → 小卡人格下 external_agent/attach/list/revoke 工具真挂(M1 attach 端到端能走)。
4. 零回归:citizen_registry 构造失败(降级)→ 执行面 hook 保持 None,console 照旧起、管理面优雅降级。
"""
from __future__ import annotations

import pathlib
import sys
from argparse import Namespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402


def _client(app):
    """带 access token 的 TestClient(cmd_console 装配会设 access_token → 非 loopback 来源需带 token)。

    TestClient 的来源 host 是 "testclient"(非 loopback),故每次请求带 ?token=<真 token>。"""
    tok = getattr(app.state, "access_token", "") or ""
    c = TestClient(app)
    if tok:
        c.params = {"token": tok}   # 所有请求默认带 ?token=(query 优先,access.token_from_request)
    return c


def _run_cmd_console(monkeypatch, tmp_path, *, no_llm: bool = False):
    """走 cmd_console 真装配,返回捕获到的 app(照 test_console_spend_brake_wiring 先例)。

    mock resolve_runtime(假 gateway)+ uvicorn.run(捕 app 不真起服务)+ 隔离真 HOME
    + 关掉 LLM pump。默认(no_llm=False)= 用户真跑的默认 console 路径。"""
    import uvicorn

    import karvyloop.cli._runtime as _rt
    import karvyloop.cli.intent_pump as ip_mod
    import karvyloop.console.entry as entry_mod
    from karvyloop.runtime.main_loop import MainLoop

    # 隔离真 HOME(cmd_console 往 ~/.karvyloop 写账本/对话/域/原子/角色/外部公民)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(entry_mod, "_port_free", lambda *a, **k: True)
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: None)
    monkeypatch.setattr(ip_mod, "DEFAULT_TRACE_DB", tmp_path / ".karvyloop" / "trace_buffer.db")
    monkeypatch.setattr(ip_mod, "DEFAULT_HABIT_DB", tmp_path / ".karvyloop" / "habits.db")

    captured: list = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append(app))

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("# minimal\n", encoding="utf-8")

    class _FakeGW:
        def __init__(self):
            self.reg = None

    ml = MainLoop(skills_dir=tmp_path / "skills")
    resolved = _rt.ResolvedRuntime(
        config_path=cfg_path, main_loop=ml,
        runtime_kwargs={"gateway": _FakeGW(), "model_ref": "m",
                        "workspace_root": str(tmp_path)},
        skills_dir=tmp_path / "skills",
    )
    monkeypatch.setattr(_rt, "resolve_runtime", lambda **kw: resolved)

    args = Namespace(host="127.0.0.1", port=8766, config=str(cfg_path),
                     no_browser=True, no_llm=no_llm, lang=None, relay=None)
    rc = entry_mod.cmd_console(args)
    assert rc == 0 and len(captured) == 1
    return captured[0]


# ---------------------------------------------------------------- 1. 装配:外部执行面真接线
def test_cmd_console_wires_external_execution_face(monkeypatch, tmp_path):
    """默认 console:citizen_registry 非 None + external_bridge_factory 接成内置桥工厂(非 None)。

    这是 critic 点名的核心 —— app.py 默认 None,console 启动路径必须把它接活,而不是让 app.state 停在 None。"""
    from karvyloop.external_runtime import bridge_factory as _builtin_bf

    app = _run_cmd_console(monkeypatch, tmp_path)
    reg = getattr(app.state, "citizen_registry", None)
    assert reg is not None, "默认 console 没接 citizen_registry(外部管理面会返空/降级)"
    # 执行面 hook 真接活(不是隐式 None → 看着像没接线)
    assert getattr(app.state, "external_bridge_factory", None) is _builtin_bf, \
        "external_bridge_factory 没接成内置桥工厂(M2 执行面 hook 停在 None = 造了没接线)"
    # token_recorder:留 hook,默认 None(外部 usage 只落 provenance,gateway 记账不双计)—— 允许 None
    assert hasattr(app.state, "external_token_recorder")


# ---------------------------------------------------------------- 2. 管理面走真 registry
def test_external_citizens_endpoint_returns_real_data(monkeypatch, tmp_path):
    """接活后:/api/external/citizens 走真 registry(非 _integration_pending),管理面拿得到公民。"""
    from karvyloop.external_runtime import ExternalCitizen

    app = _run_cmd_console(monkeypatch, tmp_path)
    # 往 console 装配好的**同一个** registry 里加一个公民(模拟已接入)
    app.state.citizen_registry.add(ExternalCitizen(
        citizen_id="helper", runtime_kind="raw_text_sidecar", bin_path="ext-cli",
        domain_id="d1", status="active", tier="guest"))

    client = _client(app)
    r = client.get("/api/external/citizens")
    assert r.status_code == 200
    body = r.json()
    # 真 registry(有 .list(domain=)/.detach/.liveness 契约面)→ 不落回退面
    assert "_integration_pending" not in body, \
        "默认 console 管理面仍走回退面(citizen_registry 没接活)"
    ids = {c["citizen_id"] for c in body["citizens"]}
    assert "helper" in ids, "管理面没拿到已接入的公民(接线断)"


# ---------------------------------------------------------------- 3. drive 路径 forward citizen_registry
def test_direct_chat_drive_forwards_citizen_registry(monkeypatch, tmp_path):
    """直接聊天(routes._api_intent)真把 citizen_registry + 执行面 hook forward 给 drive_in_tui。

    没 forward = 小卡的 external_agent/attach/list/revoke 工具永远不挂 → M1 attach 在默认 console 跑不了。
    这里 monkeypatch drive_in_tui 捕获它收到的 kwargs,断言接线穿透(不真跑 LLM)。"""
    import asyncio

    from karvyloop.console import routes as routes_mod
    from karvyloop.external_runtime import bridge_factory as _builtin_bf
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    app = _run_cmd_console(monkeypatch, tmp_path)

    captured_kwargs: dict = {}

    async def _fake_drive(intent, ml, **kw):
        captured_kwargs.update(kw)
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok",
                            skill_name="", fast_brain_hit=False, crystallized=False,
                            error="")

    monkeypatch.setattr(routes_mod, "drive_in_tui", _fake_drive)

    client = _client(app)
    r = client.post("/api/intent", json={"intent": "hi"})
    assert r.status_code == 200

    # 接线穿透:citizen_registry 与 console 装配的同一个;执行面 hook 也 forward(内置桥工厂)。
    assert captured_kwargs.get("citizen_registry") is app.state.citizen_registry, \
        "drive 路径没 forward citizen_registry → 小卡 external_agent/attach 工具永不挂(M1 attach 跑不了)"
    assert captured_kwargs.get("external_bridge_factory") is _builtin_bf
    # token_recorder 也穿透(默认 None,允许)
    assert "external_token_recorder" in captured_kwargs


# ---------------------------------------------------------------- 3b. 工具真挂(drive_in_tui 端到端 → forge 工具集)
def test_karvy_persona_mounts_external_tools_end_to_end(tmp_path):
    """端到端:小卡人格 + citizen_registry 接活 → drive_in_tui 真把 external_agent/attach/list/revoke
    并进 forge 的 mcp_tools。patch forge_slow_brain_factory 捕获它收到的工具集(不跑 LLM),
    证明 M1 attach 能力在默认 console 接线下真出现在 agent 工具集里(不是只 forward 到函数边界)。"""
    import asyncio

    from karvyloop.external_runtime import (
        ClaimTicketStore, ExternalCitizenRegistry, ExternalCitizenStore,
        bridge_factory as _builtin_bf,
    )
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench import main_loop_bridge as mlb
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    reg = ExternalCitizenRegistry(
        store=ExternalCitizenStore(tmp_path / "external_citizens.json"),
        ticket_store=ClaimTicketStore(tmp_path / "external_claim_tickets.json"))

    class _KarvyPersona:
        karvy_self = True

        def __getattr__(self, k):  # 其余 persona 属性取空(covers_domain_governance 等)
            return ""

    class _FakeML:
        scope = ""

        def drive(self, intent, **kw):
            return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok", skill_name="",
                                fast_brain_hit=False, crystallized=False, error="")

        def background_review(self):
            pass

    captured: dict = {}

    def _fake_forge(**kw):
        captured.update(kw)
        return lambda *a, **k: None

    async def _go():
        import unittest.mock as m
        with m.patch.object(mlb, "forge_slow_brain_factory", _fake_forge):
            return await mlb.drive_in_tui(
                "接入一个外部 runtime", _FakeML(), token=None, sandbox=None, gateway=None,
                workspace_root=str(tmp_path), persona=_KarvyPersona(),
                citizen_registry=reg, external_bridge_factory=_builtin_bf,
                external_token_recorder=None)

    asyncio.run(_go())
    mcp = captured.get("mcp_tools") or {}
    names = set(mcp) if isinstance(mcp, dict) else set()
    want = {"external_agent", "attach_external_agent",
            "list_external_agents", "revoke_external_agent"}
    assert want <= names, f"M1/M2 外部工具没进 forge 工具集(缺 {want - names})"


# ---------------------------------------------------------------- 4. 零回归:registry 构造失败 → 执行面不接、优雅降级
def test_registry_construction_failure_degrades_gracefully(monkeypatch, tmp_path):
    """citizen_registry 构造失败 → external_bridge_factory 保持 None(不硬开)、console 照旧起、管理面降级。

    诚实条件激活:没 registry 就没执行面 hook(接了也空转);管理面返 _integration_pending 但不崩。"""
    import karvyloop.external_runtime as ext_mod

    # 让 ExternalCitizenRegistry 构造抛错(模拟落盘/依赖异常)
    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("boom (test)")

    monkeypatch.setattr(ext_mod, "ExternalCitizenRegistry", _Boom)

    app = _run_cmd_console(monkeypatch, tmp_path)
    # 降级:registry None → 执行面 hook 不接(留 app.py 的 None 默认)
    assert getattr(app.state, "citizen_registry", "sentinel") is None, \
        "构造失败却没降级为 None"
    assert getattr(app.state, "external_bridge_factory", None) is None, \
        "registry 没就绪却接了执行面 hook(不该无脑强开)"
    # 管理面优雅降级(不崩):返空 + _integration_pending
    client = _client(app)
    r = client.get("/api/external/citizens")
    assert r.status_code == 200
    body = r.json()
    assert body["citizens"] == [] and "_integration_pending" in body
