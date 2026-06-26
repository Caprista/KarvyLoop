"""chat_history — 进程级聊天历史 ring buffer(M3+ 批 8.5-A)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-A。

为什么需要:TUI 修"石沉大海" 的 3 缺陷之一 — 之前 0 持久化,用户发了啥、回复了啥
完全没记录,出问题无对证。批 8.5-A 加进程级 ring buffer(500 条),`WorkbenchApp`
调 `push_chat_log_line` 写,console 端点 `GET /api/chat_history` 读。

借:Q5 自造≠闭门造车 — 这是 1 个 ring buffer + 1 个 dataclass,没有任何业务逻辑
可以"借"既有模块;`deque(maxlen=500)` 是 stdlib。

K 边界:K4 read-only 外暴露(`get_chat_history()` 是只读),K 铁律 grep 不破。
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, List, Optional


@dataclass
class ChatEntry:
    """单条聊天记录(用户/system/agent 角色,文本 + ts)。"""
    role: str        # "user" | "system" | "agent" | "h2a"
    text: str
    ts: str          # ISO 8601 UTC
    # 9.4:agent 回合的结构化渲染事件(text/tool_call/tool_result/terminal);其余角色空。
    # 持久在历史里 → 周期性 chat_history 刷新也渲染成结构化回合,不被裸文本覆盖。
    events: list = field(default_factory=list)


class ChatHistory:
    """进程级 ring buffer,线程安全。

    Args:
        maxlen: 最大条数(默认 500;超了 deque 自动丢最旧)。
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._buf: Deque[ChatEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, role: str, text: str, ts: str, events: Optional[list] = None) -> None:
        """追加一条(满则丢最旧)。events:agent 结构化回合(可选)。"""
        entry = ChatEntry(role=role, text=text, ts=ts, events=list(events or []))
        with self._lock:
            self._buf.append(entry)

    def snapshot(self) -> List[dict]:
        """返当前 buffer 的 dict 列表(从最旧到最新,JSON 友好)。"""
        with self._lock:
            return [asdict(e) for e in self._buf]

    def clear(self) -> None:
        """清空(测试用)。"""
        with self._lock:
            self._buf.clear()


# 进程级单例 — WorkbenchApp 实例共享(同一 TUI 进程内)
_global_history: ChatHistory = ChatHistory()


def get_chat_history() -> List[dict]:
    """返进程级聊天历史(供 WorkbenchApp.get_chat_history() + 8.5-C console 用)。"""
    return _global_history.snapshot()


def push_chat_log_line(role: str, text: str, ts: str, events: Optional[list] = None) -> None:
    """追加一条到进程级历史(供 WorkbenchApp.push_chat_log_line() 用)。"""
    _global_history.push(role, text, ts, events)


def reset_for_test() -> None:
    """测试用:清空全局历史。"""
    _global_history.clear()


__all__ = ["ChatEntry", "ChatHistory", "get_chat_history", "push_chat_log_line", "reset_for_test"]
