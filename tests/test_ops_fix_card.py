"""test_ops_fix_card — L1 自愈 slice3:运维诊断升正式 H2A 决策卡。

锁三件事(诚实铁律):
1. proposal_for_ops_fix 卡天然 unverifiable(无 sig)+ basis 含原因/修法 + 幂等 id;
2. _ops_fix_handler **绝不执行 LLM 文本**:只在 auto_fixable&reversible 时跑确定性 repair_finding,
   否则只"记下不改系统";
3. POST /ops/propose_fix:无问题不造卡 / 无模型退回 / 有问题 register+broadcast + 幂等 + 折入运行时报错。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_OPS_FIX, proposal_for_ops_fix,
)


# ---- 1. 工厂:unverifiable + basis + 幂等 ----
def _diag(summary="数据文件坏了", cause="JSON 被截断", fix="备份后重置该文件", risk="reversible"):
    return {"summary": summary, "cause": cause, "fix": fix, "risk": risk}


def test_factory_shape_and_unverifiable():
    p = proposal_for_ops_fix(diagnosis=_diag(), finding_codes=["data_corrupt"],
                             ts=1.0, auto_fixable=True)
    assert p.kind == KIND_OPS_FIX
    assert p.summary == "数据文件坏了"
    # 天然 unverifiable:payload 无 sig → 决策卡 build 时落"未核验"区(不伪 grounded)
    assert "sig" not in p.payload
    assert p.payload["finding_codes"] == ["data_corrupt"] and p.payload["auto_fixable"] is True
    # basis 含原因 + 修法 + 可逆修复口径
    assert "JSON 被截断" in p.basis and "备份后重置该文件" in p.basis
    assert "确定性可逆修复" in p.basis


def test_factory_needs_approval_basis_says_no_auto_change():
    p = proposal_for_ops_fix(diagnosis=_diag(risk="needs_approval"),
                             finding_codes=["no_key"], ts=1.0, auto_fixable=False)
    assert "未经验证" in p.basis and "不会自动改" in p.basis


def test_factory_idempotent_id_by_codes():
    a = proposal_for_ops_fix(diagnosis=_diag(summary="x"), finding_codes=["b", "a"], ts=1.0)
    b = proposal_for_ops_fix(diagnosis=_diag(summary="完全不同的措辞"), finding_codes=["a", "b"], ts=2.0)
    assert a.proposal_id == b.proposal_id   # 同坏态(码集合)→ 同一张卡,不刷屏
    c = proposal_for_ops_fix(diagnosis=_diag(), finding_codes=["c"], ts=1.0)
    assert c.proposal_id != a.proposal_id


# ---- 2. handler:LLM 文本绝不执行;只确定性可逆修复 ----
def test_handler_needs_approval_records_not_executes(monkeypatch):
    import karvyloop.doctor as D
    from karvyloop.console.proposal_handlers import _ops_fix_handler
    # 哨兵:若 handler 敢碰确定性修复,这里会被调到 → 断言它**没被调**
    called = {"repair": False, "doctor": False}
    monkeypatch.setattr(D, "repair_finding", lambda f: called.__setitem__("repair", True))
    monkeypatch.setattr(D, "run_doctor", lambda check_port=False: called.__setitem__("doctor", True) or [])
    p = proposal_for_ops_fix(diagnosis=_diag(risk="needs_approval"),
                             finding_codes=["no_key"], ts=1.0, auto_fixable=False)
    ok, detail = _ops_fix_handler(p)
    assert ok is True and "系统不会自动改" in detail
    assert called["repair"] is False   # needs_approval → 绝不执行任何修复


def test_handler_reversible_runs_only_deterministic_repair(monkeypatch):
    import karvyloop.doctor as D
    from karvyloop.console.proposal_handlers import _ops_fix_handler
    seen = {"codes_repaired": []}
    # 重新跑 doctor 取新鲜 finding(只这条匹配)
    monkeypatch.setattr(D, "run_doctor",
                        lambda check_port=False: [D.Finding(D.WARN, "data_corrupt", {"files": "tasks.json"})])

    def _fake_repair(f):
        seen["codes_repaired"].append(f.code)
        return D.Finding(D.OK, "repaired_data_corrupt", {"files": f.params.get("files", "")})
    monkeypatch.setattr(D, "repair_finding", _fake_repair)

    p = proposal_for_ops_fix(diagnosis=_diag(risk="reversible"),
                             finding_codes=["data_corrupt"], ts=1.0, auto_fixable=True)
    ok, detail = _ops_fix_handler(p)
    assert ok is True and "备份" in detail
    assert seen["codes_repaired"] == ["data_corrupt"]   # 跑的是确定性 repair,不是 LLM 文本


def test_handler_reversible_but_not_autofixable_does_not_repair(monkeypatch):
    import karvyloop.doctor as D
    from karvyloop.console.proposal_handlers import _ops_fix_handler
    called = {"repair": False}
    monkeypatch.setattr(D, "repair_finding", lambda f: called.__setitem__("repair", True))
    # code 不在 AUTO_FIXABLE(如 port_busy)→ 即便 risk=reversible 也不碰系统
    p = proposal_for_ops_fix(diagnosis=_diag(risk="reversible"),
                             finding_codes=["port_busy"], ts=1.0, auto_fixable=False)
    ok, detail = _ops_fix_handler(p)
    assert ok is True and called["repair"] is False


# ---- 3. 端点 ----
class TextDelta:                      # 名字必须叫 TextDelta(diagnose 按 type(ev).__name__ 取文本)
    def __init__(self, text):
        self.text = text


def _gw(chunk):
    class _GW:
        def resolve_model(self, scope):
            return "x/y"

        async def complete(self, messages, tools, ref, system=None):
            yield TextDelta(chunk)
    return _GW()


def _app(monkeypatch, findings):
    import karvyloop.doctor as D
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    monkeypatch.setattr(D, "run_doctor", lambda check_port=False: findings)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    return app


def test_endpoint_healthy_makes_no_card(monkeypatch):
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    app = _app(monkeypatch, [D.Finding(D.OK, "model_ready", {"model": "m"})])
    r = TestClient(app).post("/api/ops/propose_fix", json={}).json()
    assert r["ok"] is True and r["healthy"] is True and r["proposal_id"] == ""
    assert len(app.state.proposal_registry) == 0


def test_endpoint_no_model_falls_back(monkeypatch):
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    app = _app(monkeypatch, [D.Finding(D.FAIL, "no_key", {})])   # 没设 gateway
    r = TestClient(app).post("/api/ops/propose_fix", json={}).json()
    assert r["ok"] is True and r["reason"] == "no_model" and r["proposal_id"] == ""


def test_endpoint_registers_and_is_idempotent(monkeypatch):
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    app = _app(monkeypatch, [D.Finding(D.WARN, "data_corrupt", {"files": "tasks.json"})])
    app.state.runtime_kwargs = {
        "gateway": _gw('{"summary":"数据坏了","cause":"截断","fix":"重置","risk":"reversible"}'),
        "model_ref": ""}
    c = TestClient(app)
    r1 = c.post("/api/ops/propose_fix", json={}).json()
    assert r1["ok"] is True and r1["proposal_id"].startswith("ops_fix-")
    assert r1["auto_fixable"] is True            # data_corrupt ∈ AUTO_FIXABLE & reversible
    assert len(app.state.proposal_registry) == 1
    r2 = c.post("/api/ops/propose_fix", json={}).json()
    assert r2["proposal_id"] == r1["proposal_id"]   # 幂等:同坏态不再多一张卡
    assert len(app.state.proposal_registry) == 1


def test_endpoint_folds_runtime_error_signal(monkeypatch):
    """无 doctor 问题、但带真实运行时报错 → 仍诊断造卡(codes 空 → auto_fixable False)。"""
    from fastapi.testclient import TestClient
    import karvyloop.doctor as D
    app = _app(monkeypatch, [D.Finding(D.OK, "model_ready", {"model": "m"})])  # doctor 全绿
    app.state.runtime_kwargs = {
        "gateway": _gw('{"summary":"后台蒸馏炸了","cause":"x","fix":"看日志","risk":"needs_approval"}'),
        "model_ref": ""}
    r = TestClient(app).post("/api/ops/propose_fix",
                             json={"error": "KeyError: 'role'", "source": "auto_distill"}).json()
    assert r["ok"] is True and r["proposal_id"].startswith("ops_fix-")
    assert r["auto_fixable"] is False            # 纯运行时错,无确定性可逆修复
    assert len(app.state.proposal_registry) == 1
