"""cognition/auto_distill — 对话自动蒸馏(loop step4b:个人知识库的"不用你喂"半边)。

地基(step4b 地基)铺好后,Belief 长期库在产品里真活了(落盘 + 召回注入)。这步让系统
**不用用户显式喂料**,轮后自动从对话里蒸出"关于你"的知识 —— 复用 4b-1 的摄入编译器
(`ingest.ingest_material`,source="conversation"),只是触发口从"用户喂"变成"轮后批量"。

**省 token(用得起)**:不是每轮调一次模型,而是**攒够 N 轮未蒸馏才蒸一次**(一次调用蒸
一批),且只蒸 watermark 之后的新轮(不重复蒸)。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from karvyloop.cognition.ingest import IngestResult, ingest_material, parse_facts

DISTILL_BATCH = 4  # 每积累 N 轮未蒸馏 → 蒸一次(批量省 token)

# §11 P1b 显式陈述源:piggyback —— **同一次** LLM 调用既抽"关于你的事实/偏好"(进记忆),
# 又抽"你怎么决策"的决策偏好(进决策结晶)。不加 token 成本(守用得起),且把两者分桶——
# 一般偏好→记忆 Belief;拍板规则→决策偏好,理清与记忆 preference 的重叠。
DISTILL_COMBINED_SYSTEM = (
    "你是 KarvyLoop 的知识编译器。从对话里抽两类东西,**严格分桶、别混**:\n"
    "1) facts:关于这个用户的原子事实与一般偏好(每条独立自足),kind=fact|preference。\n"
    "2) decisions:**关于用户怎么拍板 / 要求什么**的可复用决策偏好——是'决策规则',不是一般喜好。"
    "kind=constraint(硬约束,如'碰生产先写测试')| taste(品味,如'输出默认用表格')| "
    "standing(站位,如'设计师先考虑移动端');explicit=用户明说过=true,从行为推断=false;"
    "scope=global(普遍成立,默认)| domain(只在某情境)。\n"
    "只抽**确有依据**的;decisions 要能泛化到将来类似情形,一次性的别抽。"
    "区分:'喜欢简洁'=facts 的 preference;'碰生产必须先写测试'=decisions 的 constraint。\n"
    "严格输出 JSON 对象 {\"facts\":[{\"content\",\"kind\"}...],"
    "\"decisions\":[{\"content\",\"kind\",\"explicit\",\"scope\"}...]};没有就给空数组。"
    "不要输出 JSON 以外任何文字。"
)


def format_turns(turns: list) -> str:
    """把若干对话轮拼成一段材料喂给摄入编译器。"""
    parts: list[str] = []
    for t in turns:
        u = getattr(t, "user_intent", "") or ""
        a = getattr(t, "agent_response", "") or ""
        if u.strip():
            parts.append(f"用户: {u.strip()}")
        if a.strip():
            parts.append(f"小卡: {a.strip()}")
    return "\n".join(parts)


def should_distill(n_turns: int, watermark: int, *, batch: int = DISTILL_BATCH) -> bool:
    """攒够 batch 轮未蒸馏 → 该蒸了。"""
    return (n_turns - watermark) >= batch


async def distill_turns(
    turns: list,
    *,
    gateway: Any,
    mem: Any,
    model_ref: str = "",
    agent_id: str = "user",
    now: Optional[float] = None,
) -> IngestResult:
    """把一批对话轮编译成"关于用户"的 Belief(source=conversation)。复用 4b-1 摄入编译器。"""
    material = format_turns(turns)
    if not material.strip():
        return IngestResult(written=0, raw="(无可蒸馏内容)")
    return await ingest_material(
        material, gateway=gateway, mem=mem, model_ref=model_ref,
        agent_id=agent_id, scope="personal", source="conversation", now=now,
    )


def parse_combined(text: str) -> tuple[list[dict], list[dict]]:
    """解析组合抽取输出 → (facts, decisions)。宁空勿毒:像 JSON 解析失败 → ([], [])。"""
    import json
    from karvyloop.crystallize.decision_pref import parse_decision_prefs
    t = (text or "").strip()
    if not t:
        return [], []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return [], []
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    facts = parse_facts(json.dumps(data.get("facts", [])))
    decisions = parse_decision_prefs(json.dumps(data.get("decisions", [])))
    return facts, decisions


async def distill_turns_with_decisions(
    turns: list, *, gateway: Any, mem: Any, model_ref: str = "",
    agent_id: str = "user", now: Optional[float] = None,
) -> tuple[IngestResult, list[dict]]:
    """**一次** LLM 调用 piggyback:抽 facts(写进记忆)+ decisions(返回给调用方路由进决策结晶)。

    facts 写法与 ingest 一致(provenance/freshness/去重在 mem.write 里);decisions 不在此写,
    由 console 侧 `crystallize_candidates` 走双关门(避免 cognition 依赖 console)。
    """
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.schemas.cognition import Belief

    if now is None:
        now = time.time()
    material = format_turns(turns)
    if not material.strip():
        return IngestResult(written=0, raw="(无可蒸馏内容)"), []
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[DISTILL_COMBINED_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    facts, decisions = parse_combined(out)
    written: list = []
    for f in facts:
        content = (f.get("content") or "").strip()
        if not content:
            continue
        try:
            mem.write(Belief(
                content=content,
                provenance={"source": "conversation", "agent": agent_id, "ts": now,
                            "trace_ref": "", "kind": f.get("kind", "fact")},
                freshness_ts=now, scope="personal"))
            written.append(content)
        except Exception:
            pass
    return IngestResult(written=len(written), raw=f"facts={len(facts)} decisions={len(decisions)}"), decisions


__all__ = [
    "DISTILL_BATCH", "DISTILL_COMBINED_SYSTEM", "format_turns", "should_distill",
    "distill_turns", "parse_combined", "distill_turns_with_decisions",
]
