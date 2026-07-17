"""test_belief_consolidate — Bug2:知识库和解/合并(近重复聚类 → H2A suggest+apply)。

锁:① parse 宁空勿毒(越界/不足2/跨簇复用/空 merged/非JSON 全丢)② apply 先写合并条、再删被并旧条、不动无关。
"""
from __future__ import annotations

from karvyloop.cognition.consolidate import parse_belief_clusters, apply_belief_merge
from karvyloop.cognition.memory import MemoryManager
from karvyloop.schemas.cognition import Belief


def _b(content, title="", ts=1.0):
    return Belief(content=content, freshness_ts=ts, scope="personal",
                  provenance={"source": "fed", "ts": ts, "title": title, "kind": "knowledge"})


# ---- parse_belief_clusters 宁空勿毒 ----
def test_parse_valid():
    out = parse_belief_clusters(
        '{"clusters":[{"member_indices":[0,1],"merged_title":"T","merged_content":"merged","reason":"same"}]}', 3)
    assert len(out) == 1 and out[0]["merged_content"] == "merged" and out[0]["member_indices"] == [0, 1]


def test_parse_drops_out_of_range_leaving_lt2():
    assert parse_belief_clusters('{"clusters":[{"member_indices":[0,5],"merged_content":"x"}]}', 3) == []


def test_parse_drops_cross_cluster_reuse():
    # 第一簇用了 0、1;第二簇 [0,2] 里 0 已用 → 去掉 → [2] < 2 → 丢
    out = parse_belief_clusters(
        '{"clusters":[{"member_indices":[0,1],"merged_content":"a"},{"member_indices":[0,2],"merged_content":"b"}]}', 3)
    assert len(out) == 1 and out[0]["merged_content"] == "a"


def test_parse_drops_empty_merged_and_non_json():
    assert parse_belief_clusters('{"clusters":[{"member_indices":[0,1],"merged_content":""}]}', 3) == []
    assert parse_belief_clusters("not json at all", 3) == []
    assert parse_belief_clusters("```json\n[]\n```", 3) == []


# ---- apply_belief_merge:先写合并条、再删被并旧条 ----
def test_apply_writes_merged_removes_members_keeps_others():
    mem = MemoryManager()
    mem.write(_b("A: loop 是自运转", "loopA"))
    mem.write(_b("B: loop 无人参与", "loopB"))
    mem.write(_b("C: 一条无关知识", "other"))
    res = apply_belief_merge(["A: loop 是自运转", "B: loop 无人参与"], "loop 自运转、无人参与",
                             merged_title="loop 工程", mem=mem)
    assert res["ok"] is True and res["removed"] == 2
    contents = {b.content for b in mem.index.all("personal")}
    assert "loop 自运转、无人参与" in contents          # 合并条写入
    assert "A: loop 是自运转" not in contents and "B: loop 无人参与" not in contents  # 被并旧条删
    assert "C: 一条无关知识" in contents                # 无关的不动


def test_apply_rejects_when_fewer_than_two_present():
    mem = MemoryManager()
    mem.write(_b("only one真实"))
    res = apply_belief_merge(["only one真实", "根本不存在的一条"], "merged", mem=mem)
    assert res["ok"] is False                          # 真实成员 < 2 → 不动
    assert {b.content for b in mem.index.all("personal")} == {"only one真实"}


# ---- P1c:失效成员不复活、不毁墓碑 ----
def test_apply_skips_dead_member_preserves_tombstone():
    # 卡素材里某成员在 ACCEPT 前已被 supersede 打 invalid → 死版**不并、不删**(墓碑保留,不复活)
    mem = MemoryManager()
    mem.write(_b("活条一:loop 自运转"))
    mem.write(_b("活条二:loop 无人参与"))
    mem.write(_b("死条:旧说法"))
    mem.invalidate(mem.index.get("死条:旧说法"), reason="superseded", force=True)   # 变成墓碑
    res = apply_belief_merge(["活条一:loop 自运转", "活条二:loop 无人参与", "死条:旧说法"],
                             "loop 自运转、无人参与", mem=mem)
    assert res["ok"] is True and res["removed"] == 2 and res.get("dead_skipped") == 1
    tomb = mem.index.get("死条:旧说法")
    assert tomb is not None and tomb.invalid_at is not None   # 墓碑仍在(失效不删的审计层没被物理删)


def test_apply_rejects_when_only_one_live_member():
    # 三成员但两个已死 → 活成员 < 2 → 不动(不写合并条、不删活条、墓碑都保留)
    mem = MemoryManager()
    mem.write(_b("唯一活条"))
    mem.write(_b("死一"))
    mem.write(_b("死二"))
    mem.invalidate(mem.index.get("死一"), reason="x", force=True)
    mem.invalidate(mem.index.get("死二"), reason="x", force=True)
    res = apply_belief_merge(["唯一活条", "死一", "死二"], "merged", mem=mem)
    assert res["ok"] is False and res.get("dead_skipped") == 2
    assert mem.index.get("死一") is not None and mem.index.get("死二") is not None
    assert mem.index.get("唯一活条") is not None
    assert {b.content for b in mem.index.all("personal")} == {"唯一活条", "死一", "死二"}


def test_apply_rejects_empty_merged():
    mem = MemoryManager()
    mem.write(_b("x")); mem.write(_b("y"))
    assert apply_belief_merge(["x", "y"], "  ", mem=mem)["ok"] is False
