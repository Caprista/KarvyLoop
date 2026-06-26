"""test_conversation — 对话数据层 + 持久化 + 按场分区(M3+ 拍 9.1a + 9.2a 归属)。

设计:docs/26 §C。

AC 矩阵:
- 数据模型(Turn frozen / Conversation peer / context_view / last_active 派生)
- new/append/load(JSONL + 按场分区路径)
- 历史 most_recent(按 peer 隔离)
- 跨进程恢复(关机重启)+ peer 持久
- 落盘脱敏 + 内存保真
- CV-2 无超时(grep 锁)
- CV-13 场隔离:不同 peer 的对话互不串
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from karvyloop.cognition.conversation import (
    BRAIN_FAST,
    BRAIN_SLOW,
    KARVY_WORLD_DOMAIN,
    Conversation,
    ConversationMeta,
    ConversationStore,
    Turn,
    karvy_world_peer,
)
from karvyloop.domain.registry import Address


PRIVATE = karvy_world_peer()  # 私聊小卡(l0/observer/karvy)
BIZ = Address(domain_id="dom-装修", role="agent", agent_id="设计师")


@pytest.fixture
def fixed_clock():
    base = [1_700_000_000.0]

    def clock() -> float:
        return base[0]

    def advance(s: float) -> None:
        base[0] += s

    return clock, advance


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations")


# ---- 数据模型 ----


def test_turn_roundtrip_record() -> None:
    t = Turn(user_intent="删掉它", agent_response="删了", brain=BRAIN_SLOW, ts=5.0, task_id="abc")
    assert Turn.from_record(t.to_record()) == t


def test_conversation_peer_and_is_private() -> None:
    c = Conversation(id="c1", created_at=0.0, peer=PRIVATE)
    assert c.is_private is True
    c2 = Conversation(id="c2", created_at=0.0, peer=BIZ)
    assert c2.is_private is False


def test_conversation_last_active_derived() -> None:
    c = Conversation(id="c1", created_at=10.0, peer=PRIVATE)
    assert c.last_active_at == 10.0
    c.turns.append(Turn("a", "b", ts=110.0))
    assert c.last_active_at == 110.0


def test_context_view_recent_oldest_first() -> None:
    c = Conversation(id="c1", created_at=0.0, peer=PRIVATE)
    for i in range(20):
        c.turns.append(Turn(f"u{i}", f"a{i}", ts=float(i)))
    view = c.context_view(max_turns=5)
    assert [t.user_intent for t in view] == ["u15", "u16", "u17", "u18", "u19"]
    assert c.context_view(max_turns=0) == ()


# ---- new / append / load(按场分区)----


def test_new_creates_file_under_peer_dir(store: ConversationStore, tmp_path: Path) -> None:
    conv = store.new(PRIVATE, title="私聊")
    # 路径:<root>/l0/observer__karvy/<id>.jsonl
    path = tmp_path / "conversations" / KARVY_WORLD_DOMAIN / "observer__karvy" / f"{conv.id}.jsonl"
    assert path.exists()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["_meta"] is True
    assert first["peer"]["domain_id"] == "l0"
    assert first["peer"]["role"] == "observer"


def test_biz_conversation_under_domain_dir(store: ConversationStore, tmp_path: Path) -> None:
    conv = store.new(BIZ, title="装修")
    path = tmp_path / "conversations" / "dom-装修" / "agent__设计师" / f"{conv.id}.jsonl"
    assert path.exists()


def test_append_and_load_roundtrip(store: ConversationStore) -> None:
    conv = store.new(PRIVATE, title="T")
    store.append_turn(conv, Turn("u1", "a1", brain=BRAIN_FAST, ts=1.0, task_id="x"))
    store.append_turn(conv, Turn("u2", "a2", brain=BRAIN_SLOW, ts=2.0))
    loaded = store.load(PRIVATE, conv.id)
    assert loaded is not None
    assert loaded.peer.domain_id == "l0"
    assert loaded.turn_count == 2
    assert loaded.turns[0].brain == BRAIN_FAST
    assert loaded.turns[1].user_intent == "u2"


def test_load_missing_returns_none(store: ConversationStore) -> None:
    assert store.load(PRIVATE, "nope") is None


def test_append_fills_ts(tmp_path: Path, fixed_clock) -> None:
    clock, advance = fixed_clock
    s = ConversationStore(tmp_path / "c", clock=clock)
    conv = s.new(PRIVATE)
    advance(50)
    t = s.append_turn(conv, Turn("hi", "yo"))
    assert t.ts == clock()


# ---- 历史 / most_recent(按 peer 隔离 CV-13)----


def test_list_and_most_recent_per_peer(tmp_path: Path, fixed_clock) -> None:
    clock, advance = fixed_clock
    s = ConversationStore(tmp_path / "c", clock=clock)
    c1 = s.new(PRIVATE, title="老")
    advance(1); s.append_turn(c1, Turn("u", "a"))
    advance(100)
    c2 = s.new(PRIVATE, title="新")
    advance(1); s.append_turn(c2, Turn("u", "a"))
    metas = s.list_conversations(PRIVATE)
    assert [m.title for m in metas] == ["新", "老"]
    assert s.most_recent(PRIVATE).id == c2.id


def test_scope_isolation_private_vs_biz(tmp_path: Path) -> None:
    """CV-13:私聊和业务域线互不串 —— list 各看各的。"""
    s = ConversationStore(tmp_path / "c")
    cp = s.new(PRIVATE, title="私聊话题")
    s.append_turn(cp, Turn("私", "a", ts=1.0))
    cb = s.new(BIZ, title="装修话题")
    s.append_turn(cb, Turn("装", "a", ts=1.0))
    priv = s.list_conversations(PRIVATE)
    biz = s.list_conversations(BIZ)
    assert [m.title for m in priv] == ["私聊话题"]
    assert [m.title for m in biz] == ["装修话题"]
    # most_recent 也隔离
    assert s.most_recent(PRIVATE).id == cp.id
    assert s.most_recent(BIZ).id == cb.id


def test_list_empty(store: ConversationStore) -> None:
    assert store.list_conversations(PRIVATE) == []
    assert store.most_recent(PRIVATE) is None


# ---- 跨进程恢复(CV-6)+ peer 持久 ----


def test_persistence_across_reopen(tmp_path: Path) -> None:
    d = tmp_path / "c"
    s1 = ConversationStore(d)
    conv = s1.new(BIZ, title="持久")
    s1.append_turn(conv, Turn("记得我", "记得", ts=1.0))
    s2 = ConversationStore(d)
    loaded = s2.load(BIZ, conv.id)
    assert loaded is not None
    assert loaded.peer.domain_id == "dom-装修"  # peer 持久
    assert loaded.turns[0].user_intent == "记得我"


def test_persistence_across_real_subprocess(tmp_path: Path) -> None:
    d = tmp_path / "c"
    writer = textwrap.dedent(
        f"""
        from pathlib import Path
        from karvyloop.cognition.conversation import ConversationStore, Turn, karvy_world_peer
        s = ConversationStore(Path({str(d)!r}))
        conv = s.new(karvy_world_peer(), title="子进程")
        s.append_turn(conv, Turn("第一句", "回应1", ts=1.0))
        s.append_turn(conv, Turn("第二句", "回应2", ts=2.0))
        print(conv.id)
        """
    )
    r = subprocess.run([sys.executable, "-c", writer], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    cid = r.stdout.strip().splitlines()[-1]
    s = ConversationStore(d)
    loaded = s.load(karvy_world_peer(), cid)
    assert loaded is not None and loaded.turn_count == 2


# ---- 落盘脱敏 ----


def test_disk_redacts_keys_memory_keeps(store: ConversationStore, tmp_path: Path) -> None:
    conv = store.new(PRIVATE)
    secret = "sk-ant-abcdefghijklmnop12345"
    store.append_turn(conv, Turn(f"我的 key 是 {secret}", "收到", ts=1.0))
    assert secret in conv.turns[0].user_intent  # 内存保真
    disk = (tmp_path / "conversations" / "l0" / "observer__karvy" / f"{conv.id}.jsonl").read_text(encoding="utf-8")
    assert secret not in disk and "[redacted]" in disk


# ---- 坏行跳过 ----


def test_corrupt_line_skipped(store: ConversationStore, tmp_path: Path) -> None:
    conv = store.new(PRIVATE)
    store.append_turn(conv, Turn("good", "ok", ts=1.0))
    path = tmp_path / "conversations" / "l0" / "observer__karvy" / f"{conv.id}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write("{坏 json\n")
    store.append_turn(conv, Turn("good2", "ok2", ts=2.0))
    loaded = store.load(PRIVATE, conv.id)
    assert [t.user_intent for t in loaded.turns] == ["good", "good2"]


# ---- CV-2 无超时(grep 锁)----


def test_no_timeout_concept_in_source() -> None:
    import karvyloop.cognition.conversation as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    lines, in_doc = [], False
    for line in src.splitlines():
        st = line.strip()
        if st.startswith('"""') or st.startswith("'''"):
            if st.count('"""') >= 2 or st.count("'''") >= 2:
                continue
            in_doc = not in_doc
            continue
        if in_doc or st.startswith("#"):
            continue
        lines.append(line.lower())
    code = "\n".join(lines)
    for forbidden in ("timeout", "expire", "inactivity", "ttl"):
        assert forbidden not in code, f"CV-2 违反 — 数据层出现超时逻辑: {forbidden}"
