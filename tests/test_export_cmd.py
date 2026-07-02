"""test_export_cmd — `karvyloop export`:实例打包带走,秘密绝不入包(docs/42 △)。

AC:
- AC1: 导出 zip 存在;含 skills/x/SKILL.md + MANIFEST.txt + tokens.db
- AC2: 包里**没有** config.yaml(API key!)/ console.runtime.json / *.lock
- AC3: MANIFEST.txt 说明排除了 config.yaml(keys stay put)+ 怎么恢复
- AC4: exit code 0 + 人话摘要(文件数/大小/落哪)
- AC5: 空 home(没 ~/.karvyloop)→ 友好提示、非零退出、不崩
- AC6: 走 CLI 入口 main(["export", ...]) 同样通(注册没断线)
- AC7: 默认输出名 karvyloop-instance-<YYYYMMDD>.zip;--out *.tar.gz 走 tarball
"""
from __future__ import annotations

import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from karvyloop.cli.export_cmd import cmd_export
from karvyloop.cli.main import main


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    """伪 ~/.karvyloop:真数据 + 秘密(fixture key 带 FAKE/DO-NOT-LEAK,防泄露纪律)。"""
    home = tmp_path / "home"
    kl = home / ".karvyloop"
    (kl / "skills" / "x").mkdir(parents=True)
    (kl / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: test skill\n---\nSteps: do x\n", encoding="utf-8")
    (kl / "tokens.db").write_bytes(b"\x00fake-sqlite")
    (kl / "atoms.json").write_text("[]", encoding="utf-8")
    # 秘密 —— 必须被排除
    (kl / "config.yaml").write_text(
        "models:\n  providers:\n    p:\n      api_key: FAKE-KEY-DO-NOT-LEAK\n",
        encoding="utf-8")
    (kl / "console.runtime.json").write_text('{"token": "FAKE-DO-NOT-LEAK"}', encoding="utf-8")
    (kl / "state.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return kl


# ---- AC1/AC2/AC3/AC4: 正常导出 ----

def test_export_includes_data_excludes_secrets(fake_home: Path, tmp_path: Path, capsys):
    out = tmp_path / "inst.zip"
    rc = cmd_export(out=str(out))
    assert rc == 0
    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert "skills/x/SKILL.md" in names
        assert "MANIFEST.txt" in names
        assert "tokens.db" in names
        assert "atoms.json" in names
        # AC2: 秘密绝不入包
        assert "config.yaml" not in names
        assert "console.runtime.json" not in names
        assert not any(n.endswith(".lock") for n in names)
        # AC3: manifest 讲清排除了什么 + 怎么恢复
        manifest = zf.read("MANIFEST.txt").decode("utf-8")
    assert "config.yaml" in manifest and "API keys" in manifest
    assert "excluded" in manifest.lower()
    assert "~/.karvyloop" in manifest and "karvyloop console" in manifest
    # 防泄露:key 内容不进 manifest
    assert "FAKE-KEY-DO-NOT-LEAK" not in manifest
    # AC4: 人话摘要(3 个实例文件;MANIFEST.txt 是包里额外生成的第 4 个条目)
    out_text = capsys.readouterr().out
    assert "3 files" in out_text
    assert "config.yaml" in out_text  # 明说排除了


# ---- AC5: 空 home ----

def test_export_empty_home_friendly_no_crash(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    rc = cmd_export(out=str(tmp_path / "never.zip"))
    assert rc != 0
    assert not (tmp_path / "never.zip").exists()
    err = capsys.readouterr().err
    assert "Nothing to export" in err


def test_export_home_with_only_secrets_friendly(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "home2"
    kl = home / ".karvyloop"
    kl.mkdir(parents=True)
    (kl / "config.yaml").write_text("api_key: FAKE-DO-NOT-LEAK", encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    rc = cmd_export(out=str(tmp_path / "never.zip"))
    assert rc != 0
    assert not (tmp_path / "never.zip").exists()
    assert "Nothing to export" in capsys.readouterr().err


# ---- AC6: CLI 入口注册没断线 ----

def test_export_via_cli_main(fake_home: Path, tmp_path: Path):
    out = tmp_path / "via-main.zip"
    rc = main(["export", "--out", str(out)])
    assert rc == 0
    with zipfile.ZipFile(out) as zf:
        assert "MANIFEST.txt" in zf.namelist()


# ---- AC7: 默认文件名 + tar.gz 变体 ----

def test_export_default_name_in_cwd(fake_home: Path, tmp_path: Path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    rc = cmd_export()
    assert rc == 0
    expected = workdir / f"karvyloop-instance-{datetime.now():%Y%m%d}.zip"
    assert expected.exists()


def test_export_targz_variant(fake_home: Path, tmp_path: Path):
    out = tmp_path / "inst.tar.gz"
    rc = cmd_export(out=str(out))
    assert rc == 0
    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())
    assert "MANIFEST.txt" in names
    assert "skills/x/SKILL.md" in names
    assert "config.yaml" not in names
