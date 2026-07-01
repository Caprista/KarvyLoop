"""tests/_scan.py — OS-portable source scanner (replaces shell `grep` in K-lock tests).

Why: the K-lock source-scan tests shelled out to `grep`, which doesn't exist on Windows
→ the scan silently no-op'd there (false pass), while on Linux it matched even the
**docstrings/comments that document the forbidden pattern** (false fail — e.g. app.py's
module docstring listing "绝不碰 Courier.send("). A Python-native, token-aware scan is
OS-independent *and* only looks at real code, so the K invariant is enforced identically
everywhere.
"""
from __future__ import annotations

import io
import pathlib
import re
import tokenize

# 把这些 token 视作"非代码"(扫调用模式时该忽略):注释 + 各类字符串/docstring/f-string。
_NONCODE = {tokenize.COMMENT, tokenize.STRING}
for _n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):   # 3.12+ f-string token 拆分
    _t = getattr(tokenize, _n, None)
    if _t is not None:
        _NONCODE.add(_t)


def _blank_noncode(text: str) -> str:
    """把注释 + 字符串/docstring 的字符抹成空格(保留行号/列位),只留下"代码"。
    解析失败(语法不全)→ 原样返回(退化为 raw 扫描,宁可多报不漏报)。"""
    lines = text.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return text
    for tok in toks:
        if tok.type not in _NONCODE:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        for r in range(sr, er + 1):
            if r - 1 >= len(lines):
                break
            line = lines[r - 1]
            a = sc if r == sr else 0
            b = ec if r == er else len(line)
            lines[r - 1] = line[:a] + " " * (b - a) + line[b:]
    return "\n".join(lines)


def grep_py(pattern: str, root, *, skip_comments: bool = True) -> list[str]:
    """Scan `.py` files under `root` (file or dir) for `pattern`; return grep-style
    "path:lineno:line" matches (reporting the ORIGINAL line).

    `skip_comments=True`(默认):只在**代码**里匹配 —— 注释 / 字符串 / docstring 里出现该模式
    不算违规(修正旧 grep 把"绝不碰 Courier.send("这句文档也判成违规的假阳性)。
    `skip_comments=False`:扫原始文本(连字符串/注释也算 —— 查真 key、URL 字面量这类用)。
    """
    rx = re.compile(pattern)
    root = pathlib.Path(root)
    files = ([root] if root.is_file()
             else [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts])
    out: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        orig = text.splitlines()
        scan = _blank_noncode(text).splitlines() if skip_comments else orig
        for i, line in enumerate(scan, 1):
            if rx.search(line):
                out.append(f"{f}:{i}:{orig[i - 1] if i - 1 < len(orig) else line}")
    return out
