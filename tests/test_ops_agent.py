"""test_ops_agent — 自愈运维 agent(L1):接地 + 宁空勿毒 + 只诊断不执行 + 无模型退确定性。"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import ops_agent as O  # noqa: E402


# ---- 严格解析(宁空勿毒)----
def test_parse_good_json():
    d = O.parse_diagnosis('{"summary":"端口被占","cause":"已有进程","fix":"换端口","risk":"reversible"}')
    assert d["summary"] == "端口被占" and d["fix"] == "换端口" and d["risk"] == "reversible"


def test_parse_fence_wrapped():
    d = O.parse_diagnosis('```json\n{"summary":"x","cause":"","fix":"y","risk":"needs_approval"}\n```')
    assert d is not None and d["summary"] == "x" and d["fix"] == "y"


def test_parse_garbage_returns_none():
    assert O.parse_diagnosis("不是 JSON,纯散文诊断") is None       # prose 不抽
    assert O.parse_diagnosis("{broken json") is None               # 像 JSON 却坏 → None
    assert O.parse_diagnosis("") is None


def test_parse_missing_substance_returns_none():
    assert O.parse_diagnosis('{"summary":"","cause":"x","fix":"","risk":"reversible"}') is None  # 无实质
    assert O.parse_diagnosis('{"summary":"x","fix":""}') is None


def test_parse_bad_risk_defaults_needs_approval():
    d = O.parse_diagnosis('{"summary":"a","fix":"b","risk":"whatever"}')
    assert d["risk"] == "needs_approval"          # 拿不准 → 保守要批准


# ---- diagnose(mock gateway)----
class TextDelta:                                  # 名字必须叫 TextDelta(diagnose 按 __name__ 取)
    def __init__(self, text):
        self.text = text


def _gw(chunks, *, raise_on_complete=False):
    class _GW:
        def resolve_model(self, scope):
            return "x/y"

        async def complete(self, messages, tools, ref, system=None):
            if raise_on_complete:
                raise RuntimeError("gateway boom")
            for c in chunks:
                yield TextDelta(c)
    return _GW()


def test_diagnose_ok_with_valid_json():
    gw = _gw(['{"summary":"端口被占","cause":"c","fix":"换端口","risk":"reversible"}'])
    d = asyncio.run(O.diagnose("- 控制台端口 8766 被占用", gateway=gw))
    assert d.ok is True and d.risk == "reversible" and "端口" in d.summary


def test_diagnose_prose_is_not_ok():
    gw = _gw(["我觉得大概是端口的问题吧"])     # LLM 吐散文 → 宁空勿毒,ok=False
    d = asyncio.run(O.diagnose("signal", gateway=gw))
    assert d.ok is False


def test_diagnose_no_gateway_is_not_ok():
    d = asyncio.run(O.diagnose("signal", gateway=None))
    assert d.ok is False


def test_diagnose_gateway_error_is_not_ok():
    d = asyncio.run(O.diagnose("signal", gateway=_gw([], raise_on_complete=True)))
    assert d.ok is False


# ---- 端点 ----
def test_endpoint_no_model_falls_back(monkeypatch):
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    monkeypatch.setattr(D, "run_doctor", lambda check_port=False: [D.Finding(D.FAIL, "no_key", {})])
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)   # 无 gateway
    r = TestClient(app).get("/api/ops/diagnose").json()
    assert r["ok"] is True and r["healthy"] is False and r["reason"] == "no_model"


def test_endpoint_with_gateway_diagnoses(monkeypatch):
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    monkeypatch.setattr(D, "run_doctor", lambda check_port=False: [D.Finding(D.FAIL, "no_key", {})])
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.runtime_kwargs = {"gateway": _gw(['{"summary":"没 key","cause":"c","fix":"去配置页加 key","risk":"needs_approval"}']),
                                "model_ref": ""}
    r = TestClient(app).get("/api/ops/diagnose").json()
    assert r["ok"] is True and r["diagnosis"]["summary"] == "没 key" and r["diagnosis"]["risk"] == "needs_approval"
