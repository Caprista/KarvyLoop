"""Tool 契约 + build_tool 工厂 + TOOL_DEFAULTS（registry/tool.py）。

规格：docs/modules/registry.md §3 tool.py + §4 约束。
关键纪律（HR-1）：所有工具/技能**必须经 build_tool** 注册；禁止绕过工厂
直接构造 Tool（否则 fail-closed 默认丢失）。未经 build_tool 构造的 Tool
对象在 dispatch / exposed_tools 时会被识别为"裸"并 fail-closed。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from karvyloop.capability import Allow, Decision, Mode
from karvyloop.schemas import CapabilityToken


# ---- 工厂标记（HR-1：build_tool 单一入口）----
_BUILT_VIA_FACTORY = "_karvyloop_built_via_factory"


# ---- TOOL_DEFAULTS（HR-1 fail-closed）----
# 未声明 → 当写、当不可并发、当无权限放行默认（由 capability 决定）。
TOOL_DEFAULTS: dict[str, Any] = {
    "is_read_only": lambda inp: False,         # 未声明 → 当写
    "is_concurrency_safe": lambda inp: False,  # 未声明 → 当不可并发
    "is_enabled": lambda: True,
    "check_permissions": lambda inp, ctx: Allow(
        reason="default-passthrough(由 capability 决策链兜底)",
    ),
    "to_classifier_input": lambda inp: "",
    "max_result_size": 100_000,
    "required_mode": Mode.FULL,                # 未声明 → 最严
}


# ---- Tool 契约 ----

@dataclass
class Tool:
    """工具契约。实例必须经 build_tool 工厂（否则 fail-closed）。"""

    name: str
    description: str
    input_schema: dict
    # call(inp, token, sandbox) → 任意（CodingResult / dict / str）
    call: Callable[[dict, CapabilityToken, Any], Awaitable[Any]]
    # 能力声明（接收 input 动态判定）：
    is_read_only: Callable[[dict], bool] = lambda inp: False
    is_concurrency_safe: Callable[[dict], bool] = lambda inp: False
    is_enabled: Callable[[], bool] = lambda: True
    check_permissions: Callable[[dict, Any], Decision] = (
        lambda inp, ctx: Allow(reason="default-passthrough")
    )
    to_classifier_input: Callable[[dict], str] = lambda inp: ""
    required_mode: Mode = Mode.FULL
    max_result_size: int = 100_000
    # 内部标记：经 build_tool 构造则为 True；裸构造则为 False
    _factory_built: bool = field(default=False, repr=False, compare=False)
    # 可选：原始注册时的 defn（debug 用）
    _origin: str = field(default="bare", repr=False, compare=False)


def build_tool(
    *,
    name: str,
    description: str = "",
    input_schema: dict,
    call: Callable,
    is_read_only: Optional[Callable[[dict], bool]] = None,
    is_concurrency_safe: Optional[Callable[[dict], bool]] = None,
    is_enabled: Optional[Callable[[], bool]] = None,
    check_permissions: Optional[Callable[[dict, Any], Decision]] = None,
    to_classifier_input: Optional[Callable[[dict], str]] = None,
    required_mode: Optional[Mode] = None,
    max_result_size: Optional[int] = None,
) -> Tool:
    """**唯一工厂**：未声明字段用 TOOL_DEFAULTS 填（HR-1 fail-closed）。

    禁止直接 new Tool()。所有工具/技能注册都走这里。
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"build_tool: name 必填且非空字符串 (got {name!r})")
    if not isinstance(input_schema, dict):
        raise ValueError(f"build_tool: input_schema 必填且为 dict (got {type(input_schema).__name__})")
    if not callable(call):
        raise TypeError(f"build_tool: call 必填且可调用 (got {type(call).__name__})")
    t = Tool(
        name=name,
        description=description or f"tool {name}",
        input_schema=input_schema,
        call=call,
        is_read_only=is_read_only or TOOL_DEFAULTS["is_read_only"],
        is_concurrency_safe=is_concurrency_safe or TOOL_DEFAULTS["is_concurrency_safe"],
        is_enabled=is_enabled or TOOL_DEFAULTS["is_enabled"],
        check_permissions=check_permissions or TOOL_DEFAULTS["check_permissions"],
        to_classifier_input=to_classifier_input or TOOL_DEFAULTS["to_classifier_input"],
        required_mode=required_mode if required_mode is not None else TOOL_DEFAULTS["required_mode"],
        max_result_size=max_result_size if max_result_size is not None else TOOL_DEFAULTS["max_result_size"],
        _factory_built=True,
        _origin="build_tool",
    )
    return t


def is_factory_built(t: Tool) -> bool:
    """HR-1：识别裸 Tool（未走 build_tool）。"""
    return bool(getattr(t, "_factory_built", False))


__all__ = [
    "TOOL_DEFAULTS",
    "Tool",
    "build_tool",
    "is_factory_built",
]
