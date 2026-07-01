"""L1DomainDetail — L1 业务域详情(M3 批 3)。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from karvyloop.workbench.snapshot import WidgetSnapshot


class L1DomainDetail(Vertical):
    """L1 域详情:domain id + karvy_role + 成员 + 当前 Pursuit 摘要。

    K 边界:K4 — 只展示业务域元数据,不调 domain.apply_*。
    """

    DEFAULT_CSS = """
    L1DomainDetail {
        height: auto;
        padding: 1 1;
        border: round $primary;
    }
    L1DomainDetail Static {
        width: 100%;
    }
    """

    def __init__(self, snapshot: WidgetSnapshot, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snap = snapshot

    def compose(self) -> ComposeResult:
        if not self._snap.current_domain:
            yield Static("[dim]暂无业务域[/dim]")
            return
        yield Static(f"[b]域[/b]: {self._snap.current_domain}")
        yield Static(f"[b]小卡角色[/b]: observer (K1 灵魂级)")
        yield Static(f"[b]广播数[/b]: {len(self._snap.broadcasts)}")
        yield Static(f"[b]任务数[/b]: {self._snap.task_count}")
        yield Static(f"[b]Pursuit 数[/b]: {self._snap.pursuit_count}")
        if self._snap.unhealthy:
            yield Static("[red]⚠ 小卡健康度异常(详见 L3 状态条)[/red]")