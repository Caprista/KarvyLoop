"""severity 3 档 —— 借 openclaw HealthFinding + parseHealthFindingSeverity 思想。

设计:docs/15 §3.3。
"""
from __future__ import annotations

# 3 档(可扩展到 5,留接口 M3+)
ERROR: str = "error"
WARNING: str = "warning"
INFO: str = "info"

# 排序:E5 不变量 —— error > warning > info
_RANK: dict[str, int] = {
    ERROR: 0,
    WARNING: 1,
    INFO: 2,
}


class Severity(str):
    """类型化 severity(继承 str 便于 yaml/json 序列化)。"""
    pass


def severity_rank(sev: str) -> int:
    """E5:返回排序 rank(数字越小越严重)。"""
    if sev not in _RANK:
        # 未知 → 兜底为 info(最不严重,**不**抛 — E2)
        return _RANK[INFO]
    return _RANK[sev]


def is_more_severe(a: str, b: str) -> bool:
    """a 比 b 严重 → True。"""
    return severity_rank(a) < severity_rank(b)
