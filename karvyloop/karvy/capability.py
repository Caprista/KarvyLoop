"""capability — 小卡的 scope 边界 + 按 peer 分派(修 D6 重做,M3+ 拍 9.4-门2-fix)。

设计:docs/29(已按本次纠正重写)+ docs/20 K1 + docs/00 §2 L0-L4 / scope。

**2026-06-18 用户纠正(我之前划错维度)**:
小卡能不能执行**不是**边界。边界是 **scope(领域)**,不是 读/写:
- 小卡**能执行,且执行面广** —— 检索 / 全局执行 / 系统设置 / 运维 / 个人 scope 的活,
  **全是它的能力,该干就干**(含有副作用的:改系统设置、跑运维命令)。它是你的
  个人/系统/全局执行者 + 业务调度者,**直接为你(H2A)干这些**。
- 它**不碰业务域(L3/L4)** —— **K1(docs/20:244):小卡永远是 observer,不接受任何业务角色。**
  即:不以自己名义进某业务域当参与者、不当业务 role、不跟别的(业务)role 协作干业务活。
  属于某业务域的事,它**匹配 + 委派**(route_to_role)给该域的 role,不自己越进去。

一句话:**小卡 = 你的个人/系统/全局执行者 + 业务的调度者,但不是业务的参与者。**

(原 READ_ONLY 天花板是错的 —— 它把"不参与业务"误解成"不执行"。已废弃。)
"""
from __future__ import annotations

import dataclasses
from typing import Optional

# K1(docs/20:244):小卡永远是 observer,不接受任何业务角色。
KARVY_ROLE_OBSERVER = "observer"

# 小卡的场 = karvy world / 个人·系统 scope(docs/26;前端 peer.domain_id === "l0")。
# 这是 L0/个人 scope —— 小卡在这里为你直接执行(系统/全局/运维/检索),不是业务域。
KARVY_DOMAIN_ID = "l0"


def is_karvy_peer(domain_id: Optional[str]) -> bool:
    """peer 是否私聊小卡(场 = karvy world / l0 / 个人·系统 scope)。"""
    return (domain_id or "") == KARVY_DOMAIN_ID


def is_direct_role_peer(peer) -> bool:
    """peer 是否**l0 场里直聊某个业务角色**(Hardy:角色面板点角色卡即聊,不必先加进业务域)。

    判据:domain_id==l0(个人场)+ role=="agent" + 有 agent_id + 不是小卡自己(observer/karvy)。
    这是 **l0/personal scope 的直聊**:用角色的通用/镜像认知层,**不挂任何业务域的
    value.md/deontic 治理**(§2.6 域私有认知按(域,角色)隔离,无域=不掺域治理),也不做
    域专属角色经验沉淀(experience.py 对无域已返 False,天然一致)。

    小卡私聊 = (l0, observer, karvy) → False(它走 is_karvy_peer 分支,人格=小卡)。
    """
    if peer is None:
        return False
    did = getattr(peer, "domain_id", "") or ""
    if did != KARVY_DOMAIN_ID:
        return False   # 业务域直聊走既有 per-role 编译(带域治理),不归这条
    role = getattr(peer, "role", "") or ""
    aid = getattr(peer, "agent_id", "") or ""
    if role != "agent" or not aid:
        return False   # observer/group/无 agent_id → 不是"直聊某角色"
    return not karvy_can_take_role(role)   # 稳妥:observer 永不当直聊角色(小卡自己)


def is_business_domain(domain_id: Optional[str]) -> bool:
    """domain_id 是否一个业务域(L3/L4)—— 即非 l0 个人场、非空。

    小卡不进这些当参与者(K1);这些里干活的是业务 role。
    """
    d = domain_id or ""
    return bool(d) and d != KARVY_DOMAIN_ID


def karvy_can_take_role(role: Optional[str]) -> bool:
    """K1 锁:小卡只能是 observer,**不接受任何业务角色**。

    role == "observer" → True;其余(任何业务角色)→ False。
    用于挡住"我也 observer 一下 / 给小卡派个业务 role"破坏 K1(docs/20:225)。
    """
    return (role or "") == KARVY_ROLE_OBSERVER


# ---- 小卡意图分类:它自己执行 / 转达 / 委派给业务 role ----

# 转达类(courier:from user, by karvy)——告诉某人某事,小卡不自己做
_COURIER_KW = ("告诉", "转告", "转达", "通知", "帮我跟", "帮我和", "tell ", "notify")
# 显式委派业务 role(route_to_role)——把某业务活交给某个角色去干
_ROUTE_KW = ("让", "交给", "委派", "派给", "转给", "找", "delegate", "assign", "hand to")

INTENT_EXECUTE = "execute"   # 小卡自己干(个人/系统/全局 scope)—— 默认
INTENT_COURIER = "courier"   # 转达
INTENT_ROUTE = "route"       # 委派给业务 role(route_to_role PROPOSE)


def classify_karvy_intent(intent: str) -> str:
    """私聊小卡的一句话:默认小卡自己执行;只有显式转达/委派才分流。

    - courier:"告诉张三…" → 转达(K2)。
    - route:"让设计师做X / 交给运维…" → 委派业务 role(route_to_role,docs/30)。
    - execute(默认):检索 / 系统设置 / 运维 / 全局执行 / 个人活 —— **小卡直接干**。
    """
    low = (intent or "").lower()
    if any(k in intent or k in low for k in _COURIER_KW):
        return INTENT_COURIER
    if any(k in intent or k in low for k in _ROUTE_KW):
        return INTENT_ROUTE
    return INTENT_EXECUTE


@dataclasses.dataclass(frozen=True)
class PeerDispatch:
    """一次 peer 分派决策(给调用层用)。"""
    is_karvy: bool
    intent_class: str       # 仅 is_karvy 时有意义(execute/courier/route)
    should_drive: bool      # 是否走 drive→forge 真执行
    should_route: bool      # 是否出 route_to_role PROPOSE(委派业务 role)


def dispatch_for_peer(domain_id: Optional[str], intent: str) -> PeerDispatch:
    """按 peer 分派(docs/29 §4 执行模型,已按纠正重写)。

    - 私聊小卡(l0,个人/系统 scope):
        · execute(默认)→ **小卡直接 drive 执行**(系统/全局/运维/检索/个人活)。
        · courier → 转达(不 drive)。
        · route → 出 route_to_role PROPOSE(委派业务 role,不自己越进业务域)。
      小卡**能执行**,所以默认 should_drive=True —— 这正是"聊天即执行"对小卡是对的。
    - 私聊业务 role(非 l0):该业务 role 直接执行(它在自己域里干活)。
    """
    if is_karvy_peer(domain_id):
        cls = classify_karvy_intent(intent)
        return PeerDispatch(
            is_karvy=True,
            intent_class=cls,
            should_drive=(cls == INTENT_EXECUTE),  # 小卡个人/系统 scope 直接干
            should_route=(cls == INTENT_ROUTE),    # 业务活 → 委派,不自己进业务域
        )
    # 业务域 peer:该 role 在自己域里执行
    return PeerDispatch(
        is_karvy=False,
        intent_class=INTENT_EXECUTE,
        should_drive=True,
        should_route=False,
    )


__all__ = [
    "KARVY_ROLE_OBSERVER", "KARVY_DOMAIN_ID",
    "is_karvy_peer", "is_direct_role_peer", "is_business_domain", "karvy_can_take_role",
    "classify_karvy_intent", "dispatch_for_peer", "PeerDispatch",
    "INTENT_EXECUTE", "INTENT_COURIER", "INTENT_ROUTE",
]
