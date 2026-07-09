"""receipt_solver — 票据算术求解/纠错(**确定性,不靠模型**)。

Hardy 的架构初衷:OCR+LLM 拆开是为了防模型下降。这一层把它推到底 —— **一张票据是个冗余的
数学系统**(`单价×数量=金额`、`Σ金额=小计`、`小计+税(+服务费)=总额`),这些互相冗余的约束能
**反解被 OCR 读错/漏读的数**,而且**全程纯算术、一个 token 都不烧、模型再弱也不塌**。

与①(逐段置信度)配合:LLM 见到 `865⟦?0.57⟧` 这种低把握的数就**留 null**(别把蒙的数传下来),
本层再用约束把它**唯一钉死时反解回来**;钉不死(欠定/冲突)就 flag,**绝不猜**。

纪律:
- 只在"约束把某值**唯一确定**"时纠(单一未知求解);多个未知=欠定 → 只填能填的、其余 flag。
- 全 present 但违反约束 → **不盲改**(不知道哪个错)→ flag,把自洽的子关系点出来供人判。
- 钱按分容差(默认 0.02 或 0.5%),避开浮点毛刺;负数/0 价(免费项)安全跳过。
"""
from __future__ import annotations

from typing import Any, Optional


def _num(x: Any) -> Optional[float]:
    """尽量转成 float;转不了 → None(不崩)。"""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _close(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol, abs(b) * 0.005)


def _r2(x: float) -> float:
    return round(x + 0.0, 2)


def arithmetic_reconcile(data: dict, *, tol: float = 0.02, max_passes: int = 4) -> dict:
    """确定性算术求解/纠错。

    入参(缺项用 None):{"line_items":[{"name","qty","unit_price","amount"}...],
                       "subtotal","tax","total","fees"(可选,服务费/小费之和)}。
    返回浅拷贝 + 填/纠后的值 + ``reconciled``(改了啥·为什么·从→到)+ ``flags``(定不了的)
    + ``balanced``(最终账平否)。**只填/纠约束唯一确定的,其余 flag,绝不猜。**
    """
    out: dict[str, Any] = dict(data or {})
    items = [dict(it) for it in (out.get("line_items") or [])]
    out["line_items"] = items
    for k in ("subtotal", "tax", "total", "fees"):
        out[k] = _num(out.get(k))
    reconciled: list[dict[str, Any]] = []
    flags: list[str] = []

    def rec(field: str, frm, to, why: str) -> None:
        reconciled.append({"field": field, "from": frm, "to": _r2(to), "why": why})

    for _ in range(max_passes):
        changed = False

        # ① 行级:单价×数量=金额,恰缺一个 → 反解;三个都在但违约束 → flag(不盲改)
        for i, it in enumerate(items):
            q, u, a = _num(it.get("qty")), _num(it.get("unit_price")), _num(it.get("amount"))
            name = it.get("name") or f"#{i + 1}"
            known = sum(v is not None for v in (q, u, a))
            if known == 2:
                if a is None and q is not None and u is not None:
                    it["amount"] = _r2(q * u); rec(f"item[{i}].amount", None, q * u,
                        f"'{name}': 单价{u}×数量{q} 反解金额"); changed = True
                elif u is None and q not in (None, 0) and a is not None:
                    it["unit_price"] = _r2(a / q); rec(f"item[{i}].unit_price", None, a / q,
                        f"'{name}': 金额{a}÷数量{q} 反解单价"); changed = True
                elif q is None and u not in (None, 0) and a is not None:
                    it["qty"] = _r2(a / u); rec(f"item[{i}].qty", None, a / u,
                        f"'{name}': 金额{a}÷单价{u} 反解数量"); changed = True
            elif known == 3 and a != 0 and not _close(q * u, a, tol):
                flags.append(f"行'{name}': 单价{u}×数量{q}={_r2(q * u)} ≠ 金额{a}(读错了一个,未盲改)")

        amts = [_num(it.get("amount")) for it in items]
        have_all_amts = bool(amts) and all(x is not None for x in amts)
        n_missing_amt = sum(x is None for x in amts)
        S = _r2(sum(x for x in amts if x is not None)) if amts else None

        # ② Σ金额 = 小计
        sub = out.get("subtotal")
        if have_all_amts and S is not None:
            if sub is None:
                out["subtotal"] = S; rec("subtotal", None, S, "Σ明细金额 反解小计"); changed = True
            elif not _close(S, sub, tol):
                flags.append(f"Σ明细 {S} ≠ 小计 {sub}(差 {_r2(S - sub)};有低把握项就该留 null 让本层反解)")
        elif sub is not None and n_missing_amt == 1:
            known_sum = sum(x for x in amts if x is not None)
            missing = _r2(sub - known_sum)
            for it in items:
                if _num(it.get("amount")) is None:
                    it["amount"] = missing
                    rec("item[?].amount", None, missing,
                        f"小计{sub} − 其余明细和{_r2(known_sum)} 反解唯一缺失项 '{it.get('name') or '?'}'")
                    changed = True
                    break

        # ③ 小计 + 税 (+ 服务费) = 总额
        sub, tax, fees, tot = out.get("subtotal"), out.get("tax"), out.get("fees") or 0.0, out.get("total")
        trio = [sub, tax, tot]
        if sum(v is not None for v in trio) == 2:
            if tot is None and sub is not None and tax is not None:
                out["total"] = _r2(sub + tax + fees); rec("total", None, sub + tax + fees,
                    "小计+税(+服务费) 反解总额"); changed = True
            elif tax is None and sub is not None and tot is not None:
                out["tax"] = _r2(tot - sub - fees); rec("tax", None, tot - sub - fees,
                    "总额−小计(−服务费) 反解税"); changed = True
            elif sub is None and tax is not None and tot is not None:
                out["subtotal"] = _r2(tot - tax - fees); rec("subtotal", None, tot - tax - fees,
                    "总额−税(−服务费) 反解小计"); changed = True
        elif all(v is not None for v in trio) and not _close(sub + tax + fees, tot, tol):
            flags.append(f"小计{sub}+税{tax}{'+费' + str(fees) if fees else ''}={_r2(sub + tax + fees)} ≠ 总额{tot}")

        if not changed:
            break

    # 最终账平判定(有小计+税+总额且自洽,或纯明细求和=总额)
    sub, tax, fees, tot = out.get("subtotal"), out.get("tax"), out.get("fees") or 0.0, out.get("total")
    balanced = False
    if sub is not None and tot is not None:
        balanced = _close(sub + (tax or 0.0) + fees, tot, tol)
    # 诚实:总额自洽 ≠ 明细齐全。仍有金额定不下来的明细项要点出来,别让 balanced 盖住"明细不全"。
    n_null_amt = sum(_num(it.get("amount")) is None for it in items)
    if n_null_amt:
        flags.append(f"{n_null_amt} 个明细项金额未能确定(免费项/票面无价/OCR 未读到)——"
                     f"{'总额已核对自洽,' if balanced else ''}但逐项明细不齐,别当完整清单。")
    out["reconciled"] = reconciled
    out["flags"] = flags
    out["balanced"] = balanced
    return out


__all__ = ["arithmetic_reconcile"]
