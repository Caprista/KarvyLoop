"""Stage 2+3 Map/Plan —— 7 槽位 plan。

**核心不变量**(doc §4):
- J2 7 槽位**全**部**有** Plan
- J4 target_agent_dir 注**入**
- J7 全 Callable 注**入**

设计:docs/14 §3.4。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from .source import ExternalManifest

logger = logging.getLogger(__name__)


class PlanError(ValueError):
    """Plan 阶段错误(7 槽位不齐全 / 关键字段缺失)。"""


# 7 灵魂层槽位(对应 #0 §2.4 + Wizard 拍 1 产物)
SLOT_NAMES: tuple[str, ...] = (
    "IDENTITY",
    "SOUL",
    "USER",
    "MEMORY",
    "COMMITMENT",
    "VERIFY",
    "COMPOSITION",  # .yaml 而非 .md
)


@dataclasses.dataclass(frozen=True)
class SlotAction:
    """一个槽位上的迁移动作。"""
    COPY: str = "copy"
    SYNTHESIZE: str = "synthesize"
    SKIP: str = "skip"
    SKIP_EXISTS: str = "skip_exists"   # J5 目标已存在
    MERGE: str = "merge"
    WARN: str = "warn"                 # 强制用户决策


@dataclasses.dataclass(frozen=True)
class SlotPlan:
    """一个 7 文件槽位的迁移计划。"""
    slot: str                    # "IDENTITY" / "SOUL" / ...
    action: str                  # SlotAction 之一
    source: Optional[str] = None
    target: str = ""             # ~/.karvyloop/agents/<id>/IDENTITY.md
    content_preview: str = ""    # 预览前 200 字(用户确认用)
    warnings: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class AdapterPlan:
    """一次迁移的总计划 = 7 槽位 + 总览。"""
    source_id: str
    target_agent_dir: str
    slots: tuple[SlotPlan, ...]  # 必须 = 7 (J2)
    has_warnings: bool
    can_apply: bool              # False → 强制用户决策(J3)

    def slot_for(self, slot: str) -> SlotPlan:
        for s in self.slots:
            if s.slot == slot:
                return s
        raise KeyError(f"slot {slot!r} not in plan (got {len(self.slots)} slots)")


# ---- 文案模板(本拍不调 LLM,J7) ----

_SOUL_SYNTH = (
    "# SOUL (synthesized)\n\n"
    "From {source_id} import. Original agent had:\n"
    "{prompt_excerpt}\n"
    "(本拍 v0 占位 — M3+ 接入 LLM 改写时由 Bootstrapper 升级)\n"
)

_USER_SYNTH = (
    "# USER (synthesized)\n\n"
    "No USER.md found in {source_id} source. Empty placeholder.\n"
    "由 KarvyLoop Bootstrapper 后续引导用户填写。\n"
)

_MEMORY_SYNTH = (
    "# MEMORY (synthesized)\n\n"
    "No MEMORY.md in {source_id} source. Starting fresh.\n"
)

_COMMITMENT_SYNTH = (
    "# COMMITMENT (synthesized)\n\n"
    "Imported from {source_id} at {source_path}.\n"
    "本角色的承诺:为用户跑通原 agent 的核心工作流。\n"
)


def synth_commitment(source_id: str, source_path: str) -> str:
    """v0 导入路径的 COMMITMENT 内容 = 系统默认「尽责下属」契约 + 来源说明。

    docs/02 §15.1.5:导入的 agent 同样是你的下属,得带尽责 disposition —— 三入口
    (系统默认创建 / LLM 导入 / v0 导入)都从同一份规范默认 seed,不能只拷人设。
    """
    from karvyloop.paradigm.contract import default_commitment_contract
    return (
        "# COMMITMENT\n\n"
        + default_commitment_contract()
        + "\n\n---\n\n## This role's own commitments\n\n"
        + f"Imported from {source_id} at {source_path}.\n"
        + "本角色的承诺:为用户跑通原 agent 的核心工作流。\n"
    )

_VERIFY_SYNTH = (
    "# VERIFY (synthesized)\n\n"
    "## 最小验证\n"
    "- 跑 `karvyloop chat` 问一句 'hi',能正常响应 = 通过。\n"
    "(本拍 v0 — 拍 5 Ethos Agent Auditor 会升级)\n"
)

_COMPOSITION_TEMPLATE = (
    "<!-- step_id: COMPOSITION -->\n"
    "imported_from: {source_id}\n"
    "imported_at: <runtime>\n"
    "tools:\n{tools_block}\n"
)


def _truncate(s: str, n: int = 200) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _identity_from_manifest(manifest: ExternalManifest) -> tuple[str, str, tuple[str, ...]]:
    """IDENTITY 槽位:从 system_prompt 第一段 + agent_name 合成。"""
    first_para = manifest.system_prompt.split("\n\n", 1)[0].strip()
    content = (
        f"# IDENTITY (synthesized)\n\n"
        f"agent_name: {manifest.agent_name}\n\n"
        f"## 来源\n{manifest.source_id} at {manifest.source_path}\n\n"
        f"## system_prompt 第一段\n{first_para}\n"
    )
    return content, _truncate(content), ()


def _plan_slot(manifest: ExternalManifest, slot: str, target_agent_dir: str) -> SlotPlan:
    """单个槽位的 plan 策略(异构同源表 doc §3.2)。"""
    target_path = f"{target_agent_dir.rstrip('/')}/{slot}.md"
    if slot == "COMPOSITION":
        target_path = f"{target_agent_dir.rstrip('/')}/COMPOSITION.yaml"
    source_path: Optional[str] = None
    action = SlotAction.SYNTHESIZE
    content = ""
    warnings: tuple[str, ...] = ()
    preview = ""

    if slot == "IDENTITY":
        content, preview, warnings = _identity_from_manifest(manifest)
        action = SlotAction.SYNTHESIZE
    elif slot == "SOUL":
        if manifest.soul_files:
            source_path = manifest.soul_files[0]
            action = SlotAction.COPY
            try:
                content = pathlib_read_text(source_path)
            except Exception as e:
                warnings = (f"read SOUL.md failed: {e}",)
                action = SlotAction.WARN
            preview = _truncate(content)
        else:
            content = _SOUL_SYNTH.format(
                source_id=manifest.source_id,
                prompt_excerpt=_truncate(manifest.system_prompt, 300),
            )
            action = SlotAction.SYNTHESIZE
            preview = _truncate(content)
    elif slot == "USER":
        if manifest.user_files:
            source_path = manifest.user_files[0]
            action = SlotAction.COPY
            try:
                content = pathlib_read_text(source_path)
            except Exception as e:
                warnings = (f"read USER.md failed: {e}",)
                action = SlotAction.WARN
            preview = _truncate(content)
        else:
            content = _USER_SYNTH.format(source_id=manifest.source_id)
            action = SlotAction.SYNTHESIZE
            preview = _truncate(content)
    elif slot == "MEMORY":
        if manifest.memory_files:
            # 拍 4 v0 = 选第一个(M3+ 升级为 merge 多个)
            source_path = manifest.memory_files[0]
            action = SlotAction.COPY
            try:
                content = pathlib_read_text(source_path)
            except Exception as e:
                warnings = (f"read MEMORY.md failed: {e}",)
                action = SlotAction.WARN
            preview = _truncate(content)
        else:
            content = _MEMORY_SYNTH.format(source_id=manifest.source_id)
            action = SlotAction.SYNTHESIZE
            preview = _truncate(content)
    elif slot == "COMMITMENT":
        content = synth_commitment(manifest.source_id, manifest.source_path)
        action = SlotAction.SYNTHESIZE
        preview = _truncate(content)
    elif slot == "VERIFY":
        content = _VERIFY_SYNTH
        action = SlotAction.SYNTHESIZE
        preview = _truncate(content)
    elif slot == "COMPOSITION":
        # 拍 4 v0:只列 tool 名,不校验
        tools_block = "\n".join(
            f"  - name: {t.get('name', 'unknown')}\n    source: {manifest.source_id}"
            for t in manifest.tools
        ) or "  []"
        content = _COMPOSITION_TEMPLATE.format(
            source_id=manifest.source_id,
            tools_block=tools_block,
        )
        action = SlotAction.SYNTHESIZE
        preview = _truncate(content)
    else:
        raise PlanError(f"Unknown slot: {slot!r}; expected one of {SLOT_NAMES}")

    return SlotPlan(
        slot=slot,
        action=action,
        source=source_path,
        target=target_path,
        content_preview=preview,
        warnings=warnings,
    )


def pathlib_read_text(path: str) -> str:
    import pathlib
    return pathlib.Path(path).read_text(encoding="utf-8")


def build_plan(manifest: ExternalManifest, target_agent_dir: str) -> AdapterPlan:
    """AC2/AC3/AC4 入口:从 ExternalManifest 产 7 槽位 plan。

    J2:7 个 slot 全有;J3:WARN → can_apply=False。
    """
    if not manifest.is_minimal():
        raise PlanError(
            f"manifest fails is_minimal: source_id={manifest.source_id} "
            f"system_prompt={bool(manifest.system_prompt)} tools={len(manifest.tools)}"
        )
    slots = tuple(
        _plan_slot(manifest, slot, target_agent_dir)
        for slot in SLOT_NAMES
    )
    if len(slots) != 7:
        # 双保险:J2
        raise PlanError(f"J2 invariant broken: got {len(slots)} slots, expected 7")
    has_warnings = any(s.action == SlotAction.WARN or s.warnings for s in slots)
    can_apply = not has_warnings
    return AdapterPlan(
        source_id=manifest.source_id,
        target_agent_dir=target_agent_dir,
        slots=slots,
        has_warnings=has_warnings,
        can_apply=can_apply,
    )
