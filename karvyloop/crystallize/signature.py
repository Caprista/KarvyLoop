"""签名归一化 — 判"是否同一能力"（crystallize/signature.py）。

规格：docs/modules/crystallize.md §3 signature.py + §4 保守可逆。

签名的三要素(spec §3):
  - intent cluster  意图语义聚类
  - schema shape    输入结构形状
  - tool set        用到的工具集

M1.5 升级(v1 → v1.1 归一化):
  - intent cluster 不再只用"去数字",还要做:
      · 月份名归一(jan/feb/.../dec + 一月/二月/... → 统一 token "month")
      · 简易同义词归一(synonym 表,小但精准;M1.5 v1 显式白名单,
        不引外部词向量库 —— 与 cognition "无向量库" 一致)
      · 去停用词(几个中文/英文常见功能词)
  - schema shape: 保留原 key 路径(结构信息),但**值归一**(字符串按长度分桶,
    数字按数量级分桶)—— 让 "2026-01" 和 "2026-05" 看上去更像
  - tool set: 排序拼接(原 v1)

输出:sha256 前 16 hex(碰撞概率 1/2^64,M1.5 v1 接受)

**保守可逆**:同 signature 才合并候选;不同即视为不同能力(spec §4 严于通用做法)。
用真实数据后,可在 strict 之上加 `expand_signature` 试探(模糊合并,P1 范畴;
此处接口先留)。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from karvyloop.schemas import AtomRun


# ---- 文本归一工具 ----

_INTENT_STOP_PUNCT = re.compile(r"[^\w\s一-鿿]+")
_INTENT_STOP_WS = re.compile(r"\s+")

# 英文月份名 → "month" token
_EN_MONTHS = {
    "jan", "january", "feb", "february", "mar", "march",
    "apr", "april", "may", "jun", "june",
    "jul", "july", "aug", "august", "sep", "sept", "september",
    "oct", "october", "nov", "november", "dec", "december",
}
# 中文月份名 → "month" token
_CN_MONTHS = {
    "一月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "十一月", "十二月",
    "月份",
}
# 简易同义词表(M1.5 v1 白名单,后续可接 LLM 抽取扩展)
_SYNONYMS = {
    "summarize": "summarize", "summary": "summarize", "report": "summarize",
    "汇报": "summarize", "整理": "summarize", "总结": "summarize",
    "translate": "translate", "翻译": "translate", "译": "translate",
    "search": "search", "find": "search", "搜": "search", "查找": "search",
    "fetch": "fetch", "get": "fetch", "下载": "fetch", "抓": "fetch", "拉": "fetch",
    "rewrite": "rewrite", "改写": "rewrite", "重写": "rewrite", "refactor": "rewrite",
    "test": "test", "tests": "test", "测试": "test", "测": "test",
    "delete": "delete", "remove": "delete", "删除": "delete", "删": "delete",
}
# 停用词(过滤功能词,不看是否同义)
_STOPWORDS = frozenset({
    "a", "an", "the", "for", "to", "of", "in", "on", "with", "and", "or", "please",
    "请", "一下", "给我", "帮我", "的", "把",
})


def _normalize_token(tok: str) -> str | None:
    """单 token 归一:
      1. 小写
      2. 月份名(中/英)→ "month"
      3. 同义词表命中 → 规范词
      4. 停用词 → None(过滤)
      5. 纯数字 → None(数字本身不算能力特征)
    """
    t = tok.lower().strip()
    if not t:
        return None
    if t in _EN_MONTHS or t in _CN_MONTHS:
        return "month"
    if t in _STOPWORDS:
        return None
    if t.isdigit():
        return None
    if t in _SYNONYMS:
        return _SYNONYMS[t]
    return t


def _intent_cluster(intent: str) -> str:
    """意图语义聚类 v1.1:归一 token + 取前 5 个非停用词。"""
    if not intent:
        return ""
    s = intent.lower()
    s = _INTENT_STOP_PUNCT.sub(" ", s)
    s = _INTENT_STOP_WS.sub(" ", s).strip()
    if not s:
        return ""
    raw_tokens: list[str] = []
    for w in s.split():
        raw_tokens.append(w)
    normalized = [_normalize_token(w) for w in raw_tokens]
    normalized = [t for t in normalized if t]
    if not normalized:
        return ""
    seen: set[str] = set()
    uniq: list[str] = []
    for t in normalized:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " ".join(uniq[:5])


# ---- schema shape(结构) + 值归一(让 "2026-01" / "2026-05" 归一)----

def _value_bucket(v: Any) -> str:
    """值分桶:
      - 数字:按量级(<0、<1、<10、<100、<1k、<1m、其他)
      - 字符串:按长度(<4、<16、<64、其他)+ 形态(isodate / url / other)
      - bool: 'bool'
      - None: 'null'
      - dict / list: 'obj' / 'arr'
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        if v < 0:
            return "num<0"
        if v < 1:
            return "num<1"
        if v < 10:
            return "num<10"
        if v < 100:
            return "num<100"
        if v < 1000:
            return "num<1k"
        if v < 1_000_000:
            return "num<1m"
        return "num+"
    if isinstance(v, str):
        L = len(v)
        if L == 0:
            return "str<>"
        bucket = "str<4" if L < 4 else "str<16" if L < 16 else "str<64" if L < 64 else "str+"
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", v.strip()):
            bucket = "isodate:" + bucket
        elif re.fullmatch(r"\d{4}-W\d{1,2}", v.strip()):
            bucket = "isoweek:" + bucket
        elif re.match(r"^https?://", v):
            bucket = "url:" + bucket
        return bucket
    if isinstance(v, dict):
        return "obj"
    if isinstance(v, list):
        return "arr"
    return type(v).__name__


def _canonical_schema(obj: Any, prefix: str = "") -> list[str]:
    """输入结构形状 + 值分桶:
    例:{"month":"2026-01","limit":10} → ["month:isodate:str<16","limit:num<100"]
    """
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}{k}"
            if isinstance(v, dict):
                sub = _canonical_schema(v, f"{full_key}.")
                if sub:
                    out.extend(sub)
                else:
                    out.append(f"{full_key}:obj")
            elif isinstance(v, list):
                if v:
                    sub = _canonical_schema(v[0], f"{full_key}[].")
                    if sub:
                        out.extend(sub)
                    else:
                        out.append(f"{full_key}[]:" + _value_bucket(v[0]))
                else:
                    out.append(f"{full_key}[]:arr")
            else:
                out.append(f"{full_key}:" + _value_bucket(v))
    return out


def _tool_set(run: AtomRun) -> tuple[str, ...]:
    """run 用到的工具集(去重、排序)。"""
    names = {tc.get("name", "") for tc in (run.tool_calls or [])}
    names.discard("")
    return tuple(sorted(names))


def compute_signature(run: AtomRun) -> str:
    """计算结晶签名(= 累积/结晶身份)。

    payload: `<intent_part>|<schema_buckets joined by ,>` 的 sha256 前 16 hex。

    **9.4 门1 真机修正(用户拍板"结晶宽松、召回严格")**:
    签名**不再含工具集**。门1 在 Linux+真 MiniMax 上抓到:同一个任务重复跑,LLM
    每次工具路径都不同(write_file 被 HR-4 拦 → 改 cat heredoc … 每次不一样),
    工具集进签名 → 同一任务碎成 N 个签名各 usage=1 → 永远到不了结晶门槛(5)→
    "用够了自动结晶"的命题在真机上不成立。

    取舍:
    - **结晶宽松**:累积/结晶身份只看 意图聚类 + 输入形状(不看执行路径)→ 同任务
      重复就攒得上 → 真能结晶。工具集是 LLM 的非确定性执行细节,不该决定"是不是同一能力"。
    - **召回严格**:recall(recall.py)本就**只按 intent token overlap** 匹配
      (执行前根本拿不到工具集)→ 召回精度不受本改动影响,仍按意图严格命中。
    （`_tool_set` 保留:trace/审计仍可记录用过哪些工具,只是不再进签名。）
    """
    intent = run.input.get("intent", "") if isinstance(run.input, dict) else ""
    intent_part = _intent_cluster(intent)
    schema_buckets = _canonical_schema(run.input)
    payload = "|".join([
        intent_part,
        ",".join(schema_buckets),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def same_signature(sig_a: str, sig_b: str) -> bool:
    """v1.1:严格相等即同签(同保守可逆原则)。"""
    return sig_a == sig_b and bool(sig_a)


def expand_signature(sig: str) -> list[str]:
    """M1.5 留口:模糊合并试探(同 sig 桶 + 相邻 intent token)。

    v1.1 默认 noop(返回 [sig]);P1 接 LLM 抽 embedding 之后再启用模糊合并。
    """
    return [sig] if sig else []


__all__ = [
    "compute_signature", "same_signature", "expand_signature",
    "_intent_cluster", "_canonical_schema", "_value_bucket",
]
