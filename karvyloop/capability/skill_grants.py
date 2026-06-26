"""skill_grants — 技能 allowed-tools → 能力授予 + 第三方信任收口(P0-c)。

docs/02 §3:`allowed-tools` 就是 #0 的 deontic 能力清单。执行技能脚本时据此签发 token。

**信任收口(地基级 —— #0 安全是地基不是招牌)**:
第三方(`trust: untrusted` / `source: third-party`)技能 = **别人的代码**,无论它在 frontmatter 里
`allowed-tools` 声称什么,一律只拿**最小授予**:仅工作区(scratch)读写 + 技能自身目录只读;
**绝不给网络 / 宿主任意 fs**。恶意技能不能靠多列几个 allowed-tools 给自己提权。
只有自家结晶技能(有 verify_proof、非第三方)才按 allowed-tools 放开更多(如网络)。

→ 这把"用第三方生态"和"安全是地基"两条同时落地:能用别人的技能,但别人的脚本被关进笼子。
"""
from __future__ import annotations

from typing import Optional

from karvyloop.capability.token import mint
from karvyloop.schemas import Capability, CapabilityToken

# 联网类工具名(自家可信技能才据此放开 net)
_NET_TOOLS = {"webfetch", "websearch", "fetch", "curl", "wget", "http"}


def is_trusted_skill(fm) -> bool:
    """技能是否可信(自家结晶)。第三方导入(trust:untrusted / source:third-party)→ 不可信。

    自家结晶技能有 verify_proof、无 trust 标记 → 可信。无任何标记的本地手写技能默认可信
    (是用户自己放进来的,不是从外部生态拉的)。
    """
    raw = getattr(fm, "raw", None) or {}
    if str(raw.get("trust", "")).strip().lower() == "untrusted":
        return False
    if str(raw.get("source", "")).strip().lower() == "third-party":
        return False
    return True


def capability_for_skill(allowed_tools, *, skill_dir: str, workspace: str,
                         trusted: bool, net: bool = False) -> list[Capability]:
    """把 allowed-tools 映射成 grants。第三方(trusted=False)硬收口到最小集。

    `net`:**用户显式授权**这个技能联网(≠ 技能自己在 allowed-tools 里声称)。第三方默认拒网,
    但用户可在 UI 里逐个授权(很多 API 类技能要联网才有用)—— 授权是**人的决定**,不是技能能自取的。
    """
    grants = [
        # 技能读自己的 scripts/references/assets(只读,跑脚本要能读到 bundle)
        Capability(resource=f"fs:{skill_dir}", ops=["read"]),
        # scratch 工作区:可写(脚本的输入/产物落这里;沙箱外看不到宿主)
        Capability(resource=f"fs:{workspace}", ops=["read", "write"]),
    ]
    has_net = False
    if trusted:
        # 自家可信技能:按 allowed-tools 放开(目前主要是网络;更多 op P1)
        for tool in (allowed_tools or []):
            name = str(tool).split("(", 1)[0].strip().lower()
            if name in _NET_TOOLS or name.startswith("net"):
                has_net = True
                break
    # 用户显式授网(对可信/第三方都生效)—— 人的决定凌驾默认收口
    if (net or has_net):
        grants.append(Capability(resource="net:*", ops=["connect"]))
    return grants


def token_for_skill(fm, *, skill_dir: str, workspace: str,
                    task_id: str = "skill-exec", ttl_seconds: float = 600.0,
                    trusted: Optional[bool] = None, net: bool = False) -> CapabilityToken:
    """据技能 frontmatter(信任级 + allowed-tools)+ 用户授网签发执行用 token。"""
    if trusted is None:
        trusted = is_trusted_skill(fm)
    grants = capability_for_skill(
        getattr(fm, "allowed_tools", None), skill_dir=skill_dir,
        workspace=workspace, trusted=trusted, net=net)
    return mint(task_id, grants, ttl_seconds)


__all__ = ["is_trusted_skill", "capability_for_skill", "token_for_skill"]
