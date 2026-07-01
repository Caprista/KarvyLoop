"""atoms/tool_catalog — 真实工具目录 + 原子工具真实性判定(docs/14 §11.1).

**病根**(round2 裁判逮到):导入纯人设 agent 时,能力小标题被合成成"工具名"(如
`validate-geographic-coherence`),但它**根本不在 forge/atom 真能调的工具表里** —— 原子看着有工具、
其实一个都调不动。"能跑(结构建出来)≠ 高可用(真能执行)"。

这里给一个**真实工具目录**(forge 内置工具 + 可注入的 MCP 工具名)+ `classify_atom_tools`:
把原子的 tools 拿去核对 → `executable`(至少一个真工具)/ `advisory`(一个都对不上,只靠人设推理)
+ `unresolved_tools`(对不上的名字)。**只诚实标注,不补全、不造工具、不改执行逻辑**。
"""
from __future__ import annotations

# forge / atom executor 真能调的内置工具(karvyloop/coding/tools/*.py 的 name)。
# MCP 接进来的工具名由调用方通过 extra_known 注入(M2+),不在此硬编码。
BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({
    "run_command", "read_file", "write_file", "edit_file", "web_fetch", "web_search",
})


def _norm(name: str) -> str:
    """轻量归一:小写 + 连字符/空格→下划线 —— 让 'web-search'/'Web Search' 也能认出 web_search。"""
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


_KNOWN_NORM = frozenset(_norm(n) for n in BUILTIN_TOOL_NAMES)


def classify_atom_tools(tools, *, extra_known=()) -> dict:
    """把原子的 tools 核对真实工具目录 → {executable, unresolved_tools}。

    - extra_known:额外认作"真"的工具名(如 MCP 当前会话接进来的),归一后并入。
    - executable = 至少一个 tool 归一后落在(内置 ∪ extra_known)里。
    - 没有任何 tool(纯 prompt 原子)→ executable=False(advisory:只靠人设推理,这是诚实不是 bug)。
    - unresolved_tools:对不上的**原始**名字(保留原样,便于人看是哪些合成名)。
    """
    known = _KNOWN_NORM | frozenset(_norm(n) for n in (extra_known or ()))
    resolved, unresolved = [], []
    for t in (tools or []):
        (resolved if _norm(str(t)) in known else unresolved).append(str(t))
    return {"executable": bool(resolved), "unresolved_tools": unresolved}


__all__ = ["BUILTIN_TOOL_NAMES", "classify_atom_tools"]
