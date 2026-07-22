"""test_h2a_drawer_static — 决策卡同链合并·刀2:积压 Top-N 抽屉限流的契约(docs/92)。

锁五件(前端 grep 桩与 test_h2a_chain_static.py 同风格;后端走真 TestClient):
① app.js 抽屉机制在:入列时刻溢出判定(只用 wire 已有字段 high_risk/payload.user_initiated,
   零每卡 API)+ 直出白名单 + 幂等重推保区 + 不做回填搬运;
② 计数语义:_countCards 总徽章含抽屉卡(反投降不少报);#6 筛选对抽屉内外一视同仁;
③ 抽屉卡不参与刀1组折叠(data-chain-key 改存 data-drawer-chain-key,_regroupChains 不受扰);
④ i18n 两新键 en+zh 双表齐(TS 源 + 构建产物;away bundle 一致性由 test_away_bundle.py 守);
⑤ 后端:OVERFLOW_DRAWER_N=7(Hardy 拍)且随 /api/proposals/pending 带出(boot 配置,
   不做每卡字段);fastlane user_initiated 标经 wire 出口透传(打标本身在
   test_private_mention_fastlane.py 锁)。

红线回归锚:刀1 组折叠(_regroupChains)逻辑一字不动;聊天流 inline 卡不经抽屉。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "karvyloop" / "console" / "static"
FRONTEND_SRC = ROOT / "karvyloop" / "console" / "frontend" / "src"

_DRAWER_KEYS = ["h2a.drawer_more", "h2a.drawer_open"]


# ---- ① 前端:抽屉结构 + 入列时刻溢出判定 ----

def test_app_js_has_drawer_structure():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    for token in ["h2a-drawer", "h2a-drawer-row", "h2a-drawer-body",
                  "_placeCardInDrawer", "_ensureDrawer", "_refreshDrawerRow",
                  "_visibleCardCount", "_drawerN"]:
        assert token in app_js, f"app.js 缺积压抽屉结构锚:{token}"
    # 抽屉行文案走 i18n(计数在行内,点开才展)
    assert '"h2a.drawer_more"' in app_js and '"h2a.drawer_open"' in app_js


def test_app_js_overflow_judged_at_entry_with_wire_fields_only():
    """溢出判定在入列时刻、只用 wire 已有字段:high_risk(silence.HIGH_RISK_KINDS 单一
    判定源,刀1 统一出口)+ payload.user_initiated(fastlane 显式来源标)→ 直出;
    违背/needs_recheck 懒加载才知道 → 明确不作判据(注释边界)。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function _isDirectOut" in app_js
    assert "payload.high_risk" in app_js
    assert "payload.user_initiated" in app_js
    # 判定发生在 renderProposal 入列分支(新卡且非直出且可视区已 ≥N → 抽屉)
    assert "_isDirectOut(payload)" in app_js
    assert "_visibleCardCount(list) >= _drawerN" in app_js
    # 边界注释:违背不作入抽屉判据(懒加载才可得,入列时刻拿不到)
    assert "不作入抽屉判据" in app_js


def test_app_js_idempotent_repush_keeps_zone_and_no_backfill():
    """幂等重推保区(同 id 换卡不换区);不做回填搬运(抽屉是滞留区不是队列)。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function _cardZone" in app_js
    assert "不做回填搬运" in app_js
    # boot 从 pending 响应校准 N(后端唯一配置源;第一版无 UI 设置项)
    assert "data.drawer_n" in app_js


# ---- ② 计数语义:总徽章含抽屉卡;#6 筛选一视同仁 ----

def test_count_cards_includes_drawer():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'ch.classList.contains("h2a-drawer")' in app_js, \
        "_countCards 必须按抽屉内卡数计(总徽章含抽屉卡,反投降不少报)"


def test_filter_treats_drawer_cards_same():
    """#6 筛选:数据源查询天然含抽屉卡(计数对得上);抽屉行跟随成员显隐(同组壳纪律)。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '.querySelectorAll(".h2a-card[data-kind]")' in app_js
    assert 'list.querySelector(".h2a-drawer")' in app_js
    assert "drawer.style.display = anyVisible" in app_js


# ---- ③ 抽屉卡不参与刀1组折叠(_regroupChains 不动)----

def test_drawer_cards_opt_out_of_chain_grouping():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "data-drawer-chain-key" in app_js, \
        "抽屉卡链键须改存 data-drawer-chain-key(否则 _regroupChains 组壳搬动会把卡拽出抽屉)"
    # 刀1 组折叠逻辑锚原样在(刀2 不许动它)
    assert "risky ? pin : body" in app_js
    assert 'querySelectorAll(".h2a-card[data-chain-key]")' in app_js


# ---- ④ i18n 两新键 en+zh 双表齐 ----

def test_i18n_drawer_keys_both_locales():
    for f in (FRONTEND_SRC / "i18n.ts", STATIC / "i18n.js"):
        text = f.read_text(encoding="utf-8")
        for key in _DRAWER_KEYS:
            assert text.count(f'"{key}"') >= 2, f"{f.name} 键 {key} 不在 en+zh 双表(parity 断)"


def test_styles_have_drawer():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for cls in [".h2a-drawer", ".h2a-drawer-row", ".h2a-drawer-body"]:
        assert cls in css, f"styles.css 缺 {cls}"


# ---- ⑤ 后端:N=7 配置源 + pending 带出 + wire 透传 ----

def test_backend_drawer_n_is_seven():
    from karvyloop.console.proposals import OVERFLOW_DRAWER_N
    assert OVERFLOW_DRAWER_N == 7   # Hardy 拍 N=7;改值需过他


def test_pending_api_carries_drawer_n():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    r = TestClient(app).get("/api/proposals/pending").json()
    assert r["drawer_n"] == 7
    # registry 未接的兜底分支同口径(前端 boot 校准不缺食)
    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r2 = TestClient(app2).get("/api/proposals/pending").json()
    assert r2["proposals"] == [] and r2["drawer_n"] == 7


def test_wire_payload_passes_user_initiated_through():
    """user_initiated 在 payload 里 → to_dict → proposal_wire_payload 原样带出(前端
    入列时刻可得);普通 route 卡不带此标(只有用户主动动作打标)。"""
    from karvyloop.console.proposals import proposal_wire_payload
    from karvyloop.karvy.proposal_registry import proposal_for_route

    plain = proposal_for_route(ts=1.0, requirement="出一版海报", domain_id="d1",
                               role="设计师", agent_id="设计师", domain_name="设计工作室")
    assert "user_initiated" not in plain.payload
    wire = proposal_wire_payload(None, plain)
    assert "user_initiated" not in wire["payload"]

    import dataclasses
    flagged = dataclasses.replace(plain, payload={**plain.payload, "user_initiated": True})
    assert flagged.proposal_id == plain.proposal_id   # replace 不重算 id(frozen 已派生)
    wire2 = proposal_wire_payload(None, flagged)
    assert wire2["payload"]["user_initiated"] is True
