"""Syntonos 行为审计验收测试(tests/test_syntonos.py)。

**M2.0 拍 6**。7 AC + 1 协议 = 8 测试。设计:docs/16-syntonos.md。
"""
from __future__ import annotations

import pathlib

import pytest

from karvyloop.ethos import (
    AuditorAttestation,
    AuditorFinding,
    AuditorReport,
    default_auditor,
    ERROR as ETHOS_ERROR,
    WARNING as ETHOS_WARNING,
)
from karvyloop.syntonos import (
    BehaviorEvent,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_SIZE,
    DriftConfig,
    DriftReport,
    EVENT_TYPES,
    SYNTONOS_CHECK_IDS,
    SyntonosBaseline,
    SyntonosFinding,
    SyntonosReport,
    build_baseline_from_attestation,
    default_drift_config,
    detect_drift,
    reconcile,
)


# ============ helpers ============

def _write_full_agent_dir(path: pathlib.Path) -> None:
    """写一个**齐**全 7 文件灵魂层(给 Auditor 用)。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "IDENTITY.md").write_text(
        "# IDENTITY\n\n## 职责\n产品经理", encoding="utf-8"
    )
    (path / "SOUL.md").write_text(
        "# SOUL\n\n## 原则\n数据驱动", encoding="utf-8"
    )
    (path / "USER.md").write_text(
        "# USER\n\n## 客户\nSaaS 行业客户", encoding="utf-8"
    )
    (path / "MEMORY.md").write_text(
        "# MEMORY\n\n## 记录\n对话历史", encoding="utf-8"
    )
    (path / "COMMITMENT.md").write_text(
        "# COMMITMENT\n\n## Q1 OKR\nQ1 上线", encoding="utf-8"
    )
    (path / "VERIFY.md").write_text(
        "# VERIFY\n\n## 验证门\n跑通端到端测试", encoding="utf-8"
    )
    (path / "COMPOSITION.yaml").write_text(
        "<!-- step_id: COMPOSITION -->\nimported_from: claude\n", encoding="utf-8"
    )


def _make_attestation(tmp_path: pathlib.Path) -> AuditorAttestation:
    """从**齐**全 7 文件**生** Attestation。"""
    agent_dir = tmp_path / "agent"
    _write_full_agent_dir(agent_dir)
    a = default_auditor(registered_atoms=())
    return a.attest(str(agent_dir), agent_id="syntonos-test")


def _mk_event(t: str, agent_id: str = "syntonos-test", **payload) -> BehaviorEvent:
    return BehaviorEvent(
        timestamp="2026-06-16T10:00:00Z",
        agent_id=agent_id,
        event_type=t,
        payload=payload,
    )


# ============ AC1:Baseline 构造 ==============
def test_ac1_baseline_construction_requires_attestation(tmp_path: pathlib.Path):
    """AC1: SyntonosBaseline 必**须**含 attestation。"""
    att = _make_attestation(tmp_path)
    baseline = build_baseline_from_attestation(att)
    assert isinstance(baseline, SyntonosBaseline)
    assert baseline.attestation is att
    assert baseline.baseline_hash == att.attestation_hash
    # 默认 5 组灵魂层关键词
    assert "SOUL" in baseline.soul_keywords
    assert "IDENTITY" in baseline.soul_keywords
    # 全**部**预**期**关**键**词**展**平**
    all_kws = baseline.all_expected_keywords()
    assert "数据驱动" in all_kws
    assert "产品经理" in all_kws


# ============ AC2:Reconcile missing ============
def test_ac2_reconcile_missing_keyword():
    """AC2: 基**准**线**有**关**键**词 '数据驱动' 但**行**为**中** **没**出**现** → 1 个 warning finding。"""
    att_hash = "deadbeef"
    # 简**化** attestation(只**需** hash)
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash=att_hash, findings=(), ok=True,
    )
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("数据驱动", "温和")},
    )
    # 行**为**中**只**出**现** "温和",**没** "数据驱动"
    events = [_mk_event("keyword_spoken", keyword="温和")]
    report = reconcile(baseline, events)
    assert isinstance(report, SyntonosReport)
    assert report.ok is True   # missing 走 warning,**不**是 error
    missing = [f for f in report.findings if f.check_id == "syntonos.missing"]
    assert len(missing) == 1
    assert missing[0].baseline_keyword == "数据驱动"
    assert missing[0].severity == "warning"
    assert report.baseline_hash == att_hash
    assert report.events_examined == 1


# ============ AC3:Reconcile forbidden ============
def test_ac3_reconcile_forbidden_keyword():
    """AC3: 基**准**线**禁**忌 '激**进**' 但**行**为**出**现** → 1 个 error finding。"""
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="cafebabe", findings=(), ok=True,
    )
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("数据驱动",)},
        forbidden_keywords=("激进",),
    )
    events = [_mk_event("keyword_spoken", keyword="激进")]
    report = reconcile(baseline, events)
    assert report.ok is False   # forbidden 走 error
    forbidden = [f for f in report.findings if f.check_id == "syntonos.forbidden"]
    assert len(forbidden) == 1
    assert forbidden[0].severity == "error"
    assert forbidden[0].baseline_keyword == "激进"


# ============ AC4:Reconcile extra ============
def test_ac4_reconcile_extra_keyword():
    """AC4: 行**为**有 X 基**准**线**没**有**预**期** → 1 个 info finding。"""
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="feedface", findings=(), ok=True,
    )
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("数据驱动",)},
    )
    # 行**为**出**现** "**随**机" 关**键**词(基**准**线**没**有)
    events = [_mk_event("keyword_spoken", keyword="随机闲聊")]
    report = reconcile(baseline, events)
    assert report.ok is True   # extra 走 info
    extras = [f for f in report.findings if f.check_id == "syntonos.extra"]
    assert len(extras) == 1
    assert extras[0].severity == "info"
    assert extras[0].baseline_keyword == "随机闲聊"


# ============ AC5:**不**抛(空 events)============
def test_ac5_reconcile_does_not_raise_on_empty_events():
    """AC5: events=() → 0 finding(**因** baseline **也**空,**所**以** **不**会 missing),ok=True(**不**抛)。"""
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="abc12345", findings=(), ok=True,
    )
    # baseline **关**键**词**空,events **也**空 → 0 finding
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ()},
    )
    report = reconcile(baseline, ())
    assert isinstance(report, SyntonosReport)
    assert report.ok is True
    assert report.findings == ()
    assert report.events_examined == 0


# ============ AC6:偏离检测 ============
def test_ac6_drift_detection_threshold():
    """AC6: missing+forbidden ≥ 阈**值** → is_drifting=True;**未**达 → False。"""
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="11112222", findings=(), ok=True,
    )
    # 基**准**线**有** 4 个**预**期**关**键**词,1 个**禁**忌
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("a", "b", "c", "d")},
        forbidden_keywords=("forbidden1",),
    )
    # 构**造** 10 条**事**件,5 条 missing/1 forbidden = 6/10 = 0.6 ≥ 0.3 → 偏**离**
    events = []
    for i in range(5):
        events.append(_mk_event("keyword_spoken", keyword=f"triggered_{i}"))   # 命中 a/b/c/d?不会,**都**是 extra
    # 修正:让 5 条 missing + 1 forbidden,total 10
    # baseline 4 expected + 1 forbidden;**观**察**只**有 1 forbidden + 5 extra
    # missing = 4(预**期**都**没**出现),forbidden = 1,total = 6 → 5/6 ≈ 0.83 ≥ 0.3
    report = detect_drift(events, baseline, config=DriftConfig(window_size=100, threshold=0.3))
    assert isinstance(report, DriftReport)
    assert report.is_drifting is True
    # 4 expected keywords (a/b/c/d) + 1 forbidden (forbidden1)
    # observed: 0 expected + 0 forbidden (没**有**触发任**何** expected keyword,**只**有 random + forbidden)
    # missing = 4 (a/b/c/d **都**没**出现**),forbidden = 0 (**没**有**禁**忌**关**键**词**进 events),extra = 5
    # 但**因**为**我的**事件**只** 5 个 random,**没**有**禁**忌**关**键**词**,so total = 5
    # ratio = 4/5 = 0.8 ≥ 0.3 → 偏**离**
    assert report.missing_count == 4
    assert report.forbidden_count == 0
    assert report.extra_count == 5
    assert report.total_events == 5
    assert abs(report.drift_ratio - 4 / 5) < 1e-9  # missing 4,total 5,**不**含 extra

    # 低偏离:all expected 都出**现** + **没**有 forbidden
    baseline2 = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("x",)},
    )
    events2 = [_mk_event("keyword_spoken", keyword="x")]
    report2 = detect_drift(events2, baseline2)
    assert report2.is_drifting is False
    assert report2.drift_ratio == 0.0


# ============ AC7:baseline_hash 一**致** ============
def test_ac7_baseline_hash_consistency():
    """AC7: 同一**个** Attestation → 同**一**个** baseline_hash;**改** Attestation → 变。"""
    att1 = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="aaaa1111", findings=(), ok=True,
    )
    att2 = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="bbbb2222", findings=(), ok=True,
    )
    b1 = build_baseline_from_attestation(att1)
    b2 = build_baseline_from_attestation(att1)   # 同一 attestation
    b3 = build_baseline_from_attestation(att2)   # 不**同** attestation
    assert b1.baseline_hash == b2.baseline_hash == "aaaa1111"
    assert b3.baseline_hash == "bbbb2222"
    assert b1.baseline_hash != b3.baseline_hash


# ============ 协**议**不**变**量:3 类**发**现**名** + 偏**离**率**公**式** + 滑**动**窗**默**认 100 + 阈**值**默**认 0.3 ============
def test_protocol_invariants_check_ids_window_threshold():
    """协**议**不**变**量:3 类**发**现**名** + 偏**离**率**公**式** + 滑**动**窗**默**认 100 + 阈**值**默**认 0.3。"""
    # 3 类**发**现
    assert SYNTONOS_CHECK_IDS == ("syntonos.missing", "syntonos.forbidden", "syntonos.extra")
    # 4 event_type
    assert EVENT_TYPES == ("tool_call", "soul_loaded", "keyword_spoken", "pursuit_created")
    # 滑**动**窗**默**认 100 + 阈**值**默**认 0.3
    cfg = default_drift_config()
    assert cfg.window_size == 100
    assert cfg.threshold == 0.3
    assert DEFAULT_WINDOW_SIZE == 100
    assert DEFAULT_THRESHOLD == 0.3
    # 偏**离**率**公**式**:**只**算 (missing + forbidden) / total,**不**含 extra
    # 用**例**: 5 预**期** + 1 禁**忌**,4 missing,1 forbidden,5 extra,total 6
    # ratio = (4+1)/6 = 5/6
    att = AuditorAttestation(
        agent_id="x", attested_at="2026-06-16T10:00:00Z",
        checks_run=8, checks_passed=8, findings_count=0,
        attestation_hash="9999aaaa", findings=(), ok=True,
    )
    baseline = build_baseline_from_attestation(
        att,
        soul_keywords={"SOUL": ("a", "b", "c", "d", "e")},
        forbidden_keywords=("forbidden1",),
    )
    events = [
        _mk_event("keyword_spoken", keyword="forbidden1"),   # 命中禁**忌** = 1
        _mk_event("keyword_spoken", keyword="random1"),
        _mk_event("keyword_spoken", keyword="random2"),
        _mk_event("keyword_spoken", keyword="random3"),
        _mk_event("keyword_spoken", keyword="random4"),
        _mk_event("keyword_spoken", keyword="random5"),
    ]
    report = detect_drift(events, baseline)
    assert report.total_events == 6
    assert report.missing_count == 5    # a/b/c/d/e 都**没**出**现**
    assert report.forbidden_count == 1  # forbidden1 出**现**
    assert report.extra_count == 5      # random1-5 是 extra
    # ratio = (5+1)/6 = 1.0(extra **不**进**分**子)
    assert abs(report.drift_ratio - 1.0) < 1e-9
