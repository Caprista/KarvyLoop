"""Forge = 原子运行时的 coding 预设（coding/forge.py）。

规格：docs/modules/forge.md §2.1。
**关键纪律**:Forge 不另起 ReAct 循环,直接调 atoms.executor.run;
它只提供 coding 工具集 / 提示词 / 会话 / NDJSON 薄封装(HR + spec 边界)。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from karvyloop.atoms import (
    Terminal,
    TerminalEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    run as atom_run,
)
from karvyloop.atoms.orchestration import ToolResult
from karvyloop.gateway import GatewayClient
from karvyloop.sandbox import Sandbox
from karvyloop.schemas import AtomRun, CapabilityToken
from karvyloop.schemas.skill import EphemeralTool

from .filestate import FileState
from .ndjson import NdjsonEmitter
from .prompt import build_coding_prompt
from .session import ForgeSession
from .tools import make_coding_tools
from .tools import CodingResult


@dataclass
class RunResult:
    """forge.generate_and_run 的返回包装。"""
    tool: EphemeralTool
    terminal: Terminal
    run: AtomRun
    text: str = ""


def _coding_result_to_payload(r: CodingResult) -> Any:
    """CodingResult → 模型可读的 tool_result.content。"""
    if r.ok:
        return r.payload
    return {"ok": False, "error_code": r.error_code, "message": r.error_message}


def _make_summarizer(gateway: GatewayClient, model_ref: str):
    """构造 autocompact 用的摘要函数(loop step4a):把 middle 段喂模型 → 摘要文本。

    用同一个 gateway/model 直接 complete(无工具),收集 TextDelta。失败由 autocompact
    的 HR-3 断路器兜(record_failure → 多次失败开断路器,不反复烧 token)。
    """
    async def _summarize(middle: list[dict]) -> str:
        parts: list[str] = []
        for m in middle:
            role = m.get("role", "?")
            c = m.get("content", "")
            if isinstance(c, list):
                # 关键:tool_use / tool_result block 没有 "text" 键 —— 若只取 "text" 会把
                # 工具历史(文件路径/命令/输出)渲染成空 → 摘要丢掉 autocompact 本要保的东西。
                rendered: list[str] = []
                for b in c:
                    if not isinstance(b, dict):
                        rendered.append(str(b))
                    elif b.get("type") == "tool_use":
                        rendered.append(f"tool_use {b.get('name', '')}({b.get('input', '')})")
                    elif b.get("type") == "tool_result":
                        rendered.append(f"tool_result[{b.get('tool_use_id', '')}]: {b.get('content', '')}")
                    elif "text" in b:
                        rendered.append(str(b["text"]))
                    else:
                        rendered.append(str(b))
                c = " ".join(rendered)
            parts.append(f"[{role}] {c}")
        prompt = (
            "Summarize the following agent conversation segment concisely, "
            "preserving every fact, decision, file path, command, and task-state "
            "needed to continue the work. Output only the summary.\n\n"
            + "\n".join(parts)
        )
        out = ""
        async for ev in gateway.complete(
            [{"role": "user", "content": prompt}], [], model_ref, system=None
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
        return out.strip() or "(summary unavailable)"

    return _summarize


class _RegistryToolAgentAdapter:
    """registry.build_tool 产的 Tool(`call(inp, token, sandbox)` 的 dataclass)→ agent 执行器的
    CodingTool 形状(`async __call__(inp)` + `parameters` + `is_concurrency_safe(inp)`)。

    **为什么必须有这层**(真模型验证抓到的缝):执行器 `_run_one` 调 `await tool(inp)`、
    `_tools_to_schemas` 读 `t.parameters` —— registry Tool 两个都不满足 → 模型每次调用
    都吃 `TypeError('Tool' object is not callable)` 的 is_error(工具"看得见、调不动")。
    create_atom / instantiate_domain_template 等 build_tool 工具挂进 extra_tools 都要过这层。
    token/sandbox 在挂载时绑定(同内置 coding 工具的构造期绑定语义)。
    """

    def __init__(self, tool, token, sandbox):
        self.name = tool.name
        self.description = getattr(tool, "description", "") or tool.name
        self.parameters = getattr(tool, "input_schema", None) or {"type": "object", "properties": {}}
        self._tool = tool
        self._token = token
        self._sandbox = sandbox

    def is_concurrency_safe(self, inp: dict) -> bool:
        try:
            return bool(self._tool.is_concurrency_safe(inp))
        except Exception:
            return False   # fail-closed:判不出当不可并发

    async def __call__(self, inp: dict):
        # 返回值(dict/str)由执行器 _serialize_results_for_model 统一 JSON 化回灌
        return await self._tool.call(inp or {}, self._token, self._sandbox)


def _merge_extra_tools(tools: dict, extra_tools: Optional[dict], *,
                       token=None, sandbox=None) -> None:
    """把额外工具(如 MCP)并入 agent 工具集,并做**搜索源偏好**:

    若注入了"复用你 LLM key 的 web 搜索"(MCP 的 *web_search,如 mcp_minimax_web_search),
    就让内置的 keyless DuckDuckGo `web_search` **让位**(pop 掉)——否则 agent 面对两个搜索工具
    可能挑到更差的那个,白瞎了你配的复用-key 搜索。`web_fetch` 是通用读网页,不受影响保留。
    (外部 Brave/Tavily key 的情形不在这处理:那是 web.py 内 WebSearchTool 自己择优,仍是单一 web_search。)

    另:registry build_tool 产的 Tool(不可调用、接口是 `.call(inp, token, sandbox)`)在这里
    统一包成 CodingTool 形状(_RegistryToolAgentAdapter)—— 已满足协议的(MCP/内置)原样并入。
    """
    if not extra_tools:
        return
    for k, t in extra_tools.items():
        if not callable(t) and callable(getattr(t, "call", None)):
            t = _RegistryToolAgentAdapter(t, token, sandbox)   # registry Tool → CodingTool 形状
        tools[k] = t   # 键已带 mcp_ 前缀,不与内置撞名
    if any(str(k).endswith("web_search") for k in extra_tools):
        tools.pop("web_search", None)   # 复用-key 搜索在场 → keyless DDG 让位


async def generate_and_run(
    intent: str,
    token: CapabilityToken,
    sandbox: Sandbox,
    *,
    gateway: GatewayClient,
    session: Optional[ForgeSession] = None,
    emitter: Optional[NdjsonEmitter] = None,
    renderer: Optional["object"] = None,  # cli.render.Renderer(避免循环 import)
    workspace_root: str = "/",
    model_ref: str = "",
    max_turns: int = 30,
    system_prompt: Optional["object"] = None,  # 9.4e:人格 prompt 覆盖(方案 A);None=默认 coding 提示
    read_only: bool = False,  # loop step3:独立验收者用 —— 只给 read_file + run_command(不给 write/edit)
    enable_compression: bool = False,  # loop step4a:接上下文治理管线(microcompact + autocompact),默认 off=0 回归
    extra_tools: Optional[dict] = None,  # A:注入额外工具(如 MCP 工具),并进内置工具集(键带 mcp_ 前缀,不撞名)
    images: Optional[list] = None,  # 多模态:[{data: base64, media_type}];带进首条 user 消息(需视觉模型)
) -> RunResult:
    """生成并跑一次 coding 任务(薄封装 → atom-executor)。

    流程:
      1. 组装 coding 工具集(read/write/edit/bash,绑 token)
      2. 组装 coding 提示词(static/dynamic/cache_control)
      3. 调 atoms.executor.run(同一个 ReAct 循环,Forge 不另起)
      4. 把 executor 事件透传 + 喂给 NDJSON emitter(若有)或 Renderer(若有)
      5. 末事件包成 RunResult + EphemeralTool
    """
    file_state = FileState()
    tools = make_coding_tools(sandbox, file_state, workspace_root, token=token,
                              read_only=read_only)
    # A:并入 MCP 等外部工具 + 搜索源偏好(复用 key 的搜索优先);registry Tool 在此绑 token/sandbox
    _merge_extra_tools(tools, extra_tools, token=token, sandbox=sandbox)
    # 9.4e 方案 A:有人格 prompt 就用人格(对话优先,要动手才用工具);
    # 没有(如 `karvyloop run` 编码路径)→ 默认 coding 提示(0 回归)。
    sys_prompt = system_prompt if system_prompt is not None else build_coding_prompt(workspace_root)

    if emitter is not None:
        emitter.run_start(workspace=workspace_root, model=model_ref,
                          permission_mode="workspace_write")

    # 构造 atom spec(单次 coding 任务)
    from karvyloop.schemas import AtomSpec
    atom = AtomSpec(
        id=f"forge-{int(time.time()*1000)}",
        kind="task",
        prompt="",  # system prompt 走 system 参数,不在 atom.prompt
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        tools=list(tools.keys()),
        required_capabilities=[],
        model=model_ref or None,
    )

    accumulated_text = ""
    final_terminal: Optional[Terminal] = None
    final_run: Optional[AtomRun] = None

    # loop step4a:上下文治理(对话/长任务多轮累积 → 防 O(n²) 烧 token)。
    # 默认 off;on 时构造 GovConfig + 摘要函数 + 取模型真实窗口。
    gov_config = None
    gov_state = None
    summarize = None
    ctx_window = 200_000
    if enable_compression:
        from karvyloop.context.budget import GovConfig, GovState
        gov_config = GovConfig()
        gov_state = GovState()
        try:
            from karvyloop.gateway import ResolveScope
            _ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
            summarize = _make_summarizer(gateway, _ref)
            _m = gateway.reg.get(_ref)
            ctx_window = getattr(_m, "context_window", 0) or 200_000
        except Exception:
            # 解析失败 → microcompact 仍可跑(不调模型),autocompact 因 summarize=None 跳过
            summarize = None

    # 时效标注(atoms/freshness):任务文本含时效信号(今天/最新/现价/实时…)→ 追加一行
    # 【时效提示】(有 web 工具=必须 web_search 查证;没有=如实说查不到,绝不编)。
    # 放在 forge 这个咽喉:直接聊天/委派/圆桌的慢脑都过这里,一处全覆盖;无信号=原样(0 回归)。
    from karvyloop.atoms.freshness import annotate_task as _annotate_freshness
    _has_web = ("web_search" in tools) or ("web_fetch" in tools)
    run_intent = _annotate_freshness(intent, has_web=_has_web)

    # 域 deontic 确定性硬闸(docs/54 B1):persona 带 deontic_forbid(paradigm_prompt 挂的
    # 机器可读属性)→ 本次 run 期间武装工具闸(authorize step 6.5 真拦交易/删除/外发类
    # + forbid 点名的工具,C-03)。known_tools 传本次 run 的真实工具集(含 MCP 注入)——
    # 点名闸「运行时以真实工具目录为准」,不硬编码清单。
    # forge 是所有 drive/委派/圆桌慢脑的唯一咽喉,一处武装全覆盖;scope 随 with 退出复位,
    # 绝不泄漏到下一次 run。没挂(私聊/CLI/默认 coding 提示)= None → no-op,0 回归。
    from karvyloop.capability.deontic_gate import deontic_scope as _deontic_scope
    from karvyloop.capability.deontic_gate import scope_from_system as _deontic_from_system

    with _deontic_scope(_deontic_from_system(sys_prompt, known_tools=tools.keys())):
        async for ev in atom_run(atom, {"intent": run_intent}, token,
                              gateway=gateway, tools=tools,
                              max_turns=max_turns, system=sys_prompt,
                              gov_config=gov_config, gov_state=gov_state,
                              summarize=summarize, context_window=ctx_window,
                              images=images):
            # 透传渲染: emitter(--json/NDJSON)优先,renderer(人读)次之,都没有就静默累文本
            if emitter is not None:
                pass  # 下面按类型分发
            elif renderer is not None:
                renderer.render(ev)
            if isinstance(ev, TextEvent):
                accumulated_text += ev.text
                if emitter is not None:
                    emitter.assistant_text_delta(ev.text)
            elif isinstance(ev, ThinkingEvent):
                # P4:推理增量 —— 不进答案正文(accumulated_text);emitter 有 thinking 钩子则折叠展示,
                # 否则回退 [thinking] 内联(0 回归:旧 emitter/renderer 仍看得到模型在思考)。
                if emitter is not None:
                    _think = getattr(emitter, "assistant_thinking_delta", None)
                    if callable(_think):
                        _think(ev.text)
                    else:
                        emitter.assistant_text_delta(f"[thinking] {ev.text}")
            elif isinstance(ev, ToolCallEvent):
                if emitter is not None:
                    emitter.turn_start()
                    # 9.4:把工具调用详情(名/输入)发给 emitter —— 渲染层据此出 tool 卡。
                    # 原来只 turn_start(),工具名/输入丢了。duck-type:emitter 无 tool_call 则跳过(0 回归)。
                    _tc = getattr(emitter, "tool_call", None)
                    if callable(_tc):
                        _b = ev.block
                        _tc(id=getattr(_b, "id", ""), name=getattr(_b, "name", ""),
                            input=getattr(_b, "input", {}) or {})
            elif isinstance(ev, ToolResultEvent):
                r: ToolResult = ev.result
                # CodingResult → 序列化(若 payload 里有 CodingResult 实例,转)
                content = r.content
                if isinstance(content, CodingResult):
                    content = _coding_result_to_payload(content)
                if emitter is not None:
                    emitter.tool_result(
                        tool_use_id=r.tool_use_id,
                        is_error=r.is_error,
                        output=content,
                        truncated=False,
                    )
                # 会话落盘(若有)— 内存态保真(spec §2.6)
                if session is not None and not r.is_error:
                    # 内存态保真:此处只记 tool 名 + 摘要,不做脱敏
                    session.append_record({
                        "kind": "tool_call",
                        "tool_use_id": r.tool_use_id,
                        "name": r.name,
                        "ok": True,
                        "output": content,  # 内存态——append_record 内部会脱敏落盘
                    })
            elif isinstance(ev, TerminalEvent):
                final_terminal = ev.reason
                final_run = ev.run
                if emitter is not None:
                    emitter.run_end(ok=(ev.reason == Terminal.COMPLETED),
                                    reason=ev.reason.value)

    assert final_terminal is not None and final_run is not None
    # 末事件再落盘一条
    if session is not None:
        session.append_record({
            "kind": "run_end",
            "terminal": final_terminal.value,
            "ok": (final_terminal == Terminal.COMPLETED),
        })

    # 产出 EphemeralTool(intent → code/command)
    code = f"# generated by forge\n# intent: {intent}\n# (M0: 实际产物由 coding 工具集在沙箱中执行;此处记录 intent 引用)"
    now = time.time()
    et = EphemeralTool(
        id=f"forge-tool-{int(now*1000)}",
        from_intent=intent,
        code=code,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        trace_refs=[final_run.trace_ref],
        created_at=now,
        ttl=3600.0,
    )

    return RunResult(tool=et, terminal=final_terminal, run=final_run, text=accumulated_text)


__all__ = ["generate_and_run", "RunResult"]
