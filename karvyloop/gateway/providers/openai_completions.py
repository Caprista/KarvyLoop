"""openai_completions — OpenAI Chat Completions API adapter(真 HTTP/SSE → 统一 Event)。

P3 协议层(2026-06-21):此前 gateway 只有 anthropic-messages、openai-completions 是 stub →
OpenAI 本体 + 所有 **OpenAI-兼容端点**(vLLM / Ollama OpenAI 模式 / 本地模型 / 自建网关)都跑不了。
本 adapter 补上,与 AnthropicAdapter 同构。

格式转换(统一用 anthropic 块格式 ↔ OpenAI chat 格式):
- 消息:content 字符串原样;content 块数组 → text 拼成 content、tool_use → assistant.tool_calls、
  tool_result → 独立 `role:"tool"` 消息(tool_call_id);system 作为首条 system 消息。
- 工具:{name,description,input_schema} → {type:"function",function:{name,description,parameters}}。
- SSE:`data: {...}` 块 → choices[0].delta.content=TextDelta;delta.tool_calls 按 index 增量累积
  → ToolUseStart(见 name)+ ToolUseStop(finish_reason 时 flush);usage(include_usage)→ Usage。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import AsyncIterator, Optional

from karvyloop.schemas import ModelDefinition, ProviderConfig

from ..events import (
    Done, Event, ErrorEvent, TextDelta, ThinkingDelta, ToolUseStart, ToolUseStop, Usage,
)
from ..system import SystemPrompt
from .anthropic import StreamTimeoutError, provider_timeout, stream_deadline


def _cached_read_tokens(usage: dict) -> int:
    """从 OpenAI 系 usage 里读"命中缓存的 prompt token"(自动缓存,无需请求侧标记)。

    字段名各家不同,按实际形态读、读不到就 0(绝不猜):
    - OpenAI / vLLM / 多数兼容端点:`usage.prompt_tokens_details.cached_tokens`。
    - DeepSeek:`usage.prompt_cache_hit_tokens`(另有 `prompt_cache_miss_tokens`,不需要)。
    记进 Usage.cache_read → gateway 唯一咽喉按现有 cache_read 列记账(记账逻辑一字不改)。
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        v = details.get("cached_tokens")
        if isinstance(v, int):
            return v
    v = usage.get("prompt_cache_hit_tokens")   # DeepSeek 方言
    if isinstance(v, int):
        return v
    return 0


_TIMEOUT_ERRORS = {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"}


def _error_message(e: Exception, *, url: str, timeout_s: float) -> str:
    """异常 → 可诊断的 message。

    httpx 的 Read/ConnectTimeout `str(e)` 是**空串**(线上日志里就是 `message=` 空着),
    等于只知道"超时"、不知道阈值多少、打的是哪个端点,无法区分「阈值太小」和「服务端卡死」。
    超时类异常统一补上 timeout 阈值与 URL;其余异常保留原文(空则退回类名)。
    """
    detail = str(e).strip()
    if type(e).__name__ in _TIMEOUT_ERRORS:
        msg = f"等待超过 timeout={timeout_s:.0f}s 无数据 (POST {url})"
        return f"{msg} | {detail}" if detail else msg
    return detail or type(e).__name__


def _content_to_text(content) -> str:
    """tool_result.content(字符串 / text 块数组)→ 纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", "") if b.get("type") == "text" else json.dumps(b, ensure_ascii=False))
            else:
                parts.append(str(b))
        return "".join(parts)
    return "" if content is None else str(content)


def messages_to_openai(messages: list[dict], system: Optional[SystemPrompt]) -> list[dict]:
    """统一(anthropic 块)消息 → OpenAI chat 消息。"""
    out: list[dict] = []
    if system is not None:
        sys_text = "\n\n".join([*getattr(system, "static", []), *getattr(system, "dynamic", [])]).strip()
        if sys_text:
            out.append({"role": "system", "content": sys_text})
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        text_parts, tool_calls, tool_results, image_parts = [], [], [], []
        for b in (content or []):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                text_parts.append(b.get("text", ""))
            elif bt == "image":
                # Anthropic 图块 → OpenAI image_url(data URI)。多模态:发图问。
                src = b.get("source") or {}
                if src.get("type") == "base64" and src.get("data"):
                    image_parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{src.get('media_type', 'image/png')};base64,{src['data']}"}})
            elif bt == "tool_use":
                tool_calls.append({
                    "id": b.get("id", ""), "type": "function",
                    "function": {"name": b.get("name", ""),
                                 "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)},
                })
            elif bt == "tool_result":
                tool_results.append({"role": "tool", "tool_call_id": b.get("tool_use_id", ""),
                                     "content": _content_to_text(b.get("content", ""))})
        if role == "assistant":
            msg: dict = {"role": "assistant", "content": "".join(text_parts) or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:
            # user 回合:tool_result 必须作为 role:tool 消息紧跟 assistant.tool_calls,故先发它们
            out.extend(tool_results)
            if image_parts:
                # 有图 → content 是 parts 列表(文本 + 图);OpenAI 多模态格式
                parts = ([{"type": "text", "text": "".join(text_parts)}] if any(text_parts) else []) + image_parts
                out.append({"role": role, "content": parts})
            elif text_parts:
                out.append({"role": role, "content": "".join(text_parts)})
            elif not tool_results:
                out.append({"role": role, "content": ""})
    return out


def tools_to_openai(tools: list[dict]) -> list[dict]:
    """anthropic 工具 schema → OpenAI function schema。"""
    out = []
    for t in (tools or []):
        out.append({"type": "function", "function": {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
        }})
    return out


class OpenAICompletionsAdapter:
    api = "openai-completions"

    def build_request(self, messages, tools, model: ModelDefinition,
                      provider: ProviderConfig, system: Optional[SystemPrompt],
                      extra_body: Optional[dict] = None,
                      response_schema: Optional[dict] = None) -> dict:
        body: dict = {
            "model": model.id.split("/", 1)[-1],
            "messages": messages_to_openai(messages, system),
        }
        if model.max_tokens:
            body["max_tokens"] = model.max_tokens
        if tools:
            body["tools"] = tools_to_openai(tools)
        if response_schema is not None:
            # 约束解码(业界做法):response_format=json_schema strict 模式 →
            # provider 保证输出是 schema-合法 JSON。schema 归一(补 additionalProperties:false
            # + 全 required)在 openai_response_format 内做,不改调用方原 dict。schema=None → 不碰(零回归)。
            from ..structured import openai_response_format
            body["response_format"] = openai_response_format(response_schema)
        if extra_body:
            # 推理强度等按配置注入的顶层参数(gateway/reasoning.py 产;如 reasoning_effort)
            body.update(extra_body)
        return body

    async def complete(self, messages, tools, model, provider, *, system=None,
                       extra_body: Optional[dict] = None,
                       cache: bool = True,
                       response_schema: Optional[dict] = None) -> AsyncIterator[Event]:
        import httpx  # 延迟导入(测试走 mock 不需要)

        # cache:OpenAI 系是**自动缓存**(命中不需请求侧标记 cache_control)—— 参数只为与
        # anthropic adapter 签名对齐,此处不影响请求体。命中读在 _normalize 的 usage 里。
        body = self.build_request(messages, tools, model, provider, system, extra_body,
                                  response_schema)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}   # 流末带 usage
        key = provider.api_key or ""
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        # 额外静态头(如 Kimi For Coding 的 User-Agent 放行门)—— 奇怪端点也只靠配置接入。
        # 不覆盖 Authorization(密钥唯一来源是 api_key);其余 header 调用方可定制。
        for hk, hv in (getattr(provider, "extra_headers", None) or {}).items():
            if hk.lower() != "authorization":
                headers[hk] = hv
        base = provider.base_url.rstrip("/")
        path = getattr(provider, "messages_path", "") or ""
        # 自愈:messages_path 没设(或是 ProviderConfig 的 anthropic schema 默认 "/v1/messages",
        # 对 openai 端点会 404)→ 当"未设",按 base 是否已含版本段选对路径,让旧/错配置也开箱即跑。
        # base 末尾已是版本段(/v1 = deepseek/openai/...;/v3 等 = 火山 Ark 之类的 OpenAI 兼容端点)
        # → 只补 /chat/completions;否则补 /v1/chat/completions。CFG-04 实况:Ark base 是
        # .../api/v3,旧的「只认 /v1」会拼出 .../api/v3/v1/chat/completions → 404。
        if not path or path == "/v1/messages":
            import re as _re
            # 版本根识别放宽(审计 #87 §3-SUSPECTED①):除 /v1../v3(deepseek/openai/Ark)外,
            # 也认 /v1beta·/v1alpha(Gemini OpenAI 兼容形态 .../v1beta/openai)与 /openai 结尾根
            # (显式配置的兼容挂载点)—— 否则给它们再补 /v1/... 会拼出 .../v1beta/v1/... 404。
            # 仍有歧义端点走显式 messages_path 兜底(它优先,这里只是没设时的自愈)。
            has_version_root = bool(_re.search(r"/(v\d+[a-z]*|openai)$", base))
            path = "/chat/completions" if has_version_root else "/v1/chat/completions"
        url = base + path
        tools_acc: dict = {}   # index → {id,name,args}(工具流式累积)
        if os.environ.get("KARVYLOOP_ADAPTER_DEBUG"):
            print(f"[openai adapter] complete() model={model.id!r} msgs={len(messages)} "
                  f"tools={len(tools)} url={url}", file=sys.stderr)
        # 整段墙钟(docs/87 §五):同 anthropic —— httpx timeout 只管单次 read,provider 周期吐
        # keepalive 就能把 drive worker 无限吊住;从流开始计时,循环体顶部逐行核对,超了 fail-loud。
        _start = time.monotonic()
        _deadline = stream_deadline(provider)
        _timeout_s = provider_timeout(provider)
        _saw_sse = False
        _plain_lines: list[str] = []   # 非 SSE 回包(整段 JSON)缓冲
        _done = False
        try:
            async with httpx.AsyncClient(timeout=_timeout_s) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if time.monotonic() - _start > _deadline:
                            raise StreamTimeoutError(
                                f"流式响应超过整段墙钟上限 {_deadline:.0f}s "
                                f"(provider={provider.name}) — provider 只吐 keepalive/未完成")
                        if not line.startswith("data:"):
                            # 非 SSE 回包兜底:不少自建网关会忽略 stream=true,直接回一整段
                            # application/json。此前这些行全被 continue 跳过 → 零事件且不报错,
                            # 上层只看到"模型空流"。先攒着;整条流都没 SSE 帧时按整包解析。
                            if not _saw_sse and line.strip():
                                _plain_lines.append(line)
                            continue
                        _saw_sse = True
                        _plain_lines.clear()
                        data = line[len("data:"):].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            # 部分端点不发 finish_reason 只发 [DONE]:已累积的工具必须在此落地,
                            # 否则模型明明出了工具却"什么都没发生"。
                            if not _done:
                                had_tools = bool(tools_acc)
                                for ev in self._tool_stops(tools_acc):
                                    yield ev
                                yield Done(stop_reason="tool_calls" if had_tools else "stop")
                                _done = True
                            break
                        for ev in self._normalize(json.loads(data), tools_acc):
                            if isinstance(ev, Done):
                                _done = True
                            yield ev
            if not _saw_sse and _plain_lines:
                try:
                    payload = json.loads("\n".join(_plain_lines))
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    if payload.get("error") is not None:
                        # 200 包裹的错误体(自建网关常见):如实报真因,别再当"空流"。
                        yield ErrorEvent(
                            kind="ProviderErrorBody",
                            message=json.dumps(payload["error"], ensure_ascii=False)[:500])
                    else:
                        for ev in self._normalize(payload, tools_acc):
                            yield ev
                        u = payload.get("usage")
                        if u:
                            yield Usage(input_tokens=u.get("prompt_tokens", 0),
                                        output_tokens=u.get("completion_tokens", 0),
                                        cache_read=_cached_read_tokens(u))
            if not _done and tools_acc:
                # 流自然结束(既无 [DONE] 也无 finish_reason)也把累积工具落地。
                for ev in self._tool_stops(tools_acc):
                    yield ev
                yield Done(stop_reason="tool_calls")
        except Exception as e:  # noqa: BLE001 — 归一化成 ErrorEvent,不穿透打断上层
            yield ErrorEvent(kind=type(e).__name__,
                             message=_error_message(e, url=url, timeout_s=_timeout_s))

    def _normalize(self, chunk: dict, tools_acc: dict):
        choices = chunk.get("choices") or []
        if not choices:
            u = chunk.get("usage")
            if u:
                yield Usage(input_tokens=u.get("prompt_tokens", 0),
                            output_tokens=u.get("completion_tokens", 0),
                            cache_read=_cached_read_tokens(u))
            return
        ch = choices[0]
        delta = ch.get("delta")
        if delta is None:
            # 非流式形态(choices[0].message):整段 JSON 回包(网关忽略 stream=true)。
            msg = ch.get("message") or {}
            delta = {
                "content": msg.get("content"),
                "reasoning_content": msg.get("reasoning_content") or msg.get("reasoning"),
                "tool_calls": [
                    {"index": i, "id": tc.get("id"), "function": tc.get("function") or {}}
                    for i, tc in enumerate(msg.get("tool_calls") or [])
                ],
            }
        delta = delta or {}
        content = delta.get("content")
        if isinstance(content, list):
            # 少数端点 content 是 parts 列表([{"type":"text","text":...}])而非字符串。
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        if content:
            yield TextDelta(text=content)
        # 推理模型(SGLang/vLLM/DeepSeek 方言)会把输出分到 reasoning_content(或 reasoning)。
        # 只认 content 时,答案整段落在这两个字段 → 上层拿到"零文本"并误判 infra_dead。
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            yield ThinkingDelta(text=reasoning)
        for tc in (delta.get("tool_calls") or []):
            idx = tc.get("index", 0)
            slot = tools_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name") and not slot["name"]:
                slot["name"] = fn["name"]
                yield ToolUseStart(id=slot["id"], name=slot["name"])
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]
        fr = ch.get("finish_reason")
        if fr:
            for ev in self._tool_stops(tools_acc):
                yield ev
            yield Done(stop_reason=fr)

    def _tool_stops(self, tools_acc: dict):
        """把累积工具一次落地(finish_reason / [DONE] / 流自然结束 共用)。"""
        for idx in sorted(tools_acc):
            slot = tools_acc[idx]
            try:
                inp = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                inp = {"_raw": slot["args"]}
            yield ToolUseStop(id=slot["id"], input=inp)
        tools_acc.clear()

    async def embed(self, text: str, model: ModelDefinition, provider: ProviderConfig):
        raise NotImplementedError("openai-completions embedding P-next(用专门 embedding provider)")


__all__ = ["OpenAICompletionsAdapter", "messages_to_openai", "tools_to_openai"]
