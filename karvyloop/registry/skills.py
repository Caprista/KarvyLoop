"""SKILL.md 加载 → Tool 包装（registry/skills.py）。

规格：docs/modules/registry.md §3 skills.py + §4 约束。
渐进披露：只 frontmatter 进 schema(常驻上下文);正文 `call` 时才读。
frontmatter 解析遵循极简 YAML 子集(避免拉 PyYAML;M0 用极简手解)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from karvyloop.capability import Mode
from karvyloop.schemas import CapabilityToken

from .tool import build_tool


_SKILL_FILENAME = "SKILL.md"

# 极简 frontmatter 解析:首行 --- 起,末行 --- 止
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class SkillFrontmatter:
    """SKILL.md frontmatter 解析结果(只常驻字段;正文按需读)。

    **agentskills.io 对齐**(2026-06-16 起的 v1.5 演进):
    - `name` / `description` 标准必填 — 已有
    - `version` 标准可选 — v1.5 加; 缺省空串(向后兼容旧 SKILL.md)
    - 我们扩展字段 `signature` / `when_to_use` / `allowed_tools` / `scope` /
      `arguments` — 全部 v1.5 保留, 不破坏 agentskills.io 兼容
    """
    name: str
    description: str
    version: str = ""  # agentskills.io 标准可选字段(v1.5+); 旧 SKILL.md 缺省空
    signature: str = ""  # sig 哈希(SkillIndex 用);M1.5 起写入,旧 SKILL.md 缺时为空
    when_to_use: str = ""
    allowed_tools: list[str] = None  # type: ignore
    scope: str = "user"  # 'user' | 'domain'
    arguments: list[dict] = None  # type: ignore
    # #2 §13:结果可复用性。'dynamic'(默认)=结果会变,命中**重跑**不回放;'stable'=语义稳定可回放。
    # 旧 SKILL.md 缺省 → dynamic:让历史"存了答案"的技能也走重跑,不再吐 stale(回填式修复)。
    result_reuse: str = "dynamic"
    raw: dict = None  # type: ignore


def _parse_simple_yaml(text: str) -> dict:
    """极简 YAML 子集解析。

    支持:
      - key: value  （字符串）
      - key: [a, b, c]  （行内列表）
      - 块列表:
          arguments:
            - name: foo
              type: string
              required: true
            - name: bar

    不引 PyYAML;M0 范围足够。失败返回空 dict。
    """
    out: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        # 块列表(支持两形态:`- key: val`(dict 项)与 `- Scalar`(标量项,如标准 allowed-tools))
        if not v and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
            block: list = []
            i += 1
            current: Optional[dict] = None  # 仅 dict 项用于挂延续字段
            while i < len(lines):
                ln = lines[i]
                stripped = ln.lstrip()
                if not stripped:
                    i += 1
                    continue
                if stripped.startswith("- "):
                    if current:
                        block.append(current)
                        current = None
                    item_body = stripped[2:].strip()
                    if ":" in item_body:
                        current = {}
                        kk, _, vv = item_body.partition(":")
                        current[kk.strip()] = vv.strip()
                    else:
                        # 标量项(`- Read`)→ 直接进列表(去引号)
                        block.append(item_body.strip('"').strip("'"))
                elif stripped.startswith("#"):
                    pass
                elif ":" in stripped and not ln.startswith(" "):
                    # 回到顶层 key,跳出
                    break
                else:
                    # 列表项的延续字段(只对 dict 项)
                    if current is not None and ":" in ln:
                        kk, _, vv = ln.strip().partition(":")
                        current[kk.strip()] = vv.strip()
                i += 1
            if current:
                block.append(current)
            out[k] = block
            continue
        # 行内列表
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                out[k] = []
            else:
                out[k] = [
                    item.strip().strip('"').strip("'")
                    for item in inner.split(",")
                    if item.strip()
                ]
        elif v.startswith('"') and v.endswith('"'):
            out[k] = v[1:-1]
        elif v.startswith("'") and v.endswith("'"):
            out[k] = v[1:-1]
        else:
            out[k] = _coerce_scalar(v)
        i += 1
    return out


def _coerce_scalar(v: str) -> Any:
    """字符串值归一: 'true'/'false' → bool,纯数字 → int/float。"""
    s = v.strip()
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_frontmatter(skill_path: Path) -> tuple[SkillFrontmatter, str]:
    """读 SKILL.md,返回 (frontmatter, body)。

    - 没有 frontmatter → frontmatter 为空(空 name/description)
    - 解析失败 → frontmatter 为空(让上层 fail-closed 拒)
    """
    text = skill_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return SkillFrontmatter(name="", description=""), text
    fm_raw = _parse_simple_yaml(m.group(1))
    body = m.group(2)
    # Agent Skills 开放标准用连字符键(`allowed-tools` / `when-to-use`);我们历史用下划线。
    # 采标准、不另造:连字符键作为别名读入(Q5 通用基建必借),让第三方 SKILL.md 直接可用。
    allowed = fm_raw.get("allowed_tools")
    if allowed is None:
        allowed = fm_raw.get("allowed-tools", [])
    when = fm_raw.get("when_to_use") or fm_raw.get("when-to-use", "")
    fm = SkillFrontmatter(
        name=fm_raw.get("name", ""),
        description=fm_raw.get("description", ""),
        version=fm_raw.get("version", ""),
        signature=fm_raw.get("signature", ""),
        when_to_use=when,
        allowed_tools=allowed or [],
        scope=fm_raw.get("scope", "user"),
        arguments=fm_raw.get("arguments", []) or [],
        result_reuse=(fm_raw.get("result_reuse") or "dynamic").strip().lower(),
        raw=fm_raw,
    )
    return fm, body


def _arguments_to_schema(arguments: list[dict]) -> dict:
    """把 arguments 列表(简易 [{name, type, description, required}])转 JSON Schema。"""
    if not arguments:
        return {"type": "object", "properties": {}}
    properties = {}
    required = []
    for arg in arguments:
        if not isinstance(arg, dict) or "name" not in arg:
            continue
        name = arg["name"]
        # 字符串 "true"/"false" 归一为 bool
        req_raw = arg.get("required", False)
        if isinstance(req_raw, str):
            req = req_raw.strip().lower() in ("true", "1", "yes")
        else:
            req = bool(req_raw)
        properties[name] = {
            "type": arg.get("type", "string"),
            "description": arg.get("description", ""),
        }
        if req:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def load_skill(
    skill_md: Path,
    *,
    token: Optional[CapabilityToken] = None,
) -> Any:
    """加载单个 SKILL.md → Tool(经 build_tool 工厂;HR-1)。

    - frontmatter 进 input_schema(常驻)
    - 正文 `call` 时才读(渐进披露,内存里只存 path)
    """
    fm, _body = parse_frontmatter(skill_md)
    if not fm.name:
        raise ValueError(f"SKILL.md {skill_md} 缺 frontmatter.name")
    scope = (fm.scope or "user").lower()
    required_mode = Mode.WORKSPACE_WRITE if scope == "domain" else Mode.READ_ONLY

    async def _call(inp: dict, token: CapabilityToken, sandbox: Any) -> dict:
        # **渐进披露**:此处才读 body + 把 fm 完整内容喂出
        _, body = parse_frontmatter(skill_md)
        return {
            "name": fm.name,
            "body": body,
            "input": inp,
        }

    return build_tool(
        name=fm.name,
        description=fm.description or fm.when_to_use or f"skill {fm.name}",
        input_schema=_arguments_to_schema(fm.arguments),
        call=_call,
        # 技能通常只读 + 不可并发
        is_read_only=lambda inp: scope == "user",
        is_concurrency_safe=lambda inp: False,  # 技能可能有副作用
        required_mode=required_mode,
    )


def load_skills_dir(
    dir_: Path,
    *,
    token: Optional[CapabilityToken] = None,
    recursive: bool = False,
) -> list[Any]:
    """从目录加载所有 SKILL.md → Tool 列表。

    子目录递归:`{dir_}/<skill>/SKILL.md`(spec 默认布局)。
    """
    dir_ = Path(dir_)
    if not dir_.is_dir():
        return []
    if recursive:
        candidates = sorted(dir_.glob(f"*/{_SKILL_FILENAME}"))
    else:
        candidates = sorted(dir_.glob(f"*/{_SKILL_FILENAME}"))
    out: list[Any] = []
    for p in candidates:
        try:
            out.append(load_skill(p, token=token))
        except (ValueError, OSError):
            # 缺 frontmatter / 解析失败 → 跳过(让 registry.fail-closed 兜底)
            continue
    return out


__all__ = [
    "SkillFrontmatter",
    "parse_frontmatter",
    "load_skill",
    "load_skills_dir",
]
