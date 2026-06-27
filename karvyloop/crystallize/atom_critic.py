"""atom_critic — role 对 atom 的多维分级满意度评判（crystallize/atom_critic.py）。

规格:docs/02 §14（结晶分两层·按问责链）。这是 **atom 层结晶的裁判**——

  问责链 `人 ← role ← atom`:atom 对 role 负责,所以 **atom 的结晶由 role 用客观
  评价体系判,绝不由人的反馈判**(人连 atom 实际做了啥都不知道)。本模块就是
  role 那把"评价体系"的尺(LLM 质量维在 ①-b 接,见 §14.2 第 3 条)。

三条铁律(docs/02 §14.2):
  1. **信用隔离**:只吃"这一条 run + 它自己的 signature(= role 派给它的子目标锚)",
     **绝不吃 role 自己对人的成败**。所以本模块的函数签名里**没有**任何 role-outcome 入参——
     role 侥幸成功白洗烂 atom / role 失败错罚好 atom,在结构上就不可能。
  2. **先做对,再做好**:做对(achievement:达成 + 过验证门)是首要维度;做好(efficiency/quality)
     在其上**加权**——做对没站住,整体满意度被压到 0,质量分作弊压不动它(见 `overall`)。
  3. **多维分级,不是二极管**:满意度是 (达成度, 效率, 质量) 的分级画像(0..1),不是 pass/fail。

效率维 v1 用 **步数(len(tool_calls))** 作"更省"的可验证代理(相对该 sig 的历史基线;
越省越高)。token/耗时维留后(逐 run 没存)。质量维(LLM)留 ①-b。
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass
from typing import Optional


# 「先做对再做好」的权重:做对是地基(W_BASE),做好(效率/质量)在其上加权(W_GOOD)。
# 二者和为 1 → achievement=1 且 good=1 时 overall=1.0;good=0 时只得 W_BASE。
W_BASE = 0.6
W_GOOD = 0.4

# 成功但**无验证门**(没法自证对错)的达成分。可调旋钮(docs/02 §14 纪律:改它需记数据依据)。
# 取 0.5 = "像做对但未核验"的诚实半分;偏严可下调让已核验的真做对更显性地压过它。
UNVERIFIED_SUCCESS_ACHIEVEMENT = 0.5


# ---- 评分体系(确定性·可验证;不是二极管,是分级)----

def score_achievement(success: bool, has_proof: bool) -> float:
    """做对(可验证):达成 + 过验证门。

    - 成功 **且** 该子目标有验证门证明 → 1.0(真做对)
    - 成功但**无**验证门(没法自证对错) → 0.5(像做对,但未核验——诚实打折,不假装 1.0)
    - 失败 → 0.0
    这把 verify verdict **流进了 atom 结晶信号**(docs/02 §14 契约 #1)。
    """
    if not success:
        return 0.0
    return 1.0 if has_proof else UNVERIFIED_SUCCESS_ACHIEVEMENT


def score_efficiency(steps: int, baseline_steps: Optional[float]) -> float:
    """做好·效率(可验证):相对该子目标历史基线步数,越省越高。

    - 无基线(首次跑)→ 1.0(没有可比的,不罚)
    - steps ≤ baseline → 1.0(达到或优于典型开销)
    - steps 越超基线 → 越低(baseline/steps),趋近 0
    """
    if not baseline_steps or baseline_steps <= 0:
        return 1.0
    return max(0.0, min(1.0, baseline_steps / max(1, steps)))


@dataclass
class AtomSatisfaction:
    """role 对 atom 一次 run 的多维分级满意度(docs/02 §14.2)。"""

    sig: str
    achievement: float          # 做对(可验证)
    efficiency: float           # 做好·效率(可验证)
    quality: Optional[float] = None   # 做好·质量(LLM 判;①-b 接,做对站住才采信)
    critique: str = ""          # LLM 评语(①-b)
    at: float = 0.0

    @property
    def overall(self) -> float:
        """聚合成单一满意度,但守「先做对再做好」:

            overall = achievement × (W_BASE + W_GOOD × good)

        - achievement=0(没做对)→ overall=0,**做好维救不回来**(防质量分作弊)。
        - good = 可用的"做好"维均值(效率一定有;质量有则并入)——质量只在做对之后才被加权。
        """
        good_dims = [self.efficiency]
        if self.quality is not None:
            good_dims.append(self.quality)
        good = sum(good_dims) / len(good_dims)
        return self.achievement * (W_BASE + W_GOOD * good)


class SatisfactionStore:
    """atom 多维满意度存储。仿 VerifyStore:内存实现(后续可换 sqlite)。

    按 sig(= role 派的子目标锚)分桶,**与 role 全局成败无关**(信用隔离)。
    """

    def __init__(self, cap: int = 64) -> None:
        self._by_sig: dict[str, list[tuple[AtomSatisfaction, int]]] = {}
        self._cap = cap
        self._lock = threading.Lock()

    def baseline_steps(self, sig: str) -> Optional[float]:
        """该 sig 历史 run 的步数基线(效率基准);无历史 → None。

        用**中位数**而非均值:抗"首跑特别贵 → 拉高基线 → 后面平庸跑全看着高效"的污染
        (对抗验收 M2)。中位数对单个离群早跑稳健。
        """
        with self._lock:
            rows = self._by_sig.get(sig) or []
            if not rows:
                return None
            return float(statistics.median(s for _, s in rows))

    def record(self, sig: str, sat: AtomSatisfaction, steps: int) -> None:
        with self._lock:
            rows = self._by_sig.setdefault(sig, [])
            rows.append((sat, steps))
            if len(rows) > self._cap:
                del rows[0]

    def samples(self, sig: str) -> list[AtomSatisfaction]:
        with self._lock:
            return [s for s, _ in (self._by_sig.get(sig) or [])]

    def mean_overall(self, sig: str) -> Optional[float]:
        s = self.samples(sig)
        if not s:
            return None
        return sum(x.overall for x in s) / len(s)

    def mean_dims(self, sig: str) -> Optional[dict]:
        """各维均值(质量维仅在有样本时计);无样本 → None。"""
        s = self.samples(sig)
        if not s:
            return None
        ach = sum(x.achievement for x in s) / len(s)
        eff = sum(x.efficiency for x in s) / len(s)
        q = [x.quality for x in s if x.quality is not None]
        return {
            "achievement": ach,
            "efficiency": eff,
            "quality": (sum(q) / len(q)) if q else None,
            "n": len(s),
        }


# ---- 评估 + 记录(信用隔离:入参只有 run + sig,无 role-outcome)----

def evaluate_run(run, sig: str, *, has_proof: bool,
                 baseline_steps: Optional[float], clock=time.time) -> AtomSatisfaction:
    """对一条 atom run 算多维满意度。duck-type:只读 run.success / run.tool_calls。"""
    steps = len(getattr(run, "tool_calls", None) or [])
    return AtomSatisfaction(
        sig=sig,
        achievement=score_achievement(bool(getattr(run, "success", False)), has_proof),
        efficiency=score_efficiency(steps, baseline_steps),
        at=clock(),
    )


def record_run(store: SatisfactionStore, run, sig: str, *,
               has_proof: bool, clock=time.time) -> AtomSatisfaction:
    """算 + 存一次(基线取记录前的历史均值,所以本次不污染自己的基线)。"""
    steps = len(getattr(run, "tool_calls", None) or [])
    sat = evaluate_run(run, sig, has_proof=has_proof,
                       baseline_steps=store.baseline_steps(sig), clock=clock)
    store.record(sig, sat, steps)
    return sat


__all__ = [
    "W_BASE", "W_GOOD",
    "score_achievement", "score_efficiency",
    "AtomSatisfaction", "SatisfactionStore",
    "evaluate_run", "record_run",
]
