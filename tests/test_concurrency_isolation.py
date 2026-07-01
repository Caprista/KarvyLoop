"""test_concurrency_isolation — #6:多协作并发写同一角色/知识库不丢数据(Hardy 报的并发隐患)。

核实后的真相(比最初说的窄):角色 7 文件在 drive 时**只读**(拼 system prompt),不写 → 并发 drive 同一角色
不 race 文件;共享的 MEMORY/Trace 本就有锁。真正的窄缺口 = ① RoleRegistry 写文件的方法**无锁**
(两次 create_atom 并发 add_atom 同一角色 → 读-改-写互相盖,少一个)② MemoryManager.write 的 put+落盘
**没持锁**(并发沉淀互相盖)。本测用 barrier 齐发线程最大化竞争,锁住后应一条不丢。
"""
from __future__ import annotations

import threading

from karvyloop.roles.registry import RoleRegistry
from karvyloop.cognition.memory import MemoryManager
from karvyloop.cognition.belief_store import BeliefStore
from karvyloop.schemas.cognition import Belief


def test_concurrent_add_atom_no_lost_writes(tmp_path):
    """N 个线程同时给同一角色 add_atom 不同原子 → 一个都不能丢(读-改-写有锁)。"""
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("pm", identity="x")
    N = 24
    barrier = threading.Barrier(N)

    def worker(i):
        barrier.wait()                 # 齐发,最大化竞争窗口
        reg.add_atom("pm", f"atom{i}")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    atoms = set(reg.read_paradigm("pm")["atom_ids"])
    want = {f"atom{i}" for i in range(N)}
    assert atoms == want, f"并发 add_atom 丢了:{want - atoms}"


def test_concurrent_memory_write_no_lost(tmp_path):
    """N 个线程同时 mem.write 不同 Belief → 一条都不能丢(put+落盘持锁串行)。"""
    mem = MemoryManager(store=BeliefStore(tmp_path / "b.json"))
    N = 32
    barrier = threading.Barrier(N)

    def worker(i):
        barrier.wait()
        mem.write(Belief(content=f"知识{i}", freshness_ts=float(i) + 1, scope="personal",
                         provenance={"source": "t", "ts": float(i) + 1, "kind": "knowledge"}))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    contents = {b.content for b in mem.index.all("personal")}
    want = {f"知识{i}" for i in range(N)}
    assert contents == want, f"并发 write 丢了:{want - contents}"
    # 落盘也全(新实例从 store 加载)→ 重启不丢
    mem2 = MemoryManager(store=BeliefStore(tmp_path / "b.json"))
    assert {b.content for b in mem2.index.all("personal")} == want
