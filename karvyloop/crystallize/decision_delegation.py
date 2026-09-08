"""Decision delegation questionnaires, contracts, and authorization gates."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

_RISK_RANK = {"low": 1, "medium": 2, "high": 3}
_VALID_AUTONOMY = {"recommend", "reversible", "low_risk", "bounded"}


def detect_delegation_intent(text: str) -> bool:
    """Recognize an explicit request to delegate a class of decisions."""
    value = re.sub(r"\s+", "", text or "")
    if len(value) < 8 or len(value) > 500:
        return False
    patterns = (
        r"(?:想|希望|以后|今后)?(?:让|由|交给|委托)(?:小卡|你).{0,24}(?:决定|决策|选择|拍板)",
        r"(?:小卡|你).{0,12}(?:替我|帮我|为我).{0,16}(?:决定|决策|选择|拍板)",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def build_questionnaire(goal: str, domain: str = "") -> dict[str, Any]:
    goal = (goal or "").strip()[:1000]
    domain = (domain or "").strip()[:120]
    if not goal:
        raise ValueError("决策目标不能为空")
    return {
        "version": 1,
        "goal": goal,
        "domain": domain,
        "questions": [
            {
                "id": "primary_objective", "type": "choice", "required": True,
                "text": f"针对“{goal[:80]}”，你最优先保证什么？",
                "options": ["结果质量与长期收益", "尽快得到可用结果", "成本可控", "方案灵活且容易撤销"],
            },
            {
                "id": "risk_tolerance", "type": "choice", "required": True,
                "text": "小卡可以独立承担多高风险的选择？",
                "options": ["仅低风险", "最高中风险", "允许高风险但必须可撤销"],
            },
            {
                "id": "uncertainty_policy", "type": "choice", "required": True,
                "text": "信息不完整时，小卡应该怎么做？",
                "options": ["先询问，不自行补假设", "采用保守假设后推进", "只要可撤销就先推进"],
            },
            {
                "id": "autonomy_scope", "type": "choice", "required": True,
                "text": "你准备授予哪一级决策权限？",
                "options": ["只给建议，由我拍板", "可逆选择可自主拍板", "低风险选择可自主拍板", "授权边界内均可自主拍板"],
            },
            {
                "id": "hard_constraints", "type": "text", "required": True,
                "text": "有哪些绝对不能违反的条件？请逐条填写，可用分号分隔。",
                "placeholder": "例如：不能丢失现有数据；不能产生外部费用",
            },
            {
                "id": "escalation_triggers", "type": "text", "required": True,
                "text": "出现哪些情况时必须重新询问你？",
                "placeholder": "例如：不可逆变更；候选方案差距很小；需要对外承诺",
            },
            {
                "id": "success_criteria", "type": "text", "required": True,
                "text": "什么结果算这类决策做对了？",
                "placeholder": "写出可观察的成功标准",
            },
        ],
    }


def _split_items(value: Any, *, limit: int = 12) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip()[:300] for part in re.split(r"[;；\n]+", text) if part.strip()][:limit]


def compile_contract(goal: str, answers: dict[str, Any], domain: str = "") -> dict[str, Any]:
    questionnaire = build_questionnaire(goal, domain)
    questions = {item["id"]: item for item in questionnaire["questions"]}
    clean: dict[str, str] = {}
    for qid, question in questions.items():
        value = str((answers or {}).get(qid, "")).strip()
        if question.get("required") and not value:
            raise ValueError(f"问题“{question['text']}”尚未回答")
        if question["type"] == "choice" and value not in question["options"]:
            raise ValueError(f"问题“{question['text']}”的选项无效")
        clean[qid] = value[:2000]

    objective = clean["primary_objective"]
    objective_weights = {
        "结果质量与长期收益": {"quality": 0.5, "speed": 0.15, "cost": 0.15, "reversibility": 0.2},
        "尽快得到可用结果": {"quality": 0.2, "speed": 0.5, "cost": 0.15, "reversibility": 0.15},
        "成本可控": {"quality": 0.2, "speed": 0.15, "cost": 0.5, "reversibility": 0.15},
        "方案灵活且容易撤销": {"quality": 0.2, "speed": 0.15, "cost": 0.15, "reversibility": 0.5},
    }[objective]
    risk_ceiling = {"仅低风险": "low", "最高中风险": "medium", "允许高风险但必须可撤销": "high"}[clean["risk_tolerance"]]
    autonomy_mode = {
        "只给建议，由我拍板": "recommend",
        "可逆选择可自主拍板": "reversible",
        "低风险选择可自主拍板": "low_risk",
        "授权边界内均可自主拍板": "bounded",
    }[clean["autonomy_scope"]]
    confidence_threshold = {
        "先询问，不自行补假设": 0.9,
        "采用保守假设后推进": 0.8,
        "只要可撤销就先推进": 0.72,
    }[clean["uncertainty_policy"]]
    now = time.time()
    return {
        "id": uuid.uuid4().hex,
        "version": 1,
        "goal": questionnaire["goal"],
        "domain": questionnaire["domain"],
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "objectives": objective_weights,
        "primary_objective": objective,
        "hard_constraints": _split_items(clean["hard_constraints"]),
        "success_criteria": _split_items(clean["success_criteria"], limit=8),
        "authority": {
            "mode": autonomy_mode,
            "risk_ceiling": risk_ceiling,
            "confidence_threshold": confidence_threshold,
            "requires_reversible": autonomy_mode == "reversible" or risk_ceiling == "high",
        },
        "escalation_triggers": _split_items(clean["escalation_triggers"]),
        "answers": clean,
        "decisions": [],
    }


def summarize_contract(contract: dict[str, Any]) -> str:
    auth = contract.get("authority", {})
    return (
        f"目标：{contract.get('goal', '')}\n"
        f"首要标准：{contract.get('primary_objective', '')}\n"
        f"权限：{auth.get('mode', 'recommend')}；风险上限：{auth.get('risk_ceiling', 'low')}；"
        f"最低置信度：{float(auth.get('confidence_threshold', 0.9)):.0%}\n"
        f"硬约束：{'；'.join(contract.get('hard_constraints', [])) or '无'}\n"
        f"必须升级：{'；'.join(contract.get('escalation_triggers', [])) or '无'}"
    )


def authorization_verdict(contract: dict[str, Any], assessment: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if contract.get("status") != "active":
        reasons.append("决策委托尚未激活或已撤销")
    authority = contract.get("authority", {})
    mode = authority.get("mode", "recommend")
    if mode not in _VALID_AUTONOMY or mode == "recommend":
        reasons.append("授权模式仅允许提供建议")
    violations = [str(x) for x in assessment.get("constraint_violations", []) if str(x).strip()]
    if violations:
        reasons.append("候选方案触发硬约束：" + "；".join(violations[:4]))
    risk = str(assessment.get("risk", "high")).lower()
    ceiling = str(authority.get("risk_ceiling", "low")).lower()
    if _RISK_RANK.get(risk, 99) > _RISK_RANK.get(ceiling, 0):
        reasons.append(f"风险等级 {risk} 超过授权上限 {ceiling}")
    if authority.get("requires_reversible") and not bool(assessment.get("reversible")):
        reasons.append("当前授权要求决策可撤销")
    try:
        confidence = float(assessment.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    threshold = float(authority.get("confidence_threshold", 0.9))
    if confidence < threshold:
        reasons.append(f"置信度 {confidence:.0%} 低于授权阈值 {threshold:.0%}")
    selected = str(assessment.get("selected_option_id", "")).strip()
    if not selected:
        reasons.append("没有可验证的候选方案")
    return ("decided" if not reasons else "escalated", reasons)


class DecisionDelegationStore:
    """Atomic JSON persistence with thread and cross-process coordination."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._lock = RLock()
        self._items: list[dict[str, Any]] = []
        with self._locked_items():
            pass

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        with self._lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _locked_items(self) -> Iterator[None]:
        with self._lock:
            with self._process_lock():
                self._items = self._load()
                yield

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("id")] if isinstance(data, list) else []

    def _save(self) -> None:
        payload = json.dumps(self._items, ensure_ascii=False, indent=2)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)

    def list(self) -> list[dict[str, Any]]:
        with self._locked_items():
            return [json.loads(json.dumps(item, ensure_ascii=False)) for item in reversed(self._items)]

    def get(self, contract_id: str) -> dict[str, Any] | None:
        with self._locked_items():
            item = next((x for x in self._items if x.get("id") == contract_id), None)
            return json.loads(json.dumps(item, ensure_ascii=False)) if item else None

    def begin_decision(self, contract_id: str) -> dict[str, Any] | None:
        """Linearize model-call admission against activation and revocation."""
        with self._locked_items():
            item = next((x for x in self._items if x.get("id") == contract_id), None)
            if item is None or item.get("status") != "active":
                return None
            return json.loads(json.dumps(item, ensure_ascii=False))

    def put(self, contract: dict[str, Any]) -> dict[str, Any]:
        with self._locked_items():
            item = json.loads(json.dumps(contract, ensure_ascii=False))
            self._items = [x for x in self._items if x.get("id") != item.get("id")]
            self._items.append(item)
            self._save()
            return json.loads(json.dumps(item, ensure_ascii=False))

    def activate(self, contract_id: str) -> dict[str, Any] | None:
        with self._locked_items():
            for item in self._items:
                if item.get("id") != contract_id:
                    continue
                if item.get("status") == "revoked":
                    raise ValueError("已撤销的决策委托不能重新激活")
                if item.get("status") == "active":
                    return json.loads(json.dumps(item, ensure_ascii=False))
                if item.get("status") != "draft":
                    raise ValueError("决策委托状态无效")
                item["status"] = "active"
                item["updated_at"] = time.time()
                self._save()
                return json.loads(json.dumps(item, ensure_ascii=False))
        return None

    def set_status(self, contract_id: str, status: str) -> dict[str, Any] | None:
        if status == "active":
            return self.activate(contract_id)
        if status != "revoked":
            raise ValueError("无效的契约状态")
        with self._locked_items():
            for item in self._items:
                if item.get("id") == contract_id:
                    item["status"] = "revoked"
                    item["updated_at"] = time.time()
                    self._save()
                    return json.loads(json.dumps(item, ensure_ascii=False))
        return None

    def record_decision(self, contract_id: str, receipt: dict[str, Any]) -> dict[str, Any] | None:
        """Record a receipt while atomically honoring a concurrent revocation."""
        with self._locked_items():
            for item in self._items:
                if item.get("id") == contract_id:
                    stored = json.loads(json.dumps(receipt, ensure_ascii=False))
                    if stored.get("status") == "decided" and item.get("status") != "active":
                        stored["status"] = "escalated"
                        reasons = stored.setdefault("gate_reasons", [])
                        reasons.append("决策评估期间授权已撤回")
                    decisions = item.setdefault("decisions", [])
                    decisions.append(stored)
                    del decisions[:-50]
                    item["updated_at"] = time.time()
                    self._save()
                    return json.loads(json.dumps(stored, ensure_ascii=False))
        return None


__all__ = [
    "DecisionDelegationStore", "authorization_verdict", "build_questionnaire",
    "compile_contract", "detect_delegation_intent", "summarize_contract",
]
