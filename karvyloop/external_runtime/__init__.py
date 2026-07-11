"""external_runtime — 跨 runtime 协作接入插件(桥 / 配方 / 探活 / 公民注册)。

**定位(#71 协作产品层 + #72 接入插件层)**:让用户把**别人家的 agent 运行时**(任意
headless CLI)当作**一个有身份的频道参与者**拉进来 —— @ 它派活、看它干活、拍板它的产出——
而它始终是 **opaque 的外部执行体**,输出永远是**不可信数据**,绝不占决策席、绝不碰记忆护城河。

三条红线(贯穿全包):
1. **联邦能力、不联邦信任、不联邦记忆**。外部 runtime 提供执行能力;它的诚实不可假设;
   它的输出永不自动进记忆/结晶/跨设备总线。
2. **它是"工具包/供稿人",不是"对话席位/role"**。输出走数据通道(标 provenance=untrusted),
   不进对话主线的 role 席。
3. **H2A 人拍板是唯一升级门**。外部产出要进记忆/触发下游/动敏感面,必须过决策卡。

M1 交付:ExternalCitizen + 注册表(持久化)+ SubprocessBridge(子进程实现,fail-loud + 密钥过滤)+
三份内置配方 + doctor 式探活 + citizen-aware 寻址解析。协作语义(圆桌供稿席/采纳门)在后续 M。

**clean-room 纪律**:公开仓代码/注释走中性词(external_runtime/bridge/recipe),不点参照工程名。
"""
from __future__ import annotations

from .addressing import citizen_address, make_citizen_aware_resolver
from .bridge import (
    BridgeResult,
    STATUS_DONE,
    STATUS_FAILED,
    SubprocessBridge,
    bridge_factory,
    make_sandbox_runner,
    sandbox_bridge_factory,
)
from .citizen import (
    CLAIM_TICKET_TTL_S,
    EXTERNAL_ROLE,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_NEEDS_REATTACH,
    STATUS_PENDING,
    STATUS_RETIRED,
    STATUS_UNREACHABLE,
    TIER_GUEST,
    TIER_SCOPED,
    ClaimTicket,
    ExternalCitizen,
    ExternalCitizenRegistry,
    compute_manifest_hash,
    mint_claim_ticket,
    normalize_tier,
    split_claim_secret,
)
from .probe import HashVerifyResult, ProbeResult, probe, verify_manifest_hash
from .recipe import (
    DriveRecipe,
    ExitSpec,
    ParseSpec,
    PARSE_NDJSON,
    PARSE_RAW_TEXT,
    PARSE_SINGLE_JSON,
    builtin_kinds,
    builtin_probe_bins,
    builtin_recipe,
)
from .redact import contains_secret, redact
from .store import ClaimTicketStore, ExternalCitizenStore

__all__ = [
    # bridge
    "SubprocessBridge", "BridgeResult", "bridge_factory",
    "make_sandbox_runner", "sandbox_bridge_factory",
    "STATUS_DONE", "STATUS_FAILED",
    # citizen
    "ExternalCitizen", "ExternalCitizenRegistry", "compute_manifest_hash", "EXTERNAL_ROLE",
    "STATUS_ACTIVE", "STATUS_UNREACHABLE", "STATUS_BLOCKED",
    "STATUS_RETIRED", "STATUS_NEEDS_REATTACH", "STATUS_PENDING",
    "TIER_GUEST", "TIER_SCOPED", "normalize_tier",
    # 认领码握手
    "ClaimTicket", "mint_claim_ticket", "split_claim_secret", "CLAIM_TICKET_TTL_S",
    # recipe
    "DriveRecipe", "ParseSpec", "ExitSpec",
    "PARSE_SINGLE_JSON", "PARSE_NDJSON", "PARSE_RAW_TEXT",
    "builtin_recipe", "builtin_kinds", "builtin_probe_bins",
    # probe
    "probe", "ProbeResult", "verify_manifest_hash", "HashVerifyResult",
    # addressing
    "make_citizen_aware_resolver", "citizen_address",
    # redact
    "redact", "contains_secret",
    # store
    "ExternalCitizenStore", "ClaimTicketStore",
]
