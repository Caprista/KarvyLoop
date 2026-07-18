"""cognition/auto_distill — 对话自动蒸馏(loop step4b:个人知识库的"不用你喂"半边)。

地基(step4b 地基)铺好后,Belief 长期库在产品里真活了(落盘 + 召回注入)。这步让系统
**不用用户显式喂料**,轮后自动从对话里蒸出"关于你"的知识 —— 复用 4b-1 的摄入编译器
(`ingest.ingest_material`,source="conversation"),只是触发口从"用户喂"变成"轮后批量"。

**省 token(用得起)**:不是每轮调一次模型,而是**攒够 N 轮未蒸馏才蒸一次**(一次调用蒸
一批),且只蒸 watermark 之后的新轮(不重复蒸)。N 走会话级冷启动 warmup(1→2→4→稳态,
见 `warmup_batch`):新对话第 1 轮就有"记得你"信号,稳态节奏与固定 batch 一致。
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from karvyloop.cognition.ingest import _FACTS_SCHEMA, IngestResult, ingest_material, parse_facts
from karvyloop.crystallize.decision_pref import _PREF_ITEM_SCHEMA

DISTILL_BATCH = 4  # 每积累 N 轮未蒸馏 → 蒸一次(批量省 token)

# ---- 约束解码底层 schema(组合抽取:facts + decisions 一次调用)----
# distill_turns_with_decisions 的产物直写记忆(facts)+ 路由进决策结晶(decisions),两条都是最高
# 投毒风险车道 → 给支持的 provider 走原生结构化输出(保证吐合法 JSON 对象),不支持的网关侧退回
# 无约束、上层 parse_combined 宁空勿毒二层兜底。schema 逐字段对齐 parse_combined:它读对象
# {"facts":[...],"decisions":[...]},facts 走 parse_facts(item 复用 ingest 的 _FACTS_SCHEMA:
# 只强求 content,title/kind 可选),decisions 走 parse_decision_prefs(item 复用决策偏好 schema:
# 只强求 content,kind/explicit/scope 可选)。子 item schema 直接复用两处唯一定义,防形状漂移。
_COMBINED_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": _FACTS_SCHEMA,                            # 数组;item = ingest 的 facts 形状
        "decisions": {"type": "array", "items": _PREF_ITEM_SCHEMA},  # 数组;item = 决策偏好形状
    },
    "required": ["facts", "decisions"],   # 组合器 system 明列两键(没有就给空数组,键仍在)
}

# 质量门①(防自反馈投毒):蒸馏材料里剔除 <memory-context> 围栏召回块及其提示行——
# 轮文本若带着"已召回的旧记忆",蒸馏会把旧记忆**再抽成新条**写回库(复述循环:
# 一条知识每蒸一轮就自我复制一次,库越长越毛)。配对块连内容一起剥;孤立标签/提示行单剥。
_RECALL_PAIR_RE = re.compile(
    r"<[\s/]*memory[\s-]*context[\s/]*>.*?</[\s]*memory[\s-]*context[\s/]*>",
    re.IGNORECASE | re.DOTALL,
)
_RECALL_TAG_RE = re.compile(r"</?[\s/]*memory[\s-]*context[\s/]*>", re.IGNORECASE)
_RECALL_HINT_RE = re.compile(r"（以上是召回的记忆背景[^\n]*非新用户输入）?")


def strip_recall_echo(text: str) -> str:
    """剔除文本里的召回围栏块(整块含内容)+ 孤立标签 + 提示行。蒸馏材料预处理用。"""
    s = text or ""
    s = _RECALL_PAIR_RE.sub("", s)
    s = _RECALL_TAG_RE.sub("", s)
    s = _RECALL_HINT_RE.sub("", s)
    return s

# §11 P1b 显式陈述源:piggyback —— **同一次** LLM 调用既抽"关于你的事实/偏好"(进记忆),
# 又抽"你怎么决策"的决策偏好(进决策结晶)。不加 token 成本(守用得起),且把两者分桶——
# 一般偏好→记忆 Belief;拍板规则→决策偏好,理清与记忆 preference 的重叠。
DISTILL_COMBINED_SYSTEM = (
    "你是 KarvyLoop 的知识编译器。从对话里抽两类东西,**严格分桶、别混**:\n"
    "1) facts:关于这个用户的原子事实与一般偏好(每条独立自足),kind=fact|preference。\n"
    "2) decisions:**关于用户怎么拍板 / 要求什么**的可复用决策偏好——是'决策规则',不是一般喜好。"
    "kind=constraint(硬约束,如'碰生产先写测试')| taste(品味,如'输出默认用表格')| "
    "standing(站位,如'设计师先考虑移动端');explicit=用户明说过=true,从行为推断=false;"
    "scope=global(普遍成立,默认)| domain(只在某情境)。\n"
    "只抽**确有依据**的;decisions 要能泛化到将来类似情形,一次性的别抽。"
    "区分:'喜欢简洁'=facts 的 preference;'碰生产必须先写测试'=decisions 的 constraint。\n"
    "严格输出 JSON 对象 {\"facts\":[{\"content\",\"kind\"}...],"
    "\"decisions\":[{\"content\",\"kind\",\"explicit\",\"scope\"}...]};没有就给空数组。"
    "不要输出 JSON 以外任何文字。"
)


def format_turns(turns: list) -> str:
    """把若干对话轮拼成一段材料喂给摄入编译器。召回围栏块先剥(防已召回记忆被再抽成新条)。"""
    parts: list[str] = []
    for t in turns:
        u = strip_recall_echo(getattr(t, "user_intent", "") or "")
        a = strip_recall_echo(getattr(t, "agent_response", "") or "")
        if u.strip():
            parts.append(f"用户: {u.strip()}")
        if a.strip():
            parts.append(f"小卡: {a.strip()}")
    return "\n".join(parts)


def warmup_batch(watermark: int, *, batch: int = DISTILL_BATCH) -> int:
    """会话级冷启动 warmup:按 watermark 返回本次蒸馏阈值(1→2→4→稳态 batch)。

    痛点:固定 batch 下新对话前 batch-1 轮永远不蒸——用户走完 onboarding 开新话题,
    前几轮零"记得你"信号。warmup 让**第一天就有记忆感**:第 1 轮就蒸 → 第 3 轮 →
    第 7 轮 → 之后回到稳态每 batch 轮(watermark 按会话存,新对话=0,天然生效)。

    成本纪律(用得起):warmup 只在 watermark 小时(0/1 两档)比旧固定 batch **多蒸 2 次**,
    稳态节奏不变——指数升阈,不是每轮都蒸。

    batch 显式传非默认值时的语义:batch 只定**稳态**阈值;warmup 阶梯(1/2/4)与 batch 取
    min 封顶——warmup 只会比稳态**更早**蒸,绝不更晚(batch=2 时阶梯 4 若不封顶反而拖慢,
    违背 warmup 本意)。

    注(docs 判定表):若未来触发机制改成**防抖**(轮后延迟蒸 + 新消息到达取消重排),
    冷启动问题在源头消失,整个 warmup(本函数 + should_distill 的接线)应整体撤掉。
    """
    if watermark <= 0:
        step = 1          # 新对话第 1 轮就蒸,立刻有记忆感
    elif watermark <= 1:
        step = 2          # 第 3 轮
    elif watermark <= 3:
        step = 4          # 第 7 轮
    else:
        return batch      # 稳态:与旧固定 batch 行为完全一致
    return min(step, batch)


def should_distill(n_turns: int, watermark: int, *, batch: int = DISTILL_BATCH) -> bool:
    """攒够阈值轮未蒸馏 → 该蒸了。阈值走 `warmup_batch`(冷启动 1→2→4→稳态 batch)。

    batch 形参保留兼容既有调用:它定稳态阈值,warmup 阶梯与它取 min(见 warmup_batch)。
    """
    return (n_turns - watermark) >= warmup_batch(watermark, batch=batch)


async def distill_turns(
    turns: list,
    *,
    gateway: Any,
    mem: Any,
    model_ref: str = "",
    agent_id: str = "user",
    now: Optional[float] = None,
    trace: Any = None,
    conversation_id: str = "",
) -> IngestResult:
    """把一批对话轮编译成"关于用户"的 Belief(source=conversation)。复用 4b-1 摄入编译器。

    质量门②:auto 蒸(无人审直接写库)一律标 `provisional`——provenance_rank 封顶蒸馏档,
    supersede 时掀不翻人明说的;由 daily 复审/下次冲突判定处理,不与人审沉淀同权。

    `conversation_id`(Q2 出处回链):产生这批轮的 Conversation.id(现成定位键,全局唯一)——
    进 provenance,记忆面板"对话沉淀"条目据此点回那次对话。空 = 调用方无会话上下文,不写键。"""
    material = format_turns(turns)
    if not material.strip():
        return IngestResult(written=0, raw="(无可蒸馏内容)")
    return await ingest_material(
        material, gateway=gateway, mem=mem, model_ref=model_ref,
        agent_id=agent_id, scope="personal", source="conversation", now=now,
        provisional=True, trace=trace, conversation_id=conversation_id,
    )


def parse_combined(text: str) -> tuple[list[dict], list[dict]]:
    """解析组合抽取输出 → (facts, decisions)。宁空勿毒:像 JSON 解析失败 → ([], [])。"""
    import json
    from karvyloop.crystallize.decision_pref import parse_decision_prefs
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
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    facts = parse_facts(json.dumps(data.get("facts", [])))
    decisions = parse_decision_prefs(json.dumps(data.get("decisions", [])))
    return facts, decisions


async def distill_turns_with_decisions(
    turns: list, *, gateway: Any, mem: Any, model_ref: str = "",
    agent_id: str = "user", now: Optional[float] = None, trace: Any = None,
    conversation_id: str = "",
) -> tuple[IngestResult, list[dict]]:
    """**一次** LLM 调用 piggyback:抽 facts(写进记忆)+ decisions(返回给调用方路由进决策结晶)。

    facts 写法与 ingest 一致(provenance/freshness/去重在 mem.write 里);decisions 不在此写,
    由 console 侧 `crystallize_candidates` 走双关门(避免 cognition 依赖 console)。

    `conversation_id`(Q2 出处回链):产生这批轮的 Conversation.id → 进 provenance,
    面板"对话沉淀"条目据此点回那次对话。空 = 无会话上下文,不写键(老数据同形,优雅降级)。
    """
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.schemas.cognition import Belief

    if now is None:
        now = time.time()
    material = format_turns(turns)
    if not material.strip():
        return IngestResult(written=0, raw="(无可蒸馏内容)"), []
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    # 约束解码底层:带组合 schema(facts+decisions 对象)→ 支持的 provider 保证吐合法 JSON 对象;
    # 不支持的网关侧退回无约束。上层 parse_combined 宁空勿毒不动,作二层兜底。老网关/测试桩不认
    # response_schema kwarg → 捕 TypeError 剥掉重调(与网关内部降级同纪律),请求照发不崩。
    msgs = [{"role": "user", "content": material}]
    sp = SystemPrompt(static=[DISTILL_COMBINED_SYSTEM])
    from karvyloop.gateway.structured import harvest_structured
    from karvyloop.llm.token_ledger import token_source
    # P1b:轮后自动蒸馏 piggyback(最高频后台燃烧点)的 token 归到 auto_distill(此前记 unknown)
    with token_source("auto_distill"):
        try:
            stream = gateway.complete(msgs, [], ref, system=sp, response_schema=_COMBINED_SCHEMA)
        except TypeError:
            stream = gateway.complete(msgs, [], ref, system=sp)
        # 约束解码正身可能在工具入参(anthropic 方言)→ 统一收割,别把正身丢了
        out += await harvest_structured(stream)
    facts, decisions = parse_combined(out)
    written: list = []
    for f in facts:
        content = (f.get("content") or "").strip()
        if not content:
            continue
        try:
            prov = {"source": "conversation", "agent": agent_id, "ts": now,
                    "trace_ref": "", "kind": f.get("kind", "fact"),
                    "provisional": True}   # 质量门②:auto 蒸 = 低置信,不与人审同权
            if conversation_id:
                prov["conversation_id"] = conversation_id   # Q2 出处回链:点回产生它的那次对话
            b = Belief(content=content, provenance=prov,
                       freshness_ts=now, scope="personal")
            mem.write(b)
            written.append(b)
        except Exception:
            pass
    extends: list = []
    conflicts: list = []
    if written:
        # 写入路径 supersede(与 ingest_material 同一咽喉;失败内部自吞,原库不动)。
        # 摄入调和:duplicate 高置信自动并;extends 素材带回给 console 升卡。
        from karvyloop.cognition.conflict import run_supersede_pass
        sup = await run_supersede_pass(written, mem=mem, gateway=gateway,
                                       model_ref=model_ref, now=now, trace=trace)
        extends = list(sup.get("extends") or [])
        # D2:supersede 要推翻你钉住/人审的旧记忆 → 收回冲突素材,由 console 侧升 H2A「记忆冲突」卡
        # (与 ingest_material 同一处理:此前这里漏收 → routes_memory 的 _raise_memory_conflicts
        # 对后台蒸馏路径恒空转,pinned 低权威 belief 在后台冲突时被保护但不弹卡)。
        conflicts = list(sup.get("conflicts") or [])
        # 标签预计算(#61 研判①a + 反向标签,与 ingest_material 同一接缝):蒸馏产物措辞高度
        # 模板化,语义标签是同义改写召回的唯一救场层;失败自吞,daily 慢侧回填。
        cc = getattr(mem, "concept_cache", None)
        if cc is not None:
            try:
                from karvyloop.cognition.concepts import tag_beliefs
                from karvyloop.llm.token_ledger import token_source
                with token_source("belief_tags"):
                    await tag_beliefs(written, cache=cc, gateway=gateway,
                                      model_ref=model_ref, trace=trace)
            except Exception:
                pass
    return IngestResult(written=len(written), raw=f"facts={len(facts)} decisions={len(decisions)}",
                        extends=extends, conflicts=conflicts), decisions


__all__ = [
    "DISTILL_BATCH", "DISTILL_COMBINED_SYSTEM", "format_turns", "should_distill",
    "warmup_batch", "distill_turns", "parse_combined", "distill_turns_with_decisions",
    "strip_recall_echo",
]
