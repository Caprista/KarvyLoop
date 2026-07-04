"""test_skill_net_grant — 第三方技能按需授网(P1):默认拒,用户显式授才放。"""
from __future__ import annotations
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.capability.skill_grants import capability_for_skill, token_for_skill  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter  # noqa: E402
from karvyloop.registry.skill_user_grants import SkillUserGrants  # noqa: E402
from karvyloop.sandbox.mounts import has_net  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.security   # 安全套件:第三方技能默认拒网,需用户显式授权


def _third_party(tmp):
    d = tmp / "sk" / "demo"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\nallowed-tools:\n  - WebFetch\n"
        "source: third-party\ntrust: untrusted\nsignature: imp\n---\n# d\n", encoding="utf-8")
    return d


def test_untrusted_default_no_net_but_user_grant_opens_it(tmp_path):
    d = _third_party(tmp_path)
    fm, _ = parse_frontmatter(d / "SKILL.md")
    base = capability_for_skill(fm.allowed_tools, skill_dir=str(d), workspace=str(tmp_path / "ws"), trusted=False)
    granted = capability_for_skill(fm.allowed_tools, skill_dir=str(d), workspace=str(tmp_path / "ws"), trusted=False, net=True)
    from karvyloop.capability.token import mint
    assert has_net(mint("t", base)) is False        # 默认:第三方拒网
    assert has_net(mint("t", granted)) is True       # 用户授网 → 放开


def test_user_grants_store_roundtrip_and_default_deny(tmp_path):
    g = SkillUserGrants(tmp_path / "skill_grants.json")
    assert g.net_granted("demo") is False            # 默认拒
    g.set_net("demo", True)
    assert SkillUserGrants(tmp_path / "skill_grants.json").net_granted("demo") is True  # 落盘
    g.set_net("demo", False)
    assert g.net_granted("demo") is False             # 收回


def test_corrupt_grants_file_defaults_deny(tmp_path):
    p = tmp_path / "skill_grants.json"; p.write_text("{garbage", encoding="utf-8")
    assert SkillUserGrants(p).net_granted("demo") is False   # 坏文件 → fail-safe 拒
