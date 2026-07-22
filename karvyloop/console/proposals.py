"""proposals — IntentAnalyst → console h2a_proposal 推送桥(M3+ 拍 9.0d)。

设计:docs/20 §3.3.5 + docs/25 + 用户原话 2026-06-17。

**本拍 9.0d 职责**:
- 把 IntentAnalyst(小卡私有,9.0c)产生的 `Proposal` 推到 console 的 WS clients
- 推过去后,用户在 console 点 ACCEPT/DEFER/REJECT → 走既有 `decision_to_envelope`(K5 工厂)
- **此前 8.5-C 的 ws.py 协议注释提到 `h2a_proposal` 但从未真 emit** — 本拍补上真路径

**K7-safe 桥接架构**(关键设计):
- IntentAnalyst **不**依赖 console(FB-7 锁,9.0c 测试锁住)
- console **不**直接 import `karvyloop.karvy.atoms.IntentAnalyst`(避免小卡私有泄漏)
- 本模块用 **duck type** 接 Proposal(只调 `.to_dict()`)+ 接 analyst(只调 `boot_poll`/`daily_poll`/`on_event`)
- 谁来 new ProposalPump?**9.0d entry / CLI 接线层**(知道两边的协调者),不是 console 也不是小卡

**灵魂铁律**:
- K5:本模块**不**替用户决策 — 只**推 proposal**,决策仍由用户点 → `decision_to_envelope`
- K5:本模块**不** import / 调 `decision_to_envelope`(那是用户点击后的路径,不是推送路径)
- K7:本模块**不**参与 A2A(只 WS send_json,不动 Courier / EnvelopeRouter)
- 用户原话"小卡可以建议,它不替我做决策":推 proposal = 建议;decision_to_envelope = 用户拍板
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# WS 消息类型(与 ws.py 协议一致)
WS_TYPE_H2A_PROPOSAL = "h2a_proposal"

# docs/92 刀2:右栏待决卡积压限流阈值 —— 可视区(含组壳内卡)已 ≥N 张时,**新来的低价值卡**
# 不再压进可视列,落前端「待办抽屉」(点开才展;高风险 / 用户主动触发的卡永远直出)。
# 后端只当配置源:随 /api/proposals/pending 响应带给前端(boot 配置),不参与判定 ——
# 溢出判定在前端**入列时刻**用 wire 已有字段(high_risk / payload.user_initiated)完成,
# 零新 API 调用。第一版不做 UI 设置项(Hardy 拍 N=7)。
OVERFLOW_DRAWER_N = 7


def proposal_wire_payload(registry: Any, proposal: Any) -> dict:
    """一张卡的对外 payload(WS 推送 / /api/proposals/pending 共用一个出口口径)。

    docs/92 刀1 加两个**派生**字段(不进 Proposal 本体,消费侧口径):
    - `chain_intent`:链源意图(registry.chain_intent,register 时算好 → O(1))——
      前端组头「🔗 关于:{intent}」+ 空理解保护句直引它,零 LLM;
    - `high_risk`:kind ∈ silence.HIGH_RISK_KINDS —— 前端组折叠对高风险卡**永远展开
      置顶**(同刀1b 安全不折叠哲学),判定源唯一(不在前端另抄 kind 表)。
    fail-soft:registry 缺/查询坏 → 只少 chain_intent,卡照常出。
    """
    d = proposal.to_dict()  # duck type:不直接 import Proposal
    try:
        cid = str(d.get("chain_id") or "") or str(d.get("proposal_id") or "")
        intent = registry.chain_intent(cid) if (registry is not None and cid
                                                and hasattr(registry, "chain_intent")) else ""
        if intent:
            d["chain_intent"] = intent
    except Exception:
        pass
    try:
        from karvyloop.karvy.silence import HIGH_RISK_KINDS
        d["high_risk"] = str(d.get("kind") or "") in HIGH_RISK_KINDS
    except Exception:
        d["high_risk"] = False
    return d


async def broadcast_proposal(app: Any, proposal: Any, *, allow_silence: bool = True) -> int:
    """把一条 Proposal 广播给所有 WS clients。

    Args:
        app: FastAPI app(读 app.state.ws_clients)
        proposal: 任何有 `.to_dict()` 的对象(duck type — IntentAnalyst.Proposal)
        allow_silence: 「挣来的静音」拦截开关;silence 模块回退重入时传 False 防递归

    Returns:
        成功推送的 client 数量(死连接被剔除,不计入;被静音接管 → 0)

    K5:本函数**只推建议**,不替用户决策(决策走 ws.h2a_decision → decision_to_envelope)。
    """
    # T1 decision_point(docs/85):决策点在此诞生(登记/广播咽喉)—— 提案的 basis/strength
    # 此前在拍板瞬间随卡蒸发,lifeline 第①站从此有据。静音接管/正常出卡都算"诞生"(诚实);
    # fail-soft:埋点坏,推送/静音/登记行为一字不变。
    try:
        _pid = getattr(proposal, "proposal_id", "") or ""
        if _pid:
            from karvyloop.console.decision_wire import emit_decision_trace
            _ctx = getattr(proposal, "context_ref", None) or {}
            emit_decision_trace(app, "decision_point", _pid, {
                "kind": getattr(proposal, "kind", "") or "",
                "summary": (getattr(proposal, "summary", "") or "")[:160],
                "basis": (getattr(proposal, "basis", "") or "")[:160],
                "strength": round(float(getattr(proposal, "strength", 0.0) or 0.0), 2),
                "context_ref": (f"{_ctx.get('kind', '')}:{_ctx.get('id', '')}"
                                if isinstance(_ctx, dict) and _ctx else ""),
                "source": "broadcast",
            }, source="proposals")
    except Exception as e:
        logger.debug(f"[proposals] T1 decision_point 埋点失败(不阻断): {e}")

    # 挣来的静音(docs/49 机制2 / docs/50 决定1):**register 咽喉**在此 —— 已授权桶的卡
    # 不进待决表、不推卡,由 karvy/silence.py 按口味预测自动兑现 + 完整留痕 + WS 轻通知。
    # 判定链任何一环不满足(高危 kind / 未授权 / 预测非 ACCEPT / 置信不足 / 无 handler)
    # → try_silence 返 False / silence 内部回退到本函数(allow_silence=False),正常出卡。
    if allow_silence:
        try:
            from karvyloop.karvy.silence import try_silence
            if try_silence(app, proposal):
                return 0
        except Exception as e:   # 静音判定失败 → 走正常路径(宁可少静音绝不静音错)
            logger.debug(f"[proposals] 静音判定失败,走正常路径: {e}")

    # D5(docs/30 PR-2):推给用户前先进待决议表 → ACCEPT 时凭 proposal_id 查回兑现。
    registry = getattr(app.state, "proposal_registry", None)
    if registry is not None and getattr(proposal, "proposal_id", ""):
        try:
            registry.register(proposal)
            # docs/92 刀1:register 可能做了"同任务兜底"补上 chain_id(frozen → 表里是
            # 新对象)—— 推送 payload 要用登记后的那份,否则前端拿不到链。
            stored = registry.get(getattr(proposal, "proposal_id", ""))
            if stored is not None:
                proposal = stored
        except Exception as e:  # 登记失败不该阻断推送
            logger.debug(f"[proposals] registry.register 失败(不阻断推送): {e}")

    # 口味命中率(taste_eval):卡片发出=系统**先押注**"我猜你会怎么拍"(fire-and-forget,
    # 绝不拖慢推送;押注失败不计入=宁空勿毒)。拍板后在 record_decision_signals 对账。
    _schedule_taste_bet(app, proposal)

    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    payload = proposal_wire_payload(registry, proposal)  # docs/92 刀1:统一出口(chain_intent/high_risk)
    sent = 0
    dead: list = []
    for ws in list(clients):
        try:
            await ws.send_json({"type": WS_TYPE_H2A_PROPOSAL, "payload": payload})
            sent += 1
        except Exception as e:
            logger.debug(f"[proposals] ws client 推送失败,剔除: {e}")
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
    if sent:
        logger.debug(f"[proposals] h2a_proposal 推送给 {sent} client(s)")
    return sent


def _filter_rejected_extends(app: Any, ext: list) -> list:
    """REJECT 记忆(P0 修复⑤):用户拒过的同对 extends 合并建议,下次不再弹。

    住在升卡咽喉(本函数只被 raise_extends_cards 调)——摄入路径(routes_memory
    ._raise_extends)和 auto_distill 路径(routes.py)都过这里,一处过滤全路径生效。
    "已拒"状态**不新造存储**——就住在 decision_log(H2A 拍板回看流水,entry 接线时
    已落盘):REJECT 时 record_decision_signals 记下 proposal_id,而 extends 素材的
    幂等键与 merge_knowledge 卡 proposal_id 同一派生(conflict.extends_idem_key,
    测试锁不漂移),按键查一次即得。decision_log 未接/查询失败 → 不过滤(宁多弹
    一张卡,勿静默丢建议)。留存边界:decision_log 只保最近 5000 条拍板,更老的
    拒绝自然过期(拒不是永久封杀)。"""
    if not ext:
        return []
    log = getattr(app.state, "decision_log", None)
    if log is None:
        return list(ext)
    try:
        rejected = {str(e.get("proposal_id") or "")
                    for e in log.query(decision="REJECT", limit=5000)}
        rejected.discard("")
    except Exception as e:
        logger.debug(f"[proposals] REJECT 记忆查询失败(不过滤): {e}")
        return list(ext)
    if not rejected:
        return list(ext)
    from karvyloop.cognition.conflict import extends_idem_key
    kept: list = []
    for rec in ext:
        try:
            key = str(rec.get("idem_key") or "") or extends_idem_key(
                str(rec.get("old") or ""), str(rec.get("new") or ""))
        except Exception:
            key = ""
        if key and key in rejected:
            continue   # 你拒过这对合并 → 不再唠叨(素材痕迹仍在 Trace,可审计)
        kept.append(rec)
    return kept


async def raise_extends_cards(app: Any, extends: list, *, now: Optional[float] = None) -> int:
    """摄入调和的 extends 半边升卡(#61 研判③):新沉淀的知识与库里旧条讲同一主题、
    **补充了新信息** → 升 merge_knowledge H2A 卡(ACCEPT 才 apply_belief_merge,复用
    knowledge_tick 同一套卡机制/handler,不另造)。duplicate 高置信的自动合并在
    conflict.run_supersede_pass 里已做,这里只处理"加信息、人拍板"的那半。

    素材来自 IngestResult.extends(cognition 不依赖 console,升卡在这层)。merged 空
    (LLM 没给/低置信 duplicate 降级)→ 确定性拼接兜底(两条原文都已在库,拼接不投毒)。
    proposal_id 按成员内容稳定哈希 → 同对幂等,不唠叨。返回升卡数;单条失败跳过不阻断。
    """
    # REJECT 记忆(P0⑤):拒过的同对不再弹——过滤在升卡咽喉,所有调用路径统一生效
    extends = _filter_rejected_extends(app, extends)
    if not extends:
        return 0
    import time as _time
    if now is None:
        now = _time.time()
    n = 0
    for e in extends:
        try:
            old_c = str(e.get("old") or "").strip()
            new_c = str(e.get("new") or "").strip()
            if not old_c or not new_c or old_c == new_c:
                continue
            merged = str(e.get("merged") or "").strip() or f"{old_c}(补充:{new_c})"
            from karvyloop.karvy.proposal_registry import proposal_for_merge_knowledge
            card = proposal_for_merge_knowledge(
                member_contents=[old_c, new_c],
                member_titles=[str(e.get("old_title") or ""), str(e.get("new_title") or "")],
                merged_content=merged,
                reason="新沉淀的知识点与库里这条讲同一主题且补充了新信息(摄入调和)",
                ts=now)
            await broadcast_proposal(app, card)   # register 咽喉在 broadcast 里(含静音判定)
            n += 1
        except Exception as ex:
            logger.warning(f"[proposals] extends 升卡失败(跳过该对): {ex}")
    return n


async def raise_memory_conflict_cards(app: Any, conflicts: list, *,
                                      now: Optional[float] = None) -> int:
    """D2 记忆冲突升卡:supersede 要推翻你**钉住/人审的旧记忆** → 不自动失效,升 H2A「记忆冲突」卡
    描述冲突(旧 vs 新原文 + 旧条来源/时间)让你裁(保留旧/采纳新/都留)。

    素材来自 run_supersede_pass 的 `conflicts`(cognition 不依赖 console,升卡在这层,同 extends)。
    走 broadcast_proposal 正门(register + 静音判定;memory_conflict ∈ HIGH_RISK_KINDS 硬排除静音)。
    REJECT 记忆:你拒过的同对(proposal_id=idem_key,住 decision_log)不再唠叨。返回升卡数。
    """
    if not conflicts:
        return 0
    import time as _time
    if now is None:
        now = _time.time()
    # REJECT 记忆(同 extends):拒过的同对不再弹(idem_key = 冲突卡 proposal_id,住 decision_log)
    rejected: set = set()
    log = getattr(app.state, "decision_log", None)
    if log is not None:
        try:
            rejected = {str(e.get("proposal_id") or "")
                        for e in log.query(decision="REJECT", limit=5000)}
            rejected.discard("")
        except Exception as e:
            logger.debug(f"[proposals] 冲突卡 REJECT 记忆查询失败(不过滤): {e}")
    n = 0
    from karvyloop.karvy.proposal_registry import proposal_for_memory_conflict
    for c in conflicts:
        try:
            old_c = str(c.get("old") or "").strip()
            new_c = str(c.get("new") or "").strip()
            if not old_c or not new_c or old_c == new_c:
                continue
            idem = str(c.get("idem_key") or "")
            if idem and idem in rejected:
                continue   # 你拒过这对冲突 → 不再唠叨(痕迹仍在 Trace,可审计)
            card = proposal_for_memory_conflict(
                old_content=old_c, new_content=new_c,
                old_source=str(c.get("old_source") or ""),
                old_ts=float(c.get("old_ts") or 0.0),
                old_pinned=bool(c.get("old_pinned")),
                new_source=str(c.get("new_source") or ""),
                relation=str(c.get("relation") or "update"),
                ts=now, idem_key=idem)
            await broadcast_proposal(app, card)
            n += 1
        except Exception as ex:
            logger.warning(f"[proposals] 记忆冲突升卡失败(跳过该对): {ex}")
    return n


async def proactive_from_state(app: Any):
    """loop-step2b:小卡基于**持久化状态**(任务看板)主动产一条建议并广播。

    不依赖 LLM pump —— 是 pump 沉默/未接时的确定性兜底(观察任务看板:有失败任务 → 提议重试)。
    返回 (proposal_or_None, sent_count)。K5:只推建议,用户拍板仍走 h2a_decide。
    """
    try:
        from karvyloop.karvy.proactive import propose_from_tasks
        task_reg = getattr(app.state, "task_registry", None)
        proposal = propose_from_tasks(task_reg)
    except Exception as e:
        logger.debug(f"[proposals] proactive_from_state 失败: {e}")
        return None, 0
    if proposal is None:
        return None, 0
    sent = await broadcast_proposal(app, proposal)
    return proposal, sent


class ProposalPump:
    """IntentAnalyst 触发 + 推 console 的协调者(K7-safe 桥)。

    谁持有它:9.0d entry / CLI 接线层(知道 analyst + app 两边)。

    **三种触发包装**(对应 IntentAnalyst 的 on_event / boot_poll / daily_poll):
    - `on_event(chunk)`:事件驱动 — analyst.on_event → 有 Proposal 就推
    - `boot()`:启动一次 — analyst.boot_poll → 有 Proposal 就推
    - `daily()`:每天一次 — analyst.daily_poll → 有 Proposal 就推

    每个方法返回 (proposal, sent_count):
    - proposal=None → 沉默(IntentAnalyst 判断不够强)
    - proposal=Proposal → 已推给 sent_count 个 client

    **依赖倒置**:analyst 用 duck type(只调 boot_poll/daily_poll/on_event),
    避免 console import 小卡私有 IntentAnalyst。

    **distill 钩子**(修"predict 页签永远空"的真根因):drive 事件只落 Trace **原文**层,
    而 analyst 读的是**摘要**层 —— 此前 raw→summary 提炼器在生产路径无人调用(孤儿函数),
    摘要层永远空 → analyst 永远沉默。接线层(intent_pump)把提炼器作为 callable 注入,
    boot/daily 每次先提炼再分析(duck type:console 不 import fastbrain)。
    """

    def __init__(self, app: Any, analyst: Any, *, distill: Optional[Any] = None) -> None:
        self._app = app
        self._analyst = analyst
        self._distill = distill
        # analyst/distill 是同步 LLM 调用:必须下线程跑,否则整个事件循环(所有 HTTP/WS)
        # 冻结到 LLM 返回(Hardy 实拍:点一次建议,业务域面板载入"异常久远")。
        # Lock 保序:analyst 内部状态按旧语义一次只进一个调用。
        self._work_lock: Optional[Any] = None

    def _lock(self) -> Any:
        # 惰性建锁:__init__ 可能发生在无事件循环的接线期(entry/CLI)
        if self._work_lock is None:
            import asyncio
            self._work_lock = asyncio.Lock()
        return self._work_lock

    def _run_distill(self) -> None:
        """boot/daily 前先跑 raw→summary 提炼(注入的 callable;幂等由提炼器 watermark 保证)。

        fail-loud:提炼失败打 warning(此前这条链静默断掉,页签空得毫无线索)。"""
        if self._distill is None:
            return
        try:
            got = self._distill()
            if got is not None:
                logger.info(
                    f"[proposals] raw→summary 提炼完成:覆盖 {got.get('from_raw_count', '?')} 条原文事件"
                )
        except Exception as e:
            logger.warning(f"[proposals] raw→summary 提炼失败(analyst 只能看旧摘要): {e}")

    async def on_event(self, chunk: Any) -> tuple[Optional[Any], int]:
        """事件驱动:analyst.on_event → 推。(LLM 下线程,不冻事件循环)"""
        import asyncio
        async with self._lock():
            proposal = await asyncio.to_thread(self._analyst.on_event, chunk)
        return await self._maybe_push(proposal)

    async def boot(self, recent_n: int = 20) -> tuple[Optional[Any], int]:
        """启动一次:先 raw→summary 提炼,再 analyst.boot_poll → 推。(LLM 下线程)"""
        import asyncio
        async with self._lock():
            proposal = await asyncio.to_thread(self._boot_sync, recent_n)
        return await self._maybe_push(proposal)

    def _boot_sync(self, recent_n: int) -> Optional[Any]:
        self._run_distill()
        return self._analyst.boot_poll(recent_n=recent_n)

    async def daily(self, recent_n: int = 50) -> tuple[Optional[Any], int]:
        """每天一次:先 raw→summary 提炼,再 analyst.daily_poll → 推。(LLM 下线程)"""
        import asyncio
        async with self._lock():
            proposal = await asyncio.to_thread(self._daily_sync, recent_n)
        return await self._maybe_push(proposal)

    def _daily_sync(self, recent_n: int) -> Optional[Any]:
        self._run_distill()
        return self._analyst.daily_poll(recent_n=recent_n)

    async def _maybe_push(self, proposal: Optional[Any]) -> tuple[Optional[Any], int]:
        if proposal is None:
            return None, 0
        sent = await broadcast_proposal(self._app, proposal)
        return proposal, sent


def trace_aged_defers(app: Any) -> int:
    """B-5 #7(docs/81):DEFER 熬过 48h 首次重浮 → 落 `defer_aged_out` 埋点。

    调用点 = /api/proposals/pending(待决卡重呈现的必经口)。registry 自己判"首次"
    (pop_aged_defers 打标持久),这里只翻成 TraceEntry(带阈值当前值,内测标定用)。
    fail-soft:任何失败返 0,待决列表照常。返回本次落的埋点条数。"""
    try:
        registry = getattr(app.state, "proposal_registry", None)
        pop = getattr(registry, "pop_aged_defers", None)
        if registry is None or not callable(pop):
            return 0
        from karvyloop.console.decision_wire import emit_decision_trace
        from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S
        n = 0
        for row in pop():
            emit_decision_trace(app, "defer_aged_out", row.get("proposal_id", ""), {
                "threshold_s": AGING_THRESHOLD_S,
                "defer_age_s": round(float(row.get("defer_age_s", 0.0)), 1),
                "age_s": round(float(row.get("age_s", 0.0)), 1),
                "kind": row.get("kind", "") or "",
            }, source="proposals")
            n += 1
        return n
    except Exception as e:
        logger.debug(f"[proposals] defer_aged_out 埋点失败(不阻断): {e}")
        return 0


__all__ = [
    "WS_TYPE_H2A_PROPOSAL",
    "OVERFLOW_DRAWER_N",
    "ProposalPump",
    "broadcast_proposal",
    "proposal_wire_payload",
    "raise_fs_access_cards",
    "trace_aged_defers",
]

def _schedule_taste_bet(app: Any, proposal: Any) -> None:
    """异步押一注"用户会 ACCEPT 还是 REJECT"(口味命中率的前瞻端)。

    诚实三律:押注必须在拍板前落库;LLM 失败/无 loop → 不押不计入;元循环 kind 跳过。"""
    import asyncio
    from karvyloop.crystallize.taste_eval import SKIP_KINDS
    store = getattr(app.state, "taste_predictions", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    pid = getattr(proposal, "proposal_id", "") or ""
    kind = getattr(proposal, "kind", "") or ""
    if store is None or gw is None or not pid or kind in SKIP_KINDS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _bet() -> None:
        try:
            from karvyloop.crystallize.decision_pref import is_decision_pref, prealign_block
            from karvyloop.crystallize.taste_eval import predict_decision
            from karvyloop.llm.token_ledger import token_source
            prefs_block = ""
            mem = getattr(app.state, "memory", None)
            if mem is not None:
                try:
                    beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)
                               if is_decision_pref(b)]
                    prefs_block = prealign_block(beliefs, query=getattr(proposal, "summary", "") or "")
                except Exception:
                    prefs_block = ""
            with token_source("taste_predict"):
                got = await predict_decision(
                    gw, rk.get("model_ref", "") or "",
                    summary=getattr(proposal, "summary", "") or "",
                    basis=getattr(proposal, "basis", "") or "", kind=kind,
                    prefs_block=prefs_block)
            if got is not None:
                store.record_prediction(pid, got[0], got[1])
        except Exception as e:
            logger.debug(f"[taste] 押注失败(不计入): {e}")

    task = loop.create_task(_bet())
    tasks = getattr(app.state, "_taste_tasks", None)
    if tasks is None:
        tasks = app.state._taste_tasks = set()
    tasks.add(task)
    task.add_done_callback(tasks.discard)

async def raise_fs_access_cards(app: Any) -> int:
    """drive 收尾:把工具层攒的"想要碰工作区外路径"(note_denied)升成授权卡。

    去重靠 proposal 的稳定 id(path+ops 派生)——同路径反复碰壁只挂一张卡;敏感路径在
    note_denied 已滤掉,永不出卡。返回升卡数。"""
    from karvyloop.capability.fs_grants import get_store
    st = get_store()
    if st is None:
        return 0
    denied = st.pop_denied()
    if not denied:
        return 0
    import time as _t
    from karvyloop.karvy.proposal_registry import proposal_for_fs_access
    raised = 0
    # 同 path 多 op 合并成一张卡(read+write)
    by_path: dict = {}
    for d in denied:
        by_path.setdefault(d["path"], set()).add(d["op"])
    reg = getattr(app.state, "proposal_registry", None)
    from karvyloop.karvy.proposal_registry import KIND_FS_ACCESS
    pending_paths = set()
    if reg is not None:
        pending_paths = {(getattr(pr, "payload", {}) or {}).get("path", "")
                         for pr in reg.pending() if getattr(pr, "kind", "") == KIND_FS_ACCESS}
    for path, ops in by_path.items():
        if path in pending_paths:
            continue   # 同路径卡已挂着(不管 op 组合),不重复骚扰
        card = proposal_for_fs_access(path=path, ops=sorted(ops), ts=_t.time())
        try:
            await broadcast_proposal(app, card)
            raised += 1
        except Exception as e:
            logger.debug(f"[fs_grants] 升授权卡失败: {e}")
    return raised

