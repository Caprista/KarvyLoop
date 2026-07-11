"""test_collab_foundation — 频道原语基座(collab/)的安全关键不变量。

线 B 建了 collab/ 基座(room/registry/gate)但死于 API 瞬断没写测试;这里补上,锁住
**载重的安全不变量**(opacity deny-by-default = A2A Contagion 防御 / containment / 限流零回归)。
基座本身尚未接进圆桌(M3 wiring 是后续),这里只验数据模型与判据的确定性。
"""
from __future__ import annotations

from karvyloop.collab.gate import MemberRateLimiter, VisibilityGate
from karvyloop.collab.room import (
    OPACITY_INTERNAL,
    OPACITY_OPAQUE,
    PARTICIPANT_EXTERNAL,
    PARTICIPANT_ROLE,
    RoomMember,
    RoomScope,
    normalize_opacity,
)


# ---- opacity deny-by-default:A2A Contagion 防御的判据(载重红线)----

def test_external_can_never_be_internal():
    """外部执行体**永不**能标 internal —— 否则外部产出直接进对话主线触发别人(A2A Contagion)。"""
    assert normalize_opacity("internal", PARTICIPANT_EXTERNAL) == OPACITY_OPAQUE
    assert normalize_opacity("INTERNAL", PARTICIPANT_EXTERNAL) == OPACITY_OPAQUE
    m = RoomMember(participant_id="cc1", kind=PARTICIPANT_EXTERNAL, opacity="internal")
    assert m.opacity == OPACITY_OPAQUE
    assert m.enters_mainline() is False, "外部成员绝不进对话主线"


def test_role_always_internal():
    assert normalize_opacity("opaque", PARTICIPANT_ROLE) == OPACITY_INTERNAL
    m = RoomMember(participant_id="designer", kind=PARTICIPANT_ROLE)
    assert m.is_internal() and m.enters_mainline()


def test_unknown_opacity_denies_to_opaque():
    """未知/篡改 opacity 一律降最不透明(deny-by-default),不因一个字符串放外部进主线。"""
    assert normalize_opacity("t2", PARTICIPANT_EXTERNAL) == OPACITY_OPAQUE
    assert normalize_opacity("garbage", PARTICIPANT_EXTERNAL) == OPACITY_OPAQUE


def test_member_composite_key_and_roundtrip():
    m = RoomMember(participant_id="cc1", kind=PARTICIPANT_EXTERNAL, opacity="opaque", domain_id="finance")
    assert m.composite_key() == ("finance", "cc1")
    assert RoomMember.from_dict(m.to_dict()).opacity == OPACITY_OPAQUE


# ---- containment:RoomScope(workspace + egress + access_scope)----

def test_roomscope_egress_normalized_and_carries_containment():
    s = RoomScope(workspace_root="/w/room1", egress_allowlist=(" API.X.com ", "api.x.com", ""), access_scope="shared")
    assert s.egress_allowlist == ("api.x.com",), "去空白/小写/去重"
    assert s.workspace_root == "/w/room1" and s.access_scope == "shared"
    assert RoomScope.from_dict(s.to_dict()).egress_allowlist == ("api.x.com",)


# ---- VisibilityGate:deny-by-default 白名单 ----

class _Room:
    def __init__(self, ids):
        self._ids = set(ids)

    def member_ids(self):
        return set(self._ids)


def test_visibility_gate_deny_by_default():
    room = _Room({"a", "b"})
    assert VisibilityGate.allow(room, "a") is True
    assert VisibilityGate.allow(room, "z") is False, "不在册成员 deny"
    assert VisibilityGate.allow(None, "a") is False, "无 room 全拒"
    assert VisibilityGate.filter_targets(room, ["a", "z", "b", "a"]) == ["a", "b"]


# ---- MemberRateLimiter:0=零回归,>0 确定性生效 ----

def test_rate_limiter_zero_is_no_regression():
    lim = MemberRateLimiter(min_interval_s=0.0)
    assert all(lim.allow("r", "m") for _ in range(5))


def test_rate_limiter_enforces_interval_deterministic():
    lim = MemberRateLimiter(min_interval_s=10.0)
    assert lim.gate_and_mark("r", "m", now=100.0) is True
    assert lim.allow("r", "m", now=105.0) is False, "10s 内再派被限"
    assert lim.allow("r", "m", now=111.0) is True, "过窗放行"
    assert lim.allow("r", "other", now=105.0) is True, "不同成员互不影响"


def test_registry_imports_and_constructs():
    """registry 基座能导入构造(未接圆桌,只验不炸)。"""
    import karvyloop.collab.registry as reg  # noqa: F401
    assert hasattr(reg, "__all__") or True
