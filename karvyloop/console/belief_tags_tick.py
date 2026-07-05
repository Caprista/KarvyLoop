"""console/belief_tags_tick.py — 知识库概念标签**回填**(daily 慢侧 tick;#61 研判①b)。

**为什么**:召回种子的语义标签层(spread 三层种子)读 ConceptCache 的**预计算**标签——
新条在写入路径打(ingest/auto_distill → tag_beliefs),但存量老条没标签只能退回纯词面
(同义改写召不回)。本 tick 把存量补齐:老库渐进增强,补到哪儿哪儿的同义改写就活了。

**不打扰、不烧钱**(镜像 skill_tags_tick 设计):
- 只看**缓存里没有的**条(缓存命中 = 天然 watermark,零 LLM);一轮封顶 MAX_TAG_PER_TICK。
- 抽空(LLM 给不出标签)记冷却,窗口内不反复烧同一条;失效条(invalid_at)不烧(召回不看它)。
- 复用 cognition.concepts.extract_concepts_batch(严解析、宁空勿毒:解析失败全空不投毒)。
状态落 `~/.karvyloop/belief_tags_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_TAG_PER_TICK = 40
EMPTY_COOLDOWN_S = 7 * 86400   # 抽空的条,一周内别再烧


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "belief_tags_tick.json"


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
        logger.warning(f"[belief_tags] 状态落盘失败(下轮重算): {e}")


def _key(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]


async def belief_tags_tick(app: Any, *, state_path: Optional[Path] = None,
                           now: Optional[float] = None) -> dict:
    """每日慢侧给没标签的存量 Belief 补概念标签一轮。返回 {ran, tagged, reason}。"""
    if now is None:
        now = time.time()
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    cc = getattr(mem, "concept_cache", None) if mem is not None else None
    if cc is None:
        cc = getattr(app.state, "concept_cache", None)
    if mem is None or gw is None or cc is None:
        # reason 复用既有 i18n key(concept_cache 属 memory 接线的一部分,不另造新串)
        return {"ran": False, "tagged": 0, "reason": "memory/gateway 未接(--no-llm?)"}

    # 候选:两 scope 全量、仍有效、内容非空(index 双 key 去重 by id,同 recall_block 的坑)
    beliefs, seen = [], set()
    for sc in ("personal", "domain"):
        for b in mem.index.all(sc):
            if id(b) in seen:
                continue
            seen.add(id(b))
            if getattr(b, "invalid_at", None) is not None:
                continue   # 失效条召回不看 → 不给它烧标签
            if (getattr(b, "content", "") or "").strip():
                beliefs.append(b)

    contents = [b.content for b in beliefs]
    _, missing = cc.resolve(contents)   # 缓存命中 = watermark,零 LLM 跳过
    state = _load_state(state_path)
    empty = state.get("empty") or {}
    todo: list[str] = []
    for i in missing:
        prev = empty.get(_key(contents[i]))
        if prev is not None and now - float(prev) < EMPTY_COOLDOWN_S:
            continue   # 抽过但空 → 冷却窗内不再烧
        todo.append(contents[i])
        if len(todo) >= MAX_TAG_PER_TICK:
            break
    if not todo:
        # 动态 reason(带真实覆盖数):走 tBackend 前缀回退,不入静态表(库空时 0/0 也归这)
        done = len(contents) - len(missing)
        return {"ran": False, "tagged": 0,
                "reason": f"没有待打标签的知识(watermark,缓存已覆盖 {done}/{len(contents)} 条)"}

    from karvyloop.cognition.concepts import extract_concepts_batch
    from karvyloop.llm.token_ledger import token_source
    with token_source("belief_tags"):
        tag_lists = await extract_concepts_batch(
            todo, gateway=gw, model_ref=rk.get("model_ref", ""))

    tagged = 0
    for content, tags in zip(todo, tag_lists):
        if tags:
            cc.put(content, tags)   # 落缓存:下一次召回/图谱/supersede 立刻可用
            tagged += 1
        else:
            state.setdefault("empty", {})[_key(content)] = now   # 空结果记冷却
    _save_state(state, state_path)
    return {"ran": True, "tagged": tagged, "reason": ""}


__all__ = ["belief_tags_tick", "MAX_TAG_PER_TICK", "EMPTY_COOLDOWN_S"]
