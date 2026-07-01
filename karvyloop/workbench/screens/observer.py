"""ObserverScreen — L0+L1+L2 主屏(M3 批 3)。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Header

from karvyloop.workbench.snapshot import WidgetSnapshot
from karvyloop.workbench.widgets import (
    H2AInput,
    L0TopBar,
    L1DomainDetail,
    L2Board,
    L3StatusBar,
)


class ObserverScreen(Screen):
    """L0+L1+L2+L3+H2A 输入 主屏。

    K 边界:
    - K3 继承 WorkbenchObserver 过滤(只展示 BROADCAST)
    - K4 工作台只读(无 apply_* 调用,grep 验证)
    - K5 H2A 决策由 App 层用 ProposalModal(M3 拍 3b 注入)
    """

    DEFAULT_CSS = """
    ObserverScreen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "退出"),
        ("ctrl+c", "quit", "退出"),
    ]

    def __init__(self, snapshot: WidgetSnapshot, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snap = snapshot

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield L0TopBar(self._snap)
        with Container(id="body"):
            with Vertical():
                yield L1DomainDetail(self._snap)
                yield L2Board(self._snap)
        yield L3StatusBar()
        yield H2AInput(id="h2a-input")

    def update_snapshot(self, snapshot: WidgetSnapshot) -> None:
        """App 通知屏幕:新 snapshot 到了 → 重挂载(简化实现)。

        v0:整个 screen 重 compose;v1 改 reactive 属性。
        """
        self._snap = snapshot
        # 简化:让 App 重启 screen;在 headless 测试里直接 replace。
        # 这里 **不** 深做刷新,留给拍 3b H2A 集成时一并处理。

    async def on_intent_submitted(self, message) -> None:
        """批 5:H2AInput 发出的 IntentSubmitted 消息 → App.submit_intent。"""
        # 类型不显式 import(IntentSubmitted)避免循环依赖
        intent = getattr(message, "intent", "")
        if intent and hasattr(self.app, "submit_intent"):
            await self.app.submit_intent(intent)

    def action_quit(self) -> None:
        self.app.exit()