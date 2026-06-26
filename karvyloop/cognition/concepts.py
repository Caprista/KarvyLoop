"""cognition/concepts.py — 给 Belief 抽核心概念/实体(LLM Wiki 式),做认知图谱的**语义边**。

Hardy 选 B(LLM 抽概念 + wiki 互链,而非 embedding/向量调参——后者是已否决方向)。参照卡帕西
《知识自生长》= LLM Wiki:把知识**编译**成概念页/实体页 + `[[互链]]`(编译不是检索)。

本模块:① `extract_concepts_batch` —— 一次 LLM 调用给一批 Belief 各抽 2-4 个概念(严解析、宁空勿毒);
② `ConceptCache` —— content-hash → concepts 持久化(**编译一次、高效运行**:抽过的看图时零 LLM)。
图怎么连见 graph.concept_graph(共享概念=语义边)。
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
    numbered = "\n".join(f"{i + 1}. {(c or '').strip()}" for i, c in enumerate(contents))
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


class ConceptCache:
    """content-hash → [concepts] 持久化(原子写)。抽过不再抽(编译一次)。"""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: Optional[dict] = None

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

    def put(self, content: str, concepts: list) -> None:
        cache = self._load()
        cache[_hash(content)] = list(concepts or [])
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass


__all__ = ["extract_concepts_batch", "ConceptCache"]
