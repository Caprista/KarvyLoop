"""routes_memory — /api/memory* 端点(个人知识库:摄入/沉淀工作流/列表/最近/异步合并)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

认知库沉淀工作流(Hardy):喂料→抓取分析→知识自生长框架结构化→交流→你拍板沉淀/拒绝。
一次一条、持久化(重启续),不结束不开下一条。distill 引擎在 distill_engine.py(此处直接 import 用)。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from karvyloop.llm.token_ledger import token_source as _token_src

from .distill_engine import (
    _distill_analyze,
    _distill_chat_reply,
    _distill_public,
    _distill_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---- loop step4b:个人知识库(摄入编译 + 列表)----

class MemoryIngestRequest(BaseModel):
    material: str = Field(..., min_length=1, max_length=20000)
    agent_id: str = Field(default="user", max_length=64)


@router.post("/memory/ingest")
async def api_memory_ingest(req: MemoryIngestRequest, request: Request) -> dict[str, Any]:
    """摄入一段材料 → 编译成结构化 Belief 写进个人知识库(loop step4b-1 + 地基)。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接(--no-llm?)"}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "无 gateway,无法编译(--no-llm?)"}
    from karvyloop.cognition.ingest import ingest_material
    try:
        res = await ingest_material(req.material, gateway=gw, mem=mem,
                                    model_ref=rk.get("model_ref", ""), agent_id=req.agent_id,
                                    trace=_main_trace(request.app))
    except Exception as e:
        logger.warning(f"[memory/ingest] 摄入失败: {e}")
        return {"ok": False, "reason": f"摄入失败: {e}"}
    await _raise_extends(request.app, res)
    return {"ok": True, "written": res.written, "skipped": res.skipped,
            "beliefs": [b.content for b in res.beliefs],
            "skip_reasons": res.skip_reasons[:5]}


def _main_trace(app: Any):
    """Trace 底座句柄(标签词表事件/摄入调和审计落这里);--no-llm/无 main_loop → None(照跑)。"""
    return getattr(getattr(app.state, "main_loop", None), "trace", None)


async def _raise_extends(app: Any, res: Any) -> None:
    """摄入调和 extends 半边:IngestResult.extends → merge_knowledge H2A 卡。失败不阻断摄入回执。

    素材不丢的兜底在**产生端**(conflict.run_supersede_pass 落 Trace belief_extends_found,
    P0 修复⑤):这里升卡失败/进程崩,素材仍可审计。REJECT 记忆过滤(拒过的对不再弹)住在
    升卡咽喉 proposals._filter_rejected_extends——本路径和 auto_distill 路径统一生效;
    待决期间同对去重靠现成机制(幂等 proposal_id + registry 同 id 覆盖),不另造。"""
    try:
        ext = getattr(res, "extends", None) or []
        if ext:
            from karvyloop.console.proposals import raise_extends_cards
            n = await raise_extends_cards(app, ext)
            if n:
                logger.info(f"[memory] 摄入调和:升 {n} 张 extends 合并建议卡")
    except Exception as e:
        logger.warning(f"[memory] extends 升卡失败(摄入不受影响): {e}")


# ---- 认知库沉淀工作流(Hardy):喂料→抓取分析→知识自生长框架结构化→交流→你拍板沉淀/拒绝 ----
# 一次一条、持久化(重启续),不结束不开下一条。用 LLM Wiki/知识自生长框架结构化(others/卡帕西)。


def _source_ref(url: str, material: str) -> str:
    """来源指纹:有 URL 用规范化 URL;否则用材料内容 hash。用于"同一资料喂两遍"识别 + supersede。"""
    u = (url or "").strip().rstrip("/")
    if u:
        return u
    mat = (material or "").strip()
    if not mat:
        return ""
    import hashlib
    return "text:" + hashlib.sha1(mat.encode("utf-8")).hexdigest()[:16]


def _extract_url(material: str) -> str:
    import re
    m = re.search(r"https?://\S+", material or "")
    return m.group(0).rstrip(").,。)】>\"'") if m else ""


async def _fetch_url(url: str, *, timeout: float = 12.0, max_chars: int = 16000) -> str:
    """抓链接正文(极简 HTML→text)。本地优先 + 用户主动分享的链接;失败返空。"""
    import re
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                     headers={"User-Agent": "Mozilla/5.0 KarvyLoop"}) as c:
            r = await c.get(url)
            r.raise_for_status()
            txt = r.text
        txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
        txt = re.sub(r"(?is)<[^>]+>", " ", txt)
        txt = re.sub(r"&[a-z]+;", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt[:max_chars]
    except Exception as e:
        logger.warning(f"[distill] 抓链接失败 {url}: {e}")
        return ""


class MemoryFeedRequest(BaseModel):
    material: str = Field(..., min_length=1, max_length=20000)


@router.get("/memory/distill")
def api_memory_distill(request: Request) -> dict[str, Any]:
    """当前待沉淀的那一条(没有→null)。前端开知识库先查这个 —— "下次打开继续聊"。"""
    return {"pending": _distill_public(_distill_store(request.app).current())}


@router.post("/memory/feed")
async def api_memory_feed(req: MemoryFeedRequest, request: Request) -> dict[str, Any]:
    """喂料(第1步):抓链接正文 → 知识自生长框架分析结构化 → 给你看(进"待沟通"态)。

    一次一条:已有待办未结束 → 拒绝,让你先把当前这条聊完(确认沉淀或拒绝)。
    """
    app = request.app
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return {"ok": False, "reason": "memory/gateway 未接(--no-llm?)"}
    store = _distill_store(app)
    if store.current() is not None:
        return {"ok": False, "reason": "还有一条料在沉淀流程里没结束 —— 先把它聊完(确认沉淀或拒绝)再喂下一条。",
                "pending": _distill_public(store.current())}
    material = (req.material or "").strip()
    url = _extract_url(material)
    fetched = material
    if url:
        body = await _fetch_url(url)
        if body:
            fetched = f"[链接 {url} 的内容]\n{body}"
    # 正文没抓到/很薄(如 JS 渲染的 GitHub 页,原始 HTML 几乎只有导航)→ 提醒分析器别凭链接硬推断当事实,
    # 否则会"建议沉淀"但沉淀抽 0(推断 ≠ 正文里的具体知识)。阈值宽松:基本只兜"抓空/只剩链接本身"的情况。
    if url and len((fetched or "").strip()) < len(url) + 400:
        fetched = (f"[注意:链接 {url} 的正文没抓到或很薄,下面几乎只有链接本身。请勿凭链接/仓库名把推断当成事实;"
                   f"在『建议沉淀吗』里如实提示用户先贴正文或补充关键点。]\n\n{fetched}")
    user_ctx = ""
    try:
        user_ctx = mem.recall_block(material, scope="personal", limit=5) or ""
    except Exception:
        pass
    summary = await _distill_analyze(gw, rk.get("model_ref", ""), fetched, user_ctx)
    # 同一资料喂过没?(source 指纹)→ already_fed>0 时前端弹"这份喂过了,沉淀会换新版"
    sref = _source_ref(url, material)
    already = mem.count_source_ref(sref) if sref else 0
    s = store.open(material=material, fetched=fetched[:16000], summary=summary,
                   source_url=url or "", source_ref=sref, already_fed=already)
    return {"ok": True, "session": _distill_public(s), "fetched_url": url or "", "already_fed": already}


class DistillChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/memory/distill/chat")
async def api_memory_distill_chat(req: DistillChatRequest, request: Request) -> dict[str, Any]:
    """沉淀前交流(第3步前半):你对这条料追问/补充,小卡回应,记进 transcript。"""
    app = request.app
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    store = _distill_store(app)
    s = store.current()
    if s is None:
        return {"ok": False, "reason": "没有待沉淀的料"}
    if gw is None:
        return {"ok": False, "reason": "无 gateway(--no-llm?)"}
    reply = await _distill_chat_reply(gw, rk.get("model_ref", ""), s, req.message.strip())
    store.append_turn(who="you", text=req.message.strip())
    store.append_turn(who="karvy", text=reply)
    return {"ok": True, "reply": reply}


class DistillDecideRequest(BaseModel):
    decision: str = Field(..., pattern="^(persist|reject)$")


@router.post("/memory/distill/decide")
async def api_memory_distill_decide(req: DistillDecideRequest, request: Request) -> dict[str, Any]:
    """你拍板(第3步):persist → 沉淀进认知库(编译成 Belief);reject → 丢弃。都结束这条、可开下一条。"""
    app = request.app
    store = _distill_store(app)
    s = store.current()
    if s is None:
        return {"ok": False, "reason": "没有待沉淀的料"}
    if req.decision == "reject":
        store.close()
        return {"ok": True, "decision": "reject"}
    # persist:把抓来的正文编译进 Belief(复用 ingest;失败不丢待办,让你重试)
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return {"ok": False, "reason": "memory/gateway 未接,沉淀失败(待办保留,可重试)"}
    # 沉**通用知识**(ingest_knowledge,非关于用户的 ingest_material —— 真实压测揪出旧的用错口径
    # → 通用文章一律抽 []、沉 0 条)。材料 = 结构化总结(你+小卡聊过的理解)+ 抓来的正文,
    # 让知识抽取既拿到提炼后的要点、又有原文兜底。
    from karvyloop.cognition.ingest import ingest_knowledge
    summary = (s.get("summary") or "").strip()
    body = (s.get("fetched") or s.get("material") or "").strip()
    # Bug B:你在沉淀前跟小卡补充的关键点(transcript 里的 you 轮)**必须**进摄入材料 —— 否则"聊两句补充
    # 再重试"的提示形同虚设(旧版 persist 只喂 summary+body、丢了 transcript,补充等于白补)。
    notes = "\n".join(f"- {x.get('text', '').strip()}"
                      for x in (s.get("transcript") or [])
                      if x.get("who") == "you" and (x.get("text") or "").strip())
    parts = []
    if summary:
        parts.append(f"[结构化分析]\n{summary}")
    if body:
        parts.append(f"[原始材料]\n{body}")
    if notes:
        parts.append(f"[你补充的关键点]\n{notes}")
    material = "\n\n".join(parts) if parts else body
    # Bug1 supersede:这份资料喂过 → **先写新版、再删旧版**(避免写 0 时把旧的也误删 = 净丢失)。
    import time as _time
    sref = (s.get("source_ref") or "").strip()
    _t0 = _time.time()

    async def _try_ingest():
        return await ingest_knowledge(material, gateway=gw, mem=mem,
                                      model_ref=rk.get("model_ref", ""), source="fed",
                                      source_ref=sref, trace=_main_trace(app))
    try:
        res = await _try_ingest()
        # 边界/偏薄材料上,严格知识抽取是**概率性**的(同一份料这次抽 0、下次抽出 → 用户会看到"失败了、
        # 再点一次又成功"的迷惑)。写 0 时**自动重试一发**再判,把这枚硬币多抛一次,而不是把重试甩给用户。
        if res.written == 0:
            logger.info("[distill] persist 首轮抽 0,自动重试一次(抽取有随机性)")
            res = await _try_ingest()
    except Exception as e:
        logger.warning(f"[distill] 沉淀失败: {e}")
        return {"ok": False, "reason": f"沉淀失败(待办保留,可重试): {e}"}
    # 绝不静默写 0(历史 bug:persist 抽出 0 条还报成功 + 悄悄关待办 → 用户"点确认后不进知识库、也没反馈")。
    # 写 0 = 抓取失败 / 模型输出不可解析 → 留着待办、说清原因、**不删旧版**(旧知识保住)。
    if res.written == 0:
        logger.warning(f"[distill] persist 抽出 0 条(不关待办、不删旧版):{res.raw}; skip={res.skip_reasons}")
        return {"ok": False, "written": 0,
                "reason": "分析完成,但没抽出可沉淀的知识点(0 条,已保留待办)。"
                          "多半是没抓到正文——在上面跟小卡补充几句关键点(会一起沉淀),然后重试沉淀。"}
    # 写成功 → 删掉本次之前该来源的旧版(supersede;只删 ts<_t0 的旧,保住刚写的新)
    superseded = mem.purge_source_ref(sref, before_ts=_t0) if sref else 0
    await _raise_extends(app, res)   # 摄入调和:extends 升合并建议卡(人拍板)
    store.close()
    return {"ok": True, "decision": "persist", "written": res.written, "superseded": superseded}


@router.get("/memory")
def api_memory_list(request: Request) -> dict[str, Any]:
    """列个人知识库当前 Belief(管理面 / 验证用)。决策偏好走自己的面,这里排除(免双显)。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"beliefs": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    return {"beliefs": [
        {"content": b.content, "title": b.provenance.get("title", ""),
         "kind": b.provenance.get("kind", "?"),
         "source": b.provenance.get("source", "?"),
         "source_ref": b.provenance.get("source_ref", ""),   # 列表/详情卡显示真实来源(链接/文件)
         # Q2 出处回链:对话蒸馏产物带产生它的会话 id → 面板"对话沉淀"可点回;老数据降级 ""
         "conversation_id": b.provenance.get("conversation_id", ""),
         "freshness_ts": b.freshness_ts}
        for b in mem.index.all("personal") if not is_decision_pref(b)
    ]}


@router.get("/memory/recent")
def api_memory_recent(request: Request, limit: int = 20, scope: str = "",
                      domain: str = "") -> dict[str, Any]:
    """最近沉淀(P1.5 灵魂缺口②:"它记得你且你看得见"小卡)。契约形状冻结:
    {"items":[{"id","content","ts","source","domain"}]},按沉淀时刻(provenance.ts,
    缺则 freshness_ts)降序;content 不带全文,超 300 字截断。纯只读。

    `scope=personal|domain`(空 = 两层都看);`domain=` 给了只看该域的域专属认知。
    """
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"items": []}   # --no-llm / 未接线:诚实空,不猜
    from karvyloop.cognition.memory import belief_recency_ts
    lim = max(1, min(int(limit or 20), 100))
    sc = scope if scope in ("personal", "domain") else None
    items = []
    for b in mem.recent(limit=lim, scope=sc, domain=(domain or "").strip()):
        prov = b.provenance or {}
        content = b.content or ""
        items.append({
            "id": str(prov.get("id", "") or ""),
            "content": content[:300],
            "ts": belief_recency_ts(b),
            "source": str(prov.get("source", "") or ""),
            "domain": str((prov.get("applies") or {}).get("domain", "") or ""),
        })
    return {"items": items}


# ---- Bug2:知识库**异步和解/合并**(整理近重复;H2A suggest+apply,离摄入热路径,无向量)----

@router.post("/memory/consolidate/suggest")
async def api_memory_consolidate_suggest(request: Request) -> dict[str, Any]:
    """点「整理相似知识」→ 一次 LLM 把整库近重复知识点聚类、出**合并建议**(dry-run,不改)。"""
    app = request.app
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return {"ok": False, "reason": "memory/gateway 未接(--no-llm?)", "clusters": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    beliefs = [b for b in mem.index.all("personal") if not is_decision_pref(b)]
    from karvyloop.cognition.consolidate import suggest_consolidation
    try:
        with _token_src("consolidate"):
            clusters = await suggest_consolidation(beliefs, gateway=gw, model_ref=rk.get("model_ref", ""))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[consolidate] 建议失败: {e}")
        return {"ok": False, "reason": f"整理失败: {e}", "clusters": []}
    return {"ok": True, "clusters": clusters}


class MemoryRemoveRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


@router.post("/memory/remove")
def api_memory_remove(req: MemoryRemoveRequest, request: Request) -> dict[str, Any]:
    """删掉一条知识(用户在知识库里管理)。按 content 精确删。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接"}
    n = mem.remove_by_content({req.content})
    return {"ok": n > 0, "removed": n}


class ConsolidateApplyRequest(BaseModel):
    member_contents: list[str] = Field(default_factory=list)
    merged_content: str = Field(default="", max_length=2000)
    merged_title: str = Field(default="", max_length=64)


@router.post("/memory/consolidate/apply")
def api_memory_consolidate_apply(req: ConsolidateApplyRequest, request: Request) -> dict[str, Any]:
    """兑现一簇合并(经你拍板):先写合并条、再删被并的旧条。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接"}
    from karvyloop.cognition.consolidate import apply_belief_merge
    return apply_belief_merge(req.member_contents, req.merged_content,
                              merged_title=req.merged_title, mem=mem)


# ---- 轮后自动蒸馏(从 routes.py 下沉:god-module 拆分,蒸馏维护属 memory 域;零逻辑改动)----

async def maybe_auto_distill(app: Any, mgr: Any) -> Optional[dict]:
    """轮后自动蒸馏(loop step4b):攒够 N 轮未蒸馏 → 把新轮编译成 Belief 写进长期库。

    复用 4b-1 编译器(经 auto_distill.distill_turns)。fire-and-forget 调,**异步晚跑**,故须防:
    - **并发重复蒸**(每轮都 schedule 一个 task):per-conv in-flight 闸 + watermark 在 await 前
      **乐观推进**(单调,`max`)→ 第二个 task 看到已推进/在飞 → 跳过。
    - **TOCTOU**:slice 端点 `end` 只读一次,watermark 推进到 end(不回读 len)。
    - **失败 hammer**:推进后**不回退**(失败该批跳过 + 记日志),否则坏 gateway 每轮重试烧钱。
    - **隐私/隔离**:只蒸**私聊(l0)**进 personal;业务域对话不混进个人库(personal/domain
      路径隔离硬规则)。
    无 memory/gateway/对话 → 跳过。返回 {"written":N};无动作返 None。
    """
    try:
        mem = getattr(app.state, "memory", None)
        if mem is None or mgr is None:
            return None
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        gw = rk.get("gateway")
        if gw is None:
            return None
        conv = mgr.current() if hasattr(mgr, "current") else None
        if conv is None or not getattr(conv, "turns", None):
            return None
        # 只蒸私聊(l0)→ personal;业务域对话不混进个人库
        from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
        peer = getattr(conv, "peer", None)
        if peer is not None and getattr(peer, "domain_id", KARVY_WORLD_DOMAIN) != KARVY_WORLD_DOMAIN:
            return None
        from karvyloop.cognition.auto_distill import should_distill, distill_turns_with_decisions
        marks = getattr(app.state, "distill_watermarks", None)
        if marks is None:
            marks = app.state.distill_watermarks = {}
        inflight = getattr(app.state, "_distill_inflight", None)
        if inflight is None:
            inflight = app.state._distill_inflight = set()
        n = len(conv.turns)
        wm = marks.get(conv.id, 0)
        if not should_distill(n, wm) or conv.id in inflight:
            return None
        end = n                                  # slice 端点只读一次(防 TOCTOU)
        new_turns = list(conv.turns[wm:end])
        inflight.add(conv.id)
        marks[conv.id] = max(wm, end)            # await 前乐观推进(单调;防并发重复蒸)
    except Exception as e:
        logger.warning(f"[auto_distill] 准备阶段异常(跳过本轮): {e}")  # 不静默吞,留诊断信号
        return None
    try:
        # §11 P1b:同一次 LLM 调用 piggyback —— 抽 facts(写记忆)+ decisions(显式陈述源)。
        _trace = getattr(getattr(app.state, "main_loop", None), "trace", None)
        res, decisions = await distill_turns_with_decisions(
            new_turns, gateway=gw, mem=mem, model_ref=rk.get("model_ref", ""), trace=_trace,
            conversation_id=conv.id)   # Q2 出处回链:蒸馏产物记下产生它的这次会话
        if getattr(res, "extends", None):
            try:   # 摄入调和 extends 半边 → 升合并建议卡(REJECT 过滤已内建在升卡咽喉)
                from karvyloop.console.proposals import raise_extends_cards
                await raise_extends_cards(app, res.extends)
            except Exception as e:
                logger.debug(f"[auto_distill] extends 升卡失败(不影响蒸馏): {e}")
        if decisions:
            try:
                from karvyloop.console.decision_wire import crystallize_candidates
                # 聊天来源 = 私聊小卡 → 全局(ctx 空);走双关门(显式 1 次/隐式跨批复现)。
                # 回执(Q3 真机压测逮到的缺口):你聊天里亲口说的偏好也要带 STATE 证据
                # (何时/哪次会话),否则偏好面板"来自你的拍板"对聊天源永远是空——
                # 与 onboarding_intake / H2A 卡路径同形,gist 不复述内容(批级共享,内容在卡上)。
                import time as _t
                ev = [{"ts": _t.time(), "decision": "STATE",
                       "gist": f"对话中明确陈述(conv {conv.id[:8]})"}]
                await crystallize_candidates(app, decisions, evidence=ev)
            except Exception as e:
                logger.debug(f"[auto_distill] 决策偏好结晶失败(不影响蒸馏): {e}")
        return {"written": res.written}
    except Exception as e:
        # 已推进 watermark,不回退 → 失败只跳过该批,不每轮重试 hammer LLM
        logger.warning(f"[auto_distill] 蒸馏失败(该批跳过): {e}")
        # §0.7 fail-loud:后台蒸馏失败不再只 log 静默死,主动 push 给 UI(灭死角)
        try:
            from karvyloop.console.task_events import schedule_system_error
            schedule_system_error(app, "auto_distill", str(e))
        except Exception:
            pass
        return None
    finally:
        inflight.discard(conv.id)


def schedule_auto_distill(app: Any, mgr: Any) -> None:
    """fire-and-forget 调度轮后自动蒸馏(不阻塞对话响应)。保 task 引用防被 GC。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环(同步上下文)→ 跳过
    tasks = getattr(app.state, "_distill_tasks", None)
    if tasks is None:
        tasks = app.state._distill_tasks = set()
    task = loop.create_task(maybe_auto_distill(app, mgr))
    tasks.add(task)

    def _on_done(t: Any) -> None:
        tasks.discard(t)
        # §0.7 fail-loud:防 maybe_auto_distill 之外逃逸的异常静默死(防御性兜底)
        try:
            exc = t.exception()
        except Exception:
            return  # cancelled / 取结果失败 → 不处理
        if exc is not None:
            logger.error(f"[auto_distill] 后台任务逃逸异常: {exc}")
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "auto_distill", str(exc))
            except Exception:
                pass

    task.add_done_callback(_on_done)
