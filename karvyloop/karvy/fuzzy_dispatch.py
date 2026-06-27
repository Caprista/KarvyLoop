"""fuzzy_dispatch — 模糊指令的 LLM 拆解层(Hardy 2026-06-27).

**病根**:全局调度原本是纯关键词/规则(capability.py / _resolve_roundtable_from_intent):
"去产品研发域,找几个人,帮我分析一下竞品" 这类**模糊语义**——没点名具体角色、没说"圆桌"——
就解析不出"目标域 + 选哪几个人 + 什么计划",只能退回小卡自己干。这是全局调度的缺口。

**这层做什么**:在确定性规则**没命中**编排时兜底——把模糊指令喂 LLM,连同**可用业务域+成员清单**,
拆出 `{action(圆桌/委派/运维/自己), 目标域, 选哪几个角色, 做什么}`,解析+落地到**既有 H2A 提案**
(proposal_for_roundtable / proposal_for_route / proposal_for_ops_fix)。**不 auto-execute**:仍走 H2A
由 Hardy 拍板(global-karvy-orchestrates-roundtable 记忆)。耗 token(gateway.complete 自动入账)。

**宁空勿毒 + 降级**:严格 JSON;action 非法 / 选不出真实成员 / gateway None → 返 None → 小卡自己干
(0 回归,绝不凭空编出不存在的角色去开桌)。

**v1 范围**:action ∈ {roundtable, delegate, ops, self}。显式 DAG 工作流(@多人)仍走既有 /workflow
路径(本层不接管);模糊→工作流留后续拍。
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Optional

_VALID_ACTIONS = ("roundtable", "delegate", "ops", "self")
FUZZY_MAX_PARTICIPANTS = 8       # 模糊"几个人"= 少数几个;50+ 是显式 API 压测,不走这层
_MAX_TOPIC = 400


DISPATCH_SYSTEM = """你是 KarvyLoop 全局助手"小卡"的调度拆解器。用户对你说了一句**模糊的指令**,
你要判断这是不是一次"需要拉业务角色干活"的编排,如果是,拆出"找哪个域、哪几个人、做什么、用什么方式"。

你会拿到:用户的话 + 当前**可用业务域和它们的成员角色清单**。

只输出**一个 JSON 对象**(不要解释、不要 markdown 围栏):
{
  "action": "roundtable | delegate | ops | self",
  "domain": "目标业务域名(必须从清单里挑一个;ops/self 可空)",
  "participants": ["角色名", ...],
  "topic": "要做的事,一句话(中文)"
}

判定规则:
- **roundtable(圆桌)**:用户想让**几个人一起讨论/分析/出主意**(如"找几个人帮我分析竞品")。从目标域成员里挑 2-6 个相关的人。
- **delegate(委派)**:把一件事**交给一个人**做(如"让产品经理写个方案")。participants 填 1 个。
- **ops(运维)**:用户想**诊断/排查/修系统**(如"帮我看下系统哪有问题""诊断一下")。domain/participants 留空。
- **self(小卡自己)**:这根本不是"拉业务角色"的活——是闲聊、问小卡自己能答的、或信息不足无法编排。**宁可填 self,也不要硬编排**。

硬约束:
- domain 和 participants 里的名字**必须来自给你的清单**,**不许编造**不存在的域或角色。挑不出真实的人 → 用 self。
- participants 最多 6 个。
- 严格 JSON,无围栏无尾随文本。"""


@dataclasses.dataclass(frozen=True)
class FuzzyPlan:
    """拆解+解析后的可执行调度计划(名字已对齐到真实 registry)。"""
    action: str                          # roundtable | delegate | ops | self
    domain_id: str = ""
    domain_name: str = ""
    participants: tuple[str, ...] = ()   # agent_id 列表
    participant_names: tuple[str, ...] = ()
    topic: str = ""

    def is_actionable(self) -> bool:
        """是否真能落地成一次编排(self / 解析不出 → 不可执行,让小卡自己干)。"""
        if self.action == "ops":
            return True
        if self.action == "roundtable":
            return len(self.participants) >= 1
        if self.action == "delegate":
            return len(self.participants) == 1 and bool(self.domain_id)
        return False


def build_roster(app: Any) -> list[dict]:
    """当前可用业务域 + 成员角色清单(喂 LLM 让它从中选,不许编造)。"""
    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return []
    out: list[dict] = []
    try:
        for domain in reg.list_all():
            if getattr(domain, "lifecycle", "active") != "active":
                continue
            members = []
            seen = set()
            for m in reg.resolve_members(domain.id):
                if m.role in ("user", "observer"):
                    continue
                name = m.agent_id if (m.role == "agent" and m.agent_id) else m.role
                if not name or name in seen:
                    continue
                seen.add(name)
                members.append({"name": name, "agent_id": m.agent_id or ""})
            if members:
                out.append({"domain_id": domain.id,
                            "domain_name": getattr(domain, "name", domain.id),
                            "members": members})
    except Exception:
        return []
    return out


def _format_roster(roster: list[dict]) -> str:
    if not roster:
        return "(当前没有任何业务域/成员)"
    lines = []
    for d in roster:
        names = "、".join(m["name"] for m in d["members"])
        lines.append(f"- 业务域「{d['domain_name']}」成员:{names}")
    return "\n".join(lines)


def parse_fuzzy_plan(text: str, roster: list[dict]) -> Optional[FuzzyPlan]:
    """宁空勿毒:严格 JSON 解 LLM 拆解 → 把名字**对齐到真实 registry** → FuzzyPlan;
    解不出 / 非法 action / 编排但选不出真实成员 → None(让小卡自己干,绝不凭空开桌)。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl != -1 else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    raw = raw.strip()
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    action = str(obj.get("action", "")).strip()
    if action not in _VALID_ACTIONS:
        return None
    topic = str(obj.get("topic", "")).strip()[:_MAX_TOPIC]
    if action in ("self", "ops"):
        return FuzzyPlan(action=action, topic=topic)

    # roundtable / delegate:解析目标域 + 成员(只认清单里真实存在的)
    want_domain = str(obj.get("domain", "")).strip()
    dom = None
    for d in roster:
        if d["domain_name"] == want_domain or (want_domain and want_domain in d["domain_name"]):
            dom = d
            break
    if dom is None:
        return None                      # 域对不上 → 不硬编排
    want_names = [str(n).strip() for n in (obj.get("participants") or []) if str(n).strip()]
    by_name = {m["name"]: m for m in dom["members"]}
    picks, pick_names, seen = [], [], set()
    for n in want_names:
        m = by_name.get(n)
        if m is None:                    # 模糊匹配(LLM 可能给"产品经理"而成员是"产品经理A")
            m = next((mm for mm in dom["members"] if n in mm["name"] or mm["name"] in n), None)
        if m is None or m["name"] in seen:
            continue
        seen.add(m["name"])
        picks.append(m["agent_id"] or m["name"])
        pick_names.append(m["name"])
        if len(picks) >= FUZZY_MAX_PARTICIPANTS:
            break
    if not picks:
        return None                      # 选不出真实成员 → 不硬编排
    if action == "delegate":
        picks, pick_names = picks[:1], pick_names[:1]
    return FuzzyPlan(action=action, domain_id=dom["domain_id"], domain_name=dom["domain_name"],
                     participants=tuple(picks), participant_names=tuple(pick_names), topic=topic or "")


async def decompose_dispatch(intent: str, *, roster: list[dict],
                             gateway: Any, model_ref: str = "") -> Optional[FuzzyPlan]:
    """跑一次受限 LLM 拆解 → FuzzyPlan(名字已对齐真实 registry)。gateway.complete 自动入 token 账本。

    无 gateway / 无业务域 / 解析失败 → None(降级:小卡自己干)。
    """
    if gateway is None or not roster:
        return None
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    material = f"用户说:{intent}\n\n可用业务域和成员:\n{_format_roster(roster)}"
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[DISPATCH_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return parse_fuzzy_plan(out, roster)


__all__ = ["FuzzyPlan", "build_roster", "parse_fuzzy_plan", "decompose_dispatch",
           "DISPATCH_SYSTEM", "FUZZY_MAX_PARTICIPANTS"]
