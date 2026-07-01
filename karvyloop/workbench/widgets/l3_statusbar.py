"""L3StatusBar — L3 跨设备状态条(M3 批 3 边界:占位)。"""
from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Static


class L3StatusBar(Horizontal):
    """L3 状态条:跨设备同步状态(M3 拍 3 边界 = 占位)。

    v0 文案固定:
      ● 本机 · 跨设备同步留口(M4+ 跨设备拍)
    """

    DEFAULT_CSS = """
    L3StatusBar {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    L3StatusBar Static {
        width: 100%;
        color: $text-muted;
    }
    """

    def compose(self):
        yield Static("● 本机 · 跨设备同步留口(M4+ 跨设备拍)")