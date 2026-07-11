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


@dataclasses.dataclass(frozen=True)
class HashVerifyResult:
    """use-time hash 复验结果(rug-pull 防御)。ok=False → 目标被换过,fail-loud 不派活。"""
    ok: bool
    current_hash: str = ""
    pinned_hash: str = ""
    reason: str = ""


def verify_manifest_hash(recipe: DriveRecipe, pinned_hash: str) -> HashVerifyResult:
    """派活前复验:目标二进制/配方的 hash 是否还对得上 attach 时 pin 的值(rug-pull 防御)。

    **确定性、零模型、不起子进程** —— 只重算 manifest_hash(bin_path/version/argv 模板/黑名单)
    并与 pin 值逐字比对。漂移(有人换了目标 runtime 的命令模板/入口)→ ok=False,
    调用侧 fail-loud 返回 needs_reattach,**绝不静默跑一个被换过的 runtime**。

    - pin 值为空(老记录没 pin)→ 视为不可复验,ok=False(deny-by-default:宁拒不放被换的)。
    - bin 找不到 → ok=False(reason=二进制找不到;连目标都不在,更不能派)。
    - 注:version 复算沿用 attach 时口径(空 version;冒烟才抓 model_hint),保证同配方 hash 稳定可比。
    """
    if not pinned_hash:
        return HashVerifyResult(ok=False, pinned_hash="",
                                reason="无 pin 值可复验(老记录?)—— 重新接入以 pin 当前指纹")
    if _which(recipe.resolved_bin()) is None:
        return HashVerifyResult(ok=False, pinned_hash=pinned_hash,
                                reason=f"二进制找不到:{recipe.bin_path}")
    current = compute_manifest_hash(
        bin_path=recipe.bin_path, version="",
        argv_template=recipe.argv_template,
        blocked_entrypoints=recipe.blocked_entrypoints)
    if current != pinned_hash:
        return HashVerifyResult(ok=False, current_hash=current, pinned_hash=pinned_hash,
                                reason="配方/命令指纹已漂移(疑似 rug-pull),需重新接入复审")
    return HashVerifyResult(ok=True, current_hash=current, pinned_hash=pinned_hash)


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


__all__ = ["probe", "ProbeResult", "verify_manifest_hash", "HashVerifyResult"]
