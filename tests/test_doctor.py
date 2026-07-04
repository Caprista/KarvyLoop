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
    "repaired_data_corrupt", "repaired_config_missing", "repaired_config_unreadable",
    # 活性检查 + 确认/日志
    "endpoint_reachable", "endpoint_unreachable", "local_endpoint_down", "liveness_skipped",
    "disk_writable", "disk_not_writable", "sandbox_ok", "sandbox_degraded", "sandbox_stub",
    "sandbox_error",
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


# ---- --fix 建缺失骨架(config_missing → 创建,幂等,不覆盖)----
def test_repair_config_missing_creates_skeleton(tmp_path):
    cfg = tmp_path / "sub" / "config.yaml"
    finding = D.check_config(cfg)
    assert finding[0].code == "config_missing"
    r = D.repair_finding(finding[0])
    assert r is not None and r.code == "repaired_config_missing"
    assert cfg.exists() and "models:" in cfg.read_text(encoding="utf-8")


def test_repair_config_missing_idempotent_no_overwrite(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("USER-CONTENT", encoding="utf-8")
    # 已存在 → repair 返回 None(幂等,不踩用户已配)
    assert D.repair_finding(D.Finding(D.FAIL, "config_missing", {"path": str(cfg)})) is None
    assert cfg.read_text(encoding="utf-8") == "USER-CONTENT"


def test_apply_fixes_config_missing_is_auto(tmp_path):
    cfg = tmp_path / "config.yaml"
    findings = D.check_config(cfg)
    repaired = D.apply_fixes(findings)   # 默认 include_confirmed=False,config_missing 属 AUTO
    assert any(r.code == "repaired_config_missing" for r in repaired)
    assert cfg.exists()


# ---- config_unreadable 是危险修:默认 --fix 不碰,确认后才修(备份后重写)----
def test_config_unreadable_not_auto_fixed(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{ this is: not: valid: yaml", encoding="utf-8")
    findings = [D.Finding(D.FAIL, "config_unreadable", {"path": str(cfg)})]
    # 默认(不确认)→ 不动
    assert D.apply_fixes(findings, include_confirmed=False) == []
    assert cfg.read_text(encoding="utf-8").startswith("{ this")


def test_config_unreadable_repaired_after_confirm(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("BROKEN", encoding="utf-8")
    r = D.repair_finding(D.Finding(D.FAIL, "config_unreadable", {"path": str(cfg)}))
    assert r is not None and r.code == "repaired_config_unreadable"
    # 坏的被备份(可逆),新骨架就位
    bak = tmp_path / "config.yaml.bak"
    assert bak.exists() and bak.read_text(encoding="utf-8") == "BROKEN"
    assert "models:" in cfg.read_text(encoding="utf-8")
    assert D.CONFIRM_FIXABLE == {"config_unreadable"}


# ---- 活性检查:endpoint 连不上被检出 / 装了没配 vs 配了连不上 ----
def _write_cloud_cfg(p, key="sk-FAKE-DO-NOT-LEAK"):
    import textwrap
    p.write_text(textwrap.dedent(f"""\
        models:
          providers:
            anthropic:
              base_url: https://api.anthropic.com
              auth: api-key
              api_key: {key}
              models:
                - id: anthropic/claude-x
                  name: X
                  api: anthropic-messages
                  context_window: 200000
                  max_tokens: 8192
        agents:
          defaults:
            model: anthropic/claude-x
        """), encoding="utf-8")


def test_liveness_endpoint_unreachable_detected(tmp_path):
    from karvyloop import doctor_liveness as L
    cfg = tmp_path / "config.yaml"
    _write_cloud_cfg(cfg)
    # 配好了(有 key)但网络探测失败 → endpoint_unreachable(FAIL)
    f = L.check_endpoint(cfg, connect_probe=lambda h, p: False)
    assert f[0].level == "fail" and f[0].code == "endpoint_unreachable"
    assert "host" in f[0].params
    # 探通 → reachable
    f2 = L.check_endpoint(cfg, connect_probe=lambda h, p: True)
    assert f2[0].code == "endpoint_reachable"


def test_liveness_distinguishes_unconfigured_from_unreachable(tmp_path):
    from karvyloop import doctor_liveness as L
    cfg = tmp_path / "config.yaml"
    _write_cloud_cfg(cfg, key="")   # 没配 key
    # 没配 → skipped(不冒充"连不上"),区分"装了没配" vs "配了连不上"
    f = L.check_endpoint(cfg, connect_probe=lambda h, p: False)
    assert f[0].code == "liveness_skipped"
    # 没 config 文件 → 也 skipped
    assert L.check_endpoint(tmp_path / "none.yaml")[0].code == "liveness_skipped"


def test_liveness_probe_never_sends_key(tmp_path, monkeypatch):
    """活性探测只做 TCP connect,不发任何字节 → 不可能泄 key。"""
    from karvyloop import doctor_liveness as L
    cfg = tmp_path / "config.yaml"
    _write_cloud_cfg(cfg, key="sk-FAKE-DO-NOT-LEAK-SECRET")
    seen = {}
    def spy(host, port):
        seen["host"] = host
        seen["port"] = port
        return True
    f = L.check_endpoint(cfg, connect_probe=spy)
    assert f[0].code == "endpoint_reachable"
    # probe 只拿到 host/port,拿不到 key(签名里根本没 key)
    assert seen == {"host": "api.anthropic.com", "port": 443}


def test_liveness_disk_and_sandbox(tmp_path, monkeypatch):
    from karvyloop import doctor_liveness as L
    monkeypatch.setattr(L, "_data_dir", lambda: tmp_path)
    assert L.check_disk_writable()[0].code == "disk_writable"
    # 沙箱:任一实现,永不抛,产 ok/warn
    sf = L.check_sandbox()[0]
    assert sf.level in ("ok", "warn")


def test_run_liveness_never_raises(tmp_path):
    from karvyloop import doctor_liveness as L
    out = L.run_liveness(config_path=tmp_path / "none.yaml", connect_probe=lambda h, p: True)
    assert isinstance(out, list) and out


# ---- 日志固定落盘 ----
def test_log_findings_writes_to_fixed_path(tmp_path, monkeypatch):
    import karvyloop.doctor_log as LOG
    monkeypatch.setattr(LOG, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(LOG, "_configured", False)
    import logging
    logging.getLogger("karvyloop.doctor").handlers = []
    p = LOG.log_findings([D.Finding(D.WARN, "port_busy", {"port": 8766})], phase="test")
    assert p is not None and p.exists()
    text = p.read_text(encoding="utf-8")
    assert "port_busy" in text


def test_log_never_contains_key(tmp_path, monkeypatch):
    """落盘只写 finding code+params(从不含 key);断言日志里没有任何 FAKE key 痕迹。"""
    import karvyloop.doctor_log as LOG
    monkeypatch.setattr(LOG, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(LOG, "_configured", False)
    import logging
    logging.getLogger("karvyloop.doctor").handlers = []
    LOG.log_findings([D.Finding(D.OK, "endpoint_reachable",
                                {"host": "api.anthropic.com", "provider": "anthropic"})])
    text = LOG.log_path().read_text(encoding="utf-8")
    assert "FAKE" not in text and "sk-" not in text and "api_key" not in text


# ---- health_summary(给 /api/health)----
def test_health_summary_shape(tmp_path):
    from karvyloop.cli.doctor_cmd import health_summary
    s = health_summary(config_path=tmp_path / "none.yaml")
    assert s["overall"] in ("ok", "warn", "fail")
    assert isinstance(s["findings"], list)
    for item in s["findings"]:
        assert item["fixable"] in ("auto", "confirm", "no")
    # config_missing 应标 auto-fixable
    assert any(f["code"] == "config_missing" and f["fixable"] == "auto" for f in s["findings"])
