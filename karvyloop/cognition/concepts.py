"""cognition/concepts.py — 给 Belief 抽核心概念/实体(LLM Wiki 式),做认知图谱的**语义边**。

Hardy 选 B(LLM 抽概念 + wiki 互链,而非 embedding/向量调参——后者是已否决方向)。参照卡帕西
《知识自生长》= LLM Wiki:把知识**编译**成概念页/实体页 + `[[互链]]`(编译不是检索)。

本模块:① `extract_concepts_batch` —— 一次 LLM 调用给一批 Belief 各抽 2-4 个概念(严解析、宁空勿毒);
② `ConceptCache` —— content-hash → concepts 持久化(**编译一次、高效运行**:抽过的看图时零 LLM);
③ `tag_beliefs` —— **写入路径**批量打标(#61 研判①:ingest/auto_distill 写完新条后与 supersede
   同节奏抽一次入缓存;召回种子的语义标签层读的就是它,打字热路径零 LLM 铁律不动)。
图怎么连见 graph.concept_graph(共享概念=语义边);召回怎么用见 spread(标签进种子+边)。
标签就是把"语义相似"预计算成可 grep 的词面(创建时打一次,查询时纯词面匹配,无向量)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array


_CONCEPT_INSTRUCTION = (
    "为下面每条知识抽 2-4 个**核心概念/实体**(像 wiki 的概念页/实体页:人 / 项目 / 技术 / 主题 / 偏好)。"
    "**严格只输出一个 JSON 二维数组**,每条对应一个字符串数组,**顺序和条数与输入完全一致**,别的话都不要。"
    '示例:输入 2 条 → [["Python","后端"],["周报","自动化"]]'
)


async def extract_concepts_batch(contents: list, *, gateway, model_ref: str = "") -> list:
    """一次调用给一批知识各抽概念。返与 contents 等长的 list[list[str]];解析失败 → 全空(不投毒)。"""
    if not contents:
        return []
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    numbered = "\n".join(f"{i + 1}. {(c or '').strip()}" for i, c in enumerate(contents))
    numbered, _ = clip_to_tokens(numbered, LLM_MATERIAL_TOKENS)   # 基建天花板(防一批超大内容爆上下文)
    out = ""
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gateway.complete([{"role": "user", "content": numbered}], [], ref,
                                         system=SystemPrompt(static=[_CONCEPT_INSTRUCTION])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        return [[] for _ in contents]
    try:
        arr = json.loads(_extract_json_array(out))
        if (isinstance(arr, list) and len(arr) == len(contents)
                and all(isinstance(x, list) for x in arr)):
            # 严格:只收字符串、每条封顶 6 个、去空
            return [[str(c).strip() for c in x if isinstance(c, str) and str(c).strip()][:6] for x in arr]
    except Exception:
        pass
    return [[] for _ in contents]   # 长度/类型对不上 → 全空(回退词面,绝不投毒)


def _hash(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]


_TAG_MEMO_CAP = 65536   # tags_for 的内存 memo 上界(防长期运行下极端内容量把 memo 撑爆)


class ConceptCache:
    """content-hash → [concepts] 持久化(原子写)。抽过不再抽(编译一次)。"""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: Optional[dict] = None
        # 召回热路径 memo:content 串 → 标签(Python 串 hash 有内建缓存,重复查询免重复 sha1)
        self._tag_memo: dict = {}

    def _load(self) -> dict:
        if self._mem is None:
            try:
                d = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
                self._mem = d if isinstance(d, dict) else {}
            except Exception:
                self._mem = {}
        return self._mem

    def resolve(self, contents: list):
        """返 (concepts 对齐列表[未命中=None], 未命中的 idx 列表)。"""
        cache = self._load()
        concepts, missing = [], []
        for i, c in enumerate(contents):
            hit = cache.get(_hash(c))
            if isinstance(hit, list):
                concepts.append(hit)
            else:
                concepts.append(None)
                missing.append(i)
        return concepts, missing

    def tags_for(self, content: str) -> list:
        """**只读零 LLM**:content → 缓存标签(没抽过 → [],调用方退回词面)。
        召回热路径(recall_block → spread 种子)每次查全库都过这里 —— memo 化,
        同一 content 不重复 sha1(万条级每次召回省 ~10ms)。"""
        hit = self._tag_memo.get(content)
        if hit is None:
            raw = self._load().get(_hash(content))
            hit = [str(t).strip() for t in raw if str(t).strip()] if isinstance(raw, list) else []
            if len(self._tag_memo) >= _TAG_MEMO_CAP:
                self._tag_memo.clear()   # 极端量下整清重建(memo 是加速器不是状态)
            self._tag_memo[content] = hit
        return hit

    def put(self, content: str, concepts: list) -> None:
        cache = self._load()
        cache[_hash(content)] = list(concepts or [])
        self._tag_memo.pop(content, None)   # memo 失效:下次 tags_for 读到新标签
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass


async def tag_beliefs(beliefs: list, *, cache: ConceptCache, gateway, model_ref: str = "") -> int:
    """写入路径的标签预计算(#61 研判①a):给刚写入的 Belief 批量抽概念标签入缓存。

    - 与 supersede 同节奏:在 ingest/auto_distill 的**写入侧异步路径**里调,不占打字热路径。
    - 缓存已有的跳过(watermark,零 LLM);一次 batch 调用判整批。
    - **抽空/失败不落缓存**(宁缺勿错标):留给 daily 慢侧回填重试(belief_tags_tick)。
    返回新打上标签的条数。任何异常调用方自吞(标签是增益不是命脉,写入主流程不因它挂)。
    """
    if not beliefs or cache is None or gateway is None:
        return 0
    contents = [getattr(b, "content", "") or "" for b in beliefs]
    _, missing = cache.resolve(contents)
    todo = [i for i in missing if contents[i].strip()]
    if not todo:
        return 0
    tag_lists = await extract_concepts_batch([contents[i] for i in todo],
                                             gateway=gateway, model_ref=model_ref)
    tagged = 0
    for k, i in enumerate(todo):
        ts = tag_lists[k] if k < len(tag_lists) else []
        if ts:
            cache.put(contents[i], ts)
            tagged += 1
    return tagged


__all__ = ["extract_concepts_batch", "ConceptCache", "tag_beliefs"]
