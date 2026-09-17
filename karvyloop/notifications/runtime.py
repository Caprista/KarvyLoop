"""notifications/runtime — notify_user 平台运行时接线。

职责(通知基础能力落地):
- 组装 OutboxStore + ChannelAdapter(钉钉)+ Dispatcher;
- 后台 dispatch loop:审批通过 / 预授权直接入队的通知,最迟一个 interval 内真实投递;
- 钉钉入站自动登记 recipient binding(白名单 sender = 平台侧已授权,consent=True);
- 给 REST/WS/定时/钉钉等 drive 入口统一产出 drive_in_tui 的 notification_* kwargs。

边界:Agent 只表达通知意图(notify_user 工具);access token、重试、限流、审计
全部收在本运行时 —— Agent 永不直接触达钉钉 API。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .models import NotificationPolicy

logger = logging.getLogger(__name__)

_DISPATCH_INTERVAL_S = 5.0


class NotificationRuntime:
    """Outbox + Adapters + 后台投递 loop 的宿主。"""

    def __init__(self, *, store: Any, adapters: dict[str, Any],
                 dispatch_interval_s: float = _DISPATCH_INTERVAL_S) -> None:
        from .dispatcher import Dispatcher
        if dispatch_interval_s <= 0:
            raise ValueError("dispatch_interval_s must be positive")
        self.store = store
        self.adapters = adapters
        self.dispatcher = Dispatcher(store=store, adapters=adapters)
        self.dispatch_interval_s = float(dispatch_interval_s)
        self._task: asyncio.Task | None = None

    async def dispatch_loop(self) -> None:
        """长生投递协程:console 用 _supervised_bg 包;独立使用可经 start()/stop()。"""
        while True:
            try:
                delivered = await self.dispatcher.dispatch_once(limit=10)
                if delivered:
                    logger.info("[notifications] 本轮投递 %s 条", delivered)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[notifications] dispatch tick 异常(下轮再试): %s", e)
            await asyncio.sleep(self.dispatch_interval_s)

    def start(self) -> None:
        """程序化启动(测试/嵌入式);console 走 _supervised_bg 更稳。"""
        if self._task is not None:
            return

        async def _runner() -> None:
            try:
                await self.dispatch_loop()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("[notifications] dispatch loop 意外退出")

        self._task = asyncio.get_running_loop().create_task(_runner())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.close()

    def close(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass


def build_notification_runtime(*, config_path: str | None = None,
                               dispatch_interval_s: float = _DISPATCH_INTERVAL_S,
                               store: Any = None) -> NotificationRuntime | None:
    """按 config.yaml 组装通知运行时。

    钉钉未配置 / adapter 全部构建失败 → None(console 侧零负担,notify_user 不挂)。
    多实例共用同一 client_id → 只建一个 adapter(同一应用,凭据相同)。
    """
    try:
        from karvyloop.config_channels import load_dingtalk_channel_configs
        cfgs = load_dingtalk_channel_configs(config_path)
    except Exception as e:
        logger.warning("[notifications] 钉钉配置读取失败(通知运行时不启动): %s", e)
        return None
    if not cfgs:
        return None

    from .dingtalk import DingTalkAdapter
    from .outbox import OutboxStore

    store = store if store is not None else OutboxStore()
    adapters: dict[str, Any] = {}
    for cfg in cfgs:
        if "dingtalk" in adapters:
            break
        try:
            adapters["dingtalk"] = DingTalkAdapter(cfg)
        except Exception as e:
            logger.warning("[notifications] 钉钉 adapter 构建失败(该实例跳过): %s", e)
    if not adapters:
        return None
    # 平台所有者映射:第一个白名单 sender(current_user 兜底解析目标)。
    store.owner_user_id = next((s for cfg in cfgs for s in (cfg.allow_senders or ()) if s), "")
    # 开关播种(声明式:consent 跟随配置,改配置重启即升降档):
    # notify_auto_approve=True 的实例 → 白名单 sender 的 direct 绑定建成 consent=auto(2),
    # 且该实例名下**已有**绑定(含群聊)全部对齐到 auto;=False → 全部回 1(恢复审批)。
    for cfg in cfgs:
        tenant = cfg.client_id or ""
        auto = bool(getattr(cfg, "notify_auto_approve", False))
        consent = NotificationPolicy.CONSENT_AUTO if auto else 1
        try:
            store.reconcile_tenant_consent(tenant=tenant, consent=consent)
        except Exception:
            logger.debug("[notifications] 绑定 consent 同步失败(旁路忽略)", exc_info=True)
        for sender in (cfg.allow_senders or ()):
            if not sender:
                continue
            try:
                store.upsert_binding(platform_user=sender, channel="dingtalk",
                                     tenant=tenant, address_type="direct",
                                     address_id=sender, enabled=True, consent=consent)
            except Exception:
                logger.debug("[notifications] auto 绑定播种失败(旁路忽略)", exc_info=True)
    return NotificationRuntime(store=store, adapters=adapters,
                               dispatch_interval_s=dispatch_interval_s)


def register_dingtalk_binding(store: Any, cfg: Any, *, sender: str,
                              chat: str, chat_type: str) -> None:
    """钉钉入站自动登记 recipient binding(白名单 sender 才走到这)。

    - direct:platform_user=address_id=staffId → user:<staffId> / current_user 可达;
    - group:再登记一条会话绑定(conversation_address=openConversationId)→
      conversation:<cid> 可达;platform_user 借 "conversation:" 前缀做行标识,
      address_id 借 chat 满足唯一约束(投递地址仍取 conversation_address);
    - consent 跟随 cfg.notify_auto_approve(单聊+群聊一致,True=auto 免审直投);
    best-effort:登记失败只 debug,不挡消息驱动。
    """
    if not sender:
        return
    consent = int(NotificationPolicy.CONSENT_AUTO) if bool(getattr(cfg, "notify_auto_approve", False)) else 1
    try:
        store.upsert_binding(platform_user=sender, channel="dingtalk",
                             tenant=getattr(cfg, "client_id", "") or "",
                             address_type="direct", address_id=sender,
                             enabled=True, consent=consent)
        if (chat_type or "").strip().lower() == "group" and chat:
            store.upsert_binding(platform_user=f"conversation:{chat}", channel="dingtalk",
                                 tenant=getattr(cfg, "client_id", "") or "",
                                 address_type="group", address_id=chat,
                                 conversation_address=chat, enabled=True, consent=consent)
    except Exception:
        logger.debug("[notifications] 钉钉 binding 登记失败(旁路忽略)", exc_info=True)


def notification_drive_kwargs(app: Any, *, task_id: str = "", trace_ref: str = "") -> dict[str, Any]:
    """drive_in_tui 的 notification_* kwargs(运行时未接 → 空 dict = 0 回归)。"""
    rt = getattr(getattr(app, "state", None), "notification_runtime", None)
    if rt is None:
        return {}
    return {
        "notification_store": rt.store,
        "notification_task_id": task_id or "",
        "notification_trace_ref": trace_ref or "",
    }


__all__ = ["NotificationRuntime", "build_notification_runtime",
           "register_dingtalk_binding", "notification_drive_kwargs"]
