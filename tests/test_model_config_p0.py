"""test_model_config_p0 — 内测 P0:Kimi 三面孔 + CFG-04 stub api 收口 + init 自定义档 + CFG-02 诚实面。

真实内测用户报的两个 P0(测试矩阵):
① "模型配置里 Kimi 跑不通" —— Kimi 有三张面孔(Global=moonshot.ai / CN 聊天=moonshot.cn /
   For Coding=api.kimi.com/coding/v1+UA 门,key 前缀 sk-kimi-),此前 preset 只有 Global,
   且引导保存把 preset 的 extra_headers 静默丢掉。
② CFG-04:自定义模型 api 选中 stub(openai-responses 等)→ 配置"成功保存",聊天时才
   NotImplementedError。修三层:下拉不给 stub / 写入+验证阶段 fail-loud 人话 / stub 报错带行动指引。
"""
from __future__ import annotations

import io
import pathlib
import sys
import textwrap
from types import SimpleNamespace

import httpx
import pytest
import respx
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from karvyloop import i18n  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.gateway import config_models as cm  # noqa: E402
from karvyloop.gateway.presets import PROVIDER_PRESETS, kimi_key_guidance  # noqa: E402
from karvyloop.gateway.providers import default_adapters  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture(autouse=True)
def _en_locale():
    """消息内容断言按 en 锁定(默认 locale);测完复位走 env/默认。"""
    i18n.set_locale("en")
    yield
    i18n.set_locale(None)


CFG = textwrap.dedent("""
models:
  providers:
    anthropic:
      base_url: https://api.anthropic.com
      api_key: sk-ant-FAKE-DO-NOT-LEAK-12345
      models:
        - id: anthropic/claude
          name: Claude
          api: anthropic-messages
          context_window: 200000
          max_tokens: 8192
agents:
  defaults:
    model: anthropic/claude
""")


def _w(tmp):
    p = tmp / "config.yaml"
    p.write_text(CFG, encoding="utf-8")
    return p


def _app(cfg_path=None):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    if cfg_path is not None:
        app.state.config_path = str(cfg_path)
    return app


# ============ ① Kimi 三面孔:preset 形态 ============

def _preset(pid: str) -> dict:
    return next(p for p in PROVIDER_PRESETS if p["id"] == pid)


def test_kimi_three_faces_in_presets():
    """Global(moonshot.ai)/ CN 聊天(moonshot.cn)/ For Coding(api.kimi.com+UA)三张面孔都在。"""
    g, cn, coding = _preset("kimi"), _preset("kimi-cn"), _preset("kimi-coding")
    assert "api.moonshot.ai" in g["base_url"]
    assert "api.moonshot.cn" in cn["base_url"]          # 与 llm/profiles/kimi.py 已验通端点一致
    assert coding["base_url"] == "https://api.kimi.com/coding/v1"
    for p in (g, cn, coding):
        assert p["api"] == "openai-completions"
        assert p["auth_header"] == "Authorization"       # Bearer 系(kimi.py 已验通)
        assert p["model_id"].split("/", 1)[0] == p["id"]  # 块 key = model 前缀(provider_of 契约)


def test_kimi_coding_preset_has_ua_gate_and_no_auth_header_leak():
    """For Coding 端点有 UA 白名单门 → preset 必须带 User-Agent;extra_headers 绝不带鉴权头。"""
    coding = _preset("kimi-coding")
    eh = coding.get("extra_headers") or {}
    assert eh.get("User-Agent"), "For Coding preset 必须带 UA(白名单门)"
    assert not any(k.lower() in ("authorization", "x-api-key") for k in eh)


# ============ ① Kimi key 前缀识别(sk-kimi- = For Coding 专用) ============

def test_kimi_key_guidance_prefix_detection():
    fake_coding_key = "sk-kimi-FAKE-DO-NOT-LEAK-123"
    # sk-kimi- key 配在 moonshot 聊天端点 → 诚实提示(指向 coding 端点/正确取 key 处)
    hint = kimi_key_guidance(fake_coding_key, "https://api.moonshot.ai/v1")
    assert "api.kimi.com" in hint and "moonshot" in hint
    # 已经配在 coding 端点 → 不啰嗦
    assert kimi_key_guidance(fake_coding_key, "https://api.kimi.com/coding/v1") == ""
    # 普通 key → 无提示;空/遮罩串 → 无提示
    assert kimi_key_guidance("sk-FAKE-DO-NOT-LEAK-normal", "https://api.moonshot.ai/v1") == ""
    assert kimi_key_guidance("", "https://api.moonshot.ai/v1") == ""
    assert kimi_key_guidance("****1234", "https://api.moonshot.ai/v1") == ""


def test_wizard_validate_api_key_rejects_coding_key_on_moonshot():
    """向导侧:kimi/moonshot 粘 sk-kimi- key → 当场拦 + 指路(写出去必 401,不坑用户)。"""
    from karvyloop.cli.wizard import validate_api_key
    for prov in ("kimi", "moonshot"):
        ok, err = validate_api_key(prov, "sk-kimi-FAKE-DO-NOT-LEAK-123")
        assert not ok, f"{prov} 应拦 sk-kimi- key"
        assert "api.kimi.com" in err                     # 人话:告诉他这 key 归哪


def test_model_save_route_returns_kimi_hint(tmp_path):
    """console 保存侧:sk-kimi- key + moonshot 端点 → 不拦保存,但响应带诚实 hint。"""
    p = _w(tmp_path)
    c = TestClient(_app(p))
    r = c.post("/api/model/save", json={
        "provider": "kimi", "model_id": "kimi/kimi-k2-0711-preview",
        "api": "openai-completions", "base_url": "https://api.moonshot.ai/v1",
        "api_key": "sk-kimi-FAKE-DO-NOT-LEAK-123", "auth_header": "Authorization"}).json()
    assert r["ok"] is True
    assert "api.kimi.com" in r.get("hint", "")
    # 普通 key → 无 hint 字段
    r2 = c.post("/api/model/save", json={
        "provider": "kimi", "model_id": "kimi/kimi-k2-0711-preview",
        "api": "openai-completions", "base_url": "https://api.moonshot.ai/v1",
        "api_key": "sk-FAKE-DO-NOT-LEAK-normal-1", "auth_header": "Authorization"}).json()
    assert r2["ok"] is True and "hint" not in r2


# ============ ① extra_headers 全链路(preset → 路由 → config.yaml → adapter) ============

def test_model_save_route_persists_extra_headers_and_strips_auth(tmp_path):
    """路由层此前没 extra_headers 字段 → preset 的 UA 门被静默丢掉。现在:落盘 + 剥鉴权头。"""
    p = _w(tmp_path)
    c = TestClient(_app(p))
    r = c.post("/api/model/save", json={
        "provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
        "api": "openai-completions", "base_url": "https://api.kimi.com/coding/v1",
        "api_key": "sk-kimi-FAKE-DO-NOT-LEAK-123", "auth_header": "Authorization",
        "extra_headers": {"User-Agent": "KarvyLoop-Forge/0.1",
                          "Authorization": "Bearer SHOULD-BE-STRIPPED"}}).json()
    assert r["ok"] is True
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    eh = cfg["models"]["providers"]["kimi-coding"]["extra_headers"]
    assert eh == {"User-Agent": "KarvyLoop-Forge/0.1"}   # UA 在,鉴权头被剥(密钥唯一来源 api_key)


def test_model_save_without_extra_headers_keeps_existing(tmp_path):
    """编辑请求不带 extra_headers(None)→ 保留已有配置,不静默清空 UA 门。"""
    p = _w(tmp_path)
    ok, _ = cm.upsert_model({"provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
                             "api": "openai-completions", "base_url": "https://api.kimi.com/coding/v1",
                             "api_key": "sk-kimi-FAKE-DO-NOT-LEAK-123",
                             "extra_headers": {"User-Agent": "UA-1"}}, p)
    assert ok
    c = TestClient(_app(p))
    r = c.post("/api/model/save", json={
        "provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
        "model_name": "renamed", "api": "openai-completions",
        "base_url": "https://api.kimi.com/coding/v1", "api_key": ""}).json()
    assert r["ok"] is True
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert cfg["models"]["providers"]["kimi-coding"]["extra_headers"] == {"User-Agent": "UA-1"}


def _sse(*chunks: str) -> str:
    return "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.mark.asyncio
@respx.mock
async def test_custom_base_url_request_shape_auth_ua_and_ark_path():
    """Q2 request-shape(mock,无真调):自定义 base_url(火山 Ark 形态 /api/v3)+ extra_headers →
    Authorization: Bearer / UA / URL(不再错拼 /api/v3/v1/...)全落对。"""
    from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter
    from karvyloop.schemas import ModelDefinition, ProviderConfig

    fake_key = "sk-cp-FAKE-DO-NOT-LEAK-777"
    captured = {}

    def _resp(req):
        captured["auth"] = req.headers.get("authorization")
        captured["ua"] = req.headers.get("user-agent")
        captured["url"] = str(req.url)
        import json as _j
        captured["body"] = _j.loads(req.content)
        return httpx.Response(200, text=_sse(
            '{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}'))

    respx.post("https://ark.example.com/api/v3/chat/completions").mock(side_effect=_resp)
    model = ModelDefinition(id="custom/my-endpoint-model", name="m", api="openai-completions",
                            context_window=0, max_tokens=512)
    prov = ProviderConfig(name="custom", base_url="https://ark.example.com/api/v3",
                          api_key=fake_key, auth="api-key", auth_header="Authorization",
                          extra_headers={"User-Agent": "KarvyLoop-Test/0.1"}, models=[])
    evs = [ev async for ev in OpenAICompletionsAdapter().complete(
        [{"role": "user", "content": "ping"}], [], model, prov)]
    assert captured["auth"] == f"Bearer {fake_key}"
    assert captured["ua"] == "KarvyLoop-Test/0.1"
    # Ark 形态 base(/api/v3)→ 只补 /chat/completions,不再错拼 /api/v3/v1/chat/completions
    assert captured["url"] == "https://ark.example.com/api/v3/chat/completions"
    assert captured["body"]["model"] == "my-endpoint-model"   # 引用键前缀剥掉,发端点认的裸名
    assert not any(type(e).__name__ == "ErrorEvent" for e in evs)


@pytest.mark.asyncio
@respx.mock
async def test_v1_base_url_path_heal_unchanged():
    """回归锁:/v1 结尾的 base(openai/deepseek/moonshot)路径自愈行为不变。"""
    from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter
    from karvyloop.schemas import ModelDefinition, ProviderConfig

    seen = {}

    def _resp(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, text=_sse('{"choices":[{"delta":{},"finish_reason":"stop"}]}'))

    respx.post("https://api.moonshot.cn/v1/chat/completions").mock(side_effect=_resp)
    model = ModelDefinition(id="kimi-cn/kimi-k2-0711-preview", name="m", api="openai-completions",
                            context_window=0, max_tokens=64)
    prov = ProviderConfig(name="kimi-cn", base_url="https://api.moonshot.cn/v1",
                          api_key="sk-FAKE-DO-NOT-LEAK", auth="api-key",
                          auth_header="Authorization", models=[])
    async for _ in OpenAICompletionsAdapter().complete([{"role": "user", "content": "hi"}],
                                                       [], model, prov):
        pass
    assert seen["url"] == "https://api.moonshot.cn/v1/chat/completions"


# ============ ② CFG-04(a):下拉/写入不给 stub ============

def test_list_models_valid_apis_only_implemented(tmp_path):
    d = cm.list_models(_w(tmp_path))
    assert d["valid_apis"] == ["anthropic-messages", "openai-completions"]
    assert set(d["all_apis"]) == set(cm.VALID_APIS)      # 完整 schema 集合仍可查(诊断用)


def test_upsert_chat_model_on_stub_api_rejected(tmp_path):
    """chat 模型落 stub api = 存一个每次聊天必炸的配置 → 写入即拦 + 人话指路。"""
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    for stub in ("openai-responses", "google-generative-ai", "ollama", "bedrock-converse"):
        ok, reason = cm.upsert_model({"provider": "x", "model_id": "x/y", "api": stub,
                                      "base_url": "https://x.example.com",
                                      "api_key": "sk-FAKE-DO-NOT-LEAK"}, p)
        assert not ok, f"stub api {stub} 不该存成 chat 模型"
        assert "openai-completions" in reason            # 行动指引:该选什么
    assert p.read_text(encoding="utf-8") == before       # 一个字节没动


def test_upsert_embedding_model_stub_api_still_allowed(tmp_path):
    """embedding 槽位不拦(默认配置自带 api:ollama 的 embedding 模型,embed 无生产调用者)。"""
    ok, reason = cm.upsert_model({"provider": "ollama", "model_id": "ollama/nomic-embed-text",
                                  "api": "ollama", "role": "embedding",
                                  "base_url": "http://127.0.0.1:11434", "api_key": ""},
                                 _w(tmp_path))
    assert ok, reason


# ============ ② CFG-04(b):验证阶段 fail-loud(不等聊天时才炸) ============

def test_validate_fails_loud_on_stub_api_without_calling():
    app = _app()

    class _GW:
        reg = SimpleNamespace(
            default_chat="g/gemini",
            models={"g/gemini": SimpleNamespace(api="google-generative-ai")})

        async def complete(self, messages, tools, ref):
            raise AssertionError("stub api 不该发真请求")
            yield  # pragma: no cover

    app.state.runtime_kwargs = {"gateway": _GW()}
    r = TestClient(app).post("/api/model/validate").json()
    assert r["ok"] is False
    assert r["error_class"] == "unimplemented_api"
    assert "openai-completions" in r["reason"]           # 人话:OpenAI 兼容端点该用什么


def test_validate_still_calls_for_implemented_api():
    """回归锁:已实现方言照常真调(models 元数据在也不误拦)。"""
    app = _app()

    class _GW:
        reg = SimpleNamespace(default_chat="a/c",
                              models={"a/c": SimpleNamespace(api="anthropic-messages")})

        async def complete(self, messages, tools, ref):
            yield {"type": "text", "text": "pong"}

    app.state.runtime_kwargs = {"gateway": _GW()}
    r = TestClient(app).post("/api/model/validate").json()
    assert r["ok"] is True and r["model"] == "a/c"


# ============ ② CFG-04(c):stub 报错本身带行动指引(i18n en+zh) ============

@pytest.mark.asyncio
async def test_stub_adapter_error_actionable_en_and_zh():
    stub = default_adapters()["openai-responses"]
    with pytest.raises(NotImplementedError) as ei:
        await anext(stub.complete([], [], None, None))
    msg = str(ei.value)
    assert "openai-responses" in msg and "openai-completions" in msg   # 指路,不是裸"未实现"
    i18n.set_locale("zh")
    try:
        with pytest.raises(NotImplementedError) as ei_zh:
            await anext(stub.complete([], [], None, None))
        assert "未实现" in str(ei_zh.value) and "openai-completions" in str(ei_zh.value)
    finally:
        i18n.set_locale("en")


# ============ 顺手:init 自定义档(名称/base_url/model id/key,api 固定 openai-completions) ============

def test_build_custom_config_loads_and_resolves():
    from karvyloop.cli.wizard import _build_custom_config
    from karvyloop.gateway.registry import ModelRegistry

    txt = _build_custom_config("https://ark.example.com/api/v3", "my-endpoint-model",
                               "sk-cp-DO-NOT-LEAK-0123456789")
    cfg = yaml.safe_load(txt)
    reg = ModelRegistry.from_config(cfg)                 # schema 合法 + 校验过
    assert cfg["agents"]["defaults"]["model"] == "custom/my-endpoint-model"
    pc = reg.provider_of("custom/my-endpoint-model")     # 块 key = 前缀(provider_of 契约)
    assert pc.base_url == "https://ark.example.com/api/v3"
    assert pc.auth_header == "Authorization"
    assert reg.get("custom/my-endpoint-model").api == "openai-completions"  # 不给 stub 选项


def test_run_wizard_custom_flow_end_to_end(tmp_path, monkeypatch):
    """init 向导选 custom → 三问(base_url/model id/key)→ 写出的 config 可加载可解析。"""
    from karvyloop.cli.render import Renderer
    from karvyloop.cli.wizard import run_wizard
    from karvyloop.gateway.registry import ModelRegistry

    target = tmp_path / "config.yaml"
    inputs = iter(["custom", "https://ark.example.com/api/v3", "my-endpoint-model",
                   "sk-cp-DO-NOT-LEAK-0123456789"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    out = io.StringIO()
    rc = run_wizard(target=target, renderer=Renderer(), stdout=out)
    assert rc == 0
    cfg = yaml.safe_load(target.read_text(encoding="utf-8"))
    ModelRegistry.from_config(cfg)                       # 首跑不崩(拍 9.4-P0 同款门)
    assert cfg["agents"]["defaults"]["model"] == "custom/my-endpoint-model"
    assert cfg["models"]["providers"]["custom"]["api_key"] == "sk-cp-DO-NOT-LEAK-0123456789"


def test_custom_flow_bad_base_url_fails_loud(tmp_path, monkeypatch):
    from karvyloop.cli.render import Renderer
    from karvyloop.cli.wizard import WizardError, run_wizard

    inputs = iter(["custom", "not-a-url"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    with pytest.raises(WizardError):
        run_wizard(target=tmp_path / "config.yaml", renderer=Renderer(), stdout=io.StringIO())
    assert not (tmp_path / "config.yaml").exists()       # 半截配置不落盘


def test_validate_api_key_custom_generic_checks():
    from karvyloop.cli.wizard import validate_api_key
    ok, _ = validate_api_key("custom", "sk-cp-DO-NOT-LEAK-0123456789")
    assert ok
    ok2, err2 = validate_api_key("custom", "FAKE-KEY-123")
    assert not ok2 and "占位符" in err2


# ============ ③ CFG-02:无 key 写配置的诚实面 ============

def test_wizard_skipped_key_prints_export_warning_not_lie(tmp_path, monkeypatch):
    """跳过 key → config 写 ${ENV} 占位;收尾必须说"先 export 才跑得起",不能说"key 已写入"。"""
    from karvyloop.cli.render import Renderer
    from karvyloop.cli.wizard import run_wizard

    target = tmp_path / "config.yaml"
    inputs = iter(["anthropic", ""])                     # 选 anthropic,key 直接回车跳过
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    out = io.StringIO()
    rc = run_wizard(target=target, renderer=Renderer(), stdout=out)
    assert rc == 0
    text = target.read_text(encoding="utf-8")
    assert "${ANTHROPIC_API_KEY}" in text                # 占位写盘(设计如此:env 路径合法)
    printed = out.getvalue()
    assert "export ANTHROPIC_API_KEY" in printed         # 诚实:没 export 之前跑不起来
    assert "already in config.yaml" not in printed       # 旧谎话不再出现
