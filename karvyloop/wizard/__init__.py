"""karvyloop.wizard —— Wizard CLI(M2.0 拍 1)。

引导用户从 0 写一个 role 的 7 文件 + COMPOSITION.yaml。
**不**是"AI 自动写 Soul"——是 AI 问、用户答、AI 整理、用户最终确认。

设计:docs/11-wizard.md。决策:CONTEXT/01-decision-log §十六。
本体论依据:#0 §2.4 7 文件清单 + §2.4.1 两条成长时间线 + 镜像 P(Bootstrapper)。
"""

from .bootstrapper import WIZARD_STEPS, WizardStep
from .compositor import Compositor, compose_file
from .preview import preview_paradigm

__all__ = [
    # 协议
    "WIZARD_STEPS",
    "WizardStep",
    "Compositor",
    "compose_file",
    "preview_paradigm",
]
