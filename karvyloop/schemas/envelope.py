"""交互总线：统一 Envelope（#3 §6 / #7 §1）。

Agent 之间传**带 schema 的类型化产物**，不是自由聊天——抗级联幻觉（#0 §5.1）。
关键：Envelope **传输无关**（thin waist）——同一结构在 Tier 1/2/3 都不变，
只有底层"送到对端"的方式不同（#3 §3.2）。

字段名以本契约为准：用 from_addr/to_addr，不用 from/to（from 是 Python 保留字）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from ._base import Schema


class Envelope(Schema):
    """跨 Agent/跨终端的统一消息封套。"""

    id: str
    channel: str  # 频道（私聊/协作群/Topic群/Agent频道）
    from_addr: str  # "RootID.AgentID"（三层通用；人也有地址）
    to_addr: Optional[str] = None  # "RootID.AgentID" | channel | None（点对点或频道广播）
    kind: Literal["chat", "artifact", "status", "summary"]
    schema_id: Optional[str] = None  # kind=artifact 时必填——类型化产物的契约
    payload: dict = Field(default_factory=dict)  # 按 schema_id 校验
    stream_no: Optional[str] = None  # 流式消息
    provenance: dict = Field(default_factory=dict)  # 出处（#0 §4.1 / §5.3 冲突消解）
    ts: float

    @model_validator(mode="after")
    def _artifact_requires_schema_id(self) -> "Envelope":
        # 不变量（#7 §6 验收）：artifact 必带 schema_id——类型化产物交接抗级联幻觉。
        if self.kind == "artifact" and not self.schema_id:
            raise ValueError("kind='artifact' 的 Envelope 必须带 schema_id")
        return self
