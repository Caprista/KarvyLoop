"""WorkbenchApp — Textual App 主循环(M3 批 3a + 3b + 批 5)。

设计:plans/snoopy-singing-sunbeam.md §3 + §批 5。

边界(K 铁律):
- 主循环:`while running: await asyncio.sleep(0.1) + pump subscribe_async`
- A1:不**直**接**构**造 Envelope(只走 `decision_to_envelope` 工厂)
- K5:不**调**用 Courier.send(走 ProposalModal → decision_to_envelope;`by=()`)
- K4:不**调**用 domain.apply_*(grep 验证)
- L0 不可写:envelope ARRIVED 时**不**触发任何修改动作
- 批 5:H2AInput.on_submit 走 `drive_in_tui` 调 MainLoop.drive(主循环驱动)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from textual.app import App

from karvyloop.a2a import Envelope, EnvelopeType
from karvyloop.cli.main_loop import Brain, MainLoop
from karvyloop.domain import Address
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.karvy.h2a import (
    H2A_ACCEPT,
    H2A_DEFER,
    H2A_REJECT,
    H2ADecision,
    UserInputFn,
    decision_to_envelope,
    h2a_decide,
)
from karvyloop.workbench.binding import EnvelopeArrived
from karvyloop.workbench.chat_history import (
    get_chat_history,
    push_chat_log_line,
)
from karvyloop.workbench.main_loop_bridge import DriveOutcome  # drive 现走 GlobalKarvy.ask
from karvyloop.workbench.screens.observer import ObserverScreen
from karvyloop.workbench.screens.proposal import ProposalModal
from karvyloop.workbench.snapshot import (
    make_snapshot_with_mainloop,
    snapshot_for_widgets,
    WidgetSnapshot,
)

logger = logging.getLogger(__name__)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class WorkbenchApp(App):
    """KarvyLoop Workbench 主 App。

    Args:
        workbench: 小卡工作台观察者(只读,K4)。
        user_address: 用户 Address(K5:用户拍板的来源)。
        h2a_input: H2A 决策回调(M3 拍 3b 注入 ProposalModal;None 时默认 DEFER)。
        serve_mode: True 时不进入主循环(让 textual-serve 接管)。
        poll_interval: 事件泵间隔(秒,默认 0.1s)。
        main_loop: MainLoop 注入(批 5;H2AInput 提交 intent 走此)。
        runtime_kwargs: 慢脑工厂 kwargs(token/sandbox/gateway/workspace_root/model_ref)。
    """

    TITLE = "KarvyLoop · 协作舞台"
    SUB_TITLE = "Workbench v0 · Textual TUI"

    def __init__(
        self,
        *,
        workbench: WorkbenchObserver,
        user_address: Address,
        h2a_input: Optional[UserInputFn] = None,
        serve_mode: bool = False,
        poll_interval: float = 0.1,
        main_loop: Optional[MainLoop] = None,
        runtime_kwargs: Optional[dict] = None,
        conversation_manager: Optional[object] = None,
    ) -> None:
        super().__init__()
        self._workbench = workbench
        self._user_address = user_address
        # 拍 9.2d:对话编排器(可 None)— 喂 ctx/governance + 每轮 record(CV-8/10/14)
        self._conversation_manager = conversation_manager
        # K5:user_input 决定 H2A 决策走 ProposalModal(用户拍板)还是 default DEFER
        self._h2a_input = h2a_input or self._proposal_user_input
        self._serve_mode = serve_mode
        self._poll_interval = poll_interval
        self._pump_task: Optional[asyncio.Task] = None
        self._iter: Optional[object] = None
        # 批 5:MainLoop 注入 + 慢脑工厂 kwargs
        self._main_loop = main_loop
        self._runtime_kwargs = runtime_kwargs or {}
        # 批 5:TUI 进程内 MainLoop 状态(snapshot 喂给 widget)
        self._crystallized_skills: list[str] = []
        self._last_fast_brain_skill: str = ""
        self._last_drive_text: str = ""
        # 批 8.5-A:错误/输入回显独立通道(修"石沉大海")
        self._last_error: str = ""
        self._last_intent: str = ""
        self._initial_snap: Optional[WidgetSnapshot] = None
        # 渠道无关的「全局小卡」对话接口(Hardy):TUI 是它的一个壳,语音以后用同一个。
        # 关键:走小卡人格(不是裸 forge)—— 这才是"跟全局 Karvy 沟通"。
        from karvyloop.karvy.global_karvy import GlobalKarvy
        self._karvy = GlobalKarvy(main_loop=main_loop, conversation_manager=conversation_manager,
                                  runtime_kwargs=self._runtime_kwargs,
                                  dashboard_fn=self._build_snapshot)   # 看板也走同一接口(语音可复用)

    # ---- 拍 3b 接入:K5 决策闭环 ----

    async def _proposal_user_input(self, prompt: str, user: Address) -> H2ADecision:
        """默认 user_input:推 ProposalModal 模态,等用户拍板(K5 灵魂级)。

        返回的 H2ADecision 走 h2a_decide → decision_to_envelope → A2A 投递。
        **不**经 Courier(避免代发链污染 K5 边界)。
        """
        # proposal_id 从 prompt 里抽(v0 简化:用 prompt 前 8 字符当 id)
        proposal_id = prompt[:8] if prompt else "unknown"
        modal = ProposalModal(proposal_id=proposal_id, summary=prompt, user=user)
        decision: H2ADecision = await self.push_screen_wait(modal)
        return decision

    async def decide_h2a(
        self,
        proposal_id: str,
        proposal_summary: str,
    ) -> H2ADecision:
        """H2A 决策入口(供 PROPOSE envelope 触发时调用)。

        走 h2a_decide(user_input=self._h2a_input) → 返回 H2ADecision。
        """
        return h2a_decide(
            user=self._user_address,
            proposal_id=proposal_id,
            proposal_summary=proposal_summary,
            user_input=self._h2a_input,
            timestamp_fn=_now_ts,
        )

    def envelope_for_decision(
        self,
        decision: H2ADecision,
        to: Address,
    ) -> Optional[Envelope]:
        """把 H2A 决策转 Envelope(K5 工厂)。

        - ACCEPT/REJECT → decision_to_envelope(由=`()`,**不**经 Courier)
        - DEFER → None(不投递)

        K5 验证点:返回 envelope 的 `from_=user_address, by=()`(空 by)。
        """
        if decision.decision == H2A_DEFER:
            return None
        return decision_to_envelope(decision, to, timestamp_fn=_now_ts)

    # ---- 批 5 接入:TUI ↔ MainLoop ----

    def _build_snapshot(self) -> WidgetSnapshot:
        """构造当前 snapshot(含 MainLoop 状态字段 + 批 8.5-A 错误/输入回显)。"""
        return make_snapshot_with_mainloop(
            self._workbench,
            crystallized_skills=tuple(self._crystallized_skills),
            last_fast_brain_skill=self._last_fast_brain_skill,
            last_drive_text=self._last_drive_text,
            last_error=self._last_error,
            last_intent=self._last_intent,
        )

    # ---- 批 8.5-A:聊天日志 API(供 h2a_input / 8.5-C console 复用) ----

    def push_chat_log_line(self, role: str, text: str, events: list | None = None) -> None:
        """追加一条聊天日志(进程级 ring buffer + LChatLog widget 同步刷新)。

        events:9.4 agent 结构化回合(text/tool_call/tool_result/terminal),可选。
        """
        ts = _now_ts()
        # 1. 写 ring buffer(8.5-C console 复用)
        push_chat_log_line(role, text, ts, events)
        # 2. 推 LChatLog widget(若已挂载)
        try:
            from karvyloop.workbench.widgets.l_chat_log import ChatLine
            self.post_message(ChatLine(role=role, text=text, ts=ts))
        except Exception:
            # headless 测试时无 widget 树,跳过
            pass

    def get_chat_history(self) -> list[dict]:
        """返当前进程级聊天历史(供 8.5-C console `GET /api/chat_history` 用)。"""
        return get_chat_history()

    async def submit_intent(self, intent: str) -> None:
        """H2AInput 提交 intent → 调 drive_in_tui → 更新 snapshot + 重挂屏。

        批 5:R3-async 包装走 `asyncio.to_thread` 防 asyncio.run 嵌套。
        批 8.5-A:拆 `last_drive_text` / `last_error` / `last_intent` 三状态,失败不污染慢脑槽;
                 `main_loop=None` 时显式 system 提示("请先 karvyloop init"),不静默 swallow。
        """
        if not self._main_loop:
            # 8.5-A 修 silent-fail death-spiral:不再只 logger.warning,推到 UI
            msg = (
                "MainLoop 未注入 — 请先 `karvyloop init` 生成 ~/.karvyloop/config.yaml,"
                " 或用 --config 指向已有配置"
            )
            self._last_error = f"⚠ {msg}"
            self._last_intent = intent
            self.push_chat_log_line("system", msg)
            try:
                self.pop_screen()
            except Exception:
                pass
            self.push_screen(ObserverScreen(self._build_snapshot()))
            return
        if not intent.strip():
            return
        # 8.5-A 缺陷 #1 修:先把用户的 intent 写进聊天日志(input echo)
        self.push_chat_log_line("user", intent)
        self._last_intent = intent
        # 跟**全局小卡**说话(渠道无关接口):小卡人格 + ctx + 记一轮都在 ask() 里。
        # 语音以后走同一个 self._karvy.ask —— 这就是抽象的意义。
        mgr = self._conversation_manager
        outcome: DriveOutcome = await self._karvy.ask(intent)
        if outcome.error:
            # 8.5-A 缺陷 #2 修:错误独立通道,**不**再塞 `last_drive_text` 误导"🤖 慢脑输出"
            self._last_error = f"⚠ {outcome.error}"
            self._last_drive_text = ""
            self.push_chat_log_line("system", f"⚠ {outcome.error}")
        else:
            self._last_error = ""
            self._last_drive_text = outcome.text or "(empty result)"
            self.push_chat_log_line("agent", self._last_drive_text)
            # 这一轮已在 GlobalKarvy.ask() 里 record_turn(CV-10),这里不重复记。
        if outcome.crystallized and outcome.skill_name:
            if outcome.skill_name not in self._crystallized_skills:
                self._crystallized_skills.append(outcome.skill_name)
            self.push_chat_log_line("system", f"🔔 已结晶: {outcome.skill_name}")
        if outcome.fast_brain_hit and outcome.skill_name:
            self._last_fast_brain_skill = outcome.skill_name
        # 重挂屏(简化实现:v0 整屏重 compose;v1 改 reactive)
        try:
            self.pop_screen()
        except Exception:
            pass
        self.push_screen(ObserverScreen(self._build_snapshot()))

    # ---- 拍 3a 接入:主循环 ----

    def on_mount(self) -> None:
        """挂载 ObserverScreen + 启动事件泵。"""
        snap = self._build_snapshot()
        self._initial_snap = snap
        self.push_screen(ObserverScreen(snap))

        if self._serve_mode:
            return  # serve 模式让 textual-serve 接管

        try:
            self._iter = self._workbench.subscribe_async()
        except Exception as e:
            logger.warning(f"subscribe_async 初始化失败: {e}")
            return
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """事件泵:每 poll_interval 拉一次 EnvelopeArrived。"""
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                if self._iter is None:
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"pump 异常: {e}")
                await asyncio.sleep(1.0)

    def on_unmount(self) -> None:
        """卸载时取消 pump 任务。"""
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()

    async def on_envelope_arrived(self, message: EnvelopeArrived) -> None:
        """订阅事件 → 刷新 ObserverScreen(拍 3b v0:仅日志)。"""
        env: Envelope = message.envelope
        logger.info(f"envelope arrived: type={env.type} from={env.from_}")
        # 拍 3b 后续:PROPOSE → 推 ProposalModal(单 proposal 锁)
        # v0:L0 不可写 + K3 只读,先日志不触发动作