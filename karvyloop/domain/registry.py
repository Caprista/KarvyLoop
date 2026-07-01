"""registry — 业务域注册表 + 动态成员解析。

**核心不变量**(doc §4):
- D4 成员 = 动态解析(不是静态列表)
- D6 archived 业务域不接受新请求
- D7 全部依赖注入

设计:docs/18 §3.4。
"""
from __future__ import annotations

import dataclasses
import logging
import re
import uuid
from typing import Callable, Optional

from .deontic import Deontic, apply_deontic, derive_soul_subset
from .value import ValueMd, ValueMdRequiredError

logger = logging.getLogger(__name__)


# 业务域 lifecycle 状态
LIFECYCLE_ACTIVE: str = "active"
LIFECYCLE_ARCHIVED: str = "archived"


class ArchivedDomainError(RuntimeError):
    """archived 业务域不允许新操作(D6)。"""


# 寻址类型
ADDR_USER: str = "user"
ADDR_AGENT: str = "agent"
ADDR_OBSERVER: str = "observer"  # 小卡特殊身份


@dataclasses.dataclass(frozen=True)
class Address:
    """业务域内寻址(domain + role,有时带 agent_id)。"""
    domain_id: str
    role: str
    agent_id: Optional[str] = None

    def is_observer(self) -> bool:
        """小卡特殊身份:observer(K1)。"""
        return self.role == "observer"


@dataclasses.dataclass(frozen=True)
class MemberClause:
    """member_query 的子句(动态成员解析的最小单位)。"""
    type: str  # "role" / "user" / "agent"
    value: str  # role 名 / user 名 / agent 名
    filter_role: Optional[str] = None  # 用于 agent 类型,过滤 role(例:observer)
    filter_status: Optional[str] = None  # 用于 role 类型,过滤 status(例:active)


@dataclasses.dataclass(frozen=True)
class Routine:
    """业务域日常性(像企业,docs/18 §3.1)。"""
    daily: tuple[dict, ...] = ()
    weekly: tuple[dict, ...] = ()


@dataclasses.dataclass(frozen=True)
class BusinessDomain:
    """业务域 5 维身份卡(docs/18 §3.1)。

    字段:
      id: snowflake-like 唯一 ID
      name: 业务域名称
      created_by: user address
      created_at: ISO
      lifecycle: "active" / "archived"
      value_md: 灵魂(value.md 解析结果)
      deontic: 强护栏
      member_query: 动态成员查询字符串(简单子句格式)
      routine: 日常性
      parent_id: 父业务域 ID(子域用)
    """
    id: str
    name: str
    created_by: str  # user:xxx 格式
    created_at: str
    lifecycle: str
    value_md: ValueMd
    deontic: Deontic
    member_query: str
    routine: Routine
    parent_id: Optional[str] = None

    @property
    def soul_subset(self) -> tuple[str, ...]:
        """灵魂级:由 deontic 推,property 只读(D3)。"""
        return derive_soul_subset(self.deontic)


# ---- 注入式生成器 ----
def _default_id_factory() -> str:
    return f"dom-{uuid.uuid4().hex[:8]}"


def _default_timestamp_fn() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---- member_query 解析器 ----
# 段定义:以 MAIN_TYPES(role/user/agent)开头的 token 序列,后续 0+ FILTERS(status/role)。
# 段与段之间用 AND 串联(段之间才能用 AND;段内 token 不能用 AND)。
# 例:
#   "role:engineer AND status:active"
#     → 1 段(主体 role:engineer + 过滤 status:active)
#     → (MemberClause(type="role", value="engineer", filter_status="active"),)
#   "user:ch"
#     → 1 段
#   "agent:karvy AND role:observer"
#     → 1 段(主体 agent:karvy + 过滤 role:observer)
#   "user:ch AND agent:karvy AND role:observer"
#     → 2 段:user 段 + agent 段(带 filter_role)
_MAIN_TYPES: frozenset[str] = frozenset({"role", "user", "agent"})
_FILTER_TYPES: frozenset[str] = frozenset({"status", "role"})
# value 部分用 Unicode \w(+ 连字符)—— 接受中文 role/agent 名(如 agent:设计师);
# type 部分仍 ASCII(role/user/agent/status 固定词)。向后兼容原 ASCII 值。
_TOKEN_PATTERN = re.compile(r"^([A-Za-z]+):([\w\-]+)$", re.UNICODE)


def parse_member_query(query: str) -> tuple[MemberClause, ...]:
    """解析 member_query 字符串为子句列表。

    算法:按 AND 切分 token 流,逐 token 累积,遇到 MAIN_TYPES 开头 → 新建子句;
    遇到 FILTER_TYPES → 给当前子句加 filter;遇到其他 → 跳过(坏数据)。

    例:
      "role:engineer AND status:active"
        → (MemberClause(type="role", value="engineer", filter_status="active"),)
      "user:ch"
        → (MemberClause(type="user", value="ch"),)
      "agent:karvy AND role:observer"
        → (MemberClause(type="agent", value="karvy", filter_role="observer"),)
      "user:ch AND agent:karvy AND role:observer"
        → 2 段
    """
    if not query or not query.strip():
        return ()
    tokens = [t.strip() for t in query.split() if t.strip() and t.strip() != "AND"]
    clauses: list[MemberClause] = []
    current: MemberClause | None = None
    for tok in tokens:
        m = _TOKEN_PATTERN.match(tok)
        if not m:
            continue
        t, v = m.group(1), m.group(2)
        # "role" 同时是主体和过滤项 — 若当前有段且 token 是 role: 优先作为过滤
        if t == "role" and current is not None and current.type != "role":
            current = dataclasses.replace(current, filter_role=v)
            clauses[-1] = current
            continue
        if t in _MAIN_TYPES:
            # 新建段
            current = MemberClause(type=t, value=v, filter_role=None, filter_status=None)
            clauses.append(current)
        elif t in _FILTER_TYPES and current is not None:
            # 给当前段加 filter
            if t == "status":
                current = dataclasses.replace(current, filter_status=v)
                clauses[-1] = current
            elif t == "role":
                current = dataclasses.replace(current, filter_role=v)
                clauses[-1] = current
        # else: 坏数据,跳过
    return tuple(clauses)


# ---- 业务域注册表 ----
class BusinessDomainRegistry:
    """业务域注册表(全部注入,D7)。

    职责:
      - 业务域 CRUD(create/get/list/archive)
      - 动态成员解析(resolve_members)
      - deontic 强制应用(apply_deontic)
      - 子业务域创建(create_child,继承 value.md + deontic,D5)
    """

    def __init__(
        self,
        id_factory: Optional[Callable[[], str]] = None,
        timestamp_fn: Optional[Callable[[], str]] = None,
        agent_directory: Optional[Callable[[str], tuple[dict, ...]]] = None,
        user_directory: Optional[Callable[[], tuple[str, ...]]] = None,
    ) -> None:
        """
        agent_directory(role) -> ({"agent_id": ..., "role": ..., "status": ...}, ...)
            注入式:返回该 role 下所有 agent(用于 role:role 解析)
        user_directory() -> (user_id, ...)
            注入式:返回所有 user(用于 user:user 校验)
        """
        self._id_factory = id_factory or _default_id_factory
        self._timestamp_fn = timestamp_fn or _default_timestamp_fn
        self._agent_directory = agent_directory
        self._user_directory = user_directory
        self._domains: dict[str, BusinessDomain] = {}

    # ---- 创建 ----
    def create(
        self,
        name: str,
        created_by: str,
        value_md_raw: str = "",
        deontic: Optional[Deontic] = None,
        member_query: str = "",
        routine: Optional[Routine] = None,
    ) -> BusinessDomain:
        """创建业务域(AC1)。

        强制:
          - created_by 必须以 "user:" 开头
          - value.md(9.4d)**可选**:空 = 暂无价值观(以后可补),非空须合规范
        """
        if not created_by.startswith("user:"):
            raise ValueError(f"created_by must start with 'user:', got {created_by!r}")
        value_md = ValueMd.parse(value_md_raw)  # 9.4d:空 → 空灵魂(合法)
        domain = BusinessDomain(
            id=self._id_factory(),
            name=name,
            created_by=created_by,
            created_at=self._timestamp_fn(),
            lifecycle=LIFECYCLE_ACTIVE,
            value_md=value_md,
            deontic=deontic or Deontic(),
            member_query=member_query,
            routine=routine or Routine(),
            parent_id=None,
        )
        self._domains[domain.id] = domain
        return domain

    def create_child(
        self,
        parent_id: str,
        name: str,
        created_by: str,
        deontic_override: Deontic,
        member_query: str,
        routine: Optional[Routine] = None,
    ) -> BusinessDomain:
        """创建子业务域(AC4:继承 value.md + deontic,只能加不能删,D5)。"""
        parent = self._domains.get(parent_id)
        if parent is None:
            raise ValueError(f"parent domain {parent_id} not found")
        if parent.lifecycle == LIFECYCLE_ARCHIVED:
            raise ArchivedDomainError(
                f"D6: cannot create child of archived domain {parent_id}"
            )
        # 继承 value.md(不可改)
        merged_deontic = parent.deontic.merged(deontic_override)  # D5
        child = self.create(
            name=name,
            created_by=created_by,
            value_md_raw=parent.value_md.text,  # 继承
            deontic=merged_deontic,
            member_query=member_query,
            routine=routine,
        )
        # 标记 parent_id
        child = dataclasses.replace(child, parent_id=parent_id)
        self._domains[child.id] = child
        return child

    # ---- 查询 ----
    def restore(self, domain: BusinessDomain) -> None:
        """放回一个预建业务域(保留原 id)—— 供 DomainStore 重启加载(拍 9.2c-持久化)。

        与 create 不同:不生成新 id、不重新校验(域已在创建时校验过)。
        """
        self._domains[domain.id] = domain

    def get(self, domain_id: str) -> Optional[BusinessDomain]:
        return self._domains.get(domain_id)

    def list_all(self) -> tuple[BusinessDomain, ...]:
        return tuple(self._domains.values())

    def list_active(self) -> tuple[BusinessDomain, ...]:
        return tuple(d for d in self._domains.values() if d.lifecycle == LIFECYCLE_ACTIVE)

    # ---- 编辑(P0 审计:此前建错只能删重建)----
    def update(self, domain_id: str, *, value_md_raw: Optional[str] = None,
               member_query: Optional[str] = None) -> BusinessDomain:
        """编辑业务域:改价值观(value.md)/ 成员(member_query)。只改传入字段;archived 域拒改。"""
        d = self._domains.get(domain_id)
        if d is None:
            raise ValueError(f"domain {domain_id} not found")
        if d.lifecycle == LIFECYCLE_ARCHIVED:
            raise ArchivedDomainError(f"domain {domain_id} 已归档,先恢复再改")
        changes: dict = {}
        if value_md_raw is not None:
            changes["value_md"] = ValueMd.parse(value_md_raw)
        if member_query is not None:
            changes["member_query"] = member_query
        if changes:
            d = dataclasses.replace(d, **changes)
            self._domains[domain_id] = d
        return d

    # ---- 归档 / 恢复 ----
    def archive(self, domain_id: str) -> BusinessDomain:
        """归档业务域(AC5:archived 不接受新请求,只读)。"""
        d = self._domains.get(domain_id)
        if d is None:
            raise ValueError(f"domain {domain_id} not found")
        new = dataclasses.replace(d, lifecycle=LIFECYCLE_ARCHIVED)
        self._domains[domain_id] = new
        return new

    def unarchive(self, domain_id: str) -> BusinessDomain:
        """取消归档 → 恢复 active(P0 审计:registry 只有 archive、没法恢复)。"""
        d = self._domains.get(domain_id)
        if d is None:
            raise ValueError(f"domain {domain_id} not found")
        new = dataclasses.replace(d, lifecycle=LIFECYCLE_ACTIVE)
        self._domains[domain_id] = new
        return new

    # ---- 动态成员解析(AC3,D4)----
    def resolve_members(self, domain_id: str) -> tuple[Address, ...]:
        """动态解析业务域成员(从 member_query 解析)。"""
        d = self._domains.get(domain_id)
        if d is None:
            raise ValueError(f"domain {domain_id} not found")
        clauses = parse_member_query(d.member_query)
        members: list[Address] = []
        for c in clauses:
            if c.type == "user":
                members.append(Address(domain_id=domain_id, role="user", agent_id=c.value))
            elif c.type == "agent":
                # 特殊:小卡 observer 身份(K1 灵魂级)
                # agent_id == "karvy" 永远 → observer
                if c.value == "karvy" or c.filter_role == "observer":
                    members.append(
                        Address(domain_id=domain_id, role="observer", agent_id=c.value)
                    )
                else:
                    # 默认 role="agent"(不假定小卡是唯一 agent)
                    members.append(
                        Address(domain_id=domain_id, role="agent", agent_id=c.value)
                    )
            elif c.type == "role":
                # 通过 agent_directory 解析该 role 下所有 agent
                if self._agent_directory is None:
                    continue  # 未注入 = 跳过(测试可注入)
                agents = self._agent_directory(c.value)
                for ag in agents:
                    if c.filter_status and ag.get("status") != c.filter_status:
                        continue
                    members.append(
                        Address(
                            domain_id=domain_id,
                            role=ag.get("role", c.value),
                            agent_id=ag.get("agent_id"),
                        )
                    )
        return tuple(members)

    # ---- deontic 强制应用(AC7)----
    def apply_deontic(
        self,
        domain_id: str,
        action: str,
        *,
        mode: str = "report",
    ) -> "apply_deontic_Result":
        """应用 deontic(D7 注入式)。"""
        d = self._domains.get(domain_id)
        if d is None:
            raise ValueError(f"domain {domain_id} not found")
        if d.lifecycle == LIFECYCLE_ARCHIVED:
            # archived 业务域 apply_deontic 不抛(只读),但 create_child 抛
            result = apply_deontic(d.deontic, action, mode="report")
        else:
            result = apply_deontic(d.deontic, action, mode=mode)
        # 包装一层(返回 domain 信息)
        return apply_deontic_Result(
            domain_id=domain_id,
            lifecycle=d.lifecycle,
            deontic_result=result,
        )


@dataclasses.dataclass(frozen=True)
class apply_deontic_Result:
    """apply_deontic 的包装结果(含 domain 信息)。"""
    domain_id: str
    lifecycle: str
    deontic_result: object  # DeonticResult(避免循环 import)
