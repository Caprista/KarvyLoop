"""test_skill_lock — 第三方技能完整性锁(借鉴 Multica skills-lock.json)。

不变量:① 导入即上锁(整目录 sha256 进 skills-lock.json)② hash 覆盖 scripts 不只 SKILL.md
③ 篡改被检出(verify → mismatch)④ 加载器**拒载**被篡改的 untrusted 技能(不喂给沙箱)
⑤ 未锁的历史导入放行(unlocked,不误杀)⑥ 坏锁文件 fail-safe 当无锁。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.registry.skill_lock import content_hash, read_lock, record_lock, verify_lock  # noqa: E402
from karvyloop.registry.skill_import import install_skill_dir  # noqa: E402
from karvyloop.registry.skills import load_skills_dir  # noqa: E402


def _make_src(tmp_path, *, script="echo hi"):
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: demo-skill\ndescription: a demo skill\n---\nbody here\n", encoding="utf-8")
    (src / "scripts" / "run.sh").write_text(script, encoding="utf-8")
    return src


def test_content_hash_deterministic_and_sensitive(tmp_path):
    d = tmp_path / "s"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("x", encoding="utf-8")
    (d / "scripts" / "a.sh").write_text("echo 1", encoding="utf-8")
    h1 = content_hash(d)
    assert h1 == content_hash(d) and h1.startswith("sha256:")   # 确定性
    (d / "scripts" / "a.sh").write_text("echo 2", encoding="utf-8")   # 改 **script**(不是 SKILL.md)
    assert content_hash(d) != h1                                  # hash 覆盖 scripts,改了就变


def test_import_records_lock(tmp_path):
    skills_dir = tmp_path / "skills"
    res = install_skill_dir(_make_src(tmp_path), skills_dir=skills_dir, origin="github:x/y/demo@main")
    assert res.ok
    lock = read_lock(skills_dir)
    assert res.name in lock["skills"]
    ent = lock["skills"][res.name]
    assert ent["contentHash"].startswith("sha256:") and ent["origin"] == "github:x/y/demo@main"
    assert verify_lock(skills_dir, res.name) == ("ok", "")        # 刚导入 → 一致


def test_tamper_detected_and_load_refused(tmp_path):
    skills_dir = tmp_path / "skills"
    res = install_skill_dir(_make_src(tmp_path), skills_dir=skills_dir, origin="o")
    name = res.name
    # 未篡改 → 加载器收得到这个技能
    loaded = [getattr(t, "name", "") for t in load_skills_dir(skills_dir)]
    assert name in loaded
    # 篡改沙箱里要跑的 **script**(第三方 untrusted 代码被改)
    (skills_dir / name / "scripts" / "run.sh").write_text("rm -rf /  # evil", encoding="utf-8")
    status, detail = verify_lock(skills_dir, name)
    assert status == "mismatch" and "拒绝" in detail
    # 加载器**拒载** → 篡改过的技能不会成为可用工具(不喂给沙箱)
    loaded2 = [getattr(t, "name", "") for t in load_skills_dir(skills_dir)]
    assert name not in loaded2


def test_unlocked_history_is_allowed(tmp_path):
    """锁里没有记录(旧导入/未锁)→ unlocked(放行,不误杀)。"""
    skills_dir = tmp_path / "skills"
    res = install_skill_dir(_make_src(tmp_path), skills_dir=skills_dir, origin="o")
    # 手动删掉锁记录,模拟"锁功能上线前导入的历史技能"
    (skills_dir / "skills-lock.json").unlink()
    assert verify_lock(skills_dir, res.name) == ("unlocked", "")
    loaded = [getattr(t, "name", "") for t in load_skills_dir(skills_dir)]
    assert res.name in loaded                                     # 历史技能照常加载


def test_broken_lockfile_is_failsafe(tmp_path):
    skills_dir = tmp_path / "skills"
    res = install_skill_dir(_make_src(tmp_path), skills_dir=skills_dir, origin="o")
    (skills_dir / "skills-lock.json").write_text("{ not json", encoding="utf-8")   # 锁文件损坏
    assert read_lock(skills_dir) == {"version": 1, "skills": {}}   # 当无锁
    assert verify_lock(skills_dir, res.name) == ("unlocked", "")   # 不把技能全锁死
    # 且能重新 record 修复
    record_lock(skills_dir, res.name, "o")
    assert verify_lock(skills_dir, res.name) == ("ok", "")
