"""cognition/converge — 收敛式分层认知提议(docs/66 第二轮:摄入=聊天,你点「收敛」才总结)。

与 auto_distill 的分工:
- auto_distill = 轮后**自动**蒸、直接写库(provisional 低置信)—— "不用你喂"的半边。
- converge = **你点「收敛」才触发**,把对话总结成**分层认知候选**(不写库),喂给确认卡;
  只有你**逐层确认**的才写(user_explicit,非 provisional)。这是"理解关"那半边(防认知债)。

颗粒度由理解关自己切:**不预设块数**,按理解到的**深度/类型**分层(经历/推理/原则/校正/涌现)。
最值钱的是「涌现层」——聊才长出来、源材料里本没有的认知;也最需用户确认(是模型替你刨的,不是你说的)。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from karvyloop.cognition.auto_distill import format_turns
from karvyloop.paradigm.agent_spec import AgentSpec

# 5 层认知(Hardy 2026-07-06 docs/66 §D):深度递增,确认关**越深越严**。
LAYERS = ("experience", "reasoning", "principle", "corrective", "emergent")
DEPTH_BY_LAYER = {"experience": 1, "reasoning": 2, "principle": 3, "corrective": 4, "emergent": 5}

CONVERGE_SYSTEM = (
    "你是 KarvyLoop 的认知编译器。用户点了「收敛」——把这段对话总结成**分层的认知候选**,"
    "供用户逐条确认后再沉淀。**颗粒度不预设**:理解到几层就抽几条,别硬凑、别切成流水账。\n"
    "每条标一个 layer(认知的深度/类型):\n"
    "- experience:客观经历/事实(做过什么、发生过什么;如『从 React 换到了 Vue』)\n"
    "- reasoning:某段经历的推理(如『为什么换』)\n"
    "- principle:约束将来决策的原则(如『别为半年后模型会有的功能提前建』)\n"
    "- corrective:更深的、拿来校正别的推理的通识(如『不做≠不好,只是那条件下有更优解』)\n"
    "- emergent:**对话现场才涌现、源材料里本没有**的潜在认知——你在聊的过程中替用户刨出来的"
    "(如『每个决策都藏着隐含假设,跨域套用要先把假设刨出来』)。这层最值钱,也最需用户确认。\n"
    "每条给:content(自足一句,用户的语言)、layer、why(为什么值得沉淀 / 它是什么)、"
    "when(**仅当对话里用户明说了某个真实时间**才填那个原话,如『上个月』/『2026-03』;没明说填 null——"
    "**绝不自己猜时间**)。\n"
    "只抽**确有依据**的;能泛化到将来的才留,一次性寒暄别抽;没有可沉淀的就给空数组。\n"
    "**每条必须是已确定的陈述句**(用户持有的事实/偏好/原则/洞察),写成用户可点头认领的一句话。"
    "**绝不把开放式问题、待办任务、或用户还没拿定的选择当候选**——那是『待你决策』,不是『可沉淀的"
    "认知』(如『要不要给沉默权设 Wilson 门』是问题、不是候选;『我倾向沉默权从严』才是)。"
    "拿不准某条是不是问题时,宁可不抽。\n"
    "严格输出 JSON 数组 [{\"content\",\"layer\",\"why\",\"when\"}...];不要输出 JSON 以外任何文字。"
)

# 收敛器的**范式工程内核**(知行合一:内部 agent 也用我们的范式 —— 见 paradigm/agent_spec.py)。
# 上面的 CONVERGE_SYSTEM 是它 identity+principles 的散文实现,下面的 parse_candidates 是它 verify
# (宁空勿毒)的代码实现;test_converge_layered 把这份 spec 和二者对账,不许漂移。
# persona 层(USER 服务谁 / MEMORY 自己长)对这个无状态编译器不适用,故意缺席。
CONVERGE_AGENT = AgentSpec(
    id="converge",
    identity="认知编译器:用户点「收敛」时,把一段对话总结成分层认知候选"
             "(经历/推理/原则/校正/涌现),供用户逐条确认后再沉淀。",
    principles=(
        "颗粒度不预设:理解到几层抽几条,别硬凑、别切成流水账。",
        "绝不猜时间:when 仅当对话里用户明说了真实时间才填原话,否则 null。",
        "只抽确有依据、能泛化到将来的;一次性寒暄不抽;没有可沉淀的就给空数组。",
        "每条是已确定的陈述句(用户可点头认领),不是开放问题/待办/未拿定的选择——拿不准宁可不抽。",
    ),
    contract="只产候选,绝不写库:converge_and_propose 返回候选交确认卡,落盘是 sediment_confirmed 的事。",
    verify="严格输出 JSON 数组,每条 content+layer(∈LAYERS)+why+when;解析走 parse_candidates,"
           "宁空勿毒——非严格 JSON / 非数组 / 未知 layer / 空内容一律跳过或返 [],绝不硬塞垃圾进候选。",
    tools=(),   # 纯 LLM,无工具
)


def _cid(content: str) -> str:
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:12]


@dataclass
class CognitionCandidate:
    """一条待确认的认知候选(收敛总结产出;确认后才写库)。"""

    content: str
    layer: str                       # LAYERS 之一
    why: str = ""
    when_hint: Optional[str] = None  # 用户明说的真实时间原话(如『上个月』);没说 = None(绝不猜)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _cid(self.content)

    @property
    def depth(self) -> int:
        return DEPTH_BY_LAYER.get(self.layer, 1)

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content, "layer": self.layer,
                "depth": self.depth, "why": self.why, "when_hint": self.when_hint}


def parse_candidates(text: str) -> list[CognitionCandidate]:
    """解析收敛总结输出 → 候选列表。宁空勿毒:非严格 JSON 数组 / 坏项 → 跳过或返 []。

    真机实拍:真模型偶发把数组裹在散文里 / 思考烧掉 max_tokens 把数组尾截断 → 严格
    json.loads 直接 [] → UI 误报"没什么可沉淀"。前置复用 trace_habit 的硬化提取器
    (剥围栏/散文里找完整数组/截断打捞,自带 O(n²) 双上限),提取仍失败才落回严格解析。
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
        from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array
        cleaned = _extract_json_array(cleaned)   # 找不到合法数组时原文返回,下方严格解析兜底
    except Exception:
        pass   # 提取器只是增益:任何意外退回严格解析,不因它挂
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[CognitionCandidate] = []
    seen: set = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip() if isinstance(item.get("content"), str) else ""
        if not content:
            continue
        layer = (item.get("layer") or "").strip() if isinstance(item.get("layer"), str) else ""
        if layer not in LAYERS:
            continue                       # 未知层 = 宁空勿毒,跳过(别硬塞默认层)
        cid = _cid(content)
        if cid in seen:
            continue
        seen.add(cid)
        when = item.get("when")
        when_hint = when.strip() if isinstance(when, str) and when.strip() else None
        why = (item.get("why") or "").strip() if isinstance(item.get("why"), str) else ""
        out.append(CognitionCandidate(content=content, layer=layer, why=why, when_hint=when_hint, id=cid))
    return out


async def converge_and_propose(
    turns: list, *, gateway: Any, model_ref: str = "", trace: Any = None,
) -> list[CognitionCandidate]:
    """收敛:把一段对话总结成分层认知候选(**不写库**)。产出喂给确认卡,确认了才沉。

    一次 LLM 调用,严格 JSON、宁空勿毒。空对话 / 解析失败 / 调用异常 → []。
    """
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source

    material = format_turns(turns)
    if not material.strip():
        return []
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    try:
        with token_source("converge"):
            async for ev in gateway.complete(
                [{"role": "user", "content": material}], [], ref,
                system=SystemPrompt(static=[CONVERGE_SYSTEM]),
            ):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception:
        return []
    return parse_candidates(out)


# 只认**绝对**日期(YYYY / YYYY-MM / YYYY-MM-DD)。相对/模糊("上个月"/"Vue 之前")一律不解析——
# 拿 now 反推一个精确时间戳 = 造假精度 = 又造认知债(docs/66 §A 红线:绝不猜时间)。
_ABS_DATE_RE = re.compile(r"^\s*(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?\s*$")


def _parse_when(hint: Optional[str]) -> Optional[float]:
    """用户明说的**绝对**日期 → UTC 时间戳;相对/模糊 → None(留原话字符串,绝不猜 float)。"""
    if not hint or not isinstance(hint, str):
        return None
    m = _ABS_DATE_RE.match(hint)
    if not m:
        return None
    try:
        import datetime as _dt
        y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
        return _dt.datetime(y, mo, d, tzinfo=_dt.timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None


async def sediment_confirmed(
    candidates: list, *, mem: Any, gateway: Any = None, model_ref: str = "",
    agent_id: str = "user", now: Optional[float] = None, trace: Any = None,
    learned_via: str = "",
) -> dict:
    """把用户**确认过**的分层认知候选沉进记忆库。只沉传进来的(= 你确认的),没确认的候选压根不到这。

    - `provenance.source = "user_explicit"`(最高档、非 provisional:过了人的理解关 = 最高权威,
      supersede 时掀不翻,强于 auto 蒸的一档);带 layer / why / learned_via(理解出处,可审计)。
    - 时间格:`when_hint` 能解析成**绝对**日期 → `provenance.valid_from`(as_of 用);相对/模糊 →
      留 `provenance.valid_from_hint` 原话字符串(顺序/为什么才是重点,时间可后补,绝不猜)。
    - 写后跑 `run_supersede_pass`(新高权威条正确打失效矛盾旧条,失效不删)+ 语义标签(同 distill 接缝)。
    - Trace 只记**确认沉淀**的。返回 {"written", "extends", "ids"}。
    """
    from karvyloop.schemas.cognition import Belief

    if now is None:
        now = time.time()
    written: list = []
    ids: list = []
    for c in candidates:
        content = (getattr(c, "content", "") or "").strip()
        if not content:
            continue
        prov = {
            "source": "user_explicit", "agent": agent_id, "ts": now, "trace_ref": "",
            "kind": "belief", "layer": getattr(c, "layer", "") or "",
            "why": getattr(c, "why", "") or "",
        }
        if learned_via:
            prov["learned_via"] = learned_via
        when_hint = getattr(c, "when_hint", None)
        vf = _parse_when(when_hint)
        if vf is not None:
            prov["valid_from"] = vf
        elif when_hint:
            prov["valid_from_hint"] = when_hint
        try:
            b = Belief(content=content, provenance=prov, freshness_ts=now, scope="personal")
            mem.write(b)
            written.append(b)
            ids.append(_cid(content))
        except Exception:
            continue
    result = {"written": len(written), "extends": [], "ids": ids}
    if written and gateway is not None:
        try:
            from karvyloop.cognition.conflict import run_supersede_pass
            sup = await run_supersede_pass(written, mem=mem, gateway=gateway,
                                           model_ref=model_ref, now=now, trace=trace)
            result["extends"] = list(sup.get("extends") or [])
        except Exception:
            pass
        cc = getattr(mem, "concept_cache", None)
        if cc is not None:
            try:
                from karvyloop.cognition.concepts import tag_beliefs
                from karvyloop.llm.token_ledger import token_source
                with token_source("belief_tags"):
                    await tag_beliefs(written, cache=cc, gateway=gateway, model_ref=model_ref, trace=trace)
            except Exception:
                pass
    if trace is not None and written:
        try:
            from karvyloop.cognition.trace import TraceEntry
            trace.append(TraceEntry(
                task_id="cognition_sediment", kind="belief_sedimented",
                payload={"n": len(written), "layers": [(b.provenance or {}).get("layer", "") for b in written],
                         "learned_via": learned_via},
                source="converge"))
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# ② 沉淀确认卡:把分层候选摆成一张 H2A 卡 → 用户逐条 yes/改/删 → 只把确认的喂给 sediment_confirmed。
# 确认动作 = 理解关(docs/66):你没法对没读懂的东西诚实说"沉";越深的层越不能盲拍。
# ---------------------------------------------------------------------------

def build_sediment_card(candidates: list, *, conversation_ref: str = "") -> dict:
    """把分层候选摆成沉淀确认卡(UI 渲染用,kind="sediment")。

    按深度递增排(经历在前、涌现在后);涌现/校正层(depth≥4)标 `needs_attention`——
    那是模型替你刨的、不是你说的,UI 该视觉上要求你真读过再拍。
    """
    items = sorted((c.to_dict() for c in candidates), key=lambda d: d.get("depth", 1))
    for it in items:
        it["needs_attention"] = it.get("depth", 1) >= 4
    return {
        "kind": "sediment",
        "conversation_ref": conversation_ref,
        "items": items,
        "n": len(items),
        "max_depth": max((it.get("depth", 1) for it in items), default=0),
    }


def apply_confirmation(
    candidates: list, decisions: dict,
) -> tuple[list, bool]:
    """按用户逐条决定过滤候选 → (要沉的列表, engaged)。

    decisions:{candidate_id: {"action": "accept"|"edit"|"drop", "content": 改后文本(edit 时)}}。
    - **不在 decisions 里的候选 = 未确认 = 不沉**(只沉你确认的,缺省即 drop);
    - edit:改后的话才是你背书的 → 替换 content、id 重算(空改后文本 = drop);
    - engaged = 有任何 edit/drop(你真判断过,非 rubber-stamp)——镜像 DecisionCard.engaged()。
    """
    accepted: list[CognitionCandidate] = []
    engaged = False
    for c in candidates:
        d = decisions.get(getattr(c, "id", ""))
        if not isinstance(d, dict):
            continue                                   # 未确认 → 不沉
        action = d.get("action")
        if action == "drop":
            engaged = True
            continue
        if action == "edit":
            engaged = True
            new_content = (d.get("content") or "").strip()
            if not new_content:
                continue                               # 改成空 = drop
            accepted.append(CognitionCandidate(
                content=new_content, layer=c.layer, why=c.why,
                when_hint=c.when_hint, id=_cid(new_content)))
            continue
        if action == "accept":
            accepted.append(c)
    return accepted, engaged


@dataclass
class SedimentTracker:
    """反投降闸(沉淀版):侦测"你在无脑沉"再拦一次。

    盲拍 = 全收且零改零删。**越深越严**:含涌现/校正层(depth≥4)的盲拍计 2 分——
    那些是模型替你立的观点,盲拍它们 = 最毒的认知债(docs/66:深度↔确认关绑死)。
    阈值 3(比决策卡的 5 严:沉淀写进"你是谁",错认比错执行贵)。
    """
    threshold: int = 3
    score: int = 0

    def record(self, *, accepted_any: bool, engaged: bool, max_depth: int = 1) -> None:
        if accepted_any and not engaged:
            self.score += 2 if max_depth >= 4 else 1
        else:
            self.score = 0                             # 改/删过、或全删 → 你还在判断

    def needs_recheck(self) -> bool:
        return self.score >= self.threshold


__all__ = ["LAYERS", "DEPTH_BY_LAYER", "CognitionCandidate", "parse_candidates",
           "converge_and_propose", "sediment_confirmed", "CONVERGE_SYSTEM",
           "build_sediment_card", "apply_confirmation", "SedimentTracker"]
