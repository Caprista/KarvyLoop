from __future__ import annotations

from typing import Any

from .models import NotificationCommand


def make_notify_user_tool(*, store: Any, actor_id: str, task_id: str = "", trace_ref: str = "", actor_type: str = "agent", current_user_id: str = "", preauthorized: bool = False):
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    # 注:notify_user 不进通用外发草稿闸 —— outbound_gate._PLATFORM_EXEMPT 已结构性豁免
    # (名字里的 "notify" 会命中发送动词,但本工具自具治理链:策略审批 → outbox →
    # notification_approval 决策卡 → dispatcher;截成 outbound_draft 卡是死路,实拍 bug)。

    async def _call(inp: dict, token: Any, sandbox: Any) -> dict:
        inp = inp or {}
        recipient = str(inp.get("recipient") or "").strip()
        content = str(inp.get("content") or "").strip()
        if not recipient or not content:
            return {"status": "rejected", "reason": "recipient、content 均为必填"}
        allowed = {"recipient", "content", "title", "channel", "conversation_id", "urgency", "reason", "dedupe_key"}
        command = NotificationCommand(recipient=recipient, content=content, title=str(inp.get("title") or ""), channel=str(inp.get("channel") or "auto"), conversation_id=str(inp.get("conversation_id") or ""), urgency=str(inp.get("urgency") or "normal"), reason=str(inp.get("reason") or ""), dedupe_key=str(inp.get("dedupe_key") or ""), actor_type=actor_type, actor_id=actor_id, task_id=task_id, trace_ref=trace_ref, runtime_context={"current_user_id": current_user_id, "preauthorized": preauthorized})
        if set(inp) - allowed:
            return {"status": "rejected", "reason": "输入包含不允许的字段"}
        try:
            receipt = store.submit(command)
            if receipt.status == "rejected" and receipt.reason == "recipient_unresolved":
                # 诚实回执带可执行纠正(实拍:agent 猜姓名/乱填 id → 连续 recipient_unresolved)
                return {"status": "rejected", "reason": (
                    "接收人无法解析。recipient 只支持:current_user(通知当前用户,推荐)、"
                    "user:<平台用户id 或钉钉staffId>(须已建立绑定)、conversation:<会话id>。"
                    "不要填姓名或其他 id;给当前所有者发通知直接用 current_user。")}
            return {"status": receipt.status, "notification_id": receipt.notification_id, "duplicate_of": receipt.duplicate_of, "reason": receipt.reason}
        except Exception as exc:
            return {"status": "rejected", "reason": f"入队失败:{type(exc).__name__}"}

    return build_tool(name="notify_user", description=(
        "主动通知用户(平台能力,异步投递)。recipient 只支持三种取值:"
        "current_user(通知当前所有者,首选)、user:<平台用户id或钉钉staffId>(须已有绑定)、"
        "conversation:<钉钉会话id>(群聊,须已有绑定)。不要填姓名。"
        "返回 queued 仅代表受理,送达由平台异步完成;普通通知会先出审批卡待用户拍板。"), input_schema={"type": "object", "additionalProperties": False, "properties": {"recipient": {"type": "string", "description": "current_user | user:<id> | conversation:<id>;禁止姓名"}, "content": {"type": "string"}, "title": {"type": "string"}, "channel": {"type": "string", "enum": ["auto", "dingtalk"]}, "conversation_id": {"type": "string"}, "urgency": {"type": "string", "enum": ["normal", "important", "urgent"]}, "reason": {"type": "string"}, "dedupe_key": {"type": "string"}}, "required": ["recipient", "content"]}, call=_call, required_mode=Mode.FULL)
