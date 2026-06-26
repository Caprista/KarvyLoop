"""binding — WorkbenchObserver → Textual Message 桥(M3 批 3)。

边界(K3 强过滤必须继承):EnvelopeArrived 只 emit 通过 K3 的 envelope。
"""
from __future__ import annotations

import dataclasses

from textual.message import Message

from karvyloop.a2a import Envelope


@dataclasses.dataclass
class EnvelopeArrived(Message):
    """WorkbenchObserver.subscribe_async 发出 → App 主循环接收 → 按 type 分发。"""
    envelope: Envelope