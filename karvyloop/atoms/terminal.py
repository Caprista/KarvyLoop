"""原子终止原因（atoms/terminal.py）。

规格：docs/modules/atom-executor.md §2.3（HR-11 区分终止语义）。
每种独立 reason,上层可据此决定结晶/重试/告警策略。
"""

from __future__ import annotations

import re
from enum import Enum


class Terminal(Enum):
    COMPLETED = "completed"            # 主循环正常结束(无 tool_use)
    MAX_TURNS = "max_turns"            # 超过 max_turns 上限
    CIRCUIT_OPEN = "circuit_open"      # 连续失败超断路阈值
    ABORTED_STREAMING = "aborted_streaming"   # 流式传输中被中断
    ABORTED_TOOLS = "aborted_tools"    # 工具执行阶段被中断
    HOOK_STOPPED = "hook_stopped"      # hook 强制停
    BLOCKING_LIMIT = "blocking_limit"  # token/成本预算耗尽
    INFRA_DEAD = "infra_dead"          # 基础能力失效:网关/网络/模型解析不可用


# ---- 终止语义分类(docs/02 §15 Pursuit Loop:role 重规划 vs 不白爬阶梯)----
# infra-dead = 基础能力没了(token 调不通/网络断/模型解析失败)。这**不是 planning 问题**:
# role 重规划同一条路也没用 → 上层(尽责下属阶梯)必须**立刻 fail-loud 标 infra,不进 replan**。
# 其余非 COMPLETED 终止(MAX_TURNS/CIRCUIT_OPEN/...)= planning 不够稳 → role 可重规划。
_INFRA_DEAD: frozenset[Terminal] = frozenset({Terminal.INFRA_DEAD})


def is_infra_dead(terminal: object) -> bool:
    """是否"基础能力失效"——上层据此决定 fail-loud(不重规划)。容忍 str/None 入参。"""
    if isinstance(terminal, Terminal):
        return terminal in _INFRA_DEAD
    if isinstance(terminal, str):
        return terminal == Terminal.INFRA_DEAD.value
    return False


def is_replannable(terminal: object) -> bool:
    """atom 没跑成、但属于"planning 不够稳"可由 role 重规划的那类(非 infra、非正常完成)。"""
    if isinstance(terminal, str):
        try:
            terminal = Terminal(terminal)
        except ValueError:
            return False
    if not isinstance(terminal, Terminal):
        return False
    return terminal not in _INFRA_DEAD and terminal != Terminal.COMPLETED


# ---- 异常 → 终止语义分类(可观测性收敛②:infra-dead 白名单化)----
# 病根:执行器曾把调模型路径的**一切**异常吞成 INFRA_DEAD("模型/网络调不通")——
# 一个 TypeError(代码 bug,如 to_blocks 少 kwarg)被误诊成网络问题,整条慢脑全灭还查错方向。
# 纪律:**infra-dead 判定必须白名单式** —— 网络/超时/认证类才算 infra;
# TypeError/AttributeError/KeyError 等代码缺陷返 None,调用方必须 fail-loud 上冒原始异常链。

# HTTP 状态码里算 infra 的:认证(401/403/407)、请求超时(408)、限流(429)。5xx 另判(服务端不可用)。
_INFRA_HTTP_STATUS = frozenset({401, 403, 407, 408, 429})
# 传输层异常的来源模块(顶层包名):这些库抛的"非状态码"异常都是连接/超时/协议 IO 类。
_TRANSPORT_MODULES = frozenset({
    "httpx", "httpcore", "aiohttp", "requests", "urllib3", "urllib",
    "socket", "ssl", "anyio", "h11", "h2",
})


def _http_status_of(exc: BaseException):
    """从 httpx.HTTPStatusError 一类异常上取状态码(拿不到 = None)。"""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def classify_model_call_exception(exc: BaseException) -> "Terminal | None":
    """调模型(gateway.complete)抛的异常 → 终止语义。**白名单式**:

    - 网络/超时/socket/ssl(OSError 家族、TimeoutError、httpx/aiohttp 等传输层)→ INFRA_DEAD
    - HTTP 认证/限流/超时状态(401/403/407/408/429)与 5xx(服务端不可用)→ INFRA_DEAD
    - 预算/上下文天花板(SpendBudgetExceeded / ContextCeilingError,系统**有意**拒发)→ BLOCKING_LIMIT
      (它们不是"网络调不通",按预算类语义报,提示语才对得上真因)
    - 其余(TypeError/AttributeError/KeyError… = 代码缺陷,含 4xx 坏请求 = 请求体/协议 bug)
      → **None**:调用方必须 fail-loud 上冒原始异常链,绝不吞成"模型/网络调不通"。
    """
    # 系统有意抛的预算/天花板闸(不属于 infra,也绝不该当代码缺陷上冒成崩溃)
    try:
        from karvyloop.llm.spend_budget import SpendBudgetExceeded
        if isinstance(exc, SpendBudgetExceeded):
            return Terminal.BLOCKING_LIMIT
    except Exception:
        pass
    try:
        from karvyloop.gateway.client import ContextCeilingError
        if isinstance(exc, ContextCeilingError):
            return Terminal.BLOCKING_LIMIT
    except Exception:
        pass
    # 白名单 1:内建网络/超时家族(socket/ssl 错误都是 OSError 子类;3.11+ asyncio.TimeoutError=TimeoutError)
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return Terminal.INFRA_DEAD
    # 白名单 2:HTTP 客户端库异常(按模块判,不硬依赖 httpx 类型)
    mod = (type(exc).__module__ or "").split(".", 1)[0]
    if mod in _TRANSPORT_MODULES:
        status = _http_status_of(exc)
        if status is None:
            return Terminal.INFRA_DEAD          # 连接/超时/读写等传输层错误
        if status in _INFRA_HTTP_STATUS or status >= 500:
            return Terminal.INFRA_DEAD          # 认证/限流/超时/服务端不可用
        return None                              # 其余 4xx(400/404/422…)= 请求体/协议 bug → 上冒
    # 白名单 3:网关自己的"没这个 api 的 adapter"(配置/能力缺失,非代码缺陷)
    try:
        from karvyloop.gateway.providers.base import UnsupportedApiError
        if isinstance(exc, UnsupportedApiError):
            return Terminal.INFRA_DEAD
    except Exception:
        pass
    return None


# adapter 流内异常被归一化成 ErrorEvent(kind=原异常类名, message=str(e),不穿透)——
# 这些 kind 是代码缺陷,绝不归 infra:executor 据此重建 fail-loud(可观测性②:
# 此前 executor 无 ErrorEvent 分支,真网络断会变成"COMPLETED + 空输出"的静默假成功)。
_CODE_DEFECT_KINDS = frozenset({
    "TypeError", "AttributeError", "KeyError", "IndexError", "NameError",
    "ZeroDivisionError", "AssertionError", "UnboundLocalError", "RecursionError",
    "NotImplementedError",
})

_HTTP_STATUS_IN_MESSAGE = re.compile(r"'(\d{3}) ")   # httpx: "Client error '401 Unauthorized' for url …"


def classify_error_event(kind: str, message: str) -> "Terminal | None":
    """adapter ErrorEvent(kind=原异常类名)→ 终止语义。

    adapter 的 try 只包传输/SSE 解析段(build_request 在 try 之外),所以这里的 kind
    绝大多数是传输层错误 → 默认 INFRA_DEAD;两类例外:
    - 代码缺陷 kind(TypeError/KeyError…,如 _normalize 里的 bug)→ None:executor 重建
      fail-loud 上冒(带 kind+message,真因可见);
    - HTTPStatusError 且状态码是 400/404/422 等坏请求 → None(请求体/协议 bug,同上冒;
      认证/限流/超时/5xx 照旧 INFRA_DEAD;状态码解析不出 → 保守 INFRA_DEAD)。
    """
    k = (kind or "").strip()
    if k in _CODE_DEFECT_KINDS:
        return None
    if k == "HTTPStatusError":
        m = _HTTP_STATUS_IN_MESSAGE.search(message or "")
        if m:
            status = int(m.group(1))
            if status in _INFRA_HTTP_STATUS or status >= 500:
                return Terminal.INFRA_DEAD
            return None   # 4xx 坏请求 = 请求体/协议 bug → 上冒
        return Terminal.INFRA_DEAD
    return Terminal.INFRA_DEAD


def classify_resolve_exception(exc: BaseException) -> "Terminal | None":
    """解析模型(gateway.resolve_model)抛的异常 → 终止语义。白名单:

    - UnknownModelError / ValueError / RuntimeError(没有可用模型/配置不合法)→ INFRA_DEAD
      (基础能力没了,role 重规划同一条路也没用;docs/02 §15)
    - 其余(TypeError/AttributeError/裸 KeyError… = 代码缺陷)→ None:fail-loud 上冒。
      注意 UnknownModelError 是 KeyError 子类 —— 白的是**它**,不是 KeyError 全家。
    """
    try:
        from karvyloop.gateway.registry import UnknownModelError
        if isinstance(exc, UnknownModelError):
            return Terminal.INFRA_DEAD
    except Exception:
        pass
    if isinstance(exc, KeyError):
        return None   # 裸 KeyError = 代码缺陷(上面已放行 UnknownModelError)
    if isinstance(exc, (ValueError, RuntimeError)):
        return Terminal.INFRA_DEAD
    return None
