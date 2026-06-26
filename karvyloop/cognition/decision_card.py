"""decision_card — 决策 loop 界面:把执行翻成决策者能判断的卡(KarvyLoop 的差异器官)。

设计稿:_local/decision-card-spec.md。四条硬不变量(本模块强制 + 测试锁住):
1. **只在决策层**:卡长在 role↔人 的 H2A 提案上,**不在 atom 执行层**(执行全自动、零卡)。
2. **接地,不诱信任**:`resolvable` 与每条依据的 status **只能来自确定性验证**(执行层 verify gate);
   没有验证门 → `unverifiable` + 老实标"未核验",**绝不给无依据的 solved**。problem/approach 是
   Karvy 的"复述"(narrated),与接地依据**分区**——让人一眼看清"被验证 vs 只是声称"。
3. **逼判断,不 rubber-stamp**:没有"全盘 Accept";依据逐条 认/改/删,改/删过 = engaged。
4. **稀有 + 高价值(过度判断 = 没判断)**:`should_surface` 是价值闸——纯执行成功不浮卡,
   只浮真正该你拍的(出问题 / 未核验 / 命中高价值偏好 / deontic 要求)。频率本身要最小化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

CriterionStatus = Literal["passed", "failed", "unchecked"]
CriterionSource = Literal["verify_gate", "trace", "narrated"]
Resolvable = Literal["solved", "partial", "failed", "unverifiable"]


@dataclass
class Criterion:
    """一条判定依据。status 仅当 source==verify_gate 时是接地的真核验;否则 unchecked。"""
    text: str
    status: CriterionStatus = "unchecked"
    source: CriterionSource = "narrated"
    dropped: bool = False          # 用户删掉(不认这条依据)
    edited_from: Optional[str] = None  # 用户改写过 → 原文(留痕)

    @property
    def grounded(self) -> bool:
        return self.source == "verify_gate" and self.status in ("passed", "failed")

    def to_dict(self) -> dict:
        return {"text": self.text, "status": self.status, "source": self.source,
                "grounded": self.grounded, "dropped": self.dropped, "edited": self.edited_from is not None}


@dataclass
class DecisionCard:
    """一张决策卡。verified 区(接地 ✓/✗)与 narrated 区(Karvy 复述)分开呈现。"""
    problem: str                       # narrated:这次解决什么(你的语言)
    approach: str                      # narrated:怎么解(思路,非工具细节)
    resolvable: Resolvable
    criteria: list[Criterion] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)   # 接地:trace_refs
    grounded: bool = False             # resolvable 是否来自真验证(决定 UI 是否标"未核验")

    def verified_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.grounded and not c.dropped]

    def engaged(self) -> bool:
        """用户是否真判断过(改或删过任一依据)= 没 rubber-stamp。"""
        return any(c.dropped or c.edited_from is not None for c in self.criteria)

    def to_dict(self) -> dict:
        return {
            "problem": self.problem, "approach": self.approach,
            "resolvable": self.resolvable, "grounded": self.grounded,
            "narrated_warning": not self.grounded,   # True → UI 必须显眼标"未经核验"
            "criteria": [c.to_dict() for c in self.criteria],
            "provenance": list(self.provenance),
        }


def build_decision_card(
    *,
    problem: str,
    approach: str,
    gate_results: Optional[list[tuple[str, bool]]] = None,   # [(依据文本, 是否通过)] ← 执行层 verify gate 跑出
    provenance: Optional[list[str]] = None,
) -> DecisionCard:
    """建卡。接地规则:gate_results 来自确定性验证(往上取自执行层);
    没有 → unverifiable + 老实(不给无依据的 solved)。"""
    criteria: list[Criterion] = []
    if gate_results:
        passed = 0
        for text, ok in gate_results:
            criteria.append(Criterion(text=text, status=("passed" if ok else "failed"),
                                      source="verify_gate"))
            passed += 1 if ok else 0
        total = len(gate_results)
        if passed == total:
            resolvable: Resolvable = "solved"
        elif passed == 0:
            resolvable = "failed"
        else:
            resolvable = "partial"
        grounded = True
    else:
        # 没有可自动验证的判定依据 —— 老实说,绝不伪装成 solved。
        resolvable = "unverifiable"
        grounded = False
    return DecisionCard(problem=problem, approach=approach, resolvable=resolvable,
                        criteria=criteria, provenance=list(provenance or []), grounded=grounded)


def build_report_card(
    *,
    problem: str,
    approach: str,
    passed: bool,
    inconclusive: bool,
    criterion: str = "通过独立验收",
    feedback: str = "",
    provenance: Optional[list[str]] = None,
) -> dict:
    """执行后回报卡:一次**已执行**的活,翻成"它到底验过没"的卡(grounded ✓ 的自然产地)。

    诚实铁律(还 ROADMAP 记的那笔债):**只有独立验收给了明确判定(非 inconclusive)才接地**。
    - 验收通过(passed 且非 inconclusive)→ ✓ solved(真接地);
    - 验收未过(非 inconclusive)→ ✗ failed(真接地,你该知道);
    - 验收**无能力/未给明确判定**(inconclusive)→ `unverifiable` + 老实标"未核验",
      **绝不**把"跑完了/慢脑没崩"冒充成 ✓。

    复用 build_decision_card 的接地逻辑;返回 dict(带 kind="report" + surface_full + 失败时 feedback)。
    """
    gate_results = None if inconclusive else [(criterion, bool(passed))]
    card = build_decision_card(problem=problem, approach=approach,
                               gate_results=gate_results, provenance=provenance)
    d = card.to_dict()
    d["kind"] = "report"                          # 区分:执行后回报,非待决提案
    d["surface_full"] = d["resolvable"] != "solved"   # 纯通过 → 可压成一行;否则展开给你看
    if feedback and d["resolvable"] != "solved":
        d["feedback"] = feedback[:300]            # 没过/未决时,把验收意见给人看(为什么)
    return d


def should_surface(card: DecisionCard, *, high_value: bool = False,
                   deontic_requires: bool = False) -> bool:
    """价值闸 / 执行↔决策路由(第一刀保守版)。返回这张卡是否该浮给人拍。

    过度判断 = 没判断:纯执行成功**不浮**(自动办);只浮真正该你拍的:
      - deontic 要求 / 命中已确认高价值偏好 → 浮
      - 出问题(failed / partial)→ 浮(该你知道)
      - unverifiable → 浮(没核验的别静默替你放过;保守默认)
      - solved + 接地 + 非高价值 → **不浮**(纯执行成功,自动)
    """
    if deontic_requires or high_value:
        return True
    if card.resolvable in ("failed", "partial"):
        return True
    if card.resolvable == "unverifiable":
        return True
    return False  # solved + grounded + 非高价值 → 自动,不打扰


@dataclass
class SurfaceTracker:
    """反投降闸:连续"零修改 Accept"计数;达阈值 → 下次要求轻确认。

    过度判断 = 没判断的另一半防护:不是靠多弹卡,是靠侦测"你在无脑认"再拦一次。
    """
    threshold: int = 5
    consecutive_blind_accepts: int = 0

    def record(self, *, accepted: bool, engaged: bool) -> None:
        if accepted and not engaged:
            self.consecutive_blind_accepts += 1
        else:
            # 改/删过、或拒了/DEFER → 重置(你还在判断)
            self.consecutive_blind_accepts = 0

    def needs_recheck(self) -> bool:
        return self.consecutive_blind_accepts >= self.threshold


__all__ = ["Criterion", "DecisionCard", "build_decision_card", "build_report_card",
           "should_surface", "SurfaceTracker"]
