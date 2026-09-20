from karvyloop.channels.dingtalk_runtime import ApprovalSnapshotStore


def test_snapshot_resolves_and_skips_completed_indexes():
    store = ApprovalSnapshotStore(ttl_s=60)
    item = store.put("oa", "chat", {1: "r1", 2: "r2"}, now=100)
    assert item["status"] == "OK"
    assert store.resolve("oa", "chat", (1, 2), now=101) == ({1: "r1", 2: "r2"}, "OK")
    store.mark("oa", "chat", completed=(1,))
    assert store.resolve("oa", "chat", (1, 2), now=102) == ({2: "r2"}, "OK")


def test_snapshot_rejects_unknown_and_expired_indexes():
    store = ApprovalSnapshotStore(ttl_s=10)
    store.put("oa", "chat", {1: "r1"}, now=100)
    assert store.resolve("oa", "chat", (2,), now=101) == ({}, "UNKNOWN_INDEX")
    assert store.resolve("oa", "chat", (1,), now=111) == ({}, "EXPIRED")
