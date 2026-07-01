"""Compositor —— 用户答案 + step_id → .md 文本(纯函数)。

**AI 写规则,但用户保留最终否决权**——Compositor 只是"整理",**不**生成新内容。

设计:docs/11-wizard.md §3.3 不变量 4(纯函数,无 LLM 调)。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional

from .bootstrapper import WizardStep, is_skip


# 7 个文件头模板(每个 .md 的元数据 + 内容结构)
# 不变:每份 .md 都含 role_id + domain_id + step_id + ts 元数据头 + 用户答案

def _header(role_id: str, domain_id: str, step_id: str) -> str:
    """.md 文件的标准元数据头(frontmatter-like)。"""
    ts = datetime.now(timezone.utc).isoformat()
    return (
        f"<!-- karvyloop.wizard generated -->\n"
        f"<!-- role_id: {role_id} -->\n"
        f"<!-- domain_id: {domain_id} -->\n"
        f"<!-- step_id: {step_id} -->\n"
        f"<!-- ts: {ts} -->\n"
        f"\n"
    )


def _skip_placeholder(role_id: str, domain_id: str, step_id: str) -> str:
    """AC3:用户 skip 该步 → 写'暂不填'占位(Paradigm Loader 走 default)。"""
    return (
        _header(role_id, domain_id, step_id)
        + f"# {step_id}(暂不填)\n\n"
        + "_本文件由 Wizard 引导时跳过,内容由 Paradigm Loader 走 default 占位。_\n"
        + "_可在后续用 `karvyloop wizard edit` 或 M2.0 拍 5 Ethos Agent 补全。_\n"
    )


def _dont_understand_example(role_id: str, domain_id: str, step_id: str, step: WizardStep) -> str:
    """AC6:用户答'我不懂' → 给'示例'而非自动写(用户保留最终否决权)。"""
    return (
        _header(role_id, domain_id, step_id)
        + f"# {step_id}(示例 —— 待你确认)\n\n"
        + f"## Wizard 引导问题\n\n"
        + "\n".join(f"- {q}" for q in step.questions)
        + "\n\n"
        + f"## 示例占位\n\n"
        + f"_你刚说'我不懂'。Wizard **不**自动写 —— 这里给一个示例结构,你可参考并手动改写:_\n\n"
        + f"```\n"
        + f"[示例] 这里写{step_id}的内容...\n"
        + f"```\n\n"
        + f"_Wizard **不**会**自动**把这份示例提交 —— 等你**回车**或**修改后**再确认。_\n"
    )


def _compose_markdown(
    role_id: str,
    domain_id: str,
    step: WizardStep,
    answers: list[str],
) -> str:
    """正常的 .md 文本生成:把用户答案按问题顺序排成 section。"""
    body_lines: list[str] = []
    # title
    body_lines.append(f"# {step_id_title(step.step_id)}")
    body_lines.append("")
    # 每问一节
    for i, q in enumerate(step.questions):
        ans = answers[i] if i < len(answers) else ""
        body_lines.append(f"## Q{i+1}. {q}")
        body_lines.append("")
        body_lines.append(ans if ans else "_未填_")
        body_lines.append("")
    return _header(role_id, domain_id, step.step_id) + "\n".join(body_lines)


def _compose_composition_yaml(
    role_id: str,
    domain_id: str,
    answers: list[str],
) -> str:
    """COMPOSITION.yaml 特殊处理:第 1 个答案是 atom 列表(逗号分隔),第 2 个是顺序/条件说明。"""
    atoms_str = answers[0] if answers else ""
    ordering = answers[1] if len(answers) > 1 else ""
    # 简单解析:逗号分隔 → yaml 列表
    atoms = [a.strip() for a in atoms_str.split(",") if a.strip()]
    yaml_lines: list[str] = [
        f"<!-- karvyloop.wizard generated -->",
        f"<!-- role_id: {role_id} -->",
        f"<!-- domain_id: {domain_id} -->",
        f"<!-- step_id: COMPOSITION -->",
        f"role: {role_id}",
        f"domain: {domain_id}",
        f"composition:",
    ]
    for atom in atoms:
        yaml_lines.append(f"  - atom: {atom}")
    if ordering:
        yaml_lines.append(f"ordering: {_yaml_safe_str(ordering)}")
    return "\n".join(yaml_lines) + "\n"


def _yaml_safe_str(s: str) -> str:
    """简单 yaml 字符串转义(避免引号问题)。"""
    if any(c in s for c in [":", "#", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def step_id_title(step_id: str) -> str:
    """step_id → 人类可读标题。"""
    return {
        "IDENTITY": "我是谁(Identity)",
        "SOUL": "我的灵魂(Soul)",
        "USER": "我的用户(User)",
        "COMMITMENT": "我的承诺(Commitment)",
        "VERIFY": "我的验证门(Verify)",
        "MEMORY": "我的记忆(Memory)",
        "COMPOSITION": "我的配方(Composition)",
    }.get(step_id, step_id)


@dataclasses.dataclass
class Compositor:
    """Wizard 的"整理者"——纯函数,无 LLM 调。

    设计:docs/11-wizard.md §3.3 不变量 4。
    """
    role_id: str
    domain_id: str
    skip_marker: str = "skip"

    def compose(self, step: WizardStep, answers: list[str]) -> str:
        """根据用户答案 + step → .md 文本。

        行为分支:
          - 全 skip  → 写"暂不填"占位(AC3)
          - 任一"我不懂" → 写"示例 —— 待你确认"占位(AC6)
          - 否则 → 正常 .md 文本
        """
        from .bootstrapper import is_dont_understand
        # 全 skip
        if answers and all(is_skip(a) for a in answers):
            return _skip_placeholder(self.role_id, self.domain_id, step.step_id)
        # 任一"我不懂"
        if answers and any(is_dont_understand(a) for a in answers):
            return _dont_understand_example(self.role_id, self.domain_id, step.step_id, step)
        # COMPOSITION 走 yaml
        if step.step_id == "COMPOSITION":
            return _compose_composition_yaml(self.role_id, self.domain_id, answers)
        # 正常 .md
        return _compose_markdown(self.role_id, self.domain_id, step, answers)


def compose_file(
    *,
    role_id: str,
    domain_id: str,
    step: WizardStep,
    answers: list[str],
) -> str:
    """便捷函数:一次性用 Compositor 整理 1 个 .md 文本。

    等价于 `Compositor(role_id, domain_id).compose(step, answers)`。
    """
    return Compositor(role_id=role_id, domain_id=domain_id).compose(step, answers)
