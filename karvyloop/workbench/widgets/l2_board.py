"""L2Board — L2 工作台(只读任务流)(M3 批 3)。

K 边界:K4 工作台只读 — 任何"写"操作都**不**走 UI,UI **不**直调 domain.apply_*;
代发走 Courier.send,决策走 ProposalModal(M3 拍 3b)。
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from karvyloop.workbench.snapshot import WidgetSnapshot


class L2Board(VerticalScroll):
    """L2 工作台:只读 BROADCAST 任务流 + Pursuit 卡。

    banner 灰底"只读视图 · 修改经 A2A 投递"(第一道墙视觉化)。
    """

    DEFAULT_CSS = """
    L2Board {
        height: 1fr;
        padding: 0 1;
    }
    L2Board Static {
        width: 100%;
        margin: 0 0 1 0;
    }
    .banner {
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, snapshot: WidgetSnapshot, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snap = snapshot

    def compose(self) -> ComposeResult:
        # 第一道墙:只读视图 banner
        yield Static("📖 只读视图 · 修改经 A2A 投递(工作台不直接修改业务域)", classes="banner")
        # 批 8.5-A:错误独立通道(不截断) — 优先于 last_drive_text,失败时不污染慢脑槽
        if self._snap.last_error:
            yield Static(
                f"[b reverse red] {self._snap.last_error} [/b reverse red]",
                id="last-error",
            )
        # 批 5:最近一次 drive 结果(快脑命中 / 慢脑输出 / 结晶通知)
        if self._snap.last_drive_text:
            if self._snap.last_fast_brain_skill:
                yield Static(
                    f"⚡ [b]用了你的技能[/b] `{self._snap.last_fast_brain_skill}` → {self._snap.last_drive_text[:60]}",
                    id="last-fast-brain",
                )
            else:
                yield Static(f"🤖 慢脑输出: {self._snap.last_drive_text[:80]}", id="last-slow-brain")
        # 批 8.5-A:用户最近一次提交的 intent(input echo,让用户看到自己发出去啥)
        if self._snap.last_intent:
            yield Static(
                f"[dim]📤 你说:[/dim] {self._snap.last_intent}",
                id="last-intent",
            )
        if not self._snap.broadcasts:
            yield Static("[dim]暂无 BROADCAST[/dim]")
            return
        for i, env in enumerate(self._snap.broadcasts, start=1):
            tag = getattr(env.payload, "tag", "?") if hasattr(env.payload, "tag") else "?"
            msg = getattr(env.payload, "message", str(env.payload)) if hasattr(env.payload, "message") else str(env.payload)
            yield Static(f"[b]#{i}[/b] [{tag}] {msg[:60]}{'...' if len(msg) > 60 else ''}")