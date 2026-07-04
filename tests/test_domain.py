"""domain —— M3 路线 C 拍 1:业务域测试(8 个:7 AC + 1 协议)。

设计:docs/18 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.domain import (  # noqa: E402
    ADDR_OBSERVER,
    ADDR_USER,
    Address,
    ArchivedDomainError,
    BusinessDomain,
    BusinessDomainRegistry,
    Deontic,
    Routine,
    SOUL_FILES,
    ValueMd,
    ValueMdFormatError,
    ValueMdRequiredError,
    apply_deontic,
    assert_active,
    derive_soul_subset,
    parse_member_query,
)


# ---------- fixtures ----------

VALID_VALUE_MD = """# 价值观
- 我们坚信产品解决真实问题
- 不接受"先把功能做出来再说"
- 每周一迭代,每周五复盘
"""


def _user_address(name: str = "ch") -> Address:
    return Address(domain_id="dom-tmp", role="user", agent_id=name)


def _empty_registry() -> BusinessDomainRegistry:
    """空 registry(全部用默认注入)。"""
    return BusinessDomainRegistry()


def _registry_with_agents() -> BusinessDomainRegistry:
    """注入 agent_directory,用于动态成员解析。"""
    agents = [
        {"agent_id": "eng-1", "role": "engineer", "status": "active"},
        {"agent_id": "eng-2", "role": "engineer", "status": "active"},
        {"agent_id": "eng-3", "role": "engineer", "status": "inactive"},
        {"agent_id": "pm-1", "role": "pm", "status": "active"},
    ]
    return BusinessDomainRegistry(
        agent_directory=lambda role: tuple(a for a in agents if a["role"] == role),
    )


# ---------- AC1: 业务域创建(含 value.md)----------

class TestAC1Create:
    """AC1: create() 必须返回 5 维身份卡;value.md(9.4d)可选(空=空灵魂),非空须合规范。"""

    def test_create_with_valid_value_md(self):
        r = _empty_registry()
        d = r.create(
            name="PRD-2026Q3 探索",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        # 5 维身份卡
        assert d.id.startswith("dom-")
        assert d.name == "PRD-2026Q3 探索"
        assert d.created_by == "user:ch"
        assert d.lifecycle == "active"
        assert isinstance(d.value_md, ValueMd)
        assert d.value_md.principles  # 至少一条

    def test_create_allows_empty_value_md(self):
        """9.4d:value.md 创建时可选 —— 空 = 空灵魂(合法,以后可补),不再抛。"""
        r = _empty_registry()
        d = r.create(
            name="空域",
            created_by="user:ch",
            value_md_raw="",
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        assert isinstance(d.value_md, ValueMd)
        assert d.value_md.is_empty
        assert d.value_md.principles == ()

    def test_create_allows_omitted_value_md(self):
        """9.4d:value_md_raw 默认 "" —— 完全不传也合法。"""
        r = _empty_registry()
        d = r.create(name="无值域", created_by="user:ch", member_query="user:ch")
        assert d.value_md.is_empty

    def test_create_allows_short_value_md(self):
        """9.4d:取消最小长度 —— 只要非空合规范(以 '# 价值观' 开头)即可,哪怕很短。"""
        r = _empty_registry()
        d = r.create(
            name="短域",
            created_by="user:ch",
            value_md_raw="# 价值观\n- 短",
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        assert d.value_md.principles == ("短",)

    def test_create_rejects_wrong_format_value_md(self):
        r = _empty_registry()
        with pytest.raises(ValueMdFormatError):
            r.create(
                name="错格式域",
                created_by="user:ch",
                value_md_raw="我们没有标题\n- 原则 1\n- 原则 2",
                deontic=Deontic.empty(),
                member_query="user:ch",
            )

    def test_create_rejects_non_user_creator(self):
        r = _empty_registry()
        with pytest.raises(ValueError):
            r.create(
                name="非用户创建",
                created_by="agent:foo",
                value_md_raw=VALID_VALUE_MD,
                deontic=Deontic.empty(),
                member_query="user:ch",
            )


# ---------- AC2: deontic 推 soul_subset ----------

class TestAC2SoulSubset:
    """AC2: derive_soul_subset(deontic) 返回子集(不接受外部传)。"""

    def test_empty_deontic_yields_base_subset(self):
        subset = derive_soul_subset(Deontic.empty())
        # 基本盘: SOUL + USER + IDENTITY(无 forbid/oblige 时也含 IDENTITY)
        assert "SOUL" in subset
        assert "USER" in subset
        assert "IDENTITY" in subset
        # 空 deontic 不强制 VERIFY/COMMITMENT
        assert "VERIFY" not in subset
        assert "COMMITMENT" not in subset

    def test_forbid_yields_verify(self):
        subset = derive_soul_subset(Deontic(forbid=("无验证门的代码提交",), oblige=(), permit=()))
        assert "VERIFY" in subset

    def test_oblige_yields_commitment(self):
        subset = derive_soul_subset(Deontic(forbid=(), oblige=("每日 17 点前提交摘要",), permit=()))
        assert "COMMITMENT" in subset

    def test_subset_is_tuple_of_soul_files(self):
        subset = derive_soul_subset(Deontic(forbid=("x",), oblige=("y",), permit=("z",)))
        for f in subset:
            assert f in SOUL_FILES

    def test_domain_soul_subset_is_property(self):
        """D3: soul_subset 是 property,不能外部传。"""
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic(forbid=("x",)),
            member_query="user:ch",
        )
        # property 派生,非字段
        subset = d.soul_subset
        assert "VERIFY" in subset
        assert isinstance(subset, tuple)


# ---------- AC3: 动态成员解析 ----------

class TestAC3MemberResolve:
    """AC3: resolve_members(domain) 从 member_query 动态解析(3 种 clause)。"""

    def test_user_clause(self):
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        members = r.resolve_members(d.id)
        assert len(members) == 1
        assert members[0].role == "user"
        assert members[0].agent_id == "ch"

    def test_role_clause_with_status_filter(self):
        r = _registry_with_agents()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="role:engineer AND status:active",
        )
        members = r.resolve_members(d.id)
        # eng-1 + eng-2(active),eng-3(inactive 被过滤)
        assert len(members) == 2
        agent_ids = {m.agent_id for m in members}
        assert agent_ids == {"eng-1", "eng-2"}

    def test_karvy_observer_clause(self):
        """K6: karvy 出现在 member_query 时是 observer(K1)。"""
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch AND agent:karvy AND role:observer",
        )
        members = r.resolve_members(d.id)
        observer = [m for m in members if m.role == "observer"]
        assert len(observer) == 1
        assert observer[0].agent_id == "karvy"


# ---------- AC4: 子业务域继承 ----------

class TestAC4ChildInheritance:
    """AC4: create_child() 必须继承 value.md + deontic(不能删)。"""

    def test_child_inherits_value_md(self):
        r = _empty_registry()
        parent = r.create(
            name="父域",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        child = r.create_child(
            parent_id=parent.id,
            name="子域",
            created_by="user:ch",
            deontic_override=Deontic.empty(),
            member_query="user:ch",
        )
        assert child.parent_id == parent.id
        # 继承 value.md
        assert child.value_md.text == parent.value_md.text

    def test_child_merges_deontic_only_additive(self):
        """D5: 父+子并集,子不能删父。"""
        r = _empty_registry()
        parent = r.create(
            name="父域",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic(forbid=("无验证门",), oblige=("日报",), permit=("圆桌",)),
            member_query="user:ch",
        )
        child = r.create_child(
            parent_id=parent.id,
            name="子域",
            created_by="user:ch",
            deontic_override=Deontic(forbid=("代码无注释",), oblige=(), permit=()),
            member_query="user:ch",
        )
        # 父+子并集
        assert "无验证门" in child.deontic.forbid
        assert "代码无注释" in child.deontic.forbid
        assert "日报" in child.deontic.oblige
        assert "圆桌" in child.deontic.permit


# ---------- AC5: archived 只读 ----------

class TestAC5Archived:
    """AC5: lifecycle=archived 时调 create_child 抛 ArchivedDomainError。"""

    def test_create_child_of_archived_raises(self):
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        r.archive(d.id)
        with pytest.raises(ArchivedDomainError):
            r.create_child(
                parent_id=d.id,
                name="子域",
                created_by="user:ch",
                deontic_override=Deontic.empty(),
                member_query="user:ch",
            )

    def test_assert_active_on_archived_raises(self):
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch",
        )
        r.archive(d.id)
        archived = r.get(d.id)
        assert archived is not None
        assert archived.lifecycle == "archived"
        with pytest.raises(ArchivedDomainError):
            assert_active(archived)

    def test_apply_deontic_on_archived_does_not_raise(self):
        """AC5 边界: apply_deontic 不抛(只读,返报告)。"""
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic(forbid=("无验证门",)),
            member_query="user:ch",
        )
        r.archive(d.id)
        result = r.apply_deontic(d.id, "无验证门")
        assert result.lifecycle == "archived"
        assert result.deontic_result.forbidden is True


# ---------- AC6: 小卡是 observer ----------

class TestAC6KarvyObserver:
    """AC6: member_query 中加 agent:karvy AND role:observer 时,返回 observer 地址。"""

    def test_karvy_resolves_as_observer_not_agent(self):
        r = _empty_registry()
        d = r.create(
            name="域A",
            created_by="user:ch",
            value_md_raw=VALID_VALUE_MD,
            deontic=Deontic.empty(),
            member_query="user:ch AND agent:karvy AND role:observer",
        )
        members = r.resolve_members(d.id)
        karvy = [m for m in members if m.agent_id == "karvy"]
        assert len(karvy) == 1
        m = karvy[0]
        # 是 observer 不是 agent
        assert m.role == "observer"
        assert m.is_observer() is True


# ---------- AC7: deontic 强护栏 ----------

class TestAC7Deontic:
    """AC7: apply_deontic 对 forbid 必返 forbid_violations(测试抛错和报告两种模式)。"""

    def test_forbidden_action_detected(self):
        deontic = Deontic(forbid=("无验证门的代码提交",), oblige=(), permit=())
        result = apply_deontic(deontic, "无验证门的代码提交", mode="report")
        assert result.forbidden is True
        assert result.allowed is False

    def test_required_action_detected(self):
        deontic = Deontic(forbid=(), oblige=("每日 17 点前提交摘要",), permit=())
        result = apply_deontic(deontic, "每日 17 点前提交摘要", mode="report")
        assert result.required is True

    def test_report_mode_does_not_raise(self):
        deontic = Deontic(forbid=("x",), oblige=(), permit=())
        # report 模式不抛
        result = apply_deontic(deontic, "x", mode="report")
        assert result.forbidden is True

    def test_enforce_mode_raises(self):
        deontic = Deontic(forbid=("x",), oblige=(), permit=())
        # enforce 模式**真抛**(docs/54 B1:此前"留给 M3+ = 不强抛"是假接线,已修;
        # 执行路径的工具级硬闸另见 capability/deontic_gate + tests/test_deontic_gate.py)
        import pytest as _pytest
        from karvyloop.domain.deontic import DeonticViolationError
        with _pytest.raises(DeonticViolationError):
            apply_deontic(deontic, "x", mode="enforce")
        # 未违规不抛,照常返回结果
        assert apply_deontic(deontic, "y", mode="enforce").forbidden is False


# ---------- AC8: 协议不变量 ----------

class TestAC8ProtocolInvariants:
    """AC8: 8 不变量锁定(协议测试)+ 不调 LLM(源码扫 openai/anthropic/litellm)。"""

    def test_soul_files_count(self):
        """SOUL_FILES = 7 文件。"""
        assert len(SOUL_FILES) == 7

    def test_member_query_parsed_3_clause_types(self):
        """3 种 clause 类型: role / user / agent。"""
        c1 = parse_member_query("user:ch")
        assert len(c1) == 1
        assert c1[0].type == "user"
        c2 = parse_member_query("role:engineer AND status:active")
        assert len(c2) == 1
        assert c2[0].type == "role"
        c3 = parse_member_query("agent:karvy AND role:observer")
        assert len(c3) == 1
        assert c3[0].type == "agent"
        assert c3[0].filter_role == "observer"

    def test_d5_deontic_merged_is_only_additive(self):
        """D5: 子域 deontic 不能删父域(并集)。"""
        parent = Deontic(forbid=("A",), oblige=("B",), permit=("C",))
        child = Deontic(forbid=("D",), oblige=("E",), permit=("F",))
        merged = parent.merged(child)
        # 父+子都保留
        for f in ("A", "D"):
            assert f in merged.forbid
        for o in ("B", "E"):
            assert o in merged.oblige
        for p in ("C", "F"):
            assert p in merged.permit

    def test_routine_dataclass(self):
        """Routine 强类型(daily/weekly tuple)。"""
        r = Routine(daily=({"type": "x"},), weekly=())
        assert len(r.daily) == 1
        assert len(r.weekly) == 0

    def test_no_llm_imports(self):
        """K8/D8: 不调 LLM。源码扫 openai/anthropic/litellm。"""
        import karvyloop.domain as mod
        import inspect
        src = inspect.getsource(mod)
        for forbidden in ("openai", "anthropic", "litellm"):
            assert forbidden not in src, f"domain/{mod.__name__} imports {forbidden}"
