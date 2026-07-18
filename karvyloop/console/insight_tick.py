"""console/insight_tick.py — task_insight 非任务认知沉淀(daily 慢侧 tick;docs/82)。

**为什么**:执行本身顺带揭示的环境/纠错/顺带观察此前只活在人脑和外部笔记里
(2026-07-08 "SFTP 坏的走 base64" 真发生过,系统自己一条没记)。跑评分离:这类
"不急、静心"的沉淀活挂每日慢侧,绝不进 drive/摄入热路径。

**不打扰、不烧钱**(纪律,均有测试锁):
- **池指纹 watermark**:Trace 执行池(atom_run/error/task_run)没变 → 零 LLM 跳过。
- **信号冷却**:同一信号(trace_ref)烧过 LLM,冷却窗(7 天)内不重烧。
- **单 tick 一次 LLM**:候选 ≤5(prompt+parse 双关)、写入 ≤3;token_source("task_insight")。
- **不弹卡**:过复现关的静默写 provisional(透明靠记忆面板来源列,清债靠既有归档机制);
  写走 `MemoryManager.write` 唯一咽喉,写后 supersede+概念标签,回写 Trace kind="task_insight"。
状态落 `~/.karvyloop/insight_tick.json`(坏文件当空,fail-safe)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SIGNAL_COOLDOWN_S = 7 * 86400   # 同一信号烧过 LLM 后的冷却窗
MAX_SIGNALS_PER_TICK = 5        # 单批喂进 LLM 的信号封顶(候选 ≤5 同数量级)
MAX_WRITES_PER_TICK = 3         # 单 tick 写入封顶(别一天糊一库 provisional)
TICK_TASK_ID = "task_insight_tick"   # 回写 Trace 的 task_id(审计可查)
# 冷却台账驱逐门:早于 冷却窗×N 的 seen 项清掉(过了冷却期本就不再抑制,留着只涨体量)。
# 用 N 倍留安全余量,**绝不误删还在冷却期的**(now-ts < 冷却窗 的一定保留)。防长跑无界增长(docs/87 §五)。
COOLDOWN_EVICT_FACTOR = 4


def _evict_expired_cooldown(seen: dict, now: float, *, window: float,
                            factor: float = COOLDOWN_EVICT_FACTOR) -> dict:
    """驱逐早于 window×factor 的过期冷却项(now-ts >= 门 → 丢);仍在冷却期的一律保留。
    坏值(非数)顺手丢。只保留台账不无界增长,不改冷却语义。"""
    cutoff = window * max(1.0, factor)
    kept: dict = {}
    for k, v in (seen or {}).items():
        try:
            ts = float(v)
        except (TypeError, ValueError):
            continue   # 坏时间戳:留着也没意义,清掉
        if now - ts < cutoff:
            kept[k] = ts
    return kept


def _state_path() -> Path:
    return Path.home() / ".karvyloop" / "insight_tick.json"


def _load_state(path: Optional[Path] = None) -> dict:
    p = path or _state_path()
    if not p.exists():
        return {"pool_hash": "", "seen": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"pool_hash": "", "seen": {}}
        d.setdefault("pool_hash", "")
        d.setdefault("seen", {})
        return d
    except Exception:
        return {"pool_hash": "", "seen": {}}   # 坏文件当空(fail-safe,不锁死 tick)


def _save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[insight_tick] 状态落盘失败(下轮重算): {e}")


def _pool_hash(entries: list) -> str:
    """执行池指纹:事件身份 (task_id, seq, kind) 排序哈希。池零变 → 零 LLM。
    (回写的 kind="task_insight" 不在捞料面里,不会自触发下一轮。)"""
    h = hashlib.sha1()
    for key in sorted(f"{getattr(e, 'task_id', '')}:{getattr(e, 'seq', 0)}:{getattr(e, 'kind', '')}"
                      for e in entries):
        h.update(key.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _trace_of(app: Any) -> Any:
    """Trace 源:main_loop.trace 优先,app.state.trace 备选(同 weekly_digest 口径)。"""
    ml = getattr(app.state, "main_loop", None)
    trace = getattr(ml, "trace", None) if ml is not None else None
    if trace is None:
        trace = getattr(app.state, "trace", None)
    return trace


async def task_insight_tick(app: Any, *, state_path: Optional[Path] = None,
                            now: Optional[float] = None) -> dict:
    """每日慢侧执行洞察一轮。返回 {ran, written, candidates, reason}(ran=False 说明为何跳过)。

    信号门/解析/复现关全在 cognition/insight.py(纯逻辑);本 tick 只管纪律:
    watermark → 冷却 → 一次 LLM → 复现关 → mem.write 咽喉 → supersede+标签 → Trace 回写。
    """
    if now is None:
        now = time.time()
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    trace = _trace_of(app)
    if mem is None or gw is None or trace is None:
        return {"ran": False, "written": 0, "candidates": 0,
                "reason": "memory/gateway/trace 未接(--no-llm?)"}

    from karvyloop.cognition.insight import (
        HARVEST_KINDS, build_insight_beliefs, collect_run_texts,
        distill_insights, find_insight_signals,
    )

    # ---- 捞料(只读三种执行事件;不读 eval_fact/satisfaction/lesson,不双记账)----
    entries: list = []
    for tid in trace.all_tasks():
        for k in HARVEST_KINDS:
            entries.extend(trace.query(tid, kind=k))

    state = _load_state(state_path)
    # ③ 读时顺手驱逐过期冷却项(防 seen 台账长跑无界;仍在冷却期的保留)——落盘在下方各 _save_state。
    state["seen"] = _evict_expired_cooldown(state.get("seen") or {}, now, window=SIGNAL_COOLDOWN_S)
    ph = _pool_hash(entries)
    if ph == state.get("pool_hash"):
        return {"ran": False, "written": 0, "candidates": 0,
                "reason": "执行池没变(watermark),零 LLM 跳过"}

    # ---- 门1(零 LLM)+ 冷却:平静日子/全在冷却 → 落新指纹即走,零 LLM ----
    signals = find_insight_signals(entries)
    seen = state.get("seen") or {}

    def _cooled(s: Any) -> bool:
        prev = seen.get(s.trace_ref)
        return prev is not None and now - float(prev) < SIGNAL_COOLDOWN_S

    fresh = [s for s in signals if not _cooled(s)]
    state["pool_hash"] = ph
    if not fresh:
        _save_state(state, state_path)
        reason = ("平静:执行池无信号(零 LLM)" if not signals
                  else "信号全在冷却窗内,不重烧(零 LLM)")
        return {"ran": False, "written": 0, "candidates": 0, "reason": reason}

    batch = fresh[-MAX_SIGNALS_PER_TICK:]   # 最新的封顶一批(单 tick 一次 LLM)

    # ---- 一次 LLM 抽候选(失败抛出 → 不落状态,由 _maintenance_item_failed 兜,下轮重试)----
    from karvyloop.llm.token_ledger import token_source
    with token_source("task_insight"):
        cands = await distill_insights(batch, gateway=gw, model_ref=rk.get("model_ref", ""))

    # ---- 门2(复现关)+ 写入(唯一咽喉 mem.write;单项失败不连坐)----
    run_texts = collect_run_texts(entries)
    beliefs = build_insight_beliefs(cands, signals=batch, run_texts=run_texts,
                                    now=now, max_writes=MAX_WRITES_PER_TICK)
    written: list = []
    for b in beliefs:
        try:
            mem.write(b)
            written.append(b)
        except Exception as e:
            logger.warning(f"[insight_tick] 写洞察失败(跳过该条,其余照写): {e}")

    if written:
        # 写后 supersede(auto 档掀不翻人确认的;失败自吞原库不动)+ 概念标签(增益,失败自吞)
        sup: Optional[dict] = None
        try:
            from karvyloop.cognition.conflict import run_supersede_pass
            sup = await run_supersede_pass(written, mem=mem, gateway=gw,
                                           model_ref=rk.get("model_ref", ""), now=now, trace=trace)
        except Exception as e:
            logger.warning(f"[insight_tick] supersede 失败(原库不动): {e}")
        # D2:后台洞察 supersede 撞钉住/低权威 belief → 走已建好的冲突卡咽喉升 H2A 卡。
        # 此前 run_supersede_pass 的返回**整个丢弃** → pinned 低权威 belief 只被保护、从不弹卡。
        conflicts = list((sup or {}).get("conflicts") or [])
        if conflicts:
            try:
                from karvyloop.console.proposals import raise_memory_conflict_cards
                await raise_memory_conflict_cards(app, conflicts)
            except Exception as e:
                logger.warning(f"[insight_tick] 记忆冲突升卡失败(不影响 tick): {e}")
        cc = getattr(mem, "concept_cache", None)
        if cc is not None:
            try:
                from karvyloop.cognition.concepts import tag_beliefs
                with token_source("belief_tags"):
                    await tag_beliefs(written, cache=cc, gateway=gw,
                                      model_ref=rk.get("model_ref", ""), trace=trace)
            except Exception:
                pass   # 标签是增益不是命脉,daily belief_tags_tick 会回填
        # 回写 Trace(kind="task_insight",产生即留痕;失败自吞不拖垮 tick)
        for b in written:
            try:
                from karvyloop.cognition.trace import TraceEntry
                trace.append(TraceEntry(
                    task_id=TICK_TASK_ID, kind="task_insight",
                    payload={"content": (b.content or "")[:200],
                             "kind": (b.provenance or {}).get("kind", ""),
                             "trace_ref": (b.provenance or {}).get("trace_ref", "")},
                    source="insight_tick"))
            except Exception:
                pass

    for s in batch:
        state.setdefault("seen", {})[s.trace_ref] = now   # 烧过的信号记冷却(无论抽没抽出)
    _save_state(state, state_path)
    if written:
        logger.info(f"[insight_tick] 沉淀 {len(written)} 条执行洞察(provisional,静默不弹卡)")
    return {"ran": True, "written": len(written), "candidates": len(cands), "reason": ""}


__all__ = ["task_insight_tick", "SIGNAL_COOLDOWN_S",
           "MAX_SIGNALS_PER_TICK", "MAX_WRITES_PER_TICK", "TICK_TASK_ID",
           "COOLDOWN_EVICT_FACTOR"]
