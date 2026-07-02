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


async def broadcast_proposal(app: Any, proposal: Any) -> int:
    """把一条 Proposal 广播给所有 WS clients。

    Args:
        app: FastAPI app(读 app.state.ws_clients)
        proposal: 任何有 `.to_dict()` 的对象(duck type — IntentAnalyst.Proposal)

    Returns:
        成功推送的 client 数量(死连接被剔除,不计入)

    K5:本函数**只推建议**,不替用户决策(决策走 ws.h2a_decision → decision_to_envelope)。
    """
    # D5(docs/30 PR-2):推给用户前先进待决议表 → ACCEPT 时凭 proposal_id 查回兑现。
    registry = getattr(app.state, "proposal_registry", None)
    if registry is not None and getattr(proposal, "proposal_id", ""):
        try:
            registry.register(proposal)
        except Exception as e:  # 登记失败不该阻断推送
            logger.debug(f"[proposals] registry.register 失败(不阻断推送): {e}")

    # 口味命中率(taste_eval):卡片发出=系统**先押注**"我猜你会怎么拍"(fire-and-forget,
    # 绝不拖慢推送;押注失败不计入=宁空勿毒)。拍板后在 record_decision_signals 对账。
    _schedule_taste_bet(app, proposal)

    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    payload = proposal.to_dict()  # duck type:不直接 import Proposal
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
    """

    def __init__(self, app: Any, analyst: Any) -> None:
        self._app = app
        self._analyst = analyst

    async def on_event(self, chunk: Any) -> tuple[Optional[Any], int]:
        """事件驱动:analyst.on_event → 推。"""
        proposal = self._analyst.on_event(chunk)
        return await self._maybe_push(proposal)

    async def boot(self, recent_n: int = 20) -> tuple[Optional[Any], int]:
        """启动一次:analyst.boot_poll → 推。"""
        proposal = self._analyst.boot_poll(recent_n=recent_n)
        return await self._maybe_push(proposal)

    async def daily(self, recent_n: int = 50) -> tuple[Optional[Any], int]:
        """每天一次:analyst.daily_poll → 推。"""
        proposal = self._analyst.daily_poll(recent_n=recent_n)
        return await self._maybe_push(proposal)

    async def _maybe_push(self, proposal: Optional[Any]) -> tuple[Optional[Any], int]:
        if proposal is None:
            return None, 0
        sent = await broadcast_proposal(self._app, proposal)
        return proposal, sent


__all__ = [
    "WS_TYPE_H2A_PROPOSAL",
    "ProposalPump",
    "broadcast_proposal",
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
