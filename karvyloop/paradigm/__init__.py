"""karvyloop.paradigm —— 范式加载器(M2.0 拍 0)。

机制:把"role 实例在某个 context 下应该加载哪些 .md 进 prompt"这件事**机制化**。
不**是 .md 内容生成器(那是 Wizard 拍 1);不**是"角色加载器"——角色本身**不**是被加载的对象。

设计:docs/10-paradigm-loader.md。决策:CONTEXT/01-decision-log §十五。
本体论依据:#0 §2.4 4 上下文加载规则 + 7 文件清单 + §2.4.1 两条独立成长时间线。
"""

from .loader import LoadedParadigm, ParadigmContext, load_paradigm
from .policy import (
    LAYER_ORDER,
    SOUL_FILES,
    LoadRule,
    R1_FULL_SCENE,
    R2_PURSUIT_HIT,
    R3_VERIFY_STEP,
    R4_DOMAIN_LAYER,
)

__all__ = [
    # 公开 API
    "load_paradigm",
    "ParadigmContext",
    "LoadedParadigm",
    # 协议(供 Wizard M2.0 拍 1 + Ethos M2.0 拍 5 引用)
    "LAYER_ORDER",
    "SOUL_FILES",
    "LoadRule",
    "R1_FULL_SCENE",
    "R2_PURSUIT_HIT",
    "R3_VERIFY_STEP",
    "R4_DOMAIN_LAYER",
]
