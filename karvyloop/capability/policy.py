"""策略：模式/规则表/下限表（capability/policy.py）。

规格：docs/modules/capability.md §2.1-2.3。

不变量：
  - Mode 是 3 级（READ_ONLY < WORKSPACE_WRITE < FULL）+ 独立 `ask` 标志
  - 工具下限表 `tool_requirements: dict[str, Mode]`；未声明 → 默认 FULL（HR-1）
  - 规则语法 `tool(subject)`；subject 来自 input 的
    `command / path / file_path / url / pattern`；first-match
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Mode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL = "full"

    def __ge__(self, other: "Mode") -> bool:
        order = [Mode.READ_ONLY, Mode.WORKSPACE_WRITE, Mode.FULL]
        return order.index(self) >= order.index(other)


class Verdict(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# 每工具下限（HR-1）。未声明 → FULL（最严）。
# 来源：docs/modules/capability.md §2.2 + 后续模块补全。
DEFAULT_TOOL_REQUIREMENTS: dict[str, Mode] = {
    "read_file": Mode.READ_ONLY,
    "list_dir": Mode.READ_ONLY,
    "search_code": Mode.READ_ONLY,
    # 只读联网 = 基础能力(Hardy):知识库没命中就该能搜/读网,maker/checker 都给。
    # 它们走进程内 httpx(不进沙箱子进程),只读、无副作用 → READ_ONLY 下限。
    "web_search": Mode.READ_ONLY,
    "web_fetch": Mode.READ_ONLY,
    # 报销确定性算术(coding/tools/reconcile.py):纯计算、不碰文件/网络/沙箱、无副作用 →
    # READ_ONLY 下限(否则新工具默认 FULL 会被闸拦、报销员一调就 capability_denied)。
    "reconcile_receipt": Mode.READ_ONLY,
    "run_command": Mode.WORKSPACE_WRITE,
    "write_file": Mode.WORKSPACE_WRITE,
    "edit_file": Mode.WORKSPACE_WRITE,
    # create_atom(docs/02 §15.5):role 干活时无 atom 可用→造一个。改的是公共原子库(在做事过程中,
    # 同 run_command/write_file 的写语义)→ WORKSPACE_WRITE 下限:maker/forge 放行、只读 checker 拦;
    # 安全靠下游(provisional + 合并闸 + 沉淀需认可)兜,不在这里一票 FULL 拒掉(否则 role 想造也造不了)。
    "create_atom": Mode.WORKSPACE_WRITE,
    # instantiate_domain_template(karvy/self_knowledge.py):小卡指导建 agent 后替用户一键
    # 开模板域。写的是域/角色注册表(用户已在对话里拍板选定模板)→ 同 create_atom 的
    # WORKSPACE_WRITE 语义;只读 checker 仍拦。
    "instantiate_domain_template": Mode.WORKSPACE_WRITE,
    "git_commit": Mode.WORKSPACE_WRITE,
    "network": Mode.FULL,
    "process_spawn": Mode.FULL,
    "memory_write": Mode.FULL,
    "skill_install": Mode.FULL,
}


def required_mode(tool: str) -> Mode:
    """未声明的工具 → FULL（最严；HR-1）。

    例外:`mcp_*`(用户在配置里显式接入的 MCP 工具)是**调用方注入的可信工具**,
    不该因"没在固定表里"被当 FULL 一票拒(那样配了 MCP 也用不了)。给 WORKSPACE_WRITE 下限
    —— 在 maker(forge,WORKSPACE_WRITE)放行,在只读 checker 仍拦住。
    """
    if tool.startswith("mcp_"):
        return Mode.WORKSPACE_WRITE
    return DEFAULT_TOOL_REQUIREMENTS.get(tool, Mode.FULL)


# 工具名 / 规则归一：小写 + strip
def _norm(s: str) -> str:
    return s.strip().lower()


@dataclass(frozen=True)
class Rule:
    tool: str                # 工具名（小写）
    subject: str             # 匹配目标（命令/路径/URL/...；'*' = 任意）
    verdict: Verdict         # ALLOW / ASK / DENY
    # 可选：是否仅在指定模式下生效。None = 不限
    only_mode: Optional[Mode] = None

    def matches_tool(self, tool: str) -> bool:
        return _norm(self.tool) == _norm(tool) or self.tool == "*"

    def matches_subject(self, subject: str) -> bool:
        s, sub = _norm(self.subject), _norm(subject)
        if s == "*":
            return True
        if s.endswith(":*"):
            return sub.startswith(s[:-1])  # "prefix:*" → "prefix:<...>"
        return s == sub


@dataclass
class PermissionContext:
    """决策链的输入。"""

    tool: str
    input: dict
    # 当前模式 + 独立 ask 开关
    mode: Mode = Mode.READ_ONLY
    ask: bool = False
    # prompter：M0 中为可调对象（设 None 则 ask 降级 deny）
    prompter: Optional["Prompter"] = None
    # 规则表（deny/ask/allow 三类）
    denied_tools: list[str] = field(default_factory=list)  # 一票否决
    deny_rules: list[Rule] = field(default_factory=list)
    ask_rules: list[Rule] = field(default_factory=list)
    allow_rules: list[Rule] = field(default_factory=list)
    # hook override
    hook: Optional[Verdict] = None
    # 工具自检返回值（决策链 step 5 用）
    tool_self_check: Optional[Verdict] = None
    # workspace root（用于词法路径判定）
    workspace_root: Optional[str] = None


class Prompter:
    """ask 时的回调。M0 占位：返回 bool。"""
    def ask_user(self, message: str) -> bool:
        raise NotImplementedError("M0 未实装交互式 prompter（无 prompter → ask 降级 deny）")
