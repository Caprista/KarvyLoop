"""决策链：9 步固定顺序短路（capability/decision.py）。

规格：docs/modules/capability.md §2.1（HR-2，**与模式和规则**解耦的硬约束）。

固定顺序（不可重排）：
  1. denied_tools 一票否决（Full 也照 Deny）
  2. deny 规则
  3. hook override：Deny 立即 Deny；Ask 强制 ask；Allow 不直接放行（仍受 ask 约束）
  4. ask 规则
  5. 工具自检 tool_self_check
  6. 安全检查（`.git`/`.claude`/`rm -rf /` 等）→ **免疫 bypass/Full 模式**
  7. bypass/Full 模式 → Allow
  8. allow 规则 / 模式 ≥ 工具下限 → Allow
  9. 默认 → Ask（fail-closed）

返回 Decision（判别联合）；**绝不抛异常**（错误也走 Deny+reason，AC9）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .pathnorm import is_within_workspace
from .policy import Mode, PermissionContext, Rule, Verdict, _norm


# ---- 决策判别联合（AC9：所有路径都必须有 reason）----

@dataclass(frozen=True)
class Allow:
    reason: str
    updated_input: Optional[dict] = None  # 决策链可改写 input（如裁剪路径）


@dataclass(frozen=True)
class Ask:
    message: str
    reason: str


@dataclass(frozen=True)
class Deny:
    message: str
    reason: str


@dataclass(frozen=True)
class Passthrough:
    """决策链不适用（前置校验已通过）"""
    reason: str = "passthrough"


Decision = Union[Allow, Ask, Deny, Passthrough]


# ---- 主题抽取（spec §2.3）----

def _extract_subject(tool: str, input: dict) -> str:
    """按工具类型从 input 取 subject 字段。"""
    if not input:
        return ""
    # 优先级：path / file_path > url > command > pattern
    for k in ("path", "file_path", "filepath", "url", "command", "pattern", "target"):
        v = input.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


# ---- 安全检查（spec §2.1 step 6，免疫 bypass）----

def _safety_check(ctx: PermissionContext) -> Optional[Decision]:
    """返回 Deny/Ask 表示要拦；None 表示通过。

    触发条件（保守起见，先列最重要的）：
      - 写 `.git/` / `.claude/` 内部目录
      - 命令首词是 rm 且含 `-rf /` 或 `rm -fr /`
      - 网络访问 *.anthropic 之外的（仅占位,真规则在 M1+）
    """
    tool = _norm(ctx.tool)
    inp = ctx.input or {}
    subject = _extract_subject(tool, inp)

    # 写/编辑/运行涉及危险目录
    if tool in ("write_file", "edit_file", "run_command", "delete_file"):
        # `.git` / `.claude` 任意位置出现 → 拦
        for marker in ("/.git/", "/.git", "\\.git", "/.claude/", "/.claude", "\\.claude"):
            if marker in subject:
                return Deny(
                    message=f"拒绝写 {marker} 内部目录",
                    reason="safety:internal_dir(.git|.claude)",
                )

    # rm -rf / 家族
    if tool == "run_command":
        cmd = subject
        s = cmd.replace(" ", "")
        if "rm" in s.split()[0:1] or cmd.lstrip().startswith("rm"):
            # 检测 `rm -rf /` 或 `rm -fr /`
            tokens = cmd.split()
            if "rm" in tokens:
                i = tokens.index("rm")
                window = " ".join(tokens[i : i + 5])
                if ("-rf" in window or "-fr" in window) and "/" in window:
                    return Deny(message="拒绝执行 rm -rf /", reason="safety:rm_rf_root")

    return None


# ---- 规则匹配（first-match）----

def _first_match(rules: list[Rule], tool: str, subject: str) -> Optional[Rule]:
    for r in rules:
        if r.matches_tool(tool) and r.matches_subject(subject):
            return r
    return None


# ---- 9 步决策链 ----

def authorize(ctx: PermissionContext) -> Decision:
    """按 §2.1 固定顺序短路判定。"""
    tool = _norm(ctx.tool)
    subject = _extract_subject(tool, ctx.input or {})

    # 1) denied_tools 一票否决
    if tool in (t.lower() for t in ctx.denied_tools):
        return Deny(
            message=f"工具 {tool} 在 denied_tools 中",
            reason="denied_tools:hit",
        )

    # 2) deny 规则
    r = _first_match(ctx.deny_rules, tool, subject)
    if r is not None:
        return Deny(message=f"deny 规则命中 {r.tool}({r.subject})", reason="rule:deny")

    # 3) hook override
    if ctx.hook == Verdict.DENY:
        return Deny(message="hook 强制拒绝", reason="hook:deny")
    # hook=Ask 留到 step 4 一并处理；hook=Allow 不直接放行

    # 4) ask 规则（hook=Ask 也强制走 ask）
    r = _first_match(ctx.ask_rules, tool, subject)
    if r is not None or ctx.hook == Verdict.ASK:
        if ctx.prompter is None:
            return Deny(message="ask 命中但无 prompter", reason="ask:no_prompter")
        # 有 prompter → ask；M0 直接降级为 Deny（不真去问人）
        return Deny(
            message=f"ask 规则命中 {r.tool if r else '(hook)'}（M0 无交互 prompter）",
            reason="ask:no_prompter",
        )

    # 5) 工具自检
    if ctx.tool_self_check == Verdict.DENY:
        return Deny(message="工具自检拒绝", reason="tool_self_check:deny")
    if ctx.tool_self_check == Verdict.ASK:
        if ctx.prompter is None:
            return Deny(message="工具自检要求 ask 但无 prompter", reason="ask:no_prompter")
        return Deny(message="工具自检要求 ask", reason="ask:no_prompter")

    # 6) 安全检查（**免疫 bypass** —— 放在 step 7 之前）
    s = _safety_check(ctx)
    if s is not None:
        return s

    # 7) bypass/Full 模式
    if ctx.mode == Mode.FULL:
        return Allow(reason="mode:full")

    # 8) allow 规则 / 模式 ≥ 工具下限
    r = _first_match(ctx.allow_rules, tool, subject)
    if r is not None:
        return Allow(reason=f"rule:allow:{r.tool}({r.subject})")
    from .policy import required_mode
    if ctx.mode >= required_mode(tool):
        # 写动作额外加路径边界校验
        if tool in ("write_file", "edit_file") and ctx.workspace_root and subject:
            if not is_within_workspace(subject, ctx.workspace_root):
                return Deny(
                    message=f"路径 {subject} 越出工作区 {ctx.workspace_root}",
                    reason="pathnorm:out_of_workspace",
                )
        return Allow(reason=f"mode:{ctx.mode.value} >= required:{required_mode(tool).value}")

    # 9) 默认 → Ask（fail-closed）
    if ctx.prompter is None:
        return Deny(message="无规则命中且模式不足（默认 ask，无 prompter）", reason="default:ask_no_prompter")
    return Deny(message="无规则命中且模式不足", reason="default:ask_no_prompter")
