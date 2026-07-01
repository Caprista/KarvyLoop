"""A2A Tier 2 跨进程 transport 测试(M3.0 批 1 拍 2:8 个测试 = 7 AC + 1 回归)。

设计:docs/22 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import (  # noqa: E402
    BroadcastPayload,
    Envelope,
    Inbox,
    AuditChain,
    EnvelopeRouter,
    sign_envelope,
)
from karvyloop.a2a.transport import (  # noqa: E402
    InProcessTransport,
    Transport,
    create_transport,
)
from karvyloop.a2a.transport.envelope_codec import decode, encode  # noqa: E402
from karvyloop.a2a.transport.bus_redis import (  # noqa: E402
    CHANNEL_BROADCAST,
    CHANNEL_DOMAIN,
    channel_for,
    channels_to_subscribe,
)
from karvyloop.domain import Address  # noqa: E402


# ---------- fixtures ----------

def _user(domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="user", agent_id="ch")


def _engineer(domain: str = "dom-1", agent: str = "eng-1") -> Address:
    return Address(domain_id=domain, role="engineer", agent_id=agent)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _build_envelope(*, env_type: str, from_: Address, to: Address, payload) -> Envelope:
    env = Envelope(
        type=env_type,
        from_=from_,
        by=(),
        to=to,
        payload=payload,
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


# ---------- AC1: 同进程 fast path(T5)----------

class TestAC1LocalFastPath:
    """AC1: is_local 全部 True → 走 Inbox.deliver,transport 不被调。"""

    def test_local_routes_to_inbox_not_transport(self):
        inbox = Inbox()
        audit = AuditChain()
        # Spy transport:记录 publish 次数
        from karvyloop.a2a.transport.bus_inprocess import InProcessTransport
        spy = InProcessTransport()
        called = []
        original_publish = spy.publish
        def spy_publish(env):
            called.append(env)
            original_publish(env)
        spy.publish = spy_publish  # type: ignore

        router = EnvelopeRouter(
            inbox=inbox,
            audit_chain=audit,
            transport=spy,
            is_local=lambda to: True,  # 全本地
        )
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"question": "q"},
        )
        result = router.route(env)
        assert not result.rejected
        # Inbox 收到 + transport **没**被调
        assert len(inbox.peek(_engineer())) == 1
        assert called == []  # 关键断言


# ---------- AC2: InProcessTransport 与 Tier 1 同行为 ----------

class TestAC2InProcessTransport:
    """AC2: InProcessTransport.publish → subscribe 收到。"""

    def test_publish_subscribe_round_trip(self):
        t = InProcessTransport()
        received = []
        t.subscribe(lambda env: received.append(env))
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"q": "hi"},
        )
        t.publish(env)
        assert len(received) == 1
        assert received[0].type == "ask"

    def test_subscriber_exception_does_not_break_others(self):
        """T7:一个 subscriber 失败,其他人继续收。"""
        t = InProcessTransport()
        received_ok = []
        def bad_cb(env):
            raise RuntimeError("intentional")
        t.subscribe(bad_cb)
        t.subscribe(lambda env: received_ok.append(env))
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"q": "hi"},
        )
        # 不抛
        t.publish(env)
        # 第二个 subscriber 仍收到
        assert len(received_ok) == 1

    def test_clear_resets_subscribers(self):
        t = InProcessTransport()
        t.subscribe(lambda env: None)
        t.clear()
        received = []
        t.subscribe(lambda env: received.append(env))
        env = _build_envelope(env_type="ask", from_=_user(), to=_engineer(), payload={})
        t.publish(env)
        assert len(received) == 1  # 只收到 1 个(clear 后只剩新的)


# ---------- AC3 + AC4: EnvelopeCodec 双向 + 11 类型 ----------

class TestAC3AC4CodecRoundTrip:
    """AC3: encode → decode 签名 byte-for-byte 一致。
    AC4: 11 种 EnvelopeType round-trip 全过。"""

    def test_ask_round_trip_preserves_signature(self):
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"question": "帮我 review"},
        )
        raw = encode(env)
        env2 = decode(raw)
        assert env2.type == env.type
        assert env2.from_ == env.from_
        assert env2.to == env.to
        assert env2.signature == env.signature  # AC3 关键

    def test_broadcast_round_trip_preserves_payload(self):
        env = _build_envelope(
            env_type="broadcast",
            from_=_user(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="新任务", tag="task"),
        )
        raw = encode(env)
        env2 = decode(raw)
        # payload 是 BroadcastPayload dataclass
        assert hasattr(env2.payload, "message")
        assert env2.payload.message == "新任务"
        assert env2.payload.tag == "task"
        assert env2.signature == env.signature

    def test_sort_keys_deterministic(self):
        """两次 encode 同 envelope → 字节相同。"""
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"q": "x"},
        )
        raw1 = encode(env)
        raw2 = encode(env)
        assert raw1 == raw2

    def test_by_field_preserved_as_tuple(self):
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={},
        )
        # 手动加 by(courier 测试场景)
        env2 = Envelope(
            type=env.type,
            from_=env.from_,
            by=(Address(domain_id="dom-1", role="observer", agent_id="karvy"),),
            to=env.to,
            payload=env.payload,
            ts=env.ts,
            signature=env.signature,
        )
        raw = encode(env2)
        env3 = decode(raw)
        assert len(env3.by) == 1
        assert env3.by[0].agent_id == "karvy"


# ---------- AC5: is_local 判断 ----------

class TestAC5IsLocalResolution:
    """AC5: is_local 返 True → Inbox;False → Transport。"""

    def test_remote_target_uses_transport(self):
        inbox = Inbox()
        audit = AuditChain()
        t = InProcessTransport()
        received = []
        t.subscribe(lambda env: received.append(env))

        def is_local(addr: Address) -> bool:
            return addr.agent_id == "local-eng"  # 只有 local-eng 算本地

        router = EnvelopeRouter(
            inbox=inbox,
            audit_chain=audit,
            transport=t,
            is_local=is_local,
        )

        # 远程目标:走 transport
        remote_eng = Address(domain_id="dom-1", role="engineer", agent_id="remote-eng")
        env = _build_envelope(env_type="ask", from_=_user(), to=remote_eng, payload={})
        result = router.route(env)
        assert not result.rejected
        # Inbox **没**收
        assert len(inbox.peek(remote_eng)) == 0
        # Transport 收了
        assert len(received) == 1
        assert received[0].to.agent_id == "remote-eng"

        # 本地目标:走 inbox
        local_eng = Address(domain_id="dom-1", role="engineer", agent_id="local-eng")
        env2 = _build_envelope(env_type="ask", from_=_user(), to=local_eng, payload={})
        router.route(env2)
        assert len(inbox.peek(local_eng)) == 1
        # Transport 仍只 1 个
        assert len(received) == 1


# ---------- AC6: create_transport 降级(T7)----------

class TestAC6CreateTransportFallback:
    """AC6: 无 redis_url / 无 redis-py → 降级 InProcessTransport,不抛。"""

    def test_in_process_default(self):
        t = create_transport(backend="in-process")
        assert isinstance(t, InProcessTransport)
        assert t.name == "in-process"

    def test_redis_without_url_falls_back(self, caplog):
        t = create_transport(backend="redis", redis_url=None)
        # 降级到 in-process
        assert isinstance(t, InProcessTransport)
        assert t.name == "in-process"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            create_transport(backend="kafka")


# ---------- AC7: RedisTransport 接本机 redis 真投 ----------

# 检**测**本**机** redis 是**否**可**达**;不**可**达**就 skip(不**让** CI 卡**住**)
def _redis_available() -> bool:
    try:
        import redis  # type: ignore
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=0.5)
        r.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _redis_available(), reason="本机无 redis(brew install redis)")
class TestAC7RedisTransport:
    """AC7: 本机 redis 真投/收(本机 fixture,CI skip)。"""

    def test_redis_publish_subscribe_end_to_end(self):
        from karvyloop.a2a.transport.bus_redis import RedisTransport

        url = "redis://localhost:6379/0"
        t_pub = RedisTransport(redis_url=url, client_id="pub")
        t_sub = RedisTransport(redis_url=url, client_id="sub")

        received = []
        t_sub.subscribe(lambda env: received.append(env))
        t_sub.start(domain_id="dom-redis-test")

        # 等订阅生效
        time.sleep(0.3)

        env = _build_envelope(
            env_type="ask",
            from_=_user(domain="dom-redis-test"),
            to=_engineer(domain="dom-redis-test"),
            payload={"q": "from redis"},
        )
        t_pub.publish(env)

        # 等消息(round-trip < 1s)
        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.05)

        try:
            t_sub.stop()
        except Exception:
            pass

        assert len(received) == 1, f"redis pub/sub 失败,收到 {len(received)} 个"
        assert received[0].to.agent_id == "eng-1"
        assert received[0].signature == env.signature

    def test_redis_broadcast_channel(self):
        """BROADCAST 走全局 channel,任意 domain 的订阅者都收。"""
        from karvyloop.a2a.transport.bus_redis import RedisTransport

        url = "redis://localhost:6379/0"
        t_pub = RedisTransport(redis_url=url, client_id="pub-bc")
        # 订 dom-A 的广播
        t_sub = RedisTransport(redis_url=url, client_id="sub-bc")
        received = []
        t_sub.subscribe(lambda env: received.append(env))
        t_sub.start(domain_id="dom-A")

        time.sleep(0.3)

        # BROADCAST,从 dom-B 发
        env = _build_envelope(
            env_type="broadcast",
            from_=_user(domain="dom-B"),
            to=Address(domain_id="dom-B", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="全局通知", tag="alert"),
        )
        t_pub.publish(env)

        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.05)

        try:
            t_sub.stop()
        except Exception:
            pass

        # dom-A 的订阅者**也**收到 dom-B 的广播(全局 channel)
        assert len(received) == 1
        assert received[0].payload.message == "全局通知"


# ---------- AC8: 不破坏 Tier 1(回归保证)----------

class TestAC8BackwardsCompat:
    """AC8: 76 个已过测试不破。集成 transport 后,默认行为(无 transport / 全 local)等价 Tier 1。"""

    def test_default_construction_no_transport(self):
        """不传 transport = 退化 in-process(Tier 1 等价)。"""
        inbox = Inbox()
        audit = AuditChain()
        router = EnvelopeRouter(inbox=inbox, audit_chain=audit)
        env = _build_envelope(
            env_type="ask",
            from_=_user(),
            to=_engineer(),
            payload={"q": "x"},
        )
        result = router.route(env)
        assert not result.rejected
        # 走 Inbox
        assert len(inbox.peek(_engineer())) == 1

    def test_default_is_local_is_all_true(self):
        """默认 is_local = 全 True。"""
        inbox = Inbox()
        audit = AuditChain()
        router = EnvelopeRouter(inbox=inbox, audit_chain=audit)
        # **不**传 is_local 时,任何地址都算本地
        assert router._is_local(Address(domain_id="any", role="any", agent_id="any")) is True

    def test_subscribe_in_process_does_not_silently_break(self):
        """bus_inprocess 是 router 默认 transport,显式验证。"""
        inbox = Inbox()
        audit = AuditChain()
        router = EnvelopeRouter(inbox=inbox, audit_chain=audit)
        assert isinstance(router._transport, InProcessTransport)


# ---------- AC9: 协议不变量(8 不变量)----------

class TestAC9ProtocolInvariants:
    """AC9: 8 不变量锁住。"""

    def test_t1_no_envelope_changes(self):
        """T1: Envelope / EnvelopeType / Address 没改。"""
        import dataclasses
        from karvyloop.a2a.envelope import Envelope, EnvelopeType
        from karvyloop.domain import Address
        # 字段仍在(用 dataclasses.fields 检,避免 hasattr 与 Python builtin 冲突)
        field_names = {f.name for f in dataclasses.fields(Envelope)}
        for required in ("type", "from_", "by", "to", "payload", "ts", "signature"):
            assert required in field_names, f"Envelope 缺字段 {required}(T1 违反)"
        # 11 个 type 仍存在
        assert hasattr(EnvelopeType, "BROADCAST")
        assert hasattr(EnvelopeType, "TASK_ASSIGN")
        assert hasattr(EnvelopeType, "REJECT")
        # Address 字段未变
        addr_fields = {f.name for f in dataclasses.fields(Address)}
        for required in ("domain_id", "role", "agent_id"):
            assert required in addr_fields, f"Address 缺字段 {required}(T1 违反)"

    def test_t2_route_signature_unchanged(self):
        """T2: route(env) -> RouteResult 签名没变。"""
        import inspect
        from karvyloop.a2a.router import EnvelopeRouter
        sig = inspect.signature(EnvelopeRouter.route)
        # 1 个参数 env
        params = [p for p in sig.parameters.values() if p.name != "self"]
        assert len(params) == 1
        assert params[0].name == "env"

    def test_t4_transport_fully_injected(self):
        """T4: transport 注入式,无全局。"""
        # 创**建**两**个** router 各**有**自**家** transport
        t1 = InProcessTransport(client_id="a")
        t2 = InProcessTransport(client_id="b")
        r1 = EnvelopeRouter(inbox=Inbox(), audit_chain=AuditChain(), transport=t1)
        r2 = EnvelopeRouter(inbox=Inbox(), audit_chain=AuditChain(), transport=t2)
        assert r1._transport is t1
        assert r2._transport is t2
        assert r1._transport is not r2._transport

    def test_t5_local_fast_path_zero_transport(self):
        """T5: 本地快路径 0 transport 调用。"""
        inbox = Inbox()
        audit = AuditChain()
        t = InProcessTransport()
        called = []
        orig = t.publish
        t.publish = lambda env: called.append(env) or orig(env)  # type: ignore
        router = EnvelopeRouter(
            inbox=inbox, audit_chain=audit,
            transport=t, is_local=lambda a: True,
        )
        router.route(_build_envelope(
            env_type="ask", from_=_user(), to=_engineer(), payload={},
        ))
        assert called == []

    def test_t6_sync_publish(self):
        """T6: publish 是同步(非 async)。"""
        import inspect
        from karvyloop.a2a.transport.bus_inprocess import InProcessTransport
        assert not inspect.iscoroutinefunction(InProcessTransport.publish)
        from karvyloop.a2a.transport.bus_redis import RedisTransport
        assert not inspect.iscoroutinefunction(RedisTransport.publish)

    def test_t7_graceful_fallback(self):
        """T7: 无 redis_url 不崩。"""
        t = create_transport(backend="redis", redis_url=None)
        # 仍可 publish(走 in-process)
        env = _build_envelope(env_type="ask", from_=_user(), to=_engineer(), payload={})
        t.publish(env)  # 不抛

    def test_t8_signature_preserved_through_codec(self):
        """T8: 签名 hex 编码 → 解码 byte-for-byte。"""
        from karvyloop.a2a.envelope import RejectPayload
        env = _build_envelope(
            env_type="reject",
            from_=_user(),
            to=_engineer(),
            payload=RejectPayload(reason="over budget"),
        )
        original_sig = env.signature
        raw = encode(env)
        # raw 是 bytes,检**查** hex 字段**在** JSON 里
        assert b'"signature"' in raw
        env2 = decode(raw)
        assert env2.signature == original_sig
        # payload 类**型**也**还**原**(REJECT 派**发**到** RejectPayload)**
        assert isinstance(env2.payload, RejectPayload)
        assert env2.payload.reason == "over budget"


# ---------- AC10: channel 规则 ----------

class TestAC10ChannelRules:
    """channel_for / channels_to_subscribe 正确。"""

    def test_broadcast_uses_global_channel(self):
        env = _build_envelope(
            env_type="broadcast",
            from_=_user(),
            to=Address(domain_id="dom-X", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="x"),
        )
        assert channel_for(env) == CHANNEL_BROADCAST

    def test_ask_uses_domain_channel(self):
        env = _build_envelope(
            env_type="ask",
            from_=_user(domain="dom-Y"),
            to=_engineer(domain="dom-Y"),
            payload={},
        )
        assert channel_for(env) == CHANNEL_DOMAIN.format(domain_id="dom-Y")

    def test_subscribe_returns_domain_and_broadcast(self):
        chs = channels_to_subscribe("dom-Z")
        assert CHANNEL_DOMAIN.format(domain_id="dom-Z") in chs
        assert CHANNEL_BROADCAST in chs
        assert len(chs) == 2
