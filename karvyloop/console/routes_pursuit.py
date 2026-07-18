"""routes_pursuit — /api/pursuit* 端点(docs/88 §4/§9 第一刀件③:最省 create 路径 + 列/详情)。

外环 Pursuit(跨天持久目标)的用户面最小接口:
- POST /api/pursuit —— 显式建一个带 verify_gate 的 Pursuit(第一刀 gate 只 test_pass / file_exists),
  建即升一张**承诺卡**(KIND_PURSUIT_COMMIT):人 ACCEPT=committed(commitment 第一刀简化为
  "人拍了 create 卡",不做 commitment_condition DSL)。此后维护 loop 的 pursuit_tick 自动推进。
- GET /api/pursuits —— 列(轻量摘要)。
- GET /api/pursuit/{id} —— 详情(含派生 task,按 pursuit_id 从任务看板回捞)。

K 边界:K4/K5 —— 本路由**不替用户决策**(commit 走 H2A 承诺卡);verify_gate 求值是确定性的
(招牌硬核),绝不触发 LLM。
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# 第一刀只支持这两类 gate(覆盖绝大多数能上镜目标;都确定性、零 LLM)。
_ALLOWED_GATE_TYPES = ("test_pass", "file_exists")
_ALLOWED_LEVELS = ("atom", "role", "domain")


def _validate_gate(gate: Any) -> tuple[Optional[dict], str, str]:
    """校验并归一化 verify_gate。返回 (clean_gate|None, human_desc, error)。

    第一刀只允许 test_pass(cmd 退出 0)/ file_exists(路径存在)。归一化 = 只保留白名单字段
    (防注入奇怪 key)。error 非空 = 拒绝(400)。
    """
    from karvyloop import i18n
    if not isinstance(gate, dict):
        return None, "", i18n.t("pursuit.err.gate_not_dict")
    t = str(gate.get("type") or "").strip()
    if t not in _ALLOWED_GATE_TYPES:
        return None, "", i18n.t("pursuit.err.gate_type", allowed="/".join(_ALLOWED_GATE_TYPES))
    if t == "test_pass":
        cmd = str(gate.get("cmd") or "").strip()
        if not cmd:
            return None, "", i18n.t("pursuit.err.gate_cmd")
        # 健全性校验(docs/88 真伤3):用**和 gate 求值同一口径**的平台感知拆分预演一次 —— 拆碎/空
        # argv → 400 带人话,别让它进库后每 tick 静默 FileNotFoundError 吞成"永红"(与真失败不可分)。
        from karvyloop.cognition.pursuit import split_test_pass_cmd
        try:
            argv = split_test_pass_cmd(cmd)
        except ValueError:
            argv = []
        if not argv or not str(argv[0]).strip():   # 空 argv / 空程序名(如裸引号)→ 拒
            return None, "", i18n.t("pursuit.err.gate_cmd_unsplittable", cmd=cmd)
        clean: dict = {"type": "test_pass", "cmd": cmd}
        cwd = str(gate.get("cwd") or "").strip()
        if cwd:
            clean["cwd"] = cwd
        try:
            ts = float(gate.get("timeout_s"))
            if ts > 0:
                clean["timeout_s"] = ts
        except (TypeError, ValueError):
            pass
        return clean, i18n.t("pursuit.gate_desc.test_pass", cmd=cmd), ""
    # file_exists
    path = str(gate.get("path") or "").strip()
    if not path:
        return None, "", i18n.t("pursuit.err.gate_path")
    return {"type": "file_exists", "path": path}, i18n.t("pursuit.gate_desc.file_exists", path=path), ""


class PursuitCreateRequest(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    verify_gate: dict
    title: str = Field(default="", max_length=200)
    level: str = Field(default="atom", max_length=16)
    owner: str = Field(default="karvy", max_length=64)
    domain_id: str = Field(default="l0", max_length=64)
    revision_triggers: List[str] = Field(default_factory=list)


@router.post("/pursuit")
async def api_pursuit_create(req: PursuitCreateRequest, request: Request) -> dict[str, Any]:
    """建一个跨天目标(Pursuit)+ 升承诺卡。ACCEPT 承诺卡 → committed → 机器自跑几天。"""
    from karvyloop import i18n
    app = request.app
    store = getattr(app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.no_store")}
    level = req.level if req.level in _ALLOWED_LEVELS else "atom"
    gate, gate_desc, err = _validate_gate(req.verify_gate)
    if err:
        return {"ok": False, "reason": err}
    triggers = [str(t).strip() for t in (req.revision_triggers or []) if str(t).strip()][:8]

    from karvyloop.cognition.pursuit_store import PursuitRecord, new_pursuit_id
    from karvyloop.schemas import Pursuit
    pid = new_pursuit_id(level)
    try:
        pursuit = Pursuit(
            id=pid, level=level, statement=req.statement.strip(),
            commitment_condition="",   # 第一刀:人 ACCEPT 承诺卡 = committed(不做 DSL)
            revision_triggers=triggers, verify_gate=gate, status="active")
    except Exception as e:
        return {"ok": False, "reason": i18n.t("pursuit.err.bad_pursuit", error=str(e))}
    rec = PursuitRecord(pursuit, title=req.title.strip(), owner=req.owner.strip() or "karvy",
                        domain_id=req.domain_id.strip() or "l0")
    store.put(rec)

    # 承诺卡(H2A:承诺跨天目标是决策,必人拍)。进 HIGH_RISK_KINDS,绝不被静音自动兑现。
    commit_pid = ""
    try:
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_pursuit_commit
        card = proposal_for_pursuit_commit(
            pursuit_id=pid, statement=pursuit.statement, gate_desc=gate_desc,
            level=level, revision_triggers=triggers, domain_id=rec.domain_id, ts=time.time())
        commit_pid = card.proposal_id
        await broadcast_proposal(app, card)
    except Exception as e:
        logger.warning(f"[pursuit] 升承诺卡失败(Pursuit 已建,可稍后手动承诺): {e}")
    return {"ok": True, "pursuit_id": pid, "status": pursuit.status,
            "commit_proposal_id": commit_pid, "gate_desc": gate_desc}


@router.get("/pursuits")
def api_pursuits_list(request: Request) -> dict[str, Any]:
    """列出所有 Pursuit(轻量摘要;K4 只读)。"""
    store = getattr(request.app.state, "pursuit_store", None)
    if store is None:
        return {"pursuits": [], "active_count": 0}
    recs = sorted(store.all(), key=lambda r: r.updated_ts, reverse=True)
    return {"pursuits": [r.summary() for r in recs], "active_count": store.active_count()}


@router.get("/pursuit/{pursuit_id}")
def api_pursuit_detail(pursuit_id: str, request: Request) -> dict[str, Any]:
    """一个 Pursuit 详情 + 它派生的 task(按 pursuit_id 从任务看板回捞;K4 只读)。"""
    app = request.app
    store = getattr(app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": "pursuit store not wired"}
    rec = store.get(pursuit_id)
    if rec is None:
        return {"ok": False, "reason": "not found"}
    detail = rec.summary()
    # 派生 task(复用任务账,不另造):优先按 last_task_ids 精确取;再按 pursuit_id 全量过滤兜底。
    tasks: list = []
    task_reg = getattr(app.state, "task_registry", None)
    if task_reg is not None:
        seen: set = set()
        for tid in rec.last_task_ids:
            d = task_reg.get(tid)
            if d is not None and d.get("id") not in seen:
                seen.add(d.get("id"))
                tasks.append(d)
        try:
            for d in task_reg.list():
                if d.get("pursuit_id") == pursuit_id and d.get("id") not in seen:
                    seen.add(d.get("id"))
                    tasks.append(d)
        except Exception:
            pass
    detail["tasks"] = tasks
    return {"ok": True, "pursuit": detail}


__all__ = ["router"]
