"""console/decision_wire — 决策接口结晶的接线(docs/02 §11 的 console 侧)。

`crystallize/decision_pref.py` 是纯逻辑(解析/双关门/预对齐);本模块把它接进 console:
- **observe**:H2A 决策 → 攒进缓冲(信号源,§11.3)。
- **crystallize**:攒够一批 → LLM 抽候选 → 双关门 promote(provisional)→ 写认知库(Belief)。
  fire-and-forget 但 **fail-loud**(复用 §0.7 `schedule_system_error`,失败不静默死)。
- **prealign**:提案/drive 前召回决策偏好 → 注入 governance(§11.5 预对齐)。

P0 范围(诚实标注):
- 信号源只吃 H2A 决策(最结构化);显式陈述靠决策 reason 里的明说被 LLM 标 explicit。
- 写的偏好是 **personal 全局**(applies 空);**域/角色限定的偏好 = P1**(需 LLM 归因或分组)。
- 隐式候选靠**跨批复现计数** ≥K 才提升(同方向观察 ≥K 次,§11.4 关 1);显式 1 次即过。
- **相反决策翻转 strength / H2A 确认升 confirmed = P1**(下方留位)。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from karvyloop.crystallize.decision_pref import (
    DecisionSample,
    is_decision_pref,
    is_high_value,
    maybe_promote,
    prealign_block,
    reconcile_decisions,
    reinforce,
    should_revoke,
    weaken,
)

logger = logging.getLogger(__name__)

DECISION_BATCH = 3   # 攒够 N 个决策样本 → 结晶一次(决策稀疏,批小;省 token)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _batch_context(batch: list) -> tuple[str, str]:
    """这批决策的统一情境(域/角色)—— 仅当全批同一个非私聊(非 l0)域/角色才给,否则空(全局)。"""
    doms = {s.domain for s in batch if getattr(s, "domain", "") and s.domain not in ("", "l0")}
    roles = {s.role for s in batch if getattr(s, "role", "")}
    return (next(iter(doms)) if len(doms) == 1 else "",
            next(iter(roles)) if len(roles) == 1 else "")


def observe_decision(app: Any, sample: DecisionSample) -> None:
    """记一次决策样本进缓冲(进程内;未结晶的原始信号丢了也不致命,同 distill watermark)。"""
    buf = getattr(app.state, "decision_samples", None)
    if buf is None:
        buf = app.state.decision_samples = []
    buf.append(sample)


def _existing_pref_list(mem: Any) -> list:
    """已有决策偏好(有序;1-based 编号给 LLM 标矛盾用)。"""
    out: list = []
    try:
        idx = mem.index
        for scope in ("personal", "domain"):
            for b in idx.all(scope):
                if is_decision_pref(b):
                    out.append(b)
    except Exception:
        pass
    return out


async def maybe_crystallize_decisions(app: Any) -> int:
    """攒够一批 → 抽新候选 + 标矛盾 → 加固/翻转/结晶。返回新结晶条数(reinforce/weaken/revoke 另计 log)。"""
    import time
    buf = getattr(app.state, "decision_samples", None)
    if not buf or len(buf) < DECISION_BATCH:
        return 0
    batch = buf[:]
    buf.clear()
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    mem = getattr(app.state, "memory", None)
    if gw is None or mem is None:
        return 0
    now = time.time()
    existing = _existing_pref_list(mem)
    ctx_domain, ctx_role = _batch_context(batch)
    new_c, contradict_idxs = await reconcile_decisions(
        batch, existing=[b.content for b in existing], gateway=gw,
        model_ref=rk.get("model_ref", ""), context={"domain": ctx_domain, "role": ctx_role})
    by_norm = {_norm(b.content): b for b in existing}
    evidence = [getattr(s, "ts", 0.0) for s in batch]
    weakened = revoked = 0

    # P1 不固化你:相反决策 → 削弱;provisional 跌破下限 → 撤销(confirmed 只降不删)
    for idx in sorted({i for i in contradict_idxs if 1 <= i <= len(existing)}):
        tgt = existing[idx - 1]
        w = weaken(tgt, now=now)
        try:
            mem.archive(tgt)
            if should_revoke(w):
                revoked += 1
                by_norm.pop(_norm(tgt.content), None)
            else:
                mem.write(w)
                by_norm[_norm(w.content)] = w
                weakened += 1
        except Exception as e:
            logger.warning(f"[decision_pref] 翻转偏好失败: {e}")

    # 新候选:加固/双关门 promote/高价值弹确认(共用 helper;contradiction 是本路径专属,上面已处理)
    written, reinforced = await crystallize_candidates(
        app, new_c, ctx_domain=ctx_domain, ctx_role=ctx_role,
        evidence=evidence, now=now, by_norm=by_norm)

    if written or reinforced or weakened or revoked:
        logger.info(f"[decision_pref] 结晶 new={written} 加固={reinforced} "
                    f"削弱={weakened} 撤销={revoked}")
    return written


async def crystallize_candidates(app: Any, candidates: list, *, ctx_domain: str = "",
                                 ctx_role: str = "", evidence: Optional[list] = None,
                                 now: Optional[float] = None,
                                 by_norm: Optional[dict] = None) -> tuple[int, int]:
    """一批已抽好的候选偏好 → 加固(匹配已有)/ 双关门 promote / 高价值弹 H2A 确认。

    **共用于 H2A 决策路径与 distill 显式陈述路径**(都不在这里做 contradiction —— 那是 reconcile
    专属,只 H2A 路径有)。返回 (written, reinforced)。
    """
    import time
    mem = getattr(app.state, "memory", None)
    if mem is None or not candidates:
        return 0, 0
    if now is None:
        now = time.time()
    if evidence is None:
        evidence = []
    if by_norm is None:
        by_norm = {_norm(b.content): b for b in _existing_pref_list(mem)}
    recur = getattr(app.state, "decision_recurrence", None)
    if recur is None:
        recur = app.state.decision_recurrence = {}
    written = reinforced = 0
    high_value: list = []
    for c in candidates:
        key = _norm(c.get("content", ""))
        if not key:
            continue
        if key in by_norm:
            old = by_norm[key]
            upd = reinforce(old, evidence_add=evidence, now=now)
            try:
                mem.archive(old)
                mem.write(upd)
                by_norm[key] = upd
                reinforced += 1
            except Exception as e:
                logger.warning(f"[decision_pref] 加固偏好失败: {e}")
            continue
        if c.get("explicit"):
            support = 1
        else:
            recur[key] = recur.get(key, 0) + 1
            support = recur[key]
        # P1 LLM 归因:scope=domain 且本批有统一域 → 限定该域/角色;否则全局
        use_domain = ctx_domain if (c.get("scope") == "domain" and ctx_domain) else ""
        use_role = ctx_role if (c.get("scope") == "domain" and ctx_domain) else ""
        belief = maybe_promote(c, support_count=support, scope="personal",
                               domain=use_domain, role=use_role, evidence=evidence)
        if belief is None:
            continue   # 隐式未达 K → 留在 recur 等下次复现
        try:
            mem.write(belief)
            by_norm[key] = belief
            recur.pop(key, None)
            written += 1
            if is_high_value(belief):
                high_value.append(belief)
        except Exception as e:
            logger.warning(f"[decision_pref] 写决策偏好失败: {e}")
    # 高价值新偏好 → 弹一次 H2A 确认(每条只弹一次,守"按钮越来越少")
    await _propose_confirmations(app, high_value, now)
    return written, reinforced


def proposal_for_confirm_decision(belief: Any, *, now: float) -> Any:
    """高价值决策偏好 → 一条"记成默认偏好吗?"的 H2A 建议(进预判列,轻提示)。"""
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.proposal_registry import KIND_CONFIRM_DECISION_PREF
    kind = belief.provenance.get("kind", "taste")
    label = {"constraint": "约束", "taste": "品味", "standing": "站位"}.get(kind, "偏好")
    return Proposal(
        summary=f"记成你的默认偏好吗?[{label}] {belief.content}",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=float(belief.provenance.get("strength", 0.7)),
        evidence_refs=(), habit_id=0, model_ref="", ts=now,
        kind=KIND_CONFIRM_DECISION_PREF,
        payload={"content": belief.content, "pref_kind": kind},
        basis="我从你的拍板里注意到这条;记下来后,我提案会提前按它对齐,你少拒、少重复解释自己。",
    )


async def _propose_confirmations(app: Any, beliefs: list, now: float) -> None:
    """对高价值新偏好弹 H2A 确认(每条内容只弹一次)。失败不影响结晶。"""
    if not beliefs:
        return
    proposed = getattr(app.state, "decision_confirm_proposed", None)
    if proposed is None:
        proposed = app.state.decision_confirm_proposed = set()
    try:
        from karvyloop.console.proposals import broadcast_proposal
    except Exception:
        return
    for b in beliefs:
        key = _norm(b.content)
        if key in proposed:
            continue
        proposed.add(key)
        try:
            await broadcast_proposal(app, proposal_for_confirm_decision(b, now=now))
        except Exception as e:
            logger.debug(f"[decision_pref] 弹确认建议失败(不影响结晶): {e}")


def schedule_decision_crystallize(app: Any) -> None:
    """fire-and-forget 调度决策结晶(不阻塞决策响应)。失败 fail-loud(§0.7)不静默死。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    tasks = getattr(app.state, "_decision_tasks", None)
    if tasks is None:
        tasks = app.state._decision_tasks = set()
    task = loop.create_task(maybe_crystallize_decisions(app))
    tasks.add(task)

    def _on_done(t: Any) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except Exception:
            return
        if exc is not None:
            logger.error(f"[decision_pref] 结晶后台任务异常: {exc}")
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "decision_crystallize", str(exc))
            except Exception:
                pass

    task.add_done_callback(_on_done)


def prealign_governance(app: Any, mem: Any, *, domain: str = "", role: str = "") -> str:
    """提案/drive 前:召回适用决策偏好 → 预对齐块(注入 governance)。空 → ""。"""
    if mem is None:
        return ""
    try:
        beliefs: list = []
        idx = mem.index
        for scope in ("personal", "domain"):
            for b in idx.all(scope):
                beliefs.append(b)
        return prealign_block(beliefs, domain=domain, role=role)
    except Exception:
        return ""


__all__ = [
    "DECISION_BATCH", "observe_decision", "maybe_crystallize_decisions",
    "crystallize_candidates", "schedule_decision_crystallize", "prealign_governance",
    "proposal_for_confirm_decision",
]
