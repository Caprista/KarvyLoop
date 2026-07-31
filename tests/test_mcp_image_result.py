"""docs/99 刀2 slice-A:MCP 产图工具(computer-use 截图)的 image 载荷抵达会视觉的 planner.

病根(接前对着真代码验的):screenshot/get_app_state 回的 image 块被 mcp_tool._flatten 丢成
文本占位 "[+N non-text block(s)]" → planner 是瞎的、任何 loop 白搭。

验:image 块 → 顶层 image 块回灌(provider 无关:Anthropic 直接吃 / OpenAI adapter 拆成单独
user 图消息);planner 不会视觉 → 诚实丢弃;非产图工具零回归(tool_result 文本不变、无多余
images 键、base64 不进文本)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from karvyloop.atoms.executor import _serialize_results_for_model
from karvyloop.atoms.orchestration import ToolResult, ToolUseBlock, _run_one
from karvyloop.coding.tools._result import CodingResult
from karvyloop.coding.tools.mcp_tool import _extract_images


def _img(data="AAAABBBB", mt="image/png"):
    return SimpleNamespace(type="image", data=data, mimeType=mt)


def _txt(text):
    return SimpleNamespace(type="text", text=text)


class TestExtractImages:
    def test_extracts_image_content_blocks(self):
        content = [_txt("here is the screen"), _img("B64DATA", "image/jpeg")]
        assert _extract_images(content) == [{"data": "B64DATA", "media_type": "image/jpeg"}]

    def test_default_media_type_and_skips_empty_data(self):
        content = [_img("X", mt=""), SimpleNamespace(type="image", data="")]
        assert _extract_images(content) == [{"data": "X", "media_type": "image/png"}]

    def test_no_images_returns_empty(self):
        assert _extract_images([_txt("just text")]) == []
        assert _extract_images([]) == []


class TestRunOnePassesImages:
    @pytest.mark.asyncio
    async def test_images_flow_from_coding_result_to_tool_result(self):
        class _ShotTool:
            name = "mcp_computer_use_screenshot"

            async def __call__(self, inp):
                return CodingResult(ok=True, payload="[+1 non-text block(s)]",
                                    images=[{"data": "SHOT", "media_type": "image/png"}])

            def is_concurrency_safe(self, inp):
                return False

        r = await _run_one(_ShotTool(),
                           ToolUseBlock(id="c1", name="mcp_computer_use_screenshot", input={}))
        assert r.images == [{"data": "SHOT", "media_type": "image/png"}]

    @pytest.mark.asyncio
    async def test_non_image_tool_has_none_images(self):
        class _PlainTool:
            name = "read_file"

            async def __call__(self, inp):
                return CodingResult(ok=True, payload="hi")

            def is_concurrency_safe(self, inp):
                return False

        r = await _run_one(_PlainTool(), ToolUseBlock(id="c1", name="read_file", input={}))
        assert r.images is None


class TestSerializeEmitsImageBlocks:
    def test_image_block_appended_after_tool_result_when_vision(self):
        r = ToolResult(tool_use_id="c1", name="mcp_computer_use_screenshot",
                       content=CodingResult(ok=True, payload="shot"),
                       images=[{"data": "SHOT", "media_type": "image/png"}])
        msgs = _serialize_results_for_model([r], accepts_images=True)
        assert len(msgs) == 1 and msgs[0]["role"] == "user"
        content = msgs[0]["content"]
        assert content[0]["type"] == "tool_result" and content[0]["tool_use_id"] == "c1"
        assert content[-1] == {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "SHOT"}}

    def test_multiple_results_tool_results_first_then_images(self):
        """块序合规:所有 tool_result 在前,image 块追加在后(Anthropic 要求 tool_result 领头)。"""
        r1 = ToolResult(tool_use_id="c1", name="read_file", content=CodingResult(ok=True, payload="x"))
        r2 = ToolResult(tool_use_id="c2", name="mcp_computer_use_screenshot",
                        content=CodingResult(ok=True, payload="shot"),
                        images=[{"data": "SHOT"}])
        content = _serialize_results_for_model([r1, r2], accepts_images=True)[0]["content"]
        types = [b["type"] for b in content]
        assert types == ["tool_result", "tool_result", "image"]

    def test_image_dropped_when_model_has_no_vision(self):
        r = ToolResult(tool_use_id="c1", name="mcp_computer_use_screenshot",
                       content=CodingResult(ok=True, payload="shot"),
                       images=[{"data": "SHOT"}])
        content = _serialize_results_for_model([r], accepts_images=False)[0]["content"]
        assert all(b["type"] != "image" for b in content)   # 无视觉 → 丢图,纯文字照走不 400

    def test_non_image_result_text_unchanged_no_images_key(self):
        """零回归:普通 CodingResult 的 tool_result 文本 pop 掉 images 键(byte 一致,不泄 base64)。"""
        r = ToolResult(tool_use_id="c1", name="read_file", content=CodingResult(ok=True, payload="hello"))
        content = _serialize_results_for_model([r], accepts_images=True)[0]["content"]
        assert len(content) == 1 and content[0]["type"] == "tool_result"
        assert "images" not in content[0]["content"]        # JSON 文本里没有 images 键
        assert '"payload": "hello"' in content[0]["content"]

    def test_error_result_still_serialized(self):
        r = ToolResult(tool_use_id="c1", name="x", content=None, is_error=True, error_reason="boom")
        content = _serialize_results_for_model([r], accepts_images=True)[0]["content"]
        assert content[0]["type"] == "tool_result" and "boom" in content[0]["content"]

    def test_empty_results_empty_message(self):
        assert _serialize_results_for_model([], accepts_images=True) == []
