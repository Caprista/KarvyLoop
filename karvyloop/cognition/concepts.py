"""cognition/concepts.py — 给 Belief 抽核心概念/实体(LLM Wiki 式),做认知图谱的**语义边**。

Hardy 选 B(LLM 抽概念 + wiki 互链,而非 embedding/向量调参——后者是已否决方向)。参照卡帕西
《知识自生长》= LLM Wiki:把知识**编译**成概念页/实体页 + `[[互链]]`(编译不是检索)。

本模块:① `extract_concepts_batch` —— 一次 LLM 调用给一批 Belief 各抽 2-4 个概念(严解析、宁空勿毒);
② `ConceptCache` —— content-hash → concepts 持久化(**编译一次、高效运行**:抽过的看图时零 LLM);
   外加**别名表**(同义标签收敛的落点:老标签保留为 alias 继续可匹配,不重写历史 beliefs);
③ `assign_tags` / `tag_beliefs` —— **写入路径**批量打标(#61 研判① + 反向标签):ingest/auto_distill
   写完新条后与 supersede 同节奏抽一次入缓存;召回种子的语义标签层读的就是它,打字热路径零 LLM 铁律不动。

**反向标签(Hardy,受控词表 reuse-first)**:向量化是"把自己的内容变成坐标去和别的比";
这里反过来——把**既有标签词表**拿来给新认知归类:有对应标签就挂(复用原词),没有才新建。
比对发生在**离散符号层**(标签名),零向量、可解释、可手改。三护栏:
  ① 词表不全量塞 prompt:候选 = 新内容词面重叠预筛 + 高频标签补位,top-K(零 LLM 预筛);
  ② 新建标签是**显式事件**:确定性判定(不信 LLM 自报),落 Trace kind=tag_created,词表不许悄悄发散;
  ③ 同义收敛在 daily 慢侧(console/tag_merge_tick)自动并进别名表(标签是派生数据非用户数据,
     可自动合并但留审计痕:别名表记 via/ts + Trace kind=tag_merged)。
图怎么连见 graph.concept_graph(共享概念=语义边);召回怎么用见 spread(标签进种子+边)。
标签就是把"语义相似"预计算成可 grep 的词面(创建时打一次,查询时纯词面匹配,无向量)。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from karvyloop.karvy.fastbrain.trace_habit import _extract_json_array

# 标签词表事件在 Trace 里的 task_id(tag_created / tag_merged 都挂这个,审计一处可查)
TAG_VOCAB_TASK_ID = "tag_vocabulary"

_CONCEPT_INSTRUCTION = (
    "为下面每条知识抽 2-4 个**核心概念/实体**(像 wiki 的概念页/实体页:人 / 项目 / 技术 / 主题 / 偏好)。"
    "**严格只输出一个 JSON 二维数组**,每条对应一个字符串数组,**顺序和条数与输入完全一致**,别的话都不要。"
    '示例:输入 2 条 → [["Python","后端"],["周报","自动化"]]'
)

# 反向标签(reuse-first)版指令:候选词表随 user 消息给(动态内容不进 static system)。
# 保留"核心概念"字样 —— 测试桩按它路由,别改丢。
_REUSE_INSTRUCTION = (
    "为下面每条知识抽 2-4 个**核心概念/实体**标签(像 wiki 的概念页/实体页:人 / 项目 / 技术 / 主题 / 偏好)。\n"
    "【复用优先】「已有标签」列出了这个知识库正在使用的标签。能用已有标签表达的,**必须原样复用**,"
    "不许另造同义新词(比如已有「深色主题」就不要新造「夜间模式」)。"
    "只有已有标签里确实没有等义的才新建,并在 created 里给一句新建理由。\n"
    "**严格只输出一个 JSON 对象**:{\"tags\": <二维数组,每条知识对应一个字符串数组,顺序条数与输入完全一致>, "
    "\"created\": {\"<新建的标签>\": \"<为什么已有标签都不适用>\"}}。没新建 created 给空对象。别的话都不要输出。"
)

_VOCAB_CANDIDATES_K = 24   # 词表候选 top-K(护栏①:不全量塞 prompt)


async def _llm_text(material: str, instruction: str, *, gateway, model_ref: str = "") -> str:
    """一次受限 LLM 调用(与 ingest/conflict 同 gateway.complete 模式)→ 文本。失败返 ""。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)   # 基建天花板(防病态爆上下文)
    out = ""
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gateway.complete([{"role": "user", "content": material}], [], ref,
                                         system=SystemPrompt(static=[instruction])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        return ""
    return out


def _clean_tag_lists(arr: list, n: int) -> Optional[list]:
    """校验并清洗二维标签数组:长度/类型对不上 → None(调用方按宁空勿毒处理)。"""
    if not (isinstance(arr, list) and len(arr) == n and all(isinstance(x, list) for x in arr)):
        return None
    # 严格:只收字符串、每条封顶 6 个、去空
    return [[str(c).strip() for c in x if isinstance(c, str) and str(c).strip()][:6] for x in arr]


async def extract_concepts_batch(contents: list, *, gateway, model_ref: str = "") -> list:
    """一次调用给一批知识各抽概念(自由打标,无词表——冷启动/图谱面板用)。
    返与 contents 等长的 list[list[str]];解析失败 → 全空(不投毒)。"""
    if not contents:
        return []
    numbered = "\n".join(f"{i + 1}. {(c or '').strip()}" for i, c in enumerate(contents))
    out = await _llm_text(numbered, _CONCEPT_INSTRUCTION, gateway=gateway, model_ref=model_ref)
    try:
        cleaned = _clean_tag_lists(json.loads(_extract_json_array(out)), len(contents))
        if cleaned is not None:
            return cleaned
    except Exception:
        pass
    return [[] for _ in contents]   # 长度/类型对不上 → 全空(回退词面,绝不投毒)


def _parse_reuse_output(text: str, n: int) -> tuple[list, dict]:
    """解析 reuse-first 输出 → (tags 二维数组, created {新标签: 理由})。**宁空勿毒**:
    严格 JSON 对象优先;模型没按对象格式、回了裸二维数组(旧格式)→ 认(created 空);
    都解析不出 → (全空, {})。"""
    empty: tuple[list, dict] = ([[] for _ in range(n)], {})
    t = (text or "").strip()
    if not t:
        return empty
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    data: Any = None
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        tags = _clean_tag_lists(data.get("tags"), n)
        if tags is None:
            return empty
        created_raw = data.get("created")
        created = {}
        if isinstance(created_raw, dict):
            created = {str(k).strip(): str(v).strip()[:200]
                       for k, v in created_raw.items() if str(k).strip()}
        return tags, created
    if isinstance(data, list):
        tags = _clean_tag_lists(data, n)
        if tags is not None:
            return tags, {}
    # 兜底:散文里裹了个合法数组(思考型模型常态)→ 用同一严格抽取器再试一次
    try:
        tags = _clean_tag_lists(json.loads(_extract_json_array(t)), n)
        if tags is not None:
            return tags, {}
    except Exception:
        pass
    return empty


async def extract_concepts_reuse_first(contents: list, *, vocabulary: list, gateway,
                                       model_ref: str = "") -> tuple[list, dict]:
    """反向标签打标:候选词表带进 prompt,能复用就复用,新建要给理由。

    `vocabulary` = 预筛好的候选标签(select_candidate_tags 产物,top-K);空 → 退回自由打标
    (冷启动,库里还没词表)。返回 (与 contents 等长的 tags, created {新标签: 理由})。
    解析失败 → (全空, {})(宁空勿毒,留给 daily 回填重试)。
    """
    if not contents:
        return [], {}
    if not vocabulary:
        return await extract_concepts_batch(contents, gateway=gateway, model_ref=model_ref), {}
    numbered = "\n".join(f"{i + 1}. {(c or '').strip()}" for i, c in enumerate(contents))
    material = "已有标签(能复用必须复用):" + "、".join(vocabulary) + "\n---\n" + numbered
    out = await _llm_text(material, _REUSE_INSTRUCTION, gateway=gateway, model_ref=model_ref)
    return _parse_reuse_output(out, len(contents))


def select_candidate_tags(contents: list, vocab_counts: dict, *, k: int = _VOCAB_CANDIDATES_K) -> list:
    """词表候选预筛(护栏①,零 LLM):与本批内容**词面重叠**的既有标签优先,剩余名额按
    **使用频次**补位(高频标签正是最容易被造出同义新词的;tag 系统的 "tags in use" 惯例)。

    注意:纯重叠预筛对零词面交集的同义(如内容"夜间模式" vs 标签"深色主题")天然看不见——
    高频补位提高它进候选的概率,漏网的由 daily 慢侧 tag_merge_tick 收敛(两层防线,别在这层求全)。
    """
    if not vocab_counts:
        return []
    from karvyloop.cognition.graph import _tokens, count_tag_hits
    text = "\n".join((c or "") for c in contents)
    tl = text.lower()
    toks = _tokens(text)
    memo: dict = {}
    hit, rest = [], []
    for tag, cnt in vocab_counts.items():
        (hit if count_tag_hits([tag], tl, toks, memo) else rest).append((int(cnt or 0), tag))
    hit.sort(key=lambda x: (-x[0], x[1]))
    rest.sort(key=lambda x: (-x[0], x[1]))
    out = [t for _, t in hit[:k]]
    for _, t in rest:
        if len(out) >= k:
            break
        out.append(t)
    return out


def _hash(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]


_TAG_MEMO_CAP = 65536   # tags_for 的内存 memo 上界(防长期运行下极端内容量把 memo 撑爆)
_GROUP_CAP = 6          # 单标签别名展开上界(病态大同义组不灌爆匹配集)


class ConceptCache:
    """content-hash → [concepts] 持久化(原子写)。抽过不再抽(编译一次)。

    外加**别名表**(同义标签收敛,`<name>_aliases.json` 伴生文件):
    alias → {canonical, ts, via}(审计痕)。合并**不重写历史 beliefs 的标签**——
    `tags_for`(召回/supersede 的匹配视图)按同义组**展开**,匹配面变厚;
    `resolve`(图谱/回填 watermark 的原始视图)不展开,两视图分工别混。
    """

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._alias_path = self._path.with_name(self._path.stem + "_aliases.json")
        self._mem: Optional[dict] = None
        self._aliases: Optional[dict] = None   # alias → {"canonical","ts","via"}
        self._groups: Optional[dict] = None    # 根标签 → [同组所有表面形(含根)]
        # 召回热路径 memo:content 串 → 展开后标签(Python 串 hash 有内建缓存,重复查询免重复 sha1)
        self._tag_memo: dict = {}

    def _load(self) -> dict:
        if self._mem is None:
            try:
                d = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
                self._mem = d if isinstance(d, dict) else {}
            except Exception:
                self._mem = {}
        return self._mem

    # ---- 别名表(同义标签收敛的落点) ----

    def _load_aliases(self) -> dict:
        if self._aliases is None:
            try:
                d = (json.loads(self._alias_path.read_text(encoding="utf-8"))
                     if self._alias_path.exists() else {})
                self._aliases = d if isinstance(d, dict) else {}
            except Exception:
                self._aliases = {}
        return self._aliases

    def _save_aliases(self) -> None:
        try:
            tmp = self._alias_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._load_aliases(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._alias_path)
        except Exception:
            pass

    def canonical_of(self, tag: str) -> str:
        """标签的规范形(顺别名链走到根;有环/超深防御性截断)。没别名 → 原样返回。"""
        t = str(tag or "").strip()
        amap = self._load_aliases()
        seen: set = set()
        while t in amap and t not in seen and len(seen) < 8:
            seen.add(t)
            rec = amap[t]
            nxt = str(rec.get("canonical", "")).strip() if isinstance(rec, dict) else ""
            if not nxt or nxt == t:
                break
            t = nxt
        return t

    def add_alias(self, alias: str, canonical: str, *, via: str = "",
                  now: Optional[float] = None) -> bool:
        """记一条同义合并:alias 归到 canonical(的根)。幂等;成环/自指 → False 不动。
        老别名指向 alias 的顺带 re-point 到根(表保持单层,查询 O(1))。审计痕 = ts/via。"""
        a = str(alias or "").strip()
        c = str(canonical or "").strip()
        if not a or not c or a == c:
            return False
        root = self.canonical_of(c)
        if root == a or self.canonical_of(a) == root:
            return False   # 会成环 / 已同组 → 幂等跳过
        amap = self._load_aliases()
        amap[a] = {"canonical": root, "ts": float(now if now is not None else time.time()),
                   "via": (via or "")[:60]}
        for rec in amap.values():
            if isinstance(rec, dict) and rec.get("canonical") == a:
                rec["canonical"] = root
        self._save_aliases()
        self._groups = None
        self._tag_memo.clear()   # 匹配视图变了:全量失效(慢侧才会走到这,不在打字路径)
        return True

    def alias_map(self) -> dict:
        """alias → canonical 的只读快照(审计/展示用)。"""
        return {a: str(r.get("canonical", "")) for a, r in self._load_aliases().items()
                if isinstance(r, dict) and str(r.get("canonical", "")).strip()}

    def _group_index(self) -> dict:
        if self._groups is None:
            g: dict = {}
            for a in list(self._load_aliases().keys()):
                root = self.canonical_of(a)
                members = g.setdefault(root, [root])
                if a not in members:
                    members.append(a)
            self._groups = g
        return self._groups

    def expand_tags(self, tags: list) -> list:
        """匹配视图的同义组展开:每个标签补上它同组的所有表面形(去重保序,单组封顶)。
        零盘 IO 零 LLM(组索引惰性建、随 add_alias 失效)。无别名 → 原样。"""
        groups = self._group_index()
        if not groups:
            return list(tags or [])
        out, seen = [], set()
        for t in (tags or []):
            t = str(t).strip()
            if not t:
                continue
            root = self.canonical_of(t)
            for m in ([t] + [x for x in groups.get(root, ()) if x != t])[:_GROUP_CAP]:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
        return out

    def vocabulary(self) -> dict:
        """既有标签词表(受控词表视图):canonical 标签 → 使用条数(别名折进 canonical)。
        反向标签的预筛数据源;O(缓存条数),只在写入侧/慢侧调,不在打字路径。"""
        counts: dict = {}
        for tags in self._load().values():
            if not isinstance(tags, list):
                continue
            for t in tags:
                t = str(t).strip()
                if not t:
                    continue
                c = self.canonical_of(t)
                counts[c] = counts.get(c, 0) + 1
        return counts

    def tag_lists(self) -> list:
        """缓存里所有条目的**规范化**标签列表(共现结构,daily 同义收敛的候选数据源)。"""
        out = []
        for tags in self._load().values():
            if isinstance(tags, list) and tags:
                seen, row = set(), []
                for t in tags:
                    c = self.canonical_of(str(t).strip())
                    if c and c not in seen:
                        seen.add(c)
                        row.append(c)
                if row:
                    out.append(row)
        return out

    # ---- 主表 ----

    def resolve(self, contents: list):
        """返 (concepts 对齐列表[未命中=None], 未命中的 idx 列表)。**原始视图**(不展开别名):
        图谱画的、回填 watermark 判的都是存了什么,不是匹配什么。"""
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

    def tags_for(self, content: str) -> list:
        """**只读零 LLM**:content → 缓存标签的**匹配视图**(同义组展开;没抽过 → [],调用方退回词面)。
        召回热路径(recall_block → spread 种子/边)与 supersede 候选读的都是这里 —— memo 化,
        同一 content 不重复 sha1/展开(万条级每次召回省 ~10ms)。"""
        hit = self._tag_memo.get(content)
        if hit is None:
            raw = self._load().get(_hash(content))
            base = [str(t).strip() for t in raw if str(t).strip()] if isinstance(raw, list) else []
            hit = self.expand_tags(base) if base else []
            if len(self._tag_memo) >= _TAG_MEMO_CAP:
                self._tag_memo.clear()   # 极端量下整清重建(memo 是加速器不是状态)
            self._tag_memo[content] = hit
        return hit

    def put(self, content: str, concepts: list) -> None:
        cache = self._load()
        cache[_hash(content)] = list(concepts or [])
        self._tag_memo.pop(content, None)   # memo 失效:下次 tags_for 读到新标签
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass


def _trace_tag_event(trace, kind: str, payload: dict) -> None:
    """标签词表事件落 Trace(护栏②审计面)。trace 没接/失败 → 静默跳过(事件是审计不是命脉)。"""
    if trace is None:
        return
    try:
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(task_id=TAG_VOCAB_TASK_ID, kind=kind,
                                payload=payload, source="concepts"))
    except Exception:
        pass


async def assign_tags(contents: list, *, cache: ConceptCache, gateway, model_ref: str = "",
                      trace=None) -> list:
    """reuse-first 打标一批内容并写入缓存(写入路径 `tag_beliefs` 与 daily 回填 tick 共用)。

    流程:词表候选预筛(零 LLM)→ 一次 LLM(复用优先,新建给理由)→ 归一
    (LLM 回了旧别名也折到 canonical,词表卫生;别名仍可匹配,tags_for 会展开)→
    新建标签**确定性判定**(词表里没有的才算新,不信 LLM 自报)落 Trace kind=tag_created。
    返回与 contents 等长的标签列表(空 = 没抽出,调用方记冷却);打上的已 put 进缓存。
    """
    if not contents:
        return []
    try:
        vocab = cache.vocabulary()
    except Exception:
        vocab = {}
    candidates = select_candidate_tags(contents, vocab) if vocab else []
    tag_lists, created = await extract_concepts_reuse_first(
        contents, vocabulary=candidates, gateway=gateway, model_ref=model_ref)
    out: list = []
    for i, content in enumerate(contents):
        ts = tag_lists[i] if i < len(tag_lists) else []
        if ts:
            norm, seen = [], set()
            for t in ts:
                c = cache.canonical_of(t)
                if c and c not in seen:
                    seen.add(c)
                    norm.append(c)
            ts = norm[:6]
        if ts:
            cache.put(content, ts)
            for t in ts:
                if t in vocab:
                    continue
                vocab[t] = 1   # 同批同标签只记一次事件
                _trace_tag_event(trace, "tag_created", {
                    "tag": t, "reason": created.get(t, ""),
                    "belief": (content or "")[:120],
                    "candidates_offered": len(candidates)})
        out.append(ts)
    return out


async def tag_beliefs(beliefs: list, *, cache: ConceptCache, gateway, model_ref: str = "",
                      trace=None) -> int:
    """写入路径的标签预计算(#61 研判①a + 反向标签):给刚写入的 Belief 批量打标入缓存。

    - 与 supersede 同节奏:在 ingest/auto_distill 的**写入侧异步路径**里调,不占打字热路径。
    - 缓存已有的跳过(watermark,零 LLM);一次 batch 调用判整批。
    - **抽空/失败不落缓存**(宁缺勿错标):留给 daily 慢侧回填重试(belief_tags_tick)。
    返回新打上标签的条数。任何异常调用方自吞(标签是增益不是命脉,写入主流程不因它挂)。
    """
    if not beliefs or cache is None or gateway is None:
        return 0
    contents = [getattr(b, "content", "") or "" for b in beliefs]
    _, missing = cache.resolve(contents)
    todo = [i for i in missing if contents[i].strip()]
    if not todo:
        return 0
    tag_lists = await assign_tags([contents[i] for i in todo], cache=cache,
                                  gateway=gateway, model_ref=model_ref, trace=trace)
    return sum(1 for ts in tag_lists if ts)


__all__ = ["extract_concepts_batch", "extract_concepts_reuse_first", "select_candidate_tags",
           "assign_tags", "ConceptCache", "tag_beliefs", "TAG_VOCAB_TASK_ID"]
