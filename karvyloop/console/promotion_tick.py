"""console/promotion_tick.py — 兵法回流的 daily 慢侧 tick(docs/78 §3.1/§3.5)。

**节奏纪律**(抄 knowledge_tick 的成熟三件,均有测试锁):
- **(域,角色)池指纹 watermark**:候选池没变 → 零 LLM 直接跳过;
- **冷却**:同一批候选(稳定 proposal_id)建议过,冷却窗(7 天)内不重提——REJECT 的语义
  就是"这轮不升"(源条不动,域内照用;拒的只是升层动作);
- **单轮封顶**:一次 tick 最多出 1 张攒批卡(卡内 ≤8 条)。

**H2A = 攒批出卡,ACCEPT 才升**(Hardy 2026-07-13 表单拍板,docs/78 §3.5 选 B):
泄露不可逆、打扰可逆;镜像层不只对外还进跨域召回,错条扩散面大;频率天然低(四道预筛+
宁缺勿滥),一周几条的量。卡上带**改写前后对照**(源域名只在本机管理面出现)——签字
的同时顺手校准改写质量。ACCEPT 兑现:denylist 复检(纵深防御)→ 写镜像条(写咽喉,
mesh 同步照走)→ 源条打 promoted_to。跑评分离:绝不进 drive 热路径。
状态落 `~/.karvyloop/promotion_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

SUGGEST_COOLDOWN_S = 7 * 86400
KIND_PROMOTE_EXPERIENCE = "promote_experience"   # 复用现有卡机制(Proposal+registry+handler 注入)


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "promotion_tick.json"


def _load_state(path: Optional[Path] = None) -> dict:
    p = path or _state_path()
    if not p.exists():
        return {"pool_hash": {}, "suggested": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"pool_hash": {}, "suggested": {}}
        d.setdefault("pool_hash", {})
        d.setdefault("suggested", {})
        return d
    except Exception:
        return {"pool_hash": {}, "suggested": {}}


def _save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[promotion_tick] 状态落盘失败(下轮重算): {e}")


def _pool_hash(cands: list) -> str:
    h = hashlib.sha1()
    for c in sorted((getattr(b, "content", "") or "") for b in cands):
        h.update(c.encode("utf-8")); h.update(b"\0")
    return h.hexdigest()


def _domain_display(app: Any, domain_id: str) -> str:
    """域显示名(denylist 用;拿不到就用 id 本身)。"""
    try:
        reg = getattr(app.state, "domain_registry", None)
        d = reg.get(domain_id) if reg else None
        return str(getattr(d, "name", "") or domain_id)
    except Exception:
        return domain_id


def _build_card(role: str, domain_id: str, items: list[dict], befores: dict, now: float):
    """攒批卡:改写前后对照(源域名只在本机管理面出现;对外通道永远看不到卡)。"""
    from karvyloop.karvy.atoms import Proposal
    stable = "\n".join(sorted(it["origin_key"] for it in items))
    pid = "promote_exp-" + hashlib.sha1((role + "\0" + stable).encode("utf-8")).hexdigest()[:8]
    lines = []
    for it in items:
        before = (befores.get(it["origin_key"], "") or "")[:120]
        lines.append(f"原(域内):{before}\n升(通用):{it['content']}"
                     + (f"\n  ↳ 为什么泛化:{it['why']}" if it.get("why") else ""))
    basis = (f"「{role}」在域「{domain_id}」的这些经验通过了泛化判定与脱敏改写。"
             f"ACCEPT = 升为该角色的通用兵法(跨域可用;将来对外可见面也只有这一层);"
             f"REJECT = 这轮不升,域内照用(7 天内不再重提)。升层后删域不再自动撤——"
             f"要撤在记忆面板单条失效。\n\n" + "\n\n".join(lines))
    p = Proposal(
        summary=f"📜 「{role}」有 {len(items)} 条域内经验可升为通用兵法,升吗?",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.5, evidence_refs=(), habit_id=0, model_ref="", ts=now,
        kind=KIND_PROMOTE_EXPERIENCE,
        payload={"role": role, "origin_domain": domain_id, "items": items},
        basis=basis,
    )
    # 稳定 proposal_id(冷却键):同一批候选同一个 id(Proposal 若自带 id 生成则覆盖)
    try:
        p.proposal_id = pid
    except Exception:
        pass
    return p


def _promote_experience_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """ACCEPT 兑现:denylist 复检(纵深防御,建卡后世界可能变)→ 写镜像条 → 源条标 promoted。"""
    def handler(proposal) -> Tuple[bool, str]:
        from karvyloop.roles.promotion import denylist_terms, make_promoted_belief, scrub_ok
        mem = getattr(app.state, "memory", None)
        if mem is None:
            return False, "未接 memory —— 无法升层"
        payload = getattr(proposal, "payload", None) or {}
        role = str(payload.get("role") or "")
        dom = str(payload.get("origin_domain") or "")
        items = list(payload.get("items") or [])
        if not (role and items):
            return False, "卡上没有可升条目(payload 空)"
        deny = denylist_terms(dom, _domain_display(app, dom))
        # 源条索引:origin_key → belief(打 promoted_to 用;找不到也升,指针照留)
        from karvyloop.roles.promotion import origin_key_for
        src_by_key = {}
        try:
            for scope in ("personal", "domain"):
                for b in mem.index.all(scope):
                    src_by_key[origin_key_for(b.content)] = b
        except Exception:
            pass
        now = time.time()
        written, dirty = 0, 0
        for it in items:
            content = str(it.get("content") or "").strip()
            okey = str(it.get("origin_key") or "")
            if not content:
                continue
            if not scrub_ok(content, deny):
                dirty += 1
                continue   # 二道防线兜住:脏条丢弃,不循环重试(诚实计数)
            nb = make_promoted_belief(content, str(it.get("kind") or "method"),
                                      role=role, origin_domain=dom, origin_key=okey, now=now)
            try:
                if mem.write(nb):
                    written += 1
                    src = src_by_key.get(okey)
                    if src is not None:
                        mem.mark_promoted(src.content, okey)
            except Exception:
                logger.warning("[promotion] 升层写入失败(单条跳过)", exc_info=True)
        if written == 0:
            return False, ("没有条目升成" + (f"(denylist 拦下 {dirty} 条脏改写)" if dirty else ""))
        msg = f"已把 {written} 条经验升为「{role}」的通用兵法(跨域立即可用)"
        if dirty:
            msg += f";denylist 拦下 {dirty} 条含域实体的改写(未升)"
        return True, msg + "。"
    return handler


async def maybe_promotion_tick(app: Any, *, now: Optional[float] = None,
                               state_path: Optional[Path] = None) -> int:
    """daily 慢侧入口:圈候选 →(池变了才)LLM 判+改写 → denylist → 攒批出一张卡。

    返回本轮升的卡数(0/1)。任何失败只 warning,绝不打断 daily 循环里的邻居。
    """
    if now is None:
        now = time.time()
    mem = getattr(app.state, "memory", None)
    preg = getattr(app.state, "proposal_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or preg is None or gw is None:
        return 0
    from karvyloop.roles.promotion import (
        denylist_terms, judge_and_rewrite, origin_key_for, promotion_candidates, scrub_ok)

    try:
        beliefs = []
        for scope in ("personal", "domain"):
            beliefs.extend(mem.index.all(scope))
    except Exception:
        return 0
    cands = promotion_candidates(beliefs, now=now)
    if not cands:
        return 0

    # 按 (域,角色) 分池;一轮只处理一个"有新东西"的池(单轮封顶 1 张卡,别糊脸)
    pools: dict[tuple, list] = {}
    for b in cands:
        ap = b.provenance.get("applies") or {}
        pools.setdefault((ap.get("domain", ""), ap.get("role", "")), []).append(b)

    state = _load_state(state_path)
    for (dom, role), pool in sorted(pools.items()):
        key = f"{dom}\0{role}"
        ph = _pool_hash(pool)
        if state["pool_hash"].get(key) == ph:
            continue   # 池没变 → 零 LLM(watermark)
        dom_name = _domain_display(app, dom)
        try:
            items = await judge_and_rewrite(pool, gateway=gw, model_ref=rk.get("model_ref", ""),
                                            domain_id=dom, domain_name=dom_name)
        except Exception as e:
            logger.warning(f"[promotion_tick] 判泛化失败(池 {dom}/{role} 下轮重试): {e}")
            continue
        state["pool_hash"][key] = ph   # 这版池看过了(判空也算看过;池变了才再看)
        deny = denylist_terms(dom, dom_name)
        clean = [it for it in items if scrub_ok(it["content"], deny)]
        if len(clean) < len(items):
            logger.info(f"[promotion_tick] denylist 拦下 {len(items) - len(clean)} 条含域实体的改写")
        if not clean:
            continue
        # 改写前后对照(卡上校准质量;源域名/原文只在本机管理面出现)
        befores = {origin_key_for(b.content): b.content for b in pool}
        card = _build_card(role, dom, clean, befores, now)
        prev = (state.get("suggested") or {}).get(card.proposal_id)
        if prev is not None and now - float(prev) < SUGGEST_COOLDOWN_S:
            _save_state(state, state_path)
            return 0   # 冷却窗内同批建议过(REJECT 的别次日重来)
        handlers = getattr(app.state, "proposal_handlers", None)
        if isinstance(handlers, dict):
            handlers.setdefault(KIND_PROMOTE_EXPERIENCE, _promote_experience_handler(app))
        preg.register(card)
        try:
            from karvyloop.console.proposals import broadcast_proposal
            await broadcast_proposal(app, card)
        except Exception:
            pass   # 推送失败不阻断(卡已在 registry)
        state.setdefault("suggested", {})[card.proposal_id] = now
        _save_state(state, state_path)
        return 1
    _save_state(state, state_path)
    return 0


__all__ = ["KIND_PROMOTE_EXPERIENCE", "maybe_promotion_tick", "_promote_experience_handler"]
