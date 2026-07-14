"""test_model_onboarding — 引导式 BYO-key onboarding 后端:provider 预设 + 实时校验(脱敏)。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.routes import _scrub_secret  # noqa: E402
from karvyloop.gateway.presets import PROVIDER_PRESETS  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _app():
    return build_console_app(workbench=WorkbenchObserver(), main_loop=None)


# ---- presets ----
def test_presets_have_required_fields():
    for p in PROVIDER_PRESETS:
        for k in ("id", "name", "base_url", "auth_header", "api", "model_id"):
            assert p.get(k), f"{p.get('id')} 缺 {k}"
    ids = {p["id"] for p in PROVIDER_PRESETS}
    assert "anthropic" in ids
    assert any(p.get("is_local") for p in PROVIDER_PRESETS)   # 至少一个本地选项


def test_presets_endpoint():
    r = TestClient(_app()).get("/api/providers/presets").json()
    assert len(r["presets"]) == len(PROVIDER_PRESETS)
    anth = next(p for p in r["presets"] if p["id"] == "anthropic")
    assert anth["get_key_url"].startswith("https://")


# ---- 脱敏(CLAUDE.md:绝不外泄 key)----
def test_scrub_secret_redacts_keys_keeps_signal():
    s = _scrub_secret("RuntimeError: 401 unauthorized for key sk-ABCDEFGH1234567890XYZ")
    assert "401" in s
    assert "sk-ABCDEFGH1234567890XYZ" not in s and "sk-***" in s
    s2 = _scrub_secret("Authorization: Bearer abcdef1234567890abcdef1234567890")
    assert "abcdef1234567890abcdef1234567890" not in s2


# ---- validate ----
def test_validate_no_gateway():
    r = TestClient(_app()).post("/api/model/validate").json()
    assert r["ok"] is False and r["reason"] == "no_gateway"


def test_validate_fresh_process_builds_transient_gateway(tmp_path, monkeypatch):
    """fresh 进程(无 gateway)+ 已有 config → validate 用临时 gateway 真验,不再回 no_gateway。

    Hardy 实拍拍死的不诚实面:首配(最需要验证的场景)以前反而跳过验证。"""
    import textwrap

    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent("""
    models:
      providers:
        anthropic:
          base_url: https://api.anthropic.com
          api_key: FAKE-DO-NOT-LEAK-abc123
          models:
            - id: anthropic/claude
              name: Claude
              api: anthropic-messages
              context_window: 200000
              max_tokens: 8192
    agents:
      defaults:
        model: anthropic/claude
    """), encoding="utf-8")

    class _FakeGW:
        def __init__(self, reg):
            self.reg = reg

        async def complete(self, messages, tools, ref):
            yield {"type": "text", "text": "pong"}

    import karvyloop.gateway as gwmod
    monkeypatch.setattr(gwmod, "GatewayClient", _FakeGW)
    app = _app()
    app.state.config_path = str(cfg)
    r = TestClient(app).post("/api/model/validate").json()
    assert r["ok"] is True and r["model"] == "anthropic/claude"


def test_validate_success_with_fake_gateway():
    app = _app()

    class _Reg:
        default_chat = "anthropic/claude"

    class _GW:
        reg = _Reg()

        async def complete(self, messages, tools, ref):
            yield {"type": "text", "text": "pong"}

    app.state.runtime_kwargs = {"gateway": _GW()}
    r = TestClient(app).post("/api/model/validate").json()
    assert r["ok"] is True and r["model"] == "anthropic/claude"


def test_validate_failure_scrubs_key():
    app = _app()

    class _Reg:
        default_chat = "x/y"

    class _GW:
        reg = _Reg()

        async def complete(self, messages, tools, ref):
            raise RuntimeError("401 bad key sk-LEAKME1234567890ABCDEFG")
            yield  # pragma: no cover

    app.state.runtime_kwargs = {"gateway": _GW()}
    r = TestClient(app).post("/api/model/validate").json()
    assert r["ok"] is False and "401" in r["reason"]
    assert "sk-LEAKME1234567890ABCDEFG" not in r["reason"]
