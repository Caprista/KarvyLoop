"""cognition/consolidate — 知识库**异步和解/合并**(Bug2,Hardy:两次喂入的知识不建联/近重复堆积)。

**架构(Hardy:要优雅、别增加太多成本)**:
- **"关→建联"本就免费**:认知图谱的概念/词面边是查图时算的(graph.py),recall 也靠它多跳扩散 —— 不烧 token。
- **贵的是"同→合并/扩展"**(语义去重+补全)。所以**不压摄入热路径**:用户在知识面点「整理相似知识」时才跑
  (跑评分离,run-while-role-sleep 的手动版),一次把整库的近重复聚类 + 一次便宜 LLM 出合并建议。**无向量**。
- **H2A·不静默**:`suggest` 只给建议(dry-run),`apply` 逐簇由人拍板兑现 —— 镜像 atoms/consolidate 的模式。

**宁空勿毒**:① 严格 JSON;② 成员必须都是**真实存在**的知识点(按 index 引,越界丢);③ 一簇 < 2 不算合并;
④ 拿不准就不合(留两条 > 错并一条)。合并写一条新 Belief(source=consolidated),删被并的旧条(先写后删)。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

_MAX_BELIEFS_IN = 200        # 喂 LLM 的知识点上限(挡灌爆;多了先按 freshness 取新的)
_MAX_CLUSTERS = 48


KNOWLEDGE_CONSOLIDATE_SYSTEM = """你是 KarvyLoop 的知识库整理器。你会拿到一批带**编号**的知识点。
很多是**讲同一件事、只是措辞不同 / 后喂的补充了先喂的**的近重复(应合并),或**同一主题的多个方面**(应合成一条更全的)。

把**确实讲同一件事或可无损合并**的知识点归成一簇,给出合并方案。只输出一个 JSON(无围栏无解释):
{
  "clusters": [
    {
      "member_indices": [簇内所有知识点的编号(≥2 个)],
      "merged_title": "合并后的 3–12 字短标题",
      "merged_content": "合并后的一条知识点(把成员的信息**无损并进来**、去重、自足可检索)",
      "reason": "为什么这些是同一件事 / 可合并(一句话)"
    }
  ]
}

铁律:
- **只合高置信的近重复/可无损合并**;**绝不**把不同的知识(哪怕相关)硬并一起 —— 相关但不同的,留着别归簇。
- member_indices 里每个都**必须**是给你的编号,**不许编造**;一簇至少 2 个成员(1 个不算合并)。
- 不重复的知识点**不要**出现在任何簇里(只列要合并的)。
- 合并后的 content 要**信息不丢**(先喂的细节 + 后喂的补充都留),不是简单取其一。
- 严格 JSON,无围栏无尾随文本。"""


def parse_belief_clusters(text: str, n: int) -> list[dict]:
    """宁空勿毒:严格 JSON 解合并建议 → 只留**编号全在 [0,n)、≥2 个、有 merged_content**的簇。解不出 → []。"""
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
    used: set = set()
    for c in (obj.get("clusters") or []):
        if not isinstance(c, dict):
            continue
        idxs = []
        for m in (c.get("member_indices") or []):
            try:
                mi = int(m)
            except (TypeError, ValueError):
                continue
            if 0 <= mi < n and mi not in idxs and mi not in used:   # 编造/越界/跨簇复用 → 丢
                idxs.append(mi)
        if len(idxs) < 2:
            continue
        merged = str(c.get("merged_content", "")).strip()
        if not merged:
            continue
        used.update(idxs)
        out.append({
            "member_indices": idxs,
            "merged_title": str(c.get("merged_title", "")).strip()[:40],
            "merged_content": merged[:800],
            "reason": str(c.get("reason", "")).strip()[:200],
        })
        if len(out) >= _MAX_CLUSTERS:
            break
    return out


def _format_beliefs(beliefs: list) -> str:
    lines = []
    for i, b in enumerate(beliefs[:_MAX_BELIEFS_IN]):
        prov = getattr(b, "provenance", {}) or {}
        title = prov.get("title", "") or ""
        content = getattr(b, "content", "") or ""
        head = (title + " | ") if title else ""
        lines.append(f"[{i}] {head}{str(content)[:160]}")
    return "\n".join(lines)


async def suggest_consolidation(beliefs: list, *, gateway: Any, model_ref: str = "") -> list[dict]:
    """跑一次 LLM 聚类 → 合并建议簇(dry-run,不改任何东西)。每簇带 member_contents(供前端展示+apply 定位)。

    无 gateway / 知识点 < 2 / 解析失败 → []。gateway.complete 自动入 token 账本。
    """
    items = beliefs[:_MAX_BELIEFS_IN]
    if gateway is None or len(items) < 2:
        return []
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    material = "知识点清单:\n" + _format_beliefs(items)
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[KNOWLEDGE_CONSOLIDATE_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    clusters = parse_belief_clusters(out, len(items))
    # 把 index 还原成成员内容/标题(前端展示"把这几条 → 并成这条";apply 按 content 定位)
    for c in clusters:
        c["member_contents"] = [getattr(items[i], "content", "") for i in c["member_indices"]]
        c["member_titles"] = [(getattr(items[i], "provenance", {}) or {}).get("title", "") for i in c["member_indices"]]
        c.pop("member_indices", None)
    return clusters


def apply_belief_merge(member_contents: list, merged_content: str, *, merged_title: str = "",
                       mem, scope: str = "personal", now: Optional[float] = None) -> dict:
    """把一簇知识点合并成一条:**先写合并条、再删被并的旧条**(避免中途失败丢数据)。
    返回 {ok, removed, merged, dead_skipped}。真实**活**成员 < 2 或 merged 为空 → ok=False 不动。

    **P1c 失效知识不复活/不毁墓碑**:卡上素材从产生到 ACCEPT 之间某成员可能已被 supersede 打
    invalid_at(死版)。此时只并**活版**;死成员**不并、不删**(remove_by_content 按 content 精确删
    会连墓碑一起物理删 = 失效不删的审计层被销毁,且把死知识揉进新合并条 = 复活失效知识)。"""
    from karvyloop.schemas.cognition import Belief
    if now is None:
        now = time.time()
    members = [str(c).strip() for c in (member_contents or []) if str(c).strip()]
    merged = (merged_content or "").strip()
    # 逐成员查 invalid_at:只留活版(index 单 key=content,一个 content 至多一条)。缺 index 的
    # 桩(无 .index / 无 get)→ 退回原 count 逻辑(不回归),但生产走真 MemoryManager 有 index。
    idx = getattr(mem, "index", None)
    dead_skipped = 0
    if idx is not None and hasattr(idx, "get"):
        live: list = []
        for c in members:
            b = idx.get(c) or idx.get(c.strip())
            if b is None:
                continue   # 已不在(被删/未写)
            if getattr(b, "invalid_at", None) is not None:
                dead_skipped += 1
                continue   # 死条:不并、不删(保墓碑,不复活)
            live.append(c)
        members = live
        present = len(members)
    else:
        present = mem.count_beliefs_by_content(set(members)) if hasattr(mem, "count_beliefs_by_content") else len(members)
    if len(members) < 2 or present < 2 or not merged:
        return {"ok": False, "reason": "真实活成员 < 2 或合并内容为空,不动",
                "removed": 0, "merged": "", "dead_skipped": dead_skipped}
    # ① 先写合并条(source=consolidated,带标题)
    mem.write(Belief(content=merged, freshness_ts=now, scope=scope,
                     provenance={"source": "consolidated", "agent": "user", "ts": now,
                                 "kind": "knowledge", "title": (merged_title or "").strip()}))
    # ② 再删被并的**活**旧条(按内容精确匹配;死墓碑不在 members 里 → 保留可审计)
    removed = mem.remove_by_content(set(members))
    return {"ok": True, "removed": removed, "merged": merged, "dead_skipped": dead_skipped}


__all__ = ["KNOWLEDGE_CONSOLIDATE_SYSTEM", "parse_belief_clusters",
           "suggest_consolidation", "apply_belief_merge"]
