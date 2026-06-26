"""L1 原子（#0 §2.3 / #1 §3.2 / #7 §1）。

**【2026-06-16 修正】** 原子 = role 的**不可再分构建块**（化学意义的"原子"）。
它**不**是"单一职责"的别名——单一职责是结果,**是 role 构建块才是原因**。

关键判据:判断一个东西是不是"好原子",只问——**它能不能被多个 role 组合使用?**
(旧判据"能写验证门"是结果不是定义;结晶判据见 #2 §4 仍保留。)

**两**种**生命周期**(保留架构级区分):
  - task   任务原子:无状态、按需 spawn、用完即弃、结晶候选
  - daemon 常驻原子:有状态、后台、定时唤醒

**三**种**来源**(公共能力池;**不**属于任何 role):
  - KarvyLoop 内置 / 用户自建 / 外部导入(MCP,M2+)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ._base import Schema
from .capability import Capability


class AtomSpec(Schema):
    """原子镜像（静态、可分发）。

    `model`：模型**引用串**（查全局注册表，model.py）。None → 按软默认层叠到
    上层（角色/域），最终落 `agents.defaults.model`（#1 §3.1）。
    `is_read_only` / `is_concurrency_safe`：fail-closed 默认（未声明即按危险/不可并发处理）。
    """

    id: str
    kind: Literal["task", "daemon"]
    prompt: str
    input_schema: dict  # JSON Schema
    output_schema: dict
    tools: list[str] = Field(default_factory=list)
    required_capabilities: list[Capability] = Field(default_factory=list)
    model: Optional[str] = None
    commitment_policy: Optional[dict] = None
    is_read_only: bool = False
    is_concurrency_safe: bool = False


class AtomRun(Schema):
    """一次原子执行的记录（写入 Trace；#4 §3.1）。结晶 observe() 从这里派生统计。"""

    atom_id: str
    input: dict
    output: Optional[dict]  # 失败时可为 None（必填、可空）
    success: bool
    tool_calls: list[dict] = Field(default_factory=list)
    trace_ref: str
    ts: float
