"""atoms/consolidate — 原子语义合并(docs/14 §11.2,护城河).

**病根**(round2 裁判逮到):批量导入后原子复用**只靠 exact-id 撞名**(实测 335 原子仅 33 个被共用),
"市场调研"被一个 agent 叫 `market_research`、另一个叫 `market-survey` → 两个独立原子不合并 →
原子库臃肿、近重复、reuse 仅 4-10%,直接削弱"甲/买糖:用不拥有"的资产质量。

**这层做什么**:LLM 找出**语义上做同一件事**的原子簇 → 每簇出一个**规范原子** → 把所有引到簇内原子的
角色 COMPOSITION **改写**到规范 id → 删冗余。**不静默合并**:`suggest` 只给建议(dry-run),`apply` 由
调用方(经 H2A,Hardy 拍)逐簇兑现 —— 原子库是护城河资产,合并要人拍板、可审计、可回退。

**宁空勿毒**:① 严格 JSON;② 簇成员必须都是**真实存在**的原子 id(LLM 编的丢);③ 一簇 < 2 个不算合并;
④ 拿不准就不合(留两个 > 错并一个)。**rewire-before-delete**:先改完所有角色引用,再删冗余原子,绝不留悬空引用。
"""
from __future__ import annotations

import json
from typing import Any, Optional

_MAX_ATOMS_IN = 200          # 喂 LLM 的原子条数上限(批量导入也够,挡灌爆)
_MAX_CLUSTERS = 64
_ID_MAXLEN = 64


CONSOLIDATE_SYSTEM = """你是 KarvyLoop 的原子库整理器。你会拿到一批"原子"(role 的可复用构建块),每个有 id 和 purpose。
很多是**语义上做同一件事、只是名字不同**的近重复(如 market_research / market-survey 都是"做市场调研")。

把**语义上确实做同一件事**的原子归成一簇,给出合并方案。只输出一个 JSON(无围栏无解释):
{
  "clusters": [
    {
      "canonical_id": "这簇的规范名(从成员里挑最清晰的一个,或给个更好的 snake_case 名)",
      "member_ids": ["簇内所有原子 id(≥2 个,含 canonical)"],
      "merged_purpose": "合并后的一句话 purpose",
      "merged_tools": ["合并后的工具并集"],
      "reason": "为什么这些是同一件事(一句话)"
    }
  ]
}

铁律:
- **只合高置信的近义**;**绝不**把不同能力(如"市场调研"和"竞品定价分析")并一起。拿不准就别归簇。
- member_ids 里每个都**必须**来自给你的清单,**不许编造**。一簇至少 2 个成员(1 个不算合并)。
- 不近重复的原子**不要**出现在任何簇里(只列要合并的)。
- 严格 JSON,无围栏无尾随文本。"""


def parse_clusters(text: str, valid_ids: set) -> list[dict]:
    """宁空勿毒:严格 JSON 解合并建议 → 只留**成员全真实、≥2 个**的簇;否则丢该簇。解不出 → []。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl != -1 else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    raw = raw.strip()
    if not raw.startswith("{"):
        return []
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict):
        return []
    out: list[dict] = []
    for c in (obj.get("clusters") or []):
        if not isinstance(c, dict):
            continue
        members = [str(m).strip() for m in (c.get("member_ids") or []) if str(m).strip() in valid_ids]
        members = list(dict.fromkeys(members))            # 去重保序
        if len(members) < 2:                              # 编造的/不足 2 个 → 丢
            continue
        canonical = str(c.get("canonical_id", "")).strip()
        if not canonical or len(canonical) > _ID_MAXLEN:
            canonical = members[0]
        # canonical 若不在成员里,要么是个新名(允许、后续建),要么 LLM 乱给 → 用成员第一个兜底
        if canonical not in members and canonical not in valid_ids:
            # 接受新规范名,但必须 COMPOSITION-safe
            import re as _re
            if not _re.match(r"^[A-Za-z0-9_]+$", canonical):
                canonical = members[0]
        out.append({
            "canonical_id": canonical,
            "member_ids": members,
            "merged_purpose": str(c.get("merged_purpose", "")).strip()[:400],
            "merged_tools": [str(t).strip() for t in (c.get("merged_tools") or []) if str(t).strip()][:16],
            "reason": str(c.get("reason", "")).strip()[:200],
        })
        if len(out) >= _MAX_CLUSTERS:
            break
    return out


def _format_atoms(atoms: list) -> str:
    lines = []
    for a in atoms[:_MAX_ATOMS_IN]:
        pid = getattr(a, "id", "") or (a.get("id") if isinstance(a, dict) else "")
        purpose = getattr(a, "prompt", "") or (a.get("prompt") if isinstance(a, dict) else "") or ""
        lines.append(f"- {pid}: {str(purpose)[:120]}")
    return "\n".join(lines)


async def suggest_consolidation(atoms: list, *, gateway: Any, model_ref: str = "") -> list[dict]:
    """跑一次 LLM 聚类 → 合并建议簇(dry-run,不改任何东西)。gateway.complete 自动入 token 账本。

    无 gateway / 原子 < 2 / 解析失败 → []。
    """
    if gateway is None or len(atoms) < 2:
        return []
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    valid = {(getattr(a, "id", "") or (a.get("id") if isinstance(a, dict) else "")) for a in atoms}
    valid.discard("")
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    material = "原子清单:\n" + _format_atoms(atoms)
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[CONSOLIDATE_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return parse_clusters(out, valid)


def apply_merge(canonical_id: str, member_ids: list, *, merged_purpose: str = "",
                merged_tools: Optional[list] = None, atom_registry, role_registry) -> dict:
    """把一簇原子合并成 canonical:**先改写所有角色引用,再删冗余原子**(rewire-before-delete)。

    返回 {canonical, rewired_roles, removed_atoms, ok, reason}。不合法(成员 < 2 真实存在)→ ok=False 不动。
    """
    members = [m for m in (member_ids or []) if atom_registry.get(m) is not None]
    if len(members) < 2:
        return {"ok": False, "reason": "真实存在的成员 < 2,无可合并", "canonical": canonical_id,
                "rewired_roles": [], "removed_atoms": []}
    # 确保 canonical 存在:是某成员 → 用它;否则按合并方案就地建一个
    if atom_registry.get(canonical_id) is None:
        try:
            atom_registry.create(canonical_id, "task", merged_purpose or f"合并自 {len(members)} 个近义原子",
                                 tools=list(merged_tools or []))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"建规范原子失败: {e}", "canonical": canonical_id,
                    "rewired_roles": [], "removed_atoms": []}
    redundant = [m for m in members if m != canonical_id]
    mapping = {m: canonical_id for m in redundant}
    # ① REWIRE FIRST:所有引到冗余成员的角色 → 改写到 canonical(去重)
    rewired = []
    for role in role_registry.list_all():
        if any(a in mapping for a in role.atom_ids):
            if role_registry.rewrite_atom_refs(role.id, mapping):
                rewired.append(role.id)
    # ② THEN DELETE:此刻已无角色引冗余成员,安全删
    removed = []
    for m in redundant:
        if atom_registry.remove(m):
            removed.append(m)
    return {"ok": True, "canonical": canonical_id, "rewired_roles": rewired,
            "removed_atoms": removed, "merged_n": len(members)}


__all__ = ["CONSOLIDATE_SYSTEM", "parse_clusters", "suggest_consolidation", "apply_merge"]
