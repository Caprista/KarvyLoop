"""model_selector — 小模型选择抽象(公共快脑工具,0.1.0 留空)。

设计:docs/25-fastbrain-architecture.md §3.2 小模型快脑。

**职责**:
- 抽象"用哪个小模型做意图识别 / 能力检索 / 简单分类"
- 0.1.0 **不强制选型** — 等真机测再定
- 提供 Future 切换接口,避免 hard-code 模型名

**选型待定**(用户 2026-06-17 拍板):"回头测的时候看模型效果,根据电脑配置和模型质量来建议"
- 候选:1B / 3B / 7B 本地小模型
- 量化:Q4 / Q8 / FP16 视显存
- 候选模型:实际跑一轮评测再定

**纪律**:
- 公共机制 — 任何 agent / role 可调
- 0.1.0 骨架 — 留接口,等模型选定后实做
- 选型不能影响 OS 其他部分(走抽象,后续替换一行 import)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

__all__ = ["SmallModelSpec", "SmallModelBackend", "select_default_model"]


@dataclass(frozen=True)
class SmallModelSpec:
    """小模型规格(选型信息)。"""
    name: str                # "qwen2.5-1.5b-instruct-q4" / "llama-3.2-1b-q8" ...
    size_b: float            # 模型大小(B)
    quant: str               # "q4" / "q8" / "fp16"
    est_ram_gb: float        # 估算运行内存(GB)
    task: str                # "intent_classify" / "capability_retrieve" / ...


class SmallModelBackend(Protocol):
    """小模型后端协议(0.1.0 留,等选型后实做)。"""

    def classify(self, text: str, labels: list[str]) -> tuple[str, float]:
        """分类返 (label, confidence)。0.1.0 不实做。"""
        ...


def select_default_model(task: str = "intent_classify") -> Optional[SmallModelSpec]:
    """选默认小模型(0.1.0 骨架:返 None,等真机测后定)。

    0.2.0 落地:接 ~/.karvyloop/fastbrain.yaml 配置;缺省返 None = 调用方转大脑。
    """
    logger.debug(f"[fastbrain.model_selector] no default for task={task!r} (0.1.0 stub)")
    return None
