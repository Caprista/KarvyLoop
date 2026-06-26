"""令牌 → 挂载 / 网络决策的纯逻辑（sandbox/mounts.py）。

平台无关：核心层可用,平台层也用。
"""

from __future__ import annotations

from karvyloop.schemas import CapabilityToken


def mounts_from_token(token: CapabilityToken) -> tuple[list[str], list[str]]:
    """从 token.fs 推导 ro_bind / rw_bind。

    规则：
      - fs:<path> 且 ops 含 'write' → rw
      - fs:<path> 且 ops 仅 'read' → ro
      - fs:<path> 且 ops 为空（通配） → rw
    """
    ro, rw = [], []
    for g in token.grants:
        if g.resource.startswith("fs:"):
            path = g.resource[3:]
            (rw if (not g.ops or "write" in g.ops) else ro).append(path)
    return ro, rw


def read_only_token(token: CapabilityToken) -> CapabilityToken:
    """派生一个**去掉所有 fs 写权限**的 token —— 给独立验收者(checker)用。

    docs/00 §0.6:独立 checker 必须**只核验不修改**。此前只砍 write/edit 工具,但 bash(run_command)
    仍能写 = loophole。这里在**能力层**堵死:每个 `fs:` grant 去掉 'write'(空 ops=通配可写 → 显式
    设 ['read'])→ `mounts_from_token` 把工作区算进 ro → bubblewrap `--ro-bind` → **bash 也写不动**。
    非 fs grant(exec/net)不动(checker 仍要跑测试)。
    """
    grants = getattr(token, "grants", None)
    if grants is None:
        return token  # 非标准 token(测试桩等)→ 原样返回,不崩
    new_grants = []
    for g in grants:
        if g.resource.startswith("fs:"):
            ops = [o for o in (g.ops or []) if o != "write"]
            new_grants.append(g.model_copy(update={"ops": ops or ["read"]}))
        else:
            new_grants.append(g)
    return token.model_copy(update={"grants": new_grants})


def has_net(token: CapabilityToken) -> bool:
    """是否存在任何 net: 能力。MVP 二元网络(有/无),域名级白名单 P1。"""
    return any(g.resource.startswith("net:") for g in token.grants)
