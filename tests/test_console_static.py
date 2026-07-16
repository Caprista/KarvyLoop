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
# 已迁 TS(dev-report #4):内容真相源在 frontend/src/*.ts(static/*.js 是构建产物)。
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "frontend" / "src"
RENDER_TS = FRONTEND_SRC / "render.ts"
ROLES_TS = FRONTEND_SRC / "roles_panel.ts"   # 🎭 角色面板已迁 TS(含 _skillPicker/_openRoleEdit/角色编辑)
DOMAINS_TS = FRONTEND_SRC / "domains_panel.ts"  # 🏢 业务域面板已迁 TS(含 _openDomainEdit/建域/归档/子域)
SKILLS_TS = FRONTEND_SRC / "skills_panel.ts"    # 🧩 技能库面板已迁 TS(导入/目录/检索源/Coding卡/详情沙箱)
MODELS_TS = FRONTEND_SRC / "models_panel.ts"    # 🤖 模型面板已迁 TS(CRUD/搜索配置/onboarding/强制引导)
DPREF_TS = FRONTEND_SRC / "decision_prefs_panel.ts"  # 🗳 决策偏好面已迁 TS(§11 复利信号 + 确认/编辑/撤回)


@pytest.mark.parametrize("panel,glob,old_fn", [
    ("files", "KarvyFilesPanel", "function renderFilesPanel"),
    ("schedules", "KarvySchedulesPanel", "function renderSchedulesPanel"),
    # 诊断面板走 open(deps)(pushChatLine/fetchPendingProposals 还在 app.js,经注入)
    ("diagnose", "KarvyDiagnosePanel", "function renderDiagnosePanel"),
    ("atoms", "KarvyAtomsPanel", "function renderAtomsPanel"),
    # 角色面板留薄 openRolesPanel() wrapper(业务域「新建角色」链接也调它);旧 render 已搬走
    ("roles", "KarvyRolesPanel", "function renderRolesPanel"),
    # 业务域面板留薄 openDomainsPanel() wrapper(注入 refreshPeers/pushChatLine/openPeerChat);旧 render 已搬走
    ("domains", "KarvyDomainsPanel", "function renderDomainsPanel"),
    # Agent 导入面板(open 即渲染,无单独 render);nav 派发直调 window.KarvyAgentsPanel.open({refreshPeers})
    ("agents", "KarvyAgentsPanel", "function openAgentsPanel"),
    # 技能库面板(整簇:导入/目录/检索源/Coding能力卡/详情);自洽,nav 直调 window.KarvySkillsPanel.open()
    ("skills", "KarvySkillsPanel", "function renderSkillsPanel"),
    # 知识库/认知面板(沉淀工作流/认知图谱/已知列表);自洽,nav 直调 window.KarvyMemoryPanel.open()
    ("memory", "KarvyMemoryPanel", "function renderMemoryPanel"),
    # 模型面板(含 onboarding + 强制引导);nav 直调 window.KarvyModelsPanel.open();boot 走 checkSetupGate(deps)
    ("models", "KarvyModelsPanel", "function renderModelsPanel"),
    # 决策偏好面(§11);nav 直调 window.KarvyDecisionPrefs.open()
    ("decision_prefs", "KarvyDecisionPrefs", "function renderDecisionPrefs"),
    # 💰 token 成本表;顶栏 pollMeter + 点开 window.KarvyTokens.open();旧 openTokenModal 已搬走
    ("tokens", "KarvyTokens", "function openTokenModal"),
])
def test_panel_migrated_to_ts_module(panel, glob, old_fn):
    """功能面板已从 app.js 抽成 TS 模块(大尾巴):app.js 走 window.<Glob>.open();
    构建产物 + TS 源 + 加载序都在;旧实现不再留在 app.js。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert f"window.{glob}.open" in app_js                    # nav 派发改走全局
    assert old_fn not in app_js                               # 旧实现已搬走
    assert (FRONTEND_SRC / f"{panel}_panel.ts").is_file()     # TS 真相源
    built = (STATIC_DIR / f"{panel}_panel.js")
    assert built.is_file() and f"window.{glob}" in built.read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    a = html.find('src="/static/app.js"')
    assert 0 < html.find(f'src="/static/{panel}_panel.js"') < a   # 在 app.js 前加载


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
# 来源:Hardy 实例复盘 → 状态/失败必须是 push 事件,不靠 2s 轮询。
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
    dpref_ts = DPREF_TS.read_text(encoding="utf-8")   # 决策偏好面已迁 TS
    assert "window.KarvyDecisionPrefs.open()" in app_js   # nav 派发改走全局
    assert 'p === "decision_prefs"' in app_js
    # 面板三个动作都接了 /api/decision_prefs/op(撤回=第一类动作,替代原裸 delete 按钮;
    # delete op 后端仍在但 UI 用 revoke,留审计+墓碑)。
    for op in ("revoke", "confirm", "edit"):
        assert f'op: "{op}"' in dpref_ts, f"decision_prefs_panel.ts 缺 {op} 操作"


def test_index_html_has_decision_prefs_nav():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-panel="decision_prefs"' in html


def test_i18n_has_dpref_keys_both_locales():
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("nav.decision_prefs", "dpref.title", "dpref.confirm", "dpref.confirm_del"):
        assert i18n.count(f'"{key}"') >= 2, f"i18n 键 {key} 不在 en+zh 双表(parity)"


def test_app_js_decision_prefs_signal():
    """决策偏好面顶部必须有复利信号(MVP 可测读数)。已迁 decision_prefs_panel.ts。"""
    dpref_ts = DPREF_TS.read_text(encoding="utf-8")
    assert "/api/decision_prefs/stats" in dpref_ts
    assert "_dprefSignalText" in dpref_ts


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
    domains_ts = DOMAINS_TS.read_text(encoding="utf-8")    # 业务域面板已迁 TS
    assert "parent_id: parentSel.value" in domains_ts      # 建域带 parent → 子域
    assert "/api/domain/archive" in domains_ts             # 域归档按钮
    assert "res.data.blocked" in ROLES_TS.read_text(encoding="utf-8")   # role 删引用守护二次确认(已迁 roles_panel.ts)
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("domain.parent_label", "domain.archive", "role.del_referenced"):
        assert i18n.count(f'"{key}"') >= 2, f"{key} 不在 en+zh 双表"


def test_domain_role_edit_wired():
    """P0 审计:域/角色编辑+恢复(此前建错只能删重建)接进 UI。"""
    domains_ts = DOMAINS_TS.read_text(encoding="utf-8")
    for ep in ("/api/domains", "/api/domain/update", "/api/domain/restore"):
        assert ep in domains_ts, f"domains_panel.ts 没接 {ep}"   # 业务域编辑已迁 TS
    # 角色编辑/恢复已迁 roles_panel.ts(整簇抽出)
    assert "/api/role/update" in ROLES_TS.read_text(encoding="utf-8")
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("domain.restore", "domain.edit_value", "role.edit_identity"):
        assert i18n.count(f'"{key}"') >= 2, f"{key} 不在 en+zh 双表"


def test_workflow_dependency_editable():
    """workflow 步骤依赖 = **单击切换的 chip**(替原生 multi-select 的 ctrl+click;Hardy 报"改不动")。
    点 chip → 切 depends_on 建真 DAG;删步骤清悬空依赖引用;有流程图例。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "wf-dep-chip" in app_js                                    # 单击切换的依赖 chip(非 multi-select)
    assert "selectedOptions" not in app_js                           # 原生 multi-select 已弃(ctrl+click 地狱)
    assert "s.depends_on = set" in app_js                            # chip 点击 → 切换 depends_on
    assert "filter((dp) => dp !== delId)" in app_js                   # 删步骤清悬空依赖
    assert "wf-flow-legend" in app_js                                # 一句话流程图例(怎么读)
    # 🎨 全屏拖拽画布(Drawflow)入口:复杂 DAG 用画布拖/连(Hardy:模态里编依赖是灾难)
    assert "wf-edit-canvas" in app_js and "KarvyWorkflowCanvas" in app_js
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Drawflow 包(70KB)不在 index.html 常驻 → 点「编辑画布」时 app.js 按需注入(降页面加载重量)
    assert "workflow_canvas.js" not in html                          # 已从常驻脚本移除
    assert "_ensureWorkflowCanvas" in app_js                         # 按需加载器
    assert 'createElement("script")' in app_js and "/static/workflow_canvas.js" in app_js
    # 弱机瘦身:drawflow CSS 与其 JS 同点懒注入(修"JS 已懒其 CSS 却首屏"的错配)
    assert "vendor/drawflow.min.css" not in html                      # 不再首屏常驻
    assert "drawflow-css" in app_js                                   # _ensureWorkflowCanvas 注入(防重复 id)
    assert (STATIC_DIR / "vendor" / "drawflow.min.css").is_file()     # MIT,vendored
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("wf.deps_label", "wf.flow_legend", "wf.dep_parallel", "wf.edit_canvas"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_workflow_canvas_wheel_zoom():
    """画布滚轮缩放(Hardy:节点一多必须能缩放)。Drawflow 原生只认 ctrl+滚轮 → 加裸滚轮缩放 + ＋/⟲/－ 按钮。
    交互视觉 Hardy 肉眼验;这里锁"功能已发"(源 + 构建产物)。"""
    wf_ts = (FRONTEND_SRC / "workflow_canvas.ts").read_text(encoding="utf-8")
    assert 'addEventListener("wheel"' in wf_ts                 # 裸滚轮缩放
    assert "e.ctrlKey" in wf_ts and "zoom_out()" in wf_ts and "zoom_in()" in wf_ts  # ctrl 交给原生,不重复
    assert "wf-cv-zoom" in wf_ts and "zoom_reset()" in wf_ts   # ＋/⟲/－ 按钮
    built = (STATIC_DIR / "workflow_canvas.js").read_text(encoding="utf-8")
    assert 'addEventListener("wheel"' in built and "wf-cv-zoom" in built   # 构建产物里也在


def test_code_highlight_wired():
    """P4 渲染:highlight.js vendored + 接进 render(消毒后高亮代码块)。内容查 TS 源。"""
    assert (STATIC_DIR / "vendor" / "highlight.min.js").is_file()
    assert (STATIC_DIR / "vendor" / "highlight-github.min.css").is_file()
    render = RENDER_TS.read_text(encoding="utf-8")
    assert "highlightElement" in render and "pre code" in render
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "vendor/highlight.min.js" in html and "highlight-github.min.css" in html


def test_highlight_runs_after_sanitize_safe():
    """安全不变量:高亮在 innerHTML(已 DOMPurify 消毒)之后跑 —— 不绕过消毒。内容查 TS 源。"""
    render = RENDER_TS.read_text(encoding="utf-8")
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
    render = RENDER_TS.read_text(encoding="utf-8")
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
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")   # 技能库面板已迁 TS
    assert "function open(" in skills_ts and "/api/skills" in skills_ts
    assert "window.KarvySkillsPanel.open()" in app_js   # nav 派发改走全局
    domains_ts = DOMAINS_TS.read_text(encoding="utf-8")
    assert "function _openDomainEdit(" in domains_ts   # 业务域编辑已迁 TS
    assert "function _openRoleEdit(" in ROLES_TS.read_text(encoding="utf-8")  # 角色编辑已迁 TS
    assert 'class: "edit-area"' in domains_ts          # 多行表单(域编辑)
    assert "prompt(t(\"domain.edit_value\")" not in app_js   # 单行 prompt 已替换(全仓无)
    assert "prompt(t(\"domain.edit_value\")" not in domains_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("nav.skills", "skills.title", "mgmt.save"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 不在双表"


def test_role_skill_binding_wired():
    """Hardy:agent/role 编写时能直接引用 skill。角色表单有技能选择器(Hardy 大改:chip → 穿梭框)+ POST 带 skill_ids。"""
    roles_ts = ROLES_TS.read_text(encoding="utf-8")   # 角色表单/技能选择器已迁 TS
    # chip 气泡换成穿梭框(transferList);技能选择用 skillTL.getSelected()(create + edit 都带)
    assert "transferList(" in roles_ts
    assert "skill_ids: skillTL.getSelected()" in roles_ts
    assert roles_ts.count("skillTL.getSelected()") >= 2           # create + edit 各一处
    assert "mc-tag-skill" in roles_ts                              # 卡片显示随身技能
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("role.pick_skills", "role.skills_hint"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 不在双表"


def test_role_full_paradigm_editor_wired():
    """Hardy:角色编辑要能看+改**完整七层范式**(不再只 identity)。编辑器调 /api/role/paradigm(读)
    + /api/role/paradigm/update(逐槽改)+ 5 个可编辑灵魂槽 + MEMORY 只读 + atom/skill 走穿梭框。"""
    roles_ts = ROLES_TS.read_text(encoding="utf-8")
    assert "/api/role/paradigm?role_id=" in roles_ts          # 读全范式
    assert "/api/role/paradigm/update" in roles_ts            # 逐槽改
    for slot in ("IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY"):
        assert slot in roles_ts, f"soul 编辑器缺 {slot} 槽"
    assert "soul-ro" in roles_ts                              # MEMORY 只读展示
    assert "atom_ids: atomTL.getSelected()" in roles_ts       # atoms 走穿梭框(编辑能改 atoms)


def test_role_atom_panels_create_list_split_and_search():
    """Hardy:atom/role 多了以后同页列表+chip 交互差 → 创建/列表分离 + 搜索分页(pagedList)+ 穿梭框。"""
    widgets = (FRONTEND_SRC / "ui_widgets.ts").read_text(encoding="utf-8")
    assert "function transferList" in widgets and "function pagedList" in widgets
    assert "KarvyWidgets" in widgets
    for ts in (ROLES_TS, FRONTEND_SRC / "atoms_panel.ts"):
        src = ts.read_text(encoding="utf-8")
        assert "mgmt-new-btn" in src                          # 「＋新建」= 创建/列表分离
        assert "pagedList(" in src                            # 搜索 + 分页
    built = (STATIC_DIR / "ui_widgets.js")
    assert built.is_file() and "KarvyWidgets" in built.read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    a = html.find('src="/static/app.js"')
    assert 0 < html.find('src="/static/ui_widgets.js"') < a   # widgets 在 app.js/面板前加载


def test_atom_consolidate_ui_wired():
    """审计缺口:原子面板缺「整理相似原子」按钮(memory 侧同款早已接线,原子侧漏了)。
    镜像 memory_panel 的 consolidate UX 到 atoms_panel:工具栏按钮 + suggest→渲染簇→拍板→apply,
    payload/response 对齐 routes_atoms.py(canonical_id/member_ids/merged_purpose/merged_tools;removed_atoms)。"""
    atoms_ts = (FRONTEND_SRC / "atoms_panel.ts").read_text(encoding="utf-8")
    assert "atom-consolidate-btn" in atoms_ts               # 工具栏「整理相似原子」按钮
    assert "function _runConsolidate(" in atoms_ts          # suggest→渲染→apply 流程
    assert "/api/atoms/consolidate/suggest" in atoms_ts     # dry-run 建议
    assert "/api/atoms/consolidate/apply" in atoms_ts       # 人拍板后兑现
    assert "canonical_id: c.canonical_id" in atoms_ts       # apply payload 对齐 AtomMergeRequest
    assert "member_ids: c.member_ids" in atoms_ts
    assert "removed_atoms" in atoms_ts                      # apply 返回形态(合并了几个)
    # 构建产物里也在(不是只改了源)
    built = (STATIC_DIR / "atoms_panel.js").read_text(encoding="utf-8")
    assert "/api/atoms/consolidate/suggest" in built and "/api/atoms/consolidate/apply" in built
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("atom.consolidate_btn", "atom.consolidating", "atom.consolidate_none",
              "atom.consolidate_into", "atom.consolidate_do", "atom.consolidate_done"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 不在 en+zh 双表(parity 断)"


def test_skill_import_ui_wired():
    """Hardy:能直接用第三方 skill 库。Skill 面板有导入入口 + 第三方徽章。"""
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")
    assert "function _skillImportForm(" in skills_ts and "/api/skill/import" in skills_ts
    assert "third_party" in skills_ts          # 卡片标第三方来源
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.import_btn", "skills.third_party_badge"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_skill_sandbox_run_ui_wired():
    """P0-c:技能详情有沙箱试跑入口(第三方脚本关笼子里跑)。"""
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")
    assert "function _openSkillDetail(" in skills_ts and "/api/skill/run" in skills_ts
    assert "skill-run-out" in skills_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"skills.run_hint_untrusted"') >= 2


def test_skill_net_grant_ui_wired():
    """P1:第三方按需授网 —— 技能详情有授网勾选框 + 调 /api/skill/grant。"""
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")
    assert "/api/skill/grant" in skills_ts and "skill-net-grant" in skills_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"skills.grant_net"') >= 2


def test_skill_catalog_browse_ui_wired():
    """P1-b:技能库有目录浏览(官方+市场)+ 一键导。"""
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")
    assert "function _skillCatalog(" in skills_ts and "/api/skill/catalog" in skills_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.cat_official", "skills.cat_market", "skills.catalog_import"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_skill_status_and_sources_ui_wired():
    """btw-1 状态徽章 + btw-2 检索源管理 接线。"""
    skills_ts = SKILLS_TS.read_text(encoding="utf-8")
    assert "skills.status_" in skills_ts and "function _skillSourcesManager(" in skills_ts
    assert "/api/skill/sources" in skills_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("skills.status_crystallized", "skills.src_manage", "skills.src_save"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_models_panel_wired():
    """Hardy:模型是全局配置,要有管理入口。导航 + 面板 + CRUD 端点接线。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-panel="models"' in html
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    models_ts = MODELS_TS.read_text(encoding="utf-8")   # 模型面板已迁 TS
    assert "window.KarvyModelsPanel.open()" in app_js   # nav 派发改走全局
    assert "/api/model/save" in models_ts and "/api/model/set_default" in models_ts and "/api/model/delete" in models_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("nav.models", "models.f_key", "models.key_hint"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_no_key_setup_gate_wired():
    """无 Key 强制引导:前端 boot 查 setup_status,must_setup → 锁住的强制录入模态(已迁 models_panel.ts)。"""
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    models_ts = MODELS_TS.read_text(encoding="utf-8")
    # boot 仍调 checkSetupGate(注入 pollSnapshot),实现已迁 TS
    assert "checkSetupGate(" in app_js                   # boot 调用(经 window.KarvyModelsPanel)
    assert "/api/setup_status" in models_ts
    assert "function openForcedSetup(" in models_ts and "setSetupLocked(" in models_ts
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for k in ("setup.title", "setup.add_model"):
        assert i18n.count(f'"{k}"') >= 2, k


def test_update_flow_failloud_and_version_visible():
    """升级流 fail-loud + 常显版本(Hardy 反馈:一键升级失败却只剩模糊横幅,人被晾在'升没升成'):
    ① 顶栏常显当前运行版本(brand-version,靠 update_status.current 填);
    ② 上次升级**任何失败**(不止 rolled_back)且仍没到目标版本 → fail-loud 红条 + 可重试;
    ③ current==to 时抑制(其实已到目标,别陈年误报);④ 失败文案中英双语齐(parity)。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    # ① 常显版本锚点 + 填充函数
    assert 'id="brand-version"' in html, "顶栏要有常显版本锚点"
    assert "_setBrandVersion(" in app_js, "要有填当前版本的函数"
    assert "_setBrandVersion(u.current)" in app_js, "版本必须来自 update_status.current(运行态真源)"
    # ② 广义失败(ok===false)判定 + ③ current!=to 抑制误报
    assert "lastFailed" in app_js and "lu.ok === false" in app_js, "失败判定不能只认 rolled_back"
    assert "String(u.current) !== String(lu.to)" in app_js, "current==to 要抑制(已到目标不误报)"
    assert "update.last_failed" in app_js and "update.retry_btn" in app_js, "失败态要有专属文案+重试按钮"
    # ④ 新增文案中英双语齐(parity 锁)
    for k in ("update.last_failed", "update.retry_btn", "update.version_title"):
        assert i18n.count(f'"{k}"') >= 2, f"{k} 必须中英双语齐"
