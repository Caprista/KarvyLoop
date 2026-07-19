"""cognition.conflict — 记忆冲突消解（cognition/conflict.py）。

规格：docs/modules/cognition-memory.md §3 conflict.py + §4 "最新 + 最高 provenance 胜"
- 记忆可靠性三指标:provenance / freshness / conflict(矛盾标记)
- 消解:max(freshness_ts, provenance_rank)
- 矛盾标记:同 content 不同 provenance 留下的冲突由后台 review 处理

**写入路径 supersede(生产接线,不再是死代码)**:`run_supersede_pass` 在
ingest/auto_distill 写入新 Belief 后被调——用已有召回栈(overlap_score+概念标签,无向量)
找 top-k 相似旧条 → 一次便宜 LLM 判"矛盾/更新/重复/补充/无关"(严格 JSON,宁空勿毒,
解析失败=当无关不动旧条)→ 矛盾/更新:给**输的那条**打 `invalid_at`(失效不删,保历史可审计)。
谁输谁赢由 `provenance_rank` 把关:人明说的(user_explicit/ingest)盖过对话蒸馏猜的
(distill_extracted/conversation)——低权威的新条**不能**掀翻高权威的旧条,反被打失效。

**摄入调和(#61 研判③,Hardy 点头的半步)**:同一次 LLM 调用多判两种关系,不加调用次数:
- duplicate(同一论断换措辞,信息量相同)→ **高置信才自动合并**(高置信 = 词面/标签有确定性
  佐证,`_DUPLICATE_AUTO_MIN_SCORE`;合并 = 失效不删输的一方,审计痕在 invalid_reason +
  Trace belief_auto_merged);佐证不足 → 降级 extends 升卡,不自动动库。
- extends(同主题、新条补充了新信息)→ **不动库**,收进返回值 `extends`,由 console 侧升
  merge_knowledge H2A 卡(ACCEPT 才 apply_belief_merge——知识库是护城河资产,加信息的合并人拍板)。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from karvyloop.schemas import Belief

logger = logging.getLogger(__name__)


# ---- provenance 排序权重(越高越权威)----
PROVENANCE_RANK = {
    "user_explicit": 100,    # 用户明确告知(打字 / 文件)
    "trace_verified": 80,   # Trace + 通过验证门
    "trace_observed": 60,   # Trace 投影出来(默认)
    "role_experience": 50,  # 角色经验(过 should_distill 保守门 = 验证过的成功/用户纠正):
                            # **独立档**,高于 auto 蒸(40)、低于人明说(100);(域,角色)隔离
    "distill_extracted": 40,  # 后台蒸馏小模型抽取
    "imported": 20,         # 导入
    "unknown": 0,
}

# 生产里真实写进 provenance.source 的取值 → 权威档位别名。
# (雷达实锤:原表只有抽象档位名,而 ingest/auto_distill 写的是 "ingest"/"conversation"…
# → provenance_rank 对所有真实数据一律返 0,权威表形同虚设。)
# **D1(内测前必修)**:补齐**全部**生产 source —— 漏一个就让人拍板的被机器猜的合法推翻。
# 回归测 test_source_alias_covers_production_sources 枚举全仓 Belief provenance source
# 逐个核对在档(防第三次复发)。
_SOURCE_ALIAS = {
    "ingest": "user_explicit",        # 用户显式喂料(/memory/ingest 摄入编译)
    "knowledge": "user_explicit",     # 喂料蒸馏流人审后 persist 的通用知识(ingest_knowledge 默认)
    "fed": "user_explicit",           # /memory/feed 你拍板沉淀的知识(与 ingest 同是人喂料;D1)
    "user_edit": "user_explicit",     # 记忆面板 ✏️ 手改(账本式取代;D1)
    "cli": "user_explicit",           # `karvyloop memory add` 手动写入(D1)
    "user": "user_explicit",
    "karvy_chat": "user_explicit",    # 聊天里让小卡「记住这句」= 用户显式指令(D1)
    "consolidated": "trace_verified",  # 知识合并条(人 ACCEPT 过 = 过了人审门)
    "roundtable": "trace_observed",   # 圆桌沉淀(系统观察产物)
    "conversation": "distill_extracted",  # 对话自动蒸馏(猜的,低权威)
    "task_insight": "distill_extracted",  # 执行洞察(docs/82 daily tick 自动蒸的,auto 档:
                                          # 永掀不翻 user_explicit;provisional 再封顶一道)
    "role_experience": "role_experience",  # 角色经验(独立档,见 PROVENANCE_RANK;D1)
    "external_runtime": "imported",   # 外部公民供稿经 H2A 采纳(external-origin,低权威但在档;D1)
    "import": "imported",
}

# ---- D2:钉住 / 人审来源的记忆,supersede 绝不背着你悄悄失效(升 H2A 冲突卡)----
# 只列**人审来源**(pin 态另在 memory.is_protected_memory 里查):这些是「你确认过/亲手喂的」,
# 被推翻要你拍板。低权威猜测(conversation/task_insight)不在此列 —— 它们互相取代照旧默默 supersede。
#
# ⚠ D2 真缝修复(docs/89 ②,Hardy 拍"活着的洞现在就修"):`knowledge`/`user`/`karvy_chat` 经
# _SOURCE_ALIAS **归到 user_explicit 档**(你聊天里让小卡「记住这句」= 你的原话),`consolidated`
# 是**人 ACCEPT 过**的知识合并条——它们的**权威档**都是人审级,却漏在保护集外,导致
# 「你亲口让记的东西被机器猜的不弹卡悄悄改掉」(D2 承诺漏一角)。补齐。防再漂移的守卫见
# tests/test_memory_conflict_supersede.py::test_human_authority_sources_are_protected(user_explicit
# 档的 source 必须在此集,红着逼人补 —— 不靠"我记得")。
HUMAN_REVIEWED_SOURCES = frozenset({
    "fed", "user_edit", "cli", "user_explicit", "ingest", "role_experience",
    "knowledge", "user", "karvy_chat",   # 归 user_explicit 档 = 你的原话/人审喂料
    "consolidated",                       # 人 ACCEPT 过的知识合并条
})

# ⚠ P1(连改数程冷审揪出):**经 H2A 卡人采纳**的记忆(provenance.adopted_via)= 你亲手拍板确认过 →
# 同人审来源一样受保护。圆桌高风险结论走 H2A、你 ACCEPT 才落库(proposal_handlers 写
# adopted_via="h2a"),但其 source 仍是 `roundtable`(rank 60 系统观察档、不在 HUMAN_REVIEWED_SOURCES),
# 光看 source 会漏保护"你亲手拍板进库的结论"——它可被任何 rank≥60 机器条(含 Pursuit trace_verified)
# 不弹卡悄悄失效,正是 D2 承诺漏的同一角。故保护判定加"人采纳"这一维(routine 直写无 adopted_via,
# 不受此保护,不过度打扰)。守卫见 test_h2a_adopted_conclusions_are_protected。
HUMAN_ADOPTED_MARKERS = frozenset({"h2a"})


def is_human_authority(provenance: dict) -> bool:
    """一条记忆是否**人审级权威**(受 D2 保护:supersede/invalidate 绝不背着你自动失效它):
    人审来源(HUMAN_REVIEWED_SOURCES)**或** 经 H2A 卡人采纳(adopted_via ∈ HUMAN_ADOPTED_MARKERS)。"""
    prov = provenance or {}
    if str(prov.get("source", "") or "") in HUMAN_REVIEWED_SOURCES:
        return True
    return str(prov.get("adopted_via", "") or "") in HUMAN_ADOPTED_MARKERS


def provenance_rank(provenance: dict) -> int:
    """按 provenance.source 查权重;缺/未知 → 0。

    - 真实 source 值(ingest/conversation/…)经 `_SOURCE_ALIAS` 归到抽象档位。
    - `provenance["provisional"]=True`(auto 蒸的、没过人审)→ 权威封顶 distill_extracted:
      无人审直接写库的条目不与人审沉淀的知识同权。
    """
    prov = provenance or {}
    src = prov.get("source", "unknown")
    src = _SOURCE_ALIAS.get(src, src)
    rank = PROVENANCE_RANK.get(src, 0)
    if prov.get("provisional"):
        rank = min(rank, PROVENANCE_RANK["distill_extracted"])
    return rank


@dataclass
class ConflictReport:
    """冲突消解结果:胜出的 Belief + 被压制的 Belief 列表。"""
    winner: Belief
    losers: list[Belief]
    # 同 content 是否还有别的版本(用于后台 review 触发"矛盾标记")
    has_conflict: bool = False


def resolve(beliefs: Iterable[Belief]) -> Optional[Belief]:
    """单组矛盾 Belief 消解:winner = max(freshness_ts, provenance_rank)。

    beliefs 必须表达同一论断(上层按 content/语义聚类后再调)。
    """
    items = list(beliefs)
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return max(items, key=lambda b: (b.freshness_ts, provenance_rank(b.provenance)))


def detect_conflict(beliefs: Iterable[Belief]) -> ConflictReport:
    """消解 + 矛盾标记。

    has_conflict = True 当有 ≥2 个 Belief 不完全相同(freshness_ts 或 content 不同)。
    """
    items = list(beliefs)
    if not items:
        raise ValueError("detect_conflict 需要至少一个 Belief")
    winner = resolve(items)
    assert winner is not None
    losers = [b for b in items if b is not winner]
    # 矛盾标记:仅当 losers 非空(意味着有不同时间/不同 provenance 的同主题记忆)
    has_conflict = len(losers) > 0
    return ConflictReport(winner=winner, losers=losers, has_conflict=has_conflict)


# ---- 写入路径 supersede(核心接线)----

_SUPERSEDE_TOP_K = 5          # 每条新知识最多比对的相似旧条数
_SUPERSEDE_MAX_OLD = 24       # 一次 LLM 调用里旧条总数上限(挡灌爆)

SUPERSEDE_SYSTEM = (
    "你是 KarvyLoop 的记忆一致性审查器。给你两组关于同一个用户/同一知识库的条目:"
    "「新条目」(刚写入)和「旧条目」(库里已有)。\n"
    "逐对判断新条目与旧条目的关系,只有五种:\n"
    "- contradict:两条**不能同时为真**(如「用户吃素」vs「用户吃肉」)。\n"
    "- update:讲同一件事,新条目是**更新/取代**旧条目的版本(状态随时间变了)。\n"
    "- duplicate:两条说的**是同一件事、信息量相同**(只是措辞不同),留哪条都不丢信息。\n"
    "- extends:讲同一个主题,新条目在旧条目基础上**补充了新信息**;此时给 merged="
    "合并后的一条完整表述(一两句,不丢两边信息)。\n"
    "- unrelated:以上都不是(只是相关、不矛盾不重复不互补的,算 unrelated)。\n"
    "**严格只输出一个 JSON 对象**:{\"pairs\":[{\"new\":<新条目编号>,\"old\":<旧条目编号>,"
    "\"relation\":\"contradict|update|duplicate|extends\",\"merged\":\"<仅 extends 时给>\"}]}"
    "——unrelated 一律不列;没有任何关系就输出 {\"pairs\":[]}。编号必须来自给你的编号,不许编造。"
    "拿不准的一律算 unrelated。别的话都不要输出。"
)

# duplicate 自动合并的高置信门(确定性佐证):候选打分 ≥ 2 分 = 词面强重叠(≥2 个词/bigram 双向
# 命中)或 ≥1 个语义标签命中(2.0×)。只有 LLM 判 duplicate **且**符号层有佐证才自动动库;
# 佐证不足 → 降级 extends 升 H2A 卡(LLM 一家之言不许直接改护城河资产)。
_DUPLICATE_AUTO_MIN_SCORE = 2.0


def parse_supersede_pairs(text: str, n_new: int, n_old: int) -> list[dict]:
    """解析审查器输出 → [{"new":i,"old":j,"relation":...}]。**宁空勿毒**:
    严格 JSON(只剥外层 fence);解析失败/形状不对/编号越界/关系不认识 → 丢弃该项或返 []
    (= 当无关,不动旧条)。"""
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned.startswith("{"):
        return []
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict) or not isinstance(obj.get("pairs"), list):
        return []
    out: list[dict] = []
    seen: set = set()
    for p in obj["pairs"]:
        if not isinstance(p, dict):
            continue
        rel = str(p.get("relation", "")).strip().lower()
        if rel not in ("contradict", "update", "duplicate", "extends"):
            continue   # unrelated / 编造关系 → 不动
        try:
            ni, oi = int(p.get("new")), int(p.get("old"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ni < n_new and 0 <= oi < n_old) or (ni, oi) in seen:
            continue   # 编号越界/重复 → 丢
        seen.add((ni, oi))
        item = {"new": ni, "old": oi, "relation": rel}
        if rel == "extends":
            m = p.get("merged")
            item["merged"] = m.strip()[:2000] if isinstance(m, str) else ""
        out.append(item)
    return out


def _scored_supersede_candidates(new_content: str, olds: list,
                                 concepts: Optional[list] = None) -> list[tuple[float, int]]:
    """候选打分(降序 (score, idx) 列表):overlap 词面+CJK bigram 双向取大,+2.0×概念标签命中。
    这个分同时是 duplicate 自动合并的**高置信佐证**(_DUPLICATE_AUTO_MIN_SCORE 用它把关)。"""
    from karvyloop.context.relevance import overlap_score
    from karvyloop.cognition.graph import _tokens, count_tag_hits
    new_keys = _tokens(new_content or "")
    new_lower = (new_content or "").lower()
    memo: dict = {}
    scored: list[tuple[float, int]] = []
    for j, b in enumerate(olds):
        c = getattr(b, "content", "") or ""
        # 双向词面重叠(overlap_score 不对称:query 词命中 content;两个方向取大再相加保守放大)
        s = float(max(overlap_score(new_content, c), overlap_score(c, new_content)))
        # 概念标签重叠(LLM 创建时打一次的缓存标签;缺就纯词面,不引向量)
        cs = concepts[j] if (concepts and j < len(concepts) and concepts[j]) else None
        if cs:
            s += 2.0 * count_tag_hits(cs, new_lower, new_keys, memo)   # 权重高于单个词面命中
        if s > 0:
            scored.append((s, j))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def find_supersede_candidates(new_content: str, olds: list, *, top_k: int = _SUPERSEDE_TOP_K,
                              concepts: Optional[list] = None) -> list[int]:
    """用**已有召回栈**(overlap_score 词面+CJK bigram;有缓存概念标签再加语义重叠)
    找与新条最相似的旧条下标,按分降序取 top_k。零命中 → [](零 LLM)。**无向量**(铁律)。

    标签命中规则共用 `graph.count_tag_hits`(与召回种子③同一条,别漂移)。旧版
    `tags & _tokens(new_content)` 要求标签**恰好等于**一个 bigram/整词 —— 多字 CJK 标签
    (如"夜间模式")永远不等于 2 字 bigram,语义层形同虚设(独立对抗验收揪出)。"""
    scored = _scored_supersede_candidates(new_content, olds, concepts)
    return [j for _, j in scored[:max(0, top_k)]]


async def run_supersede_pass(new_beliefs: list, *, mem: Any, gateway: Any,
                             model_ref: str = "", now: Optional[float] = None,
                             top_k: int = _SUPERSEDE_TOP_K, trace: Any = None) -> dict:
    """写入后 supersede 一轮:新条 vs 库里相似旧条,矛盾/更新 → 打失效标记(**失效不删**)。

    - 候选=已有召回栈(overlap+概念标签)top-k;**没有候选就不调 LLM**(零成本快路径)。
    - 一次便宜 LLM 调用判整批;解析**宁空勿毒**(失败=当无关,原库不动)。
    - `provenance_rank` 把关:rank(新) >= rank(旧) → 旧条失效;rank(新) < rank(旧) →
      **新条反被打失效**(蒸馏猜的掀不翻人明说的),两条都留库可审计。
    - 摄入调和(同一次调用,不加次数):duplicate 高置信 → 自动合并(失效不删输方,审计痕
      invalid_reason + Trace);extends / 低置信 duplicate → 收进返回值 `extends`,console 升卡。
    - **D2 钉住/人审记忆保护**:当要让**输的旧条**失效、而旧条钉住或人审来源(`_is_protected_old`)
      时,**绝不自动失效** —— 收进返回值 `conflicts`,console 升 H2A「记忆冲突」卡由你裁
      (保留旧/采纳新/都留)。低权威猜测互相取代不受影响(照旧默默 supersede)。
    - **P1a (域,角色)隔离**:候选只在**同一 applies 分区**内配对(通用↔通用、同域同角色↔同域同角色)——
      A 域沉淀绝不改写 B 域记忆状态(与召回侧同一隔离语义,不造第二套)。
    - **D3 同批对称**:新条在某对里已被反杀(nb.invalid_at 置)→ 后续对里不再拿它当胜者杀活旧条。
    - 任何异常吞掉只打日志(写入主流程不因审查挂掉),返回摘要 dict。
    """
    if now is None:
        now = time.time()
    empty = {"checked": 0, "invalidated_old": 0, "invalidated_new": 0,
             "auto_merged": 0, "extends": [], "pairs": [], "conflicts": []}
    news = [b for b in (new_beliefs or []) if getattr(b, "content", "").strip()]
    if not news or mem is None or gateway is None:
        return empty
    try:
        # 旧条池:同 scope、仍有效、非本批新写(按对象身份和 content 双保险排除)。
        # 决策偏好条(source=decision_pref 类)不参与知识冲突(两层问责不同,别互相失效)。
        new_ids = {id(b) for b in news}
        new_contents = {b.content for b in news}
        scopes = {getattr(b, "scope", "personal") for b in news}
        olds: list = []
        seen: set = set()
        for sc in scopes:
            for b in mem.index.all(sc):
                if id(b) in seen or id(b) in new_ids:
                    continue
                seen.add(id(b))
                if b.content in new_contents:
                    continue
                if getattr(b, "invalid_at", None) is not None:
                    continue   # 已失效的不再参赛(但留库可审计)
                if _is_decision_pref(b):
                    continue
                olds.append(b)
        if not olds:
            return empty
        # #61 研判①d:旧条的缓存概念标签传进候选筛选(打分公式里的 2.0×标签命中一直在,
        # 此前全仓无人传参 = 语义层空转)。只读缓存零 LLM;没接/没标签 → None 纯词面。
        old_concepts: Optional[list] = None
        cc = getattr(mem, "concept_cache", None)
        if cc is not None:
            try:
                old_concepts = [cc.tags_for(getattr(b, "content", "") or "") for b in olds]
            except Exception:
                old_concepts = None
        # 每条新知识取 top-k 相似旧条;并集封顶 _SUPERSEDE_MAX_OLD(一次 LLM 判整批)。
        # 顺手留每对的确定性相似分(pair_score)—— duplicate 自动合并的高置信门用它把关。
        cand_idx: list[int] = []
        pair_score: dict = {}   # (new_idx, old_pool_idx) → score
        for ni, nb in enumerate(news):
            nb_key = _applies_key(nb)   # P1a:(域,角色)分区键
            for s, j in _scored_supersede_candidates(nb.content, olds,
                                                     old_concepts)[:max(0, top_k)]:
                if _applies_key(olds[j]) != nb_key:
                    continue   # P1a:跨分区(A域↔B域 / 通用↔域私有)不比对,失效层不漏
                pair_score[(ni, j)] = s
                if j not in cand_idx:
                    cand_idx.append(j)
        cand_idx = cand_idx[:_SUPERSEDE_MAX_OLD]
        if not cand_idx:
            return empty   # 一个字面都不搭 → 无关,零 LLM
        cands = [olds[j] for j in cand_idx]
        out = await _judge(news, cands, gateway=gateway, model_ref=model_ref)
        pairs = parse_supersede_pairs(out, len(news), len(cands))
        inv_old = inv_new = auto_merged = 0
        applied: list[dict] = []
        extends_out: list[dict] = []
        conflicts_out: list[dict] = []
        for p in pairs:
            nb, ob = news[p["new"]], cands[p["old"]]
            rel = p["relation"]
            if rel == "extends":
                # 加信息的合并不自动动库:收给 console 升 H2A 卡(ACCEPT 才 apply_belief_merge)
                rec = _extends_record(nb, ob, p.get("merged", ""))
                extends_out.append(rec)
                _trace_extends(trace, rec)   # P0⑤:产生即留痕(console 升卡失败素材不失踪)
                continue
            if getattr(ob, "invalid_at", None) is not None:
                continue   # 同批里已被失效过(旧条)
            if getattr(nb, "invalid_at", None) is not None:
                continue   # D3:新条已在某对里被反杀 → 不再拿死人当胜者杀活旧条(与 ob 对称)
            if rel == "duplicate":
                score = pair_score.get((p["new"], cand_idx[p["old"]]), 0.0)
                if score < _DUPLICATE_AUTO_MIN_SCORE:
                    # LLM 判 duplicate 但词面/标签佐证不足 → 不够高置信,降级升卡(不自动动库)
                    rec = _extends_record(nb, ob, "")
                    extends_out.append(rec)
                    _trace_extends(trace, rec)   # P0⑤:降级的这半同样产生即留痕
                    continue
                # 高置信自动合并:信息量相同,不需要写合并条 —— 失效不删输的一方即是合并
                # (审计痕:invalid_reason 全文可查 + Trace belief_auto_merged;两条都留库可翻案)
                if provenance_rank(nb.provenance) >= provenance_rank(ob.provenance):
                    winner, loser = nb, ob
                else:
                    winner, loser = ob, nb
                # D2:要失效的是钉住/人审的**旧条** → 不自动合并,升 H2A 冲突卡(你钉的东西系统绝不背着你改)
                if loser is ob and _is_protected_old(mem, ob):
                    rec = _conflict_record(nb, ob, "duplicate", pinned=_pinned(mem, ob))
                    conflicts_out.append(rec)
                    _trace_conflict(trace, rec)
                    continue
                if loser is ob:
                    inv_old += 1
                else:
                    inv_new += 1
                reason = (f"duplicate(auto-merged): same assertion as "
                          f"[{(winner.provenance or {}).get('source', '?')}]: {winner.content[:80]}")
                # force=True:protected-old 已在上面路由去冲突卡,到这里的输方都是允许自动失效的
                ok = mem.invalidate(loser, reason=reason, now=now, force=True)
                auto_merged += 1
                applied.append({"loser": loser.content[:60], "winner": winner.content[:60],
                                "relation": rel, "auto_merged": True, "persisted": bool(ok)})
                if trace is not None:
                    try:
                        from karvyloop.cognition.trace import TraceEntry
                        trace.append(TraceEntry(
                            task_id="memory_reconcile", kind="belief_auto_merged",
                            payload={"loser": loser.content[:120], "winner": winner.content[:120],
                                     "score": score, "persisted": bool(ok)},
                            source="conflict"))
                    except Exception:
                        pass
                continue
            if provenance_rank(nb.provenance) >= provenance_rank(ob.provenance):
                # D2:要失效的是钉住/人审的旧条 → 不自动失效,升 H2A 冲突卡由你裁(核心)
                if _is_protected_old(mem, ob):
                    rec = _conflict_record(nb, ob, rel, pinned=_pinned(mem, ob))
                    conflicts_out.append(rec)
                    _trace_conflict(trace, rec)
                    continue
                # 新条权威不低于旧条 → 旧条失效(Graphiti 式失效不删)。
                # ④时间语义(docs/66:valid_from 只有明确来源才填,**绝不猜**):
                # invalid_at 默认 = 发现时刻(now)。唯一例外:新条 provenance 带**明确来源**的
                # valid_from(仅 converge.sediment_confirmed 从用户明说的绝对日期解析而来)且
                # 早于 now → 旧条"世界里不再为真"的时刻有据可依,invalid_at 回填成它——否则
                # as_of 在 [valid_from, 发现) 窗口新旧两真并存;发现时刻留在 reason 里可审计。
                # 没有明确来源 → 保持 now,绝不编世界时刻(test_valid_from_contract 锁)。
                inv_ts, backfilled = now, False
                vf = (nb.provenance or {}).get("valid_from")
                try:
                    if vf is not None and float(vf) < now:
                        inv_ts, backfilled = float(vf), True
                except (TypeError, ValueError):
                    pass   # 坏时间戳当不可判,不回填(与 as_of 谓词同口径)
                reason = (f"superseded({rel}) by newer belief "
                          f"[{(nb.provenance or {}).get('source', '?')}]: {nb.content[:80]}")
                if backfilled:
                    reason += f" [world-time backfilled from explicit valid_from; discovered@{now:.0f}]"
                ok = mem.invalidate(ob, reason=reason, now=inv_ts, force=True)
                inv_old += 1
                applied.append({"loser": ob.content[:60], "winner": nb.content[:60],
                                "relation": rel, "persisted": bool(ok)})
            else:
                # 新条权威更低(如 auto 蒸的 vs 人明说的)→ 新条反被失效,人明说的站住
                reason = (f"rejected({rel}): lower provenance than existing belief "
                          f"[{(ob.provenance or {}).get('source', '?')}]: {ob.content[:80]}")
                # 输方=新条(刚写入的低权威猜测被人明说的挡下),不是「推翻你确认过的旧记忆」→ 照旧默默失效
                ok = mem.invalidate(nb, reason=reason, now=now, force=True)
                inv_new += 1
                applied.append({"loser": nb.content[:60], "winner": ob.content[:60],
                                "relation": rel, "persisted": bool(ok)})
        return {"checked": len(cands), "invalidated_old": inv_old,
                "invalidated_new": inv_new, "auto_merged": auto_merged,
                "extends": extends_out, "pairs": applied, "conflicts": conflicts_out}
    except Exception as e:
        # 审查是增益不是命脉:失败绝不拖垮写入主流程,也绝不半判乱改库
        logger.warning(f"[conflict] supersede 审查失败(原库不动): {e}")
        return empty


def extends_idem_key(old_content: str, new_content: str) -> str:
    """extends 升卡素材的幂等键 —— 与 merge_knowledge 卡的 proposal_id **同一派生**
    (成员内容 strip 后排序哈希,proposal_registry.proposal_for_merge_knowledge 同式;
    test_extends_trace_and_reject 锁两边不漂移)。同一对 (old,new) 永远同键:
    - 待决期间重复出现 → registry 同 id 覆盖(现成去重,不另造);
    - 用户 REJECT 过 → decision_log 按此键查得到,console 消费端不再弹。"""
    members = sorted(((old_content or "").strip(), (new_content or "").strip()))
    return "merge_knowledge-" + hashlib.sha1("\n".join(members).encode("utf-8")).hexdigest()[:8]


def _extends_record(nb: Belief, ob: Belief, merged: str) -> dict:
    """extends / 低置信 duplicate 的升卡素材(console 用它建 merge_knowledge 卡)。
    merged 空 → console 侧用确定性拼接兜底(两条原文都在库里,拼接不投毒)。"""
    old_c = getattr(ob, "content", "") or ""
    new_c = getattr(nb, "content", "") or ""
    return {
        "old": old_c,
        "new": new_c,
        "merged": (merged or "").strip()[:2000],
        "old_title": (getattr(ob, "provenance", {}) or {}).get("title", ""),
        "new_title": (getattr(nb, "provenance", {}) or {}).get("title", ""),
        # P0⑤:素材从产生起就带幂等键(= 升卡后的 proposal_id),Trace/去重/REJECT 记忆共用
        "idem_key": extends_idem_key(old_c, new_c),
    }


def _trace_extends(trace: Any, rec: dict) -> None:
    """extends 素材**产生即落 Trace**(kind=belief_extends_found)—— P0 修复⑤:
    升卡在 console 侧(handler 异常/进程崩即丢,且此前无任何持久痕迹);Trace=唯一数据源
    院规要求产生端先留痕:幂等键+素材摘要可审计、可恢复(两边原文本就都在库里,没被动过)。
    失败自吞(同 belief_auto_merged:审计痕是增益不是命脉,绝不拖垮写入主流程)。"""
    if trace is None:
        return
    try:
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(
            task_id="memory_reconcile", kind="belief_extends_found",
            payload={"idem_key": rec.get("idem_key", ""),
                     "old": (rec.get("old") or "")[:120],
                     "new": (rec.get("new") or "")[:120],
                     "merged": (rec.get("merged") or "")[:200]},
            source="conflict"))
    except Exception:
        pass


def memory_conflict_idem_key(old_content: str, new_content: str) -> str:
    """记忆冲突卡的幂等键(同一 (old,new) 对永远同键;registry 同 id 覆盖去重)。"""
    members = sorted(((old_content or "").strip(), (new_content or "").strip()))
    return "memory_conflict-" + hashlib.sha1("\n".join(members).encode("utf-8")).hexdigest()[:8]


def _applies_key(b: Belief) -> tuple:
    """一条 Belief 的(域,角色)分区键 —— P1a 失效层隔离用。无 applies = 通用层 ("","")。"""
    ap = (getattr(b, "provenance", None) or {}).get("applies") or {}
    return (str(ap.get("domain", "") or ""), str(ap.get("role", "") or ""))


def _pinned(mem: Any, b: Belief) -> bool:
    """旧条是否钉住(冲突卡展示用;桩/异常 → False)。"""
    try:
        idx = getattr(mem, "index", None)
        return bool(idx is not None and idx.is_pinned(b))
    except Exception:
        return False


def _is_protected_old(mem: Any, b: Belief) -> bool:
    """D2:旧条是否「钉住 / 人审来源」—— 是则 supersede 不自动失效它,升 H2A 冲突卡。

    pin 态问 mem.is_protected_memory(单一真相源,同时查 pin + 人审 source);老 mem 桩没这方法
    → 退回只看 source(HUMAN_REVIEWED_SOURCES),不误伤(fail-open 到「不保护」= 旧默认行为)。"""
    fn = getattr(mem, "is_protected_memory", None)
    if callable(fn):
        try:
            return bool(fn(b))
        except Exception:
            pass
    return is_human_authority(getattr(b, "provenance", None) or {})


def _conflict_record(nb: Belief, ob: Belief, rel: str, *, pinned: bool = False) -> dict:
    """D2 冲突卡素材:①冲突了什么(旧 vs 新原文)②旧条来源+时间③给人裁(console 升卡)。"""
    ob_prov = getattr(ob, "provenance", None) or {}
    nb_prov = getattr(nb, "provenance", None) or {}
    old_c = getattr(ob, "content", "") or ""
    new_c = getattr(nb, "content", "") or ""
    try:
        old_ts = float(ob_prov.get("ts", 0.0) or 0.0) or float(getattr(ob, "freshness_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        old_ts = 0.0
    return {
        "old": old_c,
        "new": new_c,
        "relation": rel,
        "old_source": str(ob_prov.get("source", "") or ""),
        "old_ts": old_ts,
        "old_pinned": bool(pinned),
        "new_source": str(nb_prov.get("source", "") or ""),
        "idem_key": memory_conflict_idem_key(old_c, new_c),
    }


def _trace_conflict(trace: Any, rec: dict) -> None:
    """冲突素材**产生即落 Trace**(kind=memory_conflict_found)—— 同 _trace_extends:console 升卡
    失败/崩了,冲突证据仍可审计、可恢复(两边原文本就都在库里,没被动过)。失败自吞。"""
    if trace is None:
        return
    try:
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(
            task_id="memory_reconcile", kind="memory_conflict_found",
            payload={"idem_key": rec.get("idem_key", ""),
                     "old": (rec.get("old") or "")[:120],
                     "new": (rec.get("new") or "")[:120],
                     "old_source": rec.get("old_source", ""),
                     "relation": rec.get("relation", "")},
            source="conflict"))
    except Exception:
        pass


def _is_decision_pref(b: Belief) -> bool:
    """决策偏好条不参与知识 supersede(问责链不同层)。import 失败当 False(不误伤)。"""
    try:
        from karvyloop.crystallize.decision_pref import is_decision_pref
        return bool(is_decision_pref(b))
    except Exception:
        return False


async def _judge(news: list, olds: list, *, gateway: Any, model_ref: str = "") -> str:
    """一次 LLM 调用判整批(同 ingest/consolidate 的 gateway.complete 模式)。"""
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    lines = ["新条目:"]
    lines += [f"[{i}] {str(getattr(b, 'content', ''))[:200]}" for i, b in enumerate(news)]
    lines.append("旧条目:")
    lines += [f"[{j}] {str(getattr(b, 'content', ''))[:200]}" for j, b in enumerate(olds)]
    material, _ = clip_to_tokens("\n".join(lines), LLM_MATERIAL_TOKENS)
    # P1b:supersede 判官的 token 归到 supersede_judge(此前记 unknown,docs/68 P0-9 长尾大头)
    from karvyloop.llm.token_ledger import token_source
    out = ""
    with token_source("supersede_judge"):
        async for ev in gateway.complete(
            [{"role": "user", "content": material}], [], ref,
            system=SystemPrompt(static=[SUPERSEDE_SYSTEM]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    return out


__all__ = [
    "PROVENANCE_RANK", "provenance_rank", "HUMAN_REVIEWED_SOURCES",
    "HUMAN_ADOPTED_MARKERS", "is_human_authority",
    "ConflictReport", "resolve", "detect_conflict",
    "SUPERSEDE_SYSTEM", "parse_supersede_pairs", "find_supersede_candidates",
    "run_supersede_pass", "extends_idem_key", "memory_conflict_idem_key",
]   # _scored_supersede_candidates/_extends_record/_trace_extends/_conflict_record 是内部件,不出模块
