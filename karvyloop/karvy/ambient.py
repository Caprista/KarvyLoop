"""ambient — 工作台环境感知召回(⑤c,决策者环绕层的"料")。

用户在工作台干活(聊天/派活)时,把**相关的现成技能 / 已沉淀知识**主动浮出来
("你有个现成技能能干这个"/"知识库里有 N 条相关"),不用人主动搜。

硬契约:
- **零 LLM / 零向量**:纯确定性词面重叠打分,复用项目共享打分
  `context.relevance.overlap_score`(拉丁整词 + CJK bigram;知识召回 recall_block
  同款口径,不漂移)。技能侧把**已缓存**的 LLM 语义标签(frontmatter tags,daily
  慢侧打的)并进匹配文本 —— 三层匹配的语义层,只做集合并入,**不新调模型**。
  闲置 = 0 LLM 是硬契约(本模块不 import gateway)。
- **不打扰**:阈值(低分静默,宁静默勿噪音)+ 上限(一次最多 MAX_HITS 条)+
  冷却(同 intent 指纹 COOLDOWN_S 内不重复推)。
- **不挡热路径**:本模块纯同步纯本地(毫秒级);调用方(console/ws.py)
  fire-and-forget 并行触发,结果走 WS 广播,绝不阻塞 drive。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from karvyloop.context.relevance import overlap_score

# ---- 不打扰参数(依据见各行注释)----

# 一次最多浮几条(任务规格:上限 3 —— 是"料"不是刷屏)。
MAX_HITS = 3
# 归一化分数地板:query 至少 1/4 的词面单元被候选覆盖才推。
# recall(crystallize)"有交集即命中"是**内部注入**口径;ambient 直接推到人脸上,更严。
MIN_SCORE = 0.25
# 绝对共享单元下限:只共享 1 个通用词/bigram 不算信号(借 crystallize/cluster
# _MIN_SHARED=2 的既定理由:ratio 对短 query 太敏感,防"共享一个 python 就误推")。
MIN_SHARED = 2
# 同 intent 指纹冷却窗(任务规格:默认 10 分钟内不重复推同一信号)。
COOLDOWN_S = 600.0

_SUMMARY_MAX = 120


@dataclass
class AmbientHit:
    """一条环境召回命中(WS payload 的单元)。"""
    kind: str      # "skill" | "belief"
    id: str        # 入口 id:技能=name(API 按名寻址),知识=provenance.id 或 content 哈希
    name: str      # 展示名
    summary: str   # 一句话(技能=description/when_to_use,知识=内容截断)
    score: float   # 归一化重叠分 0..1(相对 query 词面单元)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "name": self.name,
                "summary": self.summary, "score": round(self.score, 3)}


def intent_fingerprint(text: str) -> str:
    """intent 指纹(冷却键):经 crystallize 既有 `_intent_cluster` 归一
    (同义词/月份/停用词,取前 5 个特征词)→ 换说法的同一 intent 落同一指纹。
    归一后为空(纯符号等)→ 退回小写原文,仍可去重。零 LLM。"""
    from karvyloop.crystallize.signature import _intent_cluster
    norm = _intent_cluster(text or "") or (text or "").strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


class AmbientCooldown:
    """同信号冷却表:同 intent 指纹在 ttl 窗口内只推一次。纯内存(重启清零,可接受:
    冷却是"别烦人"的礼貌,不是账)。"""

    def __init__(self, ttl_s: float = COOLDOWN_S) -> None:
        self._ttl = float(ttl_s)
        self._last: dict[str, float] = {}

    def allow(self, fingerprint: str, *, now: Optional[float] = None) -> bool:
        ts = self._last.get(fingerprint)
        if ts is None:
            return True
        _now = time.time() if now is None else now
        return (_now - ts) >= self._ttl

    def mark(self, fingerprint: str, *, now: Optional[float] = None) -> None:
        _now = time.time() if now is None else now
        if len(self._last) > 512:   # 有界:顺手清过期,防指纹表无限膨胀
            self._last = {k: v for k, v in self._last.items() if (_now - v) < self._ttl}
        self._last[fingerprint] = _now


def _one_line(text: str, max_len: int = _SUMMARY_MAX) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _skill_candidates(skill_index, skills_dir: Optional[Path],
                      scope: str) -> Iterator[tuple[str, str, str, str]]:
    """技能候选:(id, name, 匹配文本, 一句话)。索引优先(不读盘);无索引走
    crystallize.recall 的扫盘兜底(同一装载门:篡改的 untrusted 技能进不来)。"""
    if skill_index is not None and len(skill_index) > 0:
        for e in skill_index.all():
            if e.scope != scope:
                continue
            hay = " ".join([e.name, e.when_to_use or "", e.description or "",
                            " ".join(e.tags or ())])
            yield e.name, e.name, hay, _one_line(e.description or e.when_to_use or e.name)
        return
    if skills_dir is None:
        return
    from karvyloop.crystallize.recall import _load_skill_index
    for c in _load_skill_index(Path(skills_dir)):
        if c.get("scope") != scope:
            continue
        raw = c.get("raw") or {}
        when = str(raw.get("when_to_use") or raw.get("when-to-use") or "")
        desc = str(raw.get("description") or "")
        tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        hay = " ".join([c["name"], when, desc, " ".join(str(t) for t in tags)])
        yield c["name"], c["name"], hay, _one_line(desc or when or c["name"])


def _belief_candidates(memory, scope: str, domain: str) -> Iterator[tuple[str, str, str, str]]:
    """知识候选:(id, name, 匹配文本, 一句话)。域过滤与 MemoryManager.recall_block
    同一口径(§2.6:带 applies.domain 的私有认知只在本域浮出,跨域不漏)。"""
    if memory is None:
        return
    index = getattr(memory, "index", None)
    if index is None:
        return
    seen: set[int] = set()
    for b in index.all(scope):
        if id(b) in seen:   # MemoryIndex 双 key 存同一对象 → 去重(同 recall_block 的坑)
            continue
        seen.add(id(b))
        bd = (b.provenance.get("applies") or {}).get("domain", "") if b.provenance else ""
        if bd and bd != domain:
            continue
        content = getattr(b, "content", "") or ""
        if not content:
            continue
        bid = str((b.provenance or {}).get("id") or
                  hashlib.sha1(content.encode("utf-8")).hexdigest()[:12])
        yield bid, _one_line(content, 40), content, _one_line(content)


def ambient_recall(
    context_text: str,
    *,
    skill_index=None,
    skills_dir: Optional[Path] = None,
    memory=None,
    skill_scope: str = "user",
    belief_scope: str = "personal",
    domain: str = "",
    limit: int = MAX_HITS,
    min_score: float = MIN_SCORE,
    min_shared: int = MIN_SHARED,
    cooldown: Optional[AmbientCooldown] = None,
    now: Optional[float] = None,
) -> list[AmbientHit]:
    """环境感知召回:输入当前 intent(可拼近几轮摘要),输出 ≤limit 条命中
    (技能 kind=skill + 知识 kind=belief),按分数降序。低分/冷却中 → [](静默)。

    打分 = `overlap_score(query, 候选文本)`(拉丁整词 + CJK bigram 命中数),
    归一化分母 = `overlap_score(query, query)`(query 自身的词面单元总数,纯复用
    共享打分,不另造 tokenizer)。零 LLM、零向量、零网络。
    """
    text = (context_text or "").strip()
    if not text:
        return []
    denom = overlap_score(text, text)
    if denom <= 0:
        return []

    fp = intent_fingerprint(text)
    if cooldown is not None and not cooldown.allow(fp, now=now):
        return []   # 冷却中:同一信号 10 分钟内推过 → 静默

    hits: list[AmbientHit] = []

    def _score_into(kind: str, cands: Iterator[tuple[str, str, str, str]]) -> None:
        for cid, name, hay, summary in cands:
            raw = overlap_score(text, hay)
            if raw < min_shared:        # 只共享 1 个通用单元 ≠ 信号
                continue
            score = raw / denom
            if score < min_score:       # 低分静默,宁静默勿噪音
                continue
            hits.append(AmbientHit(kind=kind, id=cid, name=name,
                                   summary=summary, score=min(1.0, score)))

    try:
        _score_into("skill", _skill_candidates(skill_index, skills_dir, skill_scope))
    except Exception:
        pass   # 技能侧失败不拖垮知识侧(ambient 是锦上添花,绝不 fail-loud 到用户)
    try:
        _score_into("belief", _belief_candidates(memory, belief_scope, domain))
    except Exception:
        pass

    hits.sort(key=lambda h: h.score, reverse=True)
    hits = hits[: max(0, limit)]
    if hits and cooldown is not None:
        cooldown.mark(fp, now=now)   # 只在真推了才记冷却:空结果不占窗口
    return hits


__all__ = ["AmbientHit", "AmbientCooldown", "ambient_recall", "intent_fingerprint",
           "MAX_HITS", "MIN_SCORE", "MIN_SHARED", "COOLDOWN_S"]
