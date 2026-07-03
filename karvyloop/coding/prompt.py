"""coding 提示词（coding/prompt.py）。

规格：docs/modules/forge.md §2.7。
  - 静态段(coding 角色/工具说明) + 哨兵 + 动态段(cwd/git/指令文件)
  - 哨兵发送前被过滤(spec §2.7 + HR-9 闭环)
  - 静态前缀打 cache_control: ephemeral(给网关层用)
  - 多级 char 预算:单指令文件 4K / 总 12K / git diff 50K(UTF-8 char 边界)
  - 指令文件 cwd 向上到 git root + 内容 hash 去重
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional


# 哨兵:动态段的占位符;发送前被实际内容替换,残留哨兵视为错误
BOUNDARY_MARKER = "⟦KARVYLOOP_BOUNDARY⟧"

# 各级预算(char 数)
INSTRUCTION_FILE_MAX = 4 * 1024
INSTRUCTION_TOTAL_MAX = 12 * 1024
GIT_DIFF_MAX = 50 * 1024


def _git_root(start: str) -> Optional[str]:
    """向上找 .git 目录。"""
    p = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _truncate_utf8_chars(text: str, limit: int) -> tuple[str, bool]:
    """char 级截断(不是字节)。返回 (text, truncated)。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _git_diff(root: str, max_chars: int) -> Optional[str]:
    """git diff 摘要(char 截断)。"""
    try:
        r = subprocess.run(
            ["git", "diff", "--no-color", "--no-ext-diff"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None
    if r.returncode != 0:
        return None
    diff = r.stdout
    if not diff:
        return None
    truncated_diff, _ = _truncate_utf8_chars(diff, max_chars)
    return truncated_diff


def collect_instruction_files(cwd: str) -> list[tuple[str, str]]:
    """从 cwd 向上到 git root,找 AGENTS.md / CLAUDE.md 等指令文件。

    返回 [(path, content_hash)]:去重(同 hash 只一次)。
    """
    files: list[tuple[str, str]] = []
    seen_hash: set[str] = set()
    root = _git_root(cwd) or cwd
    cur = os.path.abspath(cwd)
    seen_dirs: set[str] = set()
    while True:
        if cur in seen_dirs:
            break
        seen_dirs.add(cur)
        for name in ("AGENTS.md", "CLAUDE.md", ".cursorrules"):
            p = os.path.join(cur, name)
            if os.path.isfile(p):
                content = open(p, "r", encoding="utf-8", errors="replace").read()
                h = _hash(content)
                if h not in seen_hash:
                    seen_hash.add(h)
                    files.append((p, h))
        if cur == root:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return files


@dataclass
class CodingPrompt:
    """forge 用的 system prompt(可直接喂给 atoms.executor 的 system= 参数)。"""

    static: list[str] = field(default_factory=list)
    dynamic_blocks: list[str] = field(default_factory=list)
    _unfiltered_text: str = ""  # 内部;供测试/调试

    def to_text(self) -> str:
        """拼接静态+动态,中间用 BOUNDARY_MARKER 分隔(供 NDJSON 消费时再过滤)。"""
        parts: list[str] = []
        for s in self.static:
            parts.append(s)
        if self.dynamic_blocks:
            parts.append(BOUNDARY_MARKER)
            for d in self.dynamic_blocks:
                parts.append(d)
        return "\n".join(parts)

    def boundary_index(self) -> Optional[int]:
        """返回哨兵位置(在 text 中);哨兵已被过滤则返回 None。"""
        text = self.to_text()
        idx = text.find(BOUNDARY_MARKER)
        return idx if idx >= 0 else None

    def to_blocks(self) -> list[dict]:
        """网关层用的 block 列表;静态前缀打 cache_control: ephemeral(HR-9 闭环)。"""
        blocks: list[dict] = []
        for i, s in enumerate(self.static):
            blk: dict = {"type": "text", "text": s}
            # 静态最后一段打 cache_control(喂缓存;改进常见的做法)
            if i == len(self.static) - 1 and self.dynamic_blocks:
                blk["cache_control"] = {"type": "ephemeral"}
            blocks.append(blk)
        if self.dynamic_blocks:
            blocks.append({"type": "text", "text": BOUNDARY_MARKER,
                           "cache_control": {"type": "ephemeral"}})
            for d in self.dynamic_blocks:
                blocks.append({"type": "text", "text": d})
        return blocks


def build_coding_prompt(cwd: str, *, extra_static: Optional[list[str]] = None) -> CodingPrompt:
    """组装 coding system prompt。

    流程:
      1. 静态段 = 角色 + 工具说明 + (extra)
      2. 哨兵
      3. 动态段 = cwd / git / 指令文件(去重 + 截断)
    """
    # 时效纪律(atoms/freshness):实时信息必须 web_search 查证,绝不凭训练记忆报数。
    # 惰性导入防潜在环(atoms 包不 import coding,当前无环;进函数体只为最小暴露面)。
    from karvyloop.atoms.freshness import FRESHNESS_DISCIPLINE

    static = [
        "你是 KarvyLoop 的 coding 原子。任务:按用户意图用工具集改代码并验证。",
        "工具集:read_file / write_file / edit_file / run_command / web_search / web_fetch。",
        "纪律:先读后写(HR-4);危险命令前主动 ask;输出结构化(CodingResult)。",
        FRESHNESS_DISCIPLINE,
    ]
    if extra_static:
        static.extend(extra_static)

    dynamic: list[str] = []
    dynamic.append(f"cwd={cwd}")
    gr = _git_root(cwd)
    if gr:
        dynamic.append(f"git_root={gr}")
        diff = _git_diff(gr, GIT_DIFF_MAX)
        if diff:
            dynamic.append(f"git_diff(≤{GIT_DIFF_MAX} chars):\n{diff}")

    # 指令文件:累计截断
    total = 0
    instr = collect_instruction_files(cwd)
    if instr:
        block = ["指令文件(已 hash 去重):"]
        for p, h in instr:
            content = open(p, "r", encoding="utf-8", errors="replace").read()
            content, _ = _truncate_utf8_chars(content, INSTRUCTION_FILE_MAX)
            if total + len(content) > INSTRUCTION_TOTAL_MAX:
                remaining = INSTRUCTION_TOTAL_MAX - total
                if remaining <= 0:
                    break
                content, _ = _truncate_utf8_chars(content, remaining)
            block.append(f"--- {p} (sha={h}) ---\n{content}")
            total += len(content)
        dynamic.append("\n".join(block))

    return CodingPrompt(static=static, dynamic_blocks=dynamic)


__all__ = [
    "CodingPrompt", "build_coding_prompt",
    "BOUNDARY_MARKER", "INSTRUCTION_FILE_MAX", "INSTRUCTION_TOTAL_MAX", "GIT_DIFF_MAX",
    "_truncate_utf8_chars", "_git_root", "_git_diff", "collect_instruction_files",
]
