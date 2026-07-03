"""test_import_cmd — `karvyloop import`:export 的回程,一键迁移(docs/43 碎碎念⑤)。

AC:
- AC1: 往返 — 真 export → 空机器 import → 文件逐一还原(字节相同);config.yaml /
       console.runtime.json / *.lock / MANIFEST.txt 不落地
- AC2: 本机已有 config.yaml(刚 init 完)→ import 不挡道、config.yaml 一字不动
- AC3: 路径穿越样本(../ / 绝对路径 / 盘符 / tar 链接)→ 整包拒收,零写盘
- AC4: 目标已有实例数据 → 默认拒绝并列出会被覆盖的顶层项;--force 逐文件覆盖、
       本机独有文件保留
- AC5: --dry-run 列清单、零写盘(非空目标也能干跑)
- AC6: 坏包(截断 zip)诚实报错、目标零污染、无临时目录残留
- AC7: CLI 入口 main(["import", ...]) 注册没断线;tar.gz 变体同样往返
"""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from karvyloop.cli.export_cmd import cmd_export
from karvyloop.cli.import_cmd import cmd_import
from karvyloop.cli.main import main


@pytest.fixture(autouse=True)
def _pin_locale():
    """输出走后端 i18n;钉死 en 再还原,免疫测试顺序污染(与 test_export_cmd 同策略)。"""
    from karvyloop.i18n import get_locale, set_locale
    prev = get_locale()
    set_locale("en")
    yield
    set_locale(prev)


def _grow_instance(kl: Path) -> None:
    """种一个带数据 + 秘密的实例(fixture key 带 FAKE/DO-NOT-LEAK,防泄露纪律)。"""
    (kl / "skills" / "x").mkdir(parents=True)
    (kl / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: test skill\n---\nSteps: do x\n", encoding="utf-8")
    (kl / "tokens.db").write_bytes(b"\x00fake-sqlite")
    (kl / "atoms.json").write_text('[{"id": "a1"}]', encoding="utf-8")
    # 秘密 —— export 不带走,import 更不许落地
    (kl / "config.yaml").write_text(
        "models:\n  providers:\n    p:\n      api_key: FAKE-KEY-DO-NOT-LEAK\n", encoding="utf-8")
    (kl / "console.runtime.json").write_text('{"token": "FAKE-DO-NOT-LEAK"}', encoding="utf-8")
    (kl / "state.lock").write_text("", encoding="utf-8")


@pytest.fixture()
def old_home(tmp_path: Path, monkeypatch) -> Path:
    """出发机:~/.karvyloop 有真数据。返回 home(不是 .karvyloop)。"""
    home = tmp_path / "old-home"
    _grow_instance(home / ".karvyloop")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _switch_home(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _no_tmp_leftover(home: Path) -> bool:
    return not list(home.glob(".karvyloop.import-tmp-*"))


# ---- AC1: 往返还原,秘密/MANIFEST 不落地 ----

def test_roundtrip_export_then_import(old_home: Path, tmp_path: Path, monkeypatch, capsys):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    originals = {
        "skills/x/SKILL.md": (old_home / ".karvyloop" / "skills" / "x" / "SKILL.md").read_bytes(),
        "tokens.db": (old_home / ".karvyloop" / "tokens.db").read_bytes(),
        "atoms.json": (old_home / ".karvyloop" / "atoms.json").read_bytes(),
    }

    new_home = _switch_home(monkeypatch, tmp_path / "new-home")
    rc = cmd_import(str(archive))
    assert rc == 0
    kl = new_home / ".karvyloop"
    for rel, data in originals.items():
        assert (kl / rel).read_bytes() == data, f"{rel} not restored byte-identical"
    # 秘密与包说明书都不落地
    assert not (kl / "config.yaml").exists()
    assert not (kl / "console.runtime.json").exists()
    assert not (kl / "MANIFEST.txt").exists()
    assert not list(kl.rglob("*.lock"))
    assert _no_tmp_leftover(new_home)
    out = capsys.readouterr().out
    assert "3 files" in out                    # 恢复了什么
    assert "config.yaml" in out                # 明说密钥策略
    assert "karvyloop console" in out          # 下一步


def test_targz_roundtrip(old_home: Path, tmp_path: Path, monkeypatch):
    archive = tmp_path / "inst.tar.gz"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "new-home-tgz")
    assert cmd_import(str(archive)) == 0
    assert (new_home / ".karvyloop" / "skills" / "x" / "SKILL.md").exists()
    assert not (new_home / ".karvyloop" / "config.yaml").exists()


# ---- AC2: 本机 config.yaml 绝不被碰 ----

def test_local_config_yaml_untouched(old_home: Path, tmp_path: Path, monkeypatch):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "new-home-cfg")
    kl = new_home / ".karvyloop"
    kl.mkdir(parents=True)
    local_cfg = "models:\n  providers:\n    q:\n      api_key: FAKE-LOCAL-DO-NOT-LEAK\n"
    (kl / "config.yaml").write_text(local_cfg, encoding="utf-8")
    # 只有 config.yaml ≠ 实例数据 → 不该被"非空拒绝"挡住
    assert cmd_import(str(archive)) == 0
    assert (kl / "config.yaml").read_text(encoding="utf-8") == local_cfg
    assert (kl / "atoms.json").exists()


def test_archive_smuggled_secrets_never_land(tmp_path: Path, monkeypatch):
    """手工恶意包夹带 config.yaml/*.lock/MANIFEST.txt → 全部跳过,数据照常恢复。"""
    archive = tmp_path / "smuggle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("MANIFEST.txt", "archive readme")
        zf.writestr("config.yaml", "api_key: FAKE-FOREIGN-DO-NOT-LEAK")
        zf.writestr("console.runtime.json", '{"token": "FAKE-DO-NOT-LEAK"}')
        zf.writestr("evil.lock", "")
        zf.writestr("skills/y/SKILL.md", "---\nname: y\n---\nSteps: y\n")
    new_home = _switch_home(monkeypatch, tmp_path / "new-home-smuggle")
    assert cmd_import(str(archive)) == 0
    kl = new_home / ".karvyloop"
    assert (kl / "skills" / "y" / "SKILL.md").exists()
    assert not (kl / "config.yaml").exists()
    assert not (kl / "console.runtime.json").exists()
    assert not (kl / "evil.lock").exists()
    assert not (kl / "MANIFEST.txt").exists()


# ---- AC3: 路径穿越 → 整包拒收,零写盘 ----

@pytest.mark.parametrize("bad_name", [
    "../evil.txt",
    "skills/../../evil.txt",
    "/etc/evil.txt",
    "C:/evil.txt",
    "\\\\server\\share\\evil.txt",
])
def test_traversal_member_rejects_whole_archive(tmp_path: Path, monkeypatch, capsys, bad_name):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("atoms.json", "[]")          # 好成员也救不了坏包
        zf.writestr(bad_name, "pwned")
    new_home = _switch_home(monkeypatch, tmp_path / "victim-home")
    rc = cmd_import(str(archive))
    assert rc != 0
    assert not (new_home / ".karvyloop").exists()          # 一个字节都没写
    assert not (tmp_path / "evil.txt").exists()            # 没穿出去
    assert _no_tmp_leftover(new_home)
    err = capsys.readouterr().err
    assert "unsafe" in err.lower()


def test_tar_symlink_member_rejected(tmp_path: Path, monkeypatch, capsys):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("skills/ln")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        tf.addfile(info)
        data = b"[]"
        f = tarfile.TarInfo("atoms.json")
        f.size = len(data)
        tf.addfile(f, io.BytesIO(data))
    new_home = _switch_home(monkeypatch, tmp_path / "victim-home-tar")
    rc = cmd_import(str(archive))
    assert rc != 0
    assert not (new_home / ".karvyloop").exists()
    assert "unsafe" in capsys.readouterr().err.lower()


# ---- AC4: 非空目标默认拒绝;--force 合并覆盖、本机独有保留 ----

def test_nonempty_target_refuses_then_force_merges(old_home: Path, tmp_path: Path,
                                                   monkeypatch, capsys):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "busy-home")
    kl = new_home / ".karvyloop"
    (kl / "skills").mkdir(parents=True)
    (kl / "atoms.json").write_text('[{"id": "local"}]', encoding="utf-8")   # 会冲突
    (kl / "local_only.txt").write_text("mine", encoding="utf-8")            # 本机独有

    rc = cmd_import(str(archive))
    assert rc != 0
    err = capsys.readouterr().err
    assert "atoms.json" in err            # 列出会被覆盖的顶层项
    assert "--force" in err
    assert (kl / "atoms.json").read_text(encoding="utf-8") == '[{"id": "local"}]'  # 没动

    rc = cmd_import(str(archive), force=True)
    assert rc == 0
    assert (kl / "atoms.json").read_text(encoding="utf-8") == '[{"id": "a1"}]'     # 覆盖了
    assert (kl / "local_only.txt").read_text(encoding="utf-8") == "mine"           # 保留了
    assert (kl / "skills" / "x" / "SKILL.md").exists()


# ---- AC5: --dry-run 零写盘 ----

def test_dry_run_lists_and_writes_nothing(old_home: Path, tmp_path: Path, monkeypatch, capsys):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "dry-home")
    rc = cmd_import(str(archive), dry_run=True)
    assert rc == 0
    assert not (new_home / ".karvyloop").exists()      # 真·零写盘
    assert _no_tmp_leftover(new_home)
    out = capsys.readouterr().out
    assert "skills/x/SKILL.md" in out
    assert "atoms.json" in out


def test_dry_run_allowed_on_nonempty_target(old_home: Path, tmp_path: Path, monkeypatch, capsys):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "dry-busy-home")
    kl = new_home / ".karvyloop"
    kl.mkdir(parents=True)
    (kl / "atoms.json").write_text("[]", encoding="utf-8")
    rc = cmd_import(str(archive), dry_run=True)        # 不用 --force 也能看清单
    assert rc == 0
    assert (kl / "atoms.json").read_text(encoding="utf-8") == "[]"   # 没动
    assert "overwrite" in capsys.readouterr().out      # 冲突有标记


# ---- AC6: 坏包诚实报错,不半写 ----

def test_truncated_zip_honest_error_no_partial_write(old_home: Path, tmp_path: Path,
                                                     monkeypatch, capsys):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    data = archive.read_bytes()
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(data[: len(data) // 2])
    new_home = _switch_home(monkeypatch, tmp_path / "trunc-home")
    rc = cmd_import(str(truncated))
    assert rc != 0
    assert not (new_home / ".karvyloop").exists()      # 目标零污染
    assert _no_tmp_leftover(new_home)                  # 临时目录不残留
    err = capsys.readouterr().err
    assert "Cannot read" in err and "nothing was written" in err


def test_missing_archive_friendly(tmp_path: Path, monkeypatch, capsys):
    _switch_home(monkeypatch, tmp_path / "nohome")
    rc = cmd_import(str(tmp_path / "does-not-exist.zip"))
    assert rc != 0
    assert "not found" in capsys.readouterr().err.lower()


# ---- AC7: CLI 入口注册没断线 ----

def test_import_via_cli_main(old_home: Path, tmp_path: Path, monkeypatch):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "cli-home")
    rc = main(["import", str(archive)])
    assert rc == 0
    assert (new_home / ".karvyloop" / "skills" / "x" / "SKILL.md").exists()


def test_import_via_cli_main_dry_run_flag(old_home: Path, tmp_path: Path, monkeypatch):
    archive = tmp_path / "inst.zip"
    assert cmd_export(out=str(archive)) == 0
    new_home = _switch_home(monkeypatch, tmp_path / "cli-dry-home")
    rc = main(["import", str(archive), "--dry-run"])
    assert rc == 0
    assert not (new_home / ".karvyloop").exists()
