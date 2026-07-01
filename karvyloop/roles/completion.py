"""范式对话式补全引擎(docs/02 §14 ③ / docs/00 §2.4):理解角色已有层 → 为缺的层**起草建议**。

被动塑形是死的(一股脑落库 = 助理不作为)。补全是活的:任何来源的角色(import / 自建 / 对话生成)
统一跑这个引擎——检测哪几层还空/stub,LLM 贴着已有 identity/soul 风格为缺层起草,**人确认/改后才落**
(走 ③A 的 `update_soul`)。"不补不落库" = `complete` 标志告诉调用方这角色范式还没齐。

只起草、不直接写库(宁空勿毒:起草不出 → 空,不投毒)。MEMORY=运行时不补;COMMITMENT 有 ②a 契约不算缺。
"""

from __future__ import annotations

import json
from typing import Any

_COMPLETABLE: tuple[str, ...] = ("IDENTITY", "SOUL", "USER", "VERIFY")
_STUB: tuple[str, ...] = ("", "(待充实)")
_SLOT_DESC: dict[str, str] = {
    "IDENTITY": "一句话人设:这个角色是谁、最擅长什么",
    "SOUL": "2-4 条工作风格 / 原则",
    "USER": "它主要为谁服务、对方在意什么",
    "VERIFY": "它的产出怎么算合格(确定性可检的验收:如带出处 / 过测试 / 有数字 / 对得上需求)",
}


def detect_paradigm_gaps(paradigm: dict) -> list[str]:
    """范式哪几层还是空 / stub(`(待充实)`)。COMMITMENT 有契约不算缺、MEMORY 运行时不补。"""
    return [s for s in _COMPLETABLE
            if str(paradigm.get(s.lower(), "") or "").strip() in _STUB]


_COMPLETE_SYSTEM = (
    "你帮把一个角色的范式补全。我给你它**已填的层**,你为我点名的**缺失层**各起草一段贴合的内容——"
    "贴着已有 identity/soul 的风格,**具体、可直接用,别空话套话**。只输出严格 JSON 对象:"
    '{"SLOT": "草稿", ...},key 用我点名的大写槽名(只含缺失层),起草不出的层给空串。不要解释、不要围栏。'
)


def _parse(text: str, gaps: list[str]) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = (raw[nl + 1:] if nl != -1 else raw).rstrip().removesuffix("```").strip()
    if not raw.startswith("{"):
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    for s in gaps:                       # 只收点名的缺失层、非空字符串(宁空勿毒)
        v = obj.get(s)
        if isinstance(v, str) and v.strip():
            out[s] = v.strip()[:2000]
    return out


async def suggest_paradigm_completion(paradigm: dict, gaps: list[str], *,
                                      gateway: Any, model_ref: str = "") -> dict:
    """LLM 理解已填层 → 为 `gaps` 各起草建议(宁空勿毒:失败/解不出 → {})。返回 {SLOT: 草稿}。"""
    if not gaps or gateway is None:
        return {}
    filled = {k: str(paradigm.get(k.lower(), "") or "").strip()
              for k in ("IDENTITY", "SOUL", "USER", "VERIFY", "COMMITMENT")}
    filled = {k: v for k, v in filled.items() if v and v not in _STUB}
    ask = ("已填层:\n" + "\n".join(f"{k}: {v[:400]}" for k, v in filled.items())
           + "\n\n请为这些缺失层各起草一段:\n"
           + "\n".join(f"- {g}: {_SLOT_DESC.get(g, '')}" for g in gaps))
    from karvyloop.gateway import ResolveScope, SystemPrompt
    try:
        text = ""
        async for ev in gateway.complete([{"role": "user", "content": ask}], [],
                                          model_ref or gateway.resolve_model(ResolveScope()),
                                          system=SystemPrompt(static=[_COMPLETE_SYSTEM])):
            if type(ev).__name__ == "TextDelta":
                text += getattr(ev, "text", "")
    except Exception:
        return {}
    return _parse(text, gaps)


__all__ = ["detect_paradigm_gaps", "suggest_paradigm_completion"]
