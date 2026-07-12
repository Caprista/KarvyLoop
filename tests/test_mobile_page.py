"""test_mobile_page — 📱 /m 手机拍板页(R1 切片一)静态+路由验收。

低地板纪律锁:一屏=待拍板卡+同意/稍后/拒绝,零生造名词(不出现 H2A/atom/结晶字样);
契约复用锁:只吃既有 GET /api/proposals/pending + POST /api/h2a_decide,零新后端端点。
"""
from __future__ import annotations

import pathlib
import re
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402

STATIC = ROOT / "karvyloop" / "console" / "static"


def _client():
    return TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))


def test_m_route_serves_page_with_lang_injection():
    r = _client().get("/m")
    assert r.status_code == 200
    assert 'data-default-lang="' in r.text          # 语言注入同 index
    assert "/static/m.js" in r.text and "/static/i18n.js" in r.text
    assert "viewport" in r.text                     # 手机可用的第一前提


def test_m_page_static_assets_exist_and_ordered():
    html = (STATIC / "m.html").read_text(encoding="utf-8")
    assert (STATIC / "m.js").is_file(), "m.js 构建产物缺失(node scripts/build.mjs)"
    assert html.find("i18n.js") < html.find("m.js"), "i18n.js 必须先于 m.js 加载"


def test_m_page_reuses_existing_contracts_only():
    js = (STATIC / "m.js").read_text(encoding="utf-8")
    assert "/api/proposals/pending" in js
    assert "/api/h2a_decide" in js
    assert "/api/intent" in js                       # 切片二:聊天条(发起入口)
    hit = re.findall(r'fetch\("(/api/[^"]+)"', js)
    assert set(hit) <= {"/api/proposals/pending", "/api/h2a_decide", "/api/intent"}, \
        f"手机页只许吃既有三契约,多了: {set(hit)}"


def test_m_page_low_floor_no_coined_nouns():
    """低地板锁([[avoid-ivory-tower]]):用户可见面不出现生造名词(概念留给 docs)。"""
    html = (STATIC / "m.html").read_text(encoding="utf-8")
    js = (STATIC / "m.js").read_text(encoding="utf-8")
    # i18n 键里的 m.* 用户可见串(en+zh 两表)也不许带这些词根
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    m_lines = "\n".join(ln for ln in i18n.splitlines() if '"m.' in ln)
    for banned in ("H2A", "atom", "Atom", "L0", "L4", "crystalli", "结晶", "原子"):
        assert banned not in html, f"m.html 出现生造名词: {banned}"
        assert banned not in m_lines, f"手机页 i18n 串出现生造名词: {banned}"
    assert "KarvyMobile" in js                       # 全局契约在


def test_m_decide_wires_to_h2a(monkeypatch):
    """从手机页的调用形状真打一发 /api/h2a_decide(未知 proposal_id → 结构化拒,不 500)。"""
    r = _client().post("/api/h2a_decide", json={"proposal_id": "nope-0-abc", "decision": "ACCEPT",
                                                "reason": ""})
    assert r.status_code in (200, 404, 409, 422)     # 依 registry 语义结构化返回,绝不崩


def test_m_page_chat_strip_present():
    """切片二:聊天条(发起入口)—— 输入条+发送键在,i18n 键接上,REST 一来回吃 /api/intent。"""
    html = (STATIC / "m.html").read_text(encoding="utf-8")
    assert 'id="m-chat-input"' in html and 'id="m-chat-send"' in html
    js = (STATIC / "m.js").read_text(encoding="utf-8")
    assert "m.chat_ph" in js and "m.chat_thinking" in js and "m.chat_failed" in js


def test_m_cards_diff_by_proposal_id():
    """P2 锁:刷新按 proposal_id diff,禁整列重建 —— 另一台设备拍掉卡时,你手指下的卡不挪位。"""
    js = (STATIC / "m.js").read_text(encoding="utf-8")
    assert "data-pid" in js
    assert 'innerHTML = ""' not in js, "整列重建回潮(误点窗口重开)"