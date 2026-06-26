"""Anthropic Messages adapter（gateway/providers/anthropic.py）。

真实 HTTP 流式实现（SSE → 统一 Event）。httpx 延迟导入，测试走 mock 不需要它。
⚠️ 本 adapter 为集成路径，未做网络级单测（见验收报告诚实标注）。

Debug 开关（**全部默认关**；正常跑完全静默，stderr 零输出）
─────────────────────────────────────────────────────────────
KARVYLOOP_ADAPTER_DEBUG=1
    适配器中级 trace。打印：response status / lifecycle event（message_start /
    ping / message_stop）/ content_block_start 类型 / content_block_delta 类型与
    文本 / message_delta 的 stop_reason+usage / 未知 chunk 类型。**诊断 SSE 流
    形状与 MiniMax 等兼容端点 4xx 字段级别问题时开**。
    代价：每次响应多 5-20 行 stderr；不影响 stdout 也不影响 SSE 解析。

KARVYLOOP_ADAPTER_DEBUG_RAW=1
    把每个 chunk 的完整 dict repr() 打 stderr（KARVYLOOP_ADAPTER_DEBUG 之**上**
    的更高一档）。**只在 KARVYLOOP_ADAPTER_DEBUG 还看不出问题**时开。
    代价：response 越大输出越爆 stdout/stderr 缓冲；仅用于现场排错，别留开。

KARVYLOOP_ADAPTER_QUIET=1
    关掉"未知 chunk 类型" / "未知 content_block_delta 类型" 的 stderr 警告。
    适配器遇到没识别的 SSE event 时默认打 stderr（这是有意的，让用户看到
    provider 协议有变），**确认协议变更是预期后开 QUIET 屏蔽**。

用法:
    # 看到 4xx 但不知为何:
    KARVYLOOP_ADAPTER_DEBUG=1 karvyloop run --json "..."
    # 上面还看不出 chunk 形态:
    KARVYLOOP_ADAPTER_DEBUG_RAW=1 karvyloop run --json "..."
    # 协议变更确认后:
    KARVYLOOP_ADAPTER_QUIET=1 karvyloop run --json "..."
"""

from __future__ import annotations

import json
import os
import sys
from typing import AsyncIterator, Optional

from karvyloop.schemas import ModelDefinition, ProviderConfig

from ..events import Done, Event, ErrorEvent, TextDelta, ThinkingDelta, ToolUseStart, ToolUseStop, Usage
from ..system import SystemPrompt


class AnthropicAdapter:
    api = "anthropic-messages"

    def build_request(self, messages, tools, model: ModelDefinition,
                      provider: ProviderConfig, system: Optional[SystemPrompt]) -> dict:
        body: dict = {
            "model": model.id.split("/", 1)[-1],
            "max_tokens": model.max_tokens,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        if system is not None:
            body["system"] = system.to_blocks()     # HR-9：静态前缀带 cache_control
        return body

    async def complete(self, messages, tools, model, provider, *, system=None
                       ) -> AsyncIterator[Event]:
        import httpx  # 延迟导入

        body = self.build_request(messages, tools, model, provider, system)
        body["stream"] = True
        # auth_header 决定鉴权方式:
        #   - x-api-key(默认):原生 Anthropic 习惯
        #   - Authorization: 大部分 Anthropic 兼容端点(MiniMax / 自建网关)用 Bearer
        key = provider.api_key or ""
        if provider.auth_header == "Authorization":
            headers = {
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        else:
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        # 额外静态头(配置驱动,如 UA 放行门)—— 不覆盖鉴权头(密钥唯一来源是 api_key)
        for hk, hv in (getattr(provider, "extra_headers", None) or {}).items():
            if hk.lower() not in ("authorization", "x-api-key"):
                headers[hk] = hv
        url = provider.base_url.rstrip("/") + provider.messages_path
        cur_tool: dict | None = None
        # debug: 每次调用入口打 stderr(看第二轮到底调没调)
        if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
            print(f"[adapter debug] complete() called model={model.id!r} "
                  f"msgs={len(messages)} tools={len(tools)} url={url}\n"
                  f"[adapter debug] request body: {body!r}",
                  file=sys.stderr)
        try:
            async with httpx.AsyncClient(timeout=provider_timeout(provider)) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                        print(f"[adapter debug] response status={resp.status_code}",
                              file=sys.stderr)
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data:
                            continue
                        for ev in self._normalize(json.loads(data), cur_tool):
                            if isinstance(ev, _ToolState):
                                cur_tool = ev.value
                            else:
                                yield ev
        except Exception as e:  # noqa: BLE001 — 归一化成 ErrorEvent，不让异常穿透打断上层
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                err_body = ""
                if hasattr(e, "response") and e.response is not None:
                    try:
                        err_body = e.response.text
                    except Exception:
                        err_body = "<unreadable>"
                print(f"[adapter debug] complete() raised: "
                      f"{type(e).__name__}: {e}\n"
                      f"[adapter debug] request body: {body!r}\n"
                      f"[adapter debug] error response body: {err_body!r}",
                      file=sys.stderr)
            yield ErrorEvent(kind=type(e).__name__, message=str(e))

    def _normalize(self, chunk: dict, cur_tool):
        t = chunk.get("type")
        # debug-raw:打每个 chunk 原文(诊断用, 打开后流巨慢但能看清 MiniMax 发了啥)
        if os.environ.get("KARVYLOOP_ADAPTER_DEBUG_RAW"):
            print(f"[adapter debug-raw] chunk={chunk!r}", file=sys.stderr)
        if t == "content_block_start":
            blk = chunk.get("content_block", {})
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                print(f"[adapter debug] content_block_start type={blk.get('type')!r}",
                      file=sys.stderr)
            if blk.get("type") == "tool_use":
                yield _ToolState({"id": blk["id"], "name": blk["name"], "json": ""})
                yield ToolUseStart(id=blk["id"], name=blk["name"])
            elif blk.get("type") == "thinking":
                # reasoning model(M3 等)发 thinking block;向上 yield 供审计
                yield ThinkingDelta(text="")
        elif t in ("message_start", "ping", "message_stop"):
            # Anthropic SSE 标准 lifecycle 事件,无业务内容,静默
            # 但 debug 模式下打一下方便看 message_stop 是否真来
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                print(f"[adapter debug] lifecycle {t} index={chunk.get('index')}",
                      file=sys.stderr)
        elif t == "content_block_delta":
            d = chunk.get("delta", {})
            # debug:每个 chunk 类型打 stderr 供诊断(KARVYLOOP_ADAPTER_DEBUG=1)
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                print(f"[adapter debug] delta type={d.get('type')!r} text={d.get('text','')!r} thinking={d.get('thinking','')!r}",
                      file=sys.stderr)
            if d.get("type") == "text_delta":
                yield TextDelta(text=d.get("text", ""))
            elif d.get("type") == "thinking_delta":
                # M3 的 thinking 块;默认不外露,executor 当 raw 收
                yield ThinkingDelta(text=d.get("thinking", ""))
            elif d.get("type") == "input_json_delta" and cur_tool:
                cur_tool["json"] += d.get("partial_json", "")
            else:
                # 未知 delta 类型:打 stderr 供诊断(关闭: KARVYLOOP_ADAPTER_QUIET=1)
                if not os.environ.get("KARVYLOOP_ADAPTER_QUIET"):
                    print(f"[anthropic adapter] unknown content_block_delta type={d.get('type')!r}",
                          file=sys.stderr)
        elif t == "content_block_stop" and cur_tool:
            try:
                inp = json.loads(cur_tool["json"]) if cur_tool["json"] else {}
            except json.JSONDecodeError:
                inp = {"_raw": cur_tool["json"]}
            yield ToolUseStop(id=cur_tool["id"], input=inp)
            yield _ToolState(None)
        elif t == "content_block_stop":
            # thinking / text block 的 stop 没 cur_tool 是合法的,静默
            pass
        elif t == "message_delta":
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                print(f"[adapter debug] message_delta delta={chunk.get('delta')!r} usage={chunk.get('usage')!r}",
                      file=sys.stderr)
            u = chunk.get("usage", {})
            if u:
                yield Usage(input_tokens=u.get("input_tokens", 0),
                            output_tokens=u.get("output_tokens", 0),
                            cache_read=u.get("cache_read_input_tokens", 0),
                            cache_write=u.get("cache_creation_input_tokens", 0))
            sr = chunk.get("delta", {}).get("stop_reason")
            if sr:
                yield Done(stop_reason=sr)
        else:
            # 未知顶层 chunk 类型:打 stderr 供诊断(KARVYLOOP_ADAPTER_DEBUG=1 看全部 delta)
            if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
                print(f"[adapter debug] unknown chunk type={t!r} chunk={chunk!r}",
                      file=sys.stderr)
            elif not os.environ.get("KARVYLOOP_ADAPTER_QUIET"):
                print(f"[anthropic adapter] unknown chunk type={t!r}", file=sys.stderr)

    async def embed(self, text, model, provider):
        raise NotImplementedError("Anthropic 无 embedding；embedding 用本地模型（ollama）")


class _ToolState:
    """内部信号：携带当前 tool_use 累积状态（不对外 yield）。"""
    def __init__(self, value):
        self.value = value


def provider_timeout(provider: ProviderConfig) -> float:
    return 120.0
