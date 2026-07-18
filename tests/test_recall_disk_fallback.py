"""test_recall_disk_fallback — 召回**磁盘兜底**(无 SkillIndex)不丢 result_reuse/source(docs/87 §六)。

病根:recall 无 SkillIndex 时走 _load_skill_index 扫盘重建候选 dict,只 copy
{name,body,path,all_tokens,raw,sig},**漏了 result_reuse 和 source**(loader 本就有)→
① result_reuse 恒 "dynamic"(stable 技能永不回放)② source 恒 "user"(user/system 破平失效)。

这两条只在 **disk-only**(skill_index=None / 空)路径复现 —— SkillIndex 路径本就带这两个字段。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402


def _write_skill(dir_, name, *, desc, when, result_reuse="dynamic", source=None,
                 scope="user", tags=(), body="# body\n## Steps\n1. x\n"):
    d = pathlib.Path(dir_) / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"description: {desc}", f"when_to_use: {when}",
             f"signature: sig-{name}", f"scope: {scope}", f"result_reuse: {result_reuse}"]
    if source is not None:
        lines.append(f"source: {source}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    (d / "SKILL.md").write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
    return d


def test_disk_fallback_preserves_result_reuse(tmp_path):
    """无 SkillIndex(磁盘兜底)命中 stable 技能 → hit.result_reuse == 'stable'(修前恒 dynamic)。"""
    _write_skill(tmp_path, "celsius-converter",
                 desc="convert celsius fahrenheit temperature degrees",
                 when="convert celsius fahrenheit temperature degrees",
                 result_reuse="stable")
    hit = recall("convert celsius to fahrenheit temperature degrees",
                 skills_dir=tmp_path, scope="user", skill_index=None)   # disk-only
    assert hit is not None and hit.name == "celsius-converter"
    assert hit.result_reuse == "stable", \
        f"磁盘兜底把 stable 技能当成了 {hit.result_reuse}(永不回放 bug)"


def test_disk_fallback_preserves_source_tiebreak(tmp_path):
    """无 SkillIndex 时 source 破平生效:意图/满意度/验据全平 → 用户技能胜随包 system 技能。

    修前磁盘兜底丢 source → 两条都当 'user'(user_rank 都 1.0)→ 破平失效,按扫盘顺序
    (dir 名排序)system 反而先被选中并留住 → 用户自己结晶的技能被随包技能截胡。
    """
    # dir 名让 system 技能排序在前(扫盘先处理它)→ 若破平失效,它会当选;破平生效则用户胜。
    _write_skill(tmp_path, "aaa-sys-temp", source="system",
                 desc="convert celsius fahrenheit temperature degrees",
                 when="convert celsius fahrenheit temperature degrees")
    _write_skill(tmp_path, "zzz-user-temp",   # source 缺省 = user
                 desc="convert celsius fahrenheit temperature degrees",
                 when="convert celsius fahrenheit temperature degrees")
    hit = recall("convert celsius to fahrenheit temperature degrees",
                 skills_dir=tmp_path, scope="user", skill_index=None)   # disk-only
    assert hit is not None
    assert hit.name == "zzz-user-temp", \
        f"source 破平失效:随包 system 技能截胡了用户技能(命中 {hit.name})"
