"""console/tag_merge_tick.py — 同义标签 daily 收敛(受控词表的慢侧半边;Hardy「反向标签」护栏③)。

**为什么**:LLM 自由打标必然产生同义碎片——"夜间模式" vs "深色主题"是两个标签,标签重叠匹配
互相看不见,语义层被切成孤岛。写入侧 reuse-first(concepts.assign_tags)治**增量**,本 tick 治
**存量**:同义候选(标签名词面重叠 + 二阶共现)→ 一次 LLM 判同义 → **自动合并进别名表**。

**为什么可以自动**(与 knowledge_tick 的"绝不自动合知识"不冲突):标签是**派生数据**不是用户
数据——合并动的是匹配视图(ConceptCache 别名表),不动任何 Belief 原文;老标签保留为 alias
继续可匹配、随时可手改别名表翻案。审计痕:别名表记 via/ts + Trace kind=tag_merged。

**不打扰、不烧钱**(watermark + 冷却形制,镜像 belief_tags_tick):
- 词表指纹 watermark:标签词表没变 → 零 LLM 跳过;有积压时只在候选**全部处理完**才落指纹
  (封顶 MAX_PAIRS_PER_TICK/轮,积压隔天接着收,不因 watermark 卡死)。
- 判过"不同义"的对子记冷却,窗口内不重复烧;判成同义的进别名表,天然不再成为候选。
- 候选生成零 LLM:① 标签名词面重叠(表面变体"深色主题/深色模式");② **二阶共现**——两标签
  从不同时出现在同一条上(同义标签几乎不共现:LLM 一条只挑一种说法),却共享共现邻居
  (都和"界面外观"一起出现过)。零词面交集的真同义靠②逮。
状态落 `~/.karvyloop/tag_merge_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIN_VOCAB = 6                 # 词表太小不值得烧(也没同义可收)
MAX_PAIRS_PER_TICK = 16       # 单轮最多问 LLM 几对(积压隔天接着收)
JUDGE_COOLDOWN_S = 14 * 86400  # 判过不同义的对子,两周内别再烧
_NEIGHBOR_GROUP_CAP = 8       # 单个共现邻居下最多取几个标签配对(防病态大组组合爆炸)

_SYNONYM_SYSTEM = (
    "你是 KarvyLoop 的标签词表管理员。下面每行是知识库标签表里的一对标签(带编号)。"
    "判断每对是否**同义**:指同一个概念、互换使用不改变含义(如「夜间模式」和「深色主题」)。"
    "只是相关/同领域(如「烘焙点心」和「厨房手艺」)**不算**同义;拿不准的不算。\n"
    "**严格只输出一个 JSON 数组**,列出同义对子的编号(整数);没有同义就输出 []。别的话都不要输出。"
)


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "tag_merge_tick.json"


def _load_state(path: Optional[Path] = None) -> dict:
    p = path or _state_path()
    if not p.exists():
        return {"vocab_hash": "", "judged": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"vocab_hash": "", "judged": {}}
        d.setdefault("vocab_hash", "")
        d.setdefault("judged", {})
        return d
    except Exception:
        return {"vocab_hash": "", "judged": {}}


def _save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[tag_merge] 状态落盘失败(下轮重算): {e}")


def _vocab_hash(vocab: dict) -> str:
    h = hashlib.sha1()
    for t in sorted(vocab):
        h.update(t.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _pair_key(a: str, b: str) -> str:
    return "||".join(sorted((a, b)))


def candidate_pairs(vocab: dict, tag_lists: list, judged: dict, now: float,
                    *, cap: int = MAX_PAIRS_PER_TICK) -> tuple[list, int]:
    """同义候选对(零 LLM)。返回 (对子列表[截断到 cap], 截断前总数)。

    ① 标签名词面重叠(共享 ≥1 个词/bigram):表面变体。
    ② 二阶共现:不直接共现(同义标签几乎不同时挂在一条上)但共享 ≥1 个共现邻居标签。
    直接共现的对子**排除**(LLM 一次输出里并列给出的两个标签,是它有意区分的两个概念)。
    冷却窗口内判过的不重复;排序:两边使用频次之和降序(合并高频标签收益最大)。
    """
    from karvyloop.cognition.graph import _tokens
    # 共现结构
    cooccur: dict = {}
    for row in tag_lists:
        for a in row:
            s = cooccur.setdefault(a, set())
            s.update(x for x in row if x != a)
    pairs: set = set()
    # ① 名字词面重叠(倒排 token → 标签,免 O(V²))
    by_tok: dict = {}
    for t in vocab:
        for tok in _tokens(t):
            by_tok.setdefault(tok, []).append(t)
    for group in by_tok.values():
        if len(group) < 2:
            continue
        g = sorted(group)[:_NEIGHBOR_GROUP_CAP]
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                pairs.add((g[i], g[j]) if g[i] < g[j] else (g[j], g[i]))
    # ② 二阶共现(倒排 邻居 → 挂着它的标签)
    by_neighbor: dict = {}
    for t, ns in cooccur.items():
        for nb in ns:
            by_neighbor.setdefault(nb, []).append(t)
    for group in by_neighbor.values():
        if len(group) < 2:
            continue
        g = sorted(group, key=lambda t: -vocab.get(t, 0))[:_NEIGHBOR_GROUP_CAP]
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                pairs.add((g[i], g[j]) if g[i] < g[j] else (g[j], g[i]))
    # 过滤:直接共现的 / 冷却窗内判过的
    out = []
    for a, b in pairs:
        if b in cooccur.get(a, ()):
            continue
        prev = judged.get(_pair_key(a, b))
        if prev is not None and now - float(prev) < JUDGE_COOLDOWN_S:
            continue
        out.append((a, b))
    out.sort(key=lambda p: (-(vocab.get(p[0], 0) + vocab.get(p[1], 0)), p))
    return out[:cap], len(out)


def parse_synonym_indices(text: str, n: int) -> list:
    """解析同义判定输出 → 合法编号列表。**宁空勿毒**:严格 JSON 数组、只收范围内整数、去重。"""
    from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array
    try:
        arr = json.loads(_extract_json_array(text or ""))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(arr, list):
        return []
    out, seen = [], set()
    for x in arr:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def tag_merge_tick(app: Any, *, state_path: Optional[Path] = None,
                         now: Optional[float] = None) -> dict:
    """每日慢侧同义标签收敛一轮。返回 {ran, merged, judged, reason}。"""
    if now is None:
        now = time.time()
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    cc = getattr(mem, "concept_cache", None) if mem is not None else None
    if cc is None:
        cc = getattr(app.state, "concept_cache", None)
    if gw is None or cc is None:
        return {"ran": False, "merged": 0, "judged": 0, "reason": "gateway/concept_cache 未接(--no-llm?)"}

    try:
        vocab = cc.vocabulary()
    except Exception as e:
        return {"ran": False, "merged": 0, "judged": 0, "reason": f"词表读取失败: {e}"}
    if len(vocab) < MIN_VOCAB:
        return {"ran": False, "merged": 0, "judged": 0,
                "reason": f"标签词表 < {MIN_VOCAB} 个,不值得收敛"}
    state = _load_state(state_path)
    vh = _vocab_hash(vocab)
    if vh == state.get("vocab_hash"):
        return {"ran": False, "merged": 0, "judged": 0,
                "reason": "标签词表没变(watermark),零 LLM 跳过"}

    todo, total = candidate_pairs(vocab, cc.tag_lists(), state.get("judged") or {}, now)
    if not todo:
        state["vocab_hash"] = vh   # 没候选 = 这版词表看完了
        _save_state(state, state_path)
        return {"ran": False, "merged": 0, "judged": 0, "reason": "没有同义候选对(词表指纹已记)"}

    lines = [f"{i}. 「{a}」 vs 「{b}」" for i, (a, b) in enumerate(todo)]
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=rk.get("model_ref", "") or None))
    except Exception:
        ref = rk.get("model_ref", "")
    try:
        with token_source("tag_merge"):
            async for ev in gw.complete([{"role": "user", "content": "\n".join(lines)}], [], ref,
                                        system=SystemPrompt(static=[_SYNONYM_SYSTEM])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[tag_merge] 同义判定调用失败(下轮重试,词表指纹不落): {e}")
        _save_state(state, state_path)
        return {"ran": False, "merged": 0, "judged": 0, "reason": f"LLM 调用失败: {e}"}

    syn = set(parse_synonym_indices(out, len(todo)))
    trace = getattr(getattr(app.state, "main_loop", None), "trace", None)
    merged = 0
    for i, (a, b) in enumerate(todo):
        if i in syn:
            # canonical = 使用更多的那个(平手按字典序,稳定);输方进别名表继续可匹配
            canonical, alias = (a, b) if (vocab.get(a, 0), a) >= (vocab.get(b, 0), b) else (b, a)
            if cc.add_alias(alias, canonical, via="tag_merge_tick", now=now):
                merged += 1
                if trace is not None:
                    try:
                        from karvyloop.cognition.concepts import TAG_VOCAB_TASK_ID
                        from karvyloop.cognition.trace import TraceEntry
                        trace.append(TraceEntry(
                            task_id=TAG_VOCAB_TASK_ID, kind="tag_merged",
                            payload={"alias": alias, "canonical": canonical,
                                     "count_alias": vocab.get(alias, 0),
                                     "count_canonical": vocab.get(canonical, 0)},
                            source="tag_merge_tick"))
                    except Exception:
                        pass
        else:
            state.setdefault("judged", {})[_pair_key(a, b)] = now   # 不同义 → 冷却
    if total <= len(todo):
        state["vocab_hash"] = vh   # 候选全处理完才落指纹;有积压 → 不落,隔天接着收
    _save_state(state, state_path)
    if merged:
        logger.info(f"[tag_merge] 同义标签收敛:并 {merged} 对进别名表(共判 {len(todo)} 对)")
    return {"ran": True, "merged": merged, "judged": len(todo), "reason": ""}


__all__ = ["tag_merge_tick", "candidate_pairs", "parse_synonym_indices",
           "MIN_VOCAB", "MAX_PAIRS_PER_TICK", "JUDGE_COOLDOWN_S"]
