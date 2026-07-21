"""决策链：9 步固定顺序短路（capability/decision.py）。

规格：docs/modules/capability.md §2.1（HR-2，**与模式和规则**解耦的硬约束）。

固定顺序（不可重排）：
  1. denied_tools 一票否决（Full 也照 Deny）
  2. deny 规则
  3. hook override：Deny 立即 Deny；Ask 强制 ask；Allow 不直接放行（仍受 ask 约束）
  4. ask 规则
  5. 工具自检 tool_self_check
  6. 安全检查（`.git`/`.claude`/`rm -rf /` 等）→ **免疫 bypass/Full 模式**
  6.5. 域 deontic 确定性硬闸（scope 武装时;交易/删除/外发类 + 点名工具的 forbid 真拦）→ **免疫 Full**
  7. bypass/Full 模式 → Allow
  8. allow 规则 / 模式 ≥ 工具下限 → Allow
  9. 默认 → Ask（fail-closed）

返回 Decision（判别联合）；**绝不抛异常**（错误也走 Deny+reason，AC9）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .pathnorm import is_within_workspace, resolve_in_workspace
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

    # 敏感路径硬地板(fs_grants):密钥/ssh/凭据库 —— 读写运行一律拒,**免疫 bypass 与任何授权**。
    # 与 rm -rf 检测同级:这是"拿到即约等于拿到你的账号"的类别,谁批都不行。
    if tool in ("read_file", "write_file", "edit_file", "run_command", "delete_file") and subject:
        from .fs_grants import is_sensitive_path
        if is_sensitive_path(subject):
            return Deny(message=f"敏感路径(密钥/凭据类),永不放行:{subject}",
                        reason="safety:sensitive_path")

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

    # 6.5) 域 deontic 确定性硬闸(docs/54 B1 Top1)—— 与 fs_grants 敏感地板同层,**免疫 FULL**。
    # scope 由 forge 在 run 前从 persona 的 deontic_forbid 武装(contextvar,run 完复位);
    # 未武装(私聊/CLI/无域)= no-op。只拦确定性匹配到的:高危类别(交易/删除/外发)
    # + forbid 点名的真实工具名(精确匹配,C-03;点名的只读工具也拦——用户意图明确优先),
    # 纯语义 forbid 仍走 prompt 软护栏(分层诚实,见 deontic_gate 模块头)。
    from .deontic_gate import check_active as _deontic_check
    hit = _deontic_check(ctx.tool, ctx.input or {})   # 传原始名:camelCase 切分需要大小写信息
    if hit is not None:
        _dom = f"「{hit.domain}」" if hit.domain else ""
        return Deny(
            message=(f"业务域{_dom}治理禁止:「{hit.source}」—— 本次调用确定性命中"
                     f"({hit.detail}),已拦截。这是域的 deontic 硬规则,不是模型自觉。"),
            reason=f"deontic:forbid:{hit.category}",
        )

    # 7) bypass/Full 模式
    if ctx.mode == Mode.FULL:
        return Allow(reason="mode:full")

    # 8) allow 规则 / 模式 ≥ 工具下限
    r = _first_match(ctx.allow_rules, tool, subject)
    if r is not None:
        return Allow(reason=f"rule:allow:{r.tool}({r.subject})")
    from .policy import required_mode
    if ctx.mode >= required_mode(tool):
        # 写动作额外加路径边界校验;工作区外但**授权台账放行过**的路径 → 行(fs_grants)
        if tool in ("write_file", "edit_file") and ctx.workspace_root and subject:
            # 相对 subject 先锚到 workspace_root 解析成绝对:is_within_workspace 按 root 拼、
            # 台账 allows 却按进程 CWD resolve —— 同一 subject 两个锚,判定与放行看的不是
            # 同一条路径。块首统一解析,后续判定与 allows 都用同一绝对路径。
            subject_abs = resolve_in_workspace(subject, ctx.workspace_root)
            if not is_within_workspace(subject_abs, ctx.workspace_root):
                from .fs_grants import get_store
                _st = get_store()
                if _st is None or not _st.allows(subject_abs, "write"):
                    return Deny(
                        message=f"路径 {subject_abs} 越出工作区 {ctx.workspace_root}",
                        reason="pathnorm:out_of_workspace",
                    )
        return Allow(reason=f"mode:{ctx.mode.value} >= required:{required_mode(tool).value}")

    # 9) 默认 → Ask（fail-closed）
    if ctx.prompter is None:
        return Deny(message="无规则命中且模式不足（默认 ask，无 prompter）", reason="default:ask_no_prompter")
    return Deny(message="无规则命中且模式不足", reason="default:ask_no_prompter")
