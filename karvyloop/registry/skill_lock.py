"""registry/skill_lock.py — 第三方(untrusted)技能的**完整性锁**(借鉴 Multica 的 skills-lock.json)。

**为什么**:导入第三方技能时我们盖了 `signature`(sha1[:16] 写进 SKILL.md frontmatter),但那只是**在场戳**、
从不重算比对 —— 导入之后有人改了 SKILL.md、或(更危险的)沙箱里真正跑的 `scripts/`,旧 signature 照样过。
这是一道供应链完整性缺口:第三方是 untrusted 代码,它在你机器上不该能"神不知鬼不觉地变了"。

**做什么**(**只做完整性锁,不做全网技能市场** —— 跨用户生态/分发已否决):
- 一个集中、可审计的锁文件 `<skills_dir>/skills-lock.json`,每条 untrusted 技能记 `origin` + 内容 `contentHash`。
- hash 覆盖**整个技能目录**(SKILL.md + scripts + assets)——因为真正在沙箱里执行的是 scripts,只锁 SKILL.md 不够。
  用**全长 sha256**(旧 signature 是截断 sha1[:16],碰撞面大)。
- **加载前校验**:untrusted 技能 hash 对不上锁 → **fail-loud、拒绝加载**(篡改/损坏检出);绝不静默把改过的第三方代码
  喂给沙箱。这与"第三方沙箱收口 = 安全核心"同向:沙箱把代码关进笼子,锁保证笼子里关的还是你当初放进去那只。

锁文件坏了 → fail-safe 当作"无锁"(不误杀历史导入,靠下次 record 重建),而不是把所有技能锁死。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOCK_NAME = "skills-lock.json"


def content_hash(skill_dir: Path) -> str:
    """技能目录的确定性 sha256:按相对路径排序,逐文件喂 (rel, size, bytes)。覆盖 SKILL.md + scripts + assets。"""
    skill_dir = Path(skill_dir)
    h = hashlib.sha256()
    files = sorted(
        (f for f in skill_dir.rglob("*") if f.is_file() and not f.is_symlink()),
        key=lambda f: f.relative_to(skill_dir).as_posix(),
    )
    for f in files:
        rel = f.relative_to(skill_dir).as_posix()
        data = f.read_bytes()
        h.update(rel.encode("utf-8")); h.update(b"\0")
        h.update(str(len(data)).encode("ascii")); h.update(b"\0")
        h.update(data); h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _lock_path(skills_dir: Path) -> Path:
    return Path(skills_dir) / _LOCK_NAME


def read_lock(skills_dir: Path) -> dict:
    p = _lock_path(skills_dir)
    if not p.exists():
        return {"version": 1, "skills": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("skills"), dict):
            return {"version": 1, "skills": {}}
        return d
    except Exception:
        return {"version": 1, "skills": {}}   # 坏锁文件 → 当作无锁(fail-safe:不误杀,靠 record 重建)


def write_lock(skills_dir: Path, data: dict) -> None:
    _lock_path(skills_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_lock(skills_dir: Path, name: str, origin: str = "", *, now: Optional[float] = None) -> str:
    """导入完成后调用:算目录 hash、写进锁。返回 hash。"""
    skills_dir = Path(skills_dir)
    ch = content_hash(skills_dir / name)
    d = read_lock(skills_dir)
    d.setdefault("skills", {})[name] = {
        "origin": origin or "",
        "contentHash": ch,
        "lockedAt": now if now is not None else time.time(),
    }
    write_lock(skills_dir, d)
    return ch


def verify_lock(skills_dir: Path, name: str) -> tuple[str, str]:
    """校验一条技能。返回 (status, detail):
    - ``ok``       hash 与锁一致。
    - ``mismatch`` 锁里有、但当前 hash 变了 → **篡改/损坏**,调用方应拒绝加载/执行。
    - ``unlocked`` 锁里没有(旧导入 / 未锁)→ 允许,但可标记(不误杀历史)。
    """
    skills_dir = Path(skills_dir)
    d = read_lock(skills_dir)
    ent = (d.get("skills") or {}).get(name)
    if not ent or not ent.get("contentHash"):
        return ("unlocked", "")
    cur = content_hash(skills_dir / name)
    if cur == ent["contentHash"]:
        return ("ok", "")
    return ("mismatch", f"内容与锁不符(锁 {ent['contentHash'][:22]}… 现 {cur[:22]}…)——第三方技能被改动/损坏,已拒绝")


def remove_lock(skills_dir: Path, name: str) -> None:
    d = read_lock(skills_dir)
    if (d.get("skills") or {}).pop(name, None) is not None:
        write_lock(skills_dir, d)


def reject_tampered_untrusted(skills_dir: Path, name: str, raw: dict) -> bool:
    """所有"从盘装载技能"的路径共用的一道门:untrusted 第三方技能与锁不符 → True(调用方应跳过)。

    对抗验收揪出的洞:锁只接在 SkillIndex._scan_dir / run_skill_script,而 recall 的
    **三个扫盘兜底**(_load_skill_index / auto_suggest 兜底 / load_bound_skills 按名直取)
    绕过索引直接读盘 —— 被篡改的技能虽然进不了索引、跑不起来,SKILL.md body 却仍可能被
    带进召回上下文(提示注入面)。任何读盘装载点都该过这一道;非 untrusted / 锁没记录 → False(放行)。
    """
    if str((raw or {}).get("trust", "")).strip().lower() != "untrusted":
        return False
    try:
        status, detail = verify_lock(skills_dir, name)
    except Exception:
        return False   # 锁自身出错 → fail-safe 放行(与 read_lock 同调,不误杀)
    if status == "mismatch":
        logger.warning("完整性锁失败,拒绝装载被篡改的第三方技能:%s(%s)", name, detail)
        return True
    return False


__all__ = ["content_hash", "read_lock", "write_lock", "record_lock", "verify_lock",
           "remove_lock", "reject_tampered_untrusted", "_LOCK_NAME"]
