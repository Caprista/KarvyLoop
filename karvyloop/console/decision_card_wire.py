"""decision_card_wire — 把决策卡内核接进活的 H2A 提案层(console 侧)。

决策卡只长在 role↔人 的**提案**上(不在 atom 执行层)。
- **接地**:若提案 payload 引用了 sig 且 verify store 有该 sig 的通过证明 → 接地依据(✓);
  否则老实 `unverifiable`(problem/approach 是 Karvy 复述,标"未核验")。
- **逼判断 + 反投降**:用户对依据的改/删(engaged)回喂 observe_decision(EDIT 信号);
  "零修改 Accept"计入 SurfaceTracker,达阈值要求轻确认。
最终 ACCEPT/REJECT/DEFER 仍走既有 /api/h2a_decide(K5 不变量不动),本模块只做"翻译 + 判断捕获"。
"""
from __future__ import annotations

from typing import Any, Optional

from karvyloop.cognition.decision_card import SurfaceTracker, build_decision_card


def _verify_store(app: Any):
    ml = getattr(app.state, "main_loop", None)
    return getattr(ml, "verify", None) if ml is not None else None


# 决策偏好 kind → 人话标签(与 decision_wire.proposal_for_confirm_decision 一致)
_PREF_LABEL = {"constraint": "约束", "taste": "品味", "standing": "站位"}


def _recall_aligned_prefs(app: Any, payload: dict) -> tuple[list[dict], int]:
    """召回**与本提案相关**的、你已结晶的决策偏好 —— 摆到卡上(用你自己的标准帮你拍)。

    楔子(decision-pref crystallization)在拍板那一刻**可见**:不是凭空让你拍,而是
    "你以前的标准是 X,我已按它预对齐"。只读呈现(改偏好走 🧭 决策偏好管理面,不在卡上改)。
    按相关性召回(规模大也先摆相关的)+ 返回**漏掉条数**(不静默丢,卡上明示"+N 条")。
    返回 (prefs, omitted)。
    """
    mem = getattr(app.state, "memory", None)
    if mem is None:
        return [], 0
    try:
        from karvyloop.crystallize.decision_pref import (
            applicable_decision_prefs, is_high_value, receipt_gists)
        beliefs: list = []
        idx = getattr(mem, "index", None)
        if idx is None:
            return [], 0
        for scope in ("personal", "domain"):
            try:
                beliefs.extend(idx.all(scope))
            except Exception:
                pass
        domain = payload.get("domain_id", "") or ""
        role = payload.get("role", "") or ""
        # query = 本提案文本(需求/主题/摘要)→ 按相关性排;无则回退强度
        query = " ".join(str(payload.get(k, "")) for k in ("requirement", "topic", "intent", "summary")).strip()
        applicable = applicable_decision_prefs(beliefs, query=query, domain=domain, role=role)
        LIMIT = 5
        out: list[dict] = []
        for b in applicable[:LIMIT]:
            kind = b.provenance.get("kind", "taste")
            out.append({
                "content": b.content,
                "kind": kind,
                "kind_label": _PREF_LABEL.get(kind, "偏好"),
                "strength": round(float(b.provenance.get("strength", 0.0)), 2),
                "status": b.provenance.get("status", ""),   # provisional | confirmed
                "high_value": is_high_value(b),
                "receipt": receipt_gists(b),   # 回执:这条标准来自你哪几次拍板(可核,答 Q2)
            })
        return out, max(0, len(applicable) - len(out))
    except Exception:
        return [], 0   # 召回失败不挡决策卡(降级为无预对齐)


async def check_violations(app: Any, proposal_text: str, standards: list[str]) -> list[dict]:
    """Cut 2 守线:LLM 判这条提案**违背**了哪些已定标准。无 gateway/无标准/空提案 → []。

    宁可漏拦不可错拦(prompt 已嘱)。返回 [{"standard","why"}]。失败 → [](不挡决策卡)。
    """
    rk = getattr(getattr(app, "state", None), "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None or not standards or not (proposal_text or "").strip():
        return []
    from karvyloop.crystallize.decision_pref import VIOLATION_SYSTEM, parse_violations
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(standards))
    material = f"提案:\n{proposal_text.strip()}\n\n用户已定的决策标准:\n{numbered}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=rk.get("model_ref") or None))
        async for ev in gw.complete([{"role": "user", "content": material}], [], ref,
                                    system=SystemPrompt(static=[VIOLATION_SYSTEM])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        return []
    return parse_violations(out)


def _attach_violations(app: Any, d: dict, aligned: list[dict], problem: str,
                       approach: str, payload: dict) -> None:
    """对召回到的标准跑一道守线(只在有标准时,省 token),把违背摆到卡最显眼处 + 逼拍前确认。"""
    if not aligned:
        return
    proposal_text = " ".join([problem or "", approach or ""]
                             + [str(payload.get(k, "")) for k in ("requirement", "topic", "intent")]).strip()
    try:
        import asyncio
        violations = asyncio.run(check_violations(app, proposal_text, [a["content"] for a in aligned]))
    except Exception:
        violations = []
    if not violations:
        return
    # 给每条违背配上回执(来自你哪几次拍板)+ kind,卡上"它替我把关"才可核
    by_content = {a["content"]: a for a in aligned}
    for v in violations:
        src = by_content.get(v["standard"])
        if src:
            v["receipt"] = src.get("receipt", [])
            v["kind_label"] = src.get("kind_label", "")
    d["violations"] = violations
    d["high_value"] = True       # 违背 = 高价值,拍前必确认
    d["needs_recheck"] = True


def build_card_for_proposal(app: Any, proposal_id: str) -> Optional[dict]:
    """从一条待决提案建决策卡(接地于 verify store,无则 honest unverifiable)。"""
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return None
    p = reg.get(proposal_id)
    if p is None:
        return None
    problem = getattr(p, "summary", "") or ""
    approach = getattr(p, "basis", "") or ""        # 提案的"决策依据(为什么)"= 怎么解的复述
    payload = getattr(p, "payload", {}) or {}
    gate_results = None
    provenance: list[str] = []
    sig = payload.get("sig") or ""
    vs = _verify_store(app)
    if sig and vs is not None and vs.has_gate(sig):
        proof = vs.latest_proof(sig)
        note = (getattr(proof, "note", "") or "").strip() or "已通过验证门"
        gate_results = [(note, True)]               # 接地:真有通过证明
        tref = getattr(proof, "trace_ref", "") if proof is not None else ""
        if tref:
            provenance = [tref]
    card = build_decision_card(problem=problem, approach=approach,
                               gate_results=gate_results, provenance=provenance)
    d = card.to_dict()
    d["proposal_id"] = proposal_id
    # 预对齐:把你已结晶、适用本场景的决策偏好摆上卡。命中高价值偏好 → 标 high_value
    # (价值闸输入:这类该你拍的别静默放过)。
    aligned, aligned_omitted = _recall_aligned_prefs(app, payload)
    d["aligned_prefs"] = aligned
    d["aligned_omitted"] = aligned_omitted   # 不静默漏:还有几条适用标准没摆上(卡上明示)
    d["high_value"] = any(p.get("high_value") for p in aligned)
    if aligned:
        # 高价值命中的那条标准文本(给前端"拍前确认"弹窗点名用)
        hv = next((p for p in aligned if p.get("high_value")), aligned[0])
        d["high_value_standard"] = hv.get("content", "")
    # 反投降当前态(只读 tracker,不创建)→ 前端据此在**拍之前**拦,不再马后炮
    tracker = getattr(app.state, "decision_card_tracker", None)
    d["needs_recheck"] = bool(tracker.needs_recheck()) if tracker is not None else False
    d["violations"] = []
    # Cut 2 违背即拦:对召回到的标准跑守线(放在最后 —— 会把 high_value/needs_recheck 升上去)
    _attach_violations(app, d, aligned, problem, approach, payload)
    return d


def _tracker(app: Any) -> SurfaceTracker:
    t = getattr(app.state, "decision_card_tracker", None)
    if t is None:
        t = SurfaceTracker()
        app.state.decision_card_tracker = t
    return t


def judge_card(app: Any, *, proposal_id: str, decision: str, engaged: bool,
               edited_criteria: Optional[list] = None, basis: str = "") -> dict:
    """记录用户对决策卡的判断:反投降计数 + 回喂 observe_decision(喂楔子)。

    两种 engagement 都喂结晶,但信号强度不同:
    - **basis(你在卡上陈述的判断依据)= STATE / 显式信号**——最强,1 次即可结晶
      (尤其救 unverifiable 卡:它没有 criteria 可改/删,以前永远拿不到 engaged → 楔子在最常见
       卡上瞎;现在你直接说"我凭什么这么定",楔子从常见卡也学得到)。
    - **edited_criteria(改/删接地依据)= EDIT 信号**(grounded 卡上)。
    任一存在都算真判断(engaged)→ 反投降重置 + 高价值闸放行。返回 {ok, needs_recheck}。
    """
    basis = (basis or "").strip()
    eff_engaged = bool(engaged) or bool(basis)
    tracker = _tracker(app)
    tracker.record(accepted=(str(decision).upper() == "ACCEPT"), engaged=eff_engaged)
    if eff_engaged:
        try:
            from karvyloop.console.decision_wire import observe_decision
            from karvyloop.crystallize.decision_pref import DecisionSample
            if basis:
                observe_decision(app, DecisionSample(
                    decision="STATE", context=basis,
                    reason="你在决策卡上陈述的判断依据(显式)", scope="personal"))
            ctx = "; ".join(c.get("text", "") for c in (edited_criteria or []) if c.get("text"))
            if ctx:
                observe_decision(app, DecisionSample(
                    decision="EDIT", context=ctx,
                    reason="判定依据被改/删(决策卡)", scope="personal"))
        except Exception:
            pass   # 回喂失败不挡用户(宁可丢信号不卡流程)
    return {"ok": True, "needs_recheck": tracker.needs_recheck()}


__all__ = ["build_card_for_proposal", "judge_card"]
