"""cognition/ingest.py — 摄入时编译(loop step4b-1:个人知识库的第一块)。

**为什么**:今天的长期记忆是"扁平 markdown + 查询时 grep + 后台蒸馏"(#4 §spec)。
§0.6 context 层 + §4.1 Belief 长期语义库要的是 **摄入时编译(Karpathy 式)**:用户喂材料的
**那一刻**就把它编译成结构化 Belief(原子事实/偏好),而不是塞一坨原文等查询时再 grep。

**怎么对齐宪法**(docs/00,新概念先对齐):
- 它不是新概念 —— 就是 **Belief(记忆)的长期语义库**那一层(§4.1),只是把"写入时机"从
  查询时/后台挪到**摄入时**。
- 与 `distill.py`(对话后台蒸馏)**同源**:都产 Belief、都复用 `MemoryManager.write`
  (provenance/freshness/去重消解都在那)。区别只是触发口:ingest=用户显式喂材料,
  distill=轮后自动。两者共享同一个编译落点(本模块的 Belief 构造)。
- 是"越用越懂你、抄不走"moat 的**陈述性半边**(技能结晶是程序性半边),都是**实例**
  (镜像可抄、你喂出来的实例抄不走,§2.1)。

诚实边界:抽取靠一次受限 LLM 调用(无工具),解析容错(JSON→按行兜底);跨语言/知识图
结构是后续。冲突消解沿用 recall 的 freshness 合并(本模块不另造)。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from karvyloop.schemas.cognition import Belief

# 编译器 system:让模型当"知识编译器",抽关于用户的原子事实/偏好,输出 JSON 数组
INGEST_SYSTEM = (
    "你是 KarvyLoop 的知识编译器。从用户提供的材料里,抽出**关于这个用户**的原子事实与偏好"
    "(每条独立、自足、可单独检索),用于长期记住这个人。\n"
    "只抽材料里**确有依据**的;不杜撰、不泛化成空话。每条尽量短(一句)。\n"
    "每条**先起个 3–12 字的短标题**(一眼看懂这条讲啥)。\n"
    "严格输出 JSON 数组,每个元素 {\"title\": \"<短标题>\", \"content\": \"<一条事实/偏好>\", \"kind\": \"fact|preference\"};"
    "没有可抽的就输出 []。不要输出 JSON 以外的任何文字。"
)

# **通用知识**编译器 system(≠ INGEST_SYSTEM 的"关于用户")。喂料蒸馏流(/memory/feed→你拍板沉淀)
# 沉的是**客观、可复用的知识点**(概念/方法/事实),不是关于某个用户的偏好。真实压测揪出:旧的
# persist 错用了 INGEST_SYSTEM → 通用文章一律抽成 []、沉淀 0 条,整条知识库工作流形同虚设。
KNOWLEDGE_SYSTEM = (
    "你是 KarvyLoop 的知识库编译器。从材料里抽出**客观、通用、可复用的知识点**"
    "(概念、原理、方法、事实;每条独立、自足、能单独检索),用于沉淀进知识库。\n"
    "抽材料里**确有依据**的;不杜撰、不空泛。每条一两句、自带主语(别用'它/这个'指代,"
    "脱离上下文也读得懂)。这不是关于某个用户的偏好,是知识本身。\n"
    "每条**先起个 3–12 字的短标题**(一眼看懂这条讲啥,用于图谱节点/列表,别用正文开头几个字凑数)。\n"
    "严格输出 JSON 数组,每个元素 {\"title\": \"<短标题>\", \"content\": \"<一条知识点>\", \"kind\": \"knowledge\"};"
    "材料确实没有可沉的知识就输出 []。不要输出 JSON 以外的任何文字。"
)

@dataclass
class IngestResult:
    """一次摄入编译的结果。"""
    written: int                       # 成功写入的 Belief 条数
    beliefs: list = field(default_factory=list)   # 写入的 Belief 对象
    skipped: int = 0                   # 空内容 / 写入失败被跳过的条数
    skip_reasons: list = field(default_factory=list)  # 跳过原因(可诊断:100% 跳过时能查为何)
    raw: str = ""                      # 摘要(调试)
    extends: list = field(default_factory=list)   # 摄入调和 extends 半边:待升 H2A 合并卡的素材
                                                  # (run_supersede_pass 判的;console 侧升卡)


_BULLET_RE = re.compile(r"^([-*•]|\d+[.、)])\s+(.*)$")
_MAX_FACT_LEN = 300


def _facts_from_list(data: list) -> list[dict]:
    facts: list[dict] = []
    for item in data:
        if isinstance(item, dict):
            c = (item.get("content") or item.get("fact") or "").strip()
            if c:
                facts.append({"title": (item.get("title") or "").strip(),
                              "content": c, "kind": str(item.get("kind", "fact"))})
        elif isinstance(item, str) and item.strip():
            facts.append({"title": "", "content": item.strip(), "kind": "fact"})
    return facts


def parse_facts(text: str) -> list[dict]:
    """解析编译器输出 → [{"content","kind"}]。

    **JSON 优先且严格**(独立 checker 抓到 HIGH:旧兜底会把 prose / JSON 对象整坨当成一条
    "事实"写进记忆 = 污染长期库)。规则:
      1) 逐行剥 ```fence```,再 json.loads
      2) dict → 解常见 list 键(facts/items/data/...);单 dict 带 content 也认
      3) list → 逐条抽 content/kind
      4) **看起来像 JSON(以 [ 或 { 开头)却解析失败 → 返回 []**(宁可空也不投毒)
      5) 仅当明显是 bullet/编号列表(每行带真 marker)才按行兜底;**prose 段落不抽**
         (无 marker / 含花括号 / 超长 一律丢)
    """
    t = (text or "").strip()
    if not t:
        return []
    # 只剥**外层**围栏对(不是逐行删 ``` ——否则 content 里合法的多行代码块围栏会被删穿,
    # 让有效 JSON 解析失败 → 整批静默丢,与 #2 同类的 invisible data loss,NEW-1)。
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return []

    data: Any = None
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        data = None

    if isinstance(data, dict):
        for k in ("facts", "items", "data", "beliefs", "knowledge"):
            if isinstance(data.get(k), list):
                return _facts_from_list(data[k])
        c = (data.get("content") or data.get("fact") or "").strip()
        return [{"title": (data.get("title") or "").strip(), "content": c,
                 "kind": str(data.get("kind", "fact"))}] if c else []
    if isinstance(data, list):
        return _facts_from_list(data)

    # JSON 解析失败:像 JSON 的(以 [ / { 开头)→ 拒绝投毒,返回 []
    if cleaned[:1] in ("[", "{"):
        return []
    # 仅救"显式列表"(每行带 bullet/编号 marker);prose 段落不抽
    facts: list[dict] = []
    for ln in lines:
        m = _BULLET_RE.match(ln.strip())
        if not m:
            continue
        item = m.group(2).strip()
        if item and len(item) <= _MAX_FACT_LEN and "{" not in item and "}" not in item:
            facts.append({"content": item, "kind": "fact"})
    return facts


async def compile_material(material: str, *, gateway: Any, model_ref: str = "",
                           system: str = INGEST_SYSTEM) -> list[dict]:
    """跑一次受限 LLM 抽取(无工具)→ facts list。复用 gateway.complete(同 forge 摘要器模式)。

    `system`:抽取口径。默认 INGEST_SYSTEM(关于用户的事实);KNOWLEDGE_SYSTEM=通用知识点。

    **必须先 resolve_model**:gateway.complete 要的是**已解析的具体 model id**;调用方常传
    ""(空)或槽位别名(如 console runtime_kwargs)→ 不解析直接喂会让 complete 流空 →
    parse_facts([]) → written=0(真机抓到:单测桩 gateway 忽略 model_ref 故掩盖了它)。
    """
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref  # 解析不了就用原值(测试桩 gateway 无 resolve_model 也能跑)
    # 第一问/docs/40 §1:喂 LLM 的材料过 context engineering 基建天花板(宽松,只防病态爆炸)。
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[system]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return parse_facts(out)


async def ingest_material(
    material: str,
    *,
    gateway: Any,
    mem: Any,                          # MemoryManager
    model_ref: str = "",
    agent_id: str = "user",
    scope: str = "personal",
    source: str = "ingest",
    trace_ref: str = "",
    source_ref: str = "",              # 来源指纹(URL/材料 hash):同一资料重喂时 supersede 用
    now: Optional[float] = None,
    system: str = INGEST_SYSTEM,       # 抽取口径:默认关于用户;KNOWLEDGE_SYSTEM=通用知识
    provisional: bool = False,         # auto 蒸(无人审)标 True:低置信,不与人审沉淀同权
    trace: Any = None,                 # Trace 底座(可选):标签词表事件/自动合并审计落这里
) -> IngestResult:
    """摄入一段材料 → 编译成结构化 Belief → 写进长期记忆(provenance/freshness/去重在 write 里)。

    写入后跑一轮 **supersede 冲突消解**(cognition.conflict.run_supersede_pass):新知识与库里
    相似旧条矛盾/更新时,给输的一方打 `invalid_at`(失效不删);没有相似旧条则零 LLM 直接过。
    """
    if now is None:
        now = time.time()
    material = (material or "").strip()
    if not material:
        return IngestResult(written=0, raw="(空材料)")
    facts = await compile_material(material, gateway=gateway, model_ref=model_ref, system=system)
    written: list = []
    reasons: list = []
    for f in facts:
        content = (f.get("content") or "").strip()
        if not content:
            reasons.append("empty content")
            continue
        prov = {"source": source, "agent": agent_id, "ts": now,
                "trace_ref": trace_ref, "kind": f.get("kind", "fact"),
                "title": (f.get("title") or "").strip(),   # 短标题:图谱节点/列表可读
                "source_ref": (source_ref or "").strip()}  # 来源指纹:同资料重喂 supersede
        if provisional:
            prov["provisional"] = True   # 质量门:auto 蒸的降权(provenance_rank 封顶蒸馏档)
        belief = Belief(content=content, provenance=prov, freshness_ts=now, scope=scope)
        try:
            mem.write(belief)
            written.append(belief)
        except Exception as e:
            # 不静默吞:记下原因(否则 100% 跳过无从诊断,独立 checker 抓到的 MEDIUM)
            reasons.append(f"write failed: {type(e).__name__}: {e}")
    extends: list = []
    if written:
        # 写入路径 supersede(核心接线):失败自吞(run_supersede_pass 内部宁空勿毒,原库不动)。
        # 摄入调和:duplicate 高置信自动合并(审计痕留 invalid_reason/Trace);extends 素材
        # 带回给 console 升 H2A 卡(cognition 不依赖 console,升卡在调用方)。
        from karvyloop.cognition.conflict import run_supersede_pass
        sup = await run_supersede_pass(written, mem=mem, gateway=gateway,
                                       model_ref=model_ref, now=now, trace=trace)
        extends = list(sup.get("extends") or [])
        # 标签预计算(#61 研判①a + 反向标签):新条 reuse-first 打标入 ConceptCache —— 召回
        # 种子的语义层/supersede 候选筛选读的就是它。与 supersede 同节奏(写入侧异步,打字
        # 热路径零 LLM 铁律不动);失败自吞(标签是增益不是命脉,daily 慢侧 belief_tags_tick 会回填)。
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
    return IngestResult(written=len(written), beliefs=written,
                        skipped=len(reasons), skip_reasons=reasons,
                        raw=f"compiled {len(facts)} fact(s)", extends=extends)


async def ingest_knowledge(material: str, *, gateway: Any, mem: Any, model_ref: str = "",
                           scope: str = "personal", source: str = "knowledge",
                           trace_ref: str = "", source_ref: str = "",
                           now: Optional[float] = None, trace: Any = None) -> IngestResult:
    """沉淀**通用知识**进知识库(喂料蒸馏流的 persist 步用)。同 ingest_material 但走 KNOWLEDGE_SYSTEM
    抽客观知识点(kind='knowledge'),不是关于用户的偏好。source_ref=来源指纹(supersede 用)。"""
    return await ingest_material(material, gateway=gateway, mem=mem, model_ref=model_ref,
                                 agent_id="user", scope=scope, source=source, trace_ref=trace_ref,
                                 source_ref=source_ref, now=now, system=KNOWLEDGE_SYSTEM,
                                 trace=trace)


__all__ = ["IngestResult", "INGEST_SYSTEM", "KNOWLEDGE_SYSTEM", "parse_facts",
           "compile_material", "ingest_material", "ingest_knowledge"]
