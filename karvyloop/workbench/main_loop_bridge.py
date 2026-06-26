"""TUI ↔ MainLoop 桥(M3+ 批 5)。

设计:plans/snoopy-singing-sunbeam.md §批 5。

R3-async 关键:TUI 在 asyncio loop 内(MainLoop.drive 是同步),`forge_slow_brain_factory`
内部用 `asyncio.run` 同步化 forge —— **会嵌套爆**。本模块在 TUI 上下文中用
`asyncio.to_thread` 包装 `MainLoop.drive`,让 `forge_slow_brain_factory` 的 `asyncio.run`
跑在独立线程,合法。

借:Q5 自造≠闭门造车 — 只自造本桥,所有主链逻辑借 `MainLoop` / `forge_slow_brain_factory`。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Optional

from karvyloop.cli.main_loop import Brain, MainLoop
from karvyloop.cli.run_loop import forge_slow_brain_factory  # noqa: F401 — patched in tests

logger = logging.getLogger(__name__)


@dataclass
class DriveOutcome:
    """TUI 视角的 drive 结果(snapshot 喂给 widget 用)。"""
    intent: str
    brain: Brain
    text: str
    skill_name: str
    fast_brain_hit: bool
    crystallized: bool
    error: Optional[str] = None
    task_id: str = ""          # 拍 9.1d:供 ConversationManager.record_turn 回查 trace
    ctx_dependent: bool = False  # 拍 9.1d:本句是否被上下文依赖门判为强依赖
    events: list = dataclass_field(default_factory=list)  # 9.4:结构化渲染事件(text/tool_call/tool_result/terminal)


async def drive_in_tui(
    intent: str,
    ml: MainLoop,
    *,
    token: Any,
    sandbox: Any,
    gateway: Any,
    workspace_root: str,
    model_ref: str = "",
    ctx: object = None,
    governance: str = "",
    persona: object = None,   # 9.4e 方案 A:人格 system prompt(CodingPrompt);None=默认 coding
    scope: Optional[str] = None,   # brick3+:场-scoped 召回/结晶(None=用 ml.scope,0 回归)
    on_event: Optional[Any] = None,  # P4 逐字流式:每个 render 事件实时回调(worker 线程触发);None=旧批量
    mcp_tools: Any = None,   # A:console 启动连好的 MCP 工具(随 runtime_kwargs splat 进来);并进 agent 工具集
    fresh: bool = False,   # True=一次性步骤(workflow/圆桌):跳过 recall+observe+结晶(防跨轮串味)
    images: Any = None,   # 多模态:[{data, media_type}];带进首条 user 消息(需视觉模型)
) -> DriveOutcome:
    """在 TUI asyncio loop 里跑 MainLoop.drive。

    R3-async 关键:`asyncio.to_thread` 把同步 `MainLoop.drive` 跑在独立线程,
    线程内 `forge_slow_brain_factory` 的 `asyncio.run` 是新 loop,合法不嵌套。

    拍 9.1d:`ctx`(当前对话只读上下文)透传给 drive —— 上下文依赖门 + 慢脑消解多轮。
    拍 9.2b:`governance`(业务域 value.md,CV-14)烤进慢脑闭包。默认空 = 旧行为(0 回归)。
    """
    def _run_drive() -> DriveOutcome:
        try:
            # 9.4:渲染事件收集器 —— forge 把 text/tool_call/tool_result/terminal 顺序攒进它,
            # drive 后随 DriveOutcome.events 下发给 UI 按类型渲染。全在本 worker 线程内同步收集,
            # 不跨 thread/loop(逐字流式 = P1)。快脑命中时 forge 不跑 → events 空 → UI 回退 text。
            from karvyloop.coding.render_events import RenderEventCollector
            collector = RenderEventCollector(on_event=on_event)  # P4:接逐字流式回调
            # forge_slow_brain_factory 在模块顶层导入 — 测试通过 patch
            # `karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory` 注入桩
            slow_brain = forge_slow_brain_factory(
                token=token, sandbox=sandbox, gateway=gateway,
                workspace_root=workspace_root, model_ref=model_ref,
                governance=governance, emitter=collector,
                persona=persona,  # 9.4e 方案 A:人格 prompt 透传到 forge
                mcp_tools=mcp_tools,  # A:连好的 MCP 工具并进 agent 工具集(知识库没命中 → 搜/调外部)
                images=images,  # 多模态:首条 user 消息带图块
            )
            result = ml.drive(intent, slow_brain=slow_brain, ctx=ctx, scope=scope, fresh=fresh)
            # 审计修(2026-06-21):drive 后跑技能维护(evict 淘汰旧技能 + improve 把用户纠正
            # 写回 SKILL.md)。此前 background_review **在生产从没被调** → 技能既不淘汰也不进化。
            # 纯本地(评分+regex+写文件,无 LLM/token),best-effort 不拖垮回复。
            try:
                ml.background_review()
            except Exception:
                pass
            return DriveOutcome(
                intent=intent,
                brain=result.brain,
                text=result.text,
                skill_name=result.skill_name,
                fast_brain_hit=result.fast_brain_hit,
                crystallized=result.crystallized,
                task_id=result.task_id,
                ctx_dependent=result.ctx_dependent,
                events=list(collector.events),
            )
        except Exception as e:
            logger.exception(f"MainLoop.drive 异常: {e}")
            return DriveOutcome(
                intent=intent,
                brain=Brain.SLOW,
                text="",
                skill_name="",
                fast_brain_hit=False,
                crystallized=False,
                error=str(e),
            )

    return await asyncio.to_thread(_run_drive)


__all__ = ["DriveOutcome", "drive_in_tui"]
