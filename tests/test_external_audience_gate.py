"""test_external_audience_gate — P0 安全:对外只读分享(x-karvy-audience: external)全局门。

病根(docs/73 §9.6 侦察实证):audience 门此前是**每端点手工挂**(deny-list),只有
routes_memory 两个裸 dump + routes_mesh 全部挂了,其余 /api 端点全裸奔 —— read-scope
分享方经 relay 隧道带 `x-karvy-audience: external` 就能 GET 决策画像 / 文件区 / 对话 /
决策审计 / 待办卡… 几乎整个只读 API,个人护城河数据全量外泄。

修法(app.py `_external_audience_gate`):翻成**默认拒(allow-list)**——external 请求
默认 403 拒所有 /api,只放行一份极小白名单(/api/memory/recall + GET /api/lang)。

AC:
- G1 默认拒:external 标下,一批敏感只读端点全部 403(不再 200 泄数据)。
- G2 白名单放行:external 标下 /api/memory/recall、GET /api/lang 不被此门拒(≠403)。
- G3 写方法双保险:external 标 + 白名单路径上的写方法(POST /api/lang)仍 403。
- G4 零回归:**不带** external 标(full-scope 自有设备 / 本机 / loopback / CLI / 测试)
  同端点一律不受影响(敏感端点 200、full-scope away 页端点 200)。
- G5 非 /api 公开面(/healthz、/、/m、/away)带 external 标也不被此门拦(本就公开)。
- G6 大小写/空白鲁棒:'External' / ' EXTERNAL ' 同样触发门。
- G7 召回刀零回归(集成):external 召回经门放行后,recall 自身 deny-by-default 仍生效。
- G8 纯函数真理来源:_is_external_audience / _external_audience_allowed 单测。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.console.app import _external_audience_allowed, _is_external_audience
from karvyloop.cognition.memory import MemoryManager
from karvyloop.karvy.observer import WorkbenchObserver

pytestmark = pytest.mark.security   # 安全套件:对外分享越权外泄对抗

EXT = {"x-karvy-audience": "external"}

# 侦察脚本(scratchpad/verify_p0_share.py)逮到的、修前全部 200 泄数据的敏感只读面。
SENSITIVE = [
    "/api/memory", "/api/memory/recent",
    "/api/decision_prefs", "/api/decision_prefs/stats",
    "/api/decisions/recent", "/api/decisions/audit",
    "/api/chat_history", "/api/conversations",
    "/api/files/list",
    "/api/proposals/pending", "/api/skills", "/api/roles",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))


# ---- G1 默认拒 ----

@pytest.mark.parametrize("path", SENSITIVE)
def test_external_denied_by_default_on_all_api(client, path):
    """external 标下,任意未白名单的 /api 只读面 → 403(默认拒,不再 200 泄数据)。"""
    r = client.get(path, headers=EXT)
    assert r.status_code == 403, f"{path} 在 external 标下应默认拒(403),实际 {r.status_code}"
    body = r.json()
    assert body.get("ok") is False and body.get("reason")


def test_external_denied_on_unknown_api_path(client):
    """哪怕是不存在的 /api 路径,external 也在**门**层就 403(默认拒,连路由都不到)。"""
    assert client.get("/api/definitely_not_a_route", headers=EXT).status_code == 403


# ---- G2 白名单放行 ----

def test_whitelist_recall_passes_the_gate(client):
    """/api/memory/recall(docs/73 §9.6 唯一合法外部面)在 external 标下不被此门拒。
    未接 memory 时端点自身回 {ok:false} 但 **200**(证明是被门放行、到了处理器,不是 403)。"""
    r = client.get("/api/memory/recall?q=x", headers=EXT)
    assert r.status_code != 403
    assert r.status_code == 200


def test_whitelist_lang_get_passes_the_gate(client):
    """GET /api/lang(零敏感 UI 语言)在 external 标下放行。"""
    r = client.get("/api/lang", headers=EXT)
    assert r.status_code == 200
    assert "lang" in r.json()


# ---- G3 写方法双保险 ----

def test_external_write_on_whitelisted_path_still_denied(client):
    """external + 白名单路径上的**写**方法(POST /api/lang 会写 config.yaml)→ 仍 403。
    白名单只放只读方法;写永不因路径命中而漏过。"""
    r = client.post("/api/lang", headers=EXT, json={"lang": "zh"})
    assert r.status_code == 403


def test_external_write_on_sensitive_path_denied(client):
    """external + 敏感写端点(POST /api/h2a_decide)→ 403(既非白名单又是写)。"""
    r = client.post("/api/h2a_decide", headers=EXT,
                    json={"proposal_id": "x", "decision": "ACCEPT", "reason": ""})
    assert r.status_code == 403


# ---- G4 零回归:不带 external 标一律不受影响 ----

@pytest.mark.parametrize("path", SENSITIVE)
def test_no_header_zero_regression(client, path):
    """**不带** external 标(full-scope / 本机 / loopback / CLI / 测试)→ 同端点照常 200。"""
    assert client.get(path).status_code == 200


def test_fullscope_away_page_endpoints_not_falsely_gated(client):
    """full-scope 的 away/m 页端点(GET /api/proposals/pending + POST /api/h2a_decide)
    在**无 external 标**时不被此门误伤 —— 自有设备远程访问零回归。"""
    assert client.get("/api/proposals/pending").status_code == 200
    r = client.post("/api/h2a_decide",
                    json={"proposal_id": "nope", "decision": "ACCEPT", "reason": ""})
    assert r.status_code != 403   # 门不拦;端点自身如何回执是另一回事


# ---- G5 非 /api 公开面不被拦 ----

@pytest.mark.parametrize("path", ["/healthz", "/", "/m", "/away"])
def test_external_non_api_surfaces_not_blocked(client, path):
    """external 标下,非 /api 的公开面(存活探针 / 首页 / 手机页 / 托管接入页)不被此门拦
    (它们本就公开、karvy.chat 也托管同款静态)。"""
    assert client.get(path, headers=EXT).status_code != 403


# ---- G6 大小写/空白鲁棒 ----

@pytest.mark.parametrize("val", ["external", "External", "EXTERNAL", "  external  "])
def test_header_value_case_and_space_robust(client, val):
    """external 标的值大小写/首尾空白无关 —— 归一后一致触发门(防大小写绕过)。"""
    assert client.get("/api/decision_prefs", headers={"x-karvy-audience": val}).status_code == 403


def test_non_external_audience_value_ignored(client):
    """非 external 的 audience 值(如伪造 'internal')不激活此门 → 走既有链路(敏感端点仍
    可达)。此门只认 external;别的门(access_gate / routes_* 细粒度)各管各的。"""
    assert client.get("/api/decision_prefs",
                      headers={"x-karvy-audience": "internal"}).status_code == 200


# ---- G7 召回刀零回归(集成:门放行 → recall 自身仍 deny-by-default)----

def test_external_recall_gate_passes_but_knife_still_denies():
    """门放行 /api/memory/recall 之后,recall 自身的 audience 刀仍生效:external + 无 role
    → deny-by-default,block 空;自有设备(无标)照常召回。证明门没削弱也没绕过召回过滤。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = MemoryManager()

    class TextDelta:  # 名字必须叫 TextDelta(流式收集器按 type name 认;同 test_console_memory_api)
        def __init__(self, t): self.text = t

    class _GW:
        async def complete(self, messages, tools, model_ref, *, system=None):
            yield TextDelta('[{"content":"用户叫 Hardy","kind":"fact"},'
                            '{"content":"偏好英文默认","kind":"preference"}]')

    app.state.runtime_kwargs = {"gateway": _GW(), "model_ref": "m"}
    c = TestClient(app)
    c.post("/api/memory/ingest", json={"material": "我叫 Hardy,偏好英文。"})
    ext = c.get("/api/memory/recall?q=Hardy", headers=EXT)
    assert ext.status_code == 200 and ext.json()["block"] == ""      # 对外:一条不漏
    own = c.get("/api/memory/recall?q=Hardy")
    assert own.status_code == 200 and "Hardy" in own.json()["block"]  # 自有:零回归


# ---- G8 纯函数真理来源 ----

def test_is_external_audience_pure():
    assert _is_external_audience({"x-karvy-audience": "external"}) is True
    assert _is_external_audience({"x-karvy-audience": " External "}) is True
    assert _is_external_audience({"x-karvy-audience": "internal"}) is False
    assert _is_external_audience({}) is False


def test_external_audience_allowed_pure():
    # 非 /api → 恒放行(公开面)
    assert _external_audience_allowed("GET", "/healthz") is True
    assert _external_audience_allowed("GET", "/away") is True
    # /api 白名单 + 只读方法 → 放行
    assert _external_audience_allowed("GET", "/api/memory/recall") is True
    assert _external_audience_allowed("HEAD", "/api/lang") is True
    # /api 白名单但写方法 → 拒
    assert _external_audience_allowed("POST", "/api/lang") is False
    # /api 非白名单 → 拒(即便只读)
    assert _external_audience_allowed("GET", "/api/decision_prefs") is False
    assert _external_audience_allowed("GET", "/api/files/list") is False
