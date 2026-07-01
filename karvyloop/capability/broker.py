"""能力 broker 门面（capability/broker.py）。

规格：docs/modules/capability.md §3 / §2.5。
对外三个函数：
  - `derive_min_capabilities(task)` → 规则版最小令牌（M0：模板匹配，不上模型）
  - `check(token, action)` → 决策链判定（包装 decision.authorize + token 校验）
  - `classify(tool, input)` → 影子分类器占位（M1+ 实现）
"""

from __future__ import annotations

import re
from typing import Optional

from karvyloop.schemas import Capability, CapabilityToken

from .decision import Decision, authorize
from .pathnorm import is_within_workspace
from .policy import Mode, PermissionContext, Verdict
from .token import has_grant, mint, verify


# ---- 任务意图 → 最小令牌（规则版，M0）----

_PATH_HINT = re.compile(r"(?:整理|分类|处理|整理一下|扫一下|清一下)?\s*['\"]?(/[\w./-]+)['\"]?")


def derive_min_capabilities(task: str) -> CapabilityToken:
    """从任务文本推导最小能力令牌。

    规则版（M0）：
      - 命中 `/path/...` → fs:<path> read+write
      - 含"网络/下载/抓取" → net:* read
      - 含"git push" → net:github.com connect
      - 默认 → fs:. read
    """
    grants: list[Capability] = []

    m = _PATH_HINT.search(task)
    target = m.group(1) if m else "."

    grants.append(Capability(resource=f"fs:{target}", ops=["read", "write"]))

    if any(k in task for k in ("网络", "下载", "抓取", "http", "url")):
        grants.append(Capability(resource="net:*", ops=["read"]))
    if "git push" in task or "提交并推送" in task or "推送到远端" in task:
        grants.append(Capability(resource="net:github.com", ops=["connect"]))

    return mint(task_id=f"derived:{hash(task) & 0xffffff:x}", grants=grants, ttl_seconds=1800.0)


# ---- check：把 action 转成 PermissionContext 再走决策链 ----

def _action_to_ctx(token: CapabilityToken, action: dict) -> PermissionContext:
    """`action` 形如 {tool, input, workspace_root?, mode?, ask?, ...}"""
    return PermissionContext(
        tool=action["tool"],
        input=action.get("input", {}),
        mode=Mode(action.get("mode", "read_only")),
        ask=bool(action.get("ask", False)),
        workspace_root=action.get("workspace_root"),
    )


def check(token: CapabilityToken, action: dict) -> Decision:
    """对一个 action 走决策链。

    流程：
      1) 验令牌（过期 → Deny）
      2) 查令牌覆盖（resource,op）→ 不覆盖 → Deny
      3) 决策链 → Decision
    """
    # 1) 令牌状态
    try:
        verify(token)
    except ValueError as e:
        from .decision import Deny as _D
        return _D(message=str(e), reason="token:expired")

    # 2) 令牌覆盖（粗粒度：tool → resource 映射在 M1+ 完善；M0 只对 path 类检查）
    tool = action["tool"]
    inp = action.get("input") or {}
    target = inp.get("path") or inp.get("file_path") or ""
    if tool in ("write_file", "edit_file") and target:
        # 令牌里的 fs 资源视为 workspace root
        fs_grants = [g for g in token.grants if g.resource.startswith("fs:")]
        ok = any(
            g.ops and "write" in g.ops and is_within_workspace(target, g.resource[3:])
            for g in fs_grants
        )
        if not ok:
            from .decision import Deny as _D
            return _D(
                message=f"令牌未覆盖 {target} 写权限",
                reason="token:not_granted",
            )
    elif tool == "read_file" and target:
        fs_grants = [g for g in token.grants if g.resource.startswith("fs:")]
        ok = any(
            g.ops and "read" in g.ops and is_within_workspace(target, g.resource[3:])
            for g in fs_grants
        )
        if not ok:
            from .decision import Deny as _D
            return _D(
                message=f"令牌未覆盖 {target} 读权限",
                reason="token:not_granted",
            )
    elif tool in ("network", "process_spawn"):
        from .decision import Deny as _D
        return _D(
            message=f"工具 {tool} 在 M0 由决策链决定（令牌粗粒度未放行）",
            reason="token:not_granted",
        )

    # 3) 决策链
    return authorize(_action_to_ctx(token, action))


# ---- 影子分类器（M1+ 占位）----

def classify(tool: str, input: dict) -> str:
    """M0 占位：返回 'allow'。M1+ 接入 fast/thinking 两阶段。"""
    return "allow"
