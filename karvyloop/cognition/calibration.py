"""cognition/calibration.py — B-5 标定埋点发射器(内测期常数分布采集)。

**为什么**:一批"拍脑袋常数"(结晶灵敏度/聚类阈值/上下文窗/治理帽/召回跳数/调度节拍)
从未用真数据标定过。真人内测期间,用 Trace 记这些常数的实际分布(截断次数/触发率/
通过率),内测后拿真数据回来定值。埋点是**观测面**,业务面永不读它。

三条硬纪律:
① **fail-soft**:`emit()` 整体自兜(含序列化/append 异常),埋点炸了绝不影响主流程;
   调用点仍建议再裹一层 try(防 import/取值本身炸)。
② **量纲安全**:payload 只装标量/短字符串/短标量列表,总序列化封顶 ~300 字(超了狠截);
   高频点位(召回/tick)必须采样或聚合,策略写在各自调用点注释里。
③ **新 kind 绝不进 `trace.DROPPABLE_KINDS`**:容量环只滚大块原文;标定事件微小、
   要留到内测结束供分布分析(测试有锁)。

**为什么是进程级单例 sink**(同 logging 的哲学):埋点散在 conversation / channels /
console 等**没有 Trace 句柄**的模块里,逐层穿参数要搅十几个签名(违"少脚手架")。
入口在 `MainLoop.__init__` 装(生产恰好一个 MainLoop,其 trace 就是周报/评价共用的
那份事件底座);测试多实例 last-wins 只影响标定数据归属,不影响任何行为。
持弱引用:不给测试里已弃的 TraceStore 续命,死了自动降级为 no-op。
"""

from __future__ import annotations

import json
import threading
import time
import weakref
from typing import Any, Optional

# 本模块所有事件共用的 task_id(TraceStore 按 task_id 分桶;分析侧
# `trace.query(CALIBRATION_TASK_ID, kind=...)` 一把捞)。
CALIBRATION_TASK_ID = "calibration"

# 单值/总量封顶(纪律②):payload 带"常数当前值 + 关键上下文",不带原文。
_MAX_KEYS = 16
_MAX_STR = 120
_MAX_LIST = 12
_MAX_PAYLOAD_CHARS = 300

_sink_ref: Optional[Any] = None      # weakref.ref(TraceStore) 或(不可弱引用时)强引用
_sink_is_weak: bool = True
_lock = threading.Lock()


def set_calibration_trace(trace: Optional[object]) -> None:
    """装/卸进程级标定 sink(None = 卸)。弱引用优先(不给弃店续命)。"""
    global _sink_ref, _sink_is_weak
    with _lock:
        if trace is None:
            _sink_ref = None
            return
        try:
            _sink_ref = weakref.ref(trace)
            _sink_is_weak = True
        except TypeError:   # 极少数不可弱引用的对象:退强引用(仍可用)
            _sink_ref = trace
            _sink_is_weak = False


def calibration_trace() -> Optional[object]:
    """当前 sink(已死/未装 → None)。"""
    ref = _sink_ref
    if ref is None:
        return None
    return ref() if _sink_is_weak else ref


def _clip_payload(payload: dict) -> dict:
    """纪律②:只留标量/短串/短标量列表;总序列化 >~300 字时对字符串再狠截。"""
    out: dict = {}
    for i, (k, v) in enumerate(payload.items()):
        if i >= _MAX_KEYS:
            break
        key = str(k)[:40]
        if isinstance(v, str):
            out[key] = v[:_MAX_STR]
        elif isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[key] = v
        elif isinstance(v, (list, tuple)):
            out[key] = [(x[:40] if isinstance(x, str) else x)
                        for x in list(v)[:_MAX_LIST]
                        if isinstance(x, (int, float, bool, str))]
        else:
            out[key] = repr(v)[:80]
    try:
        if len(json.dumps(out, ensure_ascii=False, default=str)) > _MAX_PAYLOAD_CHARS:
            for k, v in out.items():
                if isinstance(v, str) and len(v) > 40:
                    out[k] = v[:40] + "…"
    except Exception:
        pass
    return out


def emit(kind: str, payload: dict, *, task_id: str = CALIBRATION_TASK_ID,
         trace: Optional[object] = None) -> bool:
    """落一条标定事件。**整体 fail-soft**:任何异常吞掉返 False,绝不冒泡进主流程。

    `trace`:显式句柄(如 MemoryManager.trace)优先;None → 进程级 sink;都无 → no-op。
    返回 True 仅表示 append 没抛(观测面,不做业务判断)。
    """
    try:
        tr = trace if trace is not None else calibration_trace()
        if tr is None:
            return False
        from karvyloop.cognition.trace import TraceEntry
        tr.append(TraceEntry(
            task_id=task_id, kind=str(kind),
            payload=_clip_payload(dict(payload or {})),
            source="calibration"))
        return True
    except Exception:
        return False


class TickStatsAggregator:
    """#11 `tick_stats`(调度 30s tick 的时长/跳拍分布)的**聚合器**。

    量控策略(30s tick 直落 = 2880 条/天,会灌爆 Trace):
    ① **每小时一条窗口汇总**(ticks/fired/late/max/mean duration)——分布分析的主体;
    ② **异常慢拍直报**(单拍 duration ≥ slow_s,默认 5s;带 min_gap 节流,默认 10min
       至多一条)——慢拍要当场看得见,不等汇总。
    全部走 `emit()`(fail-soft);`record()` 自身也兜异常,调度循环行为绝不受影响。
    非线程安全(单 asyncio 循环内使用,与调度器同栖)。
    """

    def __init__(self, *, interval_s: float = 30.0, window_s: float = 3600.0,
                 slow_s: float = 5.0, slow_report_min_gap_s: float = 600.0,
                 clock=time.time, trace: Optional[object] = None) -> None:
        self.interval_s = float(interval_s)
        self.window_s = float(window_s)
        self.slow_s = float(slow_s)
        self.slow_report_min_gap_s = float(slow_report_min_gap_s)
        self._clock = clock
        self._trace = trace          # None → 进程级 sink(emit 的默认回退)
        self._win_start = clock()
        self._last_slow_report = 0.0
        self._reset_counters()

    def _reset_counters(self) -> None:
        self.n_ticks = 0
        self.n_fired = 0
        self.n_late = 0             # "跳拍":两拍实际间隔 > 2×interval(上一拍干活拖堂/事件循环饿)
        self.sum_duration = 0.0
        self.max_duration = 0.0

    def record(self, duration_s: float, *, fired: int = 0,
               elapsed_s: Optional[float] = None) -> None:
        """记一拍。duration_s=本拍干活耗时;elapsed_s=与上一拍的实际间隔(判跳拍)。"""
        try:
            now = self._clock()
            d = max(0.0, float(duration_s))
            self.n_ticks += 1
            self.n_fired += max(0, int(fired))
            self.sum_duration += d
            if d > self.max_duration:
                self.max_duration = d
            if elapsed_s is not None and float(elapsed_s) > 2.0 * self.interval_s:
                self.n_late += 1
            # ② 异常慢拍直报(节流)
            if d >= self.slow_s and (now - self._last_slow_report) >= self.slow_report_min_gap_s:
                self._last_slow_report = now
                emit("tick_stats", {
                    "event": "slow_tick", "duration_s": round(d, 3),
                    "interval_s": self.interval_s, "fired": int(fired),
                }, trace=self._trace)
            # ① 窗口汇总(窗满且窗内有拍)
            if (now - self._win_start) >= self.window_s and self.n_ticks > 0:
                emit("tick_stats", {
                    "event": "window",
                    "ticks": self.n_ticks, "fired": self.n_fired, "late": self.n_late,
                    "max_s": round(self.max_duration, 3),
                    "mean_s": round(self.sum_duration / self.n_ticks, 4),
                    "interval_s": self.interval_s,
                    "window_s": self.window_s,
                }, trace=self._trace)
                self._win_start = now
                self._reset_counters()
        except Exception:
            pass   # 观测面绝不反哺业务面:聚合炸了丢这拍数据拉倒


__all__ = ["CALIBRATION_TASK_ID", "TickStatsAggregator", "calibration_trace",
           "emit", "set_calibration_trace"]
