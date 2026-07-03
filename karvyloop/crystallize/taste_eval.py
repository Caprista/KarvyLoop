"""crystallize/taste_eval.py — 口味命中率:把"越用越像你"变成一个**可证明、用户看得见**的数字。

**为什么**(docs/42 战略复盘,Hardy:"锋利如何让用户认可,而不是自嗨"):口味结晶此前是
暗地里预对齐,用户感不到"它学我了"。本模块让系统在每张决策卡发出时**先押注**"我猜你会
怎么拍",你拍板后对答案 —— 滚动命中率就是"像你"的刻度:"它现在能猜中你 78% 的拍板,
上一期 61%"。全市场没有第二家有结构化决策数据,想做都做不了(#42 三柱之一的可见化)。

**诚实三律**(设计即防自嗨):
1. **前瞻不回放**:预测必须记录在拍板**之前**(broadcast 时异步押注);事后没有预测的
   决策**不计入**——零数据泄漏,数字天然可信。
2. **宁空勿毒**:LLM 预测解析失败/没赶上 → 不押注不计入;绝不拿猜的数糊弄。
3. **样本门**:n < MIN_N 不报百分比(报"还在学你,再拍 N 次板");趋势要两期都够样本。

跑评分离:押注是 fire-and-forget(绝不拖慢提案推送);对账在拍板信号接缝里(纯查表,零 LLM)。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIN_N = 10          # 样本门:少于这个数不报百分比
WINDOW = 20         # 滚动窗:近 N 次算"当前命中率",再往前 N 次算"上一期"(趋势)
_PENDING_TTL_S = 14 * 86400   # 押注后两周没拍板 → 过期清理(DEFER 挂太久的不永久占坑)
_OUTCOME_RETAIN = 500         # 对账记录留存上限(够画趋势,有界)

# 押注只对真决策 kind;确认偏好卡是元循环(和 record_decision_signals 同口径)
SKIP_KINDS = ("confirm_decision_pref",)


class TastePredictionStore:
    """押注 + 对账的落盘存储(fail-safe:坏文件当空,丢了下次重攒)。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._pending: dict[str, dict] = {}    # proposal_id -> {predicted, confidence, ts}
        self._outcomes: list[dict] = []        # [{pid, predicted, actual, hit, ts}] 旧→新
        if self._path is not None and self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._pending = dict(d.get("pending") or {})
                self._outcomes = list(d.get("outcomes") or [])[-_OUTCOME_RETAIN:]
            except Exception:
                pass   # 坏文件当空

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {"pending": self._pending, "outcomes": self._outcomes[-_OUTCOME_RETAIN:]},
                ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("[taste] 落盘失败(不阻断): %s", e)

    def record_prediction(self, proposal_id: str, predicted: str, confidence: float,
                          *, now: Optional[float] = None) -> None:
        """押注一条(只在拍板前调;同 id 重推不覆盖 —— 第一次押的才算数,防事后改口)。"""
        pid = (proposal_id or "").strip()
        p = (predicted or "").upper()
        if not pid or p not in ("ACCEPT", "REJECT") or pid in self._pending:
            return
        self._pending[pid] = {"predicted": p, "confidence": float(confidence),
                              "ts": now if now is not None else time.time()}
        self._save()

    def resolve(self, proposal_id: str, actual: str, *, now: Optional[float] = None) -> Optional[bool]:
        """拍板后对账。只对 ACCEPT/REJECT(DEFER 不是终局,押注继续挂);
        没押过注 → None 不计入(诚实三律 #1)。返回 hit 与否。"""
        a = (actual or "").upper()
        if a not in ("ACCEPT", "REJECT"):
            return None
        bet = self._pending.pop((proposal_id or "").strip(), None)
        if bet is None:
            return None
        hit = bet["predicted"] == a
        self._outcomes.append({"pid": proposal_id, "predicted": bet["predicted"],
                               "actual": a, "hit": hit,
                               "ts": now if now is not None else time.time()})
        if len(self._outcomes) > _OUTCOME_RETAIN:
            self._outcomes = self._outcomes[-_OUTCOME_RETAIN:]
        self._save()
        return hit

    def prune_stale(self, *, now: Optional[float] = None) -> int:
        """清理押注后长期没拍板的(过期不计入,防 pending 无界)。"""
        n = now if now is not None else time.time()
        stale = [k for k, v in self._pending.items() if n - float(v.get("ts", 0)) > _PENDING_TTL_S]
        for k in stale:
            self._pending.pop(k, None)
        if stale:
            self._save()
        return len(stale)

    def outcomes(self) -> list[dict]:
        """**只读**:对账流水副本(旧→新,[{pid, predicted, actual, hit, ts}])。

        「挣来的静音」(karvy/silence.py,docs/49②/50 决定1)的分桶命中率从这里读 ——
        复用同一账本不另起;桶的 kind/domain 由 decision_log 按 pid 关联(本 store 不记,
        写入面保持不变)。返回副本,调用方改不到内部状态。"""
        return [dict(o) for o in self._outcomes]

    def stats(self) -> dict:
        """给 UI 的口味命中率:{n, hit_rate, prev_rate, trend, enough, need_more}。
        样本门:n<MIN_N → hit_rate=None + need_more=还差几次;趋势要两期都满窗才报。"""
        n = len(self._outcomes)
        if n < MIN_N:
            return {"taste_n": n, "taste_hit_rate": None, "taste_prev_rate": None,
                    "taste_trend": None, "taste_enough": False, "taste_need_more": MIN_N - n}
        recent = self._outcomes[-WINDOW:]
        rate = sum(1 for o in recent if o["hit"]) / len(recent)
        prev_slice = self._outcomes[-2 * WINDOW:-WINDOW]
        prev = (sum(1 for o in prev_slice if o["hit"]) / len(prev_slice)) if len(prev_slice) >= MIN_N else None
        trend = (rate - prev) if prev is not None else None
        return {"taste_n": n, "taste_hit_rate": rate, "taste_prev_rate": prev,
                "taste_trend": trend, "taste_enough": True, "taste_need_more": 0}


_PREDICT_SYSTEM = (
    "你是这位用户的决策口味模型。根据他已知的决策偏好和历史拍板,预测他对这条提案会 ACCEPT 还是 REJECT。"
    '严格只输出一个 JSON 对象:{"decision":"ACCEPT"或"REJECT","confidence":0到1的小数},别的什么都不要。'
    "把握不足就压低 confidence,但仍必须二选一。"
)


async def predict_decision(gateway: Any, model_ref: str, *, summary: str, basis: str = "",
                           kind: str = "", prefs_block: str = "") -> Optional[tuple[str, float]]:
    """押注一次:LLM 按口味预测 ACCEPT/REJECT。严解析,失败返 None(宁空勿毒)。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    material = f"提案类型:{kind}\n提案:{(summary or '').strip()[:600]}"
    if basis:
        material += f"\n依据:{basis.strip()[:400]}"
    if prefs_block:
        material += f"\n\n{prefs_block[:2000]}"
    else:
        material += "\n\n(该用户暂无已结晶的决策偏好,按提案本身的合理性保守预测。)"
    out = ""
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gateway.complete([{"role": "user", "content": material}], [], ref,
                                         system=SystemPrompt(static=[_PREDICT_SYSTEM])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        return None
    try:
        s = out.strip()
        if s.startswith("```"):
            s = s.strip("`").lstrip("json").strip()
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        d = json.loads(s[i:j + 1])
        dec = str(d.get("decision", "")).upper()
        conf = float(d.get("confidence", 0.5))
        if dec in ("ACCEPT", "REJECT") and 0.0 <= conf <= 1.0:
            return dec, conf
    except Exception:
        pass
    return None


__all__ = ["TastePredictionStore", "predict_decision", "MIN_N", "WINDOW", "SKIP_KINDS"]
