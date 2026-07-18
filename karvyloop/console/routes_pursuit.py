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
    # 真伤4:含 `{...}` 占位符的 path 是坏门(第一刀不做路径模板;run 期 file_exists 按字面判,永不满足
    # → 白等到地板才升卡)。宁空勿毒:创建期就拒(400),不放行进库。LLM 判型爱吐 `{date}` 模板路径。
    from karvyloop.cognition.pursuit import path_has_placeholder
    if path_has_placeholder(path):
        return None, "", i18n.t("pursuit.err.gate_path_placeholder", path=path)
    return {"type": "file_exists", "path": path}, i18n.t("pursuit.gate_desc.file_exists", path=path), ""


async def create_pursuit_with_commit_card(
    app: Any,
    *,
    statement: str,
    verify_gate: dict,
    title: str = "",
    level: str = "atom",
    owner: str = "karvy",
    domain_id: str = "l0",
    revision_triggers: Optional[List[str]] = None,
    origin: str = "",
) -> dict[str, Any]:
    """第一刀唯一的"创建 Pursuit + 升承诺卡"路径(POST /api/pursuit 与小卡自动判型共用)。

    docs/88 第二刀抽 helper:入口可以有多个(显式 API / 聊天判型),create 路径只有这一条 ——
    gate 校验、record 落库、承诺卡(H2A)全在这里,绝不另造第二套。返回 dict 形状 =
    POST /api/pursuit 的历史响应形状(前端在消费,加性不变)。

    origin:创建来源标,进承诺卡 payload(判型入口传 karvy_triage → REJECT 时按它清记录;
    显式 API 建的默认空 → REJECT 保留记录,"可稍后手动承诺"第一刀语义不变)。
    """
    from karvyloop import i18n
    store = getattr(app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.no_store")}
    level = level if level in _ALLOWED_LEVELS else "atom"
    gate, gate_desc, err = _validate_gate(verify_gate)
    if err:
        return {"ok": False, "reason": err}
    triggers = [str(t).strip() for t in (revision_triggers or []) if str(t).strip()][:8]

    from karvyloop.cognition.pursuit_store import PursuitRecord, new_pursuit_id
    from karvyloop.schemas import Pursuit
    pid = new_pursuit_id(level)
    try:
        pursuit = Pursuit(
            id=pid, level=level, statement=(statement or "").strip(),
            commitment_condition="",   # 第一刀:人 ACCEPT 承诺卡 = committed(不做 DSL)
            revision_triggers=triggers, verify_gate=gate, status="active")
    except Exception as e:
        return {"ok": False, "reason": i18n.t("pursuit.err.bad_pursuit", error=str(e))}
    rec = PursuitRecord(pursuit, title=(title or "").strip(), owner=(owner or "").strip() or "karvy",
                        domain_id=(domain_id or "").strip() or "l0")
    store.put(rec)

    # 承诺卡(H2A:承诺跨天目标是决策,必人拍)。进 HIGH_RISK_KINDS,绝不被静音自动兑现。
    commit_pid = ""
    try:
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_pursuit_commit
        card = proposal_for_pursuit_commit(
            pursuit_id=pid, statement=pursuit.statement, gate_desc=gate_desc,
            level=level, revision_triggers=triggers, domain_id=rec.domain_id, ts=time.time(),
            origin=origin)
        commit_pid = card.proposal_id
        await broadcast_proposal(app, card)
    except Exception as e:
        logger.warning(f"[pursuit] 升承诺卡失败(Pursuit 已建,可稍后手动承诺): {e}")
    return {"ok": True, "pursuit_id": pid, "status": pursuit.status,
            "commit_proposal_id": commit_pid, "gate_desc": gate_desc}


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
    return await create_pursuit_with_commit_card(
        request.app, statement=req.statement, verify_gate=req.verify_gate,
        title=req.title, level=req.level, owner=req.owner, domain_id=req.domain_id,
        revision_triggers=req.revision_triggers)


@router.get("/pursuits")
def api_pursuits_list(request: Request) -> dict[str, Any]:
    """列出所有 Pursuit(轻量摘要;K4 只读)。"""
    store = getattr(request.app.state, "pursuit_store", None)
    if store is None:
        return {"pursuits": [], "active_count": 0}
    recs = sorted(store.all(), key=lambda r: r.updated_ts, reverse=True)
    return {"pursuits": [r.summary() for r in recs], "active_count": store.active_count()}


def _derive_tasks(app: Any, pursuit_id: str, rec: Any) -> list:
    """一个 Pursuit 派生的 task(复用任务账,不另造平行账本 —— Trace/任务看板是唯一运行记录源)。

    优先按 last_task_ids 精确取;再按 pursuit_id 全量过滤兜底。详情页时间线 + 讲讲组料都从这里取,
    只碰**这条 pursuit 自己的** task(narrate 组料隔离)。
    """
    tasks: list = []
    task_reg = getattr(app.state, "task_registry", None)
    if task_reg is None:
        return tasks
    seen: set = set()
    for tid in list(getattr(rec, "last_task_ids", []) or []):
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
    return tasks


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
    detail["tasks"] = _derive_tasks(app, pursuit_id, rec)
    return {"ok": True, "pursuit": detail}


# ---- 挂起记录的出口(docs/88 真伤2):恢复(继续)/ 放下 —— 给挂起(infeasible/达地板/revised)的
# Pursuit 一条真出口,不再是"永久僵尸、无路可走"。REVISE 卡 REJECT 也复用 resume(不改方向=继续)。

def resume_pursuit_record(rec: Any) -> bool:
    """挂起/revised 的 Pursuit → 恢复成在跑(committed)。返回是否真改了(终态不动)。

    - suspended=False、status 回 committed、清 revision 挂起态(revision_reason)。
    - **重置成本地板计数**(advances / consecutive_failures = 0):用户明确"继续" = 再给一轮预算,
      否则被 max_advances/连败地板挂起的记录下一 tick 立刻重撞同一地板、白恢复。
    - 清节流戳(last_advance_ts=0)让它尽快接着推进。
    """
    p = rec.pursuit
    if p.status in ("done", "dropped"):
        return False
    rec.pursuit = p.model_copy(update={"status": "committed"})
    rec.suspended = False
    rec.revision_reason = ""
    rec.advances = 0
    rec.consecutive_failures = 0
    rec.last_advance_ts = 0.0
    return True


def drop_pursuit_record(rec: Any) -> bool:
    """放下 Pursuit → 标 dropped(退出活跃集),suspended=True。返回是否真改了(终态不动)。"""
    p = rec.pursuit
    if p.status in ("done", "dropped"):
        return False
    rec.pursuit = p.model_copy(update={"status": "dropped"})
    rec.suspended = True
    return True


@router.post("/pursuit/{pursuit_id}/resume")
def api_pursuit_resume(pursuit_id: str, request: Request) -> dict[str, Any]:
    """继续追一个先前暂停的目标(挂起/改方向 → 回 committed,机器接着自跑)。K5:用户主动点。"""
    from karvyloop import i18n
    store = getattr(request.app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.no_store")}
    rec = store.get(pursuit_id)
    if rec is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.not_found")}
    if not resume_pursuit_record(rec):
        return {"ok": False, "reason": i18n.t("pursuit.err.terminal_no_resume", status=rec.status)}
    store.put(rec)
    return {"ok": True, "status": rec.status}


@router.post("/pursuit/{pursuit_id}/drop")
def api_pursuit_drop(pursuit_id: str, request: Request) -> dict[str, Any]:
    """放下一个目标(标 dropped,退出活跃集)。K5:用户主动点。"""
    from karvyloop import i18n
    store = getattr(request.app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.no_store")}
    rec = store.get(pursuit_id)
    if rec is None:
        return {"ok": False, "reason": i18n.t("pursuit.err.not_found")}
    if not drop_pursuit_record(rec):
        return {"ok": False, "reason": i18n.t("pursuit.err.terminal_no_drop", status=rec.status)}
    store.put(rec)
    return {"ok": True, "status": rec.status}


# ---- 「让小卡讲讲」(docs/88 第三刀 #2):LLM 叙述,点了才烧,产出不落库(纯展示)----
# 状态条/时间线是确定性零 LLM;讲讲是唯一烧 token 的一层,且**必须人点**(绝不自动)。
# 组料只含这条 pursuit 自己的数据(task/字段),走 gateway 咽喉 + token_source("pursuit_narrate")打标;
# 无 gateway / 调用失败 / 空回复 → 确定性兜底文本,绝不 500(宁空勿毒:垃圾即空 → 兜底)。

def _narrate_material(rec: Any, tasks: list) -> str:
    """给模型的现场组料(英文中性标签;输出语言由 system prompt 定)。只喂这条 pursuit 自己的数据。"""
    p = rec.pursuit
    gate = dict(getattr(p, "verify_gate", None) or {})
    lines = [f"Goal: {(p.statement or '').strip()}"]
    if gate.get("type") == "test_pass" and gate.get("cmd"):
        lines.append(f"Done when command `{gate.get('cmd')}` exits 0")
    elif gate.get("type") == "file_exists" and gate.get("path"):
        lines.append(f"Done when file `{gate.get('path')}` exists")
    lines.append(
        f"Status: {p.status}; advanced {rec.advances}x; "
        f"{rec.consecutive_failures} failures in a row")
    if rec.progress_note:
        lines.append(f"Latest progress note: {str(rec.progress_note)[:200]}")
    if rec.revision_reason:
        lines.append(f"Direction-change reason: {str(rec.revision_reason)[:200]}")
    ordered = sorted(tasks, key=lambda d: (d.get("finished") or d.get("started") or 0))
    for i, d in enumerate(ordered[-6:], 1):
        st = d.get("status") or "?"
        res = str(d.get("result") or "")[:160]
        intent = str(d.get("intent") or "")[:80]
        lines.append(f"Run {i} [{st}] {intent}: {res}".rstrip())
    return "\n".join(lines)[:2200]


def _narrate_fallback(rec: Any, tasks: list) -> str:
    """确定性兜底(零 LLM):从同一现场数据拼一句人话。gateway 无/失败/空回复时用它。"""
    from karvyloop import i18n
    parts: list = []
    if rec.advances > 0:
        parts.append(i18n.t("pursuit.narrate.fb_advances", n=rec.advances))
    latest = None
    if tasks:
        latest = max(tasks, key=lambda d: (d.get("finished") or d.get("started") or 0))
    if rec.consecutive_failures >= 2:
        parts.append(i18n.t("pursuit.narrate.fb_stuck", n=rec.consecutive_failures))
    elif latest is not None:
        if latest.get("status") == "error":
            parts.append(i18n.t("pursuit.narrate.fb_last_fail", err=str(latest.get("result") or "")[:80]))
        else:
            parts.append(i18n.t("pursuit.narrate.fb_last_ok"))
    if not parts:
        parts.append(i18n.t("pursuit.narrate.fb_none"))
    if rec.progress_note:
        parts.append(i18n.t("pursuit.narrate.fb_progress", note=str(rec.progress_note)[:80]))
    return " ".join(parts).strip()[:200]


@router.post("/pursuit/{pursuit_id}/narrate")
async def api_pursuit_narrate(pursuit_id: str, request: Request) -> dict[str, Any]:
    """「让小卡讲讲」:把这条 pursuit 的现场翻成「我做了什么/为什么/卡在哪」(≤150 字)。

    产出**不落库**(纯展示)。无 gateway / 失败 / 空回复 → 确定性兜底(绝不 500)。
    """
    from karvyloop import i18n
    app = request.app
    store = getattr(app.state, "pursuit_store", None)
    if store is None:
        return {"ok": False, "reason": "pursuit store not wired"}
    rec = store.get(pursuit_id)
    if rec is None:
        return {"ok": False, "reason": "not found"}
    tasks = _derive_tasks(app, pursuit_id, rec)
    fallback = _narrate_fallback(rec, tasks)

    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": True, "narration": fallback, "source": "fallback"}

    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    lang = "Chinese" if i18n.get_locale() == "zh" else "English"
    sys_prompt = (
        "You are Karvy, the user's personal copilot, reporting on a long-horizon goal to a "
        "non-technical owner. In the first person, say plainly what you did, why, and where you're "
        "stuck — concrete but jargon-free (never say 'verify_gate', 'trace', 'H2A', 'commit'). "
        f"Keep it under 150 characters, one short paragraph. Reply in {lang}."
    )
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=rk.get("model_ref") or None))
    except Exception:
        ref = rk.get("model_ref", "")
    out = ""
    try:
        with token_source("pursuit_narrate"):
            async for ev in gw.complete([{"role": "user", "content": _narrate_material(rec, tasks)}],
                                        [], ref, system=SystemPrompt(static=[sys_prompt])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[pursuit] narrate 失败,退兜底: {e}")
        return {"ok": True, "narration": fallback, "source": "fallback"}
    text = (out or "").strip()[:150]
    if not text:   # 宁空勿毒:空/纯空白回复 → 确定性兜底,不硬塞
        return {"ok": True, "narration": fallback, "source": "fallback"}
    return {"ok": True, "narration": text, "source": "llm"}


__all__ = ["router", "create_pursuit_with_commit_card",
           "resume_pursuit_record", "drop_pursuit_record"]
