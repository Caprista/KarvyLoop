"""系统默认「尽责下属」协作契约(docs/02 §15.1.5)。

role↔atom 协作策略 = 我们 agent 的 harness 范式,**默认落进每个 role 的 COMMITMENT 层**。
这里只装**性情/策略**(可见、可编、按 role/域可覆盖);**薄确定性地板**(预算天花板 /
infra-dead 停 / fail-loud / verify 门)由运行时强制,不在此、模板覆盖不掉 —— 安全是地基。

契约正文是包内**只读**资产(随包发版、`git pull` 即升级、清用户数据 reset 动不到),
镜像 `registry.skills.system_skills_dir` 的放法。三条 role 起源入口(系统默认创建 /
外部 agent 导入 / 用户引导自建)都从这**一份规范默认** seed,单一真理源不漂移。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def system_contracts_dir() -> Path:
    """包内只读系统契约目录(`karvyloop/system_contracts/`)。镜像 system_skills_dir。"""
    return Path(__file__).resolve().parent.parent / "system_contracts"


@lru_cache(maxsize=1)
def default_commitment_contract() -> str:
    """「尽责下属」默认协作契约正文 —— seed 进每个 role 的 COMMITMENT,也供运行时注入。"""
    p = system_contracts_dir() / "resourceful_subordinate" / "DEFAULT_COMMITMENT.md"
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or _FALLBACK_CONTRACT
    except OSError:
        # 资产缺失(打包漏 system_contracts / 文件损坏)绝不该崩 role 创建 ——
        # 退回内联兜底(宁缺毋崩;同 wheel 打包教训,资产丢了也要能起)。
        return _FALLBACK_CONTRACT


def seed_commitment_md() -> str:
    """新 role 的 COMMITMENT.md 初始内容 = 默认契约 + 留给本 role 的可编辑区。

    默认契约可见可编(范式可见可编第一个住户);本 role 自己的承诺写在分隔线下面。
    """
    return (
        "# COMMITMENT\n\n"
        + default_commitment_contract()
        + "\n\n---\n\n## This role's own commitments\n\n(待充实)\n"
    )


# 打包漏掉资产时的内联兜底(精简版,仍传达核心 disposition)。
_FALLBACK_CONTRACT = (
    "## Collaboration contract — you are a resourceful subordinate\n\n"
    "Commit to feasible goals; exhaust your own resourcefulness (re-plan, swap "
    "atom, search/create a skill) before coming back; only return on genuine "
    "infeasibility, a dead base capability (model/network/sandbox), or spent "
    "budget — and then bring evidence, not a bare question."
)


__all__ = ["system_contracts_dir", "default_commitment_contract", "seed_commitment_md"]
