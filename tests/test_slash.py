"""test_slash — 小卡斜杠命令(确定性 ops,零 LLM/0 请求)。

锁:识别/解析、各命令跑通不崩、未知命令提示 /help、非斜杠返 None(交给正常 drive)。
不真重启(reboot 的重启动作只在真 app.state.console_relaunch 存在时起线程)。
"""
from __future__ import annotations

import types

from karvyloop.karvy.slash import dispatch_slash, is_slash


def _stub_app():
    stats = types.SimpleNamespace(drive_calls=7, fast_brain_hits=3, slow_brain_runs=4, crystallizations=1)
    state = types.SimpleNamespace(
        main_loop=types.SimpleNamespace(stats=stats),
        task_registry=types.SimpleNamespace(list=lambda: [1, 2, 3]),
        proposal_registry=types.SimpleNamespace(pending=lambda: [object()]),
        token_ledger=types.SimpleNamespace(totals=lambda: {"input_tokens": 1000, "output_tokens": 500}),
        console_relaunch=None,   # reboot 会安全返回"无法重启"
    )
    return types.SimpleNamespace(state=state)


def test_is_slash():
    assert is_slash("/status") and is_slash("  /doctor") and not is_slash("hello") and not is_slash("")


def test_non_slash_returns_none():
    assert dispatch_slash("帮我整理一下", _stub_app()) is None


def test_help_lists_commands():
    out = dispatch_slash("/help", _stub_app())
    assert out["ok"] and all(("/" + c) in out["text"] for c in ("status", "doctor", "url", "reboot"))


def test_version():
    import karvyloop
    out = dispatch_slash("/version", _stub_app())
    assert out["ok"] and karvyloop.__version__ in out["text"]


def test_status_reads_state():
    out = dispatch_slash("/status", _stub_app())
    assert out["ok"] and "跑过 7" in out["text"] and "任务 3" in out["text"] and "待你拍板 1" in out["text"]


def test_doctor_runs_deterministic():
    out = dispatch_slash("/doctor", _stub_app())
    assert out["ok"] and "doctor" in out["text"]  # 跑通 run_doctor,不崩


def test_url_graceful():
    out = dispatch_slash("/url", _stub_app())
    assert out["ok"] and ("http" in out["text"] or "失败" in out["text"])


def test_reboot_safe_without_relaunch():
    out = dispatch_slash("/reboot", _stub_app())   # console_relaunch=None → 不真重启,诚实告知
    assert out["ok"] is True and "无法重启" in out["text"]   # 处理了(没崩、没起线程),只是无从重启


def test_unknown_command_points_to_help():
    out = dispatch_slash("/nope", _stub_app())
    assert out["ok"] is False and "/help" in out["text"]
