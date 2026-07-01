"""临时原子生命周期(docs/02 §15.5)—— 让护城河自清洁。

合并/导入/向内结晶新生的原子先 `provisional`:乐观地留,但
  - **被角色真用上**(≥1 个角色 composition 引用它)→ `confirm`(挣到正式身份);
  - **没人用**(孤儿,0 引用)→ `revert`(删掉)。删孤儿**天然安全**:0 引用 = 无悬空(同 §11.2
    rewire-before-delete 的不变量,这里压根没有引用要 rewire)。
  - 正好 1 个角色用 → 留着 provisional(它被用着、不能删;但还没"跨角色复用"、不急着转正)。

判据用的是**真实数据**(`role.atom_ids` 引用计数),不靠新埋点。正式原子(provisional=False)不碰。
"""

from __future__ import annotations

from typing import Any


def _role_ref_counts(role_registry: Any) -> dict[str, int]:
    """每个原子 id → 引用它的角色数(跨角色复用度;数据源 = role.atom_ids)。"""
    counts: dict[str, int] = {}
    for role in role_registry.list_all():
        for aid in set(getattr(role, "atom_ids", []) or []):
            counts[aid] = counts.get(aid, 0) + 1
    return counts


def review_provisional(atom_registry: Any, role_registry: Any) -> dict:
    """巡检临时原子:被用的转正,孤儿删除。返回 {confirmed: [...], reverted: [...]}。

    维护期(daily)调一次即可——不在热路径。只动 provisional 原子,正式原子永不触碰。
    """
    counts = _role_ref_counts(role_registry)
    confirmed: list[str] = []
    reverted: list[str] = []
    # 先快照(避免边遍历边改)
    provisional_atoms = [a for a in atom_registry.list_all() if getattr(a, "provisional", False)]
    for a in provisional_atoms:
        refs = counts.get(a.id, 0)
        if refs >= 1:
            # 被至少一个角色用上 = 挣到正式身份(留)
            if atom_registry.confirm(a.id):
                confirmed.append(a.id)
        else:
            # 孤儿(0 引用)= 没人用 → 删。0 引用故无悬空,安全。
            if atom_registry.remove(a.id):
                reverted.append(a.id)
    return {"confirmed": confirmed, "reverted": reverted}


__all__ = ["review_provisional"]
