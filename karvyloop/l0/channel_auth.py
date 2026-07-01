"""ChannelAuthPolicy — L0 频道发送权限(l0/channel_auth.py)。

F1-F3 不变量:白名单(role-based) + observer 永远不能 + 未注册频道返 False(不抛)。

设计:docs/24 §3.1。
"""
from __future__ import annotations

import dataclasses

from karvyloop.domain import Address


@dataclasses.dataclass(frozen=True)
class ChannelPermission:
    """一个频道的发送权限(白名单)。"""
    channel: str
    allowed_sender_roles: tuple[str, ...]    # 白名单(F3 强制)


# 5 个默认频道的发送权限
DEFAULT_PERMISSIONS: dict[str, ChannelPermission] = {
    "strategy":     ChannelPermission("strategy",     ("pm",)),
    "alert":        ChannelPermission("alert",        ("secops",)),
    "celebrate":    ChannelPermission("celebrate",    ("pm", "engineer", "user")),
    "ask-for-help": ChannelPermission("ask-for-help", ("pm", "engineer", "user")),
    "general":      ChannelPermission("general",      ("pm", "engineer", "user")),
}


class ChannelAuthPolicy:
    """频道发送权限策略(注入式,无全局)。"""

    def __init__(
        self,
        permissions: dict[str, ChannelPermission] | None = None,
    ) -> None:
        # 默**认**用** 5 **个**内**置**权**限**(**注**入**式**覆**盖**口**留**着**)
        self._perms = dict(permissions) if permissions else dict(DEFAULT_PERMISSIONS)

    def can_send(self, channel: str, from_: Address) -> bool:
        """返回发件人是否能发到这个频道。

        F1: observer 永远不能
        F2: 频道未注册返 False
        F3: 白名单检查(role in allowed_sender_roles)
        """
        # F1: observer 永远不能发(L0-L8 强**制**)
        if from_.role == "observer":
            return False
        # F2: 频道未注册
        perm = self._perms.get(channel)
        if perm is None:
            return False
        # F3: 白名单
        return from_.role in perm.allowed_sender_roles

    def permissions(self) -> dict[str, ChannelPermission]:
        return dict(self._perms)


class ChannelAuthError(RuntimeError):
    """频道权限不足(F1-F3 强**制**用**错**误**类**型**区**分**)。"""
