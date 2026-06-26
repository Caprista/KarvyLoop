"""verify — 验证门（crystallize/verify.py）。

规格:docs/modules/crystallize.md §3 + §4 关 1
- 关 1:可验证 + 至少成功 1 次 → 才有资格谈结晶
- 没验证门的结晶 = 埋雷的缓存,绝不放行

M1 v1 实现:
  - verify_gate 是 sig 上的布尔标志,由外部(forge/executor)在跑出可验证
    成功(有 verify_proof)时调用 mark_verified() 写入
  - 验证数据结构 VerifyResult:proves that a particular success trace was
    verified by a verifier;保留 trace_ref 以满足 HR-7
  - 内存存在 VerifyStore(后续可换 sqlite)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VerifyResult:
    """单次验证结果(满足 HR-7 留 trace_ref)。"""

    sig: str
    trace_ref: str
    passed: bool
    at: float  # timestamp
    note: str = ""


class VerifyStore:
    """验证门存储。M1 v1:内存实现。

    - has_gate(sig): 该 sig 是否至少有一次 pass=True 验证
    - mark_verified(sig, trace_ref, note): 记录一次验证
    - latest_proof(sig): 取最近一次通过的验证(结晶时用)
    """

    def __init__(self) -> None:
        self._by_sig: dict[str, list[VerifyResult]] = {}
        self._lock = threading.Lock()

    def mark_verified(self, sig: str, trace_ref: str, *, note: str = "",
                      clock=time.time) -> VerifyResult:
        vr = VerifyResult(
            sig=sig,
            trace_ref=trace_ref,
            passed=True,
            at=clock(),
            note=note,
        )
        with self._lock:
            self._by_sig.setdefault(sig, []).append(vr)
        return vr

    def has_gate(self, sig: str) -> bool:
        with self._lock:
            results = self._by_sig.get(sig) or []
            return any(r.passed for r in results)

    def latest_proof(self, sig: str) -> Optional[VerifyResult]:
        with self._lock:
            results = self._by_sig.get(sig) or []
            passed = [r for r in results if r.passed]
            if not passed:
                return None
            return max(passed, key=lambda r: r.at)

    def proofs(self, sig: str) -> list[VerifyResult]:
        with self._lock:
            return list(self._by_sig.get(sig) or [])


__all__ = ["VerifyResult", "VerifyStore"]
