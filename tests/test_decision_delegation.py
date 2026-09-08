from __future__ import annotations

import json

from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.crystallize.decision_delegation import (
    DecisionDelegationStore,
    authorization_verdict,
    build_questionnaire,
    compile_contract,
    detect_delegation_intent,
)
from karvyloop.gateway.events import TextDelta
from karvyloop.karvy.observer import WorkbenchObserver


ANSWERS = {
    "primary_objective": "结果质量与长期收益",
    "risk_tolerance": "最高中风险",
    "uncertainty_policy": "采用保守假设后推进",
    "autonomy_scope": "低风险选择可自主拍板",
    "hard_constraints": "不能丢失数据；不能产生外部费用",
    "escalation_triggers": "不可逆变更；需要对外承诺",
    "success_criteria": "现有测试通过；维护成本不增加",
}


class FakeGateway:
    def __init__(self, payload: dict, on_complete=None):
        self.payload = payload
        self.on_complete = on_complete
        self.calls = []

    def resolve_model(self, scope):
        return "fake:model"

    async def complete(self, messages, tools, model, **kwargs):
        self.calls.append((messages, tools, model, kwargs))
        if self.on_complete:
            self.on_complete()
        yield TextDelta(json.dumps(self.payload, ensure_ascii=False))


class EmptyErrorGateway(FakeGateway):
    async def complete(self, messages, tools, model, **kwargs):
        self.calls.append((messages, tools, model, kwargs))
        raise RuntimeError()
        yield


def _client(tmp_path, gateway=None):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.decision_delegations = DecisionDelegationStore(tmp_path / "delegations.json")
    app.state.runtime_kwargs = {"gateway": gateway, "model_ref": "fake:model"} if gateway else {}
    return TestClient(app)


def test_detect_explicit_delegation_only():
    assert detect_delegation_intent("以后让小卡帮我做技术方案选择")
    assert detect_delegation_intent("我想由你替我拍板低风险采购决策")
    assert not detect_delegation_intent("比较一下两个技术方案")


def test_compile_contract_is_draft_and_structured():
    questionnaire = build_questionnaire("选择项目技术方案")
    assert len(questionnaire["questions"]) == 7
    contract = compile_contract("选择项目技术方案", ANSWERS)
    assert contract["status"] == "draft"
    assert contract["authority"]["mode"] == "low_risk"
    assert contract["authority"]["risk_ceiling"] == "medium"
    assert contract["hard_constraints"] == ["不能丢失数据", "不能产生外部费用"]


def test_compile_rejects_tampered_choice():
    answers = dict(ANSWERS, risk_tolerance="无限风险")
    try:
        compile_contract("选择项目技术方案", answers)
    except ValueError as exc:
        assert "选项无效" in str(exc)
    else:
        raise AssertionError("tampered choice must be rejected")


def test_authorization_gate_requires_active_contract():
    contract = compile_contract("选择项目技术方案", ANSWERS)
    assessment = {
        "selected_option_id": "a", "confidence": 0.95, "risk": "low",
        "reversible": True, "constraint_violations": [],
    }
    status, reasons = authorization_verdict(contract, assessment)
    assert status == "escalated" and any("尚未激活" in reason for reason in reasons)
    contract["status"] = "active"
    status, reasons = authorization_verdict(contract, assessment)
    assert status == "decided" and reasons == []


def test_questionnaire_compile_activate_and_persist(tmp_path):
    client = _client(tmp_path)
    q = client.post("/api/decision_delegations/questionnaire", json={"goal": "选择项目技术方案"})
    assert q.status_code == 200 and len(q.json()["questionnaire"]["questions"]) == 7
    made = client.post("/api/decision_delegations/compile", json={
        "goal": "选择项目技术方案", "answers": ANSWERS,
    })
    assert made.status_code == 200
    contract_id = made.json()["contract"]["id"]
    assert made.json()["contract"]["status"] == "draft"
    activated = client.post(f"/api/decision_delegations/{contract_id}/activate", json={
        "authorization_confirmed": True,
    })
    assert activated.status_code == 200
    reloaded = DecisionDelegationStore(tmp_path / "delegations.json")
    assert reloaded.get(contract_id)["status"] == "active"


def test_revoked_contract_cannot_be_reactivated(tmp_path):
    client = _client(tmp_path)
    made = client.post("/api/decision_delegations/compile", json={
        "goal": "选择项目技术方案", "answers": ANSWERS,
    }).json()
    contract_id = made["contract"]["id"]
    assert client.post(f"/api/decision_delegations/{contract_id}/revoke").status_code == 200
    response = client.post(f"/api/decision_delegations/{contract_id}/activate", json={
        "authorization_confirmed": True,
    })
    assert response.status_code == 409
    assert client.app.state.decision_delegations.get(contract_id)["status"] == "revoked"


def test_recommend_mode_cannot_activate(tmp_path):
    client = _client(tmp_path)
    answers = dict(ANSWERS, autonomy_scope="只给建议，由我拍板")
    made = client.post("/api/decision_delegations/compile", json={"goal": "选择项目技术方案", "answers": answers})
    contract_id = made.json()["contract"]["id"]
    response = client.post(f"/api/decision_delegations/{contract_id}/activate", json={"authorization_confirmed": True})
    assert response.status_code == 400


def test_decide_passes_gate_and_records_receipt(tmp_path):
    gateway = FakeGateway({
        "selected_option_id": "a", "confidence": 0.91, "risk": "low", "reversible": True,
        "constraint_violations": [], "escalation_matches": [], "reason": "更符合长期质量目标",
        "scores": [{"option_id": "a", "score": 88, "reason": "质量高"}, {"option_id": "b", "score": 70, "reason": "交付快"}],
    })
    client = _client(tmp_path, gateway)
    made = client.post("/api/decision_delegations/compile", json={"goal": "选择项目技术方案", "answers": ANSWERS}).json()
    contract_id = made["contract"]["id"]
    client.post(f"/api/decision_delegations/{contract_id}/activate", json={"authorization_confirmed": True})
    result = client.post("/api/decision_delegations/decide", json={
        "contract_id": contract_id, "scenario": "为新服务选择数据库",
        "options": [{"id": "a", "label": "PostgreSQL"}, {"id": "b", "label": "SQLite"}],
    })
    assert result.status_code == 200
    assert result.json()["status"] == "decided"
    assert result.json()["selected_option"]["label"] == "PostgreSQL"
    stored = client.app.state.decision_delegations.get(contract_id)
    assert len(stored["decisions"]) == 1
    assert gateway.calls[0][1] == []
    assert "<fenced-data" in gateway.calls[0][0][0]["content"]


def test_decide_reports_fallback_for_empty_gateway_error(tmp_path):
    client = _client(tmp_path, EmptyErrorGateway({}))
    made = client.post("/api/decision_delegations/compile", json={"goal": "选择项目技术方案", "answers": ANSWERS}).json()
    contract_id = made["contract"]["id"]
    client.post(f"/api/decision_delegations/{contract_id}/activate", json={"authorization_confirmed": True})

    result = client.post("/api/decision_delegations/decide", json={
        "contract_id": contract_id, "scenario": "为新服务选择数据库",
        "options": [{"id": "a", "label": "PostgreSQL"}, {"id": "b", "label": "SQLite"}],
    })

    assert result.status_code == 502
    assert result.json()["detail"] == "AI 决策失败：模型服务未返回错误详情"


def test_store_does_not_overwrite_cross_instance_revocation(tmp_path):
    path = tmp_path / "delegations.json"
    first = DecisionDelegationStore(path)
    contract = first.put(compile_contract("选择项目技术方案", ANSWERS))
    first.set_status(contract["id"], "active")
    stale_instance = DecisionDelegationStore(path)
    first.set_status(contract["id"], "revoked")
    receipt = stale_instance.record_decision(contract["id"], {
        "status": "decided", "gate_reasons": [], "selected_option": {"id": "a"},
    })
    assert receipt["status"] == "escalated"
    assert DecisionDelegationStore(path).get(contract["id"])["status"] == "revoked"
    assert stale_instance.begin_decision(contract["id"]) is None


def test_decide_rejects_inactive_contract_before_model_call(tmp_path):
    gateway = FakeGateway({})
    client = _client(tmp_path, gateway)
    made = client.post("/api/decision_delegations/compile", json={
        "goal": "选择项目技术方案", "answers": ANSWERS,
    }).json()
    result = client.post("/api/decision_delegations/decide", json={
        "contract_id": made["contract"]["id"], "scenario": "为新服务选择数据库",
        "options": [{"id": "a", "label": "PostgreSQL"}, {"id": "b", "label": "SQLite"}],
    })
    assert result.status_code == 409
    assert gateway.calls == []


def test_decide_honors_revocation_during_model_call(tmp_path):
    payload = {
        "selected_option_id": "a", "confidence": 0.95, "risk": "low", "reversible": True,
        "constraint_violations": [], "escalation_matches": [], "reason": "质量更高",
        "scores": [{"option_id": "a", "score": 90, "reason": "质量"}],
    }
    client = _client(tmp_path)
    made = client.post("/api/decision_delegations/compile", json={
        "goal": "选择项目技术方案", "answers": ANSWERS,
    }).json()
    contract_id = made["contract"]["id"]
    client.post(f"/api/decision_delegations/{contract_id}/activate", json={"authorization_confirmed": True})
    client.app.state.runtime_kwargs = {
        "gateway": FakeGateway(payload, lambda: client.app.state.decision_delegations.set_status(contract_id, "revoked")),
        "model_ref": "fake:model",
    }
    result = client.post("/api/decision_delegations/decide", json={
        "contract_id": contract_id, "scenario": "为新服务选择数据库",
        "options": [{"id": "a", "label": "PostgreSQL"}, {"id": "b", "label": "SQLite"}],
    })
    assert result.status_code == 200
    assert result.json()["status"] == "escalated"
    assert any("撤销" in reason or "撤回" in reason for reason in result.json()["gate_reasons"])
    assert client.app.state.decision_delegations.get(contract_id)["decisions"][-1]["status"] == "escalated"


def test_decide_escalates_on_trigger_or_low_confidence(tmp_path):
    gateway = FakeGateway({
        "selected_option_id": "a", "confidence": 0.6, "risk": "low", "reversible": True,
        "constraint_violations": [], "escalation_matches": ["需要对外承诺"], "reason": "信息不足",
        "scores": [{"option_id": "a", "score": 60, "reason": "不确定"}, {"option_id": "b", "score": 59, "reason": "接近"}],
    })
    client = _client(tmp_path, gateway)
    made = client.post("/api/decision_delegations/compile", json={"goal": "选择项目技术方案", "answers": ANSWERS}).json()
    contract_id = made["contract"]["id"]
    client.post(f"/api/decision_delegations/{contract_id}/activate", json={"authorization_confirmed": True})
    result = client.post("/api/decision_delegations/decide", json={
        "contract_id": contract_id, "scenario": "需要对客户承诺交付日期",
        "options": [{"id": "a", "label": "本周"}, {"id": "b", "label": "下周"}],
    }).json()
    assert result["status"] == "escalated"
    assert result["gate_reasons"]
