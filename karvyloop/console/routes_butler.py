"""routes_butler — /api/butler/*(文件管家第一课:扫描 → 方案预览卡)。

薄壳:扫描/方案/执行全在 karvyloop/karvy/butler_lesson.py(确定性,零 LLM)。
本层只负责:白名单核对(fs_grants,引荐 ACCEPT 落的台账)→ 扫描出方案 → 出 H2A 卡
(broadcast_proposal;register 咽喉在 broadcast 里)→ handler 运行时注入(knowledge_tick 先例)。

诚实红线:空桌面/空下载 → empty:true(前端如实说"没什么可整理的"),绝不硬凑方案;
没有白名单授权(管家没入住/授权被撤)→ ok:false + no_grants,不越权偷扫。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.post("/butler/first_lesson")
async def api_butler_first_lesson(request: Request) -> dict[str, Any]:
    """第一课:白名单内只读扫桌面/下载 → 整理方案预览卡(你拍板才动手)。

    分桶模式由人格采集器 filing 一题确定性决定(by_type/by_time,无答案默认 by_type)——
    采集的答案在这里第一次**改变系统行为**。同 kind 旧卡先撤(方案以最新扫描为准)。
    """
    from karvyloop.i18n import get_locale
    from karvyloop.karvy.butler_lesson import (
        BUTLER_ROLE_ID, FIRST_LESSON_DIR_NAMES, KIND_BUTLER_PLAN,
        build_first_lesson, filing_mode_from_memory, make_butler_plan_handler,
        proposal_for_butler_plan,
    )
    st = request.app.state
    fs = getattr(st, "fs_grants", None)
    preg = getattr(st, "proposal_registry", None)
    if fs is None or preg is None:
        return {"ok": False, "reason": "no_grants"}
    home = getattr(st, "residents_home", None) or Path.home()
    dirs = [Path(home) / name for name in FIRST_LESSON_DIR_NAMES
            if fs.allows(str(Path(home) / name), "read", role=BUTLER_ROLE_ID)]
    if not dirs:
        return {"ok": False, "reason": "no_grants"}   # 管家没入住/授权被撤 → 不越权偷扫
    mem = getattr(st, "memory", None)
    mode = filing_mode_from_memory(mem)
    mode_from_intake = mode == "by_time" or _has_filing_answer(mem)
    plan = build_first_lesson(dirs, mode=mode, locale=get_locale())
    if plan["empty"]:
        return {"ok": True, "empty": True}
    if not plan["moves"] and not plan["duplicates"] and not plan["hogs"]:
        return {"ok": True, "empty": True, "already_tidy": True}
    # handler 运行时注入(knowledge_tick / residents 同款先例;缺了 registry 的
    # "无 handler 卡保留待决"防御兜底,不吞卡)
    handlers = getattr(st, "proposal_handlers", None)
    if isinstance(handlers, dict):
        handlers.setdefault(KIND_BUTLER_PLAN, make_butler_plan_handler(request.app))
    card = proposal_for_butler_plan(plan, ts=time.time(), mode_from_intake=mode_from_intake)
    # 同 kind 旧卡先撤(重扫后旧方案已过时;同 id 幂等覆盖,不同 id 不留双卡打架)
    try:
        for p in preg.pending():
            if getattr(p, "kind", "") == KIND_BUTLER_PLAN \
                    and getattr(p, "proposal_id", "") != card.proposal_id:
                preg.remove(p.proposal_id)
    except Exception:
        pass
    from karvyloop.console.proposals import broadcast_proposal
    await broadcast_proposal(request.app, card)   # register 咽喉在 broadcast 里
    return {"ok": True, "empty": False, "proposal_id": card.proposal_id,
            "scanned": plan["scanned"], "moves": len(plan["moves"]),
            "duplicates": len(plan["duplicates"]), "mode": mode}


def _has_filing_answer(mem: Any) -> bool:
    """filing 一题是否真答过(by_type 既是答案也是默认 —— 卡上"按你入门选的"只在真答过时说)。"""
    if mem is None:
        return False
    try:
        for sc in ("personal", "domain"):
            for b in mem.index.all(sc):
                prov = getattr(b, "provenance", None) or {}
                if prov.get("source") == "decision_pref" and prov.get("intake_q") == "filing" \
                        and getattr(b, "invalid_at", None) is None:
                    return True
    except Exception:
        pass
    return False


__all__ = ["router"]
