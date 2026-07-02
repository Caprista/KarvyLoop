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
import json
import logging
from pathlib import Path
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
KIND_INFEASIBLE_REPORT = "infeasible_report"  # docs/02 §15.3:role 自助重规划耗尽/彻底不可行 → 带证据回头(非裸问题)
KIND_MERGE_ATOMS = "merge_atoms"  # docs/14 §11.2 / docs/02 §15.5:原子语义合并**不静默**——建议成卡,ACCEPT 才 rewire-before-delete
KIND_CONFIRM_RESULT = "confirm_result"  # docs/02 §15.5:人 accept role 结果=依据;ACCEPT→role 综合裁自造 atom 留不留
KIND_MERGE_KNOWLEDGE = "merge_knowledge"  # 知识库自动整理(daily 慢侧):近重复知识点升合并建议卡,ACCEPT 才 apply_belief_merge(先写后删)

ALL_KINDS = (
    KIND_CRYSTALLIZE_SKILL,
    KIND_RUN_TASK,
    KIND_SET_PREFERENCE,
    KIND_ROUTE_TO_ROLE,
    KIND_ROUNDTABLE,
    KIND_RESOLVE_CONFLICT,
    KIND_CONFIRM_DECISION_PREF,
    KIND_OPS_FIX,
    KIND_INFEASIBLE_REPORT,
    KIND_MERGE_ATOMS,
    KIND_CONFIRM_RESULT,
    KIND_MERGE_KNOWLEDGE,
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
    """待决议表(PR-2):proposal_id → Proposal。

    P1-c:可选**落盘持久化** —— 传 `persist_path` 后,待决卡跨重启存活(决策 loop
    不该因为一次重启就丢掉"还挂着待你拍的板";DEFER 挂起的更该活着)。落盘失败/文件损坏
    一律 fail-safe(不阻断、不误杀,靠后续 register 重建),与 skills-lock 同调。
    """

    def __init__(self, persist_path=None) -> None:
        self._pending: Dict[str, object] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._load()

    def _load(self) -> None:
        from karvyloop.karvy.atoms import Proposal
        p = self._persist_path
        if not p or not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[proposal] 待决卡持久化文件损坏,忽略(靠后续 register 重建):%s", p)
            return
        for item in (data.get("pending") or []):
            try:
                prop = Proposal.from_dict(item)
            except Exception as e:
                logger.debug("[proposal] 跳过坏待决卡:%s", e)
                continue
            if getattr(prop, "proposal_id", ""):
                self._pending[prop.proposal_id] = prop

    def _save(self) -> None:
        p = self._persist_path
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "pending": [prop.to_dict() for prop in self._pending.values() if hasattr(prop, "to_dict")],
            }
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # 落盘失败不阻断内存操作
            logger.warning("[proposal] 待决卡落盘失败(不阻断):%s", e)

    def register(self, proposal) -> str:
        """登记一条 Proposal,返回 proposal_id(幂等:同 id 覆盖)。"""
        pid = getattr(proposal, "proposal_id", "") or ""
        if not pid:
            raise ValueError("proposal 缺 proposal_id(应由 Proposal.__post_init__ 派生)")
        self._pending[pid] = proposal
        self._save()
        return pid

    def get(self, proposal_id: str) -> Optional[object]:
        return self._pending.get(proposal_id)

    def remove(self, proposal_id: str) -> Optional[object]:
        """移除并返回(ACCEPT 兑现后 / REJECT 丢弃)。"""
        removed = self._pending.pop(proposal_id, None)
        if removed is not None:
            self._save()
        return removed

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
        edits: Optional[Dict[str, str]] = None,
    ) -> Optional[DispatchResult]:
        """按用户决策处置一条 Proposal(PR-3)。

        - ACCEPT → 查回 Proposal → 按 kind dispatch 兑现 → 移除 → 返 DispatchResult。
        - REJECT → 移除丢弃 → 返 DispatchResult(ok=True, "rejected")。
        - DEFER  → 留在 registry(下次再呈现)→ 返 DispatchResult(ok=True, "deferred")。

        edits(#42 优化①「改了再批」):ACCEPT 时把用户就地改过的 payload 字段**覆盖后兑现**。
        安全边界:只允许覆盖 payload 里**已存在**的字符串字段(不许注入新键/改类型),单值封顶 8k。
        修改本身是楔子最富的偏好信号 —— 信号记录在 decision_wire(不在此处)。

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
            eff = apply_payload_edits(proposal, edits) if edits else proposal
            result = dispatch_accept(eff, handlers or {})
            self.remove(proposal_id)  # 兑现后离开待决议表
            return result

        return DispatchResult(proposal_id, getattr(proposal, "kind", ""), False, f"unknown decision: {decision}")


def apply_payload_edits(proposal, edits: Dict[str, str]):
    """把「改了再批」的字段覆盖进 payload,返回新 Proposal(原对象不动,frozen)。

    白名单式:只覆盖 payload 里**已有**且原值为 str 的键;新值必须是 str(封顶 8000 字);
    其余一律忽略(不抛 —— 决策流是命脉,坏 edits 静默降级成原样兑现)。"""
    import dataclasses
    try:
        base = dict(getattr(proposal, "payload", {}) or {})
        clean = {k: str(v)[:8000] for k, v in (edits or {}).items()
                 if k in base and isinstance(base.get(k), str) and isinstance(v, str) and str(v).strip()}
        if not clean:
            return proposal
        return dataclasses.replace(proposal, payload={**base, **clean})
    except Exception:
        return proposal


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


def proposal_for_merge_atoms(
    *,
    canonical_id: str,
    member_ids: List[str],
    merged_purpose: str = "",
    merged_tools: Optional[List[str]] = None,
    reason: str = "",
    ts: float,
    strength: float = 0.55,
):
    """把一簇近义原子的**合并建议**包成 merge_atoms 决策卡(docs/14 §11.2 / docs/02 §15.5)。

    原子库是护城河资产 → **绝不静默合并**:suggest = 这张提案卡,ACCEPT = handler 才真 `apply_merge`
    (rewire-before-delete:先把所有引用冗余原子的角色改写到规范原子,再删,**不留悬空引用**)。
    - basis 诚实交代"合并谁、为什么、ACCEPT 会发生什么"。
    - 幂等:proposal_id 按 canonical + 排序成员稳定派生 → 同一簇重复建议收敛成一张卡,不刷屏。
    """
    from .atoms import Proposal  # 局部 import 避免模块级循环

    members = [str(m).strip() for m in (member_ids or []) if str(m).strip()]
    members = list(dict.fromkeys(members))  # 去重保序
    canon = (canonical_id or "").strip() or (members[0] if members else "")
    tools = [str(t).strip() for t in (merged_tools or []) if str(t).strip()]

    parts: List[str] = [f"把 {len(members)} 个近义原子合并成规范原子「{canon}」:{', '.join(members)}。"]
    if reason:
        parts.append(f"判断依据:{reason}")
    parts.append("合并 = 减少重复、提升复用(护城河:批量导入的原子常因近义不并而 reuse 偏低)。")
    parts.append("ACCEPT 会 **rewire-before-delete**:先把所有引用这些原子的角色改写到规范原子,"
                 "再删冗余,**绝不留悬空引用**;不动也安全(只是不并)。")
    basis = "  ".join(parts)

    stable = f"{canon}:{','.join(sorted(members))}"
    pid = "merge_atoms-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=f"合并 {len(members)} 个近义原子 → 「{canon}」",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_MERGE_ATOMS,
        payload={
            "canonical_id": canon,
            "member_ids": members,
            "merged_purpose": (merged_purpose or "").strip()[:400],
            "merged_tools": tools[:16],
            "reason": (reason or "").strip()[:200],
        },
        proposal_id=pid,
        basis=basis,
    )


def proposal_for_merge_knowledge(
    *,
    member_contents: List[str],
    merged_content: str,
    ts: float,
    member_titles: Optional[List[str]] = None,
    merged_title: str = "",
    reason: str = "",
    strength: float = 0.55,
):
    """知识库整理建议卡(daily 慢侧自动升;手动按钮路径不经此)。ACCEPT → apply_belief_merge。

    proposal_id 由成员内容稳定哈希 → 同簇幂等(registry 同 id 覆盖;tick 层再加冷却防唠叨)。"""
    from .atoms import Proposal

    members = [str(c).strip() for c in (member_contents or []) if str(c).strip()]
    merged = (merged_content or "").strip()
    if len(members) < 2 or not merged:
        raise ValueError("merge_knowledge 需要 ≥2 条成员 + 非空合并内容")
    titles = [str(x).strip() for x in (member_titles or []) if str(x).strip()]
    label = merged_title.strip() or merged[:24]
    shown = "、".join((titles or [m[:18] for m in members])[:4])
    parts: List[str] = [f"这 {len(members)} 条知识点讲的基本是同一件事:{shown}。"]
    if reason:
        parts.append(f"判断依据:{reason}")
    parts.append(f"建议合并成一条「{label}」。ACCEPT = 先写入合并条、再删被并旧条(中途失败不丢数据);"
                 "不动也安全(只是库里留着近重复)。")
    basis = "  ".join(parts)

    stable = "\n".join(sorted(members))
    pid = "merge_knowledge-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=f"🧹 合并 {len(members)} 条近重复知识 → 「{label}」",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_MERGE_KNOWLEDGE,
        payload={
            "member_contents": members,
            "member_titles": titles,
            "merged_title": merged_title.strip()[:80],
            "merged_content": merged[:2000],
            "reason": (reason or "").strip()[:200],
        },
        proposal_id=pid,
        basis=basis,
    )


def proposal_for_confirm_result(
    *,
    role: str,
    requirement: str,
    minted: List[dict],
    ts: float,
    domain_id: str = "",
    strength: float = 0.7,
):
    """role 完成委派、过程中自造了原子 → 升「结果确认卡」(docs/02 §15.5,问责链 人←role←atom)。

    人 accept 的是 **role 的结果**(不直接碰 atom);ACCEPT = 这份认可作**依据** → handler 让 role
    (LLM 站 role 视角)综合裁每个自造 atom 留不留。不处理/拒 → atom 留作 provisional,没人复用会被
    ④ 巡检自动清(孤儿撤),所以**不显式处理 REJECT** 也安全。`minted` = [{"id","purpose"}]。
    """
    from .atoms import Proposal

    r = (role or "").strip() or "角色"
    req = (requirement or "").strip() or "这个任务"
    ids = [str(m.get("id", "")).strip() for m in minted if str(m.get("id", "")).strip()]
    lines = [f"{m.get('id', '')}:{(m.get('purpose') or '').strip()[:80]}" for m in minted if m.get("id")]
    basis = (
        f"「{r}」为完成「{req}」临时造了 {len(ids)} 个新能力:" + ";".join(lines) + "。"
        f"你认可这次结果 → 由 {r} 综合裁哪些值得留进自己的工具箱(被别的角色复用才正式转正);"
        f"不处理 / 不认可 → 它们留作试用,长期没人用会被自动清掉。"
    )
    stable = f"{r}:{req}:{','.join(sorted(ids))}"
    pid = "confirm_result-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=f"「{r}」做完「{req}」,新造了 {len(ids)} 个能力 —— 认可结果就留有用的?",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_CONFIRM_RESULT,
        payload={"role": r, "requirement": req, "domain_id": domain_id, "minted": list(minted)},
        proposal_id=pid,
        basis=basis,
    )


def proposal_for_infeasible_report(
    *,
    goal: str,
    role: str,
    attempts: List[dict],
    ts: float,
    domain_id: str = "",
    domain_name: str = "",
    strength: float = 0.9,
):
    """把"role 自助重规划耗尽 / 判定彻底不可行"包成「不可行报告」决策卡(docs/02 §15.3)。

    尽责下属契约的回头形态:**带证据,不甩裸问题**。
    - `attempts` = 真实尝试轨迹 [{"attempt": int, "terminal": str, "note": str}],从 Trace/loop 拼。
      **basis 必须由它拼**——无轨迹 = 假报告(§15.7 不变量),调用方该保证非空。
    - 天然 **unverifiable**(无 sig / 无验证门)→ 决策卡 build 自然落"未核验"区,不伪 grounded。
    - 逼判断:ACCEPT(接纳此结论 / 放下)、DEFER(暂缓)、REJECT(我来改目标或补资源再试)。
    - 幂等:proposal_id 按 role+goal+终止原因集合稳定派生 → 同一卡住的目标收敛成一张卡,不刷屏。
    """
    from .atoms import Proposal  # 局部 import 避免模块级循环

    g = (goal or "").strip() or "(未命名目标)"
    r = (role or "").strip() or "角色"
    terminals = [str(a.get("terminal") or "").strip() for a in attempts if a.get("terminal")]
    n = len(attempts)

    # basis 由真实轨迹拼(§15.7:回头必带尝试轨迹)
    trail_lines: List[str] = []
    for a in attempts:
        i = a.get("attempt", "?")
        term = (a.get("terminal") or "").strip() or "未完成"
        note = (a.get("note") or "").strip()
        trail_lines.append(f"第 {i} 次:{term}" + (f"（{note}）" if note else ""))
    trail = ";".join(trail_lines) if trail_lines else "（无轨迹）"
    basis = (
        f"「{r}」为完成「{g}」自助重规划了 {n} 次仍没成。轨迹:{trail}。"
        f"系统靠自动重规划突破不了 —— 这是带证据的结论,不是问你「怎么办」:"
        f"请你定夺(接纳并放下 / 暂缓 / 我来调整目标或补资源再试)。"
    )

    summary = f"「{r}」追求「{g}」未达成(自助重规划 {n} 次)"
    stable = f"{r}:{g}:{','.join(sorted(set(terminals)))}"
    pid = "infeasible-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=summary,
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_INFEASIBLE_REPORT,
        payload={
            "goal": g,
            "role": r,
            "domain_id": domain_id,
            "domain_name": domain_name,
            "attempts": list(attempts),
            "terminal_reasons": sorted(set(terminals)),
        },
        proposal_id=pid,
        basis=basis,
    )


__all__ = [
    "PendingProposalRegistry",
    "DispatchResult",
    "dispatch_accept",
    "apply_payload_edits",
    "ProposalHandler",
    "proposal_from_conflict",
    "proposal_for_route",
    "proposal_for_roundtable",
    "proposal_for_ops_fix",
    "proposal_for_infeasible_report",
    "proposal_for_merge_atoms",
    "proposal_for_merge_knowledge",
    "proposal_for_confirm_result",
    "KIND_CRYSTALLIZE_SKILL",
    "KIND_RUN_TASK",
    "KIND_SET_PREFERENCE",
    "KIND_ROUTE_TO_ROLE",
    "KIND_ROUNDTABLE",
    "KIND_RESOLVE_CONFLICT",
    "KIND_CONFIRM_DECISION_PREF",
    "KIND_OPS_FIX",
    "KIND_INFEASIBLE_REPORT",
    "KIND_MERGE_ATOMS",
    "KIND_MERGE_KNOWLEDGE",
    "KIND_CONFIRM_RESULT",
    "ALL_KINDS",
]
