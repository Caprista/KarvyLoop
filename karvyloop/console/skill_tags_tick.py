"""console/skill_tags_tick.py — 技能语义标签**回填**(daily 慢侧 tick;P3-c)。

**为什么**:三层匹配 = 词面 grep/overlap + **LLM 语义标签重叠**(创建时打一次,无向量,
[[matching-is-grep-overlap-tags-no-vectors]])。召回已把 `tags:` 并进匹配集,但结晶热路径
(drive 内同步)不能调 LLM 打标 —— 跑评分离:打标是"不急、静心"的养护活,挂 daily 慢侧,
新结晶的技能下一轮 tick 自动补上。

**不打扰、不烧钱**(镜像 knowledge_tick 设计):
- 只看**没有 tags 的**技能(打过 = 天然 watermark,零 LLM);一轮封顶 MAX_TAG_PER_TICK 个。
- 抽空(LLM 给不出标签)记冷却,窗口内不反复烧同一个。
- **只回填自家技能**:`trust: untrusted` 第三方**跳过**(改它的 SKILL.md 会破完整性锁);
  包内 system 技能只读,也不动(它在包里,由发版带 tags)。
- 复用 cognition.concepts.extract_concepts_batch(严解析、宁空勿毒)。
状态落 `~/.karvyloop/skill_tags_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_TAG_PER_TICK = 20
EMPTY_COOLDOWN_S = 7 * 86400   # 抽空的技能,一周内别再烧


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "skill_tags_tick.json"


def _load_state(path: Optional[Path] = None) -> dict:
    p = path or _state_path()
    if not p.exists():
        return {"empty": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"empty": {}}
    except Exception:
        return {"empty": {}}


def _save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[skill_tags] 状态落盘失败(下轮重算): {e}")


def inject_tags(skill_md: Path, tags: list) -> bool:
    """把 `tags: [a, b]` 写进 SKILL.md frontmatter(已有 tags 键则不动;无 frontmatter 不动)。"""
    clean = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if not clean:
        return False
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)(.*)$", text, re.DOTALL)
    if not m:
        return False
    head, fm, close, body = m.groups()
    if re.search(r"^tags\s*:", fm, re.MULTILINE):
        return False   # 已有(含手写)→ 不覆盖
    line = "tags: [" + ", ".join(clean) + "]"
    skill_md.write_text(head + fm + "\n" + line + close + body, encoding="utf-8")
    return True


async def skill_tags_tick(app: Any, *, skills_dir: Optional[Path] = None,
                          state_path: Optional[Path] = None,
                          now: Optional[float] = None) -> dict:
    """每日慢侧给没标签的自家技能补语义标签一轮。返回 {ran, tagged, reason}。"""
    if now is None:
        now = time.time()
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ran": False, "tagged": 0, "reason": "无 gateway(--no-llm?)"}
    sd = Path(skills_dir) if skills_dir else (Path.home() / ".karvyloop" / "skills")
    if not sd.is_dir():
        return {"ran": False, "tagged": 0, "reason": "无技能目录"}

    from karvyloop.registry.skills import parse_frontmatter
    state = _load_state(state_path)
    todo: list = []   # (path, name, material)
    for p in sorted(sd.glob("*/SKILL.md")):
        try:
            fm, _ = parse_frontmatter(p)
        except OSError:
            continue
        if not fm.name or fm.tags:
            continue   # 打过 = watermark,零成本跳过
        if str((fm.raw or {}).get("trust", "")).strip().lower() == "untrusted":
            continue   # 第三方:改 SKILL.md 会破完整性锁 → 不回填(由导入方带标签)
        prev = (state.get("empty") or {}).get(fm.name)
        if prev is not None and now - float(prev) < EMPTY_COOLDOWN_S:
            continue   # 抽过但空 → 冷却窗内不再烧
        material = " ".join(x for x in (fm.description, fm.when_to_use) if x).strip()
        if material:
            todo.append((p, fm.name, material))
        if len(todo) >= MAX_TAG_PER_TICK:
            break
    if not todo:
        return {"ran": False, "tagged": 0, "reason": "没有待打标签的技能(watermark)"}

    from karvyloop.cognition.concepts import extract_concepts_batch
    from karvyloop.llm.token_ledger import token_source
    with token_source("skill_tags"):
        tag_lists = await extract_concepts_batch(
            [m for _, _, m in todo], gateway=gw, model_ref=rk.get("model_ref", ""))

    tagged = 0
    for (p, name, _), tags in zip(todo, tag_lists):
        if tags and inject_tags(p, tags):
            tagged += 1
        elif not tags:
            state.setdefault("empty", {})[name] = now   # 空结果记冷却
    _save_state(state, state_path)
    # 索引热更新:让新标签立刻进召回(重启也会 rebuild,双保险;失败不阻断)
    if tagged:
        try:
            idx = getattr(getattr(app.state, "main_loop", None), "skill_index", None)
            if idx is not None:
                idx.rebuild_from_disk(sd)
        except Exception:
            pass
    return {"ran": True, "tagged": tagged, "reason": ""}


__all__ = ["skill_tags_tick", "inject_tags", "MAX_TAG_PER_TICK", "EMPTY_COOLDOWN_S"]
