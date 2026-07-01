"""test_multimodal — 发图问:前端 data_url→base64 归一 / openai 图块转 image_url / drive 线程透传图。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
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
