"""workflow_store.py — 结晶的 workflow 模板库(Hardy:一次稳定成功→沉淀→快脑匹配复用)。

群内协作 workflow 跑通且你确认后,把它**结晶**成可复用模板(按**角色类型 agent_id** 参数化,
跨域可复用:"产品经理→设计师→前端 做产品" 这种 pattern)。下次 @ 同类角色做类似事 → **快脑**
(便宜、确定性,无 LLM)按 角色集 + 目标词面 匹配 → 小卡提议复用、你确认。

持久化 JSON list。匹配复用 graph._tokens(中文 bigram + 英文词)。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from karvyloop.cognition.graph import _tokens


class WorkflowStore:
    def __init__(self, path, *, clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def save(self, *, goal: str, role_keys: list, steps: list, name: str = "") -> dict:
        """结晶一条模板。role_keys = 角色类型(agent_id)集;steps 用 role_key 引用角色(跨域可复用)。"""
        items = self.all()
        tpl = {
            "id": uuid.uuid4().hex[:12],
            "name": (name or goal)[:40],
            "goal": goal, "role_keys": list(dict.fromkeys(role_keys)),
            "steps": steps, "use_count": 0, "created_ts": self._clock(),
        }
        items.insert(0, tpl)
        self._save(items[:100])   # 封顶
        return tpl

    def match(self, goal: str, role_keys: list, *, min_role_jac: float = 0.6,
              min_goal: float = 0.12, min_score: float = 0.4) -> Optional[dict]:
        """快脑匹配:角色集够像(Jaccard≥min_role_jac)**且**目标词面够近(≥min_goal)→ 最佳模板。

        两条都要 —— 同一批角色但目标八竿子打不着(登录页 vs 年会)不该复用(只靠角色集会误配)。
        """
        rk = set(role_keys)
        if not rk:
            return None
        gt = _tokens(goal or "")
        best, best_score = None, 0.0
        for t in self.all():
            trk = set(t.get("role_keys", []))
            if not trk:
                continue
            jac = len(rk & trk) / len(rk | trk)
            if jac < min_role_jac:
                continue
            tgt = _tokens(t.get("goal", ""))
            gov = (len(gt & tgt) / len(gt | tgt)) if (gt or tgt) else 0.0
            if gov < min_goal:
                continue                       # 目标不够像 → 不复用(光角色像不算)
            score = jac * 0.6 + gov * 0.4
            if score > best_score and score >= min_score:
                best, best_score = t, score
        return best

    def bump_use(self, tpl_id: str) -> None:
        items = self.all()
        for t in items:
            if t.get("id") == tpl_id:
                t["use_count"] = int(t.get("use_count", 0)) + 1
                break
        self._save(items)

    def _save(self, items: list) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)


__all__ = ["WorkflowStore"]
