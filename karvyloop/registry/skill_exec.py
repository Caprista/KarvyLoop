"""skill_exec — 在沙箱里执行技能携带的脚本(P0-c)。

Agent Skills 标准把脚本放进 `scripts/`;SKILL.md 指示何时跑哪个。"执行技能" = 把那个脚本
**在沙箱里**按技能的信任级 + allowed-tools 派生的能力跑起来。

第三方脚本 = 别人的代码,**必须**经 bubblewrap + 能力令牌(最小授予,见 skill_grants)——
这正是"安全是地基"在"用第三方生态"上的兑现:能用别人的技能,别人的脚本却被关进笼子。

安全:
- 脚本路径必须落在 skill_dir 内(防 `../` 越界引宿主可执行)。
- 工作区(workspace)是调用方给的 scratch 目录;token 只放开 skill_dir(只读)+ workspace(读写)。
- 沙箱不可用(非 Linux / 无 bwrap)→ 明确报错,绝不退化成无隔离直接跑(StubSandbox 已 fail-closed)。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from karvyloop.capability.skill_grants import token_for_skill
from karvyloop.registry.skills import parse_frontmatter
from karvyloop.sandbox.exec_result import ExecResult


def mark_skill_verified(skill_dir: str) -> bool:
    """把一个技能标成"已在本机成功跑通一次"(btw-1:外部技能完整成功调用一次 → 已沉淀)。

    往 SKILL.md frontmatter 写 `verified_at: <ts>`(已有则不重复写)。这是外部技能的"验证门"——
    必须真在沙箱里跑通,才从"待验证"升"已沉淀"。返回是否新写入。
    """
    p = Path(skill_dir) / "SKILL.md"
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    if "verified_at" in text:
        return False  # 已标过
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)(.*)$", text, re.DOTALL)
    line = f"verified_at: {int(time.time())}"
    if not m:
        p.write_text(f"---\n{line}\n---\n{text}", encoding="utf-8")
        return True
    head, fm, close, body = m.groups()
    p.write_text(head + fm + "\n" + line + close + body, encoding="utf-8")
    return True

# 脚本后缀 → 解释器(沙箱内调用)
_INTERP = {".py": ["python3"], ".sh": ["bash"], ".js": ["node"], ".ts": ["node"]}


def resolve_script(skill_dir: Path, script_rel: str) -> Path:
    """把相对脚本路径安全解析到 skill_dir 内;越界 → ValueError。"""
    skill_dir = Path(skill_dir).resolve()
    target = (skill_dir / script_rel).resolve()
    if target != skill_dir and not target.is_relative_to(skill_dir):
        raise ValueError(f"脚本路径越界(不在技能目录内):{script_rel}")
    if not target.is_file():
        raise FileNotFoundError(f"脚本不存在:{script_rel}")
    return target


async def run_skill_script(
    skill_dir: str,
    script_rel: str,
    args: Optional[list[str]] = None,
    *,
    sandbox: Any,
    workspace: str,
    ttl_seconds: float = 600.0,
    timeout_s: float = 120.0,
    trusted: Optional[bool] = None,
    net: bool = False,
) -> ExecResult:
    """在沙箱里跑技能的一个脚本,token 由技能信任级 + allowed-tools 派生。

    trusted=None → 据 frontmatter 自动判(第三方=不可信=最小授予)。显式传 True/False 可覆盖。
    net=True → 用户显式授权该技能联网(默认拒;授权是人的决定)。
    """
    sd = Path(skill_dir).resolve()
    target = resolve_script(sd, script_rel)
    fm, _body = parse_frontmatter(sd / "SKILL.md")
    # 完整性锁(生产 run 防线):untrusted 第三方技能执行前查篡改 —— 改过的脚本绝不进沙箱。
    # 这是"加载前校验"在**执行**路径的兜底,不只靠索引层(索引可能被绕、直接 run 也得挡)。
    if trusted is not True and (fm.raw or {}).get("trust") == "untrusted":
        from karvyloop.registry.skill_lock import verify_lock
        status, detail = verify_lock(sd.parent, sd.name)
        if status == "mismatch":
            raise ValueError(f"技能「{sd.name}」完整性校验失败,拒绝执行(第三方代码被改动/损坏):{detail}")
    token = token_for_skill(fm, skill_dir=str(sd), workspace=str(workspace),
                            ttl_seconds=ttl_seconds, trusted=trusted, net=net)
    interp = _INTERP.get(target.suffix.lower(), ["bash"])
    argv = interp + [str(target)] + [str(a) for a in (args or [])]
    return await sandbox.exec(argv, token=token, cwd=str(workspace), timeout_s=timeout_s)


__all__ = ["run_skill_script", "resolve_script", "mark_skill_verified"]
