"""deontic — 业务域强护栏 + soul_subset 推导。

**核心不变量**(doc §4):
- D3 soul_subset 由 deontic 推(不接受外部传)
- D7 全部依赖注入

设计:docs/18 §3.1。
"""
from __future__ import annotations

import dataclasses
from typing import Optional


# 灵魂层 7 文件(拍 5 灵魂层)
SOUL_FILES: tuple[str, ...] = (
    "IDENTITY",
    "SOUL",
    "USER",
    "COMMITMENT",
    "VERIFY",
    "MEMORY",
    "COMPOSITION",
)


@dataclasses.dataclass(frozen=True)
class Deontic:
    """业务域强护栏。

    字段:
      forbid: 禁止行为列表
      oblige: 强制行为列表
      permit: 允许行为列表(可选,默认空)

    所有字段默认值 = (),允许省略参数。
    """
    forbid: tuple[str, ...] = ()
    oblige: tuple[str, ...] = ()
    permit: tuple[str, ...] = ()

    @staticmethod
    def empty() -> "Deontic":
        return Deontic(forbid=(), oblige=(), permit=())

    def merged(self, child: "Deontic") -> "Deontic":
        """子域继承父域 deontic: forbid/permit/obligue 都只能加不能删(D5)。"""
        # forbid: 父+子并集(子不能删父)
        forbid = tuple(set(self.forbid) | set(child.forbid))
        # oblige: 父+子并集
        oblige = tuple(set(self.oblige) | set(child.oblige))
        # permit: 父+子并集
        permit = tuple(set(self.permit) | set(child.permit))
        return Deontic(forbid=forbid, oblige=oblige, permit=permit)


def derive_soul_subset(deontic: Deontic) -> tuple[str, ...]:
    """从 deontic 推导 soul_subset(D3 灵魂级)。

    推导规则:
      - 如果有 forbid(强制 VERIFY) → 必含 VERIFY
      - 如果有 oblige(强制 COMMITMENT) → 必含 COMMITMENT
      - 如果有 permit 或为空(通用) → 必含 IDENTITY
      - 永远包含 SOUL 和 USER(业务域基本盘)

    注:本函数是纯函数,确定性推导(D3 不接受外部传)。
    """
    subset: list[str] = ["SOUL", "USER"]  # 基本盘
    subset.append("IDENTITY")  # 通用必含
    if deontic.forbid:
        subset.append("VERIFY")  # 有禁止 = 必验证
    if deontic.oblige:
        subset.append("COMMITMENT")  # 有强制 = 必承诺
    # MEMORY 暂不强制(留给 spec_coding/拍 2 决定)
    # COMPOSITION 暂不强制(留给拍 7 instance 决定)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for f in subset:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return tuple(out)


def deontic_guardrail_text(deontic: Deontic) -> str:
    """把域的 deontic(forbid/oblige/permit)渲染成**运行时护栏文本**,前置进慢脑 system 指令。

    **为什么是文本护栏而非确定性工具闸**:forbid/oblige 是自然语言"行为"(如"对外发邮件前必须复核"),
    不是工具名 —— 确定性 `tool in forbid` 匹配对它们基本不命中(=假接线)。NL 规则的正确 enforcement
    是①**运行时软护栏**(本函数:把规则摆进慢脑 system,让模型受约束)+ ②**建时** skill×域冲突检测
    (rules_from_domain + LLM judge)。真确定性硬闸只在 capability 链(denied_tools/deny 规则/安全检查)
    + 网关预算地板,那些是工具/命令级的、可确定判定的。

    此前的洞:`governance_text` 只注入 value.md,forbid/oblige 从不进运行时护栏 —— 域的硬规则
    在执行路径上形同虚设。本函数补上;空 deontic → 空串(0 回归)。
    """
    if not deontic:
        return ""
    lines: list[str] = []
    if deontic.forbid:
        lines.append("你在本业务域**绝不能**做以下事(违反即失职):")
        lines += [f"  - {f}" for f in deontic.forbid]
    if deontic.oblige:
        lines.append("你在本业务域**必须**做到以下事:")
        lines += [f"  - {o}" for o in deontic.oblige]
    if deontic.permit:
        lines.append("本业务域**明确允许**(即便一般情况下会犹豫):")
        lines += [f"  - {p}" for p in deontic.permit]
    return "\n".join(lines)


def apply_deontic(
    deontic: Deontic,
    action: str,
    *,
    mode: str = "report",
    auditor: Optional[object] = None,
) -> "DeonticResult":
    """应用 deontic 到一个行为(AC7)。

    模式:
      - mode="report": 仅返回结果(不抛),用于审计报告
      - mode="enforce": 违反时抛 DeonticViolationError(M3+ 才会真的 enforce)

    auditor: 拍 5 Auditor 注入(M3+ 接入)。M3 拍 1 v0 不接,传 None。
    """
    is_forbidden = action in deontic.forbid
    is_required = action in deontic.oblige
    is_permitted = action in deontic.permit

    result = DeonticResult(
        action=action,
        forbidden=is_forbidden,
        required=is_required,
        permitted=is_permitted,
        allowed=(not is_forbidden) and (not is_required or is_permitted),
    )
    return result


@dataclasses.dataclass(frozen=True)
class DeonticResult:
    """apply_deontic 的结果。"""
    action: str
    forbidden: bool
    required: bool
    permitted: bool
    allowed: bool
