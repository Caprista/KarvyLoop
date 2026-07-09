"""reconcile_receipt 工具（coding/tools/reconcile.py）—— 报销的**确定性算术**能力,给 atom/role 调。

role-atom-skill-tool 定位:这是 **tool**(最底层可执行,纯确定性、不烧 token、不担责 → 执行 loop 的东西)。
expense **skill** 在 `allowed-tools` 里声明它、方法里教何时调;报销员 **role** 组合该 skill 即得此能力。
底层就一句:把 LLM 抽出来的数字(低把握留 null)交给 receipt_solver.arithmetic_reconcile 反解/核对。

与其它 CodingTool 同构(name/description/parameters + async __call__),让 make_coding_tools 统一注入、
atoms/orchestration 当 Tool 协议消费。只读、纯计算 —— 不碰沙箱/文件,构造参数一律忽略。
"""

from __future__ import annotations

from typing import Any


class ReconcileReceiptTool:
    name = "reconcile_receipt"
    description = (
        "Deterministically reconcile a receipt's numbers using the receipt's OWN arithmetic "
        "(unit_price×qty=amount, Σamounts=subtotal, subtotal+tax=total). Reverse-solves values you "
        "left null when the math pins them down, flags what it can't determine, and NEVER guesses. "
        "Call this with the fields you extracted (leave low-confidence numbers as null); trust its "
        "output over your own mental arithmetic, and surface its `flags`. Pure/deterministic — no model."
    )
    parameters = {
        "type": "object",
        "properties": {
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "qty": {"type": ["number", "null"]},
                        "unit_price": {"type": ["number", "null"]},
                        "amount": {"type": ["number", "null"]},
                    },
                },
            },
            "subtotal": {"type": ["number", "null"]},
            "tax": {"type": ["number", "null"]},
            "total": {"type": ["number", "null"]},
            "fees": {"type": ["number", "null"]},
        },
        "required": ["line_items"],
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # 纯计算工具:make_coding_tools 会传 sandbox/file_state/workspace_root/token —— 全忽略。
        pass

    def is_concurrency_safe(self, inp: dict) -> bool:
        return True  # 纯函数,无副作用

    async def __call__(self, input: dict) -> Any:
        from karvyloop.receipt_solver import arithmetic_reconcile
        try:
            return arithmetic_reconcile(input or {})
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常,诚实返错
            return {"ok": False, "error": f"reconcile 失败:{type(e).__name__}: {e}",
                    "reconciled": [], "flags": [], "balanced": False}
