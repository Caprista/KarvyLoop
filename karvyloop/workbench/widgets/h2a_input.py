"""H2AInput — 底部 H2A 输入框(M3 批 3 + 批 5)。

批 5 改造:Enter 提交后发 `IntentSubmitted` 消息 → App.submit_intent 走 MainLoop。
K 边界:K4 — H2AInput **不**直接调 domain.apply_*,**不**发 A2A;只触发 App 内部方法。
"""
from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


class IntentSubmitted(Message):
    """H2AInput 提交 intent 后发出。App.on_intent_submitted 接收。"""

    def __init__(self, intent: str) -> None:
        super().__init__()
        self.intent = intent


class H2AInput(Input):
    """H2A 输入框:Enter 触发 → 发出 IntentSubmitted 消息。

    拍 3a v0:输入框只是占位(接收消息→DataCourier.answer 只读搬运);
    拍 3b:接 ProposalModal。
    拍 5:接 MainLoop.drive(批 5 拍)。
    """

    DEFAULT_CSS = """
    H2AInput {
        height: 3;
        margin: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(placeholder="💬 输入 intent (Enter 提交走 MainLoop)", **kwargs)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Textual 8.x:Input.Submitted 事件自带 value。

        批 8.5-A:提交后**先**让 App 层写一行 `[user] <intent>` 进聊天日志
        (input echo,修"石沉大海" 缺陷 #1),再清空输入框。
        """
        intent = (event.value or "").strip()
        if intent:
            # 1. 8.5-A 修:input echo — 推 ChatLine 进 chat log + ring buffer
            # 整个块 try/except(无 active app context / 无 push_chat_log_line 方法时跳过)
            try:
                self.app.push_chat_log_line("user", intent)
            except Exception:
                # headless 测试或 App 还没挂载时跳过(input echo 退化)
                pass
            # 2. 原行为:发 IntentSubmitted 消息 + 清空输入
            self.post_message(IntentSubmitted(intent))
            self.value = ""  # 清空输入框,准备下一条