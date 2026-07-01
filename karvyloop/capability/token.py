"""能力令牌：进程内可信引用 + 跨边界签名占位（capability/token.py）。

规格：docs/modules/capability.md §4（接 #5 §4 / schemas）。

M0 范围（Tier 1，进程内）：
  - `mint(task_id, grants, ttl)` → 可信 CapabilityToken（sig 留空）
  - `verify(token)` → 进程内仅校验 expiry、不校验 sig（sig="" 时视为进程内）
  - `is_expired` / `has_grant` 辅助
M1+ 范围（Tier 2/3 跨边界）：
  - HMAC 签名（broker 持私钥，子进程持公钥）
  - sig 不匹配 → VerificationError（fail-closed）
"""

from __future__ import annotations

import time
from typing import Iterable

from karvyloop.schemas import Capability, CapabilityToken


def mint(task_id: str, grants: Iterable[Capability], ttl_seconds: float = 3600.0) -> CapabilityToken:
    """签发一张进程内可信令牌。`sig=""` —— Tier 1 不签名。"""
    return CapabilityToken(
        task_id=task_id,
        grants=list(grants),
        expiry=time.time() + ttl_seconds,
        sig="",
    )


def is_expired(token: CapabilityToken) -> bool:
    return time.time() > token.expiry


def has_grant(token: CapabilityToken, resource: str, op: str) -> bool:
    """令牌是否覆盖 (resource, op) 对。

    匹配规则：
      - resource 精确匹配；op 精确或在 grants.ops 中
      - 空 ops 视作通配（覆盖所有 op）
    """
    for g in token.grants:
        if g.resource == resource and (not g.ops or op in g.ops):
            return True
    return False


def verify(token: CapabilityToken) -> None:
    """进程内验证。M0：仅校验 expiry。跨边界（M1+）再校验 sig。

    失败抛 ValueError（fail-closed）。"""
    if is_expired(token):
        raise ValueError(f"capability token {token.task_id} 已过期（expiry={token.expiry}）")
    # M0：进程内 sig="" 视为可信；非空则在 M1+ 校验签名
