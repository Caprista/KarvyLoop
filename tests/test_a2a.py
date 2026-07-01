"""a2a —— M3 路线 C 拍 2:A2A 协议测试(8 个:7 AC + 1 协议)。

设计:docs/19 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import (  # noqa: E402
    EXPECTED_ENVELOPE_TYPES,
    KARVY_AGENT_ID,
    BROADCAST_TYPE,
    REJECT_CROSS_DOMAIN,
    REJECT_OBSERVER_FILTER,
    TASK_ASSIGN_TYPE,
    AddressResolver,
    AuditChain,
    BroadcastPayload,
    ByContainsFromError,
    DomainLifecycleQuery,
    Envelope,
    EnvelopeRouter,
    EnvelopeType,
    FromKarvyForbiddenError,
    Inbox,
    QA,
    ProposePayload,
    RejectMissingReasonError,
    RejectPayload,
    RouteResult,
    TaskPayload,
    sign_envelope,
    verify_envelope,
)
from karvyloop.domain import Address


# ---------- fixtures ----------

def _user(addr: str = "ch", domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="user", agent_id=addr)


def _engineer(agent: str = "eng-1", domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="engineer", agent_id=agent)


def _pm(agent: str = "pm-1", domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="pm", agent_id=agent)


def _karvy_observer(domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="observer", agent_id="karvy")


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _build_envelope(
    *,
    env_type: str,
    from_: Address,
    to: Address,
    by: tuple = (),
    payload=None,
) -> Envelope:
    """构造已签名 envelope(测试用,默认 secret=b"")。"""
    env = Envelope(
        type=env_type,
        from_=from_,
        by=by,
        to=to,
        payload=payload or TaskPayload(task_id="t1", description="d1"),
        ts=_now_ts(),
        signature=b"",
    )
    return Envelope(
        type=env.type,
        from_=env.from_,
        by=env.by,
        to=env.to,
        payload=env.payload,
        ts=env.ts,
        signature=sign_envelope(env),
    )


def _build_router(domain_lc: dict | None = None) -> EnvelopeRouter:
    """构造一个 EnvelopeRouter(默认 secret=b"",无 lifecycle 过滤)。"""
    inbox = Inbox()
    audit = AuditChain()
    lc: DomainLifecycleQuery | None = None
    if domain_lc is not None:
        def _q(domain_id: str):
            return domain_lc.get(domain_id)
        lc = _q
    return EnvelopeRouter(
        inbox=inbox,
        audit_chain=audit,
        domain_lifecycle=lc,
    )


# ---------- AC1: 11 种 envelope 全有 ----------

class TestAC1EnvelopeTypes:
    """AC1: EnvelopeType 枚举锁定 11 键。"""

    def test_eleven_types_present(self):
        assert len(EXPECTED_ENVELOPE_TYPES) == 11

    def test_all_documented_types(self):
        # 11 种 docs/19 §3.1
        expected = {
            "task.assign", "task.progress", "task.done",
            "ask", "answer",
            "propose", "accept", "reject",
            "broadcast",
            "audit.request", "audit.response",
        }
        assert set(EXPECTED_ENVELOPE_TYPES) == expected

    def test_envelope_type_is_string_enum(self):
        # str 枚举的 value 可直接用
        assert EnvelopeType.TASK_ASSIGN.value == "task.assign"
        assert isinstance(EnvelopeType.TASK_ASSIGN.value, str)


# ---------- AC2: from: karvy 拒 ----------

class TestAC2FromKarvyForbidden:
    """AC2: 构造 from=karvy 抛 FromKarvyForbiddenError(A1)。"""

    def test_from_karvy_raises(self):
        with pytest.raises(FromKarvyForbiddenError):
            Envelope(
                type="ask",
                from_=_karvy_observer("dom-1"),
                by=(),
                to=_engineer(),
                payload=QA(question="q"),
                ts=_now_ts(),
            )


# ---------- AC3: from: user, by: karvy 合法 ----------

class TestAC3CourierTransparent:
    """AC3: 构造 from=user, by=(karvy,) 不抛 + 签过 + 审计链记录 (user, (karvy,))。"""

    def test_construction_does_not_raise(self):
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            by=(_karvy_observer(),),
            payload=QA(question="帮我 review 一下代码"),
        )
        assert env.from_.role == "user"
        assert env.by[0].agent_id == "karvy"

    def test_signature_verifies(self):
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            by=(_karvy_observer(),),
            payload=QA(question="q"),
        )
        assert verify_envelope(env)

    def test_audit_chain_records_user_and_karvy(self):
        router = _build_router()
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            by=(_karvy_observer(),),
            payload=QA(question="q"),
        )
        result = router.route(env)
        assert not result.rejected
        chain = router._audit
        assert len(chain) == 1
        entry = chain.all()[0]
        # A5: 审计链记录 from_ + by
        assert entry.from_.role == "user"
        assert entry.by[0].agent_id == "karvy"


# ---------- AC4: by 含 from_ 拒 ----------

class TestAC4ByContainsFrom:
    """AC4: 构造 from=A, by=(A,) 抛 ByContainsFromError(A2)。"""

    def test_by_contains_from_raises(self):
        with pytest.raises(ByContainsFromError):
            Envelope(
                type="ask",
                from_=_user(),
                by=(_user(),),  # 包含 from_ 本身
                to=_engineer(),
                payload=QA(question="q"),
                ts=_now_ts(),
            )


# ---------- AC5: 小卡是 observer 时只收 BROADCAST ----------

class TestAC5KarvyObserverFilter:
    """AC5: 发 TASK_ASSIGN 到 karvy 返 rejected=True, reason="observer_filter"(A6)。"""

    def test_task_assign_to_karvy_observer_rejected(self):
        router = _build_router()
        env = _build_envelope(
            env_type="task.assign",
            from_=_pm(),
            to=_karvy_observer(),
            payload=TaskPayload(task_id="t1", description="d1"),
        )
        result = router.route(env)
        assert result.rejected is True
        assert result.reason == REJECT_OBSERVER_FILTER

    def test_broadcast_to_karvy_observer_accepted(self):
        router = _build_router()
        env = _build_envelope(
            env_type="broadcast",
            from_=_pm(),
            to=_karvy_observer(),
            payload=BroadcastPayload(message="新任务", tag="task"),
        )
        result = router.route(env)
        assert result.rejected is False


# ---------- AC6: TASK_ASSIGN 不能跨域 ----------

class TestAC6CrossDomain:
    """AC6: 发 to=domain:dom-X(不在本域) 返 rejected=True, reason="cross_domain_forbidden"(A7)。"""

    def test_cross_domain_task_assign_rejected(self):
        router = _build_router()
        # from 在 dom-1,to 在 dom-2(跨域)
        env = _build_envelope(
            env_type="task.assign",
            from_=_pm(domain="dom-1"),
            to=_engineer(domain="dom-2"),
            payload=TaskPayload(task_id="t1", description="d1"),
        )
        result = router.route(env)
        assert result.rejected is True
        assert result.reason == REJECT_CROSS_DOMAIN

    def test_same_domain_task_assign_accepted(self):
        router = _build_router()
        env = _build_envelope(
            env_type="task.assign",
            from_=_pm(domain="dom-1"),
            to=_engineer(domain="dom-1"),
            payload=TaskPayload(task_id="t1", description="d1"),
        )
        result = router.route(env)
        assert result.rejected is False


# ---------- AC7: REJECT 必须带 reason ----------

class TestAC7RejectReason:
    """AC7: 构造 REJECT 无 reason 抛 RejectMissingReasonError(A8)。"""

    def test_reject_without_reason_raises(self):
        with pytest.raises(RejectMissingReasonError):
            Envelope(
                type="reject",
                from_=_user(),
                by=(),
                to=_pm(),
                payload=RejectPayload(reason=""),  # 空
                ts=_now_ts(),
            )

    def test_reject_with_reason_accepted(self):
        env = _build_envelope(
            env_type="reject",
            from_=_user(),
            to=_pm(),
            payload=RejectPayload(reason="预算超支"),
        )
        assert env.type == "reject"
        assert env.payload.reason == "预算超支"


# ---------- AC8: 协议不变量 ----------

class TestAC8ProtocolInvariants:
    """AC8: 8 不变量锁定(协议测试)+ 审计链 = from_ + by 全记录。"""

    def test_karvy_agent_id_constant(self):
        assert KARVY_AGENT_ID == "karvy"

    def test_audit_chain_records_from_and_by(self):
        """A5: 审计链 = from_ + by 全记录。"""
        audit = AuditChain()
        router = EnvelopeRouter(inbox=Inbox(), audit_chain=audit)
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            by=(_karvy_observer(), _pm()),  # 多跳代发
            payload=QA(question="q"),
        )
        router.route(env)
        entry = audit.all()[0]
        # from_ + by 全记录
        assert entry.from_.role == "user"
        assert len(entry.by) == 2
        assert entry.by[0].agent_id == "karvy"
        assert entry.by[1].role == "pm"

    def test_audit_chain_sequence_increments(self):
        audit = AuditChain()
        router = EnvelopeRouter(inbox=Inbox(), audit_chain=audit)
        for i in range(3):
            env = _build_envelope(
                env_type="ask",
                from_=_user(),
                to=_engineer(),
                payload=QA(question=f"q{i}"),
            )
            router.route(env)
        seqs = [e.sequence for e in audit.all()]
        assert seqs == [1, 2, 3]

    def test_inbox_deliver_and_fetch(self):
        """Inbox: deliver + fetch。"""
        inbox = Inbox()
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload=QA(question="q"),
        )
        inbox.deliver(_engineer(), env)
        items = inbox.fetch(_engineer())
        assert len(items) == 1
        # 二次 fetch 返空(atomic)
        assert inbox.fetch(_engineer()) == ()

    def test_bad_signature_rejected(self):
        """签验: 改 ts 后签失效 → router 拒。"""
        router = EnvelopeRouter(inbox=Inbox(), audit_chain=AuditChain())
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload=QA(question="q"),
        )
        # 改 ts 不重签
        bad_env = Envelope(
            type=env.type,
            from_=env.from_,
            by=env.by,
            to=env.to,
            payload=env.payload,
            ts="2099-01-01T00:00:00+00:00",
            signature=env.signature,  # 旧签
        )
        result = router.route(bad_env)
        assert result.rejected is True
        assert result.reason == "bad_signature"

    def test_no_llm_imports(self):
        """K8: 不调 LLM。源码扫 openai/anthropic/litellm。"""
        import karvyloop.a2a as mod
        import inspect
        src = inspect.getsource(mod)
        for forbidden in ("openai", "anthropic", "litellm"):
            assert forbidden not in src, f"a2a/{mod.__name__} imports {forbidden}"

    def test_archived_domain_rejected(self):
        """archived 业务域: 投递时 domain_lifecycle=archived → 拒。"""
        router = _build_router(domain_lc={"dom-1": "archived"})
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload=QA(question="q"),
        )
        result = router.route(env)
        assert result.rejected is True
        assert result.reason == "domain_archived"
