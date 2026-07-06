"""cognition/converge — 收敛式分层认知提议(docs/66 第二轮:摄入=聊天,你点「收敛」才总结)。

与 auto_distill 的分工:
- auto_distill = 轮后**自动**蒸、直接写库(provisional 低置信)—— "不用你喂"的半边。
- converge = **你点「收敛」才触发**,把对话总结成**分层认知候选**(不写库),喂给确认卡;
  只有你**逐层确认**的才写(user_explicit,非 provisional)。这是"理解关"那半边(防认知债)。

颗粒度由理解关自己切:**不预设块数**,按理解到的**深度/类型**分层(经历/推理/原则/校正/涌现)。
最值钱的是「涌现层」——聊才长出来、源材料里本没有的认知;也最需用户确认(是模型替你刨的,不是你说的)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from karvyloop.cognition.auto_distill import format_turns

# 5 层认知(Hardy 2026-07-06 docs/66 §D):深度递增,确认关**越深越严**。
LAYERS = ("experience", "reasoning", "principle", "corrective", "emergent")
DEPTH_BY_LAYER = {"experience": 1, "reasoning": 2, "principle": 3, "corrective": 4, "emergent": 5}

CONVERGE_SYSTEM = (
    "你是 KarvyLoop 的认知编译器。用户点了「收敛」——把这段对话总结成**分层的认知候选**,"
    "供用户逐条确认后再沉淀。**颗粒度不预设**:理解到几层就抽几条,别硬凑、别切成流水账。\n"
    "每条标一个 layer(认知的深度/类型):\n"
    "- experience:客观经历/事实(做过什么、发生过什么;如『从 React 换到了 Vue』)\n"
    "- reasoning:某段经历的推理(如『为什么换』)\n"
    "- principle:约束将来决策的原则(如『别为半年后模型会有的功能提前建』)\n"
    "- corrective:更深的、拿来校正别的推理的通识(如『不做≠不好,只是那条件下有更优解』)\n"
    "- emergent:**对话现场才涌现、源材料里本没有**的潜在认知——你在聊的过程中替用户刨出来的"
    "(如『每个决策都藏着隐含假设,跨域套用要先把假设刨出来』)。这层最值钱,也最需用户确认。\n"
    "每条给:content(自足一句,用户的语言)、layer、why(为什么值得沉淀 / 它是什么)、"
    "when(**仅当对话里用户明说了某个真实时间**才填那个原话,如『上个月』/『2026-03』;没明说填 null——"
    "**绝不自己猜时间**)。\n"
    "只抽**确有依据**的;能泛化到将来的才留,一次性寒暄别抽;没有可沉淀的就给空数组。\n"
    "严格输出 JSON 数组 [{\"content\",\"layer\",\"why\",\"when\"}...];不要输出 JSON 以外任何文字。"
)


def _cid(content: str) -> str:
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:12]


@dataclass
class CognitionCandidate:
    """一条待确认的认知候选(收敛总结产出;确认后才写库)。"""

    content: str
    layer: str                       # LAYERS 之一
    why: str = ""
    when_hint: Optional[str] = None  # 用户明说的真实时间原话(如『上个月』);没说 = None(绝不猜)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _cid(self.content)

    @property
    def depth(self) -> int:
        return DEPTH_BY_LAYER.get(self.layer, 1)

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content, "layer": self.layer,
                "depth": self.depth, "why": self.why, "when_hint": self.when_hint}


def parse_candidates(text: str) -> list[CognitionCandidate]:
    """解析收敛总结输出 → 候选列表。宁空勿毒:非严格 JSON 数组 / 坏项 → 跳过或返 []。"""
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return []
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[CognitionCandidate] = []
    seen: set = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip() if isinstance(item.get("content"), str) else ""
        if not content:
            continue
        layer = (item.get("layer") or "").strip() if isinstance(item.get("layer"), str) else ""
        if layer not in LAYERS:
            continue                       # 未知层 = 宁空勿毒,跳过(别硬塞默认层)
        cid = _cid(content)
        if cid in seen:
            continue
        seen.add(cid)
        when = item.get("when")
        when_hint = when.strip() if isinstance(when, str) and when.strip() else None
        why = (item.get("why") or "").strip() if isinstance(item.get("why"), str) else ""
        out.append(CognitionCandidate(content=content, layer=layer, why=why, when_hint=when_hint, id=cid))
    return out


async def converge_and_propose(
    turns: list, *, gateway: Any, model_ref: str = "", trace: Any = None,
) -> list[CognitionCandidate]:
    """收敛:把一段对话总结成分层认知候选(**不写库**)。产出喂给确认卡,确认了才沉。

    一次 LLM 调用,严格 JSON、宁空勿毒。空对话 / 解析失败 / 调用异常 → []。
    """
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source

    material = format_turns(turns)
    if not material.strip():
        return []
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    try:
        with token_source("converge"):
            async for ev in gateway.complete(
                [{"role": "user", "content": material}], [], ref,
                system=SystemPrompt(static=[CONVERGE_SYSTEM]),
            ):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception:
        return []
    return parse_candidates(out)


__all__ = ["LAYERS", "DEPTH_BY_LAYER", "CognitionCandidate", "parse_candidates",
           "converge_and_propose", "CONVERGE_SYSTEM"]
