"""address — A2A 寻址(域 + 角色 + 可选 agent_id)。

设计:docs/19 §3.3。

注:Address 类型从 karvyloop.domain 复用(已在 karvyloop/domain/registry.py 定义)。
本文件提供 A2A 寻址的辅助函数。
"""
from __future__ import annotations

from karvyloop.domain import Address


def is_karvy_observer(addr: Address) -> bool:
    """判断地址是不是小卡 observer(K3 边界)。"""
    return addr.is_observer() and addr.agent_id == "karvy"


def is_cross_domain(from_domain: str, to_domain: str) -> bool:
    """判断是否跨域(A7 边界: TASK_ASSIGN 不跨域)。"""
    return from_domain != to_domain


def same_domain(a: Address, b: Address) -> bool:
    """两个地址是否同一域。"""
    return a.domain_id == b.domain_id
