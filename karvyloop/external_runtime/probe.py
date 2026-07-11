"""external_runtime/probe — doctor 式确定性探活(缺失=诚实报不可用,不假装)。

接入向导(attach)跑一次;之后可按需 re-probe。**全确定性、零模型**(doctor 精神:L0 地板)。

探活四步(逐步 fail-loud):
  1. bin 在不在      : which(bin.path)             失败 → unreachable("二进制找不到")
  2. key 源在不在    : 存在性检查 key_source_path   失败 → unreachable("key 源缺失")——**绝不读内容**
  3. (M1 略过 preflight 主动改配置,只记录待满足项)
  4. headless 冒烟   : 派确定性小任务("reply with READY")→ 退0 + 非空 + 含锚 → active
                       否则 → unreachable(冒烟失败:<过滤后 stderr>)

- key 源只查存在性,**绝不读内容/绝不打印**(凭证纪律)。
- 冒烟锚用确定性可判的锚(不用 LLM 判)。
- 能力卡是"能干什么"的探测事实,**不合成假 soul**。
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import time
from typing import Optional

from .bridge import bridge_factory
from .citizen import (
    STATUS_ACTIVE,
    STATUS_UNREACHABLE,
    compute_manifest_hash,
)
from .recipe import DriveRecipe


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """探活结果。status active → 可注册 active;否则诚实标不可用。"""
    status: str                    # active | unreachable
    reason: str = ""               # 不可用原因(已确定性,无 key)
    version: str = ""
    capability_card: dict = dataclasses.field(default_factory=dict)
    manifest_hash: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_ACTIVE


def _which(bin_path: str) -> Optional[str]:
    """bin 在不在:绝对路径查文件,否则查 PATH。"""
    p = os.path.expanduser(bin_path or "")
    if not p:
        return None
    if os.path.sep in p or (os.altsep and os.altsep in p):
        return p if (os.path.isfile(p) and os.access(p, os.X_OK)) else None
    return shutil.which(p)


def probe(recipe: DriveRecipe, *, env_base: Optional[dict] = None,
          runner=None, smoke: bool = True) -> ProbeResult:
    """确定性探活(可注入 runner 做离线测试;smoke=False 跳过冒烟只做静态检查)。"""
    # 1. bin 在不在
    resolved = _which(recipe.resolved_bin())
    if resolved is None:
        return ProbeResult(status=STATUS_UNREACHABLE,
                           reason=f"二进制找不到:{recipe.bin_path}")

    # 2. key 源在不在(只查存在性,绝不读内容/绝不打印)
    if recipe.key_source_path:
        kp = os.path.expanduser(recipe.key_source_path)
        if not os.path.exists(kp):
            return ProbeResult(status=STATUS_UNREACHABLE,
                               reason="key 源缺失(不读内容)")

    version = ""
    manifest = compute_manifest_hash(
        bin_path=recipe.bin_path, version=version,
        argv_template=recipe.argv_template,
        blocked_entrypoints=recipe.blocked_entrypoints)

    if not smoke:
        card = _card(recipe, version, smoke_ok=None)
        return ProbeResult(status=STATUS_ACTIVE, version=version,
                           capability_card=card, manifest_hash=manifest)

    # 4. headless 冒烟:确定性锚
    br = bridge_factory(recipe, env_base=env_base, runner=runner).start(recipe.smoke_prompt)
    anchor = (recipe.smoke_anchor or "").strip().lower()
    hit = anchor and anchor in (br.text or "").lower()
    if not br.ok or not hit:
        why = br.reason or (f"冒烟未命中锚「{recipe.smoke_anchor}」" if br.ok else "冒烟失败")
        return ProbeResult(status=STATUS_UNREACHABLE, reason=why[:300],
                           manifest_hash=manifest)
    # 从 usage.model 抓 model_hint(有边车/内嵌 meta 才有)
    if br.usage and br.usage.get("model"):
        version = str(br.usage.get("model") or "")
    card = _card(recipe, version, smoke_ok=True)
    return ProbeResult(status=STATUS_ACTIVE, version=version,
                       capability_card=card, manifest_hash=manifest)


def _card(recipe: DriveRecipe, version: str, *, smoke_ok) -> dict:
    """能力卡(探测事实,非假 soul)。"""
    return {
        "runtime_kind": recipe.runtime_kind,
        "version": version or "unknown",
        "parse_mode": recipe.parse.mode,
        "has_usage_sidecar": recipe.parse.meta_from_sidecar,
        "probed_at": int(time.time()),
        "smoke_ok": smoke_ok,
        "preflight_pending": list(recipe.preflight),
    }


__all__ = ["probe", "ProbeResult"]
