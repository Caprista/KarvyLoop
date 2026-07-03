"""karvy/silence.py — 「挣来的静音」:口味命中率从**仪表**变**控制器**(docs/49 ⑫机制2、docs/50 决定1)。

此前 taste_eval 的押注/对账只有展示读点(仪表),不影响任何系统行为。本模块把它接成控制器:

1. **分桶命中率**:按 kind(+可选 domain)分桶,**复用 TastePredictionStore 的对账流水**
   (唯一账本,不另起);桶的 kind/domain 由 decision_log 按 proposal_id 关联 —— 两处都在
   拍板单一接缝(record_decision_signals)写入,天然对齐;关联不上的 outcome **不计入**
   (保守:宁可少算,绝不猜桶)。
2. **授权门**:桶内 n≥SILENCE_MIN_N 且命中率≥SILENCE_MIN_HIT_RATE → 出一张
   KIND_SILENCE_GRANT H2A 卡("这类板我最近 N 次押中 M 次,要不要以后替你静音处理?")。
   授权本身**永远走 H2A**,绝不自动扩权(docs/49:OpenClaw allowlist 静默扩权是反面教材)。
3. **静音处理**:ACCEPT 落 ~/.karvyloop/silence_grants.json(可撤销)后,该桶新卡在
   register 咽喉被拦下 → 按口味预测**自动执行 + 完整留痕**(Trace kind=silenced_decision
   + 静音台账)+ WS 轻通知,不进待决表。**只静音 ACCEPT 向**:预测 REJECT / 置信不足 /
   预测失败 / 无兑现 handler → 一律回正常路径出卡问人(宁可打扰,绝不错办)。
4. **押错自动吊销**:该桶任何一次对账 miss(押错)→ 立即吊销授权 + 出卡告知;重新挣回
   授权只认吊销**之后**的新鲜命中。翻案(overturn)同样吊销(最强负信号)。

高危 kind **硬排除**(HIGH_RISK_KINDS,授权门和授权落地两层都拒 —— 卡被伪造/文件被篡改
也授不出权);授权卡自身(silence_grant)在排除表里 → **它自己永不被静音**(自指防护)。

保守铁律(Hardy):所有边界向保守倒 —— 判定链上任何一环失败,都回"正常出卡问人"。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---- kind 常量(决策卡 wire 格式;handler 在 console/proposal_handlers.py 注册)----
KIND_SILENCE_GRANT = "silence_grant"      # 授权卡:要不要以后这类替你静音处理?
KIND_SILENCE_REVOKED = "silence_revoked"  # 告知卡:押错/翻案 → 已自动收回该桶授权

# ---- 授权门(依据)----
# SILENCE_MIN_N = 20:展示层样本门是 MIN_N=10(taste_eval,"不够样本诚实说还在学");
#   静音是**替人拍板**,证据要求取展示门的 2 倍 —— docs/49 机制2 说的是"连续 10 次押中且
#   属低风险 kind"才**开始考虑**,本任务拍板收紧为 n≥20(且是同桶,不是全局混算)。
# SILENCE_MIN_HIT_RATE = 0.90:90% 意味着 20 次里最多错 2 次;低于它"替你办"的错办率
#   不可辩护(docs/50:免打扰是唯一没法伪造的懂你 —— 也最经不起办错)。
SILENCE_MIN_N = 20
SILENCE_MIN_HIT_RATE = 0.90
# 单卡执行门:桶级授权之外,每张卡的预测还要 ACCEPT 向且置信 ≥ 此值才真静音执行;
# 0.80 与"押注要压低 confidence 表达不确定"(taste_eval prompt)对齐 —— 模型自己没把握就问人。
SILENCE_MIN_CONFIDENCE = 0.80
# 同桶授权卡被 REJECT/无人理后,过这么久才允许再提(别把"要授权"变成新打扰)。
OFFER_COOLDOWN_S = 7 * 86400
_LEDGER_RETAIN = 1000   # 静音台账留存上限(月度对账/折叠区够用,有界)

# ---- 高危 kind 硬排除(授权门 + SilenceGrantStore.grant 双层都拒)----
# 字符串字面量(而非 import 各处常量):kind 是 wire 格式稳定字符串,且避免 karvy↔console↔
# cognition 的 import 环。逐条依据:
HIGH_RISK_KINDS = frozenset({
    "fs_access",             # 文件系统扩权授权卡(任务点名;安全边界绝不自动放行)
    KIND_SILENCE_GRANT,      # 授权卡自身 —— 自指防护:静音授权永不被静音(任务点名)
    KIND_SILENCE_REVOKED,    # 吊销告知 —— 静音它=用户永远不知道授权没了
    "ops_fix",               # 会执行系统修复(哪怕确定性可逆,也属系统状态变更)
    "resolve_conflict",      # 治理冲突处置(value.md 层面的事必须人拍)
    "confirm_decision_pref", # 元循环(taste_eval SKIP_KINDS 同口径:确认偏好本身不押注)
    "cocreate_finalize",     # 真建域+角色(结构性变更)
    "merge_atoms",           # 删原子(护城河资产;rewire-before-delete 也不自动删)
    "confirm_result",        # 裁定自造原子沉淀(护城河资产变更;不处理本就有孤儿巡检兜底)
    "infeasible_report",     # H2A 升级报告 —— 静音=吞掉升级,决策 loop 塌(「怎么样了?」反模式)
    "weekly_digest",         # 信息送达类:静音=用户看不到成绩单/对账,信任阶梯自断
})

WS_TYPE_SILENCE_NOTICE = "silence_notice"   # WS 轻通知(前端 i18n key 见交付报告)


# ---------------------------------------------------------------- 桶
def _norm_domain(domain: str) -> str:
    """域归一:l0(私聊/全局)与空同桶 —— 两侧(decision_log 的 domain / 卡 payload 的
    domain_id)口径不一时只会导致**不静音**(保守),归一只做无风险的 l0≡空。"""
    d = (domain or "").strip()
    return "" if d in ("", "l0") else d


def bucket_key(kind: str, domain: str = "") -> str:
    d = _norm_domain(domain)
    return f"{kind}|{d}" if d else str(kind)


def _proposal_domain(proposal: Any) -> str:
    p = getattr(proposal, "payload", {}) or {}
    return _norm_domain(str(p.get("domain_id") or p.get("group_domain_id") or ""))


def bucket_stats(app: Any, *, min_ts: float = 0.0) -> dict[str, dict]:
    """分桶命中率:{bucket: {kind, domain, n, hits, hit_rate}}。

    数据源 = TastePredictionStore.outcomes()(唯一账本,只读)⨝ decision_log(proposal_id →
    kind/domain)。min_ts>0 时只算该时刻**之后**的对账 —— 吊销后重挣授权只认新鲜证据。
    任何一环读不到 → 返回空(没有统计就没有授权,保守)。
    """
    tstore = getattr(app.state, "taste_predictions", None)
    dlog = getattr(app.state, "decision_log", None)
    if tstore is None or dlog is None:
        return {}
    try:
        outs = tstore.outcomes()
    except Exception:
        return {}
    meta: dict[str, tuple] = {}
    try:
        for e in dlog.query(limit=5000):   # newest-first;同 pid 取最新一条(kind/domain 相同)
            pid = e.get("proposal_id") or ""
            if pid and pid not in meta and e.get("kind"):
                meta[pid] = (e.get("kind", ""), _norm_domain(e.get("domain", "")))
    except Exception:
        return {}
    buckets: dict[str, dict] = {}
    for o in outs:
        try:
            ts = float(o.get("ts", 0.0))
        except (TypeError, ValueError):
            ts = 0.0
        if min_ts > 0 and ts <= min_ts:
            continue
        km = meta.get(o.get("pid", ""))
        if not km:
            continue   # 关联不上 → 不计入(不猜桶)
        kind, domain = km
        b = bucket_key(kind, domain)
        d = buckets.setdefault(b, {"kind": kind, "domain": domain, "n": 0, "hits": 0})
        d["n"] += 1
        d["hits"] += 1 if o.get("hit") else 0
    for d in buckets.values():
        d["hit_rate"] = (d["hits"] / d["n"]) if d["n"] else 0.0
    return buckets


# ---------------------------------------------------------------- 授权台账
class SilenceGrantStore:
    """静音授权台账(~/.karvyloop/silence_grants.json;fail-safe:坏文件当空)。

    结构:{"grants": {bucket: {kind, domain, granted_at, n, hits, revoked_at, revoke_reason}},
          "offers": {bucket: last_offer_ts}}。吊销**不删记录**(revoked_at 留审计 + 重挣
    授权的新鲜证据水位);重新授权覆盖写(granted_at 更新)。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._grants: dict[str, dict] = {}
        self._offers: dict[str, float] = {}
        if self._path is not None and self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._grants = {str(k): dict(v) for k, v in (d.get("grants") or {}).items()
                                if isinstance(v, dict)}
                self._offers = {str(k): float(v) for k, v in (d.get("offers") or {}).items()
                                if isinstance(v, (int, float))}
            except Exception:
                pass   # 坏文件当空(与 taste store 同调;丢授权=回到逐张问人,安全方向)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {"grants": self._grants, "offers": self._offers},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("[silence] 授权台账落盘失败(不阻断): %s", e)

    def grant(self, kind: str, domain: str = "", *, n: int = 0, hits: int = 0,
              now: Optional[float] = None) -> Optional[dict]:
        """授权一个桶。高危 kind **硬地板拒绝**(返回 None)—— 即使卡被伪造也授不出权。"""
        k = (kind or "").strip()
        if not k or k in HIGH_RISK_KINDS:
            return None
        d = _norm_domain(domain)
        g = {"kind": k, "domain": d,
             "granted_at": now if now is not None else time.time(),
             "n": int(n), "hits": int(hits), "revoked_at": None, "revoke_reason": ""}
        self._grants[bucket_key(k, d)] = g
        self._save()
        return dict(g)

    def is_granted(self, kind: str, domain: str = "") -> bool:
        g = self._grants.get(bucket_key((kind or "").strip(), domain))
        return bool(g) and not g.get("revoked_at")

    def revoke(self, bucket: str, *, reason: str = "", now: Optional[float] = None) -> bool:
        """吊销(可撤销的"撤销":记录保留,授权失效)。没有活跃授权 → False。"""
        g = self._grants.get(bucket)
        if not g or g.get("revoked_at"):
            return False
        g["revoked_at"] = now if now is not None else time.time()
        g["revoke_reason"] = reason or "revoked"
        self._save()
        return True

    def last_revoked_at(self, bucket: str) -> float:
        g = self._grants.get(bucket)
        if not g:
            return 0.0
        try:
            return float(g.get("revoked_at") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def active_grants(self) -> dict[str, dict]:
        return {b: dict(g) for b, g in self._grants.items() if not g.get("revoked_at")}

    def note_offer(self, bucket: str, now: Optional[float] = None) -> None:
        self._offers[bucket] = now if now is not None else time.time()
        self._save()

    def offer_recently(self, bucket: str, *, now: Optional[float] = None) -> bool:
        ts = self._offers.get(bucket)
        if ts is None:
            return False
        n = now if now is not None else time.time()
        return (n - ts) < OFFER_COOLDOWN_S


def get_store(app: Any) -> SilenceGrantStore:
    """app.state 上懒加载授权台账(entry 不用改接线;测试注入 silence_grants_path 或直接放实例)。"""
    st = getattr(app.state, "silence_grants", None)
    if isinstance(st, SilenceGrantStore):
        return st
    path = getattr(app.state, "silence_grants_path", None) \
        or (Path.home() / ".karvyloop" / "silence_grants.json")
    st = SilenceGrantStore(Path(path))
    app.state.silence_grants = st
    return st


# ---------------------------------------------------------------- 静音留痕台账
def _ledger_path(app: Any) -> Path:
    p = getattr(app.state, "silenced_ledger_path", None)
    return Path(p) if p else (Path.home() / ".karvyloop" / "silenced_decisions.json")


def read_ledger(app: Any) -> list[dict]:
    try:
        p = _ledger_path(app)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _write_ledger(app: Any, items: list[dict]) -> None:
    p = _ledger_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items[-_LEDGER_RETAIN:], ensure_ascii=False), encoding="utf-8")


def record_silenced(app: Any, entry: dict) -> None:
    """静音处理留痕(跨重启;"已按你的口味处理"折叠区 + 月度对账的数据源)。"""
    try:
        items = read_ledger(app)
        items.append(dict(entry))
        _write_ledger(app, items)
    except Exception as e:
        logger.warning("[silence] 静音台账落盘失败(Trace 仍会记): %s", e)


# ---------------------------------------------------------------- 卡片工厂
def proposal_for_silence_grant(*, kind: str, domain: str = "", n: int, hits: int, ts: float):
    """授权卡:达门的桶 → 问人"要不要以后这类替你静音处理?"。稳定 id 按桶派生(同桶收敛一张)。"""
    from karvyloop.karvy.atoms import Proposal
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    digest = hashlib.sha1(b.encode("utf-8")).hexdigest()[:8]
    dom_disp = f"(域「{d}」)" if d else ""
    rate = int(round(100 * hits / n)) if n else 0
    return Proposal(
        summary=(f"「{kind}」{dom_disp}这类板,我最近 {n} 次押中 {hits} 次({rate}%)"
                 f"—— 要不要以后这类替你静音处理?"),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.6, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_SILENCE_GRANT,
        payload={"kind": kind, "domain": d, "bucket": b, "n": int(n), "hits": int(hits)},
        proposal_id=f"{KIND_SILENCE_GRANT}-0-{digest}",
        basis=(f"这不是要更多权限 —— 是同类卡上我连押 {n} 次中 {hits} 次的成绩单"
               f"(门槛:同桶 ≥{SILENCE_MIN_N} 次且命中 ≥{int(SILENCE_MIN_HIT_RATE * 100)}%)。"
               f"ACCEPT 后:这类卡我**只**替你办「我押你会 ACCEPT 且把握 ≥"
               f"{int(SILENCE_MIN_CONFIDENCE * 100)}%」的;押 REJECT 或没把握的照旧问你;"
               f"每次静音处理完整留痕(运行记录+台账)、月度对账;我**押错一次立即自动收回**授权,"
               f"你也随时可撤。REJECT=保持现状,每张都问你。"),
    )


def proposal_for_silence_revoked(*, kind: str, domain: str = "", ts: float, reason: str = ""):
    """吊销告知卡:押错/翻案 → 授权已自动收回。带时间戳派生 id(多次吊销不撞已拍过的卡)。"""
    from karvyloop.karvy.atoms import Proposal
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    digest = hashlib.sha1(f"{b}|{int(ts)}".encode("utf-8")).hexdigest()[:8]
    dom_disp = f"(域「{d}」)" if d else ""
    return Proposal(
        summary=f"已自动收回「{kind}」{dom_disp}的静音授权 —— 这类卡恢复逐张问你",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.7, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_SILENCE_REVOKED,
        payload={"kind": kind, "domain": d, "bucket": b, "reason": (reason or "")[:200]},
        proposal_id=f"{KIND_SILENCE_REVOKED}-0-{digest}",
        basis=(f"{reason or '我押错了一次你的拍板'}。挣来的静音只在命中率兑现时有效 —— "
               f"押错一次立即收回(保守边界);要重新拿授权,得吊销之后重新攒满 "
               f"{SILENCE_MIN_N} 次新鲜命中(≥{int(SILENCE_MIN_HIT_RATE * 100)}%)我才会再问你。"
               f"ACCEPT=知悉。"),
    )


# ---------------------------------------------------------------- 出卡
def _deliver(app: Any, card: Any) -> None:
    """出卡:有事件循环 → broadcast(register+押注+WS 推);无循环(REST 线程池/测试)→
    直接登记待决表(boot-fetch 会取到)。allow_silence=False:本模块出的卡不再过静音判定。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        async def _push() -> None:
            from karvyloop.console.proposals import broadcast_proposal
            await broadcast_proposal(app, card, allow_silence=False)
        t = loop.create_task(_push())
        _track_task(app, t)
        return
    reg = getattr(app.state, "proposal_registry", None)
    if reg is not None:
        try:
            reg.register(card)
        except Exception as e:
            logger.warning("[silence] 出卡登记失败: %s", e)


def _track_task(app: Any, task: Any) -> None:
    tasks = getattr(app.state, "_silence_tasks", None)
    if tasks is None:
        tasks = app.state._silence_tasks = set()
    tasks.add(task)

    def _done(t: Any) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except Exception:
            return
        if exc is not None:
            logger.error("[silence] 后台任务异常: %s", exc)
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "silence", str(exc))
            except Exception:
                pass

    task.add_done_callback(_done)


# ---------------------------------------------------------------- 授权门
def maybe_offer_grant(app: Any, *, kind: str, domain: str = "",
                      now: Optional[float] = None) -> Optional[Any]:
    """某桶新添了一次命中后调:达授权门 → 出授权卡(同桶挂着/已授权/冷却中不重复)。

    返回出的卡(或 None)。全链任何一环失败 → None(没有授权就没有静音,保守)。
    """
    k = (kind or "").strip()
    if not k or k in HIGH_RISK_KINDS:
        return None
    try:
        from karvyloop.crystallize.taste_eval import SKIP_KINDS
        if k in SKIP_KINDS:
            return None
    except Exception:
        return None
    n_ts = now if now is not None else time.time()
    d = _norm_domain(domain)
    b = bucket_key(k, d)
    store = get_store(app)
    if store.is_granted(k, d) or store.offer_recently(b, now=n_ts):
        return None
    reg = getattr(app.state, "proposal_registry", None)
    if reg is not None:
        try:
            for pr in reg.pending():
                if getattr(pr, "kind", "") == KIND_SILENCE_GRANT and \
                        (getattr(pr, "payload", {}) or {}).get("bucket") == b:
                    return None   # 同桶授权卡已挂着 → 不重复
        except Exception:
            return None
    stats = bucket_stats(app, min_ts=store.last_revoked_at(b))   # 吊销过 → 只认新鲜证据
    st = stats.get(b)
    if not st or st["n"] < SILENCE_MIN_N or st["hit_rate"] < SILENCE_MIN_HIT_RATE:
        return None
    card = proposal_for_silence_grant(kind=k, domain=d, n=st["n"], hits=st["hits"], ts=n_ts)
    store.note_offer(b, now=n_ts)
    _deliver(app, card)
    logger.info("[silence] 桶 %s 达授权门(n=%s, rate=%.0f%%)→ 出静音授权卡",
                b, st["n"], 100 * st["hit_rate"])
    return card


def on_outcome(app: Any, *, proposal_id: str, kind: str, domain: str = "",
               hit: Optional[bool], now: Optional[float] = None) -> None:
    """拍板对账后的控制器钩子(decision_wire 段3b 调,单一接缝)。

    - 押错(hit=False)且该桶已授权 → **立即吊销** + 出告知卡(押错一次都不容忍,保守)。
    - 押中(hit=True)→ 看这次是否把桶推过授权门。
    hit=None(没押过注)→ 不动。
    """
    if hit is None or not (kind or "").strip():
        return
    n_ts = now if now is not None else time.time()
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    if hit is False:
        store = get_store(app)
        if store.is_granted(kind, d):
            store.revoke(b, reason=f"押错(proposal {proposal_id})", now=n_ts)
            _deliver(app, proposal_for_silence_revoked(
                kind=kind, domain=d, ts=n_ts, reason="这次我押错了你的拍板"))
            logger.info("[silence] 桶 %s 押错 → 自动吊销静音授权", b)
        return
    maybe_offer_grant(app, kind=kind, domain=d, now=n_ts)


# ---------------------------------------------------------------- 静音拦截(register 咽喉)
def try_silence(app: Any, proposal: Any) -> bool:
    """broadcast_proposal 顶部调:已授权桶的卡 → 走静音路径(不进待决表、不推卡)。

    返回 True = 已接管(caller 直接返回);False = 走正常路径。**判定链任何一环不满足都
    返回 False**(高危 kind / 未授权 / 无兑现 handler / 无 LLM / 无事件循环 —— 宁可打扰)。
    """
    kind = (getattr(proposal, "kind", "") or "").strip()
    pid = getattr(proposal, "proposal_id", "") or ""
    if not kind or not pid or kind in HIGH_RISK_KINDS:
        return False   # 自指防护:silence_grant/silence_revoked 在 HIGH_RISK_KINDS,永不被静音
    try:
        from karvyloop.crystallize.taste_eval import SKIP_KINDS
        if kind in SKIP_KINDS:
            return False
    except Exception:
        return False
    dom = _proposal_domain(proposal)
    try:
        if not get_store(app).is_granted(kind, dom):
            return False
    except Exception:
        return False
    handlers = getattr(app.state, "proposal_handlers", None) or {}
    if handlers.get(kind) is None:
        return False   # 没真兑现能力 → 静音等于吞卡,绝不
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if rk.get("gateway") is None:
        return False   # 无 LLM 无预测 → 不静音
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False   # 同步上下文起不了静音任务 → 正常出卡
    _track_task(app, loop.create_task(_silent_handle(app, proposal)))
    logger.info("[silence] 已授权桶 %s → 卡 %s 走静音路径", bucket_key(kind, dom), pid)
    return True


async def _predict_for_silence(app: Any, proposal: Any) -> Optional[tuple]:
    """静音路径的口味预测(与押注同一模型/同一 prealign 口径;token 记 silence_predict)。
    模块级函数 → 测试可注入。失败返 None(→ 回正常路径)。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return None
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
    with token_source("silence_predict"):
        return await predict_decision(
            gw, rk.get("model_ref", "") or "",
            summary=getattr(proposal, "summary", "") or "",
            basis=getattr(proposal, "basis", "") or "",
            kind=getattr(proposal, "kind", "") or "",
            prefs_block=prefs_block)


async def _fallback(app: Any, proposal: Any) -> None:
    """静音条件不满足 → 回正常路径出卡(allow_silence=False 防递归)。最后防线:回退也失败
    → 直接登记待决表,**绝不让卡蒸发**(早返回不留痕=决策 loop 塌的老病根)。"""
    try:
        from karvyloop.console.proposals import broadcast_proposal
        await broadcast_proposal(app, proposal, allow_silence=False)
    except Exception as e:
        logger.warning("[silence] 回退广播失败,直接登记待决: %s", e)
        reg = getattr(app.state, "proposal_registry", None)
        if reg is not None:
            try:
                reg.register(proposal)
            except Exception:
                pass


async def _silent_handle(app: Any, proposal: Any) -> None:
    """静音路径主体:预测 → (只 ACCEPT 向且够置信)自动兑现 → 完整留痕 + WS 轻通知。"""
    pid = getattr(proposal, "proposal_id", "") or ""
    kind = (getattr(proposal, "kind", "") or "").strip()
    dom = _proposal_domain(proposal)
    got = None
    try:
        got = await _predict_for_silence(app, proposal)
    except Exception as e:
        logger.debug("[silence] 预测失败(回正常路径): %s", e)
    dec, conf = (got if got else ("", 0.0))
    if dec != "ACCEPT" or float(conf) < SILENCE_MIN_CONFIDENCE:
        # **只静音 ACCEPT 向**:预测 REJECT / 置信不足 / 预测失败 → 出卡问人(宁可打扰绝不错办)
        await _fallback(app, proposal)
        return
    handlers = getattr(app.state, "proposal_handlers", None) or {}
    if handlers.get(kind) is None:   # try_silence 查过;授权/接线在 await 间隙变了也兜住
        await _fallback(app, proposal)
        return
    from karvyloop.karvy.proposal_registry import dispatch_accept
    try:
        res = await asyncio.to_thread(dispatch_accept, proposal, handlers)
        ok, detail = bool(res.ok), str(res.detail)
    except Exception as e:
        ok, detail = False, f"dispatch error: {e}"
    entry = {
        "ts": time.time(), "proposal_id": pid, "kind": kind, "domain": dom,
        "bucket": bucket_key(kind, dom),
        "summary": (getattr(proposal, "summary", "") or "")[:200],
        "predicted": "ACCEPT", "confidence": float(conf),
        "ok": ok, "detail": detail[:400], "overturned": False,
    }
    record_silenced(app, entry)     # ① 台账(折叠区/月度对账,跨重启)
    _trace_silenced(app, entry)     # ② Trace kind=silenced_decision(所有评价的唯一数据源)
    try:
        await _ws_notify(app, {"type": WS_TYPE_SILENCE_NOTICE, "payload": dict(entry)})  # ③ 轻通知
    except Exception as e:
        logger.debug("[silence] WS 轻通知失败(留痕已落): %s", e)
    if not ok:
        # 兑现失败:**不重发原卡**(handler 可能已部分执行,重发→用户 ACCEPT=二次执行);
        # fail-loud 推系统错误,让人看见(§0.7 灭静默死角)。
        try:
            from karvyloop.console.task_events import schedule_system_error
            schedule_system_error(app, "silence",
                                  f"静音兑现失败({kind}/{pid}):{detail[:160]}")
        except Exception:
            pass


def _trace_silenced(app: Any, entry: dict) -> None:
    try:
        trace = getattr(getattr(app.state, "main_loop", None), "trace", None)
        if trace is None:
            return
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(task_id=entry["proposal_id"], kind="silenced_decision",
                                payload=dict(entry), agent="karvy", source="silence"))
    except Exception as e:
        logger.warning("[silence] 落 Trace 失败(台账已落,不阻断): %s", e)


async def _ws_notify(app: Any, message: dict) -> int:
    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    sent = 0
    dead: list = []
    for ws in list(clients):
        try:
            await ws.send_json(message)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
    return sent


# ---------------------------------------------------------------- 撤销 / 翻案 / 月度对账
def revoke_grant(app: Any, bucket: str, *, reason: str = "user",
                 now: Optional[float] = None) -> bool:
    """撤销某桶的静音授权(用户主动撤 / 上层接口用)。没有活跃授权 → False。"""
    return get_store(app).revoke(bucket, reason=reason, now=now)


def overturn_silenced(app: Any, proposal_id: str, *, now: Optional[float] = None) -> Optional[dict]:
    """翻案:推翻一条已静音处理的决定(最强负信号)→ 台账标记 + **吊销该桶授权** + 出告知卡。
    返回被翻案的台账条目(找不到/已翻过 → None)。"""
    n_ts = now if now is not None else time.time()
    items = read_ledger(app)
    target = None
    for it in reversed(items):
        if it.get("proposal_id") == proposal_id and not it.get("overturned"):
            it["overturned"] = True
            it["overturned_ts"] = n_ts
            target = it
            break
    if target is None:
        return None
    try:
        _write_ledger(app, items)
    except Exception as e:
        logger.warning("[silence] 翻案标记落盘失败: %s", e)
    b = target.get("bucket") or bucket_key(target.get("kind", ""), target.get("domain", ""))
    if get_store(app).revoke(b, reason=f"翻案(proposal {proposal_id})", now=n_ts):
        _deliver(app, proposal_for_silence_revoked(
            kind=target.get("kind", ""), domain=target.get("domain", ""),
            ts=n_ts, reason="你推翻了一次静音处理"))
    return dict(target)


def monthly_reconciliation(app: Any, *, days: int = 30, now: Optional[float] = None) -> dict:
    """月度对账数据(digest 用):"这个月替你挡掉了 N 次打扰,你翻案 M 次"。纯查表,零 LLM。"""
    n_ts = now if now is not None else time.time()
    since = n_ts - days * 86400
    items = [it for it in read_ledger(app) if float(it.get("ts", 0.0) or 0.0) >= since]
    by_bucket: dict[str, dict] = {}
    for it in items:
        b = it.get("bucket", "") or bucket_key(it.get("kind", ""), it.get("domain", ""))
        d = by_bucket.setdefault(b, {"silenced": 0, "overturned": 0})
        d["silenced"] += 1
        if it.get("overturned"):
            d["overturned"] += 1
    return {
        "days": days, "since_ts": since,
        "silenced_n": len(items),
        "overturned_n": sum(1 for it in items if it.get("overturned")),
        "failed_n": sum(1 for it in items if not it.get("ok", True)),
        "by_bucket": by_bucket,
        "active_grants": get_store(app).active_grants(),
    }


__all__ = [
    "KIND_SILENCE_GRANT", "KIND_SILENCE_REVOKED",
    "SILENCE_MIN_N", "SILENCE_MIN_HIT_RATE", "SILENCE_MIN_CONFIDENCE",
    "OFFER_COOLDOWN_S", "HIGH_RISK_KINDS", "WS_TYPE_SILENCE_NOTICE",
    "bucket_key", "bucket_stats",
    "SilenceGrantStore", "get_store",
    "read_ledger", "record_silenced",
    "proposal_for_silence_grant", "proposal_for_silence_revoked",
    "maybe_offer_grant", "on_outcome", "try_silence",
    "revoke_grant", "overturn_silenced", "monthly_reconciliation",
]
