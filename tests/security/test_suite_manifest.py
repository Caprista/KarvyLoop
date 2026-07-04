"""test_suite_manifest — 守住"安全套件"这件事本身:防有人悄悄摘掉标记 / 忘了打标。

这不是攻防测试,是**元测试**:确保 `pytest -m security` 始终收得到我们声称覆盖的每一类
攻击向量(见 tests/security/README.md 目录)。任何被列入清单的安全模块若丢了
`pytestmark = pytest.mark.security`,这里立刻红。
"""
from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.security

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

# 清单:必须被 security 标记收进来的模块(README 目录里的每一类都在此)。
# 键 = 相对 tests/ 的路径;值 = 该模块覆盖的攻击类(给人看,便于对账 README)。
SECURITY_MODULES = {
    "security/test_ssrf.py": "SSRF floor (metadata/loopback/private/scheme/cred/redirect)",
    "test_deontic_gate.py": "domain deontic hard-gate bypass",
    "test_sandbox.py": "Linux bubblewrap sandbox escape",
    "test_win_sandbox.py": "Windows Tier3 sandbox escape",
    "test_seatbelt_profile.py": "macOS seatbelt fail-closed",
    "test_relay.py": "relay replay/tamper/leak/pairing",
    "test_mcp_remote.py": "MCP injection-as-data / credential leak",
    "test_silence.py": "earned-silence bypass",
    "test_fs_grants.py": "sensitive-path floor",
    "test_readonly_token.py": "read-only checker hardening",
    "test_skill_net_grant.py": "third-party skill default-no-net",
    "test_capability_web_mcp.py": "capability least-privilege (web/mcp)",
}


def test_all_listed_modules_exist():
    """清单里的模块都真在磁盘上(防重命名后清单腐烂)。"""
    missing = [m for m in SECURITY_MODULES if not (TESTS / m).exists()]
    assert not missing, f"security 清单指向不存在的模块: {missing}"


def test_all_listed_modules_carry_security_marker():
    """每个清单模块都必须带 `pytestmark = pytest.mark.security`(源码级断言,不依赖收集顺序)。"""
    unmarked = []
    for m in SECURITY_MODULES:
        src = (TESTS / m).read_text(encoding="utf-8")
        if "pytestmark = pytest.mark.security" not in src:
            unmarked.append(m)
    assert not unmarked, (
        "以下安全模块丢了 security 标记(pytest -m security 会漏掉它们): " + str(unmarked))


def test_security_marker_registered_in_pyproject():
    """marker 必须在 pyproject 注册(否则 --strict-markers 下会报未知 marker)。"""
    pp = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "security:" in pp, "pyproject.toml 未注册 security marker"


def test_manifest_matches_readme_catalog():
    """README 目录必须引用清单里的每一个模块(防清单/文档漂移)。"""
    readme = (pathlib.Path(__file__).parent / "README.md").read_text(encoding="utf-8")
    missing = [m for m in SECURITY_MODULES
               if pathlib.Path(m).name not in readme]
    assert not missing, f"README 目录漏了这些安全模块的引用: {missing}"
