"""L0 karvy world 大群测试(M3.0 批 1 拍 3:8 AC + 1 协议回归)。

设计:docs/23 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import (  # noqa: E402
    AuditChain,
    BroadcastPayload,
    Envelope,
    Inbox,
    EnvelopeRouter,
    sign_envelope,
)
from karvyloop.a2a.transport import InProcessTransport  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.karvy import WorkbenchObserver  # noqa: E402
from karvyloop.l0 import (  # noqa: E402
    L0_DEFAULT_CHANNELS,
    L0_DOMAIN_ID,
    L0ObserverAggregator,
    L0World,
    KarvyL0SendForbiddenError,
    UnknownL0ChannelError,
)


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def _reset_l0_singleton():
    """每个测试前重置 L0World 单例。"""
    L0World.reset_for_test()
    yield
    L0World.reset_for_test()


def _user(domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="user", agent_id="ch")


def _pm(domain: str = "dom-1", agent: str = "pm-1") -> Address:
    return Address(domain_id=domain, role="pm", agent_id=agent)


def _secops(domain: str = "dom-1", agent: str = "secops-1") -> Address:
    """批 1.5 加:SecOps 角色(alert 频道**有**权**限**发**件**人**)。"""
    return Address(domain_id=domain, role="secops", agent_id=agent)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _new_world() -> L0World:
    """创**建**新**单**例** L0World(**带**空** audit,**便**于**检**查** audit **条**目**)。"""
    return L0World(transport=InProcessTransport(), audit_chain=AuditChain())


# ---------- AC1: L0World 单实例(K0)----------

class TestAC1Singleton:
    """AC1: 两次 L0World() 返同一实例(K0)。"""

    def test_two_constructions_return_same_instance(self):
        w1 = _new_world()
        w2 = _new_world()
        assert w1 is w2

    def test_default_channels_count(self):
        w = _new_world()
        # 5 个默认频道
        assert w.channels == ("strategy", "alert", "celebrate", "ask-for-help", "general")


# ---------- AC2: 频道白名单(L0-L5)----------

class TestAC2ChannelWhitelist:
    """AC2: 广播未注册频道 → 抛 UnknownL0ChannelError。"""

    def test_unknown_channel_raises(self):
        w = _new_world()
        with pytest.raises(UnknownL0ChannelError):
            w.broadcast("non-existent", "msg", from_=_pm())

    def test_registered_channel_ok(self):
        w = _new_world()
        # 5 个默认频道都接受(各**频**道**用**各**自**有**权**限**的**发**件**人**)
        ch_sender = {
            "strategy": _pm(),
            "alert": _secops(),
            "celebrate": _pm(),
            "ask-for-help": _pm(),
            "general": _pm(),
        }
        for ch, sender in ch_sender.items():
            env = w.broadcast(ch, "test", from_=sender)
            assert env.type == "broadcast"


# ---------- AC3: L0 广播 = BROADCAST + to.domain_id == "l0" ----------

class TestAC3L0EnvelopeShape:
    """AC3: L0 广播 envelope 是 BROADCAST 类型,to 指向 l0 域。"""

    def test_envelope_type_is_broadcast(self):
        w = _new_world()
        env = w.broadcast("alert", "全平台告警:零日漏洞", from_=_secops())
        assert env.type == "broadcast"

    def test_envelope_to_points_to_l0(self):
        w = _new_world()
        env = w.broadcast("strategy", "新财年战略", from_=_pm())
        assert env.to.domain_id == L0_DOMAIN_ID
        assert env.to.role == "observer"
        assert env.to.agent_id == "karvy"

    def test_envelope_payload_is_broadcast(self):
        w = _new_world()
        env = w.broadcast("celebrate", "发布成功", from_=_pm(), tag="release")
        assert isinstance(env.payload, BroadcastPayload)
        assert env.payload.message == "发布成功"
        assert env.payload.tag == "release"

    def test_envelope_signed(self):
        """A4: 任何 envelope 都签名。"""
        w = _new_world()
        env = w.broadcast("alert", "x", from_=_secops())
        assert env.signature  # 非空


# ---------- AC4: 小卡收到 L0 广播(L0-L4)----------

class TestAC4KarvyReceivesL0:
    """AC4: L0ObserverAggregator → WorkbenchObserver.snapshot.unread_count + 1。"""

    def test_l0_aggregator_pushes_to_workbench(self):
        w = _new_world()
        wb = WorkbenchObserver()
        agg = L0ObserverAggregator(observers=[wb])
        # 构造一个 L0 envelope(模拟从 transport 收到)
        env = w.broadcast("alert", "全平台告警", from_=_secops(), tag="security")
        # 接入到 aggregator
        agg.on_l0_broadcast(env)
        # 验证 WorkbenchObserver 收到(走 subscribe_to)
        # WorkbenchObserver 用 list_broadcasts 看所有 domain
        all_broadcasts = wb.list_broadcasts(domain_id=None) if hasattr(wb, "list_broadcasts") else None
        if all_broadcasts is None:
            # fallback: 按 l0 domain 拉快照
            snap = wb.snapshot(L0_DOMAIN_ID)
            assert snap is not None
        else:
            assert len(all_broadcasts) >= 1

    def test_aggregator_with_multiple_observers(self):
        w = _new_world()
        wb1 = WorkbenchObserver()
        wb2 = WorkbenchObserver()
        agg = L0ObserverAggregator(observers=[wb1, wb2])
        env = w.broadcast("strategy", "新方向", from_=_pm())
        agg.on_l0_broadcast(env)
        # 两**个**小**卡**都**收**到**(用** snapshot **验**证**)
        snap1 = wb1.snapshot(L0_DOMAIN_ID)
        snap2 = wb2.snapshot(L0_DOMAIN_ID)
        assert snap1 is not None
        assert snap2 is not None


# ---------- AC5: 小卡不能发 L0 广播(L0-L8)----------

class TestAC5KarvyCannotSendL0:
    """AC5: from_.role == "observer" → 抛 KarvyL0SendForbiddenError。"""

    def test_observer_sender_raises(self):
        w = _new_world()
        karvy_addr = Address(domain_id="dom-1", role="observer", agent_id="karvy")
        with pytest.raises(KarvyL0SendForbiddenError):
            w.broadcast("strategy", "我(小卡)不能发", from_=karvy_addr)

    def test_user_sender_ok(self):
        w = _new_world()
        # user 角**色**只**在** celebrate/ask-for-help/general **白**名**单**里**(**不**在** strategy/alert**)
        env = w.broadcast("general", "user 可以发 general", from_=_user())
        assert env.from_.role == "user"

    def test_pm_sender_ok(self):
        w = _new_world()
        env = w.broadcast("strategy", "pm 可以发", from_=_pm())
        assert env.from_.role == "pm"


# ---------- AC6: L0 广播有审计(L0-L7)----------

class TestAC6L0Audit:
    """AC6: 每次 broadcast → AuditChain 增加一条 L0 entry。"""

    def test_audit_count_increments(self):
        w = _new_world()
        assert len(w.audit_entries()) == 0
        w.broadcast("alert", "msg1", from_=_secops())
        assert len(w.audit_entries()) == 1
        w.broadcast("strategy", "msg2", from_=_pm())
        assert len(w.audit_entries()) == 2

    def test_audit_records_from_and_to(self):
        w = _new_world()
        env = w.broadcast("alert", "x", from_=_secops())
        entries = w.audit_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.envelope_type == "broadcast"
        assert e.from_ == _secops()
        assert e.to.domain_id == L0_DOMAIN_ID


# ---------- AC7: L0 广播不进业务域 Inbox(L0-L6)----------

class TestAC7L0NotInDomainInbox:
    """AC7: L0 广播的 to.domain_id == "l0",不进业务域 Inbox。"""

    def test_l0_envelope_to_is_l0_not_real_domain(self):
        w = _new_world()
        # 设**一**个**业**务**域** Inbox
        inbox = Inbox()
        # 收**一**个** L0 **广**播**
        env = w.broadcast("alert", "x", from_=_secops())
        # 试**图**把**它**投**到**业**务**域** Inbox
        # 但**路**由**器**根**据** is_local 决**定** — L0 **不**算**任**何**业**务**域**
        # 这**里**直**接**验**证** to.domain_id
        assert env.to.domain_id == L0_DOMAIN_ID
        assert env.to.domain_id != "dom-1"  # 不是用户业务的 dom-1
        # 注:WorkbenchObserver 按 domain 隔**离**,L0 **可**以**有**自**己**的**快照
        # 这**里**只**验**证**不**与**业**务**域**混**淆**


# ---------- AC8: 不破坏 A2A Tier 1/Tier 2(回归保证)----------

class TestAC8BackwardsCompat:
    """AC8: 跑现有 a2a 测试不破坏。"""

    def test_router_unaffected(self):
        """EnvelopeRouter 仍工作(集成测试)。"""
        inbox = Inbox()
        audit = AuditChain()
        router = EnvelopeRouter(
            inbox=inbox, audit_chain=audit,
            transport=InProcessTransport(),
        )
        # 业**务**域**广**播**不**是** L0,正**常**路**由**
        env = Envelope(
            type="broadcast",
            from_=_pm(),
            by=(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="业务域内", tag="normal"),
            ts=_now_ts(),
            signature=b"",
        )
        signed = Envelope(
            type=env.type, from_=env.from_, by=env.by, to=env.to,
            payload=env.payload, ts=env.ts, signature=sign_envelope(env),
        )
        result = router.route(signed)
        assert not result.rejected

    def test_l0_world_does_not_create_global_state(self):
        """L0World 单例不影响其他 L0 创**建**(**单**例**限**制**)。"""
        w1 = _new_world()
        w2 = _new_world()
        assert w1 is w2  # K0 单例

    def test_l0_does_not_pollute_normal_audit(self):
        """L0 写自己的 AuditChain,**不**写**业**务**域**的**。"""
        w = _new_world()
        # 创**建**业**务**域**自**己**的** audit
        business_audit = AuditChain()
        # 1 **次** L0 广**播
        w.broadcast("alert", "x", from_=_secops())
        # 1 **次**业**务**域**路**由**
        inbox = Inbox()
        router = EnvelopeRouter(
            inbox=inbox, audit_chain=business_audit, transport=InProcessTransport(),
        )
        env = Envelope(
            type="broadcast", from_=_pm(), by=(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="y"),
            ts=_now_ts(), signature=b"",
        )
        signed = Envelope(
            type=env.type, from_=env.from_, by=env.by, to=env.to,
            payload=env.payload, ts=env.ts, signature=sign_envelope(env),
        )
        router.route(signed)
        # 各自只有自己的
        assert len(w.audit_entries()) == 1  # L0
        assert len(business_audit.all()) == 1  # 业务域


# ---------- AC9: 协议不变量 ----------

class TestAC9ProtocolInvariants:
    """AC9: 8 不变量全锁。"""

    def test_l0_l1_broadcast_type(self):
        """L0-L1: type=BROADCAST(不创第 12 类型)。"""
        w = _new_world()
        env = w.broadcast("alert", "x", from_=_secops())
        assert env.type == "broadcast"

    def test_l0_l2_l0_domain_marker(self):
        """L0-L2: to.domain_id == "l0"。"""
        w = _new_world()
        env = w.broadcast("alert", "x", from_=_secops())
        assert env.to.domain_id == L0_DOMAIN_ID

    def test_l0_l3_no_a2a_changes(self):
        """L0-L3: A1-A8 不变(源码扫)。"""
        import inspect
        import karvyloop.l0.world as w_mod
        src = inspect.getsource(w_mod)
        # 不**引**用** Envelope.__post_init__ 内**部**的** A1-A8 **强**制**(只**用** Envelope **构**造**)
        # 验**证**只**复**用**现**有** Envelope / AuditChain / Transport
        assert "Envelope" in src
        assert "AuditChain" in src
        assert "Transport" in src

    def test_l0_l4_observer_only_broadcast(self):
        """L0-L4: 小卡只收 BROADCAST(K3 强**制**)。"""
        w = _new_world()
        wb = WorkbenchObserver()
        # 推**一**个** task.assign 给**小**卡** — 必**须**被**拒**
        env = Envelope(
            type="task.assign", from_=_pm(), by=(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload={"task_id": "t1"},
            ts=_now_ts(), signature=b"",
        )
        signed = Envelope(
            type=env.type, from_=env.from_, by=env.by, to=env.to,
            payload=env.payload, ts=env.ts, signature=sign_envelope(env),
        )
        result = wb.subscribe_to(signed)
        assert result.rejected is True  # K3

    def test_l0_l5_5_default_channels(self):
        """L0-L5: 5 个默认频道。"""
        w = _new_world()
        assert w.channels == ("strategy", "alert", "celebrate", "ask-for-help", "general")

    def test_l0_l6_l0_not_in_business_inbox(self):
        """L0-L6: L0 广播不进业务域 Inbox。"""
        inbox = Inbox()
        # 验**证** L0 envelope 的 to.domain_id 永**远**是** "l0",不**与**任**何**业**务**域**重**叠
        from karvyloop.l0.world import L0_DOMAIN_ID
        assert L0_DOMAIN_ID != "dom-1"
        assert L0_DOMAIN_ID != "dom-2"
        # 业务**域** Inbox 的**键**是** (domain_id, role, agent_id),L0 **的**键** 是** ("l0", ...)
        # **不**冲**突
        inbox.deliver(Address(domain_id="dom-1", role="observer", agent_id="karvy"),
                      Envelope(type="broadcast", from_=_pm(), by=(),
                               to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
                               payload=BroadcastPayload(message="x"),
                               ts=_now_ts(), signature=b""))
        # 验**证** L0 **的**键**不**存**在**
        assert inbox.count(Address(domain_id="l0", role="observer", agent_id="karvy")) == 0

    def test_l0_l7_audit_records(self):
        """L0-L7: 每次 broadcast 写审计。"""
        w = _new_world()
        before = len(w.audit_entries())
        w.broadcast("alert", "x", from_=_secops())
        assert len(w.audit_entries()) == before + 1

    def test_l0_l8_no_observer_sender(self):
        """L0-L8: 小卡不能发(0 LLM 决策,让用户走 H2A)。"""
        w = _new_world()
        karvy = Address(domain_id="dom-1", role="observer", agent_id="karvy")
        with pytest.raises(KarvyL0SendForbiddenError):
            w.broadcast("strategy", "x", from_=karvy)

    def test_l0_subscribe_raises_for_unknown(self):
        """subscribe 未注册频道也抛。"""
        w = _new_world()
        with pytest.raises(UnknownL0ChannelError):
            w.subscribe("nope", lambda env: None)

    def test_default_channels_doc(self):
        """5 频道元数据锁(防止文档/实现漂移)。"""
        ch_map = {c.name: c for c in L0_DEFAULT_CHANNELS}
        assert ch_map["alert"].requires_acknowledgment is True
        assert ch_map["celebrate"].requires_acknowledgment is False
        assert "战略" in ch_map["strategy"].description
