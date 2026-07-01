"""任意 endpoint 抽象 —— EndpointRegistry。

**借业界 PlatformRegistry 思想**(clean-room 不抄代码),**自造**:
- 字段集**精简**到 5 个(它有 14+)
- 3 个内置 endpoint(cli / im-stub / silent)
- 全 Callable 注入(I7)

设计:docs/13 §3.2。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class EndpointEntry:
    """单个 endpoint 的元数据 + 工厂 + 投递函数。

    字段:
      name:           注册名("cli" / "im" / "silent" / ...)
      label:          人可读("CLI" / "IM 群")
      delivery_fn:    把 hint 文本投递到该端(异步安全,异常不影响主流程)
      is_available_fn: 该端是否在线(不在线 → 静默跳过,I3)
      format_hint_fn: hint 文本 → 该端格式(CLI 纯文本 / IM markdown)
    """
    name: str
    label: str
    delivery_fn: Callable[[str, str], None]      # (endpoint_name, hint_text) -> None
    is_available_fn: Callable[[], bool]
    format_hint_fn: Callable[[str], str] = lambda text: text   # 默认不格式化


class EndpointRegistry:
    """Endpoint 注册表。**全局**单例(endpoint_registry)。

    不变量(I7):全是 Callable 注入,无硬编码。
    """

    def __init__(self) -> None:
        self._entries: dict[str, EndpointEntry] = {}

    def register(self, entry: EndpointEntry) -> None:
        if entry.name in self._entries:
            logger.info("Endpoint '%s' re-registered", entry.name)
        self._entries[entry.name] = entry
        logger.debug("Registered endpoint: %s (%s)", entry.name, entry.label)

    def get(self, name: str) -> Optional[EndpointEntry]:
        return self._entries.get(name)

    def all_entries(self) -> list[EndpointEntry]:
        return list(self._entries.values())

    def is_registered(self, name: str) -> bool:
        return name in self._entries

    def create(self, name: str) -> Optional[EndpointEntry]:
        """AC1:create(unknown) → None;create(known) → entry。

        **不**做 is_available 校验(那是投递时的事)。
        """
        return self._entries.get(name)


# 模块级单例 —— 测试用 `endpoint_registry.register(...)` 注入
endpoint_registry = EndpointRegistry()


# ---- 3 个内置 endpoint ----

def _cli_is_available() -> bool:
    """CLI 端永远在线(本拍)。"""
    return True


def _cli_delivery(endpoint_name: str, hint_text: str) -> None:
    """CLI 端投递:写 stderr。**异常被调用方吞**(I2)。"""
    import sys
    print(f"\n[{endpoint_name}] {hint_text}", file=sys.stderr)


def _cli_format(hint_text: str) -> str:
    """CLI 纯文本,保留 emoji(终端一般支持)。"""
    return hint_text


def _im_stub_is_available() -> bool:
    """IM 端本拍**不**实现真投递,永远 False → 静默跳过(I3)。"""
    return False


def _im_stub_delivery(endpoint_name: str, hint_text: str) -> None:
    """IM stub —— 永远不会真跑(is_available 永远 False)。"""
    raise NotImplementedError("IM endpoint delivery 留给 M3 IM 拍")


def _im_stub_format(hint_text: str) -> str:
    """IM 端预留 markdown 格式(本拍不用)。"""
    return f"_{hint_text}_"


def _silent_is_available() -> bool:
    return True


def _silent_delivery(endpoint_name: str, hint_text: str) -> None:
    """silent 端 = 啥都不做(测试用)。"""
    pass


# ---- 自动注册(import 时完成)----

endpoint_registry.register(EndpointEntry(
    name="cli",
    label="CLI",
    delivery_fn=_cli_delivery,
    is_available_fn=_cli_is_available,
    format_hint_fn=_cli_format,
))

endpoint_registry.register(EndpointEntry(
    name="im",
    label="IM 群",
    delivery_fn=_im_stub_delivery,
    is_available_fn=_im_stub_is_available,
    format_hint_fn=_im_stub_format,
))

endpoint_registry.register(EndpointEntry(
    name="silent",
    label="Silent (test)",
    delivery_fn=_silent_delivery,
    is_available_fn=_silent_is_available,
))
