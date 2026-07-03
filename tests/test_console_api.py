"""test_console_api — FastAPI 后端 HTTP 端点(M3+ 批 8.5-C-backend)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

AC 列表:
- AC1: GET /api/snapshot 返 dict 含全部字段
- AC2: POST /api/intent 无 main_loop 时返 {error: ...},**不** 500
- AC3: POST /api/h2a_decide {decision:"REJECT", reason:""} → 200(Hardy:不强制 reason;
        协议 A8 由占位 reason 守住,by=[] 仍锁)
- AC4: POST /api/h2a_decide {decision:"ACCEPT"} → 返真实 envelope,by=[] (K5 不变量)
- AC5: K4/K5 grep gate(在 commit 前跑,不在 pytest 里)
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def app():
    workbench = WorkbenchObserver()
    return build_console_app(workbench=workbench, main_loop=None)


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------- AC1: /api/snapshot 字段完整 ----------

class TestAC1SnapshotEndpoint:
    def test_snapshot_returns_dict_with_all_fields(self, client):
        """AC1: GET /api/snapshot 返 dict 含全部 WidgetSnapshot 字段。"""
        r = client.get("/api/snapshot")
        assert r.status_code == 200
        data = r.json()
        # 11 个字段(domains, current_domain, broadcasts, task_count, pursuit_count,
        # unhealthy, crystallized_skills, last_fast_brain_skill, last_drive_text,
        # last_error, last_intent)
        for key in (
            "domains", "current_domain", "broadcasts", "task_count", "pursuit_count",
            "unhealthy", "crystallized_skills", "last_fast_brain_skill",
            "last_drive_text", "last_error", "last_intent",
        ):
            assert key in data, f"missing key: {key}"


# ---------- AC2: /api/intent 无 main_loop 返 200 + error dict ----------

class TestAC2IntentEndpoint:
    def test_intent_no_main_loop_returns_error_not_500(self, client):
        """AC2: POST /api/intent 无 main_loop → 200 + error dict(修 silent-fail,**不** 500)。"""
        r = client.post("/api/intent", json={"intent": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert "error" in data
        assert "MainLoop" in data["error"]

    def test_intent_empty_string_422(self, client):
        """空 intent 被 Pydantic 拒(422)。"""
        r = client.post("/api/intent", json={"intent": ""})
        assert r.status_code == 422


# ---------- AC3: /api/h2a_decide REJECT 无 reason → 422 ----------

class TestAC3H2ADecideRejectNoReason:
    def test_reject_without_reason_is_allowed(self, client):
        """AC3(改): reason 不强制(Hardy) — REJECT 无 reason 也 200 + by=[] 的 K5 envelope。

        K5 真义 = 人拍板 / envelope by=[],与 reason 无关;强制 reason 是被否决的多余摩擦。
        """
        r = client.post("/api/h2a_decide", json={
            "proposal_id": "p1", "decision": "REJECT", "reason": "",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["envelope"]["type"] == "reject"
        assert data["envelope"]["by"] == []   # K5 不变量仍锁住

    def test_reject_with_reason_returns_envelope(self, client):
        """AC3b: REJECT + reason → 200 + envelope。"""
        r = client.post("/api/h2a_decide", json={
            "proposal_id": "p1", "decision": "REJECT", "reason": "no thanks",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["envelope"]["type"] == "reject"  # EnvelopeType.REJECT = "reject"
        assert data["envelope"]["by"] == []  # K5 不变量


# ---------- AC4: /api/h2a_decide ACCEPT → envelope, by=[] ----------

class TestAC4H2ADecideAccept:
    def test_accept_returns_envelope_with_empty_by(self, client):
        """AC4: ACCEPT → 真实 envelope(K5 factory),by=[] (K5 不变量:不经 Courier)。"""
        r = client.post("/api/h2a_decide", json={
            "proposal_id": "p1", "decision": "ACCEPT",
        })
        assert r.status_code == 200
        data = r.json()
        env = data["envelope"]
        assert env is not None
        assert env["type"] == "accept"  # EnvelopeType.ACCEPT = "accept"
        assert env["by"] == []  # K5 不变量
        assert env["payload"]["proposal_id"] == "p1"

    def test_defer_returns_null_envelope(self, client):
        """DEFER → envelope=null(K5:DEFER 不发 envelope)。"""
        r = client.post("/api/h2a_decide", json={
            "proposal_id": "p1", "decision": "DEFER",
        })
        assert r.status_code == 200
        assert r.json()["envelope"] is None


# ---------- /api/stats ----------

class TestStatsEndpoint:
    def test_stats_no_main_loop_returns_zeros(self, client):
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["main_loop_present"] is False
        assert data["drive_calls"] == 0
        assert data["fast_brain_hit_rate"] == 0.0


# ---------- /api/chat_history ----------

class TestChatHistoryEndpoint:
    def test_chat_history_no_workbench_app_returns_empty(self, client):
        """没 workbench_app → 返空 list(无 500)。"""
        r = client.get("/api/chat_history")
        assert r.status_code == 200
        assert r.json() == []


# ---------- /healthz + / ----------

class TestHealthAndIndex:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_index_returns_placeholder_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "KarvyLoop Console" in r.text


# ---------- K4/K5 grep gate 锁(commit 前跑) ----------

class TestKKlawGrepGate:
    """AC5: 灵魂铁律 grep — 8.5-C-backend 阶段**不**应有 K4/K5 违规。

    允许:`decision_to_envelope` 调用、`Envelope(` import 引用、`envelope_to_dict` 名字。
    不允许:`domain.apply_*` 调用、`Envelope(` 偷构(直接 `Envelope(type=...,...)`)。
    """

    def test_no_domain_apply_in_console(self):
        """K4: console/ 下 0 `domain.apply_*` 调用。"""
        console_dir = pathlib.Path(ROOT) / "karvyloop" / "console"
        violations: list[str] = []
        for py in console_dir.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for m in re.finditer(r"domain\.apply_\w+\s*\(", text):
                # 允许注释里的"apply_*"(grep 不应误报)
                line = text[: m.start()].rsplit("\n", 1)[-1]
                if line.strip().startswith("#"):
                    continue
                violations.append(f"{py}: {line.strip()}")
        assert violations == [], f"K4 violations: {violations}"

    def test_no_envelope_construction_outside_decision_to_envelope(self):
        """K5: console/ 下**直接** `Envelope(type=...)` 偷构 = 0。

        允许:`from karvyloop.a2a import Envelope` 引用、`envelope_to_dict` 名字、
        `decision_to_envelope` 工厂调用、`EnvelopeType.X.value` 引用、docstring 内的描述。
        """
        console_dir = pathlib.Path(ROOT) / "karvyloop" / "console"
        violations: list[str] = []
        for py in console_dir.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            # 用 tokenize 跳过 docstring + comment
            import tokenize
            import io
            try:
                tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
            except (tokenize.TokenizeError, IndentationError):
                continue
            in_docstring = False
            docstring_quote = None
            for tok in tokens:
                # 跟踪 docstring 状态
                if tok.type == tokenize.STRING and not in_docstring:
                    # 可能是 module docstring(class/func docstring 单独判)
                    if tok.start[0] == 1:  # 行 1
                        in_docstring = True
                        docstring_quote = tok.string[:3]
                        continue
                if in_docstring and tok.type == tokenize.NEWLINE:
                    if tok.start[0] > 1:
                        in_docstring = False
                if in_docstring:
                    continue
                if tok.type == tokenize.COMMENT:
                    continue
                if tok.type != tokenize.NAME:
                    continue
                if tok.string != "Envelope":
                    continue
                # 下一个非空白 token 必须是 `(`
                # 简化:直接查源 text
            # 简化路径:用 line 级别判断 + docstring 跟踪
            in_doc = False
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                # 跟踪 docstring 开关(简化:行首 """ 切换)
                if '"""' in line or "'''" in line:
                    count = line.count('"""') + line.count("'''")
                    if count == 1:
                        in_doc = not in_doc
                        continue
                    elif count >= 2:
                        in_doc = False
                        continue
                if in_doc:
                    continue
                if stripped.startswith("#"):
                    continue
                for m in re.finditer(r"\bEnvelope\s*\(", line):
                    if "envelope_to_dict" in line or "decision_to_envelope" in line:
                        continue
                    if "EnvelopeType" in line:
                        continue
                    violations.append(f"{py}:{lineno}: {stripped}")
        assert violations == [], f"K5 violations: {violations}"


# ---------- 静态资源禁强缓存(部署后普通刷新即拿新 JS,不再"刷了还是旧的")----------
def test_static_assets_send_no_cache(client):
    """/static/* 响应必须带 Cache-Control: no-cache → 浏览器每次带 ETag 条件请求(改了才 200)。"""
    r = client.get("/static/i18n.js")
    if r.status_code == 404:
        import pytest
        pytest.skip("static 未构建(裸仓)")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", ""), r.headers.get("cache-control")


# ---- #42 优化②:首跑预检错误分类 + Ollama 本地探测 ----

def test_model_error_taxonomy():
    from karvyloop.console.routes import _classify_model_error
    assert _classify_model_error("HTTPStatusError: 401 Unauthorized") == "bad_key"
    assert _classify_model_error("invalid api key provided") == "bad_key"
    assert _classify_model_error("404 Not Found for url") == "bad_url"
    assert _classify_model_error("ConnectError: connection refused") == "unreachable"
    assert _classify_model_error("ReadTimeout: timed out") == "unreachable"
    assert _classify_model_error("something exotic") == "unknown"


def test_detect_local_ollama_absent_is_graceful():
    """探测端点在无 Ollama 的机器上必须优雅 found=False(不 500 不慢);前端接线在位。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/providers/detect_local")
    assert r.status_code == 200
    body = r.json()
    assert body["found"] in (True, False) and isinstance(body["models"], list)
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "karvyloop" / "console"
           / "frontend" / "src" / "models_panel.ts").read_text(encoding="utf-8")
    assert "/api/providers/detect_local" in src and "onb.ollama_found" in src
    assert "error_class" in src   # 错误分类学真被前端消费


def test_task_cost_estimate_endpoint_and_card_wiring():
    """#42 成本预估:端点给分布(无账本=零值不崩);卡面接线在位(样本门 n>=3 在前端)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.llm.token_ledger import TokenLedger

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    z = client.get("/api/task_cost_estimate").json()
    assert z == {"n": 0, "mean": 0, "min": 0, "max": 0}     # 无账本诚实零值
    led = TokenLedger()
    for i, tok in enumerate((100, 200, 300)):
        led.record(source="route_to_role", model="m", input=tok, output=0, task_id=f"t{i}")
    app.state.token_ledger = led
    est = client.get("/api/task_cost_estimate").json()
    assert est["n"] == 3 and est["mean"] == 200 and est["min"] == 100 and est["max"] == 300
    import pathlib
    app_js = (pathlib.Path(__file__).resolve().parents[1] / "karvyloop" / "console"
              / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/task_cost_estimate" in app_js and "proposal.cost_estimate" in app_js
    assert "est.n >= 3" in app_js                            # 样本门:少于 3 不显示数字
