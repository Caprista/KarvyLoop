"""capability/seams — 能力缝(capability seam):后端能力的"定义/提供者/消费者"三相。

借鉴业界 agent runtime 的做法(一个后端能力 = 一张接口 + 一个可换实现 + 一群消费者):
**provider 一换,整套能力整体迁移**——换沙箱后端(如未来远程沙箱)只改一处注册,
而不是逐个工具改注入。这是把 KarvyLoop 既有的"半成品 seam"(沙箱已有 Protocol+平台
实现+工厂,但工具层各自直抓实例)收敛成正式三相。

设计纪律(守"少脚手架,多信模型"):
- **只做两件事**:注册 provider / 解析 provider。不引入插件树、effect、waterfall。
- **薄**:CapabilitySlot 是定义槽,SeamRegistry 是一张 dict;全局单例 SEAMS。
- **fail-soft**:未注册的槽 resolve → None,调用方回退到现状(直传 sandbox 参数)。
- **不动既有门**:刀1(authorize/outbound/computer 截)与 policy 下限表原样,本模块
  只管"后端能力从哪来",不管"这次调用放不放行"。

现状锚点:sandbox/base.py 的 `Sandbox` Protocol、selector.py 的 `default_sandbox()`
已经是松散三相;本模块把"选出的实现"登记进注册表,让 coding/tools 从缝解析,不再直抓。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .policy import Mode


@dataclass(frozen=True)
class CapabilitySlot:
    """能力定义槽(Service Definition):一张接口 + 默认模式下限。

    default_mode 是给"该能力未在 policy 表显式声明时"的渐进下限(Hardy 2026-08-20 拍:
    先 WORKSPACE_WRITE 上线,稳定后再切 FULL)。本批不接线到 required_mode,仅作元数据。
    """
    name: str                 # "sandbox" | "fs" | "shell" | ...
    interface: Any = None     # 期望的 Protocol/抽象类型(仅作文档/类型提示,运行时 duck-type)
    default_mode: Mode = Mode.WORKSPACE_WRITE


class SeamRegistry:
    """能力缝注册表:slot_name → provider。

    线程安全(register/resolve 加锁);register 幂等(同 slot 覆盖 = provider 迁移)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: Dict[str, Any] = {}
        self._slots: Dict[str, CapabilitySlot] = {}

    def register_slot(self, slot: CapabilitySlot) -> None:
        """登记一个能力定义槽(幂等)。"""
        with self._lock:
            self._slots[slot.name] = slot

    def slot(self, name: str) -> Optional[CapabilitySlot]:
        return self._slots.get(name)

    def register_provider(self, slot: str, provider: Any) -> None:
        """注册/替换某槽的 provider(幂等;换后端=再注册一次)。"""
        if not slot:
            return
        with self._lock:
            self._providers[slot] = provider

    def resolve(self, slot: str) -> Any:
        """取某槽当前 provider;未注册 → None(调用方 fail-soft 回退)。"""
        return self._providers.get(slot)

    def clear(self) -> None:
        """测试用:清空(不影响 slot 定义)。"""
        with self._lock:
            self._providers.clear()


# 进程级全局注册表。默认 provider 由 sandbox/selector.default_sandbox() 在选用后注册。
SEAMS = SeamRegistry()

# 三个核心后端能力的定义槽(本批范围;MCP/computer-use/skills 下批再进)。
SLOT_SANDBOX = CapabilitySlot("sandbox")
SLOT_FS = CapabilitySlot("fs")
SLOT_SHELL = CapabilitySlot("shell")

for _s in (SLOT_SANDBOX, SLOT_FS, SLOT_SHELL):
    SEAMS.register_slot(_s)


__all__ = [
    "CapabilitySlot", "SeamRegistry", "SEAMS",
    "SLOT_SANDBOX", "SLOT_FS", "SLOT_SHELL",
]
