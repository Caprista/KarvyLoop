"""test_doctor — 确定性自检(零模型、永不抛)+ 每个 finding code 都有双语文案。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import doctor as D  # noqa: E402


def test_overall_levels():
    mk = lambda lv: D.Finding(lv, "x")
    assert D.overall([mk("ok"), mk("ok")]) == "ok"
    assert D.overall([mk("ok"), mk("warn")]) == "warn"
    assert D.overall([mk("ok"), mk("warn"), mk("fail")]) == "fail"
    assert D.overall([]) == "ok"


def test_check_config_missing(tmp_path):
    f = D.check_config(tmp_path / "nope.yaml")
    assert len(f) == 1 and f[0].level == "fail" and f[0].code == "config_missing"


def test_check_deps_core_present():
    f = D.check_deps()
    assert not any(x.code == "dep_missing" for x in f)   # 跑测试的环境核心依赖必在
    assert any(x.code == "deps_ok" for x in f)


def test_check_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_data_dir", lambda: tmp_path)
    (tmp_path / "beliefs.json").write_text("{}", encoding="utf-8")
    assert D.check_data_dir()[0].code == "data_ok"
    (tmp_path / "tasks.json").write_text("{ broken", encoding="utf-8")
    f = D.check_data_dir()[0]
    assert f.level == "warn" and f.code == "data_corrupt" and "tasks.json" in f.params["files"]


# ---- L1 自愈:doctor --fix(只修可逆的)----
def test_repair_data_corrupt_backs_up_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_data_dir", lambda: tmp_path)
    (tmp_path / "tasks.json").write_text("{ broken json", encoding="utf-8")
    (tmp_path / "beliefs.json").write_text("{}", encoding="utf-8")   # 好的不动
    finding = D.check_data_dir()[0]
    assert finding.code == "data_corrupt"
    r = D.repair_finding(finding)
    assert r is not None and r.code == "repaired_data_corrupt" and "tasks.json" in r.params["files"]
    # 坏文件被移走(系统下次从空重建),备份留着(可逆),好文件不动
    assert not (tmp_path / "tasks.json").exists()
    assert (tmp_path / "tasks.json.corrupt.bak").exists()
    assert (tmp_path / "beliefs.json").exists()
    # 修完重诊 → 不再 corrupt
    assert D.check_data_dir()[0].code == "data_ok"


def test_apply_fixes_only_touches_autofixable(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_data_dir", lambda: tmp_path)
    (tmp_path / "domains.json").write_text("nope", encoding="utf-8")
    findings = [D.Finding(D.FAIL, "no_key", {}), *D.check_data_dir()]   # no_key 不可自动修
    repaired = D.apply_fixes(findings)
    assert len(repaired) == 1 and repaired[0].code == "repaired_data_corrupt"   # 只修了 data_corrupt
    assert not (tmp_path / "domains.json").exists()  # 坏的被备份移走


def test_check_version_offline_safe(monkeypatch):
    # 不可达 → newer=False → version_current(不卡、不报假新版)
    import karvyloop.update as U
    monkeypatch.setattr(U, "check_update", lambda: {"current": "0.2.0", "latest": None, "newer": False})
    f = D.check_version()[0]
    assert f.code == "version_current" and f.params["current"] == "0.2.0"


def test_check_version_newer(monkeypatch):
    import karvyloop.update as U
    monkeypatch.setattr(U, "check_update", lambda: {"current": "0.2.0", "latest": "0.3.0",
                                                    "newer": True, "command": "pip install -U karvyloop"})
    f = D.check_version()[0]
    assert f.level == "warn" and f.code == "version_newer" and f.params["latest"] == "0.3.0"


def test_run_doctor_never_raises():
    findings = D.run_doctor(check_port=False)
    assert isinstance(findings, list) and findings
    assert all(isinstance(x, D.Finding) for x in findings)
    assert D.overall(findings) in ("ok", "warn", "fail")


# ---- 每个可能的 finding code 都有 en+zh 文案(防 t() KeyError / 漏译)----
_ALL_CODES = [
    "config_missing", "config_unreadable", "no_default_model", "no_key", "model_not_ready",
    "model_ready", "deps_ok", "dep_missing", "dep_optional_missing", "data_fresh", "data_ok",
    "data_corrupt", "version_current", "version_newer", "port_busy", "port_free", "check_error",
    "repaired_data_corrupt",
]


def test_every_code_has_bilingual_message():
    from karvyloop.i18n._strings import TABLES
    for loc in ("en", "zh"):
        tbl = TABLES[loc]
        for code in _ALL_CODES:
            assert "doctor.msg." + code in tbl, f"[{loc}] 缺 doctor.msg.{code}"
    # 有 fix 的 code 双语 fix 也要在
    from karvyloop.cli.doctor_cmd import _FIX_CODES
    for loc in ("en", "zh"):
        for code in _FIX_CODES:
            assert "doctor.fix." + code in TABLES[loc], f"[{loc}] 缺 doctor.fix.{code}"
