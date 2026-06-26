"""Stage 3.5 文档 RAG 骨架 —— 关键词 TF + 段落切分。

**本拍只到 0.5**:不向量、不嵌入。
**拍 3.5 升级**:本地嵌入(Qwen 1.5B) + 向量索引(Chroma/FAISS)。

设计:docs/13 §3.4。
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
import re
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DocHit:
    """一条文档命中。"""
    path: str
    snippet: str
    score: float


def _split_paragraphs(text: str) -> list[str]:
    """按双换行切段(简单实用,**不**用 markdown 解析器)。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _tokenize(text: str) -> list[str]:
    """**简**单分词:小写 + 非字母数字切分。"""
    return re.findall(r"[a-z0-9一-鿿]+", text.lower())


def _tf_score(query_tokens: Sequence[str], para: str) -> float:
    """TF 评分(无 IDF 折扣,本拍 0.5 版本够用)。"""
    if not query_tokens:
        return 0.0
    para_tokens = _tokenize(para)
    if not para_tokens:
        return 0.0
    para_set = set(para_tokens)
    hits = sum(1 for qt in query_tokens if qt in para_set)
    return hits / len(query_tokens)


def doc_rag_search(
    query: str,
    docs_dir: str,
    top_k: int = 3,
    max_snippet_chars: int = 200,
) -> list[DocHit]:
    """TF 关键词搜索 docs_dir 下的所有 .md / .txt。

    返:top_k DocHit,按 score 倒序。
    0 命中 → 返空列表(不抛)。
    """
    base = pathlib.Path(docs_dir)
    if not base.exists():
        logger.debug("doc_rag_search: docs_dir %s 不存在 → 返空", docs_dir)
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    hits: list[DocHit] = []
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("doc_rag_search: read %s failed: %s", f, e)
            continue
        for para in _split_paragraphs(text):
            score = _tf_score(query_tokens, para)
            if score > 0:
                snippet = para if len(para) <= max_snippet_chars else para[:max_snippet_chars] + "…"
                hits.append(DocHit(path=str(f), snippet=snippet, score=score))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]
