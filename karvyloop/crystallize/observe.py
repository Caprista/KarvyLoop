"""observe — 任务结束后,把 Trace 投影到 UsageStats（crystallize/observe.py）。

规格:docs/modules/crystallize.md §3 observe.py
- HR-7:observe 是对 Trace 的投影,不单独埋点;成败直接来自 Trace.AtomRun.success
- 60s 去抖:同 sig 60s 内重复使用不重复计数
- 签名归一化:同能力不同参数变体应归同 sig
"""

from __future__ import annotations

import time
from typing import Iterable

from karvyloop.schemas import AtomRun, UsageStats

from .cluster import match_cluster
from .signature import compute_signature
from .store import USAGE_DEBOUNCE_SEC, UsageStore


def _extract_params(run: AtomRun) -> dict:
    """从 run 提取参数模式(用作 param_variants 的一项;M1 简版:全量 input)。

    后续要做归一(去值只留类型/形状)以判"泛化性";M1 v1 先存全量。
    """
    return dict(run.input) if isinstance(run.input, dict) else {}


def observe(
    runs: Iterable[AtomRun],
    store: UsageStore,
    *,
    clock=time.time,
    debounce_sec: float = USAGE_DEBOUNCE_SEC,
    cluster_threshold: float = 0.0,
) -> dict[str, int]:
    """对一组 AtomRun 做 observe。返回每个 sig 的更新次数(去抖后)。

    失败/成功都更新 success/failure 计数 —— 失败也算"用过",但 success_rate
    由外部算(success_count / usage_count),不会因失败被吞掉。

    去抖语义:跨 observe() 调用(任务结束 vs 任务结束)生效;
    同一 observe() 调用内不互相去抖(每次 run 都是一次真实使用)。

    时间来源:每个 run 自带 `ts` 字段(AtomRun 是 Trace 的记录,自带时间);
    只在 `ts` 缺失/为 0 时回退到 clock()。这样能精准反映"何时被用过"。

    `cluster_threshold`(9.4):token-overlap 累积聚类门槛(0=关,精确签名旧行为)。
    >0 时:同任务不同说法按 intent-token 重叠归并到同一 cluster(修"换说法不结晶")。
    """
    counts: dict[str, int] = {}
    for run in runs:
        exact_sig = compute_signature(run)
        if not exact_sig:
            continue
        intent = run.input.get("intent", "") if isinstance(run.input, dict) else ""
        # 9.4:先试把这个意图归并到最相近的已有 cluster(token overlap);无人达标 → 精确签名开新
        sig = None
        if cluster_threshold > 0 and intent:
            _cd: dict = {}
            sig = match_cluster(intent, ((s, st.intent_repr) for s, st in store.all()),
                                cluster_threshold, explain_sink=_cd)
            # B-5 #5 标定埋点 `cluster_decision`:overlap 分布/聚类命中率,内测后标定
            # cluster_overlap_threshold=0.2。**只在这里落**(observe 是累积聚类的判定点;
            # main_loop 3b 的对齐重查不传 sink → 不双记)。频率 = 每次 slow-brain run 一条,
            # 无需采样。fail-soft:emit 自兜 + 再裹一层,埋点绝不影响投影。
            try:
                from karvyloop.cognition.calibration import emit
                emit("cluster_decision", {
                    "overlap": round(float(_cd.get("best_overlap", 0.0)), 3),
                    "shared": int(_cd.get("best_shared", 0)),
                    "merged": sig is not None,
                    "threshold": cluster_threshold,
                    "n_clusters": int(_cd.get("n_candidates", 0)),
                })
            except Exception:
                pass
        sig = sig or exact_sig
        # 每个 run 自带时间戳(来自 Trace);缺失才用 clock
        now = run.ts if run.ts else clock()
        stats = store.get(sig) or store.get_or_create(sig)
        # 新 cluster:记下代表意图(供后续 token-overlap 归并)
        if not stats.intent_repr and intent:
            stats = stats.model_copy(update={"intent_repr": intent})
        if now - stats.last_used_at <= debounce_sec and stats.usage_count > 0:
            # 去抖窗口内重复使用 → 不重复计数(只更新 last_used_at 反映最近想起)
            stats = stats.model_copy(update={"last_used_at": now})
            store.put(sig, stats)
            continue
        stats = stats.model_copy(update={
            "usage_count": stats.usage_count + 1,
            "last_used_at": now,
            "success_count": stats.success_count + (1 if run.success else 0),
            "failure_count": stats.failure_count + (0 if run.success else 1),
        })
        # 追加参数变体(去抖不抑制 — 即便 60s 内复用,新参数仍要记)
        if run.success and isinstance(run.input, dict):
            pv = list(stats.param_variants) + [_extract_params(run)]
            # 限制变体数量(防止 OOM)
            if len(pv) > 64:
                pv = pv[-64:]
            stats = stats.model_copy(update={"param_variants": pv})
        store.put(sig, stats)
        counts[sig] = counts.get(sig, 0) + 1
    return counts


__all__ = ["observe"]
