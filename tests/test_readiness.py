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
