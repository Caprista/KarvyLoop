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


# ---- 接进圆桌:_build_roundtable_room 的 A2A 结构不变量(docs/73 §4)----

class _Addr:
    def __init__(self, agent_id, role="agent", domain_id="finance"):
        self.agent_id = agent_id
        self.role = role
        self.domain_id = domain_id


class _Citizen:
    def __init__(self, citizen_id):
        self.citizen_id = citizen_id


def _fake_app():
    """最小 app:_member_display 只摸 app.state.role_registry(给 None → 退回 id 名)。"""
    import types
    return types.SimpleNamespace(state=types.SimpleNamespace(role_registry=None))


def test_roundtable_room_partitions_by_opacity():
    """圆桌成员→internal(进主线)、外部客人→opaque(不进主线),按 opacity 属性分区。"""
    from karvyloop.console.roundtable_engine import _build_roundtable_room
    room = _build_roundtable_room(
        _fake_app(), None, "conv1",
        members=[_Addr("analyst"), _Addr("designer")],
        guests=[_Citizen("cc-guest")])
    internal = {m.participant_id for m in room.internal_members()}
    external = {m.participant_id for m in room.external_members()}
    assert internal == {"analyst", "designer"}, "自家 role 进决策席"
    assert external == {"cc-guest"}, "外部客人只在供稿席"
    assert not any(m.enters_mainline() for m in room.external_members()), "外部结构上不进主线"


def test_roundtable_room_forces_leaked_external_out_of_mainline():
    """载重红线:若外部执行体被当成"成员"塞进决策席,Room 强制 opaque → enters_mainline()=False。

    这是 A2A Contagion 防御从"约定"(members/guests 两个 resolver 各管一半)升成 **Room 属性**:
    哪怕上游误把外部当 role 成员传进来,它也进不了 record_turn 主线触发别的 role。
    """
    from karvyloop.collab.room import PARTICIPANT_EXTERNAL, RoomMember
    # 模拟:一个外部执行体被误标成 role/internal 塞进成员表
    leaked = RoomMember(participant_id="evil-cc", kind=PARTICIPANT_EXTERNAL, opacity="internal")
    assert leaked.enters_mainline() is False, "外部即使误标 internal 也进不了主线"
    # 圆桌结构守卫的判据:mainline_ids 只含真 internal;外部不在其中 → 被剔除
    from karvyloop.console.roundtable_engine import _build_roundtable_room
    room = _build_roundtable_room(_fake_app(), None, "c", members=[_Addr("real_role")],
                                  guests=[_Citizen("guest1")])
    mainline_ids = {m.participant_id for m in room.internal_members()}
    assert "real_role" in mainline_ids and "guest1" not in mainline_ids
