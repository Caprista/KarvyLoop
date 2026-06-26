"""ProposalModal — H2A 决策弹窗(M3 批 3b)。

设计:plans/snoopy-singing-sunbeam.md §3 拍 3b。

**K5 灵魂铁律**(UI 不能破):
- ACCEPT 按钮由**用户**点击(AI 不产生 ACCEPT)
- REJECT 必须填 reason(A8 边界)
- DEFER = 关闭模态(等下次有 proposal 再弹)
- 返回的 H2ADecision 经 `decision_to_envelope` 转 envelope(经 App 层调,**不**经 Courier)
- 返回 envelope 的 `from_=user_address, by=()`(空 by,不是代发链)

**两道墙 UI 视觉化**(拍 3b 落地):
- 模态打开期间主屏禁用(单 proposal 锁)
- 模态框显式标注"这是 H2A 决策 · 你的决定" banner
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from karvyloop.domain import Address
from karvyloop.karvy.h2a import (
    H2A_ACCEPT,
    H2A_DEFER,
    H2A_REJECT,
    H2ADecision,
)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ProposalModal(ModalScreen[H2ADecision]):
    """H2A 决策模态(K5 灵魂级:用户拍板)。

    Args:
        proposal_id: 提案 ID(从 envelope.payload 抽)。
        summary: 提案摘要(展示给用户)。
        user: 用户 Address(必**须** role='user',由 h2a_decide 校验)。
    """

    DEFAULT_CSS = """
    ProposalModal {
        align: center middle;
    }
    #proposal-frame {
        width: 70;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }
    #banner {
        background: $warning;
        color: $text;
        padding: 0 1;
        margin-bottom: 1;
    }
    #summary {
        height: auto;
        padding: 1 0;
        margin-bottom: 1;
    }
    #reason-input {
        margin-bottom: 1;
    }
    #button-row {
        height: auto;
        align: right middle;
    }
    #button-row Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        ("escape", "defer", "DEFER"),
    ]

    def __init__(
        self,
        proposal_id: str,
        summary: str,
        user: Address,
    ) -> None:
        super().__init__()
        self.proposal_id = proposal_id
        self.summary = summary
        self._user = user
        self._reason: str = ""

    def compose(self) -> ComposeResult:
        with Container(id="proposal-frame"):
            # 第二道墙:显式标注"这是 H2A,你的决定"
            yield Static(
                "🚦 这是 H2A 决策 · 你的决定(K5 灵魂级 · AI 不产生 ACCEPT)",
                id="banner",
            )
            yield Static(f"[b]提案[/b]: {self.summary}", id="summary")
            yield Static(f"[dim]提案 ID: {self.proposal_id}[/dim]")
            yield Static(f"[dim]决策者: {self._user.role}/{self._user.agent_id}[/dim]")
            yield Input(placeholder="REJECT 时必填 reason...", id="reason-input")
            with Horizontal(id="button-row"):
                yield Button("✅ ACCEPT", id="btn-accept", variant="success")
                yield Button("❌ REJECT", id="btn-reject", variant="error")
                yield Button("⏸ DEFER", id="btn-defer", variant="default")

    @on(Button.Pressed, "#btn-accept")
    def _on_accept(self) -> None:
        # K5:UI 上 ACCEPT = 用户动作 → 返 H2ADecision(ACCEPT)
        self.dismiss(H2ADecision(
            user_address=self._user,
            proposal_id=self.proposal_id,
            decision=H2A_ACCEPT,
            reason="",
            timestamp=_now_ts(),
        ))

    @on(Button.Pressed, "#btn-reject")
    def _on_reject(self) -> None:
        reason = self.query_one("#reason-input", Input).value.strip()
        # A8 边界:REJECT 必须有 reason
        if not reason:
            # 不 dismiss,清空输入框聚焦(让用户重填)
            reason_input = self.query_one("#reason-input", Input)
            reason_input.value = ""
            reason_input.placeholder = "⚠ REJECT 必须填 reason(再试一次)..."
            return
        self.dismiss(H2ADecision(
            user_address=self._user,
            proposal_id=self.proposal_id,
            decision=H2A_REJECT,
            reason=reason,
            timestamp=_now_ts(),
        ))

    @on(Button.Pressed, "#btn-defer")
    def _on_defer(self) -> None:
        # DEFER = 关闭模态(无后续 envelope)
        self.dismiss(H2ADecision(
            user_address=self._user,
            proposal_id=self.proposal_id,
            decision=H2A_DEFER,
            reason="",
            timestamp=_now_ts(),
        ))

    @on(Input.Submitted, "#reason-input")
    def _on_reason_submitted(self, event: Input.Submitted) -> None:
        """Enter 在 reason 输入框 → 触发 REJECT(带 reason)。"""
        self._reason = event.value.strip()
        if self._reason:
            self._on_reject()
        # 空 reason 时不动(让用户重填)

    def action_defer(self) -> None:
        """Esc 键 → DEFER(等同按钮)。"""
        self._on_defer()