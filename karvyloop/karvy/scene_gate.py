"""scene_gate — 「猜你想干 v2」刀1:场景主动卡的确定性唤醒门(docs/94 刀1)。

骚扰 = 产品伤害。所有**系统主动**出的场景卡(源A失败重试 / schedule_suggest 手动重复 /
新场景卡)都过这一扇门,三道确定性闸(零 LLM):

① **有限日预算**:每天最多 DEFAULT_SCENE_DAILY_BUDGET(=3)张场景主动卡(可配
   app.state.scene_daily_budget),跨源统一记账;预算日按本地日历重置。用尽当刻出
   **一次性轻回执**「今天的主动建议就这些(N/N)—— 要更多跟我说」(i18n,每天至多一次)。
   **例外不扣不拦**:HIGH_RISK 卡 / 用户主动触发的卡(payload.user_initiated)—— 它们
   不是"主动打扰",从旁直出(现有路径也不经此门,这里只是防御性放行)。
② **指纹冷却**:同 fingerprint 出过**永不再出**(通用化 manual_run_counter 的
   already_suggested 语义;跨重启持久)。
③ **REJECT 负反馈**:某场景 kind 的卡被 REJECT → 同 kind 场景 REJECT_COOLDOWN_S(7 天)
   内不再出(负反馈来自 decision_wire 拍板咽喉的回钩 note_scene_reject)。

落盘:`~/.karvyloop/scene_gate.json`(与 schedules.json / manual_run_counts.json 同家;
path=None = 纯内存,测试不污染真实 home)。**宁空勿毒**:坏文件当空、落盘失败不阻断。
旁路纪律:本模块任何函数**任何异常都吞掉**,绝不冒泡到 drive / 维护 loop。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import pathlib
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 每天最多几张场景主动卡(可配 app.state.scene_daily_budget;<1 视为默认)。
DEFAULT_SCENE_DAILY_BUDGET = 3
# REJECT 负反馈:同场景 kind 冷却这么久(7 天)。
REJECT_COOLDOWN_S = 7 * 86400
# 指纹表容量上限(有界,防无限膨胀;超了按 ts 砍最老的 —— 同 manual_run_counter 口径)。
_MAX_FINGERPRINTS = 2000

# WS 轻回执类型(前端渲染成 chat-notice,一句话,不打断)。
WS_TYPE_SCENE_RECEIPT = "scene_budget_receipt"


def _day_str(ts: float) -> str:
    """本地日历日(预算按用户的"今天"重置,同定时任务的本地墙钟口径)。"""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class SceneGateStore:
    """唤醒门的持久状态:日预算水位 + 指纹表(永不重复)+ REJECT 负反馈表。"""

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self._path = pathlib.Path(path) if path else None
        self._day = ""            # 当前预算日("YYYY-MM-DD")
        self._spent = 0           # 今日已出场景卡数
        self._receipt_day = ""    # 已出过"预算用尽"回执的日子(每天至多一次)
        self._fingerprints: dict[str, float] = {}   # fp -> 出卡 ts(永不再出)
        self._rejects: dict[str, float] = {}        # scene_kind -> REJECT ts(7 天冷却)
        self._load()

    # ---- 持久化(fail-safe:坏文件当空,落盘失败不阻断)----
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[scene_gate] 状态文件损坏,当空(少提几张不致命)")
            return
        if not isinstance(raw, dict):
            return
        try:
            self._day = str(raw.get("day") or "")
            self._spent = max(0, int(raw.get("spent") or 0))
            self._receipt_day = str(raw.get("receipt_day") or "")
            fps = raw.get("fingerprints")
            if isinstance(fps, dict):
                for fp, ts in fps.items():
                    try:
                        self._fingerprints[str(fp)] = float(ts)
                    except (TypeError, ValueError):
                        continue
            rjs = raw.get("rejects")
            if isinstance(rjs, dict):
                for k, ts in rjs.items():
                    try:
                        self._rejects[str(k)] = float(ts)
                    except (TypeError, ValueError):
                        continue
        except Exception:
            pass

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps({
                "day": self._day, "spent": self._spent, "receipt_day": self._receipt_day,
                "fingerprints": self._fingerprints, "rejects": self._rejects,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            logger.warning("[scene_gate] 状态落盘失败(不阻断): %s", e)

    def _prune(self) -> None:
        if len(self._fingerprints) > _MAX_FINGERPRINTS:
            keep = sorted(self._fingerprints.items(), key=lambda kv: kv[1],
                          reverse=True)[:_MAX_FINGERPRINTS]
            self._fingerprints = dict(keep)

    def _roll_day(self, now: float) -> None:
        """预算日重置(本地日历日变了 → 水位/回执标清零)。"""
        day = _day_str(now)
        if day != self._day:
            self._day = day
            self._spent = 0
            # receipt_day 不清 —— 它按日子比对,天变了自然允许新一天的回执

    # ---- 三道闸 ----
    def check(self, fingerprint: str, scene_kind: str, *, budget: int,
              now: Optional[float] = None) -> str:
        """判这张场景卡放不放行:返回 "allow" / "dup"(指纹出过)/
        "rejected"(kind 在 REJECT 冷却期)/ "budget"(今日预算用尽)。只判不记账。"""
        try:
            ts = time.time() if now is None else float(now)
            self._roll_day(ts)
            fp = (fingerprint or "").strip()
            if fp and fp in self._fingerprints:
                return "dup"
            rts = self._rejects.get((scene_kind or "").strip())
            if rts is not None and 0 <= ts - rts < REJECT_COOLDOWN_S:
                return "rejected"
            if self._spent >= max(1, int(budget)):
                return "budget"
            return "allow"
        except Exception as e:  # noqa: BLE001 —— 门坏了宁可不提
            logger.debug("[scene_gate] check 失败(保守不提): %s", e)
            return "budget"

    def charge(self, fingerprint: str, scene_kind: str, *, budget: int,
               now: Optional[float] = None) -> dict:
        """出卡记账:记指纹(永不再出)+ 扣今日预算。返回
        {"spent": 今日已出, "exhausted_now": 本次是否刚好把预算打满且今天还没出过回执}。"""
        try:
            ts = time.time() if now is None else float(now)
            self._roll_day(ts)
            fp = (fingerprint or "").strip()
            if fp:
                self._fingerprints[fp] = ts
                self._prune()
            self._spent += 1
            exhausted_now = (self._spent >= max(1, int(budget))
                             and self._receipt_day != self._day)
            if exhausted_now:
                self._receipt_day = self._day   # 回执每天至多一次(用尽当刻)
            self._save()
            return {"spent": self._spent, "exhausted_now": exhausted_now}
        except Exception as e:  # noqa: BLE001
            logger.debug("[scene_gate] charge 失败(忽略): %s", e)
            return {"spent": 0, "exhausted_now": False}

    def note_reject(self, scene_kind: str, *, now: Optional[float] = None) -> None:
        """REJECT 负反馈:该场景 kind 7 天内不再出(由 decision_wire 拍板咽喉回钩)。"""
        try:
            k = (scene_kind or "").strip()
            if not k:
                return
            self._rejects[k] = time.time() if now is None else float(now)
            self._save()
        except Exception as e:  # noqa: BLE001
            logger.debug("[scene_gate] note_reject 失败(忽略): %s", e)

    def seen(self, fingerprint: str) -> bool:
        return (fingerprint or "").strip() in self._fingerprints

    def spent_today(self, *, now: Optional[float] = None) -> int:
        try:
            self._roll_day(time.time() if now is None else float(now))
            return self._spent
        except Exception:
            return 0


# ---------------------------------------------------------------- app 级接线
def scene_gate(app: Any) -> SceneGateStore:
    """app.state 上懒加载唤醒门(scene_gate.json 与 schedules.json 同家 ~/.karvyloop/)。"""
    st = getattr(app.state, "scene_gate", None)
    if st is not None:
        return st
    cfgp = getattr(app.state, "config_path", "") or ""
    path = (pathlib.Path(cfgp).parent / "scene_gate.json") if cfgp else None
    st = SceneGateStore(path)
    app.state.scene_gate = st
    return st


def scene_budget(app: Any) -> int:
    """今日预算 N(默认 3;app.state.scene_daily_budget 可覆盖)。"""
    try:
        n = int(getattr(app.state, "scene_daily_budget", DEFAULT_SCENE_DAILY_BUDGET))
        return n if n >= 1 else DEFAULT_SCENE_DAILY_BUDGET
    except (TypeError, ValueError):
        return DEFAULT_SCENE_DAILY_BUDGET


async def _broadcast_receipt(app: Any, *, budget: int) -> int:
    """预算用尽的一次性轻回执(WS chat-notice;文本后端 i18n 定稿,前端纯展示)。"""
    from karvyloop import i18n
    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    msg = {"type": WS_TYPE_SCENE_RECEIPT,
           "payload": {"text": i18n.t("scene.budget.receipt", n=budget), "budget": budget}}
    sent = 0
    dead: list = []
    for ws in list(clients):
        try:
            await ws.send_json(msg)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
    return sent


async def emit_gated(app: Any, proposal: Any, *, fingerprint: str, scene_kind: str,
                     now: Optional[float] = None) -> Optional[int]:
    """经唤醒门出一张场景主动卡:门拒 → None(不出);放行 → broadcast,返回 sent 数。

    - HIGH_RISK kind / payload.user_initiated 的卡不是"主动打扰" → 直出、不扣不记(例外)。
    - 放行的卡 payload 打 scene_kind 标(REJECT 负反馈在拍板咽喉凭它回钩)。
    - 记账在 broadcast **之前**(宁可少提也别乱提:广播失败也算提过,不重弹)。
    - 任何异常返 None(旁路,绝不冒泡)。
    """
    try:
        # 例外不扣:HIGH_RISK / 用户主动触发 —— 从旁直出(它们不是主动打扰)
        exempt = False
        try:
            from karvyloop.karvy.silence import HIGH_RISK_KINDS
            exempt = ((getattr(proposal, "kind", "") or "") in HIGH_RISK_KINDS
                      or bool((getattr(proposal, "payload", {}) or {}).get("user_initiated")))
        except Exception:
            exempt = False
        from karvyloop.console.proposals import broadcast_proposal
        if exempt:
            return await broadcast_proposal(app, proposal)

        gate = scene_gate(app)
        budget = scene_budget(app)
        verdict = gate.check(fingerprint, scene_kind, budget=budget, now=now)
        if verdict != "allow":
            logger.debug("[scene_gate] 场景卡被门拦(%s):%s / %s",
                         verdict, scene_kind, fingerprint)
            return None
        # payload 打 scene_kind 标(frozen dataclass → replace 出新对象;proposal_id 已派生不重算)
        try:
            proposal = dataclasses.replace(
                proposal, payload={**(getattr(proposal, "payload", {}) or {}),
                                   "scene_kind": scene_kind})
        except Exception:
            pass   # 打不上标只是少了负反馈回钩,卡照出
        res = gate.charge(fingerprint, scene_kind, budget=budget, now=now)
        sent = await broadcast_proposal(app, proposal)
        if res.get("exhausted_now"):
            try:
                await _broadcast_receipt(app, budget=budget)
            except Exception as e:
                logger.debug("[scene_gate] 预算回执广播失败(忽略): %s", e)
        return sent
    except Exception as e:  # noqa: BLE001 —— 旁路绝不外溢
        logger.debug("[scene_gate] emit_gated 失败(忽略): %s", e)
        return None


def note_scene_reject(app: Any, payload: Any, decision: str) -> None:
    """拍板咽喉回钩(decision_wire 调):场景卡被 REJECT → 同 kind 7 天冷却。fail-soft。"""
    try:
        if (decision or "").upper() != "REJECT":
            return
        sk = str((payload or {}).get("scene_kind") or "").strip()
        if sk:
            scene_gate(app).note_reject(sk)
            logger.info("[scene_gate] 场景卡被拒 → %s 场景 7 天内不再主动提", sk)
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_gate] REJECT 负反馈记录失败(忽略): %s", e)


# ---------------------------------------------------------------- 检查入口(两个既有挂点)
async def scene_tick(app: Any, *, now: Optional[float] = None) -> int:
    """一次场景检查:采集当下信号 → 逐条过门 → 放行的广播。返回出卡数;任何异常返 0。

    挂点:慢侧维护 loop 每 tick(app.py)+ drive 收尾旁路(scene_check_after_drive)。
    刀1 判定确定性零 LLM,不阻塞热路径(调用方 fire-and-forget / 维护 tick 侧)。
    docs/94 刀2 例外:被消费面接走的信号会烧一次便宜 relevance LLM(按指纹缓存只判一次;
    --no-llm / 门拒 / 忙 时消费面直接 False,零 LLM 回落刀1 原路)。"""
    try:
        from karvyloop.karvy.scene_signals import collect_scene_signals
        emitted = 0
        for sig in collect_scene_signals(app, now=now):
            # docs/94 刀2:预测定向预执行的**消费面**(只加消费,门/信号判定不动)——
            # schedule_due/task_failed 且 relevance 判"值得先干" → 空闲把活干了,完成后
            # 它自己经本门出「已备好」卡(同一份预算,成功出卡才扣);不适用/不值得/资源
            # 不空闲/消费面异常 → 回落下面的刀1 普通卡路径(0 回归)。
            try:
                from karvyloop.console.scene_preexec import maybe_consume_signal
                if await maybe_consume_signal(app, sig, now=now):
                    continue
            except Exception as e:  # noqa: BLE001 —— 刀2 坏了绝不连坐刀1
                logger.debug("[scene_gate] 刀2 消费面异常(回落普通卡): %s", e)
            sent = await emit_gated(app, sig.proposal, fingerprint=sig.fingerprint,
                                    scene_kind=sig.scene_kind, now=now)
            if sent is not None:
                emitted += 1
        return emitted
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_gate] scene_tick 失败(旁路忽略): %s", e)
        return 0


def scene_check_after_drive(app: Any) -> None:
    """drive 收尾旁路触发(fire-and-forget,镜像 schedule_suggest_after_drive 模式)。

    成功/失败的 drive 都检查(「刚失败」本身就是场景信号)。强引用 + done-callback
    取回异常(防 GC 吞);无事件循环 → 静默跳过。任何失败只 debug,绝不冒泡 drive。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    tasks = getattr(app.state, "_scene_tick_tasks", None)
    if tasks is None:
        tasks = app.state._scene_tick_tasks = set()
    task = loop.create_task(scene_tick(app))
    tasks.add(task)

    def _done(t) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except BaseException:   # CancelledError 等(关停)
            return
        if exc is not None:
            logger.debug("[scene_gate] 后台场景检查异常(少一次场景卡,不致命): %s", exc)

    task.add_done_callback(_done)


__all__ = [
    "DEFAULT_SCENE_DAILY_BUDGET", "REJECT_COOLDOWN_S", "WS_TYPE_SCENE_RECEIPT",
    "SceneGateStore", "scene_gate", "scene_budget",
    "emit_gated", "note_scene_reject", "scene_tick", "scene_check_after_drive",
]
