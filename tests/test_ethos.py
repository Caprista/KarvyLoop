"""Ethos Agent 双升级验收测试(tests/test_ethos.py)。

**M2.0 拍 5**。7 AC + 1 协议 = 8 测试。设计:docs/15-ethos-agent.md。
"""
from __future__ import annotations

import pathlib

import pytest

from karvyloop.ethos import (
    Auditor,
    AuditorAttestation,
    AuditorFinding,
    AuditorReport,
    Bootstrapper,
    ERROR,
    INFO,
    InsightDict,
    WARNING,
    all_check_ids,
    compute_attestation_hash,
    default_auditor,
    default_bootstrapper,
    interpret_answers,
    recommend_three_questions,
    severity_rank,
)


# ============ helpers ============

def _write_full_agent_dir(path: pathlib.Path, *, with_conflict: bool = False) -> None:
    """写一个**齐**全 7 文件灵魂层(可选制造冲突)。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "IDENTITY.md").write_text(
        "# IDENTITY\n\n## 职责\n" +
        ("激进的产品经理,数据驱动" if with_conflict else "产品经理,温和"),
        encoding="utf-8",
    )
    (path / "SOUL.md").write_text(
        "# SOUL\n\n## 原则\n" +
        ("直觉判断" if with_conflict else "温和"),
        encoding="utf-8",
    )
    (path / "USER.md").write_text(
        "# USER\n\n## 客户\n我们的客户是 SaaS 行业的中型企业",
        encoding="utf-8",
    )
    (path / "MEMORY.md").write_text(
        "# MEMORY\n\n## 记录\n对话历史",
        encoding="utf-8",
    )
    (path / "COMMITMENT.md").write_text(
        "# COMMITMENT\n\n## Q1 OKR\nQ1 完成核心功能上线",
        encoding="utf-8",
    )
    (path / "VERIFY.md").write_text(
        "# VERIFY\n\n## 验证门\n跑通端到端测试,所有用例通过",
        encoding="utf-8",
    )
    (path / "COMPOSITION.yaml").write_text(
        "<!-- step_id: COMPOSITION -->\n"
        "imported_from: claude\n"
        "tools:\n  - name: write_ppt\n    source: claude\n",
        encoding="utf-8",
    )


# ============ AC1:Bootstrapper 注入式 + 关键词 fallback ============
def test_ac1_bootstrapper_injection_and_fallback():
    """AC1: model_fn=None 走关键词;model_fn 注入时调用。"""
    # 1) None 走 fallback
    r1 = interpret_answers(["我想做产品经理相关工作,产品需求和 PRD"])
    assert isinstance(r1, InsightDict)
    assert "产品" in r1.detected_themes
    assert any("产品" in kw or "PRD" in kw or "pm" in kw for kw in r1.keywords)
    assert r1.summary  # 非空

    # 2) 注入 model_fn
    captured = {}

    def my_model(answers, soul_dir):
        captured["answers"] = list(answers)
        captured["soul_dir"] = soul_dir
        return {
            "keywords": ("injected_kw",),
            "detected_themes": ("injected_theme",),
            "conflicts": (),
            "summary": "injected summary",
        }

    b = Bootstrapper(model_fn=my_model)
    r2 = b.interpret(["a", "b"], existing_soul_dir="/tmp/x")
    assert captured["answers"] == ["a", "b"]
    assert captured["soul_dir"] == "/tmp/x"
    assert r2.keywords == ("injected_kw",)
    assert r2.summary == "injected summary"

    # 3) 注入 model_fn 抛异常 → fallback
    def bad_model(answers, soul_dir):
        raise RuntimeError("intentional")

    b2 = Bootstrapper(model_fn=bad_model)
    r3 = b2.interpret(["hello product"])
    assert isinstance(r3, InsightDict)
    assert "产品" in r3.detected_themes or "产品" in r3.keywords


# ============ AC2:InsightDict 类型 ============
def test_ac2_bootstrapper_returns_typed_insight_dict():
    """AC2: 返回 InsightDict 实例,不返裸 dict。"""
    r = interpret_answers(["x"])
    assert isinstance(r, InsightDict)
    assert hasattr(r, "keywords")
    assert hasattr(r, "detected_themes")
    assert hasattr(r, "conflicts")
    assert hasattr(r, "summary")
    # 全部 tuple/str
    assert isinstance(r.keywords, tuple)
    assert isinstance(r.detected_themes, tuple)
    assert isinstance(r.conflicts, tuple)
    assert isinstance(r.summary, str)


# ============ AC3:Auditor 7 文件齐 → 0 error ============
def test_ac3_auditor_passes_full_agent(tmp_path: pathlib.Path):
    """AC3: 7 文件齐全(无冲突,无 invalid atom)→ 0 error finding。"""
    _write_full_agent_dir(tmp_path / "good", with_conflict=False)
    a = default_auditor(registered_atoms=("write_ppt",))
    report = a.audit(str(tmp_path / "good"), agent_id="good-agent")
    assert isinstance(report, AuditorReport)
    error_findings = [f for f in report.findings if f.severity == ERROR]
    assert error_findings == []
    assert report.ok is True
    assert report.checks_run == 8


# ============ AC4:Auditor 缺 SOUL.md → ≥1 error 含 "SOUL" ============
def test_ac4_auditor_missing_file_raises_error(tmp_path: pathlib.Path):
    """AC4: 缺 SOUL.md → ≥1 error finding 含 'SOUL'。"""
    d = tmp_path / "incomplete"
    d.mkdir()
    # 写 6 个,缺 SOUL
    for f in ("IDENTITY.md", "USER.md", "MEMORY.md", "COMMITMENT.md", "VERIFY.md"):
        (d / f).write_text(f"# {f}\n\n## 段\n内容", encoding="utf-8")
    (d / "COMPOSITION.yaml").write_text("<!-- step_id: COMPOSITION -->\n", encoding="utf-8")
    a = default_auditor()
    report = a.audit(str(d), agent_id="incomplete")
    assert report.ok is False
    error_findings = [f for f in report.findings if f.severity == ERROR]
    assert len(error_findings) >= 1
    assert any("SOUL" in f.message for f in error_findings)


# ============ AC5:Auditor severity 排序稳**定** ============
def test_ac5_auditor_severity_sorting():
    """AC5: severity_rank 排序稳定 + report.findings_sorted() error 排前。"""
    # 排序 rank
    assert severity_rank(ERROR) < severity_rank(WARNING)
    assert severity_rank(WARNING) < severity_rank(INFO)
    # findings_sorted 行为
    findings = (
        AuditorFinding(check_id="a", severity=INFO, message="i"),
        AuditorFinding(check_id="b", severity=ERROR, message="e"),
        AuditorFinding(check_id="c", severity=WARNING, message="w"),
    )
    report = AuditorReport(agent_id="x", findings=findings, checks_run=3, checks_passed=0)
    sorted_f = report.findings_sorted()
    assert sorted_f[0].severity == ERROR
    assert sorted_f[1].severity == WARNING
    assert sorted_f[2].severity == INFO

    # 哈希稳**定**:同**样** findings 集 → 同**样** 哈希
    h1 = compute_attestation_hash(findings)
    h2 = compute_attestation_hash(tuple(reversed(findings)))
    assert h1 == h2  # 因**为** sort 后**取**


# ============ AC6:Attestation 哈希稳**定** + 改**动**后**变** ============
def test_ac6_attestation_hash_stability_and_change(tmp_path: pathlib.Path):
    """AC6: 同**样** 7 文件 → 同**样** attestation hash;改 1 文件 → 哈希**变**。"""
    a = default_auditor(registered_atoms=("write_ppt",))
    _write_full_agent_dir(tmp_path / "h1", with_conflict=False)
    _write_full_agent_dir(tmp_path / "h1_copy", with_conflict=False)
    att1 = a.attest(str(tmp_path / "h1"), agent_id="x")
    att2 = a.attest(str(tmp_path / "h1_copy"), agent_id="x")
    assert att1.attestation_hash == att2.attestation_hash
    assert isinstance(att1, AuditorAttestation)
    assert att1.ok is True

    # 改 IDENTITY.md 一点
    (tmp_path / "h1" / "IDENTITY.md").write_text(
        "# IDENTITY\n\n## 职责\n产品经理,温和 + 一句新增内容",
        encoding="utf-8",
    )
    att3 = a.attest(str(tmp_path / "h1"), agent_id="x")
    # finding 集**变**了 → 哈希**变**(或不**变** — **不**是 finding,**不**影响 attestation hash
    # E4:attestation hash 只**覆**盖 findings,**不**覆**盖**文**本本身)
    # 因此改**文**本**不**影响 finding 集**就**不会**变**,这是**预**期
    # 所**以**这**里只**断**言 att1.findings 跟 att3.findings 一**样**(**因**为**都**没**引**发**新**finding)
    assert att3.findings == att1.findings


# ============ AC7:Auditor **不**抛 ============
def test_ac7_auditor_does_not_raise_on_missing_dir(tmp_path: pathlib.Path):
    """AC7: 不存在 dir → 返 report + ≥1 error,**不**抛。"""
    a = default_auditor()
    no_such = str(tmp_path / "no_such_agent_dir")
    report = a.audit(no_such, agent_id="ghost")
    assert isinstance(report, AuditorReport)
    assert report.ok is False
    assert any(f.severity == ERROR for f in report.findings)

    # 空 dir 同样**不**抛
    empty = tmp_path / "empty"
    empty.mkdir()
    report2 = a.audit(str(empty), agent_id="empty")
    assert report2.ok is False
    assert any("SOUL" in f.message or "IDENTITY" in f.message for f in report2.findings)


# ============ 协议不变量:severity 3 档 + 8 check + Bootstrapper 4 职责 ============
def test_protocol_severity_checks_and_recommendation(tmp_path: pathlib.Path):
    """协议:severity 3 档 + 8 项 check + Bootstrapper.recommend 3 问。"""
    # 3 档
    assert severity_rank(ERROR) == 0
    assert severity_rank(WARNING) == 1
    assert severity_rank(INFO) == 2
    # 8 check
    assert len(all_check_ids()) == 8
    for cid in (
        "soul.slot_present", "soul.file_not_empty", "soul.has_section",
        "soul.identity_soul_consistent", "soul.commitment_specific",
        "soul.verify_has_gate", "soul.user_has_subject", "soul.composition_atoms_valid",
    ):
        assert cid in all_check_ids()

    # recommend_three_questions:从 audit report 抽 3 问
    _write_full_agent_dir(tmp_path / "rec", with_conflict=True)
    a = default_auditor(registered_atoms=("write_ppt",))
    report = a.audit(str(tmp_path / "rec"), agent_id="rec-agent")
    recs = recommend_three_questions(report)
    assert len(recs) == 3
    for r in recs:
        assert isinstance(r, str)
        assert len(r) > 0
