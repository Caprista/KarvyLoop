"""test_decision_timeline_static — 决策时间线 + Part A 界面灌输的静态契约(docs/85)。

Q5 纪律:不引 JS 引擎,纯文件字符串契约 —— 锁"接线在"(函数/入口/键),不锁样式细节。
"""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"
FRONTEND = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "frontend" / "src"


def _app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _i18n_ts() -> str:
    return (FRONTEND / "i18n.ts").read_text(encoding="utf-8")


# ---- 决策时间线(七站 + 双入口 + 回放)----


def test_app_js_has_decision_lifeline():
    js = _app_js()
    assert "async function openDecisionLifeline" in js
    assert "/api/decision/" in js and "/lifeline" in js
    # 七站齐(缺站显诚实空位靠这份站表)
    for st in ("born", "aligned", "judged", "decided", "dispatched", "executed", "result"):
        assert f'"{st}"' in js, f"缺站 {st}"
    assert "dlife.no_record" in js          # 诚实空位文案
    assert "_dlifeReplay" in js             # ▶ 逐站回放
    assert "prefers-reduced-motion" in js   # reduced-motion 全显


def test_app_js_recent_row_entry_clickable():
    """🗳 最近拍板行 = 主入口(有 proposal_id 才可点);聊天终态卡 = 🧬 副入口。"""
    js = _app_js()
    assert "recent-click" in js
    assert js.count("openDecisionLifeline(") >= 3   # 定义处 + 两个入口
    assert "dlife.replay_link" in js                # 聊天终态卡链接
    # 待决卡不放入口:_buildProposalCard 里不该出现时间线入口
    build = js.split("function _buildProposalCard")[1].split("function _renderProposalInChat")[0]
    assert "openDecisionLifeline" not in build


def test_styles_has_dlife_block():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for cls in (".dlife-station", ".dlife-empty-note", ".dlife-replaying",
                ".recent-row.recent-click", ".dcard-first-hint"):
        assert cls in css, f"styles.css 缺 {cls}"


# ---- i18n:dlife.* 键 en/zh parity(编译期断言之外的文件层复核)----

_DLIFE_KEYS = (
    "dlife.title", "dlife.hint", "dlife.stub_hint", "dlife.load_failed",
    "dlife.replay", "dlife.replay_link", "dlife.entry_title", "dlife.no_record",
    "dlife.st_born", "dlife.st_aligned", "dlife.st_judged", "dlife.st_decided",
    "dlife.st_dispatched", "dlife.st_executed", "dlife.st_result",
    "dlife.tokens", "dlife.auto", "dlife.result_running",
)


def test_i18n_dlife_keys_en_zh_parity():
    src = _i18n_ts()
    for k in _DLIFE_KEYS:
        assert src.count(f'"{k}"') == 2, f"{k} 应恰好 en+zh 各一份"
    # 构建产物同步(i18n.js 变更须重建;away-dist 基线也须重建)
    for built in (STATIC / "i18n.js", STATIC / "away-dist" / "i18n.js"):
        assert "dlife.st_born" in built.read_text(encoding="utf-8"), f"{built} 未重建"


# ---- Part A 界面灌输(文案落点)----


def test_part_a_tour_paradigm_lines():
    src = _i18n_ts()
    assert "你参与决策,不参与执行" in src               # tour s5 范式句(zh)
    assert "never in execution" in src                    # tour s5(en)
    assert "它自己换法子" in src                          # tour s4 问责链(zh)


def test_part_a_empty_and_leaks_cleaned():
    src = _i18n_ts()
    assert "要花钱、要对外、拿不准的" in src              # ⚖ 空态重写(zh)
    assert "暂无 PROPOSE" not in src                      # h2a.handled 泄漏已清
    assert "暂无 BROADCAST" not in src                    # empty.broadcast 泄漏已清
    assert "暂无 domain" not in src                       # empty.domain 泄漏已清
    assert '"dcard.first_hint"' in src                    # 首卡一次性提示(en+zh)
    assert '"proposal.strength.title"' in src             # strength 加 title 解释


def test_part_a_nav_tooltips_and_stats():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for key in ("nav.roles.title", "nav.atoms.title", "nav.domains.title",
                "nav.decision_prefs.title"):
        assert f'data-i18n-title="{key}"' in html, f"nav tooltip 缺 {key}"
    # 顶栏 stats 占位不再裸英文(drives:/fast-brain:/crystallized:)
    assert "drives: —" not in html and "fast-brain: —" not in html \
        and "crystallized: —" not in html
    assert 'data-i18n-title="stat.drives.title"' in html
    js = _app_js()
    assert 'localStorage.getItem("karvyloop_dcard_hint")' in js   # 一次性(localStorage)
