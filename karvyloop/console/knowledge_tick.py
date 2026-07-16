"""console/knowledge_tick.py — 知识库**自动**整理(daily 慢侧 tick;Bug2 的后台版,原手动按钮保留)。

**为什么**:近重复知识的和解此前只有手动「🧹 整理相似知识」按钮 —— 你不点,库就慢慢长毛。
跑评分离原则(Trace 记忆 J):这类"不急、静心"的养护活该挂**每日慢侧**,绝不进摄入/对话热路径。

**怎么不打扰、不烧钱**(设计要点,均有测试锁):
- **库指纹 watermark**:知识库内容没变 → 直接跳过,**零 LLM 调用**。你 REJECT 过的建议也因此不会
  次日重来(库没变);ACCEPT 合并后库变了 → 下轮才会再看。
- **建议冷却**:同一簇(稳定 proposal_id)建议过,在冷却窗(默认 7 天)内不重复升卡 —— 防库变了
  但同簇重浮时的唠叨。
- **H2A**:发现近重复只**升 merge_knowledge 建议卡**,ACCEPT 才真合并(apply_belief_merge:
  先写合并条再删旧,中途失败不丢数据)。绝不自动合 —— 知识库是护城河资产。
状态落 `~/.karvyloop/consolidate_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIN_BELIEFS = 8            # 库太小不值得整理(也别为 3 条知识烧一次 LLM)
SUGGEST_COOLDOWN_S = 7 * 86400
MAX_CARDS_PER_TICK = 3     # 单轮最多升 3 张卡(别一天糊你一脸建议)

# ---- 时效侧(冲突消解接线③):一年没用/疑似过时 → 归档建议卡(零 LLM,纯时间信号)----
STALE_AFTER_S = 365 * 86400   # "一年没用"阈值:max(最近召回, 沉淀时刻) 距今超一年
MAX_STALE_PER_CARD = 8        # 一张卡最多列几条(别一口气糊一屏)
KIND_ARCHIVE_STALE = "archive_stale_knowledge"   # 复用现有卡机制(Proposal+registry+handler 注入)


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "consolidate_tick.json"


def _load_state(path: Optional[Path] = None) -> dict:
    p = path or _state_path()
    if not p.exists():
        return {"lib_hash": "", "suggested": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"lib_hash": "", "suggested": {}}
        d.setdefault("lib_hash", "")
        d.setdefault("suggested", {})
        return d
    except Exception:
        return {"lib_hash": "", "suggested": {}}


def _save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[knowledge_tick] 状态落盘失败(下轮重算): {e}")


def _lib_hash(beliefs: list) -> str:
    h = hashlib.sha1()
    for c in sorted((getattr(b, "content", "") or "") for b in beliefs):
        h.update(c.encode("utf-8")); h.update(b"\0")
    return h.hexdigest()


# ---- 过时归档建议(时效与使用信号的 daily 消费端) ----

def _stale_candidates(beliefs: list, mem: Any, now: float) -> list:
    """一年没用的候选:仍有效(invalid_at 空)、非 pin、有可判时间(没时间戳的不冤枉)。零 LLM。"""
    from karvyloop.cognition.memory import belief_recency_ts
    out = []
    for b in beliefs:
        if getattr(b, "invalid_at", None) is not None:
            continue
        try:
            if mem.index.is_pinned(b):
                continue   # pin 的是"永远要记得"—— 不建议归档
        except Exception:
            pass
        try:
            recency = float(belief_recency_ts(b) or 0.0)
        except Exception:
            recency = 0.0   # duck-typed 测试桩/坏数据:没时间字段 → 当无信号
        last_used = max(float(getattr(b, "last_recalled_ts", 0.0) or 0.0), recency)
        if last_used <= 0.0:
            continue   # 无任何时间信号(老数据/测试桩)→ 不冤枉,跳过
        if now - last_used >= STALE_AFTER_S:
            out.append(b)
    return out


def _stale_card(cands: list, now: float):
    """把一批过时候选升成一张 H2A 卡(Proposal;稳定 id=成员内容哈希,幂等+冷却可用)。"""
    from karvyloop import i18n
    from karvyloop.karvy.atoms import Proposal
    members = [getattr(b, "content", "") or "" for b in cands]
    titles = [((getattr(b, "provenance", {}) or {}).get("title", "") or m[:18])
              for b, m in zip(cands, members)]
    shown = "、".join(titles[:4]) + ("…" if len(titles) > 4 else "")
    basis = i18n.t("proposal.archive_stale.basis", n=len(members), shown=shown)
    stable = "\n".join(sorted(members))
    pid = "archive_stale-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=i18n.t("proposal.archive_stale.summary", n=len(members)),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.4,
        evidence_refs=(), habit_id=0, model_ref="", ts=now,
        kind=KIND_ARCHIVE_STALE,
        payload={"member_contents": members, "member_titles": titles},
        proposal_id=pid,
        basis=basis,
    )


def _archive_stale_handler(app: Any):
    """ACCEPT 兑现:逐条打 invalid_at(MemoryManager.invalidate,失效不删可审计)。
    成员已被删/已失效 → 跳过并如实回执。签名对齐 proposal_handlers:proposal → (ok, detail)。"""
    def handler(proposal):
        mem = getattr(app.state, "memory", None)
        if mem is None or not hasattr(mem, "invalidate"):
            return False, "未接 memory —— 无法归档"
        payload = getattr(proposal, "payload", None) or {}
        members = [str(c) for c in (payload.get("member_contents") or []) if str(c).strip()]
        done, missing = 0, 0
        now = time.time()
        for c in members:
            b = mem.index.get(c)
            if b is None or getattr(b, "invalid_at", None) is not None:
                missing += 1
                continue
            mem.invalidate(b, reason="stale-archived (H2A ACCEPT: 一年未用)", now=now)
            done += 1
        if done == 0:
            return False, "没有可归档的条目(可能已被删除或已失效)"
        extra = f"(另 {missing} 条已不在/已失效,跳过)" if missing else ""
        return True, f"已把 {done} 条过时知识打失效标记归档 —— 不再进召回,但仍留库可审计/可翻案{extra}。"
    return handler


async def knowledge_consolidate_tick(app: Any, *, state_path: Optional[Path] = None,
                                     now: Optional[float] = None) -> dict:
    """每日慢侧知识整理一轮。返回 {ran, suggested, reason}(ran=False 时 reason 说明为何跳过)。"""
    if now is None:
        now = time.time()
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    preg = getattr(app.state, "proposal_registry", None)
    if mem is None or gw is None or preg is None:
        return {"ran": False, "suggested": 0, "stale_suggested": 0,
                "reason": "memory/gateway/proposal_registry 未接"}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    beliefs = [b for b in mem.index.all("personal") if not is_decision_pref(b)]
    if len(beliefs) < MIN_BELIEFS:
        return {"ran": False, "suggested": 0, "stale_suggested": 0,
                "reason": f"知识 < {MIN_BELIEFS} 条,不值得整理"}
    state = _load_state(state_path)

    # ---- ①' 时效侧:使用信号落盘 + "一年没用,归档?"建议卡(零 LLM,在 watermark 前——
    #      过时是时间的函数,库没变也会变老)。冷却复用同一 suggested 表,不唠叨。 ----
    if hasattr(mem, "flush_usage"):
        try:
            mem.flush_usage()   # recall_block 攒的 last_recalled_ts/recall_count 批量落盘
        except Exception as e:
            logger.warning(f"[knowledge_tick] 使用信号落盘失败(下轮再刷): {e}")
    stale_suggested = 0
    stale = _stale_candidates(beliefs, mem, now)[:MAX_STALE_PER_CARD]
    if stale:
        try:
            card = _stale_card(stale, now)
            prev = (state.get("suggested") or {}).get(card.proposal_id)
            if prev is None or now - float(prev) >= SUGGEST_COOLDOWN_S:
                # ACCEPT 兑现 handler 运行时注入(console/entry 的 handler 表是普通 dict;
                # 卡只由本 tick 升,升卡前注入 = ACCEPT 时必有 handler;万一没有,registry
                # 的"无 handler 卡保留待决"防御兜底,不吞卡)。
                handlers = getattr(app.state, "proposal_handlers", None)
                if isinstance(handlers, dict):
                    handlers.setdefault(KIND_ARCHIVE_STALE, _archive_stale_handler(app))
                preg.register(card)
                try:
                    from karvyloop.console.proposals import broadcast_proposal
                    await broadcast_proposal(app, card)
                except Exception:
                    pass   # 推送失败不阻断(卡已在 registry)
                state.setdefault("suggested", {})[card.proposal_id] = now
                stale_suggested = 1
        except Exception as e:
            logger.warning(f"[knowledge_tick] 过时归档卡升卡失败(跳过): {e}")

    lh = _lib_hash(beliefs)
    if lh == state.get("lib_hash"):
        _save_state(state, state_path)   # 时效侧的冷却记录也要落
        return {"ran": False, "suggested": 0, "stale_suggested": stale_suggested,
                "reason": "知识库没变(watermark),零 LLM 跳过"}

    from karvyloop.cognition.consolidate import suggest_consolidation
    from karvyloop.llm.token_ledger import token_source
    with token_source("consolidate_auto"):
        clusters = await suggest_consolidation(beliefs, gateway=gw, model_ref=rk.get("model_ref", ""))
    state["lib_hash"] = lh   # 无论出几簇,这一版库看过了(失败抛出则不落 hash,下轮重试)

    suggested = 0
    if clusters:
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_merge_knowledge
        for c in clusters[:MAX_CARDS_PER_TICK]:
            try:
                card = proposal_for_merge_knowledge(
                    member_contents=c.get("member_contents", []),
                    member_titles=c.get("member_titles", []),
                    merged_title=c.get("merged_title", ""),
                    merged_content=c.get("merged_content", ""),
                    reason=c.get("reason", ""), ts=now)
            except Exception as e:
                logger.warning(f"[knowledge_tick] 建卡失败(跳过该簇): {e}")
                continue
            prev = (state.get("suggested") or {}).get(card.proposal_id)
            if prev is not None and now - float(prev) < SUGGEST_COOLDOWN_S:
                continue   # 冷却窗内建议过 → 不唠叨(从没建议过的不吃冷却)
            preg.register(card)
            try:
                await broadcast_proposal(app, card)
            except Exception:
                pass   # 推送失败不阻断(卡已在 registry,快照会带出)
            state.setdefault("suggested", {})[card.proposal_id] = now
            suggested += 1
    _save_state(state, state_path)
    return {"ran": True, "suggested": suggested, "stale_suggested": stale_suggested, "reason": ""}


__all__ = ["knowledge_consolidate_tick", "MIN_BELIEFS", "SUGGEST_COOLDOWN_S", "MAX_CARDS_PER_TICK",
           "STALE_AFTER_S", "MAX_STALE_PER_CARD", "KIND_ARCHIVE_STALE"]
