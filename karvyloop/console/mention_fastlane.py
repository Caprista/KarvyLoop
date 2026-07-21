"""mention_fastlane — 私聊 @角色 = 快通道委派卡(U-03,Hardy 拍板)。

从 routes.py 抽出成独立模块(god-module 行数红线;逻辑零改动):
- `resolve_private_mentions`:私聊文本 @ 解析——与群 @ **同一份名册**(_roundtable_roster)、
  同款精确 agent_id 命中(群走前端 chip,私聊对原始文本做 "@"+agent_id 逐字匹配;
  前缀重叠取最长)。误触发=吞用户消息,所以精确命中才算。
- `mention_fastlane`:命中 1 个角色 → 立即备好已填好的 route_to_role 卡(复用
  proposal_for_route + 既有 broadcast,零新执行逻辑、零 LLM),小卡回一句轻回执;
  **不让角色直接答**——「私聊=小卡的场」与 K1 问责链不破,H2A 拍板点保留。
  命中 2+ → 引导句(多人协作的家在群/圆桌);光 @ 没正文 → None 交回正常 drive。

调用方(routes.maybe_route_to_role,REST/WS 共用点)负责 record_turn + 早返回。
"""
from __future__ import annotations


def resolve_private_mentions(app, peer, intent: str, *, roster_fn) -> list:
    """私聊文本里的 @角色 解析。返回命中的 [Address](出现序去重;无命中 → [])。

    roster_fn = routes._roundtable_roster(避免反向 import;名册与群 @ 同源)。
    """
    text = intent or ""
    if "@" not in text or peer is None:
        return []
    roster = [a for a in roster_fn(app, peer) if (a.agent_id or "")]
    if not roster:
        return []
    hits: list = []
    seen: set = set()
    pos = text.find("@")
    while pos != -1:
        best = None
        for a in roster:
            if text.startswith(a.agent_id, pos + 1):
                if best is None or len(a.agent_id) > len(best.agent_id):
                    best = a   # 最长命中优先(前缀重叠消歧);同名同长 → 名册序第一个
        if best is not None:
            key = (best.domain_id, best.agent_id)
            if key not in seen:
                seen.add(key)
                hits.append(best)
        pos = text.find("@", pos + 1)
    return hits


async def mention_fastlane(app, peer, intent: str, *, roster_fn):
    """私聊 @ 快通道。返回 drive_done 形 payload 或 None(不触发,照常往下)。"""
    hits = resolve_private_mentions(app, peer, intent, roster_fn=roster_fn)
    if not hits:
        return None
    from karvyloop import i18n as _i18n
    if len(hits) >= 2:
        return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False,
                "crystallized": False, "skill_name": "", "routed": False,
                "text": _i18n.t("route.mention_multi_hint")}
    a = hits[0]
    # 显示名与 routes._match_role_for_intent 同款:role=="agent" 时 agent_id 才是有意义的名字
    display = a.agent_id if (getattr(a, "role", "") == "agent" and a.agent_id) else (getattr(a, "role", "") or a.agent_id)
    requirement = intent.replace("@" + a.agent_id, " ").strip()
    if not requirement:
        return None   # 光 @ 没说事 → 没需求可委派(空卡 ACCEPT 必败"委派需求为空"),交回小卡正常聊
    registry = getattr(app.state, "proposal_registry", None)
    if registry is None:
        return None
    dom_reg = getattr(app.state, "domain_registry", None)
    dom = dom_reg.get(a.domain_id) if (dom_reg is not None and a.domain_id) else None
    # 独立角色(不归任何域,如批量导入的)没有域名 → 卡上标"个人"(l0 直聊同款:无域治理)
    domain_name = (getattr(dom, "name", "") if dom is not None else "") or _i18n.t("route.mention_no_domain")
    import time as _t
    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.proposal_registry import proposal_for_route

    proposal = proposal_for_route(ts=_t.time(), requirement=requirement,
                                  domain_id=a.domain_id or "", role=display,
                                  agent_id=a.agent_id or "", domain_name=domain_name)
    registry.register(proposal)
    try:
        await broadcast_proposal(app, proposal)   # 推到 H2A 列(与既有委派卡同一条路)
    except Exception:
        pass
    return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": True,
            "text": _i18n.t("route.mention_fastlane_hint", role=display)}


__all__ = ["resolve_private_mentions", "mention_fastlane"]
