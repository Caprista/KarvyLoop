"""collab/room — Room 一等对象 + 成员 opacity 档 + 每 channel containment(docs/73 §4)。

一个 Room = 一个协作场,承接异构参与者(自家 role + 外部 opaque 执行体),带三样硬件:
1. **成员表**,每成员一档 **opacity**(一等读写属性,非消息级过滤):
   - `internal`(自家 role):对内可见,产出进 record_turn 对话主线,占决策席、进问责链。
   - `opaque`(外部单 agent):只报阶段不报思维链;产出走数据通道 provenance=untrusted、
     **不进主线**;不占决策席、不进问责链。
   - `opaque_team`(外部团队门面):更粗,只暴露"任务→产出"。
2. **每 Room 一个隔离 workspace_root**(containment):对方在这个 Room 的沙箱根里协作,
   够不到你更广的环境(其它域 / 其它 Room / 你的文件)。
3. **每 Room 一个 egress 作用域 + 访问 scope**(containment):复用 #71 net_allowlist 做域名级
   egress;access_scope 声明只暴露共享层切片(域私有绝不给)。

**钉死**:opacity 决定"这个成员的产出能不能进对话主线"——`internal` 才进;外部一律不进,
只能当 untrusted 供稿走采纳门(A2A Contagion 防御在编排层,这里只给判据)。

**方向性(participant kind + direction)**:留位给 M3(活 peer 入站 vs 托管访问出站),
本基座只用到 role/external 两类,direction 字段记录但不驱动跨设备传输。
"""
from __future__ import annotations

import dataclasses
from typing import Optional

# ---- participant kind:成员是哪一类实体(本体论对齐,不新增实体) ----
PARTICIPANT_ROLE = "role"          # 自家 role(L2 实体)
PARTICIPANT_EXTERNAL = "external"  # 外部第四类 opaque 执行体(citizen)

# ---- opacity 档:成员一等读写属性(决定产出能否进对话主线) ----
OPACITY_INTERNAL = "internal"        # 自家 role:进 record_turn,占决策席、进问责链
OPACITY_OPAQUE = "opaque"            # 外部单 agent:只报阶段,产出 untrusted 不进主线
OPACITY_OPAQUE_TEAM = "opaque_team"  # 外部团队门面:更粗,只"任务→产出"

_KNOWN_OPACITY = frozenset({OPACITY_INTERNAL, OPACITY_OPAQUE, OPACITY_OPAQUE_TEAM})
# opacity 里"能进对话主线/占决策席"的档 —— 只有 internal(自家 role)。
_MAINLINE_OPACITY = frozenset({OPACITY_INTERNAL})


def normalize_opacity(opacity: str, kind: str) -> str:
    """把 opacity 归一到已知档(deny-by-default 语义):

    - 自家 role → 只能 `internal`(role 就是对内可见实体;别人塞 opaque 也无意义,强制 internal)。
    - 外部执行体 → 未知/篡改的一律降到最不透明可见的档 `opaque`(deny-by-default:
      绝不因一个字符串就把外部产出放进对话主线)。**尤其不认把外部标成 `internal`** —— 那等于
      给 A2A Contagion 开口子(外部产出直接进主线触发别人)。
    """
    o = (opacity or "").strip().lower()
    if kind == PARTICIPANT_ROLE:
        return OPACITY_INTERNAL  # role 恒 internal
    # 外部:只认 opaque / opaque_team;其余(含误标 internal)一律降 opaque。
    if o in (OPACITY_OPAQUE, OPACITY_OPAQUE_TEAM):
        return o
    return OPACITY_OPAQUE


@dataclasses.dataclass(frozen=True)
class RoomMember:
    """Room 的一个成员(自家 role 或外部 citizen 的引用 + 该成员的 opacity 档)。

    - `participant_id`:复合键里的成员段(role 的 agent_id / citizen 的 citizen_id)。
    - `kind`:role / external(不新增实体,只标它引用哪类)。
    - `opacity`:该成员的一等读写档(见上;__post_init__ 按 kind 归一 deny-by-default)。
    - `domain_id`:成员在哪个域被解析(复合键 (域, participant_id));空=无域/私聊。
    - `direction`:方向性留位(peer=入站活 peer / hosted=出站托管访问)。**本基座不驱动**
      跨设备传输,只记录,供 M3 承接。
    """
    participant_id: str
    kind: str = PARTICIPANT_ROLE
    opacity: str = OPACITY_INTERNAL
    domain_id: str = ""
    direction: str = ""   # "" | "peer"(入站) | "hosted"(出站,M3)
    display_name: str = ""

    def __post_init__(self) -> None:
        k = (self.kind or "").strip().lower()
        if k not in (PARTICIPANT_ROLE, PARTICIPANT_EXTERNAL):
            k = PARTICIPANT_ROLE
        if k != self.kind:
            object.__setattr__(self, "kind", k)
        norm = normalize_opacity(self.opacity, k)
        if norm != self.opacity:
            object.__setattr__(self, "opacity", norm)

    def is_internal(self) -> bool:
        """自家 role:产出进对话主线、占决策席、进问责链。"""
        return self.kind == PARTICIPANT_ROLE and self.opacity in _MAINLINE_OPACITY

    def enters_mainline(self) -> bool:
        """这个成员的产出能不能进 record_turn 对话主线?只有 internal(自家 role)能。

        外部(opaque/opaque_team)一律 False —— 产出只能当 untrusted 供稿走采纳门,
        绝不直接进主线(A2A Contagion 防御的判据来源)。
        """
        return self.opacity in _MAINLINE_OPACITY

    def composite_key(self) -> tuple[str, str]:
        """复合键 (域, participant_id) —— 与 domain/citizen 同一寻址空间。"""
        return (self.domain_id or "", self.participant_id or "")

    def to_dict(self) -> dict:
        return {
            "participant_id": self.participant_id, "kind": self.kind,
            "opacity": self.opacity, "domain_id": self.domain_id,
            "direction": self.direction, "display_name": self.display_name,
        }

    @staticmethod
    def from_dict(d: dict) -> "RoomMember":
        d = d or {}
        return RoomMember(
            participant_id=str(d.get("participant_id") or ""),
            kind=str(d.get("kind") or PARTICIPANT_ROLE),
            opacity=str(d.get("opacity") or OPACITY_INTERNAL),
            domain_id=str(d.get("domain_id") or ""),
            direction=str(d.get("direction") or ""),
            display_name=str(d.get("display_name") or ""),
        )


@dataclasses.dataclass(frozen=True)
class RoomScope:
    """一个 Room 的 containment 三件(docs/73 §0.5):隔离 workspace + egress 作用域 + 访问 scope。

    - `workspace_root`:该 Room 的隔离沙箱根(空=未隔离,退回调用方兜底根 —— 但 share 出去的
      Room **必须**设,否则 containment 破)。外部成员在此根下协作,够不到更广环境。
    - `egress_allowlist`:该 Room 的按域名 egress 白名单(复用 #71 net_allowlist)。空=二元网络
      语义(零回归);非空=只放行这些 host,其余沙箱层 fail-closed。
    - `access_scope`:声明这个 Room 只暴露哪个共享层切片(如 "shared" / "domain:<id>")。
      **域私有认知绝不进 Room scope**——这是 scope 门控的声明面,消费侧据此只喂共享切片。
    """
    workspace_root: str = ""
    egress_allowlist: tuple[str, ...] = ()
    access_scope: str = ""

    def __post_init__(self) -> None:
        raw = self.egress_allowlist
        # 归一:去空白/空串/小写(域名大小写无关)/去重保序 —— 与 net_allowlist_of 同规范。
        norm: list[str] = []
        seen: set[str] = set()
        try:
            for h in (raw or ()):
                s = str(h).strip().lower()
                if s and s not in seen:
                    seen.add(s)
                    norm.append(s)
        except TypeError:
            norm = []
        nt = tuple(norm)
        if nt != self.egress_allowlist:
            object.__setattr__(self, "egress_allowlist", nt)

    def to_dict(self) -> dict:
        return {
            "workspace_root": self.workspace_root,
            "egress_allowlist": list(self.egress_allowlist),
            "access_scope": self.access_scope,
        }

    @staticmethod
    def from_dict(d: dict) -> "RoomScope":
        d = d or {}
        return RoomScope(
            workspace_root=str(d.get("workspace_root") or ""),
            egress_allowlist=tuple(d.get("egress_allowlist") or ()),
            access_scope=str(d.get("access_scope") or ""),
        )


__all__ = [
    "RoomMember", "RoomScope", "normalize_opacity",
    "PARTICIPANT_ROLE", "PARTICIPANT_EXTERNAL",
    "OPACITY_INTERNAL", "OPACITY_OPAQUE", "OPACITY_OPAQUE_TEAM",
]
