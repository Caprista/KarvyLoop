"""按需执行器：ReAct 主循环（atoms/executor.py）。

规格：docs/modules/atom-executor.md §2.1/§2.3（HR-11 + HR-3 + AC1-10）。
注入依赖：gateway / tools / context_governance 全部可注入(mock 友好,不触网)。
末事件 = Terminal(reason) + AtomRun（按 spec §3 写 Trace 用）。

Debug 开关（**全部默认关**；正常跑完全静默，stderr 零输出）
─────────────────────────────────────────────────────────────
KARVYLOOP_EXECUTOR_DEBUG=1
    主循环 trace。打印：每次迭代的 turn + msgs 数 / 模型调用后 text+tool_use
    摘要 / BREAK 时的 events 列表（帮定位 executor 为何"no_tool_use"提前退出）。
    **诊断 ReAct 循环提前结束 / 消息没正确回灌 / tool_use 没被识别**时开。
    代价：每次迭代 2-4 行 stderr；不影响事件流。

配合 karvyloop/gateway/providers/anthropic.py 的 KARVYLOOP_ADAPTER_DEBUG 使用：
    KARVYLOOP_EXECUTOR_DEBUG=1 KARVYLOOP_ADAPTER_DEBUG=1 karvyloop run --json "..."
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from karvyloop.gateway import GatewayClient, ResolveScope, SystemPrompt
from karvyloop.capability import Allow as _Allow, Mode as _Mode, PermissionContext as _PC, authorize as _authorize, check
from karvyloop.schemas import AtomRun, AtomSpec, CapabilityToken

from .loop_state import LoopState, Transition
from .orchestration import ToolResult, ToolUseBlock, run_tools
from .terminal import Terminal


CIRCUIT_OPEN_THRESHOLD = 3  # 连续失败次数(HR-3)


class AdapterStreamError(RuntimeError):
    """adapter 把流内异常归一化成 ErrorEvent(kind=原异常类名, message=str(e),不穿透)——
    executor 在此重建异常语义(可观测性②):此前 executor 没有 ErrorEvent 分支,
    真网络断/4xx 会变成「COMPLETED + 空输出」的静默假成功。原始 traceback 已在 adapter
    被吞(其契约如此),kind+message 是能拿到的全部真因,如实携带。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind or ""
        self.message = message or ""


@dataclass
class TurnOutcome:
    """单轮模型调用结果。"""
    text: str
    tool_uses: list[ToolUseBlock]
    raw_events: list  # 给上层流式透传
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ToolFailedError:
    """工具执行失败(本轮内至少一个 is_error=True)。"""
    pass


# ---- 决策辅助 ----

def _serialize_results_for_model(results: list[ToolResult]) -> list[dict]:
    """把 ToolResult 序列化成 Anthropic Messages 协议回灌消息。

    Anthropic 协议:tool_result 不是独立 role,而是 user 消息的 content blocks。
    一条 user 消息可以含多个 tool_result block(对应一轮多 tool_use)。
    我们把一轮的所有 result 打包成一条 user 消息(content 是 list)。

    **关键约束**:tool_result.content 必须是 string 或 content blocks 列表
    (Anthropic Messages API 严格规定;MiniMax 等兼容端点会因 dict 直接返 400)。
    我们统一把 content JSON 序列化成字符串 —— 模型在 message 里能完整看到
    原 dict 结构,不是被强制改写。
    """
    if not results:
        return []
    import dataclasses
    import json as _json

    def _to_string(v) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float, bool)):
            return _json.dumps(v, ensure_ascii=False)
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            return _json.dumps(dataclasses.asdict(v), ensure_ascii=False)
        # list / dict: 序列化成 JSON 字符串
        try:
            return _json.dumps(v, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(v)

    blocks: list[dict] = []
    for r in results:
        if r.is_error:
            content = _json.dumps({"error": True, "reason": r.error_reason},
                                  ensure_ascii=False)
        else:
            content = _to_string(r.content)
        blocks.append({
            "type": "tool_result",
            "tool_use_id": r.tool_use_id,
            "content": content,
        })
    return [{"role": "user", "content": blocks}]


def _synthesize_missing_tool_results(
    state: LoopState,
) -> list[ToolResult]:
    """中断时:为已发出但未执行的 tool_use 补合成 tool_result(AC6,HR-11)。

    Anthropic 协议:assistant 消息的 tool_use 在 content blocks 里,不是 tool_calls 字段。
    """
    synth: list[ToolResult] = []
    for m in state.messages:
        if m.get("role") != "assistant":
            continue
        for blk in m.get("content") or []:
            if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                continue
            tu_id = blk.get("id")
            if not tu_id:
                continue
            # 检查是否已有 tool_result 回应
            already = any(
                mm.get("role") == "user"
                and any(
                    b.get("type") == "tool_result" and b.get("tool_use_id") == tu_id
                    for b in (mm.get("content") or [])
                    if isinstance(b, dict)
                )
                for mm in state.messages
            )
            if not already:
                synth.append(ToolResult(
                    tool_use_id=tu_id,
                    name=blk.get("name", "?"),
                    content=None,
                    is_error=True,
                    error_reason="aborted_before_execution",
                ))
    return synth


# ---- 内部事件（透传给上层/bus）----

@dataclass
class TextEvent:
    text: str

@dataclass
class ThinkingEvent:
    """reasoning model 的推理增量(独立于答案正文;渲染层折叠展示)。"""
    text: str

@dataclass
class ToolCallEvent:
    block: ToolUseBlock

@dataclass
class ToolResultEvent:
    result: ToolResult

@dataclass
class TerminalEvent:
    reason: Terminal
    run: AtomRun


# ---- 主入口 ----

async def run(
    atom: AtomSpec,
    input: dict,
    token: CapabilityToken,
    *,
    gateway: GatewayClient,
    tools: dict,  # name -> Tool
    max_turns: int = 50,
    system: Optional[SystemPrompt] = None,
    default_mode: _Mode = _Mode.WORKSPACE_WRITE,  # 默认写优先;READ_ONLY 场景由调用方传
    # loop step4a:上下文治理(每轮调模型前压缩,防 O(n²) 烧 token)。
    # gov_config=None → 整段跳过(0 回归,M0/旧路径行为不变)。
    gov_config: Optional["GovConfig"] = None,
    gov_state: Optional["GovState"] = None,
    summarize: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    context_window: int = 200_000,
    images: Optional[list] = None,  # 多模态:[{data: <base64>, media_type: <mt>}];首条 user 消息带图块
) -> AsyncIterator[Any]:
    """执行一个原子。AsyncGenerator,末事件 = TerminalEvent(reason, run)。

    依赖注入：
      - gateway: 调模型
      - tools: 工具注册(name -> Tool)
      - gov_config/gov_state/summarize/context_window: 上下文治理管线(loop step4a;
        None=不治理)。每轮调模型前跑 govern(microcompact + autocompact),把多轮累积
        的消息压在窗口内 —— 接 docs/modules/context-governance.md 既有管线,非新造。
    """
    state = LoopState()
    # loop step4a:治理状态 per-task 起一次(断路器 + 摘要缓存跨轮复用)
    if gov_config is not None and gov_state is None:
        from karvyloop.context.budget import GovState as _GovState
        gov_state = _GovState()
    # Anthropic 协议要求 messages[].content 是 str 或 list[ContentBlock],不能是 dict。
    # Forge 传进来的 input 是 dict(原 M0 协议),这里序列化成 JSON 字符串(协议兼容)。
    import json as _json
    if isinstance(input, dict):
        user_content = _json.dumps(input, ensure_ascii=False)
    elif isinstance(input, str):
        user_content = input
    else:
        user_content = str(input)
    # 多模态:有图 → 首条 user 消息建成 content 块列表(文本块 + Anthropic 原生图块;
    # openai 系 adapter 会把图块转成 image_url)。无图 → 维持纯字符串(0 回归)。
    if images:
        blocks: list = [{"type": "text", "text": user_content}]
        for im in images:
            data = (im.get("data") if isinstance(im, dict) else "") or ""
            mt = (im.get("media_type") if isinstance(im, dict) else "") or "image/png"
            if data:
                blocks.append({"type": "image",
                               "source": {"type": "base64", "media_type": mt, "data": data}})
        state.messages = [{"role": "user", "content": blocks}]
    else:
        state.messages = [{"role": "user", "content": user_content}]

    started_at = time.time()
    final_reason: Terminal = Terminal.COMPLETED
    tool_calls_log: list[dict] = []
    last_assistant_output: Optional[Any] = None
    # 可观测性②:代码缺陷 fail-loud 上冒时置位 —— finally 不再吐 TerminalEvent
    # (否则半截"COMPLETED/success"假事件先于异常到达消费方 = 误报)。
    fail_loud_crash = False

    try:
        while True:
            # ---- 9 步循环 ----
            # debug trace(KARVYLOOP_EXECUTOR_DEBUG=1)
            import os as _os_ex, sys as _sys_ex
            if _os_ex.environ.get("KARVYLOOP_EXECUTOR_DEBUG"):
                print(f"[executor debug] loop start turn={state.turn_count} "
                      f"msgs={len(state.messages)}",
                      file=_sys_ex.stderr)
            # 0) 中断检查
            if state.abort_requested:
                final_reason = (Terminal.ABORTED_TOOLS
                                if state.transition.reason == "ran_tools"
                                else Terminal.ABORTED_STREAMING)
                # 补合成(Anthropic 协议: user 消息 + tool_result blocks)
                synth = _synthesize_missing_tool_results(state)
                for s in _serialize_results_for_model(synth):
                    state.messages.append(s)
                break

            # 1) 上下文治理(loop step4a:接 context-governance 管线,每轮调模型前压缩)
            if gov_config is not None:
                from karvyloop.context.budget import BlockingLimitError as _BLE
                from karvyloop.context.pipeline import govern as _govern
                try:
                    state.messages = await _govern(
                        state.messages, gov_config, gov_state, summarize,
                        context_window=context_window,
                    )
                except _BLE as e:
                    state.transition = Transition(reason="blocking_limit",
                                                  extra={"error": str(e)})
                    final_reason = Terminal.BLOCKING_LIMIT
                    break
            # 2) 解析 model
            scope = ResolveScope(atom_model=atom.model)
            try:
                model_ref = gateway.resolve_model(scope)
            except Exception as e:
                # 白名单式分类(可观测性②):解析类失败(没有可用模型/配置不合法)= 基础能力没了
                # → infra-dead,**不是** token 预算;上层据此 fail-loud 标 infra,不让 role 白重规划
                # (docs/02 §15)。TypeError/AttributeError 等**代码缺陷**绝不吞成 infra —— 上冒原始异常链。
                from .terminal import classify_resolve_exception
                reason = classify_resolve_exception(e)
                if reason is None:
                    fail_loud_crash = True
                    raise
                state.transition = Transition(reason="resolve_model_failed",
                                              extra={"error": f"{type(e).__name__}: {e}"})
                final_reason = reason
                break

            # 3) 调模型 + 4) 边流边收集
            assistant_text = ""
            assistant_tool_uses: list[ToolUseBlock] = []
            input_tokens = 0
            output_tokens = 0
            cache_read = 0
            cache_write = 0
            cost = 0.0
            try:
                events: list = []
                async for ev in gateway.complete(
                    state.messages,
                    _tools_to_schemas(tools),
                    model_ref,
                    system=system,
                ):
                    events.append(ev)
                    tname = type(ev).__name__
                    if tname == "TextDelta":
                        assistant_text += getattr(ev, "text", "")
                        yield TextEvent(text=getattr(ev, "text", ""))
                    elif tname == "ThinkingDelta":
                        # M3 等 reasoning model 的推理块。独立 ThinkingEvent(不混进答案正文)→
                        # 渲染层折叠展示;不识别 ThinkingEvent 的消费者(forge)回退 [thinking] 内联
                        # (保旧行为=不静默死,0 回归)。
                        thinking_text = getattr(ev, "text", "")
                        if thinking_text:
                            yield ThinkingEvent(text=thinking_text)
                    elif tname == "ToolUseStart":
                        bid = ToolUseBlock(
                            id=getattr(ev, "id", ""),
                            name=getattr(ev, "name", ""),
                            input={},
                        )
                        assistant_tool_uses.append(bid)
                        # 9.4:不在此 yield —— ToolUseStart 时 input 还没流完(={}),
                        # 渲染层 tool 卡会拿到空参数。移到 ToolUseStop(input 完整)后再 yield。
                    elif tname == "ToolUseDelta":
                        # partial_json 暂不解析;M1+ 真接 anthropic SDK 时再解
                        pass
                    elif tname == "ToolUseStop":
                        # 取 stop 时的完整 input,**填好后再** yield ToolCallEvent(带完整 input)
                        _matched = None
                        for bid in assistant_tool_uses:
                            if bid.id == ev.id:
                                bid.input = ev.input or {}
                                _matched = bid
                                break
                        if _matched is not None:
                            yield ToolCallEvent(block=_matched)
                    elif tname == "Usage":
                        input_tokens += getattr(ev, "input_tokens", 0) or 0
                        output_tokens += getattr(ev, "output_tokens", 0) or 0
                        cache_read += getattr(ev, "cache_read", 0) or 0
                        cache_write += getattr(ev, "cache_write", 0) or 0
                    elif tname == "ErrorEvent":
                        # 可观测性②:adapter 流内异常(网络断/超时/HTTP 状态/解析 bug)被归一化成
                        # ErrorEvent 且流随即结束 —— 此前这里没有分支,失败被当"正常结束、无 tool_use"
                        # → COMPLETED + 空输出的静默假成功。重建异常交下方分类(kind 是原异常类名)。
                        raise AdapterStreamError(getattr(ev, "kind", "") or "",
                                                 getattr(ev, "message", "") or "")
                    elif tname == "Done":
                        pass
                state.cumulative_input_tokens += input_tokens
                state.cumulative_output_tokens += output_tokens
                state.cumulative_cost_usd += cost
                # token 账本记账已上移到**唯一咽喉** GatewayClient.complete(forge 也走 gateway.complete,
                # 在那里按 Usage + contextvar source 记一次)→ 这里不再记,否则 forge 双重计数。
                # 本处只累进 state(供 state.cumulative_* 用),不碰账本。
                # 把本轮 assistant 消息入历史(Anthropic 协议: content 是 blocks 列表)
                assistant_content: list[dict] = []
                if assistant_text:
                    assistant_content.append({"type": "text", "text": assistant_text})
                for tu in assistant_tool_uses:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tu.id,
                        "name": tu.name,
                        "input": tu.input,
                    })
                # 无 text 也无 tool_use 的"空"轮也得留痕(否则下一轮断了)
                if not assistant_content:
                    assistant_content = [{"type": "text", "text": ""}]
                state.messages.append({
                    "role": "assistant",
                    "content": assistant_content,
                })
                last_assistant_output = assistant_text or (
                    [tu.input for tu in assistant_tool_uses] if assistant_tool_uses else None
                )
            except AdapterStreamError as e:
                # ErrorEvent 重建的流内异常:kind = 原异常类名 → 按 kind 分类(同一白名单纪律)。
                # 代码缺陷 kind / 4xx 坏请求 → fail-loud 上冒(str(e) 带 "Kind: message" 真因);
                # 传输层/认证/限流/5xx → INFRA_DEAD。
                from .terminal import classify_error_event
                reason = classify_error_event(e.kind, e.message)
                if reason is None:
                    fail_loud_crash = True
                    raise
                state.transition = Transition(reason="model_call_failed",
                                              extra={"error": str(e)})
                final_reason = reason
                break
            except Exception as e:
                # 白名单式分类(可观测性②,本周真痛的治本):只有网络/超时/认证/限流/5xx 才算
                # **网关/网络调不通** = infra-dead(role 重规划同一条路也没用,fail-loud 标 infra,
                # 不进 replan 阶梯,docs/02 §15);预算/上下文天花板(系统有意拒发)= BLOCKING_LIMIT;
                # TypeError/AttributeError/KeyError 等**代码缺陷**(含 400 坏请求 = 请求体/协议 bug)
                # 绝不吞成"模型/网络调不通" —— fail-loud 上冒原始异常链(traceback 由 drive 记进
                # Trace,用户可见文案由上层兜),否则误诊耽误排查(实捕:to_blocks 少 cache kwarg 的
                # TypeError 被报成 infra-dead,整条慢脑全灭还查错方向)。
                from .terminal import classify_model_call_exception
                reason = classify_model_call_exception(e)
                if reason is None:
                    fail_loud_crash = True
                    raise
                state.transition = Transition(reason="model_call_failed",
                                              extra={"error": f"{type(e).__name__}: {e}"})
                # 调模型失败本身不进断路器(避免误判 tool 质量)。
                final_reason = reason
                break

            # 5) 续跑判据：不信 stop_reason,看 tool_use 列表
            if _os_ex.environ.get("KARVYLOOP_EXECUTOR_DEBUG"):
                print(f"[executor debug] after model call: text={assistant_text!r} "
                      f"tool_uses={len(assistant_tool_uses)} events={len(events)}",
                      file=_sys_ex.stderr)
            if not assistant_tool_uses:
                state.transition = Transition(reason="no_tool_use")
                final_reason = Terminal.COMPLETED
                if _os_ex.environ.get("KARVYLOOP_EXECUTOR_DEBUG"):
                    print(f"[executor debug] BREAK: no_tool_use, "
                          f"events were: {[type(e).__name__ for e in events]}",
                          file=_sys_ex.stderr)
                break

            state.transition = Transition(reason="ran_tools",
                                          extra={"n": len(assistant_tool_uses)})
            # 记日志
            for tu in assistant_tool_uses:
                tool_calls_log.append({"id": tu.id, "name": tu.name, "input": tu.input})

            # 6) 跑工具(含 capability gate)
            # 默认 mode 由调用方传(原子执行多写少读,默认 WORKSPACE_WRITE)
            # 返回 (ok, reason):拒绝时把 Deny.message 上浮进 tool_result(诚实 reason,
            # deontic 硬闸/敏感地板拦了什么、为什么,模型看得见才能改道,不是干瞪 capability_denied)。
            async def _cap_check(name: str, inp: dict):
                d = _authorize(_PC(tool=name, input=inp, mode=default_mode,
                                   workspace_root=None))
                if isinstance(d, _Allow):
                    return True, ""
                return False, (getattr(d, "message", "") or getattr(d, "reason", ""))

            results = await run_tools(assistant_tool_uses, tools, token,
                                       capability_check=_cap_check)
            for r in results:
                yield ToolResultEvent(result=r)

            # 7) 回灌(任何 tool 都必须有对应 result,包括 is_error)
            for msg in _serialize_results_for_model(results):
                state.messages.append(msg)

            # 8) max_turns
            state.turn_count += 1
            if state.turn_count >= max_turns:
                state.transition = Transition(reason="max_turns",
                                              extra={"turn": state.turn_count})
                final_reason = Terminal.MAX_TURNS
                break

            # 9) 断路器：连续失败计数
            n_err = sum(1 for r in results if r.is_error)
            if n_err > 0:
                state.consecutive_failures += 1
            else:
                state.consecutive_failures = 0
            if state.consecutive_failures >= CIRCUIT_OPEN_THRESHOLD:
                state.transition = Transition(
                    reason="circuit_open",
                    extra={"consecutive_failures": state.consecutive_failures},
                )
                final_reason = Terminal.CIRCUIT_OPEN
                break

            # 下一轮(state 是 mutable,无需换;但 turn_count 已 +1)
            state = LoopState(
                messages=state.messages,
                turn_count=state.turn_count,
                transition=state.transition,
                recovery_flags=state.recovery_flags,
                consecutive_failures=state.consecutive_failures,
                cumulative_input_tokens=state.cumulative_input_tokens,
                cumulative_output_tokens=state.cumulative_output_tokens,
                cumulative_cost_usd=state.cumulative_cost_usd,
                abort_requested=state.abort_requested,
            )

    finally:
        # 可观测性②:代码缺陷 fail-loud 路径**不吐** TerminalEvent —— 原始异常链直接上冒
        # (注意:此处绝不能 return —— finally 里 return 会吞掉在飞的异常,恰是要治的病),
        # 消费方(forge → drive → 桥)在各自边界记真因/兜文案,不发半截"成功"假事件。
        if not fail_loud_crash:
            # 写 AtomRun(末事件给上层去存 Trace;结晶 observe 用)
            # output 必须是 dict(str/list 转 dict 的兼容做法:list→{"items":...},str→{"text":...})
            def _coerce_output(v: Any) -> Optional[dict]:
                if v is None:
                    return None
                if isinstance(v, dict):
                    return v
                if isinstance(v, str):
                    return {"text": v}
                if isinstance(v, list):
                    return {"items": v}
                return {"value": str(v)}

            run_obj = AtomRun(
                atom_id=atom.id,
                input=input,
                output=_coerce_output(last_assistant_output),
                success=(final_reason == Terminal.COMPLETED),
                tool_calls=tool_calls_log,
                trace_ref=f"trace://{atom.id}/{int(started_at*1000)}",
                ts=started_at,
                terminal=final_reason.value,  # 终止语义上冒(尽责下属阶梯据此决定 replan vs fail-loud)
            )
            yield TerminalEvent(reason=final_reason, run=run_obj)


# ---- 工具 → schema(M0 占位;真转换在 registry)----

def _tools_to_schemas(tools: dict) -> list[dict]:
    """工具 → Anthropic 原生 tool schema。

    必须是 {name, description, input_schema} —— MiniMax / Anthropic 都拒
    OpenAI 风格的 {type:"function", function:{...}}("invalid params, function
    name or parameters is empty" 2013)。Anthropic 兼容端点共享协议。
    """
    out: list[dict] = []
    for name, t in tools.items():
        out.append({
            "name": name,
            "description": getattr(t, "description", ""),
            "input_schema": getattr(t, "parameters", {"type": "object", "properties": {}}),
        })
    return out
