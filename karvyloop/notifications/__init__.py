"""通知意图基础设施：审批、outbox 与可插拔投递。"""
from .models import (DeliveryStatus, NotificationCommand, NotificationPolicy, NotificationReceipt, OutboxStatus, PolicyAction, PermanentDeliveryError, RetryableDeliveryError, RateLimitedError)
from .outbox import OutboxStore, default_notification_path
from .dispatcher import ChannelAdapter, Dispatcher
from .dingtalk import DingTalkAdapter
from .tool import make_notify_user_tool
from .runtime import (NotificationRuntime, build_notification_runtime,
                      notification_drive_kwargs, register_dingtalk_binding)

__all__ = ["ChannelAdapter", "DeliveryStatus", "Dispatcher", "NotificationCommand", "NotificationPolicy", "NotificationReceipt", "NotificationRuntime", "OutboxStatus", "PolicyAction", "PermanentDeliveryError", "RetryableDeliveryError", "RateLimitedError", "OutboxStore", "build_notification_runtime", "default_notification_path", "make_notify_user_tool", "notification_drive_kwargs", "register_dingtalk_binding"]
