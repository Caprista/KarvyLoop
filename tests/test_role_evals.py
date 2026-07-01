"""test_role_evals — #39 ⑤ 角色行为 evals:存断言 / 判定 / API CRUD / 无 LLM 降级。"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.role_evals import RoleEvalStore, judge_eval  # noqa: E402


def _client():
    return TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None, runtime_kwargs={}))


def test_judge_contains_and_absent():
    ev = {"contains": ["净额", "退款"], "absent": ["大概"]}
    assert judge_eval("收入按退款净额计", ev)["passed"] is True          # 含两个关键词、无禁词 → 过
    bad = judge_eval("大概是退款净额吧", ev)
    assert bad["passed"] is False and bad["present_forbidden"] == ["大概"]   # 出现禁词 → 挂
    miss = judge_eval("退款相关", ev)                                    # 缺"净额" → 挂
    assert miss["passed"] is False and "净额" in miss["missing"]


def test_store_crud_persist(tmp_path):
    p = tmp_path / "role_evals.json"
    st = RoleEvalStore(p)
    ev = st.add("pm", "本周收入多少?", contains=["净额"], absent=["大概"])
    assert ev and st.list("pm")[0]["prompt"] == "本周收入多少?"
    assert RoleEvalStore(p).list("pm")[0]["contains"] == ["净额"]       # 重启仍在
    assert st.delete("pm", ev["id"]) and RoleEvalStore(p).list("pm") == []


def test_api_crud_and_run_degrades():
    c = _client()
    r = c.post("/api/role/eval/add", json={"role_id": "pm", "prompt": "本周收入?", "contains": ["净额"]}).json()
    assert r["ok"] and r["eval"]["id"]
    assert c.get("/api/role/evals", params={"role_id": "pm"}).json()["evals"][0]["prompt"] == "本周收入?"
    # 无 main_loop/gateway → run 降级 no_llm(不崩)
    assert c.post("/api/role/eval/run", json={"role_id": "pm"}).json() == {"ok": False, "reason": "no_llm"}
    assert c.post("/api/role/eval/delete", json={"role_id": "pm", "eval_id": r["eval"]["id"]}).json()["ok"]
