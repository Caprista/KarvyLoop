"""conversation — 对话(Conversation)基础概念(M3+ 拍 9.1 + 9.2a 归属重做)。

设计:docs/26-conversation-session.md(§C 对话归属 grounded 在 docs/00 宪法)。

**是什么**(docs/26 §0):
- Conversation = 用户↔**某角色**、在**某场**里的一段连续交互
- 持久存盘、不超时、边界由用户管;关机重启默认续上(CV-2/CV-6)
- **双身份**:① 会话内记忆层;② 快慢脑共享上下文总线(§B)

**归属 = 「场 + 角色」= peer `Address`(CV-12,9.2a 加)**:
- 场 = karvy world 私聊(`domain_id="l0"`)或 业务域节点(`domain_id=<biz>`)
- 角色 = `role + agent_id`
- 同一角色多条线(私聊 + 每个入职业务域各一条),各自独立、互不串味(CV-13)

**落盘**:每段对话一个 `<id>.jsonl`,**按场分区**(对齐 docs/04 私人/域记忆分路径):
- 私聊:`<root>/l0/<peer_key>/<id>.jsonl`
- 业务域:`<root>/<domain_id>/<peer_key>/<id>.jsonl`
- 首行 `_meta`(id/created_at/title/peer)+ 后续每行 Turn;落盘脱敏;**无轮转**(CV-6 不丢)

放 `cognition/`(与 trace 同尺度);import `domain.Address`(domain 不依赖 cognition,无循环)。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from karvyloop.domain.registry import Address


CONVERSATION_SCHEMA = "karvyloop-conversation"
FORMAT_VERSION = 1
MAX_FIELD_CHARS = 64 * 1024
DEFAULT_CONTEXT_TURNS = 12

BRAIN_FAST = "fast"
BRAIN_SLOW = "slow"

# karvy world(私聊场)的 domain_id(docs/23:l0 = 跨域标识 / 大群)
KARVY_WORLD_DOMAIN = "l0"

# 落盘脱敏(镜像 coding/session.py;对话里可能混 key)
_REDACT_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}"),
    re.compile(r"KARVYLOOP_KEY_[A-Za-z0-9]{10,}"),
]


def _redact(text: str) -> str:
    out = text
    for p in _REDACT_PATTERNS:
        out = p.sub("[redacted]", out)
    return out


def _scrub_field(v: str) -> str:
    v = _redact(v)
    if len(v) > MAX_FIELD_CHARS:
        v = v[:MAX_FIELD_CHARS] + "…"
    return v


def _atomic_append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def karvy_world_peer(role: str = "observer", agent_id: str = "karvy") -> Address:
    """默认私聊对象(karvy world 里的小卡)。"""
    return Address(domain_id=KARVY_WORLD_DOMAIN, role=role, agent_id=agent_id)


def _safe(s: Optional[str]) -> str:
    """文件名安全化(domain_id/role/agent_id 进路径)。

    只替换**文件系统危险字符**(路径分隔/通配/控制/空白),**保留 CJK 等 Unicode**
    —— 否则中文 domain_id("装修"/"财务")会全被抹成 `_` 互相碰撞。
    """
    if not s:
        return "_"
    return re.sub(r'[\\/:*?"<>|\x00-\x1f\s]', "_", s)


def _peer_key(peer: Address) -> str:
    """peer 在文件系统里的目录名(role + agent_id)。"""
    return f"{_safe(peer.role)}__{_safe(peer.agent_id)}"


def _peer_to_record(peer: Address) -> dict:
    return {"domain_id": peer.domain_id, "role": peer.role, "agent_id": peer.agent_id}


def _peer_from_record(rec: dict) -> Address:
    return Address(
        domain_id=rec.get("domain_id", KARVY_WORLD_DOMAIN),
        role=rec.get("role", "observer"),
        agent_id=rec.get("agent_id"),
    )


# ---- 数据模型(docs/26 §4)----


@dataclass(frozen=True)
class Turn:
    """一轮对话(一来一回)。brain = fast(快脑命中)/ slow(慢脑);task_id 回查 trace。

    `data`:可选**结构化负载**(默认 None)。给"非普通一来一回"的回合带结构 —— 如圆桌
    (`{"roundtable": {transcript, conclusion, rounds, ...}}`),让重开时前端渲成群聊串而非
    一坨 markdown。普通回合 data=None,落盘不带这字段(记录干净、0 回归)。
    """
    user_intent: str
    agent_response: str
    brain: str = BRAIN_SLOW
    ts: float = 0.0
    task_id: str = ""
    data: Optional[dict] = None

    def to_record(self) -> dict:
        rec = {
            "user_intent": self.user_intent,
            "agent_response": self.agent_response,
            "brain": self.brain,
            "ts": self.ts,
            "task_id": self.task_id,
        }
        if self.data:
            rec["data"] = self.data
        return rec

    @classmethod
    def from_record(cls, rec: dict) -> "Turn":
        d = rec.get("data")
        return cls(
            user_intent=rec.get("user_intent", ""),
            agent_response=rec.get("agent_response", ""),
            brain=rec.get("brain", BRAIN_SLOW),
            ts=float(rec.get("ts", 0.0)),
            task_id=rec.get("task_id", ""),
            data=d if isinstance(d, dict) else None,
        )


@dataclass
class Conversation:
    """一段对话。归属 = peer Address(CV-12)。

    无 `status`:"当前活跃哪段"是运行时概念(Manager 持有,不持久化)。
    `last_active_at` 由最后一轮 ts 派生(续最近排序,**不**做超时判定)。
    """
    id: str
    created_at: float
    peer: Address                          # 归属:跟谁聊 + 在哪个场(CV-12)
    title: str = ""
    turns: list[Turn] = field(default_factory=list)

    @property
    def last_active_at(self) -> float:
        return self.turns[-1].ts if self.turns else self.created_at

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def is_private(self) -> bool:
        """是否私聊(karvy world)。"""
        return self.peer.domain_id == KARVY_WORLD_DOMAIN

    def context_view(self, max_turns: int = DEFAULT_CONTEXT_TURNS) -> tuple[Turn, ...]:
        """快慢脑共享上下文的**只读**视图(CV-8):最近 max_turns 轮(旧→新)。"""
        if max_turns <= 0:
            return ()
        return tuple(self.turns[-max_turns:])


@dataclass
class ConversationMeta:
    """历史列表元数据(不载全文)。"""
    id: str
    created_at: float
    title: str
    last_active_at: float
    turn_count: int
    peer: Address


# ---- 存储(JSONL per-conversation,按场分区)----


class ConversationStore:
    """对话存储:`<root>/<domain_id>/<peer_key>/<id>.jsonl`,按 (场, 角色) 分区(CV-13)。"""

    def __init__(self, root_dir: Path, *, clock=time.time) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._clock = clock

    def _peer_dir(self, peer: Address) -> Path:
        return self._root / _safe(peer.domain_id) / _peer_key(peer)

    def _path(self, peer: Address, conv_id: str) -> Path:
        return self._peer_dir(peer) / f"{conv_id}.jsonl"

    # ---- 新建 / 追加 ----

    def new(self, peer: Address, title: str = "") -> Conversation:
        """开一段新对话(归属 peer)。"""
        cid = uuid.uuid4().hex[:16]
        created = self._clock()
        conv = Conversation(id=cid, created_at=created, peer=peer, title=title, turns=[])
        d = self._peer_dir(peer)
        d.mkdir(parents=True, exist_ok=True)
        meta_line = json.dumps(
            {
                "_meta": True,
                "schema": CONVERSATION_SCHEMA,
                "v": FORMAT_VERSION,
                "id": cid,
                "created_at": created,
                "title": _scrub_field(title) if title else "",
                "peer": _peer_to_record(peer),
            },
            ensure_ascii=False,
        ) + "\n"
        _atomic_append(self._path(peer, cid), meta_line)
        return conv

    def append_turn(self, conv: Conversation, turn: Turn) -> Turn:
        """追加一轮(CV-10)。ts 为 0 → clock 补;落盘脱敏,内存保真。"""
        if not turn.ts:
            turn = Turn(
                user_intent=turn.user_intent,
                agent_response=turn.agent_response,
                brain=turn.brain,
                ts=self._clock(),
                task_id=turn.task_id,
                data=turn.data,   # 保留结构化负载(圆桌等),别在补 ts 时丢掉
            )
        disk_rec = turn.to_record()
        disk_rec["user_intent"] = _scrub_field(turn.user_intent)
        disk_rec["agent_response"] = _scrub_field(turn.agent_response)
        _atomic_append(self._path(conv.peer, conv.id), json.dumps(disk_rec, ensure_ascii=False) + "\n")
        conv.turns.append(turn)
        return turn

    # ---- 读取 / 恢复 ----

    def load(self, peer: Address, conv_id: str) -> Optional[Conversation]:
        """读文件重建(resume,CV-6)。文件不存在返 None。"""
        path = self._path(peer, conv_id)
        return self._load_path(path)

    def _load_path(self, path: Path) -> Optional[Conversation]:
        if not path.exists():
            return None
        created_at = self._clock()
        title = ""
        peer = karvy_world_peer()
        turns: list[Turn] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_meta"):
                    created_at = float(rec.get("created_at", created_at))
                    title = rec.get("title", "")
                    if isinstance(rec.get("peer"), dict):
                        peer = _peer_from_record(rec["peer"])
                    continue
                turns.append(Turn.from_record(rec))
        return Conversation(
            id=path.stem, created_at=created_at, peer=peer, title=title, turns=turns
        )

    def list_conversations(self, peer: Address) -> list[ConversationMeta]:
        """列**该 peer**(场 + 角色)的历史,按 last_active 倒序(CV-13 隔离)。"""
        d = self._peer_dir(peer)
        if not d.is_dir():
            return []
        metas: list[ConversationMeta] = []
        for path in d.glob("*.jsonl"):
            m = self._read_meta(path)
            if m is not None:
                metas.append(m)
        metas.sort(key=lambda m: m.last_active_at, reverse=True)
        return metas

    def most_recent(self, peer: Address) -> Optional[Conversation]:
        """该 peer 最近活跃的一段(默认续上,CV-6)。无则 None。"""
        metas = self.list_conversations(peer)
        if not metas:
            return None
        return self.load(peer, metas[0].id)

    def iter_all_metas(self) -> list[ConversationMeta]:
        """跨**所有** peer 扫历史 meta(用于"哪些对象沟通过 + 最近何时";左栏私聊/群聊排序)。
        结构 `<root>/<domain_id>/<peer_key>/<id>.jsonl` → glob 两层目录。每条 meta 自带 peer。"""
        metas: list[ConversationMeta] = []
        try:
            for path in self._root.glob("*/*/*.jsonl"):
                m = self._read_meta(path)
                if m is not None:
                    metas.append(m)
        except OSError:
            pass
        return metas

    def _read_meta(self, path: Path) -> Optional[ConversationMeta]:
        created_at = 0.0
        title = ""
        peer = karvy_world_peer()
        last_ts = 0.0
        turn_count = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("_meta"):
                        created_at = float(rec.get("created_at", 0.0))
                        title = rec.get("title", "")
                        if isinstance(rec.get("peer"), dict):
                            peer = _peer_from_record(rec["peer"])
                        continue
                    turn_count += 1
                    last_ts = float(rec.get("ts", last_ts))
        except OSError:
            return None
        return ConversationMeta(
            id=path.stem, created_at=created_at, title=title,
            last_active_at=last_ts if last_ts else created_at,
            turn_count=turn_count, peer=peer,
        )


# ---- 编排器(ConversationManager,多场)----


def _default_summarizer(conv: "Conversation") -> str:
    intents = [t.user_intent for t in conv.turns if t.user_intent]
    joined = " / ".join(intents[-8:])
    if len(joined) > 500:
        joined = joined[:500] + "…"
    return joined


class ConversationManager:
    """对话编排器:持有「当前场+角色(peer)」及其当前对话。

    多场:用户可在私聊(karvy world)与各业务域线之间切(switch_peer);
    每条线独立续上/历史/上下文(CV-13)。

    依赖倒置:`trace_index` duck-type(只调 append_summary),不 import fastbrain。
    """

    def __init__(
        self,
        store: ConversationStore,
        *,
        trace_index: Optional[object] = None,
        domain_registry: Optional[object] = None,
        summarizer=None,
        context_turns: int = DEFAULT_CONTEXT_TURNS,
    ) -> None:
        self._store = store
        self._trace_index = trace_index
        self._domain_registry = domain_registry  # duck-type: get(id)→域(含 value_md)
        self._summarizer = summarizer or _default_summarizer
        self._context_turns = context_turns
        self._peer: Optional[Address] = None
        self._current: Optional[Conversation] = None

    def set_peer(self, peer: Address) -> Conversation:
        """切到「场+角色」(peer)。续上该 peer 最近一段(CV-6),无则新。

        切场前把上一条线的旧对话摘要喂 Trace(CV-4)。
        """
        if self._peer is not None and (self._peer.domain_id, self._peer.role, self._peer.agent_id) == (peer.domain_id, peer.role, peer.agent_id):
            # 同一 peer:不动当前线
            if self._current is None:
                self._current = self._store.most_recent(peer) or self._store.new(peer)
            return self._current
        self._summarize_to_trace(self._current)
        self._peer = peer
        self._current = self._store.most_recent(peer) or self._store.new(peer)
        return self._current

    def start(self, peer: Optional[Address] = None) -> Conversation:
        """启动:默认私聊小卡(karvy world),续最近一段。"""
        return self.set_peer(peer or karvy_world_peer())

    def current(self) -> Optional[Conversation]:
        return self._current

    def current_peer(self) -> Optional[Address]:
        return self._peer

    def new_conversation(self, title: str = "") -> Conversation:
        """在当前 peer 下开新对话(CV-2 边界 + CV-4 旧对话摘要喂 Trace)。"""
        peer = self._peer or karvy_world_peer()
        self._summarize_to_trace(self._current)
        self._current = self._store.new(peer, title)
        self._peer = peer
        return self._current

    def resume(self, peer: Address, conv_id: str) -> Optional[Conversation]:
        """resume 指定 (peer, 对话)。找不到返 None(当前不变)。"""
        loaded = self._store.load(peer, conv_id)
        if loaded is not None:
            self._summarize_to_trace(self._current) if (self._current and self._current.id != loaded.id) else None
            self._current = loaded
            self._peer = loaded.peer
        return loaded

    def list_conversations(self, peer: Optional[Address] = None) -> list[ConversationMeta]:
        p = peer or self._peer
        if p is None:
            return []
        return self._store.list_conversations(p)

    def all_conversation_metas(self) -> list[ConversationMeta]:
        """跨所有 peer 的历史 meta(左栏:谁沟通过 + 最近何时)。"""
        return self._store.iter_all_metas()

    def context_view(self) -> tuple[Turn, ...]:
        if self._current is None:
            return ()
        return self._current.context_view(self._context_turns)

    def governance_text(self) -> str:
        """当前场的治理文本(CV-14):业务域线 = 该域 value.md **+ deontic 硬规则**(forbid/oblige/permit,
        继承父域已在 registry 解析);私聊(l0)/ 无 registry / 域不存在 → 空串。

        框成系统指令喂慢脑前缀 —— 让同一角色在不同企业受不同价值观 + 硬规则约束 → 不同表现。

        P2-a:此前只注入 value.md,deontic 的 forbid/oblige 从不进运行时护栏(域的硬规则在执行路径
        形同虚设),且 value.md 空时整段丢弃(有 forbid 无 value.md 的域裸奔)。现在两者独立装配。
        """
        if self._peer is None or self._peer.domain_id == KARVY_WORLD_DOMAIN:
            return ""
        reg = self._domain_registry
        if reg is None:
            return ""
        try:
            domain = reg.get(self._peer.domain_id)
        except Exception:
            domain = None
        if domain is None:
            return ""
        name = getattr(domain, "name", self._peer.domain_id)
        blocks: list[str] = []
        value_text = getattr(getattr(domain, "value_md", None), "text", "") or ""
        if value_text:
            # 拍 9.3b:value.md 封顶(docs/28 TK token 纪律)—— 防超长 value.md 每轮重付;
            # 域级稳定文本本就靠 prompt cache 复用,这里再兜一道上限。
            if len(value_text) > 1500:
                value_text = value_text[:1500] + "…"
            blocks.append(
                f"你正在业务域「{name}」里工作,必须遵循该域的价值观(value.md):\n{value_text}"
            )
        # deontic 硬规则(forbid/oblige/permit)→ 运行时软护栏(NL 规则的正确 enforcement)
        try:
            from karvyloop.domain.deontic import deontic_guardrail_text
            guard = deontic_guardrail_text(getattr(domain, "deontic", None))
        except Exception:
            guard = ""
        if guard:
            if not blocks:
                guard = f"你正在业务域「{name}」里工作。\n" + guard
            blocks.append(guard)
        return "\n\n".join(blocks)

    def record_turn(
        self,
        user_intent: str,
        agent_response: str,
        *,
        brain: str = BRAIN_SLOW,
        task_id: str = "",
        data: Optional[dict] = None,
    ) -> Turn:
        """记一轮进当前对话(CV-10)。无当前 peer 则默认私聊小卡。data=结构化负载(圆桌等)。"""
        if self._current is None:
            self.start()
        turn = Turn(
            user_intent=user_intent, agent_response=agent_response,
            brain=brain, task_id=task_id, data=data,
        )
        return self._store.append_turn(self._current, turn)  # type: ignore[arg-type]

    def create_record(
        self,
        peer: Address,
        *,
        title: str = "",
        user_intent: str = "",
        agent_response: str = "",
        brain: str = BRAIN_SLOW,
        task_id: str = "",
        data: Optional[dict] = None,
    ) -> Conversation:
        """建一条**独立对话**(归属 peer)并写一轮 —— **不**切走当前对话。

        给圆桌等"产物"做成 history 里可重开的记录:调用方拿 conv.id 让首页卡片精准跳转
        (switchPeer 到这个群场 → resume 这条)。peer 必须与之后跳转用的 peer 同身份
        (domain_id+role+agent_id)否则按 _peer_key 落到别的目录、resume 找不到。
        """
        conv = self._store.new(peer, title)
        turn = Turn(user_intent=user_intent, agent_response=agent_response,
                    brain=brain, task_id=task_id, data=data)
        self._store.append_turn(conv, turn)
        return conv

    def _summarize_to_trace(self, conv: Optional[Conversation]) -> None:
        if self._trace_index is None or conv is None or not conv.turns:
            return
        summary = self._summarizer(conv)
        if not summary:
            return
        try:
            self._trace_index.append_summary({
                "kind": "conversation_summary",
                "conversation_id": conv.id,
                "domain_id": conv.peer.domain_id,
                "peer_role": conv.peer.role,
                "peer_agent_id": conv.peer.agent_id,
                "turn_count": conv.turn_count,
                "summary": summary,
            })
        except Exception:
            pass


__all__ = [
    "BRAIN_FAST",
    "BRAIN_SLOW",
    "CONVERSATION_SCHEMA",
    "DEFAULT_CONTEXT_TURNS",
    "KARVY_WORLD_DOMAIN",
    "Conversation",
    "ConversationManager",
    "ConversationMeta",
    "ConversationStore",
    "Turn",
    "karvy_world_peer",
]
