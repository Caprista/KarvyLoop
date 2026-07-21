"""test_multimodal — 发图问:前端 data_url→base64 归一 / openai 图块转 image_url / drive 线程透传图。

D(内测 U-06)追加:
- 模型没声明支持视觉(input_modalities 无 "image")→ 执行器**不拼图块**,该轮 user 内容
  附一行 i18n 人话占位,任务继续跑文字(不 400 崩);声明了 → 图块照拼。
- 判不出来(mock gateway 无 reg)→ 保守沿用旧行为拼图块(0 回归)。
- drive 聊天路径 provider 4xx/密钥/网络错误 → 人话在前、真因在后(_humanize_drive_error)。
- config upsert_model 编辑别的字段不静默丢手写的 input_modalities。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

import httpx
import pytest
import respx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.routes import _normalize_images  # noqa: E402
from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter, messages_to_openai  # noqa: E402
from karvyloop.gateway.system import SystemPrompt  # noqa: E402
from karvyloop.schemas import ModelDefinition, ProviderConfig  # noqa: E402


def test_normalize_strips_data_uri():
    out = _normalize_images([{"data_url": "data:image/jpeg;base64,ZZZ", "media_type": ""},
                             {"data_url": "data:image/png;base64,Q", "media_type": "image/png"},
                             {"data_url": "garbage"}])  # 无逗号 → 丢
    assert out == [{"data": "ZZZ", "media_type": "image/jpeg"}, {"data": "Q", "media_type": "image/png"}]


def test_openai_image_block_to_image_url():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "这是什么"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAB"}}]}]
    out = messages_to_openai(msgs, None)
    assert out[0]["content"][0] == {"type": "text", "text": "这是什么"}
    assert out[0]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAB"}}


@pytest.mark.asyncio
@respx.mock
async def test_openai_adapter_sends_image_in_body():
    cap = {}

    def _resp(req):
        import json as _j
        cap["body"] = _j.loads(req.content)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    respx.post("https://api.test/v1/chat/completions").mock(side_effect=_resp)
    prov = ProviderConfig(name="x", api_key="FAKE-DO-NOT-LEAK", base_url="https://api.test/v1",
                          auth="api-key", auth_header="Authorization", messages_path="/chat/completions")
    model = ModelDefinition(id="x/vis", name="vis", api="openai-completions", context_window=128000, max_tokens=64)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAB"}}]}]
    async for _ in OpenAICompletionsAdapter().complete(msgs, [], model, prov):
        pass
    parts = cap["body"]["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in parts)   # 图真进了请求体


def test_drive_in_tui_threads_images_to_factory(monkeypatch):
    import karvyloop.workbench.main_loop_bridge as bridge
    seen = {}

    def _stub_factory(*, token, sandbox, gateway, workspace_root, model_ref="",
                      governance="", emitter=None, persona=None, mcp_tools=None, images=None, **_):
        seen["images"] = images

        def slow_brain(intent, *, ctx=None):
            from karvyloop.schemas.atom import AtomRun
            return "ok", AtomRun(atom_id="a", input={"intent": intent}, output={"text": "ok"},
                                 success=True, tool_calls=[], trace_ref="t", ts=1.0)
        return slow_brain

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", _stub_factory)

    class _Res:
        brain = types.SimpleNamespace(value="slow"); text = "ok"; skill_name = ""
        fast_brain_hit = False; crystallized = False; task_id = "t"; ctx_dependent = False

    class _ML:
        def drive(self, intent, *, slow_brain, ctx=None, scope=None, fresh=False):
            slow_brain(intent, ctx=ctx); return _Res()

    imgs = [{"data": "AAAB", "media_type": "image/png"}]
    asyncio.run(bridge.drive_in_tui("看图", _ML(), token=1, sandbox=2, gateway=3,
                                    workspace_root="/tmp", images=imgs))
    assert seen["images"] == imgs   # 图透传到了 forge 工厂


# ==== D(内测 U-06):input_modalities 门 —— 文本模型收图不 400,降级人话占位 ====

from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round  # noqa: E402
from karvyloop.atoms.executor import run as executor_run  # noqa: E402
from karvyloop.gateway import GatewayClient  # noqa: E402
from karvyloop.gateway.registry import ModelRegistry  # noqa: E402
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken  # noqa: E402


@pytest.fixture()
def _en_locale():
    """锁 en locale(占位文案断言稳定),用完复位。"""
    from karvyloop import i18n
    i18n.set_locale("en")
    yield
    i18n.set_locale(None)


def _atom(model: str) -> AtomSpec:
    return AtomSpec(id="a1", kind="task", prompt="", input_schema={"type": "object"},
                    output_schema={"type": "object"}, tools=[], model=model)


def _tok() -> CapabilityToken:
    return CapabilityToken(task_id="t", grants=[Capability(resource="fs:/tmp", ops=["read"])],
                           expiry=time.time() + 3600)


def _gw_with_models(adapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            # **显式声明** text-only → 图降级占位(不发图块,免 400)
            {"id": "p/textonly", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100,
             "input_modalities": ["text"]},
            # 显式声明支持视觉 → 图块照拼
            {"id": "p/vision", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100,
             "input_modalities": ["text", "image"]},
            # **未声明**(存量配置:没标 modalities 的 claude/gpt-4o/glm-4v)→ 未知 → 旧行为照拼
            {"id": "p/undeclared", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/textonly"}},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


_IMGS = [{"data": "AAAB", "media_type": "image/png"}]


@pytest.mark.asyncio
async def test_declared_text_only_model_drops_images_with_placeholder(_en_locale):
    """**显式声明** ["text"] 的模型 + 图 → 不拼图块(不 400),user 内容附人话占位,文字照跑完。"""
    adapter = ScriptedMockAdapter(rounds=[text_round("done")])
    gw = _gw_with_models(adapter)
    events = [ev async for ev in executor_run(_atom("p/textonly"), {"q": "看图"}, _tok(),
                                              gateway=gw, tools={}, images=list(_IMGS))]
    from karvyloop.atoms.executor import TerminalEvent
    from karvyloop.atoms.terminal import Terminal
    assert isinstance(events[-1], TerminalEvent)
    assert events[-1].reason == Terminal.COMPLETED       # 任务照常跑完,不是崩
    sent = adapter.last_request["messages"][0]["content"]
    assert isinstance(sent, str), "不该拼 content 块列表(无图块)"
    assert "can't view images" in sent                    # i18n 占位(en)
    assert "input_modalities" in sent                     # 指路怎么改声明
    assert "AAAB" not in sent                             # 图的 base64 没被塞进请求


@pytest.mark.asyncio
async def test_vision_model_keeps_image_blocks(_en_locale):
    """声明了 image 模态 → 图块照拼(行为不变)。"""
    adapter = ScriptedMockAdapter(rounds=[text_round("done")])
    gw = _gw_with_models(adapter)
    events = [ev async for ev in executor_run(_atom("p/vision"), {"q": "看图"}, _tok(),
                                              gateway=gw, tools={}, images=list(_IMGS))]
    sent = adapter.last_request["messages"][0]["content"]
    assert isinstance(sent, list)
    assert any(b.get("type") == "image" for b in sent), "视觉模型该收到图块"
    assert not any("can't view images" in str(b.get("text", "")) for b in sent)


@pytest.mark.asyncio
async def test_undeclared_model_keeps_image_blocks_regression(_en_locale):
    """**未声明** input_modalities(存量视觉模型配置)→ 未知 → 图块照拼(旧行为,零功能回退)。

    review 拧正锁:此前默认 ["text"] 会把所有没标注的模型都判 text-only 而丢图 —— 存量
    claude/gpt-4o 用户升级后图突然不发,是功能回退。正确语义 = 未声明不动、显式声明才降级。
    """
    adapter = ScriptedMockAdapter(rounds=[text_round("done")])
    gw = _gw_with_models(adapter)
    events = [ev async for ev in executor_run(_atom("p/undeclared"), {"q": "看图"}, _tok(),
                                              gateway=gw, tools={}, images=list(_IMGS))]
    sent = adapter.last_request["messages"][0]["content"]
    assert isinstance(sent, list), "未声明模型必须保持旧行为(带图块的 content 列表)"
    assert any(b.get("type") == "image" for b in sent)
    assert not any("can't view images" in str(b.get("text", "")) for b in sent)
    # schema 语义:未声明 = None(不是 ["text"])
    assert gw.reg.get("p/undeclared").input_modalities is None


def test_modality_gate_conservative_on_opaque_gateway():
    """判不出来(mock gateway 无 reg / resolve 抛)→ (True, ""):保持旧行为拼图块,0 回归。"""
    from unittest.mock import MagicMock
    from karvyloop.atoms.executor import _model_accepts_images
    opaque = MagicMock()
    opaque.resolve_model.side_effect = RuntimeError("no registry")
    assert _model_accepts_images(opaque, "x/y") == (True, "")
    # resolve 通但模型对象没 input_modalities 属性(桩)→ 同样保守放行
    stub = types.SimpleNamespace(
        resolve_model=lambda scope: "x/y",
        reg=types.SimpleNamespace(get=lambda ref: types.SimpleNamespace()))
    ok, _ = _model_accepts_images(stub, "x/y")
    assert ok is True


# ==== D②:drive 聊天路径 provider 错误人话化(人话在前,真因不丢) ====

def test_humanize_drive_error_400_bad_request(_en_locale):
    from karvyloop.atoms.executor import AdapterStreamError
    from karvyloop.workbench.main_loop_bridge import _humanize_drive_error
    e = AdapterStreamError("HTTPStatusError",
                           "Client error '400 Bad Request' for url 'https://api.test/v1/chat'")
    out = _humanize_drive_error(e)
    assert "rejected this request (4xx)" in out            # 人话开场
    assert "'400 Bad Request'" in out                       # 真因原文保留
    assert "AdapterStreamError" in out                      # 真实异常类名保留(可观测性②)


def test_humanize_drive_error_zh_locale():
    from karvyloop import i18n
    from karvyloop.atoms.executor import AdapterStreamError
    from karvyloop.workbench.main_loop_bridge import _humanize_drive_error
    i18n.set_locale("zh")
    try:
        e = AdapterStreamError("HTTPStatusError", "Client error '400 Bad Request' for url 'x'")
        out = _humanize_drive_error(e)
        assert "模型服务拒绝了这次请求" in out
        assert "'400 Bad Request'" in out
    finally:
        i18n.set_locale(None)


def test_humanize_drive_error_bad_key(_en_locale):
    from karvyloop.atoms.executor import AdapterStreamError
    from karvyloop.workbench.main_loop_bridge import _humanize_drive_error
    e = AdapterStreamError("HTTPStatusError", "Client error '401 Unauthorized' for url 'x'")
    out = _humanize_drive_error(e)
    assert "401/403" in out and "API key" in out
    assert "'401 Unauthorized'" in out


def test_humanize_drive_error_unknown_keeps_raw(_en_locale):
    """判不出的(普通代码缺陷)→ 保持旧格式原样,不乱包。"""
    from karvyloop.workbench.main_loop_bridge import _humanize_drive_error
    assert _humanize_drive_error(ValueError("boom")) == "ValueError: boom"


def test_drive_in_tui_surfaces_humanized_provider_error(_en_locale, monkeypatch):
    """接线级:drive 崩在 provider 400 → outcome.error 人话开场 + 真因保留(前端 ⚠ 直接可读)。"""
    import karvyloop.workbench.main_loop_bridge as bridge
    from karvyloop.atoms.executor import AdapterStreamError

    def _stub_factory(**_kw):
        def slow_brain(intent, *, ctx=None):
            raise AdapterStreamError(
                "HTTPStatusError", "Client error '400 Bad Request' for url 'https://api.test'")
        return slow_brain

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", _stub_factory)

    class _ML:
        def drive(self, intent, *, slow_brain, ctx=None, scope=None, fresh=False):
            return slow_brain(intent, ctx=ctx)

    outcome = asyncio.run(bridge.drive_in_tui("看图", _ML(), token=1, sandbox=2, gateway=3,
                                              workspace_root="/tmp"))
    assert outcome.error
    assert "rejected this request (4xx)" in outcome.error   # 人话
    assert "'400 Bad Request'" in outcome.error              # 真因原文
    assert "AdapterStreamError" in outcome.error             # 真实异常类名(可观测性②)


# ==== D③:config 约定 —— upsert 不静默丢 input_modalities ====

def test_upsert_model_accepts_and_preserves_input_modalities(tmp_path):
    import yaml
    from karvyloop.gateway.config_models import upsert_model
    cfg = tmp_path / "config.yaml"
    base = {"provider": "p1", "model_id": "p1/vis", "api": "openai-completions",
            "base_url": "http://localhost:8080/v1", "context_window": 1000, "max_tokens": 100}
    # ① 显式写 → 只收合法模态、去重保序
    ok, msg = upsert_model({**base, "input_modalities": ["image", "text", "bogus", "image"]},
                           cfg_path=cfg)
    assert ok, msg

    def _md():
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        return data["models"]["providers"]["p1"]["models"][0]

    assert _md()["input_modalities"] == ["image", "text"]
    # ② 编辑别的字段(请求未承载 input_modalities)→ 保留,不静默重置
    ok, msg = upsert_model({**base, "context_window": 2000}, cfg_path=cfg)
    assert ok, msg
    assert _md()["input_modalities"] == ["image", "text"]
    assert _md()["context_window"] == 2000
    # ③ 显式覆写回 text-only
    ok, msg = upsert_model({**base, "input_modalities": ["text"]}, cfg_path=cfg)
    assert ok, msg
    assert _md()["input_modalities"] == ["text"]
