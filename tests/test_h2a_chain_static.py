"""test_h2a_chain_static — 决策卡同链合并·刀1 的前端静态契约(docs/92,grep 桩)。

锁三件(与 test_console_static.py 同风格:文件存在 + 字符串搜索,不引 JS 引擎):
① app.js 组折叠结构在:_regroupChains / 组壳 h2a-chain-group / 高风险 pin 区 chain-pin,
   且高风险判定走 data-high-risk(后端 silence.HIGH_RISK_KINDS 单一判定源)→ 永不进折叠体;
② i18n 三新键 en+zh 双表齐(组头 / 空理解保护句 / 高风险标),TS 源与构建产物都在
   (away bundle 引用 i18n.js → MANIFEST 一致性由 test_away_bundle.py 守);
③ 后端两个出口(WS broadcast / /api/proposals/pending)统一走 proposal_wire_payload
   (chain_intent + high_risk 派生字段,别一边有一边没有)。

红线回归锚:组只是 DOM 分组壳 —— _buildProposalCard/decide 拍板路径不经组壳
(每张卡仍独立 h2a_decision);_placeCard 多卡不覆盖那套照旧。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "karvyloop" / "console" / "static"
FRONTEND_SRC = ROOT / "karvyloop" / "console" / "frontend" / "src"

_CHAIN_KEYS = ["chain.group_head", "chain.group_protect", "chain.group_risk"]


def test_app_js_has_chain_group_structure():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    # 组折叠机制在(分组壳/组头/折叠体/重组入口)
    for token in ["_regroupChains", "h2a-chain-group", "chain-head", "chain-body",
                  "chain-protect", "data-chain-key"]:
        assert token in app_js, f"app.js 缺同链组折叠结构锚:{token}"
    # 组头 + 保护句走 i18n(链源意图直引,零 LLM)
    assert 't("chain.group_head"' in app_js
    assert 't("chain.group_protect"' in app_js


def test_app_js_high_risk_never_folded():
    """高风险不折:data-high-risk 的卡进 chain-pin(永远展开置顶),绝不收进折叠体。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "chain-pin" in app_js
    assert 'getAttribute("data-high-risk")' in app_js
    assert "risky ? pin : body" in app_js, "高风险分区逻辑变了 —— 确认高风险卡仍永不进折叠体"
    # 折叠开关只藏 chain-body(pin 区不受折叠影响)
    assert 'querySelector(".chain-body")' in app_js and "body.hidden = !open" in app_js


def test_app_js_does_not_break_existing_mechanisms():
    """组是收纳壳:卡渲染/拍板/多卡不覆盖/kind 筛选那套照旧(锚既有函数还在被用)。"""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "_buildProposalCard(payload)" in app_js       # 组内逐卡原样渲染复用同一套
    assert "function _placeCard" in app_js               # 多卡不覆盖不动
    assert "_regroupChains(list)" in app_js              # 渲染/拍板两处都重组
    # 计数不因折叠少报:组壳按组内卡数计
    assert 'querySelectorAll(".h2a-card").length' in app_js


def test_i18n_chain_keys_both_locales():
    """三新键 en+zh 双表齐:TS 源(编译期 parity)+ 构建产物(运行时真用的)。"""
    for f in (FRONTEND_SRC / "i18n.ts", STATIC / "i18n.js"):
        text = f.read_text(encoding="utf-8")
        for key in _CHAIN_KEYS:
            assert text.count(f'"{key}"') >= 2, f"{f.name} 键 {key} 不在 en+zh 双表(parity 断)"


def test_styles_have_chain_group():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for cls in [".h2a-chain-group", ".chain-head", ".chain-pin", ".chain-protect"]:
        assert cls in css, f"styles.css 缺 {cls}"


def test_backend_edges_share_wire_payload():
    """WS 广播与 pending API 同一出口口径(chain_intent/high_risk 不能一边有一边没有)。"""
    proposals_py = (ROOT / "karvyloop" / "console" / "proposals.py").read_text(encoding="utf-8")
    routes_py = (ROOT / "karvyloop" / "console" / "routes_system.py").read_text(encoding="utf-8")
    assert "def proposal_wire_payload" in proposals_py
    assert "proposal_wire_payload(registry, proposal)" in proposals_py   # broadcast 用
    assert "proposal_wire_payload(registry, p)" in routes_py             # pending API 用
