"""external_runtime/citizen — 频道公民身份(ExternalCitizen)+ 注册表(持久化)。

**本体论对齐(#71 §2.1)**:外部 runtime 既不是 role、也不是 atom、也不是小卡,是**第四类实体**——
一个 opaque、归属外部主人的执行体。它借用 role 的**寻址壳**(复合键 (域, citizen_id)、频道成员显示)
和小卡的**中间人语义**,但**不获得 role 的本体论地位**(无记忆、无结晶、无决策席)。

- 身份 = 一张能力卡(探测生成,非灵魂 7 文件),不合成假 soul。
- 记忆 = 无(不联邦记忆)。结晶过不了边界(A2A opacity 天然护城河)。
- 寻址 = (域, citizen_id) 复合键,但解析到的是**桥**不是 role。
- 决策席 = 无,只能供稿。

manifest_hash = hash-pin(bin.path + version + argv_template + blocked_entrypoints),
版本/命令模板变更即重审(rug-pull 防御)。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Optional

from .recipe import DriveRecipe, builtin_recipe

# 公民地址里固定的 role 段(#71 §3.3:to=Address(域, role="external", agent_id=citizen_id))
EXTERNAL_ROLE = "external"

STATUS_ACTIVE = "active"
STATUS_UNREACHABLE = "unreachable"
STATUS_BLOCKED = "blocked"
STATUS_RETIRED = "retired"
STATUS_NEEDS_REATTACH = "needs_reattach"

# ---- 成员等级(受限成员生命周期,承 T0 客人)----
# T0 = guest(现状:opaque 供稿人,无域绑定深度,私聊/无域派活)。
# T1 = scoped(受限成员:绑定单个业务域深度协作,但 deny-by-default —— 只读该域**公共**记忆、
#      可**逆向供稿**(写的都可撤),域私有认知((域,角色)隔离)**完全不可读不可写**)。
# T2(写域私有认知)**绝不实现、不留代码路径** —— 它锁到未来 H2A 采纳门;现在建 = 给护城河
#      开无守卫写口。任何 tier != {guest, scoped} 一律当最不信任(guest)处理(deny-by-default)。
TIER_GUEST = "guest"      # T0:客人(现状)
TIER_SCOPED = "scoped"    # T1:受限成员(绑定单域,deny-by-default,可逆供稿)
_KNOWN_TIERS = frozenset({TIER_GUEST, TIER_SCOPED})


def normalize_tier(tier: str) -> str:
    """把任意 tier 值归一到已知等级;未知/空 → guest(deny-by-default,不确定就当最不信任)。

    **护城河纪律**:除 guest/scoped 外一律拒(尤其绝不认 "t2"/"private"/"write" 之类字样),
    防有人塞个高权 tier 字符串就拿到域私有认知写口。
    """
    t = (tier or "").strip().lower()
    return t if t in _KNOWN_TIERS else TIER_GUEST


@dataclasses.dataclass(frozen=True)
class ExternalCitizen:
    """一个已接入的外部 runtime 频道公民(#71 §2.2 字段最小集)。"""
    citizen_id: str                        # 频道内寻址花名,如 "cc" / "helper"
    runtime_kind: str                      # 配方类型(generic_cli / single_json_cli / raw_text_sidecar)
    bin_path: str                          # 探测出的二进制真路径
    domain_id: str = ""                    # 挂载域(空=私聊/无域;复合键 (域, citizen_id))
    capability_card: dict = dataclasses.field(default_factory=dict)  # 探测事实,非假 soul
    token_source: str = ""                 # 独立记账口,如 "ext:cc"(空则从 citizen_id 派生)
    manifest_hash: str = ""                # 能力/命令 hash-pin(rug-pull 防御)
    created_by: str = "user"
    status: str = STATUS_ACTIVE
    tier: str = TIER_GUEST                  # 成员等级(guest=T0 客人 / scoped=T1 受限成员;deny-by-default)

    def __post_init__(self) -> None:
        # tier 归一(deny-by-default):未知/篡改的 tier 值一律降到 guest。
        # frozen dataclass → object.__setattr__ 绕过不可变(仅归一,不放宽)。
        norm = normalize_tier(self.tier)
        if norm != self.tier:
            object.__setattr__(self, "tier", norm)

    def is_scoped_member(self) -> bool:
        """T1 受限成员(绑定单域深度协作,deny-by-default)。"""
        return self.tier == TIER_SCOPED

    def can_read_domain_public(self, domain_id: str) -> bool:
        """T1 只读**它绑定的那个域**的公共记忆;跨域一律拒(deny-by-default)。guest 无域读权。"""
        return (self.tier == TIER_SCOPED
                and bool(self.domain_id)
                and self.domain_id == (domain_id or ""))

    def can_read_domain_private(self, domain_id: str) -> bool:
        """域私有认知((域,角色)隔离):**任何 tier 都不可读**。T2 绝不实现。"""
        return False

    def can_write_domain_private(self, domain_id: str) -> bool:
        """写域私有认知:**任何 tier 都不可写**。T2 锁到未来 H2A 采纳门,现在恒 False。"""
        return False

    def source_tag(self) -> str:
        """token 账本的独立 source(默认 ext:<citizen_id>)。"""
        return self.token_source or f"ext:{self.citizen_id}"

    def recipe(self) -> Optional[DriveRecipe]:
        """取该公民的驱动配方(内置库按 runtime_kind 取,bin_path 用探测值覆盖)。"""
        base = builtin_recipe(self.runtime_kind)
        if base is None:
            return None
        return dataclasses.replace(base, bin_path=self.bin_path or base.bin_path)

    def to_dict(self) -> dict:
        return {
            "citizen_id": self.citizen_id, "runtime_kind": self.runtime_kind,
            "bin_path": self.bin_path, "domain_id": self.domain_id,
            "capability_card": self.capability_card, "token_source": self.token_source,
            "manifest_hash": self.manifest_hash, "created_by": self.created_by,
            "status": self.status, "tier": self.tier,
        }

    @staticmethod
    def from_dict(d: dict) -> "ExternalCitizen":
        d = d or {}
        return ExternalCitizen(
            citizen_id=str(d.get("citizen_id") or ""),
            runtime_kind=str(d.get("runtime_kind") or ""),
            bin_path=str(d.get("bin_path") or ""),
            domain_id=str(d.get("domain_id") or ""),
            capability_card=dict(d.get("capability_card") or {}),
            token_source=str(d.get("token_source") or ""),
            manifest_hash=str(d.get("manifest_hash") or ""),
            created_by=str(d.get("created_by") or "user"),
            status=str(d.get("status") or STATUS_ACTIVE),
            # 旧记录无 tier → 默认 guest(向后兼容,deny-by-default);normalize 在 __post_init__ 兜。
            tier=str(d.get("tier") or TIER_GUEST),
        )


def compute_manifest_hash(*, bin_path: str, version: str,
                          argv_template, blocked_entrypoints) -> str:
    """rug-pull 防御:bin/version/命令模板/黑名单变更即 hash 变 → re-probe 时标 needs_reattach。"""
    h = hashlib.sha256()
    h.update((bin_path or "").encode("utf-8"))
    h.update((version or "").encode("utf-8"))
    h.update(json.dumps(list(argv_template or ()), ensure_ascii=False).encode("utf-8"))
    h.update(json.dumps(sorted(blocked_entrypoints or ()), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


class ExternalCitizenRegistry:
    """已接入公民注册表(持久化;用户数据默认存盘)。

    复合键寻址:`resolve_in(domain_id, citizen_id)` —— 同一花名跨域是不同挂载点。
    `resolve(citizen_id)` 是调用侧带当前域的便捷入口(域可空=私聊/无域)。
    """

    def __init__(self, *, store=None, probe_fn=None) -> None:
        # 键 = (domain_id, citizen_id);同花名跨域独立
        self._by_key: dict[tuple[str, str], ExternalCitizen] = {}
        # 供稿账本(可撤销供稿的可追溯性):key=(domain_id, citizen_id) → list[dict]
        # 每条 = {seed_id, note, adopted}。adopted=True 的产出已是用户数据(采纳 = H2A 拍板),
        # detach 不级联删;adopted=False 的未采纳供稿由 detach 清理。**纯内存**(不落盘):
        # 采纳后的产出去向由消费侧(记忆/结晶)持久化,这里只做撤销可追溯的登记面。
        self._contributions: dict[tuple[str, str], list[dict]] = {}
        self._store = store
        # 探活函数注入(liveness 复用探活;默认懒加载真 probe)。测试可注入假 probe。
        self._probe_fn = probe_fn
        self.persist_error: str = ""
        self.last_detach_trace: dict = {}   # 最近一次 detach 的撤销可追溯记录
        if store is not None:
            try:
                for d in (store.load_all() or []):
                    c = ExternalCitizen.from_dict(d)
                    if c.citizen_id:
                        self._by_key[(c.domain_id, c.citizen_id)] = c
            except Exception as e:  # noqa: BLE001 — 加载失败不炸,空表起步
                self.persist_error = f"load: {type(e).__name__}: {e}"

    def add(self, citizen: ExternalCitizen) -> bool:
        """注册/覆盖一个公民,落盘。返回是否持久化成功(内存态总是写上)。"""
        self._by_key[(citizen.domain_id, citizen.citizen_id)] = citizen
        return self._persist()

    def resolve_in(self, domain_id: str, citizen_id: str) -> Optional[ExternalCitizen]:
        """复合键精确解析((域, citizen_id))。"""
        return self._by_key.get((domain_id or "", citizen_id or ""))

    def resolve(self, citizen_id: str, *, domain_id: str = "") -> Optional[ExternalCitizen]:
        """调用侧便捷解析:先按 (给定域, id) 精确查;miss 再退回该 id 的任一挂载(私聊无域场景)。"""
        hit = self._by_key.get((domain_id or "", citizen_id or ""))
        if hit is not None:
            return hit
        # 私聊/无域:同 citizen_id 的任一挂载(优先无域挂载)
        cands = [c for (d, cid), c in self._by_key.items() if cid == citizen_id]
        if not cands:
            return None
        cands.sort(key=lambda c: (c.domain_id != "", c.domain_id))
        return cands[0]

    def remove(self, domain_id: str, citizen_id: str) -> bool:
        """软删一个公民(retired)。返回是否有此公民。"""
        key = (domain_id or "", citizen_id or "")
        if key not in self._by_key:
            return False
        self._by_key.pop(key, None)
        self._persist()
        return True

    def list_all(self) -> list[ExternalCitizen]:
        return list(self._by_key.values())

    def list_active(self) -> list[ExternalCitizen]:
        return [c for c in self._by_key.values() if c.status == STATUS_ACTIVE]

    def list(self, domain=None) -> list[ExternalCitizen]:
        """契约入口(C2 消费):domain=None → 全部;给了域 → 只列该域挂载的公民。

        `list(domain="d1")` = 该业务域里所有成员(供 UI 域成员面/detach 选择)。
        私聊/无域挂载(domain_id=="")用 domain="" 取。
        """
        if domain is None:
            return list(self._by_key.values())
        did = domain or ""
        return [c for (d, _cid), c in self._by_key.items() if d == did]

    # ---- 供稿账本(可撤销供稿的可追溯性)----

    def record_contribution(self, domain_id: str, citizen_id: str, *,
                            seed_id: str, note: str = "", adopted: bool = False) -> None:
        """登记一条外部公民的供稿(供 detach 追溯)。

        - adopted=False:未采纳供稿 —— detach 时清理。
        - adopted=True :已被 H2A 采纳,已是用户数据 —— detach **不**级联删。
        纯内存登记(不落盘):采纳后的真产出由消费侧持久化,这里只记撤销可追溯的映射。
        """
        key = (domain_id or "", citizen_id or "")
        self._contributions.setdefault(key, []).append(
            {"seed_id": str(seed_id or ""), "note": str(note or "")[:200], "adopted": bool(adopted)})

    def mark_adopted(self, domain_id: str, citizen_id: str, seed_id: str) -> bool:
        """把某条供稿标为已采纳(H2A 拍板后调):之后 detach 不再清它。返回是否命中。"""
        key = (domain_id or "", citizen_id or "")
        hit = False
        for c in self._contributions.get(key, []):
            if c.get("seed_id") == str(seed_id or ""):
                c["adopted"] = True
                hit = True
        return hit

    def detach(self, domain, citizen_id) -> bool:
        """scoped 优雅撤销:撤一个成员**不 kill 整个域**(§T1 安全件)。

        - **已采纳产出不级联删**:adopted=True 的供稿已是用户数据,保留(retired 成员的贡献不因人走而消失)。
        - **未采纳供稿清理**:adopted=False 的供稿丢弃(它没进用户数据,撤人即撤稿)。
        - **可追溯**:返回前把该成员 seed 过哪些认知记进 last_detach_trace(哪些保留/哪些清)。
        不做粗暴 kill:成员从注册表移除、落盘,但采纳过的内容原地留存。

        返回是否有此成员(没有 → False)。
        """
        key = (domain or "", citizen_id or "")
        citizen = self._by_key.get(key)
        if citizen is None:
            self.last_detach_trace = {"citizen_id": citizen_id or "", "domain_id": domain or "",
                                      "found": False, "kept_adopted": [], "cleared_unadopted": []}
            return False
        contribs = self._contributions.get(key, [])
        kept = [c["seed_id"] for c in contribs if c.get("adopted")]        # 已采纳:留
        cleared = [c["seed_id"] for c in contribs if not c.get("adopted")]  # 未采纳:清
        # 只清未采纳供稿的登记;已采纳的保留(供稿去向已是用户数据,可追溯它 seed 过什么)
        remaining = [c for c in contribs if c.get("adopted")]
        if remaining:
            self._contributions[key] = remaining
        else:
            self._contributions.pop(key, None)
        # 撤成员(优雅:移出注册表 + 落盘;不触碰已采纳产出)
        self._by_key.pop(key, None)
        self._persist()
        self.last_detach_trace = {
            "citizen_id": citizen_id or "", "domain_id": domain or "", "found": True,
            "tier": citizen.tier, "kept_adopted": kept, "cleared_unadopted": cleared}
        return True

    def liveness(self, citizen_id) -> dict:
        """探活原料(确定性探一次,不起心跳线程;UI 侧按需调)。

        复用 probe 的确定性探活(二进制在不在 / 能不能跑冒烟)。返回至少
        {status: online|offline|unreachable}:
          - 注册表无此成员 / 无可用配方 → offline
          - probe active → online
          - probe 不过(bin 缺 / 冒烟失败)→ unreachable(+ reason)
        探活出错也 fail-loud 成 unreachable,绝不假装 online。
        """
        c = self.resolve(citizen_id)
        if c is None:
            return {"status": "offline", "reason": "无此成员", "citizen_id": citizen_id or ""}
        recipe = c.recipe()
        if recipe is None:
            return {"status": "offline", "reason": f"无可用配方(runtime_kind={c.runtime_kind})",
                    "citizen_id": c.citizen_id}
        probe_fn = self._probe_fn
        if probe_fn is None:
            from .probe import probe as probe_fn  # 懒加载,避免循环
        try:
            # smoke=False:确定性静态探(bin 在不在 / key 源在不在),不真起子进程冒烟 —— 探一次够 UI 用。
            pr = probe_fn(recipe, smoke=False)
        except Exception as e:  # noqa: BLE001 — 探活出错 fail-loud 成 unreachable,不假装 online
            return {"status": "unreachable", "reason": f"探活出错:{type(e).__name__}",
                    "citizen_id": c.citizen_id}
        if getattr(pr, "ok", False):
            return {"status": "online", "citizen_id": c.citizen_id, "tier": c.tier,
                    "domain_id": c.domain_id}
        return {"status": "unreachable", "reason": getattr(pr, "reason", "") or "探活不过",
                "citizen_id": c.citizen_id}

    def _persist(self) -> bool:
        if self._store is None:
            return True
        try:
            self._store.save_all([c.to_dict() for c in self._by_key.values()])
            self.persist_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.persist_error = f"{type(e).__name__}: {e}"
            return False


__all__ = [
    "ExternalCitizen", "ExternalCitizenRegistry", "compute_manifest_hash",
    "EXTERNAL_ROLE",
    "STATUS_ACTIVE", "STATUS_UNREACHABLE", "STATUS_BLOCKED",
    "STATUS_RETIRED", "STATUS_NEEDS_REATTACH",
    "TIER_GUEST", "TIER_SCOPED", "normalize_tier",
]
