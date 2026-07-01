"""Stage 4 Apply —— 写 7 文件到 target_agent_dir。

**核心不变量**(doc §4):
- J3 can_apply=False → 抛
- J4 写到注入的 target dir,不写 cwd
- J5 目标存在不覆盖 (SKIP_EXISTS)

设计:docs/14 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Optional

from .planner import AdapterPlan, PlanError, SlotAction, SlotPlan
from .source import ExternalManifest

logger = logging.getLogger(__name__)


class ApplyError(RuntimeError):
    """Apply 阶段错误(can_apply=False / 写盘失败 / 缺 content)。"""


@dataclasses.dataclass(frozen=True)
class ApplyResult:
    """Apply 阶段的产物。"""
    written: tuple[str, ...]               # 写了的文件 path
    skipped_exists: tuple[str, ...]        # 跳过的(target 已存在,J5)
    failed: tuple[str, ...]                # 失败的
    target_agent_dir: str


def _slot_content(plan: AdapterPlan, manifest: ExternalManifest, slot_plan: SlotPlan) -> str:
    """从 SlotPlan + manifest 重新构造 content(避免 plan 漏带 content 字段)。"""
    # 复用 planner 的合成逻辑 —— 但这里更简单:重跑 planner._plan_slot 取 plan,
    # 然后重读 source 取 content。我们直接重读 source。
    if slot_plan.action == SlotAction.COPY and slot_plan.source:
        return pathlib.Path(slot_plan.source).read_text(encoding="utf-8")
    # synthesize:重跑 planner 的合成(用同一 manifest)
    from .planner import _plan_slot
    re_plan = _plan_slot(manifest, slot_plan.slot, plan.target_agent_dir)
    # re_plan.content_preview 已被截断;我们要全 content,所以重新组装。
    # 简化:调 _plan_slot 时我们没有保留 content —— 这里重做一遍,完整存到 SlotPlan。
    # 拍 4 v0 简化版:走 SlotPlan.action == "synthesize" 的几个模板直接生成。
    if slot_plan.slot == "IDENTITY":
        first_para = manifest.system_prompt.split("\n\n", 1)[0].strip()
        return (
            f"# IDENTITY (synthesized)\n\n"
            f"agent_name: {manifest.agent_name}\n\n"
            f"## 来源\n{manifest.source_id} at {manifest.source_path}\n\n"
            f"## system_prompt 第一段\n{first_para}\n"
        )
    if slot_plan.slot == "SOUL":
        from .planner import _SOUL_SYNTH
        return _SOUL_SYNTH.format(
            source_id=manifest.source_id,
            prompt_excerpt=manifest.system_prompt[:300],
        )
    if slot_plan.slot == "USER":
        from .planner import _USER_SYNTH
        return _USER_SYNTH.format(source_id=manifest.source_id)
    if slot_plan.slot == "MEMORY":
        from .planner import _MEMORY_SYNTH
        return _MEMORY_SYNTH.format(source_id=manifest.source_id)
    if slot_plan.slot == "COMMITMENT":
        # §15.1.5:v0 导入也 seed 系统默认尽责契约(与默认创建/LLM 导入同一份 seed)
        from .planner import synth_commitment
        return synth_commitment(manifest.source_id, manifest.source_path)
    if slot_plan.slot == "VERIFY":
        from .planner import _VERIFY_SYNTH
        return _VERIFY_SYNTH
    if slot_plan.slot == "COMPOSITION":
        from .planner import _COMPOSITION_TEMPLATE
        tools_block = "\n".join(
            f"  - name: {t.get('name', 'unknown')}\n    source: {manifest.source_id}"
            for t in manifest.tools
        ) or "  []"
        return _COMPOSITION_TEMPLATE.format(
            source_id=manifest.source_id,
            tools_block=tools_block,
        )
    raise ApplyError(f"Unknown slot: {slot_plan.slot}")


def apply_plan(
    plan: AdapterPlan,
    manifest: ExternalManifest,
    target_agent_dir: Optional[str] = None,
) -> ApplyResult:
    """AC5/AC6 入口:写 7 文件到 target dir。

    J3:can_apply=False → 抛 ApplyError
    J5:目标存在 → 跳过(SKIP_EXISTS)
    J4:写到 target_agent_dir(不写 cwd)
    """
    if not plan.can_apply:
        raise ApplyError(
            f"J3: plan.can_apply=False, has_warnings={plan.has_warnings}; "
            f"用户必须先 review。warnings="
            f"{[s.warnings for s in plan.slots if s.warnings]}"
        )
    target_dir_str = target_agent_dir or plan.target_agent_dir
    target_dir = pathlib.Path(target_dir_str)
    target_dir.mkdir(parents=True, exist_ok=True)  # J4:不存在则建

    written: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for slot_plan in plan.slots:
        if slot_plan.action == SlotAction.SKIP:
            continue
        if slot_plan.action == SlotAction.WARN:
            # can_apply=False 应该已经拦住;若还到这 → 兜底
            failed.append(slot_plan.target)
            continue
        target_path = pathlib.Path(slot_plan.target)
        # J5:目标存在不覆盖
        if target_path.exists():
            logger.info("J5: target %s 已存在 → 跳过", target_path)
            skipped.append(str(target_path))
            continue
        try:
            content = _slot_content(plan, manifest, slot_plan)
            target_path.write_text(content, encoding="utf-8")
            written.append(str(target_path))
        except Exception as e:
            logger.warning("Apply 写 %s 失败: %s", target_path, e)
            failed.append(str(target_path))

    return ApplyResult(
        written=tuple(written),
        skipped_exists=tuple(skipped),
        failed=tuple(failed),
        target_agent_dir=str(target_dir),
    )
