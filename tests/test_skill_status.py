"""test_skill_status — 技能生命周期状态(btw-1)+ 跑通升级。"""
from __future__ import annotations
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.console.routes import _skill_status  # noqa: E402
from karvyloop.registry.skill_exec import mark_skill_verified  # noqa: E402


def test_status_pending_for_self_written():
    assert _skill_status("---\nname: a\ndescription: d\nsignature: s\n---\n# a\n") == "pending"


def test_status_unverified_for_third_party():
    assert _skill_status("---\nname: a\ndescription: d\nsource: third-party\ntrust: untrusted\n---\n# a\n") == "unverified"


def test_status_crystallized_with_verify_proof():
    assert _skill_status("---\nname: a\nverify_proof: {passed_at: 1}\n---\n# a\n") == "crystallized"


def test_status_crystallized_after_verified_at():
    assert _skill_status("---\nname: a\nsource: third-party\nverified_at: 123\n---\n# a\n") == "crystallized"


def test_mark_skill_verified_flips_status(tmp_path):
    d = tmp_path / "demo"; d.mkdir()
    (d / "SKILL.md").write_text("---\nname: demo\ndescription: d\nsource: third-party\ntrust: untrusted\nsignature: imp\n---\n# d\n", encoding="utf-8")
    body0 = (d / "SKILL.md").read_text(encoding="utf-8")
    assert _skill_status(body0) == "unverified"
    assert mark_skill_verified(str(d)) is True
    body1 = (d / "SKILL.md").read_text(encoding="utf-8")
    assert "verified_at" in body1 and _skill_status(body1) == "crystallized"
    assert mark_skill_verified(str(d)) is False   # 已标过不重复
