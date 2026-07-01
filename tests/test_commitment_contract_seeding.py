"""docs/02 §15.1.5 — Code ②a:尽责下属协作契约 seed 进三条 role 起源入口。

不变量:① 一份规范默认(单一真理源)② seed 进每个 role 的 COMMITMENT(可见可编)
③ 三入口都带(系统默认创建 / 外部 agent 导入[LLM 走 RoleRegistry.create / v0 走 planner])
④ 资产打包丢了也不崩 role 创建(宁缺毋崩,回退内联兜底)。
"""

from __future__ import annotations

from pathlib import Path

from karvyloop.paradigm.contract import (
    default_commitment_contract,
    seed_commitment_md,
    system_contracts_dir,
)


# 契约里几句"指纹",用来证明某文件确实 seed 了它(而非空 stub)。
_FINGERPRINTS = ("resourceful subordinate", "Exhaust your own resourcefulness", "bring evidence")


def _has_contract(text: str) -> bool:
    return all(fp in text for fp in _FINGERPRINTS)


# ============ 规范默认:存在、可读、非空 ============

def test_default_contract_asset_ships_and_loads():
    p = system_contracts_dir() / "resourceful_subordinate" / "DEFAULT_COMMITMENT.md"
    assert p.exists(), "默认契约必须随包发版(package-data 已声明)"
    text = default_commitment_contract()
    assert _has_contract(text)
    # 守"地板不在模板里"——契约只装性情,不许把可覆盖的预算数值写死进来误导
    assert "ladder" not in text.lower() or "Climb the ladder" in text  # 阶梯是文字描述,非机器旋钮


def test_seed_commitment_md_has_contract_and_editable_section():
    md = seed_commitment_md()
    assert md.startswith("# COMMITMENT")
    assert _has_contract(md)
    # 留给本 role 的可编辑区(范式可见可编)
    assert "This role's own commitments" in md


# ============ 入口 A + B(LLM):RoleRegistry.create seed ============

def test_origination_default_create_seeds_contract(tmp_path: Path):
    from karvyloop.roles.registry import RoleRegistry

    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("analyst", identity="你是分析师")
    commitment = (Path(v.path) / "COMMITMENT.md").read_text(encoding="utf-8")
    assert _has_contract(commitment), "系统默认创建的 role 必须 seed 尽责契约"
    # 不该再是旧空 stub
    assert "(待充实)" not in commitment.split("This role's own commitments")[0]


def test_origination_llm_import_seeds_contract(tmp_path: Path):
    """LLM 导入路径落 role 也走 RoleRegistry.create → 同一份 seed。"""
    from karvyloop.roles.registry import RoleRegistry

    reg = RoleRegistry(tmp_path / "roles")
    # 模拟 routes.py 导入成功分支:reg.create(rid, identity=decomp.identity, soul=...)
    v = reg.create("imported_geographer", identity="地理学家", soul="严谨求证")
    commitment = (Path(v.path) / "COMMITMENT.md").read_text(encoding="utf-8")
    assert _has_contract(commitment)


# ============ 入口 B(v0 确定性导入):planner.synth_commitment seed ============

def test_origination_v0_import_seeds_contract():
    from karvyloop.adapter.planner import synth_commitment

    out = synth_commitment("claude-research-agent", "/tmp/agent.md")
    assert _has_contract(out), "v0 导入路径也必须 seed 同一份契约(不能只拷人设)"
    # 来源说明仍保留(契约不顶掉原有的导入溯源)
    assert "claude-research-agent" in out


def test_v0_and_registry_seed_same_canonical_contract():
    """守单一真理源:两条入口 seed 的契约正文一致(不漂移)。"""
    from karvyloop.adapter.planner import synth_commitment

    canonical = default_commitment_contract()
    assert canonical in seed_commitment_md()
    assert canonical in synth_commitment("x", "/y")


# ============ 资产丢失安全:打包漏 system_contracts 也不崩 ============

def test_missing_asset_falls_back_not_crash(monkeypatch):
    """wheel 漏打包 / 文件损坏 → 回退内联兜底,role 创建不崩(宁缺毋崩)。"""
    import karvyloop.paradigm.contract as contract_mod

    default_commitment_contract.cache_clear()
    monkeypatch.setattr(contract_mod, "system_contracts_dir",
                        lambda: Path("/nonexistent/does/not/exist"))
    text = default_commitment_contract()
    assert "resourceful subordinate" in text  # 兜底仍传达核心 disposition
    assert text == contract_mod._FALLBACK_CONTRACT
    default_commitment_contract.cache_clear()  # 不污染其它测试
