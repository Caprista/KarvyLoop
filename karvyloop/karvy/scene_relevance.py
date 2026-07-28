"""scene_relevance — 「猜你想干 v2」刀2:预执行的 relevance 判断(docs/94 刀2)。

刀1 的场景信号(确定性零 LLM)只回答"此刻发生了什么";本模块回答**值不值得现在替用户
先把活干了**:一次便宜 LLM 调用,输入 = 场景信号人话 + 当前对话摘要 + (供料)习惯层,
输出严格 JSON `{"worth": bool, "action_intent": str, "reason": str}`。

雷达依据(docs/94):无定向的空闲算力几乎无用(0.9%),预测定向后 14.1% —— 这一步就是
"定向";判不值得 → 调用方回落刀1 的普通建议卡,**不预执行**(宁窄勿滥)。

纪律:
- **宁空勿毒**:输出解析严格 JSON,坏输出/非 bool worth/worth 但 action_intent 空 →
  返 None(调用方按"不值得"回落普通卡,绝不拿垃圾当定向)。
- **记账咽喉**:走 GatewayClient 唯一咽喉,token_source="scene_relevance"
  (∈ spend_budget.AUTOMATIC_SOURCES,全局花费刹车拦得住它)。
- 任何异常返 None(旁路,绝不冒泡到维护 tick)。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 判断材料的截断(便宜调用:料要小)。
_SCENE_DESC_CAP = 600
_CONVERSATION_CAP = 500
_HABITS_CAP = 5
_ACTION_INTENT_CAP = 200

_RELEVANCE_SYSTEM = (
    "你是这位用户的个人助理的判断模块。系统检测到一个当下场景(如:某任务刚失败/某定时任务"
    "快到点且上次失败),正考虑趁用户空闲**先替他把准备工作干了**(如:提前试跑一次、诊断失败"
    "原因并起草修法)。请判断:此刻值不值得花一次小额算力替他先干,以及具体干什么。"
    "只有当这件事对用户明显有用、且一次小任务就能干出可呈现的产物时才值得;拿不准就不值得。"
    '严格只输出一个 JSON 对象:{"worth": true或false, "action_intent": "要干的活的一句话",'
    ' "reason": "一句理由"},别的什么都不要。worth=false 时 action_intent 可为空串。'
)


def parse_relevance(raw: str) -> Optional[dict]:
    """严格解析 relevance 输出(纯函数,可单测)。

    合法形态:JSON 对象,worth 是**真 bool**(不收 "true"/"yes" 字符串),action_intent/
    reason 是 str;worth=True 但 action_intent 空 → 无定向 = 不合法。只剥外层 code fence,
    prose 不抽。任何不合法 → None(宁空勿毒)。
    """
    try:
        s = (raw or "").strip()
        if s.startswith("```"):
            s = s.strip("`").lstrip("json").strip()
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        d = json.loads(s[i:j + 1])
        if not isinstance(d, dict):
            return None
        worth = d.get("worth")
        if not isinstance(worth, bool):   # 严格 bool:字符串 "true" 也算坏输出
            return None
        action = d.get("action_intent", "")
        reason = d.get("reason", "")
        if not isinstance(action, str) or not isinstance(reason, str):
            return None
        action = action.strip()[:_ACTION_INTENT_CAP]
        if worth and not action:
            return None   # 值得干却说不出干什么 = 无定向,当坏输出
        return {"worth": worth, "action_intent": action, "reason": reason.strip()[:200]}
    except Exception:
        return None


async def judge_relevance(gateway: Any, model_ref: str, *, scene_desc: str,
                          conversation_gist: str = "",
                          habits: Optional[list] = None) -> Optional[dict]:
    """一次便宜 LLM:这个场景值不值得现在替用户预执行,干什么。

    返回 {"worth", "action_intent", "reason"} 或 None(调不通/坏输出 → 调用方回落普通卡)。
    token_source="scene_relevance"(记账咽喉纪律;∈ AUTOMATIC_SOURCES 受花费刹车管)。
    """
    if gateway is None or not (scene_desc or "").strip():
        return None
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source

    parts = [f"当下场景:{scene_desc.strip()[:_SCENE_DESC_CAP]}"]
    gist = (conversation_gist or "").strip()
    if gist:
        parts.append(f"用户最近在聊/在干:{gist[:_CONVERSATION_CAP]}")
    hb = [str(h).strip() for h in (habits or []) if str(h).strip()][:_HABITS_CAP]
    if hb:
        parts.append("已观察到的用户习惯:" + ";".join(hb))
    material = "\n".join(parts)

    out = ""
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
        with token_source("scene_relevance"):
            async for ev in gateway.complete(
                    [{"role": "user", "content": material}], [], ref,
                    system=SystemPrompt(static=[_RELEVANCE_SYSTEM])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:  # noqa: BLE001 —— 判不了 = 不预执行(回落普通卡)
        logger.debug("[scene_relevance] LLM 调用失败(回落普通卡): %s", e)
        return None
    return parse_relevance(out)


__all__ = ["judge_relevance", "parse_relevance"]
