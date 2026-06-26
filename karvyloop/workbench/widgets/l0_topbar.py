"""L0TopBar — L0 顶导(我所在的业务域列表)(M3 批 3)。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from karvyloop.workbench.snapshot import WidgetSnapshot


class L0TopBar(Horizontal):
    """L0 顶导:domain 切换 + L4 跨组织 disabled 按钮。

    K 边界:K3 — 只展示,不修改;点击切换走 Message 通知 App(K4)。
    """

    DEFAULT_CSS = """
    L0TopBar {
        height: 3;
        background: $panel;
        padding: 0 1;
    }
    L0TopBar Static {
        width: auto;
        padding: 1 1;
    }
    L0TopBar Button {
        margin: 0 1;
    }
    """

    def __init__(self, snapshot: WidgetSnapshot, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snap = snapshot

    def compose(self) -> ComposeResult:
        yield Static("[b]KarvyLoop · 协作舞台[/b]")
        for d in self._snap.domains:
            label = f"📂 {d}" + (" ●" if d == self._snap.current_domain else "")
            yield Button(label, id=f"domain-{d}", variant=("primary" if d == self._snap.current_domain else "default"))
        # 批 5:结晶事件展示(顶导右侧,每次重 compose 时反映 snapshot)
        n = len(self._snap.crystallized_skills)
        if n > 0:
            latest = self._snap.crystallized_skills[-1]
            yield Static(f"🔔 结晶 {n}: [b]{latest}[/b]", id="crystallized-badge")
        # L4 跨组织按钮(disabled,M3 拍 3 边界)
        yield Button("🌐 跨组织 (M4+)", id="l4-org", disabled=True, variant="default")