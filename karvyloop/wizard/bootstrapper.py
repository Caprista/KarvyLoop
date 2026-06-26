"""Wizard 7 步引导 schema + 问题生成。

**本模块是 docs/11 §3.2 引导问答 schema 的机器可读版**——文档给设计者看,本文件给 Compositor + Ethos Agent 看。
**不**调 LLM;Bootstrapper 的"理解用户答案"职责留给 M2.0 拍 5 Ethos Agent(强模型)。
本拍 Bootstrapper 只**生成**引导问题 + **校验**用户答案格式。

设计:docs/11-wizard.md §3.2。
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence


# ---- 7 步引导 schema -----------

@dataclasses.dataclass(frozen=True)
class WizardStep:
    """7 步引导中的 1 步——对应 1 个 .md 文件。

    字段:
      step_id:        文件 key(IDENTITY / SOUL / USER / ...)
      file_basename:  写到磁盘的文件名
      questions:      该步的引导问题(1-3 个)
      skip_allowed:   用户答 "skip" 是否合法(True = 写"暂不填"占位)
      description:    人类可读描述(Wizard 启动时显示)
    """
    step_id: str
    file_basename: str
    questions: tuple[str, ...]
    skip_allowed: bool
    description: str


# 7 步引导(严格按 #0 §2.4 7 文件清单顺序)
WIZARD_STEPS: tuple[WizardStep, ...] = (
    WizardStep(
        step_id="IDENTITY",
        file_basename="IDENTITY.md",
        questions=(
            "这个 role 是什么(产品经理?工程师?设计师?)?",
            "它属于什么部门 / 团队?",
            "它的职责边界是什么(能做什么 / 不能做什么)?",
        ),
        skip_allowed=True,
        description="我是谁 —— 名字 / 部门 / 职责 / 边界",
    ),
    WizardStep(
        step_id="SOUL",
        file_basename="SOUL.md",
        questions=(
            "这个 role 持什么原则(激进 / 保守 / 数据驱动 / 直觉型)?",
            "它最看重什么(速度 / 质量 / 创新 / 稳定)?",
            "有什么过去案例 / 行为倾向值得记住?",
        ),
        skip_allowed=True,
        description="我的灵魂 —— 原则 / 价值观 / 行为倾向",
    ),
    WizardStep(
        step_id="USER",
        file_basename="USER.md",
        questions=(
            "你的用户是谁(客户类型 / 行业 / 规模)?",
            "他们的偏好是什么(沟通风格 / 决策方式 / 痛点)?",
            "你跟他们的关系是什么(合作伙伴 / 内部 / 外部)?",
        ),
        skip_allowed=True,
        description="我的用户 —— 理解 / 偏好 / 关系",
    ),
    WizardStep(
        step_id="COMMITMENT",
        file_basename="COMMITMENT.md",
        questions=(
            "这个 role 当前最重要的承诺是什么(季度 OKR / 关键项目)?",
            "哪些原则 / 底线不能违反?",
        ),
        skip_allowed=True,
        description="我的承诺 —— OKR / 季度目标 / 关键原则",
    ),
    WizardStep(
        step_id="VERIFY",
        file_basename="VERIFY.md",
        questions=(
            "怎么判定这个 role 做得对(指标 / 标准 / 检查清单)?",
            "失败是什么样(什么样算『没做对』)?",
        ),
        skip_allowed=True,
        description="我的验证门 —— 怎么判定做对了",
    ),
    WizardStep(
        step_id="MEMORY",
        file_basename="MEMORY.md",
        questions=(
            "这个 role 的过去重要事件 / 项目历史(用户可以之后补)?",
        ),
        skip_allowed=True,
        description="我的记忆 —— 公司历史 / 项目历史 / 重要对话",
    ),
    WizardStep(
        step_id="COMPOSITION",
        file_basename="COMPOSITION.yaml",
        questions=(
            "这个 role 用哪些原子(列出名字,逗号分隔)?",
            "这些原子怎么串(顺序 / 条件)?",
        ),
        skip_allowed=True,
        description="我的配方 —— 用哪些原子 + 怎么串",
    ),
)


# 用户答案的合法标记
SKIP_MARKER = "skip"  # 用户跳过该步 → 写"暂不填"占位
DONT_UNDERSTAND_MARKER = "我不懂"  # 用户不懂 → Bootstrapper 给"示例"(AC6)
# 兼容:大小写不敏感 + 容错
SKIP_ALIASES: frozenset[str] = frozenset({"skip", "s", "跳过", "暂不填"})


def is_skip(answer: str) -> bool:
    """用户答案是否表示"跳过"该步。"""
    if answer is None or not answer.strip():
        return True
    return answer.strip().lower() in {a.lower() for a in SKIP_ALIASES}


def is_dont_understand(answer: str) -> bool:
    """用户答案是否表示"我不懂" → 给示例而非自动写。"""
    if not answer:
        return False
    a = answer.strip().lower()
    return a in {"我不懂", "不知道", "没想法", "idk", "i don't know", "i dont know"}


# ---- AC7 校验用的"已注册原子"接口 ----------

def validate_composition_atoms(
    composition_text: str,
    registered_atom_ids: Sequence[str],
) -> tuple[bool, list[str]]:
    """校验 COMPOSITION.yaml 引用的 atom 都在已注册列表里。

    返回 (is_valid, unknown_atom_ids)。
    """
    # 简化解析:从 yaml-like 文本里抓所有 `atom: <name>` 形式
    import re
    atom_pattern = re.compile(r"^\s*-\s*atom:\s*([A-Za-z0-9_]+)\s*$", re.MULTILINE)
    referenced = atom_pattern.findall(composition_text)
    unknown = [a for a in referenced if a not in registered_atom_ids]
    return (len(unknown) == 0, unknown)
