"""test_console_schedules — 定时任务 API:创建/列/算 next_run/开关/删/run_now;cron 非法拒。"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _client():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, runtime_kwargs={})
    return TestClient(app)


def test_create_list_next_run():
    c = _client()
    r = c.post("/api/schedule/create", json={"cron": "0 8 * * *", "intent": "汇总昨天进展", "title": "每日进展"}).json()
    assert r["ok"] is True
    sid = r["schedule"]["id"]
    lst = c.get("/api/schedules").json()["schedules"]
    one = next(s for s in lst if s["id"] == sid)
    assert one["cron"] == "0 8 * * *" and one["title"] == "每日进展"
    assert one["next_run"] and one["next_run"] > 0      # 算出了下次触发
    assert one["enabled"] is True


def test_bad_cron_rejected():
    c = _client()
    assert c.post("/api/schedule/create", json={"cron": "瞎写", "intent": "x"}).json()["ok"] is False


def test_toggle_then_delete():
    c = _client()
    sid = c.post("/api/schedule/create", json={"cron": "*/30 * * * *", "intent": "巡检"}).json()["schedule"]["id"]
    assert c.post("/api/schedule/toggle", json={"id": sid, "enabled": False}).json()["ok"]
    off = next(s for s in c.get("/api/schedules").json()["schedules"] if s["id"] == sid)
    assert off["enabled"] is False and off["next_run"] is None   # 关了不算 next_run
    assert c.post("/api/schedule/delete", json={"id": sid}).json()["ok"]
    assert all(s["id"] != sid for s in c.get("/api/schedules").json()["schedules"])


def test_run_now_no_llm_marks_error():
    # 无 main_loop/gateway → run_now 不崩,标 error(fail-loud)
    c = _client()
    sid = c.post("/api/schedule/create", json={"cron": "0 9 * * *", "intent": "x"}).json()["schedule"]["id"]
    assert c.post("/api/schedule/run_now", json={"id": sid}).json()["ok"]
    one = next(s for s in c.get("/api/schedules").json()["schedules"] if s["id"] == sid)
    assert one["last_status"] == "error"


def test_parse_no_llm_degrades():
    c = _client()
    assert c.post("/api/schedule/parse", json={"description": "每天8点汇总"}).json() == {"ok": False, "reason": "no_llm"}


def test_parse_passes_tz_aware_now():
    # /api/schedule/parse 传给解析器的"当前时间"必须带显式时区 offset(相对时间按此推算)
    import re
    c = _client()
    seen = {}

    def fake_parser(desc, now_str=""):
        seen["now"] = now_str
        return {"cron": "0 8 * * *", "intent": "汇总", "title": "汇总", "target_role": ""}

    c.app.state._schedule_parser_cached = fake_parser
    r = c.post("/api/schedule/parse", json={"description": "每天8点汇总"}).json()
    assert r["ok"] is True and r["cron"] == "0 8 * * *"
    assert re.search(r"[+-]\d{2}:\d{2}", seen["now"]) and "UTC" in seen["now"]
