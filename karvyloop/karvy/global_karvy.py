"""global_karvy — 全局小卡:**渠道无关**的对话接口(Hardy 2026-06-25)。

为什么有这层:未来语音聊天**只对接全局 Karvy**。所以只要把"跟全局小卡说话"抽象干净,
任何渠道(TUI / 语音 / web)都只是壳:
  - 一个渠道 = 拿一个 `GlobalKarvy` + 把输入喂 `ask()`、把看板喂 `dashboard()`。
  - 语音接入 = (语音→文字) → `ask(text)` → (文字→语音)。**抽象在这,渠道只是 I/O。**

`ask` 用**小卡人格**(l0 个人/系统场的协调者)驱动 —— 这正是"全局 Karvy",不是裸编码 agent。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from karvyloop.workbench.main_loop_bridge import DriveOutcome, drive_in_tui


class GlobalKarvy:
    """全局小卡的对话接口。从一套运行时(main_loop + 对话编排器 + runtime_kwargs)构造,
    渠道只调 ask()/dashboard()。TUI 现在用它;语音以后用同一个。"""

    def __init__(self, *, main_loop: Any, conversation_manager: Any = None,
                 runtime_kwargs: Optional[dict] = None,
                 dashboard_fn: Optional[Callable[[], dict]] = None,
                 governance_fn: Optional[Callable[[str], str]] = None) -> None:
        self._ml = main_loop
        self._mgr = conversation_manager
        self._rk = runtime_kwargs or {}
        self._dashboard_fn = dashboard_fn
        # Step 0(a):你的决策标准/知识 装配器(intent → governance)。接了 → 语音/TUI 也认你的标准,
        # 不再认知失明;None → 退化成只有 ctx+人格(旧行为)。
        self._governance_fn = governance_fn

    @property
    def ready(self) -> bool:
        """能不能真对话(接了 LLM)。没接 → 渠道该提示"先 karvyloop init"。"""
        return self._ml is not None and self._rk.get("gateway") is not None

    async def ask(self, intent: str, *, on_event=None) -> DriveOutcome:
        """跟全局小卡说一句 → 回复。小卡人格 + 当前对话 ctx + 记一轮(任何渠道同一条路)。

        on_event:逐字流式回调(语音/TUI 想边出边读时用);None=批量。
        """
        from karvyloop.coding.persona import build_karvy_persona_prompt
        mgr = self._mgr
        ctx = mgr.context_view() if mgr is not None else None
        ws = self._rk.get("workspace_root", "/")
        # intent 透传:建 agent 类意图 → 注入系统自我认知(语音/TUI 同样能指导建 agent)
        persona = build_karvy_persona_prompt(cwd=ws, intent=intent)   # ← 这就是"全局 Karvy",不是裸 forge
        # Step 0(a):语音/TUI 也要认你的标准 —— 接了 governance_fn 就装配(prealign+知识召回),不再认知失明。
        governance = ""
        if self._governance_fn is not None:
            try:
                governance = self._governance_fn(intent) or ""
            except Exception:
                governance = ""
        outcome = await drive_in_tui(intent, self._ml, ctx=ctx, persona=persona,
                                     governance=governance, on_event=on_event, **self._rk)
        if mgr is not None and not getattr(outcome, "error", ""):
            try:
                mgr.record_turn(intent, outcome.text or "",
                                brain=outcome.brain.value, task_id=outcome.task_id)
            except Exception:
                pass
        return outcome

    def dashboard(self) -> dict:
        """看板快照(任务/谁在忙/统计)。渠道按需渲染;没接 dashboard_fn → 空。"""
        if self._dashboard_fn is None:
            return {}
        try:
            return self._dashboard_fn() or {}
        except Exception:
            return {}


__all__ = ["GlobalKarvy"]
