"""karvyloop/console/static 验收测试(M3+ 批 8.5-C-frontend)。

3 条 AC(对应 plans/snoopy-singing-sunbeam.md §拍 8.5-C-frontend):
- AC4: 3 个 static 文件存在(index.html / app.js / styles.css)。
- AC5: app.js 含 `connectWS` 字符串(保证 WS boot 函数 wired)。
- AC6: index.html 含 5 个 kanban 列 div(grep 验证)。

Q5 自检:不引 JS 引擎,纯文件存在 + 字符串搜索;不依赖网络。
"""
from __future__ import annotations

from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"


# ============ AC4: 3 个 static 文件存在 ============

@pytest.mark.parametrize("filename", ["index.html", "app.js", "styles.css"])
def test_static_file_exists(filename):
    """3 个核心 static 文件必须存在(给 FastAPI StaticFiles mount 用)。"""
    path = STATIC_DIR / filename
    assert path.is_file(), f"missing static file: {path}"
    assert path.stat().st_size > 0, f"empty static file: {path}"


# ============ AC5: app.js 含 connectWS ============

def test_app_js_has_connectws():
    """app.js 必须含 `connectWS` 字符串(WS boot 入口 wired)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "connectWS" in app_js, "app.js missing connectWS()"
    # 还应调一次(防止只定义不调)
    assert "connectWS()" in app_js, "app.js never calls connectWS()"


def test_app_js_uses_websocket_api():
    """app.js 走原生 WebSocket,符合 Q5(不引前端框架)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "new WebSocket" in app_js
    assert "WebSocket.OPEN" in app_js


# ============ AC6: index.html 含 5 个数据面板容器 ============
# 9.4c 暖色品牌系重构:5 列平铺看板收进右侧上下文栏,布局 class 变了,
# 但 app.js 靠这 5 个**容器 id** 填充——锁 id(布局可改,接线不可断)。

PANEL_IDS = [
    "domain-list",
    "broadcast-list",
    "skill-list",
    "last-drive",
    "h2a-list",
]


@pytest.mark.parametrize("panel_id", PANEL_IDS)
def test_index_html_has_data_panel(panel_id):
    """5 个数据面板容器 id 必须存在(app.js 靠它们填充;借 Q5 grep 验证)。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert f'id="{panel_id}"' in html, f"index.html missing data panel: {panel_id}"


def test_index_html_loads_app_js():
    """index.html 必须 <script src=app.js>,保证 boot wired。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "app.js" in html
    assert "<script" in html


def test_index_html_loads_styles_css():
    """index.html 必须 <link styles.css>。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "styles.css" in html
    assert "<link" in html


# ============ styles.css 兜底 ============

def test_styles_css_has_theme_vars():
    """styles.css 应含 --bg / --accent 等主题 CSS 变量(9.4c 暖色品牌系)。"""
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert "--bg" in css
    assert "--accent" in css
    assert "grid" in css.lower()  # 顶栏 + 三区布局用 CSS Grid


# ============ §0.7:决策 loop fail-loud + push(前端接线锁) ============
# 来源:Hardy OpenClaw 实例复盘 → 状态/失败必须是 push 事件,不靠 2s 轮询。
# 锁前端三件:WS 分派认这三类消息 + 处理函数在 + i18n 三键 en/zh 齐(parity)。

def test_app_js_dispatches_task_events():
    """app.js 的 server message handler 必须分派 task_status/task_step/system_error。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for typ in ("task_status", "task_step", "system_error"):
        assert f'msg.type === "{typ}"' in app_js, f"app.js 未分派 {typ}(push 事件断了)"
    for fn in ("onTaskStatus", "onTaskStep", "onSystemError"):
        assert f"function {fn}(" in app_js, f"app.js 缺 {fn} 处理函数"


def test_task_status_error_surfaces_notice():
    """收到 error 状态必须冒一条可见提示(失败看得见,不靠盯看板)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'tk.status === "error"' in app_js
    assert "task.failed_notice" in app_js


def test_i18n_has_task_event_keys_both_locales():
    """i18n 三新键 en+zh 都在(parity;新增用户可见串必走双表)。"""
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("task.failed_notice", "task.step_failed", "system.bg_error"):
        # 每个键应出现 >=2 次(en 表 + zh 表)
        assert i18n.count(f'"{key}"') >= 2, f"i18n 键 {key} 不在 en+zh 双表(parity 断)"


# ============ §11:决策偏好可编辑面(前端接线锁) ============

def test_app_js_has_decision_prefs_panel():
    """app.js 必须有 openDecisionPrefs + nav 派发 decision_prefs。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function openDecisionPrefs(" in app_js
    assert 'p === "decision_prefs"' in app_js
    # 三个操作都接了 /api/decision_prefs/op
    for op in ("delete", "confirm", "edit"):
        assert f'op: "{op}"' in app_js, f"app.js 缺 decision_prefs {op} 操作"


def test_index_html_has_decision_prefs_nav():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-panel="decision_prefs"' in html


def test_i18n_has_dpref_keys_both_locales():
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("nav.decision_prefs", "dpref.title", "dpref.confirm", "dpref.confirm_del"):
        assert i18n.count(f'"{key}"') >= 2, f"i18n 键 {key} 不在 en+zh 双表(parity)"


def test_app_js_decision_prefs_signal():
    """决策偏好面顶部必须有复利信号(MVP 可测读数)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "/api/decision_prefs/stats" in app_js
    assert "_dprefSignalText" in app_js


def test_workflow_edit_signal_wired():
    """§11 P2:workflow 编辑 diff → 决策信号(前端 diff + 送 edits)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function _workflowEdits(" in app_js
    assert "edits: edits" in app_js          # run 请求带上 edits
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("wf.edit_changed", "wf.edit_added"):
        assert i18n.count(f'"{key}"') >= 2, f"{key} 不在 en+zh 双表"


def test_brand_logo_wired():
    """IP 主视觉:header 用真 logo <img>(非占位符),favicon 也是 logo。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'class="brand-logo" src="/static/assets/karvyloop-logo.png"' in html
    assert "is-placeholder" not in html                  # 占位符已替换
    assert "karvyloop-logo-64.png" in html                 # favicon = logo
    assert (STATIC_DIR / "assets" / "karvyloop-logo.png").is_file()
    assert (STATIC_DIR / "assets" / "karvyloop-logo-64.png").is_file()


def test_roundtable_banner_group_gated_and_unclipped():
    """圆桌横幅:非群场不显示(与 🎡 同门)+ flex-shrink:0 不被裁切。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # _showRoundtableBanner 守 is_group(选中频道非群 → 不显示)
    import re
    m = re.search(r"function _showRoundtableBanner\([^)]*\)\s*\{(.{0,200})", app_js, re.S)
    assert m and "is_group" in m.group(1), "圆桌横幅未守 is_group(非群场会漏出圆桌功能)"
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    # roundtable-bar 有 flex-shrink:0(不被聊天区挤压裁切)
    rb = re.search(r"\.roundtable-bar\s*\{([^}]*)\}", css, re.S)
    assert rb and "flex-shrink: 0" in rb.group(1), "roundtable-bar 缺 flex-shrink:0(会被裁切)"


def test_roundtable_member_composite_key():
    """§2.6:圆桌名册 checkbox 用 (域::agent_id) 复合键 → 同名角色跨域可独立选中。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'm.domain_id || ""' in app_js and '"::" + m.agent_id' in app_js
    assert "value: key" in app_js   # checkbox value = 复合键(非裸 agent_id)


def test_domain_subdomain_and_archive_wired():
    """§2.5/§2.6:子域 parent 选择器 + 域 archive 按钮 + role 删引用守护(前端接线)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "parent_id: parentSel.value" in app_js          # 建域带 parent → 子域
    assert "/api/domain/archive" in app_js                 # 域归档按钮
    assert "res.data.blocked" in app_js                    # role 删引用守护二次确认
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("domain.parent_label", "domain.archive", "role.del_referenced"):
        assert i18n.count(f'"{key}"') >= 2, f"{key} 不在 en+zh 双表"


def test_domain_role_edit_wired():
    """P0 审计:域/角色编辑+恢复(此前建错只能删重建)接进 UI。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for ep in ("/api/domains", "/api/domain/update", "/api/domain/restore", "/api/role/update"):
        assert ep in app_js, f"app.js 没接 {ep}"
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("domain.restore", "domain.edit_value", "role.edit_identity"):
        assert i18n.count(f'"{key}"') >= 2, f"{key} 不在 en+zh 双表"


def test_workflow_dependency_editable():
    """P1:workflow 步骤可编辑依赖(改 depends_on → 建真 DAG)+ 删步骤清依赖引用。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "wf-step-deps" in app_js and "selectedOptions" in app_js   # 依赖多选可改
    assert "filter((dp) => dp !== delId)" in app_js                   # 删步骤清悬空依赖
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"wf.deps_label"') >= 2


def test_code_highlight_wired():
    """P4 渲染:highlight.js vendored + 接进 render.js(消毒后高亮代码块)。"""
    assert (STATIC_DIR / "vendor" / "highlight.min.js").is_file()
    assert (STATIC_DIR / "vendor" / "highlight-github.min.css").is_file()
    render = (STATIC_DIR / "render.js").read_text(encoding="utf-8")
    assert "hljs.highlightElement" in render and "pre code" in render
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "vendor/highlight.min.js" in html and "highlight-github.min.css" in html


def test_highlight_runs_after_sanitize_safe():
    """安全不变量:高亮在 innerHTML(已 DOMPurify 消毒)之后跑 —— 不绕过消毒。"""
    render = (STATIC_DIR / "render.js").read_text(encoding="utf-8")
    # _highlight 调用在 innerHTML 赋值之后
    i_inner = render.find("div.innerHTML = html")
    i_hl = render.find("_highlight(div);")
    assert 0 < i_inner < i_hl


def test_live_streaming_wired():
    """P4 逐字流式:前端 onDriveEvent 接 drive_event,终态清草稿。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'msg.type === "drive_event"' in app_js
    assert "function onDriveEvent(" in app_js and "_clearLiveStream()" in app_js
    assert "text_delta" in app_js   # 逐字追加


def test_thinking_fold_wired():
    """P4 thinking 折叠:render.js 渲染 thinking 为折叠卡 + 前端 thinking_delta 实时指示。"""
    render = (STATIC_DIR / "render.js").read_text(encoding="utf-8")
    assert 'ev.type === "thinking"' in render and "thinking-card" in render
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'ev.type === "thinking_delta"' in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"render.thinking"') >= 2


def test_skill_library_panel_wired():
    """Hardy 卡点:Skill 库面板 + 域/角色编辑改多行表单(不再单行 prompt)。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-panel="skills"' in html
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function openSkillsPanel(" in app_js and "/api/skills" in app_js
    assert "function _openDomainEdit(" in app_js and "function _openRoleEdit(" in app_js
    assert 'class: "edit-area"' in app_js          # 多行表单
    assert "prompt(t(\"domain.edit_value\")" not in app_js   # 单行 prompt 已替换
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("nav.skills", "skills.title", "mgmt.save"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 不在双表"


def test_role_skill_binding_wired():
    """Hardy:agent/role 编写时能直接引用 skill。角色表单有技能选择器 + POST 带 skill_ids。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function _skillPicker(" in app_js
    assert "skill_ids: Array.from(skillPick.picked)" in app_js   # create + edit 都带
    assert app_js.count("skill_ids: Array.from(skillPick.picked)") >= 2
    assert "mc-tag-skill" in app_js                              # 卡片显示随身技能
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("role.pick_skills", "role.skills_hint"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 不在双表"


def test_skill_import_ui_wired():
    """Hardy:能直接用第三方 skill 库。Skill 面板有导入入口 + 第三方徽章。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function _skillImportForm(" in app_js and "/api/skill/import" in app_js
    assert "third_party" in app_js          # 卡片标第三方来源
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.import_btn", "skills.third_party_badge"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_skill_sandbox_run_ui_wired():
    """P0-c:技能详情有沙箱试跑入口(第三方脚本关笼子里跑)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function _openSkillDetail(" in app_js and "/api/skill/run" in app_js
    assert "skill-run-out" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"skills.run_hint_untrusted"') >= 2


def test_skill_net_grant_ui_wired():
    """P1:第三方按需授网 —— 技能详情有授网勾选框 + 调 /api/skill/grant。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "/api/skill/grant" in app_js and "skill-net-grant" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"skills.grant_net"') >= 2


def test_skill_catalog_browse_ui_wired():
    """P1-b:技能库有目录浏览(官方+市场)+ 一键导。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function _skillCatalog(" in app_js and "/api/skill/catalog" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.cat_official", "skills.cat_market", "skills.catalog_import"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_skill_status_and_sources_ui_wired():
    """btw-1 状态徽章 + btw-2 检索源管理 接线。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "skills.status_" in app_js and "function _skillSourcesManager(" in app_js
    assert "/api/skill/sources" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.status_crystallized", "skills.src_manage", "skills.src_save"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_models_panel_wired():
    """Hardy:模型是全局配置,要有管理入口。导航 + 面板 + CRUD 端点接线。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-panel="models"' in html
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function openModelsPanel(" in app_js and "/api/model/save" in app_js
    assert "/api/model/set_default" in app_js and "/api/model/delete" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("nav.models", "models.f_key", "models.key_hint"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_no_key_setup_gate_wired():
    """无 Key 强制引导:前端 boot 查 setup_status,must_setup → 锁住的强制录入模态。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "checkSetupGate(" in app_js and "/api/setup_status" in app_js
    assert "openForcedSetup(" in app_js and "_setupLocked" in app_js
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("setup.title", "setup.add_model"):
        assert i18n.count(f'"{k}"') >= 2, k
