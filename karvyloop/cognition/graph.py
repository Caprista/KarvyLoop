"""cognition/graph.py — 认知图谱(ch4 pillar 3:把 Belief 长期库算成网状)。

**为什么**:认知不是一串扁平记录,是**网** —— 一个知识点能和不同维度的别的点结边
(Hardy #7:"一个知识点能和多个维度的知识点形成网状关联,我得有地方看")。本模块把
长期库里的 Belief 算成「节点 + 关联边」,给认知图谱视图用。

边(`concept_graph`,语义版,Hardy 选 B):**共享概念** —— LLM 抽每条的核心概念/实体(`concepts.py`,
沉淀/查图时抽、缓存),共享概念 = 一条**语义边**(带 via 标注)。没概念的 belief 回退**词面重叠**
(中文 bigram + 英文词,共享 ≥2 个显著 token)。这是卡帕西 LLM Wiki 式互链(编译概念页 + 互联),
非 embedding/向量调参(已否决方向)。

(注:旧的纯词面版 `belief_graph` 已删 —— 零生产调用方,生产图谱一律走 concept_graph,
词面只作 concept_graph 内的回退;`_tokens` 仍被 conflict/spread/workflow_store 复用。)
"""
from __future__ import annotations

import re
from typing import Any

_LATIN = re.compile(r"[a-z0-9]{2,}")
_CJK = re.compile(r"[一-鿿]+")
# 中英文常见停用词(过滤掉,免得"的/了/and"把所有点连成一团)
_STOP = {
    "的", "了", "是", "在", "和", "也", "我", "你", "他", "她", "它", "用户", "喜欢", "偏好",
    "the", "and", "for", "you", "are", "with", "that", "this", "用",
}


def _tokens(s: str) -> set:
    s = (s or "").lower()
    words = {w for w in _LATIN.findall(s) if w not in _STOP}
    cjk = "".join(_CJK.findall(s))
    bigrams = {cjk[i:i + 2] for i in range(len(cjk) - 1)
               if cjk[i] not in _STOP and cjk[i + 1] not in _STOP}
    bigrams = {b for b in bigrams if b not in _STOP}
    return words | bigrams


def concept_graph(beliefs: list, concepts: list) -> dict:
    """语义版认知图谱(Hardy 选 B):边 = 两节点**共享概念**(LLM 在沉淀/查图时抽的)。

    concepts:与 beliefs 对齐的 list[list[str]];某条空/缺 → 回退该条**词面 token**。
    阈值:两边都有概念 → 共享 ≥1 个概念就连(概念显著);回退词面 → 需 ≥2(词面噪声大)。
    边带 `via`(共享的概念/词)+ `semantic`(是否概念边)。节点格式同 belief_graph(前端复用)。
    """
    keys, has_c, nodes = [], [], []
    for i, b in enumerate(beliefs):
        prov = getattr(b, "provenance", {}) or {}
        nodes.append({"id": i, "content": getattr(b, "content", ""),
                      "title": prov.get("title", ""),
                      "kind": prov.get("kind", "fact"), "source": prov.get("source", ""),
                      "source_ref": prov.get("source_ref", "")})   # 详情卡显示真实来源(URL / text:hash)
        cs = concepts[i] if (i < len(concepts) and concepts[i]) else None
        if cs:
            keys.append({str(c).strip() for c in cs if str(c).strip()})
            has_c.append(True)
        else:
            keys.append(_tokens(getattr(b, "content", "")))
            has_c.append(False)
    edges = []
    for i in range(len(beliefs)):
        for j in range(i + 1, len(beliefs)):
            shared = keys[i] & keys[j]
            need = 1 if (has_c[i] and has_c[j]) else 2
            if len(shared) >= need:
                edges.append({"source": i, "target": j, "weight": len(shared),
                              "via": sorted(shared)[:4], "semantic": bool(has_c[i] and has_c[j])})
    deg = [0] * len(beliefs)
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    for i, n in enumerate(nodes):
        n["degree"] = deg[i]
    return {"nodes": nodes, "edges": edges}


__all__ = ["concept_graph"]
