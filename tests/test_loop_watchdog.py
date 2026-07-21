"""test_loop_watchdog — 事件循环二层心跳(监督者悖论收口)。

核心场景:循环整体被同步调用堵死(不是崩,是 hang)→ 循环内的一切看门狗全哑,
只有独立线程还能喊。真起循环真堵真验,不桩心跳本体。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from karvyloop.console.loop_watchdog import LoopWatchdog


def _run_loop_in_thread():
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _shut(loop):
    loop.call_soon_threadsafe(loop.stop)


def test_healthy_loop_never_alerts(caplog):
    loop, _ = _run_loop_in_thread()
    try:
        wd = LoopWatchdog(loop, interval_s=0.05, timeout_s=0.5, stall_after=2)
        wd.start()
        with caplog.at_level(logging.ERROR):
            time.sleep(0.4)                     # 跑过好几轮心跳
        wd.stop()
        assert wd.last_status["ok"] is True
        assert not [r for r in caplog.records if "无响应" in r.getMessage()]
    finally:
        _shut(loop)


def test_blocked_loop_alerts_once_then_recovers(caplog):
    """真堵:往循环里丢一个同步 sleep(模拟同步 LLM 调用冻死循环)→ 线程侧必须喊;
    连续堵才报、只报一次;恢复后报"已恢复"。"""
    loop, _ = _run_loop_in_thread()
    try:
        wd = LoopWatchdog(loop, interval_s=0.05, timeout_s=0.15, stall_after=2)
        wd.start()
        with caplog.at_level(logging.ERROR):
            loop.call_soon_threadsafe(time.sleep, 1.2)   # 堵死 1.2s(> 2×(interval+timeout))
            time.sleep(1.0)                              # 心跳该连超 ≥2 次 → 报警
            stall_msgs = [r for r in caplog.records if "无响应" in r.getMessage()]
            assert stall_msgs, "循环被堵死却没人喊(二层心跳失职)"
            assert len(stall_msgs) == 1, "报警该冷却,不许每轮刷屏"
            time.sleep(1.0)                              # 循环恢复 → 心跳回绿 + 报恢复
        wd.stop()
        assert wd.last_status["ok"] is True
        assert [r for r in caplog.records if "恢复" in r.getMessage()]
    finally:
        _shut(loop)


def test_loop_shutdown_is_not_a_stall(caplog):
    """循环正常关闭 ≠ 堵死:watchdog 自行停表,不误报。"""
    loop, _ = _run_loop_in_thread()
    wd = LoopWatchdog(loop, interval_s=0.05, timeout_s=0.3, stall_after=2)
    wd.start()
    time.sleep(0.15)
    _shut(loop)
    with caplog.at_level(logging.ERROR):
        time.sleep(0.4)
    wd.stop()
    assert not [r for r in caplog.records if "无响应" in r.getMessage()]
