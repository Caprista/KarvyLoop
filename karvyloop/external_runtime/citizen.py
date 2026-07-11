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
            "status": self.status,
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

    def __init__(self, *, store=None) -> None:
        # 键 = (domain_id, citizen_id);同花名跨域独立
        self._by_key: dict[tuple[str, str], ExternalCitizen] = {}
        self._store = store
        self.persist_error: str = ""
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
]
