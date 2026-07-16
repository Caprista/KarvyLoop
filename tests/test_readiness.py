"""test_readiness — 无 Key 强制引导的就绪判断(网页+TUI 共用)。"""
from __future__ import annotations
import pathlib, sys, types
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.gateway.readiness import is_ready  # noqa: E402


def _reg(default_chat, providers):
    """造一个最小假 registry。providers: {name: api_key}。"""
    r = types.SimpleNamespace()
    r.default_chat = default_chat
    r.models = {default_chat: object()} if default_chat else {}
    r.provider_of = lambda ref: types.SimpleNamespace(api_key=providers.get(ref.split("/", 1)[0], ""))
    return r


def test_no_registry_not_ready():
    ok, why = is_ready(None)
    assert ok is False and why == "no_config"


def test_local_ollama_default_is_ready_without_key():
    ok, why = is_ready(_reg("ollama/qwen", {"ollama": "dummy"}))
    assert ok is True            # 本地默认不需真 key

def test_cloud_default_empty_key_not_ready():
    ok, why = is_ready(_reg("anthropic/claude", {"anthropic": ""}))
    assert ok is False and why == "no_key"     # 云端但 key 空(没配/被删/env 没设)

def test_cloud_default_placeholder_key_not_ready():
    ok, why = is_ready(_reg("anthropic/claude", {"anthropic": "changeme"}))
    assert ok is False and why == "no_key"

def test_cloud_default_real_key_is_ready():
    ok, why = is_ready(_reg("anthropic/claude", {"anthropic": "sk-real-xxxxx"}))
    assert ok is True

def test_default_model_not_in_registry_not_ready():
    r = _reg("anthropic/claude", {"anthropic": "sk-x"}); r.models = {}
    ok, why = is_ready(r)
    assert ok is False and why == "no_default_model"


def test_setup_status_endpoint_no_llm_not_forced():
    """显式 --no-llm:不强制引导(用户主动选只读)。"""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.no_llm = True
    r = TestClient(app).get("/api/setup_status").json()
    assert r["no_llm_mode"] is True and r["must_setup"] is False

def test_setup_status_endpoint_no_key_forces_setup():
    """非 no_llm 且无可用模型 → must_setup=True(强制引导)。"""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.no_llm = False   # 没 gateway/key → must_setup
    r = TestClient(app).get("/api/setup_status").json()
    assert r["ready"] is False and r["must_setup"] is True


# ---- CFG-05(内测):?live=1 —— 配置在 ≠ 能用,启动 gate 与首配"保存并验证"同一套真验 ----

def _live_app(gw):
    """带假 gateway 的 console app(配置级就绪:default_chat 在 + key 非占位)。"""
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.no_llm = False
    app.state.runtime_kwargs = {"gateway": gw}
    return app


def _fake_gw(complete_behavior):
    """假 gateway:配置级 is_ready 过(真 key 形态),complete 行为可注入。带调用计数。"""
    reg = types.SimpleNamespace()
    reg.default_chat = "anthropic/claude"
    reg.models = {"anthropic/claude": object()}
    reg.provider_of = lambda ref: types.SimpleNamespace(api_key="FAKE-DO-NOT-LEAK-key-1234")
    gw = types.SimpleNamespace(reg=reg)
    gw.calls = 0

    async def _complete(messages, tools, ref):
        gw.calls += 1
        async for ev in complete_behavior():
            yield ev
    gw.complete = _complete
    return gw


async def _ok_events():
    yield {"type": "text", "text": "pong"}


def test_setup_status_live_ok_passes_gate():
    """配置在 + 真调用通过 → live_ok=True(进主界面)。"""
    from fastapi.testclient import TestClient
    gw = _fake_gw(_ok_events)
    r = TestClient(_live_app(gw)).get("/api/setup_status?live=1").json()
    assert r["must_setup"] is False and r["live_checked"] is True and r["live_ok"] is True
    assert r["live_model"] == "anthropic/claude" and gw.calls == 1


def test_setup_status_live_bad_key_reports_class_and_scrubs():
    """key 被手改坏再重启的场景:配置级就绪但真调用 401 → live_ok=False + bad_key + 不泄 key。"""
    from fastapi.testclient import TestClient

    async def _bad():
        raise RuntimeError("401 unauthorized sk-LEAKME1234567890ABCDEFG")
        yield  # pragma: no cover
    gw = _fake_gw(_bad)
    r = TestClient(_live_app(gw)).get("/api/setup_status?live=1").json()
    assert r["must_setup"] is False                      # 配置级仍"就绪"(旧 gate 就是被这个骗过)
    assert r["live_checked"] is True and r["live_ok"] is False
    assert r["live_error_class"] == "bad_key"
    assert r["live_model"] == "anthropic/claude"         # 诚实原因:哪个模型
    assert "sk-LEAKME1234567890ABCDEFG" not in r["live_reason"] and "401" in r["live_reason"]


def test_setup_status_live_network_error_classified_unreachable():
    """网络类失败要与 key 坏区分开(前端据此给"离线继续"出口,不锁死离线用户)。"""
    from fastapi.testclient import TestClient

    async def _net():
        raise RuntimeError("ConnectError: connection refused")
        yield  # pragma: no cover
    gw = _fake_gw(_net)
    r = TestClient(_live_app(gw)).get("/api/setup_status?live=1").json()
    assert r["live_checked"] is True and r["live_ok"] is False
    assert r["live_error_class"] == "unreachable"


def test_setup_status_live_skipped_when_must_setup():
    """配置级就没就绪(本来就强制引导)→ 不发真请求(live_checked=False)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.no_llm = False
    r = TestClient(app).get("/api/setup_status?live=1").json()
    assert r["must_setup"] is True and r["live_checked"] is False


def test_setup_status_live_skipped_in_no_llm_mode():
    """显式 --no-llm(用户主动选只读)→ 不设门也不真调用。"""
    from fastapi.testclient import TestClient
    gw = _fake_gw(_ok_events)
    app = _live_app(gw)
    app.state.no_llm = True
    r = TestClient(app).get("/api/setup_status?live=1").json()
    assert r["must_setup"] is False and r["live_checked"] is False and gw.calls == 0


def test_setup_status_without_live_stays_config_level():
    """不带 live 的既有调用方零成本:绝不发真请求(gateway.complete 不被碰)。"""
    from fastapi.testclient import TestClient
    gw = _fake_gw(_ok_events)
    r = TestClient(_live_app(gw)).get("/api/setup_status").json()
    assert r["must_setup"] is False and r["live_checked"] is False and gw.calls == 0
