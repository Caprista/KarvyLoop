"""LChatLog — 聊天日志 widget(M3+ 批 8.5-A)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-A。

修 TUI "石沉大海" 缺陷 #1(无 input echo)+ 缺陷 #3(无 persistent log):
- App 提交 intent 后 `push_chat_log_line(role="user", text=intent)` 写一行
- App drive 完后 `push_chat_log_line(role="agent", text=result)` 写一行
- LChatLog 用 `RichLog` 自动滚动,每行带 role prefix:`[user] hi`、`[karvyloop] <result>`

借:Q5 — RichLog 是 textual 内建,纯包装不重写。

K 边界:K4 — 只读渲染,不构造 Envelope、不调 apply_*。
"""
from __future__ import annotations

from textual.message import Message
from textual.widgets import RichLog


class ChatLine(Message):
    """App 写一行聊天日志 → LChatLog 接收。

    8.5-A:`workbench.chat_history.push_chat_log_line` 同步写进程级 ring buffer
    (8.5-C console 复用);`LChatLog` 通过订阅 `ChatLine` Message 刷新 UI。
    """
    def __init__(self, role: str, text: str, ts: str) -> None:
        super().__init__()
        self.role = role
        self.text = text
        self.ts = ts


class LChatLog(RichLog):
    """聊天日志 widget:只读,自动滚动。

    渲染:`[user] hi` / `[karvyloop] <result>` / `[system] MainLoop 未注入` /
    `[h2a] ACCEPT sent` 形式,按时间序。
    """

    DEFAULT_CSS = """
    LChatLog {
        height: auto;
        max-height: 12;
        padding: 0 1;
        border: round $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(wrap=True, markup=True, **kwargs)

    def on_chat_line(self, message: ChatLine) -> None:
        """App 发的 ChatLine → 写一行。"""
        prefix_map = {
            "user": "[b cyan][user][/b cyan]",
            "agent": "[b green][karvyloop][/b green]",
            "system": "[b yellow][system][/b yellow]",
            "h2a": "[b magenta][h2a][/b magenta]",
        }
        prefix = prefix_map.get(message.role, f"[b][{message.role}][/b]")
        self.write(f"{prefix} {message.text}")

    def push_line(self, role: str, text: str, ts: str) -> None:
        """直接 API(给 App 层主动 push,避免 Message 循环)。"""
        self.on_chat_line(ChatLine(role=role, text=text, ts=ts))


__all__ = ["ChatLine", "LChatLog"]
