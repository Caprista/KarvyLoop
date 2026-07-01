"""L0 karvy world 批 1.5 测试(M3.0 批 1.5:12 AC = F1-F6 + AC11 回归 + AC12 向后兼容)。

设计:docs/24 §3 + §4 + §5。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import AuditChain, BroadcastPayload, Envelope, sign_envelope  # noqa: E402
from karvyloop.a2a.transport import InProcessTransport, Transport  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.l0 import (  # noqa: E402
    DEFAULT_PERMISSIONS,
    L0World,
    ChannelAuthPolicy,
    ChannelPermission,
    ChannelAuthError,
    AckTracker,
    AckState,
    BroadcastFallback,
    FallbackLog,
)


@pytest.fixture(autouse=True)
def _reset_l0_singleton():
    L0World.reset_for_test()
    yield
    L0World.reset_for_test()


# ---------- helpers ----------

def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


def _pm() -> Address:
    return Address(domain_id="dom-1", role="pm", agent_id="pm-1")


def _secops() -> Address:
    return Address(domain_id="dom-1", role="secops", agent_id="secops-1")


def _engineer() -> Address:
    return Address(domain_id="dom-1", role="engineer", agent_id="eng-1")


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _new_world(transport=None, auth=None, ack=None, fb=None) -> L0World:
    return L0World(
        transport=transport or InProcessTransport(),
        audit_chain=AuditChain(),
        auth_policy=auth,
        ack_tracker=ack,
        broadcast_fallback=fb,
    )


def _sign(env: Envelope) -> Envelope:
    return Envelope(
        type=env.type, from_=env.from_, by=env.by, to=env.to,
        payload=env.payload, ts=env.ts, signature=sign_envelope(env),
    )


# ---------- AC1-F1: observer 不能发(任何频道) ----------

class TestF1ObserverCannotSend:
    """F1: from_.role == 'observer' 在**所**有**频**道**都**被**拒**(不**只** L0-L8,也**包**括** role-based)**。

    实**现**注**:L0World.broadcast 第 1 步**就**先**查** observer(K3/L0-L8),
    这**里**回**归**验**证**:**不**进**入** can_send **前**就**抛**。
    """

    def test_observer_sender_raises_on_strategy(self):
        w = _new_world()
        karvy = Address(domain_id="dom-1", role="observer", agent_id="karvy")
        with pytest.raises(Exception):  # KarvyL0SendForbiddenError
            w.broadcast("strategy", "x", from_=karvy)

    def test_observer_sender_raises_on_general(self):
        w = _new_world()
        karvy = Address(domain_id="dom-1", role="observer", agent_id="karvy")
        with pytest.raises(Exception):
            w.broadcast("general", "x", from_=karvy)


# ---------- AC2-F2: 频道未注册 -> can_send=False ----------

class TestF2ChannelNotRegistered:
    """F2: ChannelAuthPolicy 在**频**道**未**注**册**时**返** False(**不**抛**)。"""

    def test_unknown_channel_returns_false(self):
        policy = ChannelAuthPolicy()
        assert policy.can_send("nope", _pm()) is False

    def test_unknown_channel_in_broadcast_raises_unknown_error(self):
        w = _new_world()
        from karvyloop.l0.world import UnknownL0ChannelError
        with pytest.raises(UnknownL0ChannelError):
            w.broadcast("nope", "x", from_=_pm())


# ---------- AC3-F3: 角色白名单(5 个默认频道) ----------

class TestF3RoleWhitelist:
    """F3: 5 **频**道**有**各**自**的**白**名**单**(DEFAULT_PERMISSIONS **锁**住**)**。"""

    def test_strategy_only_pm(self):
        # strategy 只允**许** pm
        assert DEFAULT_PERMISSIONS["strategy"].allowed_sender_roles == ("pm",)

    def test_alert_only_secops(self):
        assert DEFAULT_PERMISSIONS["alert"].allowed_sender_roles == ("secops",)

    def test_celebrate_open_to_3_roles(self):
        assert set(DEFAULT_PERMISSIONS["celebrate"].allowed_sender_roles) == {"pm", "engineer", "user"}

    def test_ask_for_help_open_to_3_roles(self):
        assert set(DEFAULT_PERMISSIONS["ask-for-help"].allowed_sender_roles) == {"pm", "engineer", "user"}

    def test_general_open_to_3_roles(self):
        assert set(DEFAULT_PERMISSIONS["general"].allowed_sender_roles) == {"pm", "engineer", "user"}

    def test_user_blocked_on_strategy(self):
        """user 不**在** strategy **白**名**单**里** → 抛 ChannelAuthError。"""
        w = _new_world()
        with pytest.raises(ChannelAuthError):
            w.broadcast("strategy", "user 想发战略", from_=_user())

    def test_pm_blocked_on_alert(self):
        """pm 不**在** alert **白**名**单**里** → 抛 ChannelAuthError。"""
        w = _new_world()
        with pytest.raises(ChannelAuthError):
            w.broadcast("alert", "pm 想发告警", from_=_pm())


# ---------- AC4-F4: ack_required=True 进 pending() ----------

class TestF4AckTrackerPending:
    """F4: AckTracker.pending() 只**返** required=True **且**未**完**成**的**。"""

    def test_alert_broadcast_tracked(self):
        """alert 是 requires_acknowledgment=True → 进 pending。"""
        w = _new_world()
        w.broadcast("alert", "全平台告警", from_=_secops())
        pending = w.ack_tracker.pending()
        assert len(pending) == 1
        assert pending[0].channel == "alert"
        assert pending[0].required is True

    def test_celebrate_not_tracked(self):
        """celebrate 是 requires_acknowledgment=False → 不进 pending。"""
        w = _new_world()
        w.broadcast("celebrate", "发布成功", from_=_pm())
        # acknowledge_by 为空也**不**算 pending(因**为** required=False)
        pending = w.ack_tracker.pending()
        assert len(pending) == 0


# ---------- AC5-F5: ack 幂等 ----------

class TestF5AckIdempotent:
    """F5: 同**一** agent 重**复** ack 不**抛**(幂**等**)**。"""

    def test_same_agent_ack_idempotent(self):
        tracker = AckTracker()
        env = Envelope(
            type="broadcast", from_=_secops(), by=(),
            to=Address(domain_id="l0", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x", tag="alert"),
            ts=_now_ts(), signature=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        signed = _sign(env)
        tracker.track(signed, required=True)
        env_id = signed.signature[:8].hex()
        s1 = tracker.acknowledge(env_id, "karvy")
        s2 = tracker.acknowledge(env_id, "karvy")  # 第二次
        assert s1.acknowledged_by == ("karvy",)
        assert s2.acknowledged_by == ("karvy",)  # 不重复

    def test_different_agents_accumulate(self):
        tracker = AckTracker()
        env = Envelope(
            type="broadcast", from_=_secops(), by=(),
            to=Address(domain_id="l0", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x", tag="alert"),
            ts=_now_ts(), signature=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        signed = _sign(env)
        tracker.track(signed, required=True)
        env_id = signed.signature[:8].hex()
        tracker.acknowledge(env_id, "karvy")
        s = tracker.acknowledge(env_id, "user-1")
        assert s.acknowledged_by == ("karvy", "user-1")


# ---------- AC6-F6: 主 transport 失败 → 自动回退 ----------

class _FailingTransport(Transport):
    """测**试**用**:**主**动**抛**错**的** transport。"""

    name = "failing-test-transport"

    def __init__(self):
        self.published: list = []

    def publish(self, env: Envelope) -> None:
        self.published.append(env)
        raise RuntimeError("simulated transport failure")


class TestF6BroadcastFallback:
    """F6: 主**失**败**不**抛**给**调**用**方**,**自**动**回**退**到**次**级** transport。"""

    def test_primary_failure_falls_back_silently(self):
        primary = _FailingTransport()
        fb = BroadcastFallback(primary=primary)
        env = Envelope(
            type="broadcast", from_=_pm(), by=(),
            to=Address(domain_id="l0", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x"),
            ts=_now_ts(), signature=b"\x01",
        )
        signed = _sign(env)
        # 不**抛**错**
        used, err = fb.publish(signed)
        assert used.name == "in-process"  # 默认次级 = InProcessTransport
        assert err is not None
        assert "simulated transport failure" in str(err)

    def test_fallback_log_records_failure(self):
        primary = _FailingTransport()
        fb = BroadcastFallback(primary=primary)
        env = Envelope(
            type="broadcast", from_=_pm(), by=(),
            to=Address(domain_id="l0", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x", tag="alert"),
            ts=_now_ts(), signature=b"\x01",
        )
        signed = _sign(env)
        fb.publish(signed)
        logs = fb.logs()
        assert len(logs) == 1
        assert logs[0].primary_transport == "failing-test-transport"
        assert logs[0].channel == "alert"

    def test_primary_success_no_log(self):
        primary = InProcessTransport()
        fb = BroadcastFallback(primary=primary)
        env = Envelope(
            type="broadcast", from_=_pm(), by=(),
            to=Address(domain_id="l0", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x"),
            ts=_now_ts(), signature=b"\x01",
        )
        signed = _sign(env)
        used, err = fb.publish(signed)
        assert used.name == "in-process"
        assert err is None
        assert fb.log_count() == 0


# ---------- AC11: 回归(批 1.5 不破拍 3 行为) ----------

class TestAC11RegressionP3NotBroken:
    """AC11: 批 1.5 集成**后**,**拍** 3 **的**核心**行为**依**然** OK。"""

    def test_observer_still_forbidden_to_send(self):
        """L0-L8 不**变**:小**卡**不**能**发**。"""
        w = _new_world()
        karvy = Address(domain_id="dom-1", role="observer", agent_id="karvy")
        from karvyloop.l0 import KarvyL0SendForbiddenError
        with pytest.raises(KarvyL0SendForbiddenError):
            w.broadcast("strategy", "x", from_=karvy)

    def test_5_default_channels_present(self):
        """L0-L5: 5 **频**道**元**数**据**不**变**。"""
        w = _new_world()
        assert w.channels == ("strategy", "alert", "celebrate", "ask-for-help", "general")

    def test_audit_records_still_increment(self):
        """L0-L7: 审**计**仍**然**每**次** +1。"""
        w = _new_world()
        w.broadcast("alert", "x", from_=_secops())
        w.broadcast("strategy", "y", from_=_pm())
        assert len(w.audit_entries()) == 2

    def test_broadcast_returns_signed_envelope(self):
        """L0-L1: broadcast 仍**然**返**回**签**名** envelope。"""
        w = _new_world()
        env = w.broadcast("alert", "x", from_=_secops())
        assert env.signature  # 非空


# ---------- AC12: 向后兼容(不传 auth/ack/fallback 也能用) ----------

class TestAC12BackwardsCompat:
    """AC12: **不**传** auth_policy/ack_tracker/broadcast_fallback,**也**不**破**。"""

    def test_default_construction_no_args(self):
        """只**传** transport,**不**传** 3 件**套**,**可**用**。"""
        w = L0World(transport=InProcessTransport(), audit_chain=AuditChain())
        env = w.broadcast("general", "x", from_=_pm())
        assert env.signature

    def test_default_auth_policy_uses_default_permissions(self):
        w = _new_world()
        # 默认**走** DEFAULT_PERMISSIONS → user **在** general **白**名**单**里**
        env = w.broadcast("general", "x", from_=_user())
        assert env.from_.role == "user"

    def test_default_ack_tracker_is_empty_until_required(self):
        w = _new_world()
        # celebrate 不**要** ack → 默认 tracker **不**记**它**
        w.broadcast("celebrate", "x", from_=_pm())
        assert w.ack_tracker.count() == 0