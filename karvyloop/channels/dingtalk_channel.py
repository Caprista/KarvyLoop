"""channels/dingtalk_channel — 钉钉双向通道(Stream 模式):单个 agent 在钉钉群里能听会说。

设计:docs/100(钉钉通道)。走钉钉官方 Stream SDK(MIT,`karvyloop[dingtalk]` 可选依赖):
**家里机器只出站**——主动向钉钉发 WebSocket 长连接收消息、API 回复发回去,零公网端点、
不动 relay、不配域名证书,与 channels/ 既有哲学(永不需要公网 IP)一致。

安全地基(每条都有测试锁):
- **白名单 fail-closed**:群里任何人都能 @ 到机器人 → `allow_senders`(钉钉 staffId)
  空 = 谁的 @ 都不驱动(回一句"仅授权用户可用",同一 sender 只回一次,不刷群)。
- **白名单后原文驱动**:授权发送者的消息原文直接进入绑定角色;非白名单仍在驱动前拒绝。
- **凭据**只在 ~/.karvyloop/config.yaml(仓外);repr=False 不打日志。
- **角色工具预设自动生效**:绑定 role 的 COMPOSITION `tools:` 白名单经由
  build_role_paradigm_prompt → persona.tool_preset 管到这条通道(给钉钉角色配窄工具)。
- **对话隔离**:每个钉钉会话(单聊/群聊,conversationId)一条独立对话线,不碰 console
  当前 peer(web 聊天现场不被钉钉消息抢);会话标题取群名/对方昵称,轮次带发送者昵称。

v1 边界:只应答式回复(被 @ 才回);主动推送/卡片外推走既有 digest/webhook 管道,不在此。
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Awaitable, Callable, Optional

from karvyloop.config_channels import DingTalkChannelConfig

logger = logging.getLogger(__name__)

# 拒驱动回执(白名单外):一条固定话,不烧模型
REFUSAL_TEXT = "这个机器人仅对授权用户开放。"


def _split_markdown_table_row(line: str) -> list[str]:
    """拆分 GFM 表格行，同时不把链接目标或转义竖线当作列边界。"""
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|") and not content.endswith(r"\|"):
        content = content[:-1]

    cells: list[str] = []
    cell: list[str] = []
    escaped = False
    bracket_depth = 0
    link_depth = 0
    for char in content:
        if escaped:
            cell.append(char)
            escaped = False
            continue
        if char == "\\":
            cell.append(char)
            escaped = True
            continue
        if char == "[" and not link_depth:
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        elif char == "(" and not bracket_depth:
            link_depth += 1
        elif char == ")" and link_depth:
            link_depth -= 1
        if char == "|" and not bracket_depth and not link_depth:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
    cells.append("".join(cell).strip())
    return cells


def _requires_markdown_fallback(text: str) -> bool:
    """判断正文是否包含钉钉 AI 交互卡片不稳定的 Markdown。"""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"<!--|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*?)?/?>", value):
        return True
    if re.search(r"!\[[^\]]*\]\([^\n)]+\)", value):
        return True
    if re.search(r"(?m)^\s{0,3}(?:```|~~~)", value):
        return True
    if re.search(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+\[[ xX]\](?:\s+|$)", value):
        return True

    lines = value.split("\n")
    separator_cell = re.compile(r"^:?-{3,}:?$")
    for index in range(1, len(lines)):
        separator_line = lines[index].strip()
        if "|" not in separator_line:
            continue
        separators = _split_markdown_table_row(separator_line)
        if not separators or not all(separator_cell.fullmatch(cell) for cell in separators):
            continue
        header_line = lines[index - 1].strip()
        if "|" not in header_line:
            continue
        headers = _split_markdown_table_row(header_line)
        explicit_single_column = (
            len(headers) == 1
            and header_line.startswith("|") and header_line.endswith("|")
            and separator_line.startswith("|") and separator_line.endswith("|")
        )
        if len(headers) == len(separators) and (len(headers) > 1 or explicit_single_column):
            return True
    return False


def _normalize_dingtalk_markdown(markdown: str) -> str:
    """规范 AI 卡片可承载的 Markdown；复杂格式仅供历史纯函数兼容。"""
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    output: list[str] = []
    i = 0
    separator_cell = re.compile(r"^:?-{2,}:?$")
    while i < len(lines):
        if i + 1 < len(lines) and "|" in lines[i] and "|" in lines[i + 1]:
            headers = _split_markdown_table_row(lines[i])
            separators = _split_markdown_table_row(lines[i + 1])
            if (len(headers) == len(separators) and len(headers) > 1
                    and all(separator_cell.fullmatch(c) for c in separators)):
                rows = []
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    rows.append(_split_markdown_table_row(lines[i]))
                    i += 1
                if not rows:
                    output.append("；".join(headers))
                else:
                    for row in rows:
                        parts = []
                        for n, value in enumerate(row):
                            name = headers[n] if n < len(headers) else f"字段{n + 1}"
                            if value:
                                parts.append(f"{name}：{value}")
                        if parts:
                            output.append("- " + "；".join(parts))
                continue
        line = lines[i]
        heading = re.match(r"^(\s*)(#{1,})\s+(.*)$", line)
        if heading:
            line = heading.group(1) + "#" * min(len(heading.group(2)), 3) + " " + heading.group(3)
        line = re.sub(r"<!--.*?-->", "", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1（\2）", line)
        line = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+\[[ xX]\]\s*", "- ", line)
        if re.match(r"^\s*(?:```|~~~)", line):
            i += 1
            continue
        output.append(line)
        i += 1
    return "\n".join(output).strip()


class _DingTalkDelivery:
    """按最终正文判型，稳定消息才延迟创建 AI 卡片。"""

    def __init__(self, handler: Any, msg: Any) -> None:
        self.handler = handler
        self.msg = msg
        self.card: Any = None
        self.finished = False
        self.markdown_sent = False
        self._finish_lock = asyncio.Lock()

    async def markdown(self, text: str) -> None:
        if self.markdown_sent:
            return
        await asyncio.to_thread(self.handler.reply_markdown, "AI 回复", text, self.msg)
        self.markdown_sent = True

    async def finish(self, text: str,
                     start_card: Optional[Callable[[], Any]] = None) -> None:
        async with self._finish_lock:
            if self.finished:
                return
            try:
                if _requires_markdown_fallback(text):
                    await self.markdown(text)
                    return

                try:
                    card = (await asyncio.to_thread(start_card)
                            if start_card is not None else None)
                    if not getattr(card, "card_instance_id", None):
                        raise RuntimeError("AI 卡片创建未返回有效实例")
                    self.card = card
                except Exception:
                    logger.warning("[dingtalk] AI 卡片创建失败，改用 Markdown 消息", exc_info=True)
                    await self.markdown(text)
                    return

                normalized = _normalize_dingtalk_markdown(text)
                try:
                    await asyncio.to_thread(card.ai_streaming, normalized, append=False)
                except Exception:
                    logger.warning("[dingtalk] AI 卡片正文写入失败，改用 Markdown 消息", exc_info=True)
                    await self.markdown(text)
                    return

                try:
                    await asyncio.to_thread(card.ai_finish, markdown=normalized)
                except Exception:
                    logger.warning("[dingtalk] AI 卡片收敛失败，改用 Markdown 消息", exc_info=True)
                    await self.markdown(text)
            finally:
                self.finished = True


async def _publish_channel_message(app: Any, *, role: str, text: str,
                                    chat_id: str, sender: str,
                                    conversation_id: str = "",
                                    channel_role: str = "",
                                    sender_nick: str = "",
                                    chat_type: str = "",
                                    chat_title: str = "") -> None:
    """同步外部通道消息到对应通道会话和在线 WebSocket。"""
    speaker = f"dingtalk:{chat_id or 'unknown'}"
    # 通道消息不属于当前的小卡聊天，持久化由 record_channel_turn 负责。
    try:
        from karvyloop.console.task_events import broadcast_channel_message
        await broadcast_channel_message(app, {
            "channel": "dingtalk",
            "peer_id": speaker,
            "conversation_id": conversation_id,
            "sender": sender,
            "sender_nick": sender_nick,
            "chat_type": chat_type,
            "chat_title": chat_title,
            "role": role,
            "channel_role": channel_role,
            "text": text,
        })
    except Exception:
        logger.debug("[dingtalk] console channel_message 广播失败", exc_info=True)


def _channel_conversation_id(app: Any, cfg: DingTalkChannelConfig, chat_id: str) -> str:
    from karvyloop.domain import Address
    mgr = getattr(app.state, "conversation_manager", None)
    if mgr is None:
        return ""
    try:
        conv = mgr.channel_conversation(Address(
            domain_id=(cfg.domain_id or "l0"), role="channel",
            agent_id=f"dingtalk:{chat_id or 'unknown'}"))
        return conv.id if conv is not None else ""
    except Exception:
        return ""


def _extract(payload: dict) -> dict:
    """从 SDK callback data 里抠消息要素。容错:缺 → 空串。

    - sender/chat/text:老三样(staffId、会话 ID、正文)
    - sender_nick:发送者昵称(senderNick),界面上显示"是谁发的"
    - chat_type:conversationType,"1"=单聊 "2"=群聊;归一成 direct/group
    - chat_title:conversationTitle,群聊=群名,单聊=对方昵称(常空)
    """
    d = payload or {}
    text = ""
    t = d.get("text")
    if isinstance(t, dict):
        text = str(t.get("content") or "").strip()
    elif isinstance(t, str):
        text = t.strip()
    sender = str(d.get("senderStaffId") or d.get("senderId") or "").strip()
    chat = str(d.get("conversationId") or d.get("openConversationId") or "").strip()
    raw_type = str(d.get("conversationType") or "").strip()
    chat_type = {"1": "direct", "2": "group"}.get(raw_type, raw_type)
    return {
        "sender": sender,
        "chat": chat,
        "text": text,
        "sender_nick": str(d.get("senderNick") or "").strip(),
        "chat_type": chat_type,
        "chat_title": str(d.get("conversationTitle") or "").strip(),
    }


async def drive_channel_message(app: Any, cfg: DingTalkChannelConfig, *,
                                text: str, chat_id: str, sender: str,
                                raw_text: str = "",
                                sender_nick: str = "",
                                chat_type: str = "",
                                chat_title: str = "",
                                on_event: Optional[Callable[[dict], None]] = None) -> str:
    """把一条钉钉消息驱成绑定 role 的回复。返回回复文本(失败也回诚实人话,不抛)。

    text = 喂模型的消息原文;raw_text = 落对话历史的原文。复用主聊天路径的同一 drive,
    不新造执行链。
    """
    from karvyloop.domain import Address
    ml = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if ml is None:
        return "(引擎未接,暂时没法回答 —— console 是否以 --no-llm 起的?)"

    # 1) 绑定 role → persona(paradigm 编译;tool_preset 自动带上)
    role_reg = getattr(app.state, "role_registry", None)
    rv = None
    if role_reg is not None:
        try:
            rv = role_reg.get(cfg.role)
        except Exception:
            rv = None
    if rv is None:
        return f"(绑定的角色「{cfg.role}」不在角色库 —— 去角色面板确认下名字)"
    domain = None
    dom_reg = getattr(app.state, "domain_registry", None)
    if cfg.domain_id and dom_reg is not None:
        try:
            domain = dom_reg.get(cfg.domain_id)
        except Exception:
            domain = None
    ws_root = str(rk.get("workspace_root") or "/")
    try:
        from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
        persona = build_role_paradigm_prompt(rv, domain, intent=text, cwd=ws_root)
    except Exception:
        persona = None
    if persona is None:
        from karvyloop.coding.persona import build_role_persona_prompt
        persona = build_role_persona_prompt(
            getattr(rv, "nickname", "") or cfg.role,
            domain_name=getattr(domain, "name", None), cwd=ws_root)

    # 2) 通道隔离对话:peer 带 dingtalk:<chat_id>,不碰 console 当前 peer
    peer = Address(domain_id=(cfg.domain_id or "l0"), role="channel",
                   agent_id=f"dingtalk:{chat_id or 'unknown'}")
    mgr = getattr(app.state, "conversation_manager", None)
    conv = None
    if mgr is not None:
        try:
            conv = mgr.channel_conversation(peer)
        except Exception:
            conv = None
    ctx = conv.context_view() if conv is not None else None

    # 4) 驱动(同 ws 聊天路径:runtime_kwargs 展开喂 token/sandbox/gateway)
    scope = "domain" if domain is not None else "user"
    governance = ""
    if domain is not None:
        vm = getattr(domain, "value_md", None)
        governance = (getattr(vm, "text", None) or "") if vm is not None else ""
    from karvyloop.workbench.main_loop_bridge import drive_in_tui
    try:
        outcome = await drive_in_tui(
            text, ml, ctx=ctx, governance=governance, persona=persona, scope=scope,
            on_event=on_event, **rk)
    except Exception as e:
        logger.warning("[dingtalk] drive 失败(chat=%s): %s", chat_id, e)
        return f"(小卡这轮跑挂了:{type(e).__name__} —— 回 console 看看任务面板)"

    reply = (getattr(outcome, "text", "") or "").strip()
    # 剥围栏标记(权威终态,同 ws 终态 scrub)
    try:
        from karvyloop.cognition.fence import ScrubState, scrub_stream
        _st = ScrubState()
        reply = (scrub_stream(reply, _st) + _st.buffer).strip()
    except Exception:
        pass
    if not reply:
        reply = "(这轮没产出文字回复 —— 可能跑了工具活,去 console 任务面板看结果)"

    # 5) 先落 meta/轮,再广播终态 —— 列表刷新时能立即读到最新标题、轮次和时间。
    if conv is not None and mgr is not None:
        try:
            # 首条消息回写会话标题/类型(追加 _meta 行;只写一次):
            # 群聊用群名,单聊用对方昵称 —— 列表不再显示一串 cid。
            if not conv.title and (chat_title or sender_nick):
                title = chat_title if chat_type == "group" else (sender_nick or chat_title)
                meta = ({"type": chat_type, "title": chat_title}
                        if chat_type in ("group", "direct") else None)
                mgr.set_channel_meta(peer, conv, title=title, chat=meta)
        except Exception:
            pass
        try:
            info = {"sender": sender_nick or sender, "sender_id": sender}
            if chat_type:
                info["chat_type"] = chat_type
            if chat_title:
                info["chat_title"] = chat_title
            mgr.record_channel_turn(peer, conv, user_intent=raw_text or text,
                                    agent_response=reply,
                                    data={"channel": info})
        except Exception:
            pass

    await _publish_channel_message(app, role="agent", text=reply,
                                   chat_id=chat_id, sender=sender,
                                   conversation_id=conv.id if conv is not None else "",
                                   channel_role=cfg.role,
                                   sender_nick=sender_nick,
                                   chat_type=chat_type,
                                   chat_title=chat_title)
    return reply


async def handle_incoming(app: Any, cfg: DingTalkChannelConfig, payload: dict,
                          reply_fn: Callable[[str], Any],
                          refused: Optional[set] = None,
                          processing_fn: Optional[Callable[[], Awaitable[Any]]] = None,
                          on_event: Optional[Callable[[dict], None]] = None) -> None:
    """入站消息处理(SDK 无关的纯逻辑,测试直接喂 payload + 假 reply_fn)。

    白名单 fail-closed → processing_fn(开始处理) → 原文 drive → reply_fn(回复)。
    `refused`:本实例"已拒绝过的 sender"集合(每机器人一份;None = 临时一份)——
    同一 sender 只回一次拒绝,不刷群;多实例间互不干扰。
    """
    info = _extract(payload)
    sender, chat, text = info["sender"], info["chat"], info["text"]
    if not text:
        return
    refused = refused if refused is not None else set()
    if sender not in cfg.allow_senders:
        try:
            from karvyloop.channels.dingtalk_runtime import config_instance_id
            from karvyloop.console.task_events import broadcast_channel_sender_pending
            cache = getattr(app.state, "pending_channel_senders", None)
            if cache is not None and sender:
                pending = cache.put(config_instance_id(cfg), sender,
                                    sender_nick=info["sender_nick"],
                                    chat_type=info["chat_type"],
                                    chat_title=info["chat_title"])
                await broadcast_channel_sender_pending(app, pending)
        except Exception:
            logger.debug("[dingtalk] 待授权 sender 事件广播失败", exc_info=True)
        if sender not in refused:
            refused.add(sender)
            # 打完整 staffId:配白名单时要照它填(本机日志,不进群、不外发)。
            # 必须 warning 级:console 默认日志门是 WARNING,info 级打不出来
            # (Hardy 实拍:引导说"日志会打 staffId"但什么都没出)。
            logger.warning("[dingtalk] 白名单外 sender 被拒(不驱动)。如这是你自己,"
                           "把这个 staffId 填进 allow_senders: %s", sender or "?")
            try:
                reply_fn(REFUSAL_TEXT)
            except Exception:
                pass
        return
    if processing_fn is not None:
        await processing_fn()
    conversation_id = _channel_conversation_id(app, cfg, chat)
    await _publish_channel_message(app, role="user", text=text,
                                   chat_id=chat, sender=sender,
                                   conversation_id=conversation_id,
                                   channel_role=cfg.role,
                                   sender_nick=info["sender_nick"],
                                   chat_type=info["chat_type"],
                                   chat_title=info["chat_title"])
    reply = await drive_channel_message(app, cfg, text=text, chat_id=chat, sender=sender,
                                        raw_text=text,
                                        sender_nick=info["sender_nick"],
                                        chat_type=info["chat_type"],
                                        chat_title=info["chat_title"],
                                        on_event=on_event)
    reply_fn(reply)


class DingTalkChannel:
    """钉钉 Stream 通道的宿主:起线程跑 SDK 长连接,消息桥回 console 事件循环。

    一个实例 = 一个钉钉应用 ↔ 一个绑定角色;多实例并存(每 agent 一个机器人),
    拒绝名单等状态全在实例上,互不干扰。"""

    def __init__(self, app: Any, cfg: DingTalkChannelConfig) -> None:
        self._app = app
        self._cfg = cfg
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._refused: set = set()   # 本实例已拒过的 sender(实例级,多机器人互不吃对方的名单)

    def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        """起通道(成功 True)。SDK 缺席/起失败 → False + 日志明说,不影响 console。"""
        try:
            import dingtalk_stream  # noqa: F401
        except ImportError:
            logger.warning("[dingtalk] 未装 dingtalk-stream(pip install 'karvyloop[dingtalk]')—— 通道不启动")
            return False
        self._loop = loop

        channel = self

        class _Handler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback):  # noqa: ANN001
                data = getattr(callback, "data", {}) or {}
                holder: dict = {}
                from dingtalk_stream import ChatbotMessage
                msg = ChatbotMessage.from_dict(data)
                delivery = _DingTalkDelivery(self, msg)

                def _reply(text: str) -> None:
                    holder["reply"] = text

                fut = asyncio.run_coroutine_threadsafe(
                    handle_incoming(channel._app, channel._cfg, data, _reply,
                                    refused=channel._refused), loop)
                try:
                    await asyncio.to_thread(fut.result)
                except Exception as e:
                    logger.warning("[dingtalk] 入站处理失败: %s", e)
                    holder["reply"] = "(这条处理失败了,回 console 看日志)"
                text = holder.get("reply")
                if text:
                    try:
                        await delivery.finish(
                            text,
                            start_card=lambda: self.ai_markdown_card_start(
                                msg, title="AI 回复"),
                        )
                    except Exception as e:
                        logger.warning("[dingtalk] 回复发送失败: %s", e)
                from dingtalk_stream import AckMessage
                return AckMessage.STATUS_OK, "OK"

        try:
            credential = dingtalk_stream.Credential(self._cfg.client_id, self._cfg.client_secret)
            self._client = dingtalk_stream.DingTalkStreamClient(credential)
            self._client.register_callback_handler(
                dingtalk_stream.chatbot.ChatbotMessage.TOPIC, _Handler())
        except Exception as e:
            logger.warning("[dingtalk] 客户端初始化失败: %s", e)
            return False

        def _run() -> None:
            try:
                self._client.start_forever()
            except Exception as e:
                logger.warning("[dingtalk] 长连接退出: %s", e)

        self._thread = threading.Thread(target=_run, name="dingtalk-stream", daemon=True)
        self._thread.start()
        # 用户要照启动日志确认通道活着 —— print 直出(logger.info 在默认 WARNING 门下不可见;
        # 与 MCP 接入成功的 print 同款能见度)。
        print(f"[karvyloop console] 钉钉通道已起(Stream 长连接;"
              f"{self._cfg.name or '实例'} 绑定角色 {self._cfg.role})", flush=True)
        logger.info("[dingtalk] 通道已起(Stream 长连接;%s 绑定角色 %s)",
                    self._cfg.name or "实例", self._cfg.role)
        return True

    def stop(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass


__all__ = ["DingTalkChannel", "drive_channel_message", "handle_incoming", "REFUSAL_TEXT"]
