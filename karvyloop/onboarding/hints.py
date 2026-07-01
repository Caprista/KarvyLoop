"""5 类 first-touch hint 常量。

**拍 3 不调 LLM** —— 每条 hint 是**字符串常量**(I4 不变量)。
M2.0 拍 3.5 升级弱模型后再**做** LLM 文案改写。

设计:docs/13 §3.3。
"""
from __future__ import annotations

from typing import Final

# 5 类 hint 的 flag 名(per-install 唯一,写进 seen.yaml)
NO_ROLE_YET: Final[str] = "no_role_yet"
FIRST_SKILL_USE: Final[str] = "first_skill_use"
FIRST_PURSUIT: Final[str] = "first_pursuit"
FIRST_ATOM_COMPOSE: Final[str] = "first_atom_compose"
FIRST_LONG_TOOL: Final[str] = "first_long_tool"

# 所有 flag 集中维护 —— 测试断言 5 个全在(I8 协议不变量)
ALL_FLAGS: Final[tuple[str, ...]] = (
    NO_ROLE_YET,
    FIRST_SKILL_USE,
    FIRST_PURSUIT,
    FIRST_ATOM_COMPOSE,
    FIRST_LONG_TOOL,
)


# 5 条 hint 文案 —— **纯字符串**,不调 LLM(I4)
HINTS: Final[dict[str, str]] = {
    NO_ROLE_YET: (
        "💡 我看到你还没加角色 —— KarvyLoop 的角色 = 7 个灵魂文件 + COMPOSITION.yaml。"
        "运行 `karvyloop wizard` 我会一步步引导你写好,大约 5-7 分钟。"
        "(本提示只显示一次)"
    ),
    FIRST_SKILL_USE: (
        "💡 这个 skill 是你之前对话里自动结晶的,不是 KarvyLoop 自带的 —— "
        "用得越多,以后越顺手。完整技能库在 ~/.karvyloop/skills/。"
    ),
    FIRST_PURSUIT: (
        "💡 Pursuit = 可修订的目标。它会随对话演进而调整 scope,不是一次性 task。"
        "修订历史在 pursuit.revisions[] 里可查。"
    ),
    FIRST_ATOM_COMPOSE: (
        "💡 你现在有一个 role 了。提醒一下:role = 灵魂层 7 文件,atom = 公共能力池 —— "
        "这意味着多个 role 可以共用同一个 atom(比如 write_ppt 既能给产品经理用,也能给战略规划师用)。"
    ),
    FIRST_LONG_TOOL: (
        "💡 这个工具跑得比较久。我把进度流出来了 —— "
        "如果觉得啰嗦,`/verbose` 可以切到精简(只显示新增步骤)。"
    ),
}


class OnboardingFlag(str):
    """类型化 hint flag 名(继承 str 便于 yaml 序列化)。"""
    pass
