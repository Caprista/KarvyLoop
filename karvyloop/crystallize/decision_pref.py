"""crystallize/decision_pref — 决策接口结晶(楔子真正灵魂,docs/02 §11)。

§1–§10 的执行技能结晶沉的是"怎么干 X"(atom,Hermes 也做、商品化)。本模块是护城河级、
实查里无人做的那半:**决策接口结晶** —— 沉"**你怎么拍板**"(意图/品味/拍过的板),
让决策体提案前**预对齐**,你拒得越来越少、重复解释自己越来越少(decision-loop intent compound)。

**对齐宪法(不另起炉灶)**:
- **结晶单元 = 决策偏好**(约束/品味/站位指令),**载体 = 一种 Belief**(复用认知库,Hardy 拍板):
  `provenance.source == "decision_pref"`,带 kind/evidence/strength/status/applies(scope)。
- **不卷 ML/向量**(守 §否决清单):LLM 抽 + 现有 token 召回。镜像 `ingest.py` 的受限 LLM 调用
  + "宁空勿毒"严格解析(`llm-output-parser-must-refuse-garbage`)。
- **H2A**:静默暂记 provisional;只对高价值弹一次确认(P1)。只偏置提案、不自动执行。
- **不固化你**(产品之书未尽之问#2):决策偏好比执行技能更易撤(相反决策翻转 strength、可编辑)。

P0(本模块):决策偏好 Belief 约定 + observe 采样缓冲 + LLM 抽候选 + 双关门 promote(provisional)
+ 提案前 recall 注入 governance。P1:H2A 确认高价值 / 相反决策翻转 strength(下方留钩子,诚实标注)。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from karvyloop.schemas.cognition import Belief

# Belief.provenance.source 标记(召回时据此筛出决策偏好,与普通事实/偏好区分)
DECISION_PREF_SOURCE = "decision_pref"
_KINDS = ("constraint", "taste", "standing")

# 强化/翻转步长 + 撤销下限(决策偏好比执行技能更易撤,守"不固化你",产品之书未尽之问#2)
REINFORCE_STEP = 0.1     # 同方向决策再现 → strength + 此值(封顶 1.0)
WEAKEN_STEP = 0.3        # 相反决策 → strength - 此值
STRENGTH_FLOOR = 0.25    # provisional 偏好 strength 跌破此值 → 撤销(归档);confirmed 仅降影响不删


# 决策编译器 system:从拍板样本抽"你怎么决策"的可泛化偏好(三类),严格 JSON。
DECISION_PREF_SYSTEM = (
    "你是 KarvyLoop 的决策编译器。下面是用户在决策 loop 里的拍板样本"
    "(接受/拒绝/改写提案 + 理由,或用户的显式陈述)。从中抽出**关于这个用户怎么拍板**、"
    "能复用到将来类似提案的决策偏好。三类:"
    "constraint(硬约束,如'碰生产必须先有测试')/ taste(品味,如'输出默认用 markdown 表格')"
    "/ standing(站位指令,如'设计师永远先考虑移动端')。\n"
    "只抽**确有依据、且能泛化到将来类似情形**的;只对这一个具体任务成立的一次性决策别抽。"
    "每条短、自足,用第二人称陈述用户偏好。\n"
    "严格输出 JSON 数组,元素 "
    "{\"content\":\"<偏好>\",\"kind\":\"constraint|taste|standing\","
    "\"explicit\":true|false,\"scope\":\"global|domain\"};"
    "explicit:用户明说过=true,从行为推断=false。"
    "scope:这条偏好**普遍成立**=global(默认);**只在给定的业务域/角色情境下成立**才=domain"
    "(拿不准就 global)。没有可抽的输出 []。不要输出 JSON 以外任何文字。"
)

# 决策协调器 system:抽新偏好 + 标出这批决策**推翻了**哪些已有偏好(用户改主意了)。
DECISION_RECONCILE_SYSTEM = (
    DECISION_PREF_SYSTEM
    + "\n\n另外,下面会先列出该用户**已有的决策偏好**(带编号)。如果这批新决策里有"
    "**与某条已有偏好相矛盾/相反**的(说明用户改主意了),把那条的**编号**列进 contradicts。"
    "只标真正相反的,拿不准就别标。\n"
    "严格输出 JSON 对象:{\"new\":[{\"content\":..,\"kind\":..,\"explicit\":..}...],"
    "\"contradicts\":[<编号>...]};没有新偏好则 new=[],没有矛盾则 contradicts=[]。"
    "不要输出 JSON 以外任何文字。"
)


# 守线员 system:判一条提案是否**违背**用户已定标准(Cut 2 违背即拦)。宁可漏拦不可错拦。
VIOLATION_SYSTEM = (
    "你是 KarvyLoop 的决策守线员。下面是一条待用户拍板的**提案**,和用户**已经定下的决策标准**。"
    "判断这条提案是否**违背**了其中任何标准——只标**真的踩线**的(不是沾边、不是相关、不是补充)。"
    "严格输出 JSON 数组,每条违背一项:"
    '[{"standard":"<被违背标准的原文,照抄>","why":"<一句话:踩在哪>"}];'
    "没有违背 → []。**拿不准就不标(宁可不拦,不可错拦)**。不要输出 JSON 以外任何文字。"
)


def parse_violations(text: str) -> list[dict]:
    """解析守线员输出 → [{"standard","why"}]。严格 JSON、宁空勿毒(同 parse_decision_prefs)。"""
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return []
    try:
        data: Any = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("violations") if isinstance(data.get("violations"), list) else (
            [data] if data.get("standard") else [])
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        std = (item.get("standard") or "").strip()
        if not std:
            continue
        out.append({"standard": std, "why": (item.get("why") or "").strip()})
    return out


@dataclass
class DecisionSample:
    """决策 loop 里的一次拍板样本(信号源:H2A 决策 / 显式陈述 / 步骤编辑 / 圆桌改写)。"""
    decision: str          # ACCEPT|REJECT|DEFER|EDIT|STATE
    context: str           # 被提案的内容 / 用户陈述的话
    reason: str = ""       # 用户给的理由(REJECT/EDIT 时最值钱)
    scope: str = "personal"  # personal | domain
    domain: str = ""
    role: str = ""
    ts: float = 0.0


@dataclass
class DecisionPrefThresholds:
    """双关门阈值(决策样本比工具调用稀疏 → 门比执行技能松)。"""
    K_IMPLICIT: int = 2          # 隐式:同方向观察 ≥K 次才够格(显式 1 次即够)
    HIGH_VALUE_STRENGTH: float = 0.7  # ≥此值 = 高价值候选 → P1 弹 H2A 确认


# ---- 决策偏好 Belief 约定(载体复用认知库) ----


def make_decision_pref_belief(
    content: str, kind: str, *,
    scope: str = "personal", domain: str = "", role: str = "",
    evidence: Optional[list] = None, strength: float = 0.5,
    status: str = "provisional", explicit: bool = False,
    now: Optional[float] = None,
) -> Belief:
    """构造一条决策偏好 Belief(provenance 带决策偏好元数据)。"""
    if now is None:
        now = time.time()
    k = kind if kind in _KINDS else "taste"
    return Belief(
        content=content.strip(),
        provenance={
            "source": DECISION_PREF_SOURCE, "agent": "user", "ts": now,
            "kind": k, "evidence": list(evidence or []),
            "strength": max(0.0, min(1.0, strength)),
            "status": status,               # provisional | confirmed
            "explicit": bool(explicit),
            "applies": {"domain": domain, "role": role},  # 空 = 全局(总适用)
        },
        freshness_ts=now,
        scope=scope if scope in ("personal", "domain") else "personal",
    )


def is_decision_pref(b: Belief) -> bool:
    return bool(getattr(b, "provenance", None)) and b.provenance.get("source") == DECISION_PREF_SOURCE


# ---- 解析(镜像 ingest.parse_facts:JSON 严格、宁空勿毒) ----


def parse_decision_prefs(text: str) -> list[dict]:
    """解析决策编译器输出 → [{"content","kind","explicit"}]。

    严格(守 `llm-output-parser-must-refuse-garbage`):只剥外层 fence → json.loads;
    像 JSON(以 [ 或 {)却解析失败 → 返回 [](宁空勿毒,不把垃圾写进决策画像);
    非 JSON prose **不抽**(决策画像投毒比知识库更危险——会歪掉所有未来提案)。
    """
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return []
    try:
        data: Any = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []   # 决策画像:解析失败一律拒,绝不 prose 兜底
    if isinstance(data, dict):
        for key in ("prefs", "preferences", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data] if data.get("content") else []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        c = (item.get("content") or "").strip()
        if not c:
            continue
        kind = str(item.get("kind", "taste"))
        scope = str(item.get("scope", "global"))
        out.append({
            "content": c,
            "kind": kind if kind in _KINDS else "taste",
            "explicit": bool(item.get("explicit", False)),
            "scope": scope if scope in ("global", "domain") else "global",
        })
    return out


# ---- LLM 抽候选(镜像 ingest.compile_material) ----


def format_samples(samples: list[DecisionSample]) -> str:
    """把拍板样本拼成一段材料喂决策编译器。"""
    parts: list[str] = []
    for s in samples:
        line = f"[{s.decision}] {s.context.strip()}"
        if s.reason.strip():
            line += f" —— 理由:{s.reason.strip()}"
        if s.domain or s.role:
            line += f" (域={s.domain or '-'} 角色={s.role or '-'})"
        parts.append(line)
    return "\n".join(parts)


async def compile_decisions(samples: list[DecisionSample], *, gateway: Any,
                            model_ref: str = "") -> list[dict]:
    """跑一次受限 LLM 抽取(无工具)→ 候选决策偏好 list。复用 gateway.complete(同 ingest)。"""
    material = format_samples(samples)
    if not material.strip():
        return []
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[DECISION_PREF_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return parse_decision_prefs(out)


# ---- 双关门 promote ----


def initial_strength(*, explicit: bool, support_count: int) -> float:
    """候选初始置信:显式高、隐式随支撑数增长(封顶 0.9,留余地给 H2A 确认升满)。"""
    if explicit:
        return 0.7
    return min(0.4 + 0.15 * max(0, support_count - 1), 0.9)


def qualifies(candidate: dict, *, support_count: int,
              thresholds: Optional[DecisionPrefThresholds] = None) -> bool:
    """关 1 资格门:显式 1 次即够;隐式需同方向观察 ≥K 次。"""
    th = thresholds or DecisionPrefThresholds()
    if candidate.get("explicit"):
        return True
    return support_count >= th.K_IMPLICIT


def maybe_promote(
    candidate: dict, *, support_count: int = 1,
    scope: str = "personal", domain: str = "", role: str = "",
    evidence: Optional[list] = None,
    thresholds: Optional[DecisionPrefThresholds] = None,
    now: Optional[float] = None,
) -> Optional[Belief]:
    """候选 → (过门则)provisional 决策偏好 Belief,否则 None。

    关 1 资格(qualifies)+ 关 2 价值(可泛化——P0 信 LLM 只抽可泛化的;**相反决策翻转 = P1**)。
    P0 一律写 status="provisional"(H2A 确认升 confirmed = P1)。
    """
    content = (candidate.get("content") or "").strip()
    if not content:
        return None
    if not qualifies(candidate, support_count=support_count, thresholds=thresholds):
        return None
    explicit = bool(candidate.get("explicit"))
    return make_decision_pref_belief(
        content, candidate.get("kind", "taste"),
        scope=scope, domain=domain, role=role,
        evidence=evidence, strength=initial_strength(explicit=explicit, support_count=support_count),
        status="provisional", explicit=explicit, now=now,
    )


def is_high_value(b: Belief, *, thresholds: Optional[DecisionPrefThresholds] = None) -> bool:
    """高价值 = 该弹一次 H2A 让你确认的(P1 用;P0 只标不弹)。"""
    th = thresholds or DecisionPrefThresholds()
    return is_decision_pref(b) and float(b.provenance.get("strength", 0.0)) >= th.HIGH_VALUE_STRENGTH


# ---- 强化 / 翻转(P1:不固化你 —— 同方向加固、相反削弱/撤销) ----


def _clone_with_strength(b: Belief, strength: float, *, evidence_add: Optional[list] = None,
                         now: Optional[float] = None) -> Belief:
    """复制一条决策偏好 Belief,改 strength/freshness(+追加 evidence)。其余 provenance 不变。"""
    if now is None:
        now = time.time()
    prov = dict(b.provenance)
    prov["strength"] = max(0.0, min(1.0, strength))
    if evidence_add:
        prov["evidence"] = list(prov.get("evidence", [])) + list(evidence_add)
    return Belief(content=b.content, provenance=prov, freshness_ts=now, scope=b.scope)


def reinforce(b: Belief, *, evidence_add: Optional[list] = None, now: Optional[float] = None,
              step: float = REINFORCE_STEP) -> Belief:
    """同方向决策再现 → 加固(strength+step 封顶 1.0、刷新 freshness、追加 evidence)。"""
    return _clone_with_strength(b, float(b.provenance.get("strength", 0.0)) + step,
                                evidence_add=evidence_add, now=now)


def weaken(b: Belief, *, now: Optional[float] = None, step: float = WEAKEN_STEP) -> Belief:
    """相反决策 → 削弱(strength-step)。是否撤销由 should_revoke 判(confirmed 不静默删)。"""
    return _clone_with_strength(b, float(b.provenance.get("strength", 0.0)) - step, now=now)


def norm_content(s: str) -> str:
    """偏好内容归一(去空白/小写)—— Belief 无稳定 id,按内容匹配回查(confirm/去重共用)。"""
    return re.sub(r"\s+", "", (s or "").lower())


def find_decision_pref(beliefs: list, content: str, *,
                       status: Optional[str] = None) -> Optional[Belief]:
    """在一批 Belief 里按内容找一条决策偏好(可选限定 status)。Belief 无 id → 按归一内容匹配。"""
    key = norm_content(content)
    for b in beliefs:
        if is_decision_pref(b) and norm_content(b.content) == key:
            if status is None or b.provenance.get("status") == status:
                return b
    return None


def rename_pref(b: Belief, new_content: str, *, now: Optional[float] = None) -> Belief:
    """编辑偏好内容(你可改)。保留 provenance(kind/strength/status/applies/evidence),换文本+刷新。"""
    if now is None:
        now = time.time()
    return Belief(content=(new_content or "").strip(), provenance=dict(b.provenance),
                  freshness_ts=now, scope=b.scope)


def confirm_pref(b: Belief, *, now: Optional[float] = None, boost: float = 0.1) -> Belief:
    """H2A 确认 → 升 confirmed(+小幅 strength;你拍过板的,以后只降不静默删)。"""
    if now is None:
        now = time.time()
    prov = dict(b.provenance)
    prov["status"] = "confirmed"
    prov["strength"] = max(0.0, min(1.0, float(prov.get("strength", 0.0)) + boost))
    return Belief(content=b.content, provenance=prov, freshness_ts=now, scope=b.scope)


def should_revoke(b: Belief, *, floor: float = STRENGTH_FLOOR) -> bool:
    """削弱后是否该撤销(归档):**仅 provisional 且跌破下限**才撤;confirmed 是你拍过板的,
    只降影响、绝不静默删(要删得你自己来,守 H2A + '不固化你但尊重你确认过的')。"""
    if b.provenance.get("status") == "confirmed":
        return False
    return float(b.provenance.get("strength", 0.0)) < floor


# ---- 协调:抽新偏好 + 标矛盾(P1 contradiction-flip) ----


def parse_reconcile(text: str) -> tuple[list[dict], list[int]]:
    """解析协调器输出 → (新候选 list, 被推翻的已有偏好编号 list)。宁空勿毒(同 parse_*)。

    兼容两种形态:**对象** {"new":[...],"contradicts":[...]} 或**裸数组** [...](当 new、无矛盾)。
    """
    t = (text or "").strip()
    if not t:
        return [], []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return [], []
    try:
        data: Any = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return [], []
    if isinstance(data, list):
        return parse_decision_prefs(json.dumps(data)), []
    if not isinstance(data, dict):
        return [], []
    new = parse_decision_prefs(json.dumps(data.get("new", [])))
    contradicts: list[int] = []
    for x in (data.get("contradicts") or []):
        try:
            contradicts.append(int(x))
        except (TypeError, ValueError):
            continue
    return new, contradicts


def _format_existing(existing: list[str]) -> str:
    return "\n".join(f"{i + 1}. {c}" for i, c in enumerate(existing))


async def reconcile_decisions(samples: list[DecisionSample], *, existing: list[str],
                              gateway: Any, model_ref: str = "",
                              context: Optional[dict] = None) -> tuple[list[dict], list[int]]:
    """跑一次受限 LLM:抽新偏好(带 global/domain scope)+ 标这批决策推翻了哪些已有偏好(1-based 编号)。

    existing 空 → 退化成纯抽取(等价 compile_decisions);非空 → 走协调 system。
    context={"domain","role"}:这批决策的情境(同一域/角色)→ 喂给 LLM 判每条偏好是否限定本域。
    """
    material = format_samples(samples)
    if not material.strip():
        return [], []
    system = DECISION_RECONCILE_SYSTEM if existing else DECISION_PREF_SYSTEM
    if existing:
        material = f"已有偏好:\n{_format_existing(existing)}\n\n新决策:\n{material}"
    ctx = context or {}
    if ctx.get("domain"):
        material = (f"情境:以下决策都发生在业务域「{ctx['domain']}」"
                    + (f"、角色「{ctx['role']}」" if ctx.get("role") else "")
                    + "。判断每条偏好 scope 时据此定 global/domain。\n\n" + material)
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[system]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return parse_reconcile(out)


# ---- 预对齐:提案前召回决策偏好 → 注入 governance ----


def _applies_here(b: Belief, *, domain: str, role: str) -> bool:
    """偏好的 applies 是否覆盖当前提案场景(空 applies = 全局,总适用)。"""
    ap = b.provenance.get("applies") or {}
    ad, ar = ap.get("domain", ""), ap.get("role", "")
    if ad and domain and ad != domain:
        return False
    if ar and role and ar != role:
        return False
    return True


def receipt_gists(b: Belief, *, limit: int = 3) -> list[str]:
    """这条决策偏好的人话回执:来自你哪几次拍板(决策+理由摘要)。

    兼容旧数据:早期 evidence 只存时间戳(float)→ 没 gist 的跳过(返回空,不崩)。
    """
    out: list[str] = []
    for e in (b.provenance.get("evidence") or []):
        if isinstance(e, dict) and e.get("gist"):
            out.append(str(e["gist"]))
        if len(out) >= limit:
            break
    return out


def applicable_decision_prefs(beliefs: list[Belief], *, query: str = "",
                              domain: str = "", role: str = "") -> list[Belief]:
    """适用本场景的全部决策偏好,**按相关性·强度·新鲜度**排序(不封顶)。

    相关性用知识召回同款词面打分(`context.relevance.overlap_score`,无向量)——
    决策 X 时把跟 X **相关**的标准排前面,而不是只看全局最强(规模一大相关但较弱的被挤掉)。
    query 空 → 相关性全 0 → 回退到强度·新鲜度(0 回归)。
    """
    from karvyloop.context.relevance import overlap_score
    matched = [b for b in beliefs if is_decision_pref(b) and _applies_here(b, domain=domain, role=role)]
    matched.sort(key=lambda b: (overlap_score(query, b.content),
                                float(b.provenance.get("strength", 0.0)), b.freshness_ts),
                 reverse=True)
    return matched


def recall_decision_prefs(beliefs: list[Belief], *, query: str = "", domain: str = "",
                          role: str = "", limit: int = 6) -> list[Belief]:
    """筛出适用当前场景的决策偏好,按相关性·强度·新鲜度排序、封顶 limit。"""
    return applicable_decision_prefs(beliefs, query=query, domain=domain, role=role)[:max(0, limit)]


def prealign_block(beliefs: list[Belief], *, query: str = "", domain: str = "", role: str = "",
                   limit: int = 6) -> str:
    """召回适用偏好 → 拼成注入 governance 的预对齐块(空 → "")。

    提案前注入,让小卡/角色一上来就贴合你怎么拍板。**只偏置提案、不自动执行**(H2A)。
    封顶但**绝不静默漏**:适用标准超过 limit → 末尾明示"还有 N 条(已按相关性挑最相关的)"。
    """
    applicable = applicable_decision_prefs(beliefs, query=query, domain=domain, role=role)
    if not applicable:
        return ""
    prefs = applicable[:max(0, limit)]
    lines = ["【你的决策偏好(提案请预先对齐;这些是偏置不是硬规则,最终仍你拍板)】"]
    label = {"constraint": "约束", "taste": "品味", "standing": "站位"}
    for b in prefs:
        k = label.get(b.provenance.get("kind", ""), "偏好")
        prov = "" if b.provenance.get("status") == "confirmed" else "(暂记)"
        lines.append(f"- [{k}]{prov} {b.content}")
        gists = receipt_gists(b)   # 回执:这条从你哪几次拍板来 —— 不是凭空的标准,可核
        if gists:
            lines.append(f"  └ 来自你的拍板:{'；'.join(gists)}")
    dropped = len(applicable) - len(prefs)
    if dropped > 0:   # 不静默漏:明示还有几条没展开(已按相关性挑了最相关的)
        lines.append(f"(还有 {dropped} 条适用标准未展开,已按与本次相关性挑了最相关的)")
    return "\n".join(lines)


__all__ = [
    "DECISION_PREF_SOURCE", "DECISION_PREF_SYSTEM",
    "DecisionSample", "DecisionPrefThresholds",
    "make_decision_pref_belief", "is_decision_pref",
    "parse_decision_prefs", "format_samples", "compile_decisions",
    "initial_strength", "qualifies", "maybe_promote", "is_high_value",
    "recall_decision_prefs", "applicable_decision_prefs", "prealign_block", "receipt_gists",
    "VIOLATION_SYSTEM", "parse_violations",
    "REINFORCE_STEP", "WEAKEN_STEP", "STRENGTH_FLOOR",
    "DECISION_RECONCILE_SYSTEM", "reinforce", "weaken", "should_revoke",
    "parse_reconcile", "reconcile_decisions",
    "norm_content", "find_decision_pref", "confirm_pref", "rename_pref",
]
