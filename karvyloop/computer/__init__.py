"""computer —— computer use 的平台 plumbing(docs/99).

Linux/Wayland 的 plumbing 借 computer-use-linux(门户/ydotool 那套硬活);其余平台
(Windows / macOS / Linux-X11)输入/截屏是薄标准活 → native_server 一份自造的薄 MCP server 覆盖。
OS-无关的核心(看/安全/模型无关)不在这层。
"""
