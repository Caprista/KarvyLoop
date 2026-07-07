"""test_frontend_backend_wiring — #39 ②:接线契约门。治"后端造了功能没接 API/UI"那个老病。

钉死三条:
1. 前端调的 /api/* 端点,后端必须有(否则断头前端 → 点了没反应)。
2. 后端每个路由,要么被前端调,要么在 API_ONLY 白名单(显式声明"程序化/WS 备用面")—— 否则
   = 可能造了没接线,**强制你接 UI 或显式登记**。
3. 每个 nav data-panel 都有 JS 处理函数(没有 = 死按钮)。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "karvyloop" / "console" / "static"

# 显式登记的"非本前端调用"端点(WS 备用 REST / 程序化 / 历史)。加端点时:要么接 UI,要么往这里加一行
# 并写清原因 —— 这一步是**有意识的决定**,不是默默漏接。
API_ONLY = {
    "/api/h2a_decide",          # 拍板走 WS(h2a_decision);此 REST 是外部前端团队的契约面
    "/api/memory/ingest",       # 程序化写入;UI 用 /memory/feed
    "/api/roundtable/discuss",  # 历史端点;UI 走对话式 /align
    "/api/tokens/buckets",      # token 时段时间序列(可配粒度);UI 用 /api/tokens 的 by_hour,此面给外部前端/压测按需取
    "/api/atoms/consolidate/suggest",  # 原子语义合并·建议(§11.2);外部前端/管理面按需,核心逻辑已测
    "/api/atoms/consolidate/apply",    # 原子语义合并·兑现(经 H2A);同上
    "/api/decisions/audit",     # 决策审计流水查询(dev-report #6);程序化/审计面按需查,非 UI 按钮
    "/api/skill_lifecycle",     # 技能事件时间线(契约面先行;前端时间线视图由并行工人在接,接上后本行可删)
    "/api/memory/recall",       # 时点召回(as_of,docs/69 Q4):底座+API 先就绪;NL 意图入口("当时…")是 drive 侧后续接线,接上后本行可删
    # #54 逃生门 workflow 续/丢/查(pending_resume/resume/discard)已接前端 app.js 顶部横幅
    # (fetchPendingResume + 续跑/丢弃按钮,docs/56 ②)→ 不再是 API_ONLY,已从白名单移除。
}


def _backend_routes() -> set:
    # routes.py 被按领域拆成 routes_<domain>.py(P2-② god-module 拆分);端点分散在多个
    # routes*.py 文件里,这里全扫,否则"搬出 routes.py 的端点"会被误判成不存在(断头前端)。
    console_dir = ROOT / "karvyloop" / "console"
    out = set()
    for py in sorted(console_dir.glob("routes*.py")):
        txt = py.read_text(encoding="utf-8")
        for m in re.finditer(r'@router\.(get|post|websocket)\("([^"]+)"', txt):
            out.add("/api" + m.group(2))
    return out


def _frontend_calls() -> set:
    out = set()
    for p in STATIC.glob("*.js"):
        for m in re.finditer(r'/api/[a-zA-Z0-9_/]+', p.read_text(encoding="utf-8")):
            out.add(m.group(0).rstrip("/"))
    return out


def _static_prefix(route: str) -> str:
    return route.split("{", 1)[0].rstrip("/")


def _fe_matches_be(fe: str, be: str) -> bool:
    bestatic = _static_prefix(be)
    return fe == be or fe == bestatic or (("{" in be) and fe.startswith(bestatic))


def test_no_dead_frontend_calls():
    be = _backend_routes()
    bad = [fe for fe in _frontend_calls() if not any(_fe_matches_be(fe, b) for b in be)]
    assert not bad, f"前端调了后端不存在的端点(断头前端):{sorted(bad)}"


def test_no_unwired_backend_endpoints():
    be = _backend_routes()
    fe = _frontend_calls()
    orphans = []
    for b in be:
        if b in API_ONLY:
            continue
        if not any(_fe_matches_be(f, b) for f in fe):
            orphans.append(b)
    assert not orphans, ("后端端点没接前端、也没登记 API_ONLY(可能造了没接线):"
                         f"{sorted(orphans)} —— 接 UI,或显式加进 API_ONLY 并写原因")


def test_every_nav_panel_has_handler():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    appjs = (STATIC / "app.js").read_text(encoding="utf-8")
    panels = set(re.findall(r'data-panel="([a-z_]+)"', html))
    handled = set(re.findall(r'p === "([a-z_]+)"', appjs))
    dead = panels - handled
    assert not dead, f"导航按钮没有分派处理(死按钮):{sorted(dead)}"
