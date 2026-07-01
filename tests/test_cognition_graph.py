"""test_cognition_graph — ch4 pillar 3:认知图谱(Belief → 网状)。

AC:
- AC1 共享 ≥min_shared 个显著 token 的两节点 → 一条边
- AC2 只靠停用词(的/了/用户/and)不连边(否则全连成一团)
- AC3 节点带 content/kind/source/degree;边带 weight/via
- AC4 /api/memory/graph 端点返 nodes/edges;无 memory → 空
"""
from __future__ import annotations

import types

import pytest

from karvyloop.cognition.graph import belief_graph


def _b(content, kind="fact", source="ingest"):
    return types.SimpleNamespace(content=content, provenance={"kind": kind, "source": source})


# ---- AC1/AC3 ----
def test_edge_on_shared_tokens():
    beliefs = [
        _b("用户用 Rust 写后端服务"),
        _b("用户在后端服务里用 Rust 处理并发"),   # 与上条共享"后端/服务/Rust"
        _b("用户喜欢喝美式咖啡"),                   # 不相关
    ]
    g = belief_graph(beliefs, min_shared=2)
    assert len(g["nodes"]) == 3
    ids = {(e["source"], e["target"]) for e in g["edges"]}
    assert (0, 1) in ids                      # 0↔1 相关
    assert (0, 2) not in ids and (1, 2) not in ids  # 咖啡那条不连
    # 节点字段 + degree
    assert g["nodes"][0]["kind"] == "fact" and g["nodes"][0]["source"] == "ingest"
    assert g["nodes"][0]["degree"] == 1 and g["nodes"][2]["degree"] == 0
    # 边字段
    e = [e for e in g["edges"] if (e["source"], e["target"]) == (0, 1)][0]
    assert e["weight"] >= 2 and isinstance(e["via"], list)


# ---- AC2:停用词不连 ----
def test_stopwords_do_not_connect():
    # 两条只共享"用户/喜欢/的"这类停用词 → 不该连
    beliefs = [_b("用户喜欢猫"), _b("用户喜欢狗")]
    g = belief_graph(beliefs, min_shared=2)
    assert g["edges"] == []


def test_empty():
    g = belief_graph([])
    assert g == {"nodes": [], "edges": []}


# ---- AC4:API ----
def test_graph_api():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.schemas.cognition import Belief

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    # 无 memory → 空
    app.state.memory = None
    assert TestClient(app).get("/api/memory/graph").json() == {"nodes": [], "edges": []}
    # 有 memory
    mem = MemoryManager()
    for c in ["用户用 Rust 写后端服务", "后端服务用 Rust 抗并发"]:
        mem.write(Belief(content=c, provenance={"kind": "fact", "source": "ingest"},
                         freshness_ts=1.0, scope="personal"))
    app.state.memory = mem
    g = TestClient(app).get("/api/memory/graph").json()
    assert len(g["nodes"]) == 2 and len(g["edges"]) == 1
