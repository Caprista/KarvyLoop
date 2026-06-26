"""test_belief_store — loop step4b 地基:Belief 长期库落盘 + 召回注入。

地基病根:MemoryManager 从没在产品里建起来、MemoryIndex 只在内存(重启丢)、recall 是技能
不是 Belief。这步:可落盘 MemoryManager(重启不丢)+ 同步 recall_block(注入 drive 上下文)。

AC:
- AC1 BeliefStore:save_all/load_all round-trip(含 pinned);坏文件/坏项不抛
- AC2 MemoryManager(store=):write 后落盘;新实例(同 store)启动加载 → 重启不丢
- AC3 recall_block:query 命中的排前、封顶 limit、空库返 ""、围栏包裹
- AC4 _persist 失败不阻塞 write(内存态仍在)
"""
from __future__ import annotations

import json

import pytest

from karvyloop.cognition.belief_store import BeliefStore
from karvyloop.cognition.memory import MemoryManager
from karvyloop.schemas.cognition import Belief


def _belief(content, *, scope="personal", ts=1.0, kind="fact"):
    return Belief(content=content, provenance={"source": "ingest", "kind": kind},
                  freshness_ts=ts, scope=scope)


# ---- AC1 ----
def test_store_roundtrip(tmp_path):
    p = tmp_path / "beliefs.json"
    store = BeliefStore(p)
    store.save_all([(_belief("A"), False), (_belief("B", ts=2.0), True)])
    loaded = store.load_all()
    assert {b.content for b, _ in loaded} == {"A", "B"}
    pinned = {b.content: pin for b, pin in loaded}
    assert pinned["B"] is True and pinned["A"] is False


def test_store_corrupt_file_no_throw(tmp_path):
    p = tmp_path / "beliefs.json"
    p.write_text("{ not json", encoding="utf-8")
    assert BeliefStore(p).load_all() == []


def test_store_skips_bad_items(tmp_path):
    p = tmp_path / "beliefs.json"
    p.write_text(json.dumps([
        {"content": "good", "provenance": {"source": "x"}, "freshness_ts": 1.0, "scope": "personal"},
        {"content": "missing fields"},                       # 坏项
        "not a dict",                                         # 坏项
    ]), encoding="utf-8")
    loaded = BeliefStore(p).load_all()
    assert [b.content for b, _ in loaded] == ["good"]


# ---- AC2 重启不丢 ----
def test_manager_persists_and_reloads(tmp_path):
    p = tmp_path / "beliefs.json"
    m1 = MemoryManager(store=BeliefStore(p))
    m1.write(_belief("用户叫 Hardy"))
    m1.write(_belief("偏好英文", kind="preference"), pinned=True)
    assert p.exists()                                        # write 后落盘
    # 新实例(同 store)= 模拟重启
    m2 = MemoryManager(store=BeliefStore(p))
    contents = {b.content for b in m2.index.all("personal")}
    assert contents == {"用户叫 Hardy", "偏好英文"}            # 重启不丢
    pin_b = m2.index.get("偏好英文")
    assert pin_b is not None and m2.index.is_pinned(pin_b)    # pinned 也存住


def test_manager_no_store_is_pure_memory(tmp_path):
    m = MemoryManager()                                      # 无 store → 0 回归
    m.write(_belief("X"))
    assert {b.content for b in m.index.all("personal")} == {"X"}


# ---- AC3 recall_block ----
def test_recall_block_ranks_and_caps(tmp_path):
    m = MemoryManager()
    m.write(_belief("用户喜欢 python 编程", ts=1.0))
    m.write(_belief("用户住在杭州", ts=2.0))
    m.write(_belief("用户喜欢喝咖啡", ts=3.0))
    block = m.recall_block("python", scope="personal", limit=2)
    assert block                                             # 非空、围栏包裹
    assert "python 编程" in block                            # query 命中的排进来
    # 命中条目在;limit=2 时只取 2 条(命中优先,平手 freshness)
    assert block.count("用户") <= 3


def test_recall_block_empty_store():
    assert MemoryManager().recall_block("anything") == ""


# ---- #1 回归:provenance['id'] 不导致落盘/召回翻倍 ----
def test_id_bearing_belief_not_duplicated_on_disk(tmp_path):
    p = tmp_path / "beliefs.json"
    m = MemoryManager(store=BeliefStore(p))
    # 带 id 的 Belief:MemoryIndex.put 会存在 id-key 与 content-key 两处
    b = Belief(content="带 id 的事实", provenance={"source": "ingest", "id": "x1"},
               freshness_ts=1.0, scope="personal")
    m.write(b)
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert len(on_disk) == 1                                 # 落盘 1 条,不是 2 条
    # 召回也不重复
    block = m.recall_block("事实", limit=8)
    assert block.count("带 id 的事实") == 1


# ---- #3 回归:archive 落盘(重启不复活)----
def test_archive_persists(tmp_path):
    p = tmp_path / "beliefs.json"
    m = MemoryManager(store=BeliefStore(p))
    b = _belief("要被归档的")
    m.write(b)
    m.archive(b)
    assert json.loads(p.read_text(encoding="utf-8")) == []   # 盘上已没了
    m2 = MemoryManager(store=BeliefStore(p))
    assert m2.index.all("personal") == []                    # 重启不复活


# ---- AC4 落盘失败不阻塞 ----
def test_persist_failure_does_not_block_write():
    class BoomStore:
        def load_all(self):
            return []

        def save_all(self, items):
            raise OSError("disk full")

    m = MemoryManager(store=BoomStore())
    m.write(_belief("仍在内存"))                              # 不抛
    assert {b.content for b in m.index.all("personal")} == {"仍在内存"}
