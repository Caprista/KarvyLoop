"""routes_memory — /api/memory* 端点(个人知识库:摄入/沉淀工作流/列表/最近/异步合并)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

认知库沉淀工作流(Hardy):喂料→抓取分析→知识自生长框架结构化→交流→你拍板沉淀/拒绝。
一次一条、持久化(重启续),不结束不开下一条。distill 引擎在 distill_engine.py(此处直接 import 用)。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
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


def _audience(request: Request) -> str:
    """本请求的受众:经隧道的**分享方**(非自有设备)由 console 侧咽喉 relay/client.py 注入
    `x-karvy-audience: external`(docs/78 §4.3 / docs/73 §9.6)。自有设备(full scope)不带此标
    → 内部零回归。远端伪造进不来:relay/client.py 的 `_FWD_REQ_HEADERS` 白名单不含它,
    远端自带的一律被丢;方向也安全(远端塞 internal 逃刀被丢,LAN 直打 loopback 自加 external 只会**收紧**自己)。"""
    return request.headers.get("x-karvy-audience", "").strip().lower()


def _audience_role(request: Request) -> str:
    """external 召回的被访角色(per-channel role 绑定,docs/78 §4.3):relay/client.py 咽喉从
    配对记录注入 `x-karvy-audience-role`(百分号编码——role 名可含中文;`_FWD_REQ_HEADERS`
    白名单不含它,远端伪造/剥除都不可能,同 audience 纪律)。没绑/解码失败 → ""(宁空勿毒,
    谓词③ deny-by-default 全拒)。LAN 直打 loopback 自加此头不提权:谓词③只在 external 时
    看它,且放的只是该角色升层兵法(比不带头能看的更窄)。"""
    raw = request.headers.get("x-karvy-audience-role", "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import unquote
        return unquote(raw).strip()
    except Exception:
        return ""


def _deny_external_dump(request: Request) -> None:
    """裸 dump 端点(整库列/最近沉淀)对外**直接拒**:它们绕过 recall_block 的召回过滤,
    audience 白名单刀盖不到 —— 外部分享方一个 GET 就能拉走个人生活事实,是护城河数据的对外
    只读泄露面(docs/73 §9.6 侦察实证)。分享方只该经召回面(带 audience 刀)看被访角色的通用兵法,
    不该看整库。自有设备不受影响。"""
    if _audience(request) == "external":
        raise HTTPException(status_code=403, detail="external_forbidden")


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


# 抓取头:用户主动分享的链接,馆员替他去读。裸 UA("Mozilla/5.0 KarvyLoop")被大量站点当爬虫
# 拒(真机实拍:baike.baidu 对它 403,换真浏览器头 + Accept-Language 立刻 200/105KB;addyosmani
# 也从 3.8KB 拿到全页 42KB)。发真浏览器头是抓取器通例(curl/wget/readability 皆然),用户读自己
# 贴的公开页,不构成滥用。SSRF 逐跳校验照旧(见下 follow_redirects=False + 每跳 check_url)。
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def _fetch_url(url: str, *, timeout: float = 12.0, max_chars: int = 16000) -> str:
    """抓链接正文(极简 HTML→text)。本地优先 + 用户主动分享的链接;失败返空。

    **SSRF 地板(安全审计逮到的旁路缝):URL 来自用户粘贴的"材料",不能被当跳板打云元数据
    (169.254.169.254)或内网。** 与 web 工具的正规抓取(coding/tools/web._http_get)同纪律:
    抓前 + 每次重定向后都过 urlguard.check_url,关掉 httpx 自动跟随、手动逐跳(不给
    "重定向到内网"留缝)。此前这条 feed 路径直接 follow_redirects=True 裸抓,绕过了全仓其余
    地方都在守的 SSRF 闸——本地优先产品尤其:安全是地基,一处旁路即全线失守。"""
    import re
    from urllib.parse import urljoin

    from karvyloop.coding.tools.urlguard import SsrfBlocked, check_url
    try:
        import httpx
    except Exception:
        return ""
    try:
        check_url(url)   # 首个 URL 先过 SSRF 闸
    except SsrfBlocked as e:
        logger.warning(f"[distill] SSRF 拦截,拒抓 {url}: {e}")
        return ""
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout,   # 自己跟,才能逐跳校验
                                     headers=_BROWSER_HEADERS) as c:
            cur = url
            txt = ""
            for _hop in range(6):   # 上限 5 次重定向(同 web._http_get)
                r = await c.get(cur)
                if r.is_redirect and r.headers.get("location"):
                    nxt = str(r.next_request.url) if r.next_request else \
                        urljoin(cur, r.headers["location"])
                    try:
                        check_url(nxt)   # 重定向目标同样过闸(挡 redirect→内网)
                    except SsrfBlocked as e:
                        logger.warning(f"[distill] SSRF 拦截(重定向),拒抓 {nxt}: {e}")
                        return ""
                    cur = nxt
                    continue
                r.raise_for_status()
                txt = r.text
                break
            else:
                logger.warning(f"[distill] 重定向次数过多,放弃 {url}")
                return ""
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


def _superseded_by(reason: str) -> str:
    """从 invalidate 的 reason 串里解析出**取代者内容**(考古层展示"被『…』取代")。

    conflict.py 写的 reason 形如 `superseded(update) by newer belief [ingest]: <取代者内容>` /
    `duplicate(auto-merged): same assertion as [ingest]: <胜者内容>` —— 取代者内容一律在最后一个
    `]: ` 之后。解析不出(人工归档、老格式)→ 返 ""(降级纯"已失效",不猜、不骗)。
    """
    r = (reason or "").strip()
    marker = "]: "
    idx = r.rfind(marker)
    if idx == -1:
        return ""
    tail = r[idx + len(marker):].strip()
    # 世界时刻回填会在内容后追加 ` [world-time backfilled …]`,截掉这段审计注脚只留取代者内容
    cut = tail.find(" [world-time backfilled")
    if cut != -1:
        tail = tail[:cut].strip()
    return tail[:200]


@router.get("/memory")
def api_memory_list(request: Request, include_invalid: int = 0) -> dict[str, Any]:
    """列个人知识库 Belief(管理面 / 验证用)。决策偏好走自己的面,这里排除(免双显)。

    默认只列**当前有效**条(失效条折叠进考古层,不污染"当前知道的")。
    `include_invalid=1`(Q5 记忆考古层)→ 失效条也带出来,每条附:
    - `invalid_at`(失效时刻,活条=None)
    - `invalid_reason`(为什么失效,审计可读)
    - `superseded_by`(取代者内容,从 reason 解析;解析不出=""降级)
    使用信号(Q6 读写审计薄版):每条带 `recall_count` / `last_recalled_ts`
    (Trace 没记 belief 级召回 → 退用 memory.py 既有这两个使用字段,不硬造)。
    """
    _deny_external_dump(request)   # 对外只读裸 dump 洞:分享方一律拒(见 _deny_external_dump)
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"beliefs": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    out = []
    for b in mem.index.all("personal"):
        if is_decision_pref(b):
            continue
        inv = getattr(b, "invalid_at", None)
        if inv is not None and not include_invalid:
            continue   # 失效条:默认折叠,只在 include_invalid 时进考古层
        row = {
            "content": b.content, "title": b.provenance.get("title", ""),
            "kind": b.provenance.get("kind", "?"),
            "pinned": mem.index.is_pinned(b),
            "source": b.provenance.get("source", "?"),
            "source_ref": b.provenance.get("source_ref", ""),   # 列表/详情卡显示真实来源(链接/文件)
            # Q2 出处回链:对话蒸馏产物带产生它的会话 id → 面板"对话沉淀"可点回;老数据降级 ""
            "conversation_id": b.provenance.get("conversation_id", ""),
            "freshness_ts": b.freshness_ts,
            # Q6 读写审计薄版:使用信号(被召回过几次 / 最近何时);从没用过 = 0(诚实,不硬造)
            "recall_count": int(getattr(b, "recall_count", 0) or 0),
            "last_recalled_ts": float(getattr(b, "last_recalled_ts", 0.0) or 0.0),
        }
        if inv is not None:
            # Q5 记忆考古层:失效不删,带出失效时刻 + 原因 + 取代者内容(供面板"✗ 已失效(被『…』取代)")
            row["invalid_at"] = float(inv)
            row["invalid_reason"] = str(getattr(b, "invalid_reason", "") or "")
            row["superseded_by"] = _superseded_by(row["invalid_reason"])
        else:
            row["invalid_at"] = None
        out.append(row)
    return {"beliefs": out}


@router.get("/memory/recall")
def api_memory_recall(request: Request, q: str = "", as_of: Optional[float] = None,
                      limit: int = 8) -> dict[str, Any]:
    """时点召回(Q4:"上个月你以为我在哪家公司?")—— recall_block(as_of=) 接出来。

    `as_of` 给了 → 按"T 时刻它算数吗"过滤(valid_from≤T 且未失效或失效于 T 之后);
    不给 → 当下召回(失效条不出现)。回执标注 `as_of` 供上层回答"按 X 时点的记忆"。
    底座谓词全在(memory.recall_block),这里只是 API 入口;NL 意图识别(聊天里问"当时…")
    是后续 drive 侧接线(见报告降级边界)。
    """
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接(--no-llm?)", "as_of": as_of, "block": ""}
    query = (q or "").strip()
    if not query:
        return {"ok": False, "reason": "缺 q(要召回什么)", "as_of": as_of, "block": ""}
    try:
        lim = max(1, min(int(limit or 8), 50))
        # 对外白名单刀(docs/78 §4.3):分享方 → audience=external,共享层 deny-by-default,
        # 只放被访角色的升层兵法。audience_role 源 = 分享码上的 per-channel role 绑定
        # (pairing 记录 → relay/client.py 咽喉注头 → 这里解码传谓词③)。没绑 role 的分享码
        # 照旧全拒(一条兵法也不漏);自有设备无标零回归。
        aud = _audience(request)
        block = mem.recall_block(query, scope="personal", limit=lim, as_of=as_of,
                                 audience=("external" if aud == "external" else ""),
                                 audience_role=(_audience_role(request) if aud == "external" else ""))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory/recall] 召回失败: {e}")
        return {"ok": False, "reason": f"召回失败: {e}", "as_of": as_of, "block": ""}
    return {"ok": True, "as_of": as_of, "block": block or ""}


@router.get("/memory/recent")
def api_memory_recent(request: Request, limit: int = 20, scope: str = "",
                      domain: str = "") -> dict[str, Any]:
    """最近沉淀(P1.5 灵魂缺口②:"它记得你且你看得见"小卡)。契约形状冻结:
    {"items":[{"id","content","ts","source","domain"}]},按沉淀时刻(provenance.ts,
    缺则 freshness_ts)降序;content 不带全文,超 300 字截断。纯只读。

    `scope=personal|domain`(空 = 两层都看);`domain=` 给了只看该域的域专属认知。
    """
    _deny_external_dump(request)   # 对外只读裸 dump 洞:分享方一律拒(见 _deny_external_dump)
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


class MemoryPinRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    pinned: bool = True


@router.post("/memory/pin")
def api_memory_pin(req: MemoryPinRequest, request: Request) -> dict[str, Any]:
    """📌 pin/unpin 一条知识(记忆主权面板)。pin = 防自动整理归档(distill 归档尊重 pin);
    你亲手锁定的,系统不背着你收走。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接"}
    # key 口径:先原串后 strip(库里可能存着带空白的 content;remove 用原串,这里对齐)
    c = req.content if mem.index.get(req.content) is not None else req.content.strip()
    if mem.index.get(c) is None:
        return {"ok": False, "reason": "not_found"}
    mem.set_pinned(c, req.pinned)
    return {"ok": True, "pinned": bool(req.pinned)}


class MemoryEditRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    new_content: str = Field(..., min_length=1, max_length=2000)


@router.post("/memory/edit")
def api_memory_edit(req: MemoryEditRequest, request: Request) -> dict[str, Any]:
    """✏️ 编辑一条知识 = **账本式取代**,不是原地改(失效不删的账本语义):

    新内容作为新 Belief 写入(source=user_edit,经写咽喉 → mesh 同步照走),旧条打
    invalid_at 进考古层;失效理由对齐 conflict.py 的 superseded 格式 → 考古层自动
    显示"被『新内容』取代"。pin 态随内容迁移。旧=新 → no-op;新内容已存在 → 拒
    (别静默造重复,让人先看见那条)。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接"}
    old_c = req.content if mem.index.get(req.content) is not None else req.content.strip()
    new_c = req.new_content.strip()
    if not new_c or new_c == old_c:
        return {"ok": True, "unchanged": True}
    old = mem.index.get(old_c)
    if old is None:
        return {"ok": False, "reason": "not_found"}
    if getattr(old, "invalid_at", None) is not None:
        # 死条不编辑(对抗验收#2c):否则复活出矛盾对 + 覆盖它原有的失效审计痕。
        # 要改考古层里的旧事,编辑当前活着的那条;翻案是另一个显式动作,不借编辑走后门。
        return {"ok": False, "reason": "invalidated"}
    if mem.index.get(new_c) is not None:
        return {"ok": False, "reason": "exists"}
    import time as _time
    from karvyloop.schemas.cognition import Belief
    now = _time.time()
    prov = dict(old.provenance or {})
    # mesh 戳必须剥掉(对抗验收#4):这是本设备的新写不是远端回放——拷贝旧 provenance 会带上
    # origin_device,写咽喉的回声抑制误判成回放**跳过发事件**,编辑的新内容就静默不出设备了。
    # sync_id 同剥("同 content 同 id"不变量由钩子按新内容重算)。
    prov.pop("origin_device", None)
    prov.pop("sync_id", None)
    prov.update({"source": "user_edit", "ts": now})
    was_pinned = mem.index.is_pinned(old)
    nb = Belief(content=new_c, provenance=prov, freshness_ts=now, scope=old.scope)
    written = mem.write(nb, pinned=was_pinned)
    mem.invalidate(old, reason=f"superseded(user-edit) by newer belief [user_edit]: {new_c}",
                   now=now)
    return {"ok": bool(written), "written": bool(written)}


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
