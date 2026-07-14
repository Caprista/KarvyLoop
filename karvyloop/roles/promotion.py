"""roles/promotion — 兵法回流:域角色经验 → 镜像通用层(docs/78 §3,2026-07-13 Hardy 拍板)。

**一句话**:角色在某个域学到的**可泛化判断**(兵法),经「LLM 判泛化+脱敏改写 → 确定性
denylist 复检 → 攒批出卡人过目(ACCEPT 才升)」三道门,升为该角色的镜像资产——从此
跨域可用;将来对外三件套的可见面也只有这一层。

**为什么升层判定=脱密判定**:一条经验"剥掉域实体还成立吗"——答是,它就同时满足
"可进镜像"和"可出门"。但两个误判代价不同(泛化误判=噪音;脱密误判=泄露),所以脱密
侧不信单层:LLM 改写(一道)+ denylist grep(二道)+ H2A 签字(三道)。

**写入形态**(不造新概念,全复用 Belief;docs/78 §3.6):
- `provenance.source = "role_experience"`(它仍是角色经验,只是层不同);
- `provenance.applies = {"role": R}` —— **无 domain = 镜像层**(与 §2.6"空 domain=共享"同构);
- `provenance.origin = {"domain","belief_key","promoted_at"}` —— 不透明指针:能追回域内
  证据链(本机管理面解引用),证据内容**永不复制**上升;删域后指针悬空=诚实标注;
- 源条打 `provenance.promoted_to`(幂等:已升的不再进候选)。

行业对照(Q4,docs/78 §2):Generative Agents 的 reflection(周期异步把具体记忆合成
高层抽象插回记忆流)是"经验→泛化判断"的原型;差异:它无隔离概念,我们的升层跨隔离
边界,必须加脱敏门。宁缺勿滥/宁空勿毒纪律同 ingest/decision_pref。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

from karvyloop.roles.experience import ROLE_EXPERIENCE_SOURCE, _KINDS
from karvyloop.schemas.cognition import Belief

logger = logging.getLogger(__name__)

# ---- 常数(P1 纪律:先记分布、内测真数据标定,别臆测;docs/78 §3.2/§7)----
MIN_AGE_DAYS = 3.0        # 刚沉淀的先在域内考验几天
REQUIRE_RECALL = True     # 没被召回用过的经验不值得升(usage 信号)
MAX_PER_CARD = 8          # 一张攒批卡封顶(同 knowledge_tick 的 MAX_STALE_PER_CARD 口径)

PROMOTION_SYSTEM = (
    "你是 KarvyLoop 的经验升华器。输入是某个角色在**某个业务域**里沉淀的若干条经验,"
    "每条带 origin_key。你做两件事,**宁缺勿滥**:\n"
    "1) 判:这条经验剥掉域里的具体实体(项目名/客户名/公司名/产品名/具体数字/域名),"
    "对同一角色在**任何**域还成立吗?只对这个域成立的(如'这个域的 API 分页从 0 开始')"
    "直接丢弃,不要输出。\n"
    "2) 改写:判是的,改写成**无域实体的一般方法**——输出必须自足、脱离域上下文也读得懂、"
    "对没进过这个域的人也可执行。改写是翻译成一般方法,绝不是复制换标:"
    "'给X地产的报告要先过李工审'→'交付前先过一道内部专业审'是对的;原文照抄是错的。\n"
    "严格输出 JSON 数组 [{\"origin_key\",\"content\",\"kind\"(method|preference|pitfall),"
    "\"why\"(一句:为什么泛化)}];没有可升的就输出 []。不要输出 JSON 以外任何文字。"
)

_PROMOTION_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "origin_key": {"type": "string"},
            "content": {"type": "string"},
            "kind": {"type": "string", "enum": ["method", "preference", "pitfall"]},
            "why": {"type": "string"},
        },
        "required": ["origin_key", "content"],
    },
}


def origin_key_for(content: str) -> str:
    """源条的稳定指针 = 内容寻址(sha1 前 16 hex;同 mesh sync_id 心智,不含内容)。"""
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]


# ---- 候选圈选(零 LLM 预筛,全用现成字段;docs/78 §3.2)----


def promotion_candidates(beliefs: list, *, now: Optional[float] = None,
                         min_age_days: float = MIN_AGE_DAYS,
                         require_recall: bool = REQUIRE_RECALL) -> list:
    """圈出可考虑升层的域角色经验:带 applies.domain 的 role_experience 且
    ①未失效 ②存活 ≥N 天 ③真被召回用过 ④未标 promoted_to(幂等)。空候选 → 零 LLM。"""
    if now is None:
        now = time.time()
    out = []
    for b in beliefs:
        prov = getattr(b, "provenance", None) or {}
        if prov.get("source") != ROLE_EXPERIENCE_SOURCE:
            continue
        ap = prov.get("applies") or {}
        if not ap.get("domain") or not ap.get("role"):
            continue                    # 无域 = 已是镜像层/形态不合,不进候选
        if getattr(b, "invalid_at", None) is not None:
            continue                    # 被 supersede 推翻的经验不升(没躲过域内考验)
        if prov.get("promoted_to"):
            continue                    # 已升过(幂等标记)
        try:
            ts = float(prov.get("ts") or getattr(b, "freshness_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0 or (now - ts) < min_age_days * 86400:
            continue                    # 太新:先在域内考验
        if require_recall and int(getattr(b, "recall_count", 0) or 0) < 1:
            continue                    # 没人用过的经验不值得升
        out.append(b)
    return out


# ---- 确定性 denylist 复检(LLM 之外的地板;docs/78 §3.4)----


_ASCII_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK = re.compile(r"[一-鿿]")


def _terms(s: str) -> set[str]:
    """一段文本的词面项:ASCII 词(小写)+ CJK 连续段的 2 字滑窗(与召回同心智,无向量)。"""
    s = s or ""
    out: set[str] = {w.lower() for w in _ASCII_WORD.findall(s)}
    run = ""
    for ch in s + "\0":
        if _CJK.match(ch):
            run += ch
        else:
            for i in range(len(run) - 1):
                out.add(run[i:i + 2])
            run = ""
    return out


def denylist_terms(domain_id: str, domain_name: str = "") -> set[str]:
    """域身份词面(id + 显示名的词/bigram)。改写产物命中任何一项 → 该条丢弃。

    诚实边界(docs/78 §7):slice1 只封域身份;源条里的客户/项目专名抽取是 P1
    (需要专名识别,先靠 LLM 改写 + H2A 人眼兜),别拿"denylist 过了"当全量脱密承诺。
    """
    deny: set[str] = set()
    for s in (domain_id, domain_name):
        if s and s.strip():
            deny |= _terms(s.strip())
            if _CJK.search(s):
                deny.add(s.strip())   # 整名也封(短域名可能不足 2 字滑窗)
    return deny


def scrub_ok(text: str, deny: set[str]) -> bool:
    """改写产物是否干净(不含任何 deny 项)。命中即脏 → 调用方丢弃该条,不循环重试。"""
    if not deny:
        return True
    terms = _terms(text)
    if terms & deny:
        return False
    # 整名 substring 兜底(CJK 整名在 _terms 里也会以 bigram 命中,这里防两字整名漏网)
    return not any(d in (text or "") for d in deny if len(d) >= 2 and _CJK.search(d))


# ---- 一次 LLM:判泛化 + 脱敏改写(宁缺勿滥、宁空勿毒;docs/78 §3.3)----


async def judge_and_rewrite(cands: list, *, gateway: Any, model_ref: str = "",
                            domain_id: str = "", domain_name: str = "") -> list[dict]:
    """批处理候选 → [{origin_key, content(改写后), kind, why}]。

    解析纪律同 parse_experiences:严格 JSON、失败返 []、不 prose 兜底——改写失败的候选
    宁可这轮全丢(投毒进镜像层=歪掉该角色所有域的未来行为,扩散面比域内大一个量级)。
    正身可能走工具信封(约束解码)→ harvest_structured 统一收割。
    """
    if not cands:
        return []
    import json as _json

    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.structured import harvest_structured
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source

    lines = [f"域(改写时必须剥掉的语境):{domain_name or domain_id}"]
    for b in cands[:MAX_PER_CARD]:
        lines.append(f"- origin_key={origin_key_for(b.content)} kind={b.provenance.get('kind', 'method')}"
                     f" 内容:{(b.content or '').strip()[:400]}")
    material, _ = clip_to_tokens("\n".join(lines), LLM_MATERIAL_TOKENS)
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    sp = SystemPrompt(static=[PROMOTION_SYSTEM])
    msgs = [{"role": "user", "content": material}]
    with token_source("experience_promotion"):   # 账本可见"谁在烧"(咽喉纪律)
        try:
            stream = gateway.complete(msgs, [], ref, system=sp,
                                      response_schema=_PROMOTION_SCHEMA)
        except TypeError:
            stream = gateway.complete(msgs, [], ref, system=sp)
        out = await harvest_structured(stream)
    # 严格解析:只剥外层 fence,失败返 [](宁空勿毒)
    s = (out or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("["):] if "[" in s else s
    try:
        data = _json.loads(s)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    valid_keys = {origin_key_for(b.content) for b in cands}
    items: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        content = str(it.get("content") or "").strip()
        okey = str(it.get("origin_key") or "").strip()
        if not content or okey not in valid_keys:
            continue   # 编造 origin_key / 空改写 → 丢(可溯源是底线)
        kind = it.get("kind") if it.get("kind") in _KINDS else "method"
        items.append({"origin_key": okey, "content": content, "kind": kind,
                      "why": str(it.get("why") or "").strip()[:200]})
    return items


# ---- 镜像层写入形态(docs/78 §3.6)----


def make_promoted_belief(content: str, kind: str, *, role: str, origin_domain: str,
                         origin_key: str, now: Optional[float] = None) -> Belief:
    """升层产物:`applies={"role"}` 无 domain = 镜像层;origin=不透明指针(证据不上升)。"""
    if now is None:
        now = time.time()
    k = kind if kind in _KINDS else "method"
    return Belief(
        content=(content or "").strip(),
        provenance={
            "source": ROLE_EXPERIENCE_SOURCE, "agent": role, "ts": now, "kind": k,
            "applies": {"role": role},                       # 无 domain = 镜像层(§2.6 同构)
            "origin": {"domain": origin_domain, "belief_key": origin_key,
                       "promoted_at": now},                  # 指针,不含证据内容
        },
        freshness_ts=now,
        scope="personal",                                    # 镜像资产跟人走(删域不连坐)
    )


__all__ = [
    "MIN_AGE_DAYS", "MAX_PER_CARD", "PROMOTION_SYSTEM",
    "origin_key_for", "promotion_candidates",
    "denylist_terms", "scrub_ok", "judge_and_rewrite", "make_promoted_belief",
]
