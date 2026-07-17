"""test_console_budget_doctor_resume — docs/56 audit ② MED:后端有能力没 UI 入口,补三个入口的端点测试。

三个入口(各自后端已存在,此批接 UI + 补/加端点):
  1. /api/budget       花费预算上限(GET 用量/上限,POST 改上限写 config.yaml)
  2. /api/doctor/fix   doctor 确定性自愈的 UI 触发(auto 直接修,confirm 需 body confirm 才修)
  3. workflow 续/丢    resume/discard/pending_resume(端点已在,验其行为与幂等)

铁律核对:改预算只碰 `budget:` 块(别人的键/密钥不动);doctor confirm 项没确认绝不动 config;
永不外泄 key。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.llm.token_ledger import TokenLedger  # noqa: E402


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.yaml"


@pytest.fixture
def client(cfg_path):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.config_path = str(cfg_path)
    # 内存账本喂点用量,让 /api/budget 有真实"已用"可展示
    led = TokenLedger()
    led.record(source="unknown", model="m1", input=1000, output=500)
    app.state.token_ledger = led
    return TestClient(app)


# ---------- 1. /api/budget ----------

class TestBudget:
    def test_get_budget_disabled_still_reports_usage(self, client):
        """未配预算(disabled)也返真实用量 + 上限=null → 用户先看花多少再设限。"""
        r = client.get("/api/budget")
        assert r.status_code == 200
        d = r.json()
        assert d["enabled"] is False
        dims = {x["key"]: x for x in d["dimensions"]}
        assert dims["daily_tokens"]["used"] == 1500     # 1000+500 记进今日
        assert dims["daily_tokens"]["limit"] is None     # 未设限
        assert "warn" in d["valid_on_limit"] and "pause" in d["valid_on_limit"]

    def test_post_budget_writes_only_budget_block(self, client, cfg_path):
        """改预算只碰 `budget:` 块 —— 别人的键(含密钥)一字不动。"""
        cfg_path.write_text(
            "models:\n  providers:\n    p1:\n      api_key: SECRET-DO-NOT-LEAK\n"
            "agents:\n  defaults:\n    model: p1/m1\n", encoding="utf-8")
        r = client.post("/api/budget", json={"daily_usd": 5.0, "on_limit": "pause"})
        assert r.status_code == 200 and r.json()["ok"] is True
        txt = cfg_path.read_text(encoding="utf-8")
        assert "p1/m1" in txt and "SECRET-DO-NOT-LEAK" in txt   # 原有键完好
        assert "daily_usd" in txt and "on_limit: pause" in txt   # 预算已写

    def test_post_budget_then_get_reflects_limit_and_ratio(self, client):
        """POST 上限后 GET 立刻反映上限 + ratio(1500/2000=0.75)。"""
        assert client.post("/api/budget", json={"daily_tokens": 2000}).json()["ok"] is True
        d = client.get("/api/budget").json()
        assert d["enabled"] is True
        dt = {x["key"]: x for x in d["dimensions"]}["daily_tokens"]
        assert dt["limit"] == 2000
        assert abs(dt["ratio"] - 0.75) < 1e-6

    def test_post_budget_all_zero_removes_brake(self, client, cfg_path):
        """四维显式全 0 = 关刹车(无限,零回归):删掉整个 budget 块。"""
        client.post("/api/budget", json={"daily_tokens": 2000})
        assert "budget" in cfg_path.read_text(encoding="utf-8")
        # 显式把四维都传 0 = 显式清零(区别于"没传该维度"=保留,见下条)
        client.post("/api/budget", json={"daily_usd": 0, "monthly_usd": 0,
                                         "daily_tokens": 0, "monthly_tokens": 0})
        assert "budget" not in cfg_path.read_text(encoding="utf-8")
        assert client.get("/api/budget").json()["enabled"] is False

    def test_post_budget_usd_save_preserves_config_token_limit(self, client, cfg_path):
        """审计 #87 §3-②:手配 token 上限后,UI 只存 USD(不传 token 维度)→ token 上限不被抹掉。

        这是"静默清零"的核心复现:此前前端硬塞 daily/monthly_tokens=0 + 后端把 0 当清零 →
        一动 USD 表单,config.yaml 手配的 token 刹车就消失。修后:未承载的维度保留。"""
        import yaml
        # 用户在 config.yaml 手配了 token 上限(文档路径)
        cfg_path.write_text(
            "budget:\n  daily_tokens: 2000000\n  monthly_tokens: 50000000\n  on_limit: pause\n",
            encoding="utf-8")
        # UI 存 USD(前端新行为:不传 token 维度)
        r = client.post("/api/budget", json={"daily_usd": 5.0, "monthly_usd": 100.0,
                                             "on_limit": "pause"})
        assert r.status_code == 200 and r.json()["ok"] is True
        b = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["budget"]
        assert b["daily_tokens"] == 2000000     # 手配 token 上限没被抹掉(核心)
        assert b["monthly_tokens"] == 50000000
        assert b["daily_usd"] == 5.0 and b["monthly_usd"] == 100.0   # USD 也写进去了
        assert b["on_limit"] == "pause"

    def test_post_budget_empty_body_is_noop(self, client, cfg_path):
        """空 body(没承载任何维度)= 不动已有配置(保留),不再当"全清"。"""
        client.post("/api/budget", json={"daily_tokens": 2000})
        client.post("/api/budget", json={})   # 什么都没传
        assert client.get("/api/budget").json()["enabled"] is True   # 保留,brake 仍在

    def test_get_budget_never_leaks_key(self, client, cfg_path):
        """预算响应体绝不含密钥字面量。"""
        cfg_path.write_text("models:\n  providers:\n    p1:\n      api_key: SECRET-DO-NOT-LEAK\n",
                            encoding="utf-8")
        assert "SECRET-DO-NOT-LEAK" not in client.get("/api/budget").text


# ---------- 2. /api/doctor/fix ----------

class TestDoctorFix:
    def test_auto_fix_repairs_missing_config(self, client, cfg_path):
        """config_missing 是 AUTO_FIXABLE → confirm=false 也直接修(纯创建骨架)。"""
        assert not cfg_path.exists()
        r = client.post("/api/doctor/fix", json={"confirm": False})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert "repaired_config_missing" in [x["code"] for x in d["repaired"]]
        assert cfg_path.exists()   # 骨架已写

    def test_confirm_item_not_touched_without_confirm(self, client, cfg_path):
        """config_unreadable 是 CONFIRM_FIXABLE → confirm=false 不动 config,只列 needs_confirm。"""
        cfg_path.write_text("this: is: broken: ][ yaml", encoding="utf-8")
        r = client.post("/api/doctor/fix", json={"confirm": False})
        d = r.json()
        assert "config_unreadable" in [x["code"] for x in d["needs_confirm"]]
        assert "repaired_config_unreadable" not in [x["code"] for x in d["repaired"]]
        assert cfg_path.read_text(encoding="utf-8") == "this: is: broken: ][ yaml"  # 原样

    def test_confirm_true_repairs_with_backup(self, client, cfg_path):
        """confirm=true(前端已二次确认)→ 备份坏 config + 写骨架。"""
        cfg_path.write_text("this: is: broken: ][ yaml", encoding="utf-8")
        r = client.post("/api/doctor/fix", json={"confirm": True})
        d = r.json()
        assert "repaired_config_unreadable" in [x["code"] for x in d["repaired"]]
        baks = list(cfg_path.parent.glob("config.yaml.bak*"))
        assert baks, "坏 config 应备份成 .bak(可逆)"


# ---------- 3. workflow 续/丢/查 ----------

class TestWorkflowResumeDiscard:
    def test_pending_resume_empty_by_default(self, client):
        """没有中断流程 → pending 空(不炸)。"""
        r = client.get("/api/workflow/pending_resume")
        assert r.status_code == 200 and r.json()["pending"] == []

    def test_pending_resume_reflects_state(self, client):
        """app.state.pending_resume 有条目 → 端点如实返回(前端据此画横幅)。"""
        c = client
        c.app.state.pending_resume = [{"run_id": "r1", "goal": "写报告", "done": 1, "total": 3}]
        d = c.get("/api/workflow/pending_resume").json()
        assert len(d["pending"]) == 1 and d["pending"][0]["run_id"] == "r1"

    def test_discard_unknown_run_is_safe(self, client):
        """丢弃不存在的 run → 不 500(优雅返回)。"""
        r = client.post("/api/workflow/discard", json={"run_id": "nope"})
        assert r.status_code == 200
