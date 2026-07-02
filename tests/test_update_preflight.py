"""test_update_preflight — 升级前置(回滚点快照 + 实例备份)+ 装后自检 + 自动/手动回滚。

seam:一切 shell-out(git/pip/smoke)走 `karvyloop.update._run`(subprocess 唯一咽喉),
测试在这层 stub;fake home 用 tmp_path(所有函数都收 home=/root= 参数,不碰真 ~/.karvyloop)。
诚实范围声明:备份**只**拷 ~/.karvyloop 顶层状态文件(*.json/*.yaml/*.db/*.sqlite)+ skills/
顶层 *.json 索引;skills/ 本体(SKILL.md 方法库)可能很大、且升级契约本就不动 ~/.karvyloop,
所以不整目录拷 —— 这个范围写死在 manifest.json 的 scope 字段里,本文件有测试锁它。
"""
from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import update as U  # noqa: E402


def _make_home(tmp_path) -> pathlib.Path:
    """造一个像样的 fake ~/.karvyloop:状态文件 + 临时文件 + skills(索引 + 大方法体)。"""
    home = tmp_path / ".karvyloop"
    home.mkdir()
    (home / "beliefs.json").write_text('{"beliefs": []}', encoding="utf-8")
    (home / "domains.json").write_text("{}", encoding="utf-8")
    (home / "config.yaml").write_text("lang: en\n", encoding="utf-8")
    (home / "tokens.db").write_bytes(b"sqlite-ish")
    (home / "trace.sqlite").write_bytes(b"sqlite-ish")
    (home / "_upgrade.json").write_text("{}", encoding="utf-8")          # 临时,不该被备份
    (home / ".update_check.json").write_text("{}", encoding="utf-8")     # 缓存,不该被备份
    skills = home / "skills"
    (skills / "big-skill").mkdir(parents=True)
    (skills / "index.json").write_text("{}", encoding="utf-8")           # 索引关键 → 备份
    (skills / "big-skill" / "SKILL.md").write_text("# method body", encoding="utf-8")  # 方法体 → 不拷
    return home


def _stub_run(monkeypatch, *, smoke_rc=0, reset_rc=0, pip_rc=0, rev="abc123def4567890"):
    """stub 掉唯一 shell-out 咽喉;返回调用记录列表(argv, cwd)。"""
    calls: list = []

    def fake_run(argv, cwd=None, timeout=900):
        argv = [str(a) for a in argv]
        calls.append((argv, cwd))
        if argv[:2] == ["git", "rev-parse"]:
            return 0, rev + "\n"
        if argv[:3] == ["git", "reset", "--hard"]:
            return reset_rc, "" if reset_rc == 0 else "reset boom"
        if "-c" in argv:                                   # post-install smoke
            return smoke_rc, "" if smoke_rc == 0 else "ImportError: broken build"
        if "pip" in argv:
            return pip_rc, "" if pip_rc == 0 else "pip boom"
        return 0, ""

    monkeypatch.setattr(U, "_run", fake_run)
    return calls


# ---- (a) preflight:写回滚点 + 备份目录(诚实范围)+ 有界留 3 份 ----
def test_preflight_writes_rollback_point_and_backup(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    _stub_run(monkeypatch, rev="cafe0001beef0002")
    out = U.preflight("2099.1.1", home=home, root=tmp_path)
    assert out["ok"] is True and out["prev_commit"] == "cafe0001beef0002"

    # 回滚点:{prev_commit, prev_version, ts}
    rb = json.loads((home / "update_rollback.json").read_text(encoding="utf-8"))
    assert rb["prev_commit"] == "cafe0001beef0002"
    assert rb["prev_version"] == U.current_version()
    assert rb["ts"] > 0

    # 备份目录:状态文件都在;临时/缓存/skills 方法体不在
    bdir = pathlib.Path(out["backup_dir"])
    assert bdir.parent == home / "backups" and bdir.name.startswith("pre-2099.1.1-")
    for kept in ("beliefs.json", "domains.json", "config.yaml", "tokens.db",
                 "trace.sqlite", "skills/index.json"):
        assert (bdir / kept).exists(), kept
    assert not (bdir / "_upgrade.json").exists()           # 临时不备份
    assert not (bdir / ".update_check.json").exists()      # 缓存不备份
    assert not (bdir / "skills" / "big-skill").exists()    # 方法体不拷(诚实范围)
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    assert "skills" in manifest["scope"] and "NOT copied" in manifest["scope"]   # 范围说清楚
    assert "beliefs.json" in manifest["files"] and "skills/index.json" in manifest["files"]


def test_backup_prunes_to_last_three(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    broot = home / "backups"
    for i in range(4):                                     # 4 份旧备份,mtime 递增
        d = broot / f"pre-old{i}-2020010{i}-000000"
        d.mkdir(parents=True)
        t = time.time() - 86400 * (10 - i)
        os.utime(d, (t, t))
    out = U.backup_instance_state("2099.1.1", home=home)
    assert out["ok"] is True
    remaining = sorted(p.name for p in broot.iterdir() if p.is_dir())
    assert len(remaining) == 3                             # 有界:只留最近 3 份
    assert pathlib.Path(out["backup_dir"]).name in remaining   # 新的必在
    assert "pre-old0-20200100-000000" not in remaining     # 最老的删了
    assert "pre-old1-20200101-000000" not in remaining


def test_preflight_aborts_when_snapshot_fails(tmp_path, monkeypatch):
    """记不下回滚点(git rev-parse 失败)→ ok=False:没有后悔药就不动手(调用方中止升级)。"""
    home = _make_home(tmp_path)
    monkeypatch.setattr(U, "_run", lambda argv, cwd=None, timeout=900: (128, "not a git repo"))
    out = U.preflight("2099.1.1", home=home, root=tmp_path)
    assert out["ok"] is False and out["stage"] == "snapshot"
    assert not (home / "update_rollback.json").exists()


# ---- (b) 装后自检失败 → 自动回滚调用序列(git reset --hard <prev> → pip install -e .) ----
def test_failed_smoke_triggers_auto_rollback(monkeypatch, tmp_path):
    calls = _stub_run(monkeypatch, smoke_rc=1)             # 装成功、但新代码导入不了
    logs: list = []
    fin = U.finalize_install(0, "deadbeefcafe1234", root=tmp_path, python="pyX",
                             log=logs.append)
    assert fin["ok"] is False and fin["rolled_back"] is True
    assert "自检失败" in fin["reason"]                       # fail-loud:说清为什么回滚
    argvs = [c[0] for c in calls]
    i_smoke = next(i for i, a in enumerate(argvs) if "-c" in a)
    assert argvs[i_smoke][0] == "pyX" and U._SMOKE_CODE in argvs[i_smoke]
    i_reset = argvs.index(["git", "reset", "--hard", "deadbeefcafe1234"])   # 回到 prev commit
    i_pip = next(i for i, a in enumerate(argvs) if a[:4] == ["pyX", "-m", "pip", "install"])
    assert i_smoke < i_reset < i_pip                        # 顺序:自检 → reset → 重装
    assert calls[i_reset][1] == tmp_path                    # 在仓库根跑


def test_failed_install_cmd_also_rolls_back(monkeypatch, tmp_path):
    """升级命令本身 rc!=0(git pull 可能已挪树、pip 半途死)→ 不跑 smoke,直接回已知好 commit。"""
    calls = _stub_run(monkeypatch)
    fin = U.finalize_install(1, "deadbeefcafe1234", root=tmp_path, python="pyX")
    assert fin["ok"] is False and fin["rolled_back"] is True
    argvs = [c[0] for c in calls]
    assert ["git", "reset", "--hard", "deadbeefcafe1234"] in argvs
    assert not any("-c" in a for a in argvs)               # 装都没装成,不必 smoke


def test_smoke_pass_means_no_rollback(monkeypatch, tmp_path):
    calls = _stub_run(monkeypatch, smoke_rc=0)
    fin = U.finalize_install(0, "deadbeefcafe1234", root=tmp_path, python="pyX")
    assert fin == {"ok": True, "rolled_back": False, "reason": ""}
    assert not any(a[:2] == ["git", "reset"] for a, _ in calls)


def test_rollback_failure_is_loud_not_silent(monkeypatch, tmp_path):
    """回滚也失败 → 两层原因都在 reason 里(绝不静默装死)。"""
    _stub_run(monkeypatch, smoke_rc=1, reset_rc=128)
    fin = U.finalize_install(0, "deadbeefcafe1234", root=tmp_path, python="pyX")
    assert fin["ok"] is False and fin["rolled_back"] is False
    assert "自检失败" in fin["reason"] and "回滚也失败" in fin["reason"]


# ---- (c) 手动回滚:按 update_rollback.json 恢复 ----
def test_rollback_restores_from_rollback_point(tmp_path, monkeypatch):
    home = tmp_path / ".karvyloop"
    home.mkdir()
    (home / "update_rollback.json").write_text(json.dumps(
        {"prev_commit": "cafebabe1234567", "prev_version": "2026.6.1", "ts": 1.0}),
        encoding="utf-8")
    calls = _stub_run(monkeypatch)
    out = U.rollback(home=home, root=tmp_path, python="pyX")
    assert out["ok"] is True and out["prev_version"] == "2026.6.1"
    argvs = [c[0] for c in calls]
    assert ["git", "reset", "--hard", "cafebabe1234567"] in argvs
    assert any(a[:4] == ["pyX", "-m", "pip", "install"] for a in argvs)


def test_rollback_without_point_refuses(tmp_path, monkeypatch):
    calls = _stub_run(monkeypatch)
    out = U.rollback(home=tmp_path / "empty", root=tmp_path)
    assert out["ok"] is False and "回滚点" in out["reason"]
    assert calls == []                                     # 什么都没动


def test_rollback_endpoint_gates(monkeypatch):
    """POST /update/rollback:CSRF 同款门;无回滚点 → 老实拒(不碰锁、不起 runner)。"""
    from karvyloop.console.routes import api_update_rollback

    def req(host="127.0.0.1", header="1"):
        headers = {"x-karvyloop-upgrade": header} if header is not None else {}
        return types.SimpleNamespace(
            client=types.SimpleNamespace(host=host),
            headers=types.SimpleNamespace(get=lambda k, d=None: headers.get(k.lower(), d)),
            app=types.SimpleNamespace(state=types.SimpleNamespace(console_relaunch=None)))

    out = api_update_rollback(req(header=None))
    assert out["ok"] is False and "CSRF" in out["reason"]
    out = api_update_rollback(req(host="8.8.8.8"))                     # 公网来源 → 拒
    assert out["ok"] is False and "可信网内" in out["reason"]
    monkeypatch.setattr(U, "read_rollback_point", lambda **kw: None)   # 端点在调用时才 import → 生效
    out = api_update_rollback(req())
    assert out["ok"] is False and "回滚点" in out["reason"]


# ---- (d) 状态载荷:rollback_available / prev_version(诚实 UX) ----
def test_rollback_status_fields(tmp_path):
    assert U.rollback_status(home=tmp_path / "nope") == {
        "rollback_available": False, "prev_version": None}
    home = tmp_path / ".karvyloop"
    home.mkdir()
    (home / "update_rollback.json").write_text(json.dumps(
        {"prev_commit": "abc1234", "prev_version": "2026.6.1", "ts": 1.0}), encoding="utf-8")
    assert U.rollback_status(home=home) == {
        "rollback_available": True, "prev_version": "2026.6.1"}


def test_update_status_endpoint_carries_rollback_fields(monkeypatch):
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    monkeypatch.setattr(U, "check_update",
                        lambda: {"current": "2026.6.1", "latest": "2026.7.1", "newer": True,
                                 "command": "git pull && pip install -e .", "url": "http://x",
                                 "checked": True, "source": "live"})
    monkeypatch.setattr(U, "rollback_status",
                        lambda **kw: {"rollback_available": True, "prev_version": "2026.6.1"})
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/update_status").json()
    assert r["rollback_available"] is True and r["prev_version"] == "2026.6.1"


# ---- 静态断言:备份范围不漏关键状态文件 ----
def test_backup_patterns_cover_all_critical_state_files():
    """已知的实例关键状态文件(entry.py / cli 各处落盘的)必须每个都被 BACKUP_PATTERNS 罩住。
    有意排除的只有**目录**:skills/ 方法体、conversations/、roles/(大、纯文本、升级契约不碰
    ~/.karvyloop)—— 已写进模块 docstring + 每份备份的 manifest.scope,不假装备份了全部。"""
    critical = [
        "beliefs.json", "domains.json", "atoms.json", "tasks.json",
        "decision_stats.json", "decision_log.json", "decision_revoked.json",
        "taste_predictions.json", "coding.json", "search.json",
        "config.yaml",
        "tokens.db", "habits.db", "trace_buffer.db",
        "trace.sqlite", "usage.sqlite", "verify.sqlite",
    ]
    for name in critical:
        assert any(fnmatch.fnmatch(name, pat) for pat in U.BACKUP_PATTERNS), \
            f"{name} 不被任何备份 pattern 覆盖 —— 升级备份漏了关键状态"
