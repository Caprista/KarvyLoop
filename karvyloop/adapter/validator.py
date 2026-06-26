"""Stage 5 Validate —— Paradigm Loader 烟测 + 7 文件齐性。

**核心不变量**(doc §4):
- J6 烟测不通过 = validation_errors, 不抛不**回**滚
- J7 全 Callable 注**入**(validator 走注**入**的 Paradigm Loader)

设计:docs/14 §3.6。
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Callable, Optional

from .planner import SLOT_NAMES, AdapterPlan

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    """Validate 阶段产物。"""
    is_valid: bool
    errors: tuple[str, ...]                # J6:不空 = 不 valid
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # 方便 if validate(...)
        return self.is_valid


# 注入式 loader —— 默认走 Paradigm Loader (拍 0)
def _default_loader(agent_dir: str) -> tuple[int, tuple[str, ...]]:
    """默认 Paradigm Loader 烟测:7 文件齐 + COMPOSITION 含 step_id。

    不真调 paradigm.load_paradigm (那需要 RoleSpec),这里做**最**小**校验**。
    拍 4 v0 抽**象** 7 文件齐性 + COMPOSITION 有 step_id 即可。

    返:(layer_count 拼到几层, errors)
    """
    base = pathlib.Path(agent_dir)
    if not base.exists() or not base.is_dir():
        return 0, (f"target_agent_dir {agent_dir} 不存在",)
    errors: list[str] = []
    for slot in SLOT_NAMES:
        fname = f"{slot}.md" if slot != "COMPOSITION" else "COMPOSITION.yaml"
        if not (base / fname).exists():
            errors.append(f"7 文件缺: {fname}")
    if not errors:
        comp = (base / "COMPOSITION.yaml").read_text(encoding="utf-8")
        if "step_id: COMPOSITION" not in comp:
            errors.append("COMPOSITION.yaml 缺 step_id 头")
    return (7 - len(errors) // max(1, len(SLOT_NAMES))) * 7, tuple(errors)


def validate_with_loader(
    plan: AdapterPlan,
    agent_dir: str,
    loader_fn: Optional[Callable[[str], tuple[int, tuple[str, ...]]]] = None,
) -> ValidationResult:
    """AC7 入口:7 文件齐 + COMPOSITION 头 + 走 Paradigm Loader 烟测。

    J6:不抛不**回**滚 —— 返 ValidationResult。
    """
    fn = loader_fn or _default_loader
    try:
        layer_count, errs = fn(agent_dir)
    except Exception as e:
        return ValidationResult(is_valid=False, errors=(f"loader crashed: {e}",))
    if errs:
        return ValidationResult(is_valid=False, errors=errs)
    return ValidationResult(is_valid=True, errors=(), warnings=())
