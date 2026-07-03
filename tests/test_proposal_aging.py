"""DEFER 老化(docs/43 ⑤a #6):created_ts / deferred_at 戳 + aging_scan + 持久化兼容。"""
from __future__ import annotations

import json
import time

from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S, PendingProposalRegistry

T0 = 1_800_000_000.0
DAY = 86400.0


def make_proposal(pid: str = "", summary: str = "转给秘书") -> Proposal:
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                    evidence_refs=(), habit_id=0, model_ref="", ts=T0,
                    kind="route_to_role", proposal_id=pid)


class TestAgingStamps:
    def test_register_stamps_created_ts(self):
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal(), now=T0)
        meta = reg.proposal_meta(pid)
        assert meta["created_ts"] == T0 and meta["deferred_at"] is None

    def test_register_default_now_is_wallclock(self):
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal())
        assert abs(reg.proposal_meta(pid)["created_ts"] - time.time()) < 5

    def test_reregister_keeps_original_created_ts(self):
        """幂等收敛卡(同 id 覆盖)不重置挂龄 —— 挂龄从第一次出现算。"""
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal(pid="p-1"), now=T0)
        reg.register(make_proposal(pid="p-1"), now=T0 + 3 * DAY)
        assert reg.proposal_meta(pid)["created_ts"] == T0

    def test_defer_stamps_deferred_at(self):
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal(), now=T0)
        res = reg.decide(pid, "DEFER", now=T0 + 100)
        assert res is not None and res.detail == "deferred"
        assert reg.proposal_meta(pid)["deferred_at"] == T0 + 100
        assert reg.get(pid) is not None  # DEFER 留 registry

    def test_remove_clears_meta(self):
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal(), now=T0)
        reg.remove(pid)
        assert reg.proposal_meta(pid) == {}


class TestAgingScan:
    def test_scan_flags_only_over_threshold(self):
        reg = PendingProposalRegistry()
        reg.register(make_proposal(pid="p-old"), now=T0 - AGING_THRESHOLD_S - 3600)
        reg.register(make_proposal(pid="p-new"), now=T0)
        aged = reg.aging_scan(now=T0)
        assert [a["proposal_id"] for a in aged] == ["p-old"]
        assert aged[0]["age_s"] >= AGING_THRESHOLD_S

    def test_scan_sorted_oldest_first(self):
        reg = PendingProposalRegistry()
        reg.register(make_proposal(pid="p-2d"), now=T0 - 2.5 * DAY)
        reg.register(make_proposal(pid="p-5d"), now=T0 - 5 * DAY)
        assert [a["proposal_id"] for a in reg.aging_scan(now=T0)] == ["p-5d", "p-2d"]

    def test_defer_does_not_reset_age(self):
        """DEFER 只影响 digest 计入节奏,挂龄仍从 created_ts 算(拖着的板就是拖着的板)。"""
        reg = PendingProposalRegistry()
        pid = reg.register(make_proposal(), now=T0 - 3 * DAY)
        reg.decide(pid, "DEFER", now=T0 - 60)
        aged = reg.aging_scan(now=T0)
        assert aged and aged[0]["proposal_id"] == pid
        assert aged[0]["age_s"] >= 3 * DAY - 1
        assert aged[0]["deferred_at"] == T0 - 60

    def test_custom_threshold(self):
        reg = PendingProposalRegistry()
        reg.register(make_proposal(), now=T0 - 3600)
        assert reg.aging_scan(now=T0) == []
        assert len(reg.aging_scan(now=T0, threshold_s=1800)) == 1


class TestPersistenceCompat:
    def test_meta_round_trips(self, tmp_path):
        p = tmp_path / "pending.json"
        reg = PendingProposalRegistry(persist_path=p)
        pid = reg.register(make_proposal(), now=T0)
        reg.decide(pid, "DEFER", now=T0 + 100)

        reloaded = PendingProposalRegistry(persist_path=p)
        meta = reloaded.proposal_meta(pid)
        assert meta["created_ts"] == T0 and meta["deferred_at"] == T0 + 100
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == 2 and pid in data["meta"]

    def test_v1_file_without_meta_stamps_load_time(self, tmp_path):
        """向后兼容:旧 pending.json 无戳 → 按加载时刻记,不误报老龄。"""
        prop = make_proposal(pid="p-legacy")
        p = tmp_path / "pending.json"
        p.write_text(json.dumps({"version": 1, "pending": [prop.to_dict()]},
                                ensure_ascii=False), encoding="utf-8")
        before = time.time()
        reg = PendingProposalRegistry(persist_path=p)
        meta = reg.proposal_meta("p-legacy")
        assert before <= meta["created_ts"] <= time.time()
        assert meta["deferred_at"] is None
        assert reg.aging_scan(now=time.time()) == []  # 刚加载,不算老

    def test_corrupt_meta_fail_safe(self, tmp_path):
        prop = make_proposal(pid="p-1")
        p = tmp_path / "pending.json"
        p.write_text(json.dumps({"version": 2, "pending": [prop.to_dict()],
                                 "meta": {"p-1": {"created_ts": "not-a-number",
                                                  "deferred_at": None}}}), encoding="utf-8")
        reg = PendingProposalRegistry(persist_path=p)
        assert reg.proposal_meta("p-1")["created_ts"] > 0  # 坏戳按加载时刻,不崩

    def test_accept_after_reload_still_dispatches(self, tmp_path):
        """老化不破既有决策路径:重启后 ACCEPT 仍按 kind 兑现并移除。"""
        p = tmp_path / "pending.json"
        reg = PendingProposalRegistry(persist_path=p)
        pid = reg.register(make_proposal(), now=T0)
        reloaded = PendingProposalRegistry(persist_path=p)
        hit = []
        res = reloaded.decide(pid, "ACCEPT",
                              handlers={"route_to_role": lambda pr: (hit.append(pr) or (True, "ok"))})
        assert res.ok and hit and reloaded.get(pid) is None
