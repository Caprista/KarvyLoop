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
        return {"ran": False, "suggested": 0, "reason": "memory/gateway/proposal_registry 未接"}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    beliefs = [b for b in mem.index.all("personal") if not is_decision_pref(b)]
    if len(beliefs) < MIN_BELIEFS:
        return {"ran": False, "suggested": 0, "reason": f"知识 < {MIN_BELIEFS} 条,不值得整理"}
    state = _load_state(state_path)
    lh = _lib_hash(beliefs)
    if lh == state.get("lib_hash"):
        return {"ran": False, "suggested": 0, "reason": "知识库没变(watermark),零 LLM 跳过"}

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
    return {"ran": True, "suggested": suggested, "reason": ""}


__all__ = ["knowledge_consolidate_tick", "MIN_BELIEFS", "SUGGEST_COOLDOWN_S", "MAX_CARDS_PER_TICK"]
