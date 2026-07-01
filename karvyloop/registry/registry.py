"""ToolRegistry：注册/枚举/按 capability 收窄（registry/registry.py）。

规格：docs/modules/registry.md §3 registry.py + §4 约束。
关键纪律（HR-6）：
  - exposed_tools 是发给模型的**唯一**工具来源
  - 配置期收窄：不满足前置能力的危险工具根本不进 schema
  - dispatch 必须对未知工具返回 error 不抛异常（双保险）
  - 工具搜索兜底：schema 体量超窗口阈值才启用（v1 范围外,留 hook）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Optional

from karvyloop.atoms.orchestration import ToolResult
from karvyloop.capability import Mode
from karvyloop.schemas import CapabilityToken

from .tool import Tool, is_factory_built


# 工具搜索阈值（HR-6 + spec §3 registry.py）。
# 估算：单 tool schema 约 200 token；10 个 tool ≈ 2K token；25 个 ≈ 5K token。
# 阈值 8K token 触发搜索（留余量给 system prompt）。
TOOL_SEARCH_THRESHOLD_TOKENS = 8_000

# 工具 schema 平均 token 估算（粗,4 字符/token）
_CHARS_PER_TOKEN = 4


def _estimate_tokens(tools: list[dict]) -> int:
    """粗估 schema 体量:每 tool 取 name+description+input_schema 字数。"""
    total_chars = 0
    for t in tools:
        total_chars += len(t.get("name", "")) + len(t.get("description", ""))
        total_chars += len(json.dumps(t.get("input_schema", {}), ensure_ascii=False))
    return total_chars // _CHARS_PER_TOKEN


def _mode_strictly_above(higher: Mode, lower: Mode) -> bool:
    """Mode 没有 __gt__;靠 __ge__ + 不等判定。"""
    if higher is lower:
        return False
    return higher >= lower


@dataclass
class ToolRegistry:
    """工具注册表。M0 单例使用；不持久化（→ 启动时 build）。"""

    _tools: dict[str, Tool] = field(default_factory=dict)
    # 可选:超出阈值的工具是否启用搜索(v1 留 hook,默认 False)
    _search_enabled: bool = field(default=False, init=False, repr=False)

    # ---- 注册 ----
    def register(self, tool: Tool) -> None:
        """注册一个 Tool。HR-1:必须是经 build_tool 工厂构造的(否则 fail-closed)。"""
        if not is_factory_built(tool):
            raise ValueError(
                f"HR-1: 工具 {tool.name!r} 未走 build_tool 工厂(fail-closed 丢失)。"
                "请用 karvyloop.registry.build_tool(...) 注册。"
            )
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """移除工具(测试/热更新用)。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    # ---- 暴露给模型(HR-6 配置期收窄)----
    def exposed_tools(
        self,
        token: Optional[CapabilityToken] = None,
        mode: Mode = Mode.FULL,
    ) -> list[dict]:
        """返回发给模型的工具 schema 列表。

        过滤规则(按顺序):
          1) is_enabled() = False → 跳过
          2) tool.required_mode > mode → 跳过(危险工具不暴露)
          3) capability 不满足(token 缺前置 grant) → 跳过

        输出按 name 字母序稳定(同输入字节一致 → 喂缓存)。
        """
        out: list[dict] = []
        for t in sorted(self._tools.values(), key=lambda x: x.name):
            if not t.is_enabled():
                continue
            if _mode_strictly_above(t.required_mode, mode):
                continue
            if token is not None and not self._capability_satisfiable(t, token):
                continue
            out.append({
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            })
        # 工具搜索兜底(HR-6 + spec §3):体量超阈值才启用
        # v1 范围外,留 hook:若 _search_enabled 且超出阈值,本应改按需检索;
        # 此处只标记 truncated 字段(让上层决定)。M0 不实现 search.index。
        if _estimate_tokens(out) > TOOL_SEARCH_THRESHOLD_TOKENS:
            # v1 行为:返回全量 + 标志位(便于上层切到搜索模式)
            # 严格按 spec:若实现 search 应改 return search.as_searchable(out)
            # 此处暂以 dict 形式携带提示,避免破坏 schema 形状
            return [{"_tool_search": True, "total": len(out)}]
        return out

    def _capability_satisfiable(self, tool: Tool, token: CapabilityToken) -> bool:
        """工具的 required_mode 是否被 token 的 grants 满足。

        简化实现:M0 不解析工具的"实际需要什么 grant",只用 required_mode
        粗判定(FULL 必给、READ_ONLY 不需要 grant)。后续接入 capability.broker
        解析 input_schema.required_capabilities 时再细化。
        """
        if tool.required_mode == Mode.READ_ONLY:
            return True
        if tool.required_mode == Mode.WORKSPACE_WRITE:
            # 至少需要一个 fs 写 grant
            for g in token.grants:
                if "fs" in g.resource and "write" in g.ops:
                    return True
            return False
        # FULL: 任意 cap 都需显式 grant(粗判定:有 grant 即满足)
        return len(token.grants) > 0

    # ---- dispatch(HR-6 双保险)----
    async def dispatch(
        self,
        name: str,
        inp: dict,
        token: CapabilityToken,
        sandbox: Any = None,
    ) -> ToolResult:
        """分发一次工具调用。未知工具/未注册 → 返回 is_error=True,不抛异常。"""
        t = self._tools.get(name)
        if t is None:
            return ToolResult(
                tool_use_id="", name=name,
                content=None, is_error=True,
                error_reason=f"unknown tool {name!r}(HR-6:registry 未注册)",
            )
        if not t.is_enabled():
            return ToolResult(
                tool_use_id="", name=name,
                content=None, is_error=True,
                error_reason=f"tool {name!r} disabled",
            )
        try:
            content = await t.call(inp, token, sandbox)
        except Exception as e:  # 双保险:工具异常 → 错误结果,不崩
            return ToolResult(
                tool_use_id="", name=name,
                content=None, is_error=True,
                error_reason=f"{type(e).__name__}: {e}",
            )
        return ToolResult(
            tool_use_id="", name=name,
            content=content, is_error=False,
        )


__all__ = [
    "ToolRegistry",
    "TOOL_SEARCH_THRESHOLD_TOKENS",
    "_estimate_tokens",
]
