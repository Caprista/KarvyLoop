"""proposal_registry — PROPOSE 待决议表 + 按 kind 兑现分派(修 D5,M3+ 拍 9.4-B3a)。

设计:docs/30 PROPOSE 类型化。修两个洞:
- 9.0c Proposal 无 proposal_id → 前端只能 "p-"+habit_id 凑(已在 atoms.py 修)。
- ACCEPT 后无消费者 → 接受 = 空响应(本模块修:按 kind dispatch 兑现)。

不变量:
- **PR-2**:出的 Proposal 进 registry;ACCEPT 凭 proposal_id 查回原 Proposal。
- **PR-3**:ACCEPT 按 kind 分派兑现;REJECT 丢弃;DEFER 挂起(留 registry,下次再呈现)。
- **PR-4**:守 K5 —— 本模块**不造决策、不构 Envelope**;dispatch 只在 caller 已拿到
  用户 ACCEPT 后才跑(决策永远是用户按下的)。有副作用的兑现(run/route/结晶)
  全走**可注入 handler**,默认 handler 不做副作用(只登记意图)→ 真副作用由
  上层显式接线,测试可验分派路由而不触真子系统。

kind → 兑现(docs/30 §3,0.1.0 先 crystallize_skill / route_to_role / resolve_conflict):
  crystallize_skill  → 调结晶写技能库
  run_task           → drive 执行(走对应 role/慢脑)
  set_preference     → 写习惯/偏好层
  route_to_role      → courier 转达给目标 role
  resolve_conflict   → 按用户选择处置(禁用/改/忽略)— 接 docs/31 D4
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- kind 常量(docs/30 PR-1)----
KIND_CRYSTALLIZE_SKILL = "crystallize_skill"
KIND_RUN_TASK = "run_task"
KIND_SET_PREFERENCE = "set_preference"
KIND_ROUTE_TO_ROLE = "route_to_role"
KIND_ROUNDTABLE = "roundtable"  # 私聊小卡说"让几个角色开圆桌讨论X" → 编排意图:在群里拉多人圆桌(非单点委派)
KIND_RESOLVE_CONFLICT = "resolve_conflict"
KIND_CONFIRM_DECISION_PREF = "confirm_decision_pref"  # docs/02 §11 P1:确认高价值决策偏好升 confirmed
KIND_OPS_FIX = "ops_fix"  # L1 自愈 slice3:把运维诊断升成正式 H2A 决策卡(诊断 unverifiable;ACCEPT 只跑确定性可逆修复)

ALL_KINDS = (
    KIND_CRYSTALLIZE_SKILL,
    KIND_RUN_TASK,
    KIND_SET_PREFERENCE,
    KIND_ROUTE_TO_ROLE,
    KIND_ROUNDTABLE,
    KIND_RESOLVE_CONFLICT,
    KIND_CONFIRM_DECISION_PREF,
    KIND_OPS_FIX,
)

# Handler 协议:(proposal) -> (ok: bool, detail: str)。注入式,默认无副作用。
ProposalHandler = Callable[[object], "tuple[bool, str]"]


@dataclasses.dataclass(frozen=True)
class DispatchResult:
    """ACCEPT 兑现结果(给 caller / UI 回显)。"""
    proposal_id: str
    kind: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "ok": self.ok,
            "detail": self.detail,
        }


class PendingProposalRegistry:
    """待决议表(PR-2):proposal_id → Proposal。单进程内存表(本地 console 场景足够)。"""

    def __init__(self) -> None:
        self._pending: Dict[str, object] = {}

    def register(self, proposal) -> str:
        """登记一条 Proposal,返回 proposal_id(幂等:同 id 覆盖)。"""
        pid = getattr(proposal, "proposal_id", "") or ""
        if not pid:
            raise ValueError("proposal 缺 proposal_id(应由 Proposal.__post_init__ 派生)")
        self._pending[pid] = proposal
        return pid

    def get(self, proposal_id: str) -> Optional[object]:
        return self._pending.get(proposal_id)

    def remove(self, proposal_id: str) -> Optional[object]:
        """移除并返回(ACCEPT 兑现后 / REJECT 丢弃)。"""
        return self._pending.pop(proposal_id, None)

    def pending(self) -> List[object]:
        return list(self._pending.values())

    def __len__(self) -> int:
        return len(self._pending)

    def decide(
        self,
        proposal_id: str,
        decision: str,
        *,
        handlers: Optional[Dict[str, ProposalHandler]] = None,
    ) -> Optional[DispatchResult]:
        """按用户决策处置一条 Proposal(PR-3)。

        - ACCEPT → 查回 Proposal → 按 kind dispatch 兑现 → 移除 → 返 DispatchResult。
        - REJECT → 移除丢弃 → 返 DispatchResult(ok=True, "rejected")。
        - DEFER  → 留在 registry(下次再呈现)→ 返 DispatchResult(ok=True, "deferred")。

        未知 proposal_id → 返 None(caller 决定 404 / 忽略)。
        handlers 缺某 kind → ok=False, detail 说明(不抛,不副作用)。
        """
        decision = (decision or "").upper()
        proposal = self.get(proposal_id)
        if proposal is None:
            return None

        if decision == "REJECT":
            self.remove(proposal_id)
            return DispatchResult(proposal_id, getattr(proposal, "kind", ""), True, "rejected")

        if decision == "DEFER":
            return DispatchResult(proposal_id, getattr(proposal, "kind", ""), True, "deferred")

        if decision == "ACCEPT":
            kind = getattr(proposal, "kind", "")
            result = dispatch_accept(proposal, handlers or {})
            self.remove(proposal_id)  # 兑现后离开待决议表
            return result

        return DispatchResult(proposal_id, getattr(proposal, "kind", ""), False, f"unknown decision: {decision}")


def dispatch_accept(proposal, handlers: Dict[str, ProposalHandler]) -> DispatchResult:
    """按 kind 把一条已 ACCEPT 的 Proposal 分派给对应 handler(PR-3)。

    K5(PR-4):本函数只在用户 ACCEPT 后被调用,且**不构造 Envelope / 不替用户决策**;
    真副作用由注入的 handler 执行(默认无 handler → ok=False,只登记未兑现)。
    """
    pid = getattr(proposal, "proposal_id", "")
    kind = getattr(proposal, "kind", "")
    handler = handlers.get(kind)
    if handler is None:
        logger.info("[proposal] ACCEPT %s kind=%s 无 handler(未兑现,只记录)", pid, kind)
        return DispatchResult(pid, kind, False, f"no handler for kind={kind!r}")
    try:
        ok, detail = handler(proposal)
        return DispatchResult(pid, kind, bool(ok), str(detail))
    except Exception as e:  # handler 异常不外溢(兑现失败不打断 console)
        logger.warning("[proposal] handler kind=%s 异常: %s", kind, e)
        return DispatchResult(pid, kind, False, f"handler error: {e}")


def proposal_from_conflict(conflict, *, ts: float, strength: float = 0.7):
    """把 D4 检出的 Conflict 转成 resolve_conflict Proposal(docs/31 SC-5 → docs/30 D5)。

    conflict 是 duck-typed:需有 `.summary()` 与 `.to_proposal_payload()`(domain.skill_conflict
    .Conflict 满足)—— 避免 karvy ↔ domain 反向耦合。
    """
    from .atoms import Proposal  # 局部 import:避免模块级循环
    return Proposal(
        summary=conflict.summary(),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_RESOLVE_CONFLICT,
        payload=conflict.to_proposal_payload(),
    )


def proposal_for_route(
    *,
    domain_id: str,
    role: str,
    agent_id: str,
    domain_name: str,
    requirement: str,
    ts: float,
    strength: float = 0.8,
):
    """小卡资源匹配后,把"业务活委派给某 role"包成 route_to_role Proposal(docs/29 KC-3/30 D5)。

    payload:目标 Address(domain_id/role/agent_id)+ 域名 + 需求(原 intent)。
    ACCEPT 兑现 = route_to_role handler 让该 role 在其域治理下执行 requirement。
    """
    from .atoms import Proposal  # 局部 import 避免模块级循环
    return Proposal(
        summary=f"把「{requirement}」转给业务域「{domain_name}」的「{role}」",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_ROUTE_TO_ROLE,
        payload={
            "domain_id": domain_id,
            "role": role,
            "agent_id": agent_id,
            "domain_name": domain_name,
            "requirement": requirement,
        },
        # 决策依据(为什么):让决策卡的"怎么解的"区不空 —— 说清这是委派、归谁治理、ACCEPT 才落地。
        basis=(f"这件事属于业务域「{domain_name}」的职责;我不越界自己做,"
               f"而是委派给「{role}」在该域 value.md 治理下执行。你 ACCEPT 才真正转过去。"),
    )


def proposal_for_roundtable(
    *,
    group_domain_id: str,
    group_name: str,
    participants: List[str],
    participant_names: List[str],
    topic: str,
    ts: float,
    strength: float = 0.8,
):
    """私聊小卡说"让几个角色开圆桌讨论 X" → 编排意图(非单点委派)包成 roundtable Proposal。

    payload:目标群(group_domain_id)+ 参与者 agent_id 列表 + 主题(原 intent)。
    ACCEPT 兑现 = roundtable handler 切到该群 + 拉这些成员开一场圆桌(目标对齐式开场)。
    单点委派(route_to_role)是"交给一个人干";圆桌是"几个人坐一起讨论"—— 两种不同的编排。
    """
    from .atoms import Proposal  # 局部 import 避免模块级循环
    who = "、".join(participant_names) if participant_names else "群里的角色"
    return Proposal(
        summary=f"在「{group_name}」开圆桌,叫上 {who} 讨论「{topic}」",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_ROUNDTABLE,
        payload={
            "group_domain_id": group_domain_id,
            "group_name": group_name,
            "participants": list(participants),
            "participant_names": list(participant_names),
            "topic": topic,
        },
        basis=(f"你想让多个角色一起讨论,这是**圆桌**(几个人坐一起),不是把活交给一个人(委派)。"
               f"我会在群「{group_name}」拉上 {who},先和你对齐目标再开始讨论。你 ACCEPT 才真正开桌。"),
    )


def proposal_for_ops_fix(
    *,
    diagnosis: dict,
    finding_codes: List[str],
    ts: float,
    auto_fixable: bool = False,
    key: str = "",
    strength: float = 0.6,
):
    """把 L1 运维 agent 的诊断包成 ops_fix 决策卡(slice3:诊断升正式 H2A)。

    诚实铁律(三条,见 ops_agent / doctor):
    - 卡天然 **unverifiable**:LLM 诊断无 sig / 无验证门 → 决策卡 build 时自然落到"未核验"区,
      UI 标清这是诊断不是已证事实(绝不伪 grounded)。
    - **ACCEPT 绝不执行 LLM 文本**:只有 `auto_fixable`(底层 finding 在 doctor.AUTO_FIXABLE
      且 risk=reversible)时,handler 才跑**确定性** `doctor.repair_finding`;否则只"记下,
      请按步骤手动处理"——"自动修"绝不等于"让模型改你系统"。
    - basis(决策依据)= 原因 + 修法 + 风险/执行口径,让卡的"怎么解"区不空且口径诚实。

    幂等:proposal_id 按 `key`(默认 = 排序后的 finding_codes)稳定派生 → 同一坏态多次诊断
    收敛成同一张卡,不刷屏(registry.register 同 id 覆盖)。
    """
    from .atoms import Proposal  # 局部 import 避免模块级循环

    summary = (diagnosis.get("summary") or "").strip() or "运维诊断"
    cause = (diagnosis.get("cause") or "").strip()
    fix = (diagnosis.get("fix") or "").strip()
    risk = diagnosis.get("risk", "needs_approval")
    parts: List[str] = []
    if cause:
        parts.append(f"可能原因:{cause}")
    if fix:
        parts.append(f"建议修法:{fix}")
    if auto_fixable:
        parts.append("ACCEPT 将执行**确定性可逆修复**(先备份再重置,可从 .corrupt.bak 找回),不调模型改系统。")
    else:
        parts.append("这是 LLM 诊断、**未经验证**;ACCEPT 只表示你认可,系统**不会自动改**——请按上面步骤手动处理。")
    basis = "  ".join(parts)

    stable = (key or ",".join(sorted(finding_codes)) or summary)
    pid = "ops_fix-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=summary,
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_OPS_FIX,
        payload={
            "diagnosis": dict(diagnosis),
            "finding_codes": list(finding_codes),
            "auto_fixable": bool(auto_fixable),
            "risk": risk,
        },
        proposal_id=pid,
        basis=basis,
    )


__all__ = [
    "PendingProposalRegistry",
    "DispatchResult",
    "dispatch_accept",
    "ProposalHandler",
    "proposal_from_conflict",
    "proposal_for_route",
    "proposal_for_ops_fix",
    "KIND_CRYSTALLIZE_SKILL",
    "KIND_RUN_TASK",
    "KIND_SET_PREFERENCE",
    "KIND_ROUTE_TO_ROLE",
    "KIND_RESOLVE_CONFLICT",
    "KIND_OPS_FIX",
    "ALL_KINDS",
]
