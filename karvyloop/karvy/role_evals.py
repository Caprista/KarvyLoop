"""role_evals — 角色行为级 evals(借 EVE 的 defineEval,#39 ⑤)。

楔子有"技能结晶 verify 门",但**改了角色人设/规则后,它还干对吗**没人验。这里给每个角色挂一组
行为断言:一句测试 prompt + 期望(回复**含**哪些关键词 / **不含**哪些)。改了角色 → 一键跑、红绿。

只管存断言;真跑(按角色人格 drive + 判定)在 console 层。落 role_evals.json(原子写)+ 重启恢复。
和 §13 验证门、独立验收者同源 —— 都是"别让它默默退化"。
"""
from __future__ import annotations

import json
import pathlib
import uuid
from typing import Optional


class RoleEvalStore:
    """{role_id → [{id, prompt, contains:[str], absent:[str]}]}。无 path → 纯内存(测试)。"""

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self._path = pathlib.Path(path) if path else None
        self._evals: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._evals = raw
        except Exception:
            self._evals = {}

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._evals, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    def list(self, role_id: str) -> list:
        return list(self._evals.get(role_id, []))

    def add(self, role_id: str, prompt: str, *, contains: list = None, absent: list = None) -> Optional[dict]:
        prompt = (prompt or "").strip()
        if not role_id or not prompt:
            return None
        ev = {"id": uuid.uuid4().hex[:10], "prompt": prompt,
              "contains": [str(x).strip() for x in (contains or []) if str(x).strip()][:8],
              "absent": [str(x).strip() for x in (absent or []) if str(x).strip()][:8]}
        self._evals.setdefault(role_id, []).append(ev)
        self._save()
        return ev

    def delete(self, role_id: str, eval_id: str) -> bool:
        arr = self._evals.get(role_id)
        if not arr:
            return False
        n = len(arr)
        self._evals[role_id] = [e for e in arr if e.get("id") != eval_id]
        if not self._evals[role_id]:
            del self._evals[role_id]
        self._save()
        return len(self._evals.get(role_id, [])) < n


def judge_eval(reply: str, ev: dict) -> dict:
    """纯判定:回复是否满足一条 eval 的断言。返回 {passed, missing:[…], present_forbidden:[…]}。"""
    r = reply or ""
    missing = [c for c in (ev.get("contains") or []) if c not in r]
    forbidden = [a for a in (ev.get("absent") or []) if a in r]
    return {"passed": (not missing and not forbidden), "missing": missing, "present_forbidden": forbidden}


__all__ = ["RoleEvalStore", "judge_eval"]
