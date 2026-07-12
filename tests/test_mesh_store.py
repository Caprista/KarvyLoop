"""test_mesh_store — MeshLog JSONL 持久化:跨重启存活 + 重载去重 + 坏行防御。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.mesh.store import MeshLogStore  # noqa: E402
from karvyloop.mesh.synclog import MeshLog  # noqa: E402


def test_append_and_reload_survives_restart(tmp_path):
    store = MeshLogStore(tmp_path)
    log = MeshLog("dev-a")
    e1 = log.append("belief-created", {"m": "学到一条"}, wall=1000)
    e2 = log.append("skill-crystallized", {"s": "做表"}, wall=1001)
    store.append([e1, e2])

    # "重启":新进程从盘重建 log
    log2 = MeshLogStore(tmp_path).open_log("dev-a")
    ids = {e.event_id for e in log2.entries()}
    assert ids == {e1.event_id, e2.event_id}
    assert len(log2) == 2


def test_reload_dedups_and_clock_advances(tmp_path):
    store = MeshLogStore(tmp_path)
    log = MeshLog("dev-a")
    e1 = log.append("trace", {}, wall=5000)
    store.append([e1])
    store.append([e1])                        # 重复 append(重启+同步可能重写)
    log2 = store.open_log("dev-a")
    assert len(log2) == 1                       # merge 按 event_id 去重
    # 本地时钟推进到已加载事件之后:新 append 的 HLC 晚于 e1
    e2 = log2.append("trace", {}, wall=4000)    # 即使墙钟倒退,仍单调晚于 e1
    assert e2.hlc > e1.hlc


def test_bad_line_skipped_not_blocking(tmp_path):
    store = MeshLogStore(tmp_path)
    log = MeshLog("a")
    good = log.append("trace", {"x": 1}, wall=1000)
    store.append([good])
    # 手工塞一行坏 JSON(半写/损坏)
    with store.path.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    evs = store.load_events()
    assert [e.event_id for e in evs] == [good.event_id]   # 坏行跳过,好事件照读


def test_persist_new_only_writes_delta(tmp_path):
    store = MeshLogStore(tmp_path)
    log = MeshLog("a")
    log.append("trace", {}, wall=1000)
    assert store.persist_new(log) == 1          # 首次:1 条新
    assert store.persist_new(log) == 0          # 无新事件 → 不重写
    log.append("trace", {}, wall=1001)
    assert store.persist_new(log) == 1          # 只补新的那条


def test_empty_store_loads_empty(tmp_path):
    log = MeshLogStore(tmp_path).open_log("a")
    assert len(log) == 0                         # 无盘文件 → 空 log,不炸
