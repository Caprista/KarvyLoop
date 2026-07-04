"""test_cognition_graph — ch4 pillar 3:认知图谱(Belief → 网状)。

AC:
- AC4 /api/memory/graph 端点返 nodes/edges(生产走 concept_graph,词面作其内回退);无 memory → 空

注:旧的纯词面 `belief_graph`(及其 AC1-AC3 零件测)已删 —— 它零生产调用方,生产图谱一律
走 concept_graph(见 console/routes api_memory_graph)。词面匹配的语义现由 concept_graph 的
回退分支承载,并在 test_cognition_concepts.py::test_concept_graph_token_fallback 覆盖。
"""
from __future__ import annotations


# ---- AC4:API(生产路径:/api/memory/graph → concept_graph)----
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
