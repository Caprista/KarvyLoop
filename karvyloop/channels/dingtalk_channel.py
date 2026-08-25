"""channels/dingtalk_channel — 钉钉双向通道(Stream 模式):单个 agent 在钉钉群里能听会说。

设计:docs/100(钉钉通道)。走钉钉官方 Stream SDK(MIT,`karvyloop[dingtalk]` 可选依赖):
**家里机器只出站**——主动向钉钉发 WebSocket 长连接收消息、API 回复发回去,零公网端点、
不动 relay、不配域名证书,与 channels/ 既有哲学(永不需要公网 IP)一致。

安全地基(每条都有测试锁):
- **白名单 fail-closed**:群里任何人都能 @ 到机器人 → `allow_senders`(钉钉 staffId)
  空 = 谁的 @ 都不驱动(回一句"仅授权用户可用",同一 sender 只回一次,不刷群)。
- **入站 = 不可信输入**:消息文本过 `fence_untrusted(source="dingtalk")` 统一围栏
  (群里的话是数据不是指令;注入面纪律)。
- **凭据**只在 ~/.karvyloop/config.yaml(仓外);repr=False 不打日志。
- **角色工具预设自动生效**:绑定 role 的 COMPOSITION `tools:` 白名单经由
  build_role_paradigm_prompt → persona.tool_preset 管到这条通道(给钉钉角色配窄工具)。
- **对话隔离**:每个钉钉群(conversationId)一条独立对话线,不碰 console 当前 peer
  (web 聊天现场不被钉钉消息抢)。

v1 边界:只应答式回复(被 @ 才回);主动推送/卡片外推走既有 digest/webhook 管道,不在此。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Optional

from karvyloop.config_channels import DingTalkChannelConfig

logger = logging.getLogger(__name__)

# 拒驱动回执(白名单外):一条固定话,不烧模型
REFUSAL_TEXT = "这个机器人仅对授权用户开放。"
# 同一 sender 的拒绝提示只回一次(防刷群;进程内集合即可,重启重计无害)
_refused_senders: set[str] = set()
_refused_lock = threading.Lock()


def _extract(payload: dict) -> tuple[str, str, str]:
    """从 SDK callback data 里抠 (sender_id, chat_id, text)。容错:缺 → 空串。"""
    d = payload or {}
    text = ""
    t = d.get("text")
    if isinstance(t, dict):
        text = str(t.get("content") or "").strip()
    elif isinstance(t, str):
        text = t.strip()
    sender = str(d.get("senderStaffId") or d.get("senderId") or "").strip()
    chat = str(d.get("conversationId") or d.get("openConversationId") or "").strip()
    return sender, chat, text


async def drive_channel_message(app: Any, cfg: DingTalkChannelConfig, *,
                                text: str, chat_id: str, sender: str,
                                raw_text: str = "") -> str:
    """把一条钉钉消息驱成绑定 role 的回复。返回回复文本(失败也回诚实人话,不抛)。

    text = 喂模型的(已过围栏);raw_text = 落对话历史的原文(围栏是给模型的,
    历史里该存用户的原话)。复用主聊天路径的同一 drive,不新造执行链。
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
            **rk)
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

    # 5) 记轮(通道对话自己的历史,不串 web 聊天;历史存原文,围栏版只给模型看)
    if conv is not None and mgr is not None:
        try:
            mgr.record_channel_turn(peer, conv, user_intent=raw_text or text,
                                    agent_response=reply)
        except Exception:
            pass
    return reply


async def handle_incoming(app: Any, cfg: DingTalkChannelConfig, payload: dict,
                          reply_fn: Callable[[str], Any]) -> None:
    """入站消息处理(SDK 无关的纯逻辑,测试直接喂 payload + 假 reply_fn)。

    白名单 fail-closed → fence → drive → reply_fn(回复)。
    """
    sender, chat, text = _extract(payload)
    if not text:
        return
    if sender not in cfg.allow_senders:
        with _refused_lock:
            first = sender not in _refused_senders
            if first:
                _refused_senders.add(sender)
        if first:
            logger.info("[dingtalk] 白名单外 sender 被拒(不回 drive): %s", sender[:8] + "…" if sender else "?")
            try:
                reply_fn(REFUSAL_TEXT)
            except Exception:
                pass
        return
    # 不可信围栏在入站边界(群里的话是数据不是指令)——进 drive 前就包好。
    from karvyloop.cognition.fence import fence_untrusted
    fenced = fence_untrusted(text, source="dingtalk") or text
    reply = await drive_channel_message(app, cfg, text=fenced, chat_id=chat, sender=sender,
                                        raw_text=text)
    reply_fn(reply)


class DingTalkChannel:
    """钉钉 Stream 通道的宿主:起线程跑 SDK 长连接,消息桥回 console 事件循环。"""

    def __init__(self, app: Any, cfg: DingTalkChannelConfig) -> None:
        self._app = app
        self._cfg = cfg
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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

                def _reply(text: str) -> None:
                    holder["reply"] = text

                fut = asyncio.run_coroutine_threadsafe(
                    handle_incoming(channel._app, channel._cfg, data, _reply), loop)
                try:
                    await asyncio.to_thread(fut.result)
                except Exception as e:
                    logger.warning("[dingtalk] 入站处理失败: %s", e)
                    holder["reply"] = "(这条处理失败了,回 console 看日志)"
                text = holder.get("reply")
                if text:
                    try:
                        from dingtalk_stream import ChatbotMessage
                        msg = ChatbotMessage.from_dict(data)
                        self.reply_text(text, msg)
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
        logger.info("[dingtalk] 通道已起(Stream 长连接;绑定角色 %s)", self._cfg.role)
        return True

    def stop(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass


__all__ = ["DingTalkChannel", "drive_channel_message", "handle_incoming", "REFUSAL_TEXT"]
