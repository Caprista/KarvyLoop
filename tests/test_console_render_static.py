"""test_console_render_static — 显示层前端静态验收(拍 9.4,node 无关).

dev-report #4 slice 1:render 已迁到 TypeScript(源 console/frontend/src/render.ts),markdown-it +
DOMPurify 由 npm 打包进**构建产物** static/render.js(不再 vendored 两个 .min.js);highlight.js
仍 vendored。内容锁查**源**(render.ts),存在/契约查**构建产物**(render.js)。

AC:
- AC1: 构建产物 render.js 存在且非空,且暴露 window.KarvyRender 契约
- AC2: TS 源 render.ts 存在(增量迁移的真相源)
- AC3: index.html 在 app.js 前加载 render.js(+ 仍 vendored 的 highlight)
- AC4: render.ts 源走 markdown-it + DOMPurify + sanitize + 暴露 renderEvents/appendMarkdown/renderMarkdown
- AC5: app.js 用 KarvyRender(appendMarkdown / renderEvents)渲染,不再裸 textContent 回显
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "karvyloop" / "console" / "static"
FRONTEND = ROOT / "karvyloop" / "console" / "frontend"


def _read(rel: str) -> str:
    return (STATIC / rel).read_text(encoding="utf-8")


# ---- AC1: 构建产物存在 + 契约 ----
def test_render_built_artifact_exists_and_exposes_contract():
    p = STATIC / "render.js"
    assert p.is_file() and p.stat().st_size > 0, "构建产物 static/render.js 缺失(跑 npm run build?)"
    built = _read("render.js")
    assert "window.KarvyRender" in built, "render.js 必须暴露 window.KarvyRender 全局契约"
    # 体量证明 md + DOMPurify 真打包进来了(裸 render 逻辑 ~6KB;打包后数百 KB)
    assert p.stat().st_size > 50_000, "render.js 太小,markdown-it/DOMPurify 可能没打包进来"


# ---- AC2: TS 源是真相源 ----
def test_render_ts_source_exists():
    src = FRONTEND / "src" / "render.ts"
    assert src.is_file() and src.stat().st_size > 0, "迁移真相源 frontend/src/render.ts 缺失"


# ---- AC3: 加载序(markdown-it/purify 已打包进 render.js,不再独立 vendored)----
def test_index_loads_render_before_app():
    html = _read("index.html")
    assert "render.js" in html
    # 旧的两个 vendor 脚本应已移除(改为打包进 render.js)
    assert "vendor/markdown-it.min.js" not in html and "vendor/purify.min.js" not in html
    a = html.find('src="/static/app.js"')
    assert html.find('src="/static/render.js"') < a, "render.js 必须在 app.js 前加载"


# ---- AC4: render.ts 源内容(类型化迁移,内容锁查源)----
def test_render_ts_uses_md_and_sanitize():
    ts = (FRONTEND / "src" / "render.ts").read_text(encoding="utf-8")
    assert "markdown-it" in ts                       # npm 包导入
    assert "DOMPurify" in ts and "sanitize" in ts     # 消毒(防 XSS)
    assert "html: false" in ts                        # markdown-it 关裸 HTML
    for fn in ("renderMarkdown", "appendMarkdown", "renderEvents"):
        assert fn in ts, f"render.ts 缺 {fn}"
    for t in ('"text"', '"tool_call"', '"tool_result"', '"terminal"'):
        assert t in ts, f"render.ts 未处理事件类型 {t}"


# ---- AC5: app.js 接入 ----
def test_app_js_uses_karvyrender():
    js = _read("app.js")
    assert "KarvyRender" in js
    assert "appendAgentTurn" in js          # agent 回合结构化渲染
    assert "renderEvents" in js             # 按类型渲染 events
    # pushChatLine 不再直接把 text 当裸子节点(改走 appendMarkdown)
    assert "KarvyRender.appendMarkdown" in js


# ---- ch4 圆桌前端接线(小卡主持:开桌/结论卡/讨论折叠/追问问主持)----
def test_roundtable_frontend_wired():
    html = _read("index.html")
    assert "roundtable-btn" in html                # 🎡 开圆桌按钮在 chat-head
    js = _read("app.js")
    for fn in ("openRoundtable", "renderRoundtable", "_toggleRoundtableBtn"):
        assert fn in js, f"app.js 缺 {fn}"
    assert "/api/roundtable/start" in js and "/api/roundtable/align" in js   # 两阶段:对齐(对话式)→ 自动讨论
    assert "roundtable-bar" in html and "/api/roundtable/align" in js          # 对齐横幅(无按钮)+ 对话式 align
    assert "rt-thread" in js and "rt-bubble" in js  # 群聊串:成员气泡 + 结论气泡
    assert "_renderConversationTurns" in js         # 重开时圆桌回合 → 群聊串渲染
    assert "is_group" in js                        # ch4:任何群场显隐(大群+域群)
    # ch4 #3:起桌弹窗 = 主题 + 勾选参与者(名册);走 roster 端点 + 传 participants
    assert "/api/roundtable/roster" in js
    assert "participants" in js and "rt-roster" in js
    css = _read("styles.css")
    assert ".rt-card" in css and ".rt-thread" in css and ".rt-bubble" in css  # 群聊串样式
    assert ".rt-roster" in css and ".rt-topic" in css   # 起桌弹窗样式
    # i18n:rt.* 文案 en+zh 都在(parity 另由 i18n 测锁)
    i18n = _read("i18n.js")
    for k in ("rt.open.title", "rt.opened", "rt.host_label", "rt.followup_hint",
              "rt.setup_title", "rt.topic_label", "rt.who_label", "rt.start",
              "rt.aligning", "rt.begin_discuss", "rt.discuss_started"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"


# ---- ch4 #1:群里 @ 角色(微信式 contenteditable 选择器)前端接线 ----
def test_mention_picker_frontend_wired():
    html = _read("index.html")
    assert "mention-pop" in html                      # @ 下拉
    assert 'contenteditable="true"' in html and "chat-input-ce" in html  # 输入框=contenteditable
    js = _read("app.js")
    for fn in ("_onChatInputMention", "_onChatInputKeydown", "_selectMention",
               "_loadGroupRoster", "_readChatInput", "_submitChat", "_ceClear"):
        assert fn in js, f"app.js 缺 {fn}"
    assert "mention-tag" in js                         # 行内高亮 chip(可整体删)
    assert "data-agent" in js                          # chip 上挂 agent_id,发送时读出
    assert "mention: mention" in js                    # 发送带 mention
    assert "/api/roundtable/roster" in js              # @ 名册来源(复用群名册)
    css = _read("styles.css")
    assert ".mention-pop" in css and ".mention-item" in css
    assert ".chat-input-ce" in css and ".mention-tag" in css   # 输入框 + 行内 chip 样式
    assert ".chat-input-ce.is-empty:before" in css     # contenteditable placeholder


# ---- ch4 #2:认知库沉淀工作流(喂料→分析→交流→你拍板)前端接线 ----
def test_distill_workflow_frontend_wired():
    js = _read("memory_panel.js")   # 知识库/沉淀面板已迁 TS → 构建产物 memory_panel.js
    for fn in ("_reloadDistill", "_renderDistillFeed", "_renderDistillPending", "_decideDistill"):
        assert fn in js, f"memory_panel.js 缺 {fn}"
    assert "/api/memory/feed" in js and "/api/memory/distill" in js and "/api/memory/distill/decide" in js
    assert "/api/memory/distill/chat" in js          # 沉淀前交流
    css = _read("styles.css")
    assert ".distill-summary" in css and ".distill-decide" in css
    i18n = _read("i18n.js")
    for k in ("distill.feed_btn", "distill.analyzing", "distill.pending_title",
              "distill.persist", "distill.reject", "distill.chat_send"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"


# ---- ch4 预判【你可能想做】:主动建议按 kind 分流(习惯预判 → 预判列)----
def test_predict_quadrant_wired():
    js = _read("app.js")
    for fn in ("_routeProposal", "renderPredict", "_clearPredict"):
        assert fn in js, f"app.js 缺 {fn}"
    assert "predict-list" in js                       # 渲进预判列
    assert "_DECISION_KINDS" in js                    # 真决策(派活/解冲突)走拍板,其余走预判
    assert "route_to_role" in js and "resolve_conflict" in js
    css = _read("styles.css")
    assert ".predict-card" in css and ".predict-yes" in css
    i18n = _read("i18n.js")
    assert i18n.count('"predict.do"') == 2 and i18n.count('"predict.ignore"') == 2


# ---- @ 多人回应(fanout)的重开渲染仍在(@多人新路由到 workflow,旧记录仍可重开)----
def test_mention_fanout_reopen_render():
    js = _read("app.js")
    assert "renderMentionReplies" in js                 # 旧 fanout 记录重开仍渲成群聊串
    assert "mention_fanout" in js                       # 重开时按 data.mention_fanout 渲
    assert "mentions.length >= 2" in js                 # 2 个及以上(现走 workflow)


# ---- ch4 workflow 模式(@多人→DAG→可编辑步骤表→执行)前端接线 ----
def test_workflow_frontend_wired():
    js = _read("app.js")
    for fn in ("_renderWorkflowPlan", "renderWorkflow"):
        assert fn in js, f"app.js 缺 {fn}"
    assert "/api/workflow/plan" in js and "/api/workflow/run" in js
    assert "mentions.length >= 2" in js                # @多人(≥2)走 workflow
    assert "data.workflow" in js or "tn.data.workflow" in js   # 重开渲染
    css = _read("styles.css")
    assert ".wf-steps" in css and ".wf-step-task" in css       # 可编辑步骤表
    i18n = _read("i18n.js")
    for k in ("wf.plan_title", "wf.goal_label", "wf.approve", "wf.running"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"


# ---- ch4 workflow 沉淀(复用提议 + 结晶提示)前端接线 ----
def test_workflow_distill_frontend_wired():
    js = _read("app.js")
    for fn in ("_workflowReplan", "_offerCrystallize"):
        assert fn in js, f"app.js 缺 {fn}"
    assert "/api/workflow/crystallize" in js
    assert "force_fresh" in js                          # 重新设计跳过匹配
    assert "crystallizable" in js                       # 跑稳才提议沉淀
    i18n = _read("i18n.js")
    for k in ("wf.matched", "wf.replan", "wf.crystallize_q", "wf.crystallize_yes"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"
