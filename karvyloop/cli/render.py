"""流式渲染 — 思维链/工具/权限/快脑标注（cli/render.py）。

规格：docs/modules/workbench-cli.md §3 render.py + §4 UX 三原则。
- 可见即信任:思维链/工具调用/权限请求在终端可见可回溯
- 越用越省:快脑命中显式标注"省了 X token"
- 结晶:用户确认弹窗

M0 实现核心:TextDelta/ToolUse/ToolResult/PermissionAsk 全部打到终端;
Terminal → 退出码;usage → 成本行;快脑 → ⚡ 标注;结晶 → 询问。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Optional, TextIO


# ANSI 颜色(M0 简单支持;NO_COLOR 环境变量禁用)
def _use_color() -> bool:
    return sys.stdout.isatty() and not _env_no_color()


def _env_no_color() -> bool:
    import os
    return os.environ.get("NO_COLOR") is not None


class _Style:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"


# ---- 渲染器 ----

@dataclass
class RenderStats:
    """渲染累计状态(测试/上层用)。"""
    text_chars: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    permission_asks: int = 0
    errors: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class Renderer:
    """事件流渲染器。M0 输出:纯文本 + ANSI 颜色。"""

    def __init__(self, out: Optional[TextIO] = None, err: Optional[TextIO] = None,
                 *, color: bool = True):
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.color = color and _use_color()
        self.stats = RenderStats()
        self._cost_line_printed = False

    def _c(self, code: str) -> str:
        return code if self.color else ""

    # ---- 事件分发 ----
    def render(self, ev: Any) -> None:
        """按事件类型分发。未知事件 → 静默(防御性)。

        优先按类名匹配(atoms.executor 真实事件);类名不符时回退
        到 ev.kind 字段(测试 fake 事件 / 未来扩展用)。
        """
        kind = getattr(ev, "kind", None) or type(ev).__name__
        cls = type(ev).__name__
        # 优先:类名匹配 atoms.executor 真实事件
        if "ToolCallEvent" in cls:
            # atoms.executor 真实事件: ev.block = ToolUseBlock(id, name, input)
            block = getattr(ev, "block", None)
            name = getattr(block, "name", "") if block else ""
            inp = getattr(block, "input", {}) if block else {}
            self._tool_use(name or "", inp or {})
            return
        if "ToolResultEvent" in cls:
            self._tool_result(getattr(ev, "result", None))
            return
        if "TerminalEvent" in cls:
            self._terminal(getattr(ev, "reason", None))
            return
        if "TextEvent" in cls or "TextDelta" in cls:
            self._text(getattr(ev, "text", "") or "")
            return
        # 回退:kind 字符串匹配
        if kind in ("text", "text_delta", "assistant_text_delta"):
            self._text(getattr(ev, "text", "") or "")
        elif kind in ("tool_call", "tool_use"):
            self._tool_use(getattr(ev, "name", "") or "", getattr(ev, "input", {}) or {})
        elif kind in ("tool_result",):
            self._tool_result(getattr(ev, "result", None))
        elif kind in ("terminal", "run_end"):
            self._terminal(getattr(ev, "reason", None))
        elif kind in ("permission_ask", "PermissionAsk"):
            self._permission_ask(ev)
        elif kind in ("error",) or cls.endswith("Error") or "Error" in cls:
            self._error(getattr(ev, "code", 0), getattr(ev, "message", ""))
        # 未知事件:静默

    # ---- 各类型渲染 ----
    def _text(self, text: str) -> None:
        if text:
            self.out.write(text)
            self.out.flush()
            self.stats.text_chars += len(text)

    def render_text(self, text: str) -> None:
        """公开便捷方法:直接渲染一段文本(M0 兜底用,非事件流场景)。"""
        self._text(text)
        if text and not text.endswith("\n"):
            self.out.write("\n")
            self.out.flush()

    def _tool_use(self, name: str, input_: dict) -> None:
        self.stats.tool_calls += 1
        # 工具调用前一行:⚙ 名字 · 摘要
        self.out.write("\n")
        # 摘要:取前几个参数
        preview = ""
        if isinstance(input_, dict) and input_:
            # 取第一个 string 参数
            for v in input_.values():
                if isinstance(v, str) and v:
                    preview = v[:60].replace("\n", " ")
                    break
        line = f"  {self._c(_Style.CYAN)}⚙ {name}{self._c(_Style.RESET)}"
        if preview:
            line += f"  {self._c(_Style.DIM)}{preview}{self._c(_Style.RESET)}"
        self.out.write(line + "\n")
        self.out.flush()

    def _tool_result(self, result: Any) -> None:
        self.stats.tool_results += 1
        if result is None:
            return
        is_error = getattr(result, "is_error", False)
        # 成功:不显示原文(可能很大);失败:显示 error 摘要
        if is_error:
            reason = getattr(result, "error_reason", "")
            self.out.write(f"    {self._c(_Style.RED)}✗ {reason}{self._c(_Style.RESET)}\n")
        else:
            self.out.write(f"    {self._c(_Style.GREEN)}✓{self._c(_Style.RESET)}\n")
        self.out.flush()

    def _terminal(self, reason: Any) -> None:
        v = getattr(reason, "value", str(reason))
        ok = (v == "completed")
        marker = "✓" if ok else "✗"
        color = _Style.GREEN if ok else _Style.YELLOW
        self.out.write(f"\n{self._c(color)}{marker} run {v}{self._c(_Style.RESET)}\n")
        self._print_cost_line()
        self.out.flush()

    def _permission_ask(self, ev: Any) -> None:
        self.stats.permission_asks += 1
        # ⚠ 权限请求:工具名 + 摘要
        tool = getattr(ev, "tool", "?")
        subj = getattr(ev, "subject", "")
        self.out.write(
            f"\n  {self._c(_Style.YELLOW)}⚠ 权限请求:{tool}{self._c(_Style.RESET)}"
            f"  {self._c(_Style.DIM)}{subj}{self._c(_Style.RESET)}\n"
        )
        self.out.flush()

    def _error(self, code: Any, message: str) -> None:
        self.stats.errors += 1
        self.err.write(f"{self._c(_Style.RED)}✗ error({code}): {message}{self._c(_Style.RESET)}\n")
        self.err.flush()

    def render_error_with_hint(self, code: Any, message: str, hint: str) -> None:
        """2 行错误格式:先说原因 + 再给建议(M3+ 拍 8 Onboarding wizard)。

        格式:
          ✗ 原因(code): 出了什么事
              → 建议: 下一步做什么

        Why: 单行错误看不明白为什么 + 不知道怎么办。新用户友好。
        """
        self.stats.errors += 1
        self.err.write(
            f"{self._c(_Style.RED)}✗ {message} ({code}){self._c(_Style.RESET)}\n"
        )
        self.err.write(
            f"  {self._c(_Style.DIM)}→ {hint}{self._c(_Style.RESET)}\n"
        )
        self.err.flush()

    # ---- 快脑/结晶/成本 ----
    def fast_brain_note(self, skill_name: str, saved_tokens: int) -> None:
        """⚡ 用了你的技能,省了 X token("越用越省"标注)。"""
        self.out.write(
            f"\n{self._c(_Style.MAGENTA)}⚡ 用了你的技能「{skill_name}」,"
            f"省了 ~{saved_tokens} token{self._c(_Style.RESET)}\n"
        )
        self.out.flush()

    def crystallize_confirm(self, sig_summary: str) -> None:
        """结晶前询问(不偷偷固化;M0 弹字面文案,决策由调用方接 stdin)。"""
        self.out.write(
            f"\n{self._c(_Style.YELLOW)}◆ 建议结晶:技能稳定运行 {sig_summary} 次,"
            f"是否固化?[y/N]{self._c(_Style.RESET)}\n"
        )
        self.out.flush()

    def record_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        """记录用量(供成本行)。"""
        self.stats.input_tokens += input_tokens
        self.stats.output_tokens += output_tokens
        self.stats.cost_usd += cost_usd
        self._print_cost_line()

    def _print_cost_line(self) -> None:
        if self._cost_line_printed:
            return
        if self.stats.input_tokens or self.stats.cost_usd:
            self.out.write(
                f"{self._c(_Style.DIM)}  tokens: in={self.stats.input_tokens} "
                f"out={self.stats.output_tokens}  "
                f"cost: ${self.stats.cost_usd:.4f}{self._c(_Style.RESET)}\n"
            )
            self._cost_line_printed = True


__all__ = ["Renderer", "RenderStats"]
