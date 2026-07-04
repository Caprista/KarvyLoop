"""工具编排：读写分区 + 并发/串行批（atoms/orchestration.py）。

规格：docs/modules/atom-executor.md §2.2（AC4-6）。
关键点：
  - 工具提供 `is_concurrency_safe(input)` —— 接收 input 动态判定（非工具级常量）
  - 抛异常 → 保守当非并发安全（fail-closed）
  - 连续并发安全合批；遇非安全断批；保持原序
  - **写后的并发安全读强制串行**（要看到最新 context；spec §2.2 step 5）
  - 并发批：受限并发池 MAX_CONCURRENT=10
  - 写批：逐个跑,前者副作用对后者立即可见
  - **不做** capability 判定（→ executor 层 token 边界已过；orchestration 信任调用方）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol

from karvyloop.schemas import CapabilityToken

MAX_CONCURRENT = 10


# ---- 工具协议（M0 占位；registry 模块会实现完整 Tool） ----

class Tool(Protocol):
    name: str

    async def __call__(self, input: dict) -> Any: ...

    def is_concurrency_safe(self, input: dict) -> bool: ...


@dataclass
class ToolUseBlock:
    """模型发出的单个 tool_use 块。"""

    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    """工具执行结果(回灌给模型)。"""
    tool_use_id: str
    name: str
    content: Any
    is_error: bool = False
    error_reason: str = ""


# ---- 串行执行 ----

async def _run_one(tool: Tool, block: ToolUseBlock) -> ToolResult:
    try:
        out = await tool(block.input)
        return ToolResult(tool_use_id=block.id, name=tool.name, content=out)
    except Exception as e:  # 工具异常 → is_error + 计入断路器
        return ToolResult(
            tool_use_id=block.id, name=tool.name,
            content=None, is_error=True, error_reason=type(e).__name__ + ":" + str(e),
        )


# ---- 并发批：受 Semaphore 限流 ----

async def _run_concurrent_batch(
    items: list[tuple[Tool, ToolUseBlock]],
) -> list[ToolResult]:
    """并发执行 items,受 MAX_CONCURRENT 限流。"""
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _guarded(tool: Tool, block: ToolUseBlock) -> ToolResult:
        async with sem:
            return await _run_one(tool, block)

    coros = [_guarded(t, b) for t, b in items]
    # return_exceptions=True 防止单个 tool 异常炸 gather
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: list[ToolResult] = []
    for r in results:
        if isinstance(r, BaseException):
            out.append(ToolResult(
                tool_use_id="?", name="?",
                content=None, is_error=True,
                error_reason=type(r).__name__ + ":" + str(r),
            ))
        else:
            out.append(r)
    return out


# ---- 分区：连续并发安全合批,遇非安全断批;写后读强制串行 ----

def _partition(
    blocks: list[ToolUseBlock],
    tools: dict[str, Tool],
) -> list[tuple[bool, list[tuple[Tool, ToolUseBlock]]]]:
    """返回 [(is_concurrent, items), ...]，保持原序。

    规则：
      - 同一 safe 类别连续合批
      - **写后出现的 read 强制串行**（spec §2.2 step 5:context 修改延后到批末串行应用）
      - unknown tool → 单元素串行批
    """
    batches: list[tuple[bool, list[tuple[Tool, ToolUseBlock]]]] = []
    cur_concurrent: Optional[bool] = None
    cur_items: list[tuple[Tool, ToolUseBlock]] = []
    seen_write_in_run = False  # 整个分区里是否见过写（一旦置 True 不重置）

    def _flush():
        nonlocal cur_concurrent, cur_items
        if cur_items:
            batches.append((cur_concurrent if cur_concurrent is not None else False, cur_items))
        cur_concurrent = None
        cur_items = []

    for b in blocks:
        t = tools.get(b.name)
        if t is None:
            _flush()
            batches.append((False, [(_UnknownTool(b.name), b)]))
            continue
        # 动态判定(抛异常 → fail-closed 非安全)
        try:
            safe = bool(t.is_concurrency_safe(b.input))
        except Exception:
            safe = False

        # 写后出现的读(spec:context 修改延后到批末串行应用)→ 强制断批写批
        if safe and seen_write_in_run:
            _flush()
            cur_concurrent = False
            cur_items.append((t, b))
            continue
        # 写 → 标记 seen_write(下一轮读要断批)
        if not safe:
            seen_write_in_run = True
        eff = safe  # 纯未写过时的读 → True;写 → False
        if cur_concurrent is None:
            cur_concurrent = eff
        if eff != cur_concurrent:
            _flush()
            cur_concurrent = eff
        cur_items.append((t, b))
    _flush()
    return batches


class _UnknownTool:
    """分区时遇到 unknown tool 用的占位(走串行,_run_one 会抛 unknown)。"""
    def __init__(self, name: str):
        self.name = name

    async def __call__(self, input: dict) -> Any:
        raise RuntimeError(f"unknown tool: {self.name}")

    def is_concurrency_safe(self, input: dict) -> bool:
        return False


# ---- 顶层入口 ----

# capability 判定回调类型:tool_name, tool_input → True/False
# 也接受返回 (ok, reason) 二元组 —— reason 在拒绝时上浮进 tool_result(诚实 reason,
# 模型看得见"为什么被拦"才能重规划,不是干瞪一个 capability_denied)。
CapabilityGate = Optional[Callable[[str, dict], Awaitable[bool]]]


async def run_tools(
    blocks: list[ToolUseBlock],
    tools: dict[str, Tool],
    token: CapabilityToken,  # noqa: ARG001 — 占位,M0 信任调用方已校验
    *,
    capability_check: CapabilityGate = None,
) -> list[ToolResult]:
    """对模型发出的 tool_use 块按 spec §2.2 执行。返回结果(顺序与 blocks 一致)。

    capability 判定由调用方通过 `capability_check` 注入;未注入则信任全部。
    被拒 → tool_result{is_error:true, reason:capability_denied}，不抛。
    """
    if not blocks:
        return []

    # 预过滤:被 capability 拒的 → 直接合成 is_error 结果,不进入 partition
    authorized: list[ToolUseBlock] = []
    denied_results: dict[str, ToolResult] = {}
    if capability_check is not None:
        for b in blocks:
            verdict = await capability_check(b.name, b.input)
            # 兼容两种返回:bool(旧)/ (ok, reason)(新;reason 在拒绝时上浮,诚实说清为何拦)
            if isinstance(verdict, tuple):
                ok, _reason = bool(verdict[0]), str(verdict[1] or "")
            else:
                ok, _reason = bool(verdict), ""
            if not ok:
                denied_results[b.id] = ToolResult(
                    tool_use_id=b.id, name=b.name,
                    content=None, is_error=True,
                    error_reason=("capability_denied: " + _reason) if _reason
                                 else "capability_denied",
                )
            else:
                authorized.append(b)
    else:
        authorized = list(blocks)

    # 分区 + 执行(仅 authorized)
    batches = _partition(authorized, tools)
    results_by_id: dict[str, ToolResult] = {}
    for is_conc, items in batches:
        if is_conc:
            rs = await _run_concurrent_batch(items)
        else:
            rs = [await _run_one(t, b) for t, b in items]
        for r in rs:
            results_by_id[r.tool_use_id] = r

    # 合并：按原 blocks 顺序
    out: list[ToolResult] = []
    for b in blocks:
        if b.id in denied_results:
            out.append(denied_results[b.id])
        elif b.id in results_by_id:
            out.append(results_by_id[b.id])
        else:
            out.append(ToolResult(
                tool_use_id=b.id, name=b.name,
                content=None, is_error=True, error_reason="dropped:unknown",
            ))
    return out
