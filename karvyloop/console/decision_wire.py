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


def record_decision_signals(app: Any, *, decision: str, proposal_id: str,
                            reason: str = "", domain: str = "", role: str = "",
                            edits: Optional[dict] = None) -> None:
    """一次 H2A 拍板 → 三路信号(样本缓冲→结晶 / stats 复利 / decision_log 回看)**单一接缝**。

    P3-a 病根:此前只有 WS 路径接了这三路,REST `/api/h2a_decide` 一路都没接 ——
    走 REST 拍的板从不进偏好结晶回路(决策 loop 白拍)。两条传输路都调本函数,信号对齐。
    绝不打断决策流(H2A 是命脉)→ 整段自吞;confirm_decision_pref 不观察(防结晶元循环)。

    edits(#42 优化①):「改了再批」的字段修改是**最富的偏好信号**(你不只认/拒,还亲手示范
    了"该是什么样")→ 把 原文→改文 的对照折进样本 reason,偏好结晶的 LLM 能直接读出你的标准。

    fail-loud(闭环审计断④):此前整函数一个大 `except: pass` —— 任一段炸,这次拍板的
    **全部学习信号静默蒸发**(正打在决策接口结晶的进料口)。改为**逐段 try**:每段失败
    logger.warning(带 proposal_id + 哪一段),且**不连坐**后面的段(决策流仍绝不被打断)。
    """
    import time as _time
    # 段1:读回提案(summary/kind/payload —— 后面各段的原料;失败降级为用 id 当 context)
    ctx = ""
    kind = ""
    orig_payload: dict = {}
    eff_domain = domain or ""
    try:
        reg = getattr(app.state, "proposal_registry", None)
        if reg is not None:
            p = reg.get(proposal_id)
            ctx = getattr(p, "summary", "") or ""
            kind = getattr(p, "kind", "") or ""
            orig_payload = dict(getattr(p, "payload", {}) or {})
            if p is not None:
                # 域以**卡自己的 payload 为权威**(与 silence._proposal_domain 同口径):
                # 传输层的 to_address_domain_id 是 pydantic 占位默认("dom-1"),前端常不填
                # → 直接用会把全部流水记进假域,静音分桶两侧永远对不上(对抗验收缺陷①)。
                _cd = str(orig_payload.get("domain_id")
                          or orig_payload.get("group_domain_id") or "").strip()
                eff_domain = "" if _cd in ("", "l0") else _cd
    except Exception as e:
        logger.warning(f"[decision_wire] 读回提案失败(proposal_id={proposal_id},"
                       f"样本 context 降级为 id): {e}")
    if kind == "confirm_decision_pref":
        return   # 确认"决策偏好"本身不是工作决策(否则确认偏好又生样本)
    # 段2:折「改了再批」对照进 reason(最富偏好信号;失败退回原 reason)
    eff_reason = reason
    try:
        if edits:
            pairs = []
            for k, v in edits.items():
                if not isinstance(v, str) or not v.strip():
                    continue
                old = orig_payload.get(k)
                if isinstance(old, str) and old.strip() and old.strip() != v.strip():
                    pairs.append(f"{k}:「{old.strip()[:120]}」→「{v.strip()[:120]}」")
            if pairs:
                eff_reason = (f"[用户改了再批] {'; '.join(pairs[:3])}"
                              + (f" || {reason}" if reason else ""))
    except Exception as e:
        eff_reason = reason
        logger.warning(f"[decision_wire] 折 edits 对照失败(proposal_id={proposal_id},"
                       f"改批信号丢了这条): {e}")
    # 段3:口味命中率对账(押过的注开奖;没押过=不计入,诚实)
    hit = None
    try:
        tstore = getattr(app.state, "taste_predictions", None)
        if tstore is not None:
            hit = tstore.resolve(proposal_id, decision)
    except Exception as e:
        logger.warning(f"[decision_wire] 口味对账失败(proposal_id={proposal_id},"
                       f"这次开奖丢了): {e}")
    # 段3a:decision_log 回看流水 —— **必须在静音钩子之前**:静音分桶命中率靠
    # outcomes⨝decision_log(按 proposal_id)算,本条流水晚写一步,授权门就永远差一次
    # 看不见刚开的这一奖(对抗验收缺陷②,off-by-one)。域用 eff_domain(卡 payload 权威)。
    try:
        log = getattr(app.state, "decision_log", None)
        if log is not None:
            log.record(decision=decision, summary=ctx, proposal_id=proposal_id,
                       reason=eff_reason, kind=kind, domain=eff_domain, role=role or "")
    except Exception as e:
        logger.warning(f"[decision_wire] decision_log 记录失败(proposal_id={proposal_id}): {e}")
    # 段3b:挣来的静音(docs/49②/50 决定1)—— 命中率从仪表变控制器,单一接缝就在开奖处:
    # 押错且该桶已授权 → 自动吊销 + 出卡告知;押中 → 过六道统计门(Wilson 95% 下界≥0.90 +
    # n≥35 + 评估水位 + 批/拒分向达标等,见 karvy/silence.py 头注)才提授权卡。
    try:
        if hit is not None:
            from karvyloop.karvy.silence import on_outcome
            on_outcome(app, proposal_id=proposal_id, kind=kind, domain=eff_domain, hit=hit)
    except Exception as e:
        logger.warning(f"[decision_wire] 静音控制器钩子失败(proposal_id={proposal_id},"
                       f"不阻断决策流): {e}")
    # 段4:样本入结晶缓冲 + 调度结晶(楔子的进料口 —— 丢了必须可见)
    try:
        observe_decision(app, DecisionSample(
            decision=decision, context=(ctx or proposal_id),
            reason=eff_reason, scope="personal",
            domain=domain or "", role=role or "", ts=_time.time()))
        schedule_decision_crystallize(app)
    except Exception as e:
        logger.warning(f"[decision_wire] 决策样本入缓冲/结晶调度失败(proposal_id={proposal_id},"
                       f"**这次拍板的偏好学习信号丢了**): {e}")
    # 段5:stats 复利指标
    try:
        stats = getattr(app.state, "decision_stats", None)
        if stats is not None:
            stats.record(decision)
    except Exception as e:
        logger.warning(f"[decision_wire] decision_stats 记录失败(proposal_id={proposal_id}): {e}")
    # (decision_log 回看流水已提前到段3a —— 静音分桶 join 需要它先落账)


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
    # 回执:存"这条标准来自你哪几次拍板"的人话凭据(决策+理由摘要),不只时间戳 ——
    # 让预对齐/决策卡能摆出"来自你的拍板:…",答用户视角 Q2(凭什么信你)。
    evidence = [{"ts": getattr(s, "ts", 0.0), "decision": s.decision,
                 "gist": (getattr(s, "reason", "") or getattr(s, "context", "") or "").strip()[:60]}
                for s in batch]
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
    rev = getattr(app.state, "decision_revocations", None)   # 撤回墓碑(可能没接=不抑制)
    written = reinforced = 0
    high_value: list = []
    for c in candidates:
        key = _norm(c.get("content", ""))
        if not key:
            continue
        # 你撤回过且仍在冷却窗口内 → 别自动学回来(撤回的牙;连复现计数也清,别偷偷攒)。
        if rev is not None and rev.is_suppressed(key, now=now):
            recur.pop(key, None)
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


def assemble_governance(app: Any, *, intent: str = "", domain: str = "", role: str = "",
                        base: str = "") -> str:
    """**统一上下文装配**:把"你的决策标准(prealign)"+ 个人知识召回 前置到 base governance。

    每条 drive 路径共用这一个接缝,保证**你的标准到处生效**(不只 l0 聊天),别再各拼各的漂移
    (Hardy:别只一两个功能用上 context engineering)。顺序(上→下):prealign → 知识召回 → base。
    intent = 本次意图/任务文本 → 按相关性召回(规模大也先摆相关、不静默漏)。
    """
    gov = base or ""
    mem = getattr(getattr(app, "state", None), "memory", None)
    if mem is None:
        return gov
    try:
        block = mem.recall_block(intent, scope="personal", limit=8, domain=(domain or ""))
        if block:
            gov = (block + "\n\n" + gov).strip()
    except Exception:
        pass
    # 角色经验(docs/54 模块1 Top2):角色在**同(域,角色)**积累的可召回经验(方法/偏好/踩坑)。
    # (域,角色)隔离 —— A 域角色经验绝不漏到 B(recall_block 只按域过滤,role-experience 双过滤)。
    # 仅域+角色都在时注入(通用层/私聊不掺角色私有经验),放在决策标准与知识召回之间。
    try:
        from karvyloop.roles.experience import collect_role_experiences
        exp = collect_role_experiences(mem, domain=(domain or ""), role=(role or ""), query=intent)
        if exp:
            gov = (exp + "\n\n" + gov).strip()
    except Exception:
        pass
    try:
        pa = prealign_governance(app, mem, query=intent, domain=domain, role=role)
        if pa:
            gov = (pa + "\n\n" + gov).strip()
    except Exception:
        pass
    return gov


def prealign_governance(app: Any, mem: Any, *, query: str = "", domain: str = "", role: str = "") -> str:
    """提案/drive 前:召回**与本次相关**的决策偏好 → 预对齐块(注入 governance)。空 → ""。

    query=本次意图/提案文本 → 按相关性召回(规模大也先摆相关的、不静默漏);空=回退强度排序。
    """
    if mem is None:
        return ""
    try:
        beliefs: list = []
        idx = mem.index
        for scope in ("personal", "domain"):
            for b in idx.all(scope):
                beliefs.append(b)
        return prealign_block(beliefs, query=query, domain=domain, role=role)
    except Exception:
        return ""


__all__ = [
    "DECISION_BATCH", "observe_decision", "record_decision_signals", "maybe_crystallize_decisions",
    "crystallize_candidates", "schedule_decision_crystallize", "prealign_governance",
    "assemble_governance",
    "proposal_for_confirm_decision",
]
