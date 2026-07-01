"""L0 能力：能力令牌（#0 安全地基 / #5 §4 / #7 §1）。

零权限起步：每个任务签发一张明确的、（跨边界时）不可伪造的能力令牌；
所有文件/网络/进程/记忆访问都对照令牌放行（#5 §4）。
"""

from __future__ import annotations

from pydantic import Field

from ._base import Schema


class Capability(Schema):
    """一项被授予的能力。

    `resource` 形如：
      - "fs:/home/u/project"   文件
      - "net:api.github.com"   网络
      - "proc:python"          进程
      - "mem:personal"         私人记忆
      - "mem:domain/<id>"      域记忆/KB（按域成员清单授权，#4 §4.1）
    """

    resource: str
    ops: list[str] = Field(default_factory=list)  # ["read","write","connect","exec",...]


class CapabilityToken(Schema):
    """一个任务的能力令牌（#5 §4）。

    `sig`：仅在令牌**跨信任边界**时才需要 broker 私钥签名（交给沙箱/Forge 子进程、
    或跨设备投递）。进程内（M0，Tier 1）令牌即一个可信对象引用，`sig` 可留空。
    """

    task_id: str
    grants: list[Capability] = Field(default_factory=list)
    expiry: float  # unix ts
    sig: str = ""  # 跨信任边界时签名；进程内可空（#5 §4 注）
