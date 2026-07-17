"""test_console_render_static — 显示层前端静态验收(拍 9.4,node 无关).

dev-report #4 slice 1:render 已迁到 TypeScript(源 console/frontend/src/render.ts),markdown-it +
DOMPurify 由 npm 打包进**构建产物** static/render.js(不再 vendored 两个 .min.js);highlight.js
仍 vendored,但 T5(docs/83)后不再首屏常驻 —— render.ts 首次遇到代码块才注入(_ensureHighlight)。
内容锁查**源**(render.ts),存在/契约查**构建产物**(render.js)。

AC:
- AC1: 构建产物 render.js 存在且非空,且暴露 window.KarvyRender 契约
- AC2: TS 源 render.ts 存在(增量迁移的真相源)
- AC3: index.html 在 app.js 前加载 render.js(highlight 懒加载另锁于 test_console_static)
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


# ---- 渲染层三件(dev-report):稳定锚点配对 / 编辑 diff / 断线恢复 ----

def test_render_ts_pairs_tools_by_stable_anchor():
    """工具轨迹稳定锚点:tool_call.id ↔ tool_result.tool_use_id 显式配对归组,
    不靠数组顺序(chat_history 重建/分页/流式补齐后顺序可扰动)。"""
    ts = (FRONTEND / "src" / "render.ts").read_text(encoding="utf-8")
    # RenderEvent 形状带配对锚点字段
    assert "tool_use_id" in ts and "id?" in ts, "render.ts 未在事件形状里带稳定锚点 id/tool_use_id"
    # 有按 id 建索引的分组函数,且把锚点落进 DOM(data-tool-id)供重建/测试
    assert "_renderProcessGrouped" in ts, "render.ts 缺按稳定锚点分组的过程渲染函数"
    assert "data-tool-id" in ts, "配对锚点应落进 DOM(data-tool-id)以可重建"
    assert "resById" in ts, "应按 tool_use_id 建 result 索引做稳定配对"


def test_render_ts_edit_diff_from_tool_call_input():
    """编辑类工具 diff:edit_file 的 tool_call.input(old_string/new_string,前端已有)→
    渲增删行 diff;纯 textContent 着色(XSS 天然剥);write_file 无'改前'不硬造 diff。"""
    ts = (FRONTEND / "src" / "render.ts").read_text(encoding="utf-8")
    assert "_lineDiff" in ts and "_renderDiff" in ts, "render.ts 缺 diff 计算/渲染函数"
    assert "_editDiffSignal" in ts, "render.ts 缺编辑信号抽取(edit_file old_string→new_string)"
    assert "old_string" in ts and "new_string" in ts, "diff 信号应取自 edit_file 的 old/new_string"
    # 着色类名前缀(add/del/ctx 由 op 分支拼 "diff-"+... → 断言前缀 + 三态)
    assert "diff-" in ts, "diff 行缺着色类前缀 diff-"
    assert '"add"' in ts and '"del"' in ts and '"ctx"' in ts, "diff 应有增/删/上下文三态"
    # 安全:diff 行走 textContent(不 innerHTML),文件内容里的 HTML/script 不执行
    assert "textContent" in ts, "diff 行必须走 textContent(XSS 剥)"


def test_render_diff_css_present():
    """diff 视图 + 稳定锚点分组的样式落在 styles.css。"""
    css = _read("styles.css")
    assert ".tool-group" in css, "缺 .tool-group(配对单元)样式"
    assert ".tool-diff" in css and ".diff-add" in css and ".diff-del" in css, "缺 diff 着色样式"


def test_app_js_refetches_chat_history_on_reconnect():
    """断线恢复:WS 重连时补拉 chat_history —— 断线窗口里 drive 在服务端跑完、回合已落
    持久历史(带 events),但那条 drive_done 广播给的是当时在线的 socket,断开的错过了。
    renderChatHistory 从权威历史整段幂等重建 → 把断线期间跑完的回合补回来(灭断线死角)。
    重连才补(首连启动已拉过);逐字草稿是装饰,丢了以终态为准,不需重放增量。"""
    js = _read("app.js")
    assert "_wsEverConnected" in js, "app.js 缺重连判别(首连 vs 重连)"
    # onopen 重连分支里补拉 chat_history
    assert "if (_wsEverConnected) { pollChatHistory(); }" in js, \
        "app.js 未在 WS 重连(onopen)时补拉 chat_history 做断线恢复"


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
    # P1-b:改成"预判白名单 + 默认进决策列"(新 kind 不再被误丢进预判)
    assert "_PREDICT_KINDS" in js
    # 2026-07-04:决策列调用带 opts 透传(replay=开机回放存量卡,桌面剧场只回应真事件)
    assert "renderPredict(payload)" in js and "renderProposal(payload, opts)" in js
    assert "replay: true" in js, "boot fetch 回放必须标注 replay(开屏叼卡剧场回归锁)"
    css = _read("styles.css")
    assert ".predict-card" in css and ".predict-yes" in css
    i18n = _read("i18n.js")
    assert i18n.count('"predict.do"') == 2 and i18n.count('"predict.ignore"') == 2


# ---- P1-b 接线契约:H2A kind → 渲染列(fail-safe 默认进【拍板】,新 kind 不漏)----
def test_h2a_kind_routing_contract():
    """把前端 kind 分流与后端 ALL_KINDS 锁在一起:除了明确的"习惯预判"kind,
    其余每个后端 kind 都必须走**决策列**(有认/改/删 + 拒 + 依据)。防止再出现
    merge_knowledge 那种"新 kind 被误丢进预判列、无拒绝按钮、丢 payload"的接线漏。"""
    import re
    from karvyloop.karvy.proposal_registry import ALL_KINDS, KIND_RUN_TASK
    js = _read("app.js")
    m = re.search(r"_PREDICT_KINDS\s*=\s*\[([^\]]*)\]", js)
    assert m, "app.js 未定义 _PREDICT_KINDS 数组"
    predict_kinds = set(re.findall(r'"([^"]+)"', m.group(1)))
    # 只有习惯预判(KIND_RUN_TASK)在预判列;其余全走决策列(fail-safe)
    assert predict_kinds == {KIND_RUN_TASK}, f"预判列 kind 应只有 run_task,实际 {predict_kinds}"
    # 每个后端决策 kind 都不在预判白名单 → 自动进决策列(含曾漏的 merge_knowledge)
    decision_kinds = set(ALL_KINDS) - {KIND_RUN_TASK}
    for k in ("merge_knowledge", "merge_atoms", "confirm_result",
              "crystallize_skill", "confirm_decision_pref",
              "infeasible_report", "route_to_role", "roundtable", "resolve_conflict", "ops_fix"):
        assert k in decision_kinds, f"后端 ALL_KINDS 缺 {k}(契约漂移)"
        assert k not in predict_kinds, f"{k} 是决策 kind,不该在预判列"


# ---- P1-b 多卡不覆盖:提案卡按 proposal_id 键控,不再 innerHTML="" 抹兄弟卡 ----
def test_h2a_multicard_no_overwrite():
    js = _read("app.js")
    for fn in ("_placeCard", "_removeCardById", "_stripEmpty"):
        assert fn in js, f"app.js 缺多卡键控辅助 {fn}"
    assert "data-proposal-id" in js                    # 卡上挂 id 供替换/移除
    # renderProposal / renderPredict 结尾用 _placeCard(键控追加),不再 list.appendChild(card)
    assert js.count("_placeCard(list,") >= 2
    # h2a_envelope 只撤刚拍的卡(带 proposal_id),不整列 innerHTML=""
    # (第三参 true = 微动效 P0-2 的动画退场;契约点在"按 id 撤单卡",不在参数个数)
    assert "_removeCardById(list, pid, true)" in js


# ---- #6 待你拍板列按 kind 客户端筛选(卡多时能只看一类;只显隐,不动多卡 diff)----
def test_h2a_decide_filter_wired():
    js = _read("app.js")
    # 卡带 kind(筛选数据源)
    assert 'card.setAttribute("data-kind"' in js, "_buildProposalCard 未给卡挂 data-kind"
    # 筛选状态机 + 显隐(不增删 DOM)+ humanize 兜底
    for fn in ("_decideFilter", "_kindLabel", "_refreshDecideFilter", "_applyDecideFilter"):
        assert fn in js, f"app.js 缺 {fn}"
    # 筛选走 display 显隐(绝不动 h2a-list 的增删/替换那套多卡 diff)
    assert ".style.display" in js, "筛选应走 display 显隐,不增删卡 DOM"
    # 三处触发点:新卡后 / 拍掉后 / 开机回放后 都重算筛选条
    assert js.count("_refreshDecideFilter()") >= 3, "新卡/拍掉/回放三处都要重算筛选条"
    # index.html:筛选条容器在 #h2a-list 之前、默认 hidden
    html = _read("index.html")
    assert 'id="h2a-filter"' in html, "index.html 缺 #h2a-filter"
    fi = html.find('id="h2a-filter"'); li = html.find('id="h2a-list"')
    assert fi != -1 and li != -1 and fi < li, "#h2a-filter 必须在 #h2a-list 之前"
    # CSS:轻量 chip 条 + active 态
    css = _read("styles.css")
    assert ".h2a-filter" in css and ".h2a-filter-chip" in css and ".h2a-filter-chip.active" in css
    # i18n:kind 人话标签 + 筛选文案,en+zh 各一份(parity 另由 i18n 测锁)
    i18n = _read("i18n.js")
    for k in ("proposal.kind.crystallize_skill", "proposal.kind.route_to_role",
              "proposal.kind.roundtable", "proposal.kind.merge_knowledge",
              "proposal.kind.confirm_decision_pref", "proposal.kind.ops_fix",
              "proposal.filter_all", "proposal.filter_label"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"


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


def test_agents_panel_import_result_honest_by_kind():
    """J2:外部 agent 导入结果按 import_kind 如实显示 —— executor/skill 型不再假报"已进角色库 ✓"。
    构建产物 agents_panel.js 必须:① 有按判型渲染的函数(不再无脑 imported✓);② 认 pure_executor /
    skill_like 两个"没建 role"型;③ 读后端 note 与 import_kind;④ 有中性态(executor/skill 不给绿 ✓);
    ⑤ skill_like 有指路技能库的跳转。"""
    js = _read("agents_panel.js")
    assert "_renderImportResult" in js, "缺按判型渲染导入结果的函数(仍可能无脑 imported✓)"
    assert "pure_executor" in js and "skill_like" in js, "未按 executor/skill 型分流"
    assert "import_kind" in js, "未读后端 import_kind 判型"
    assert "_setNeutral" in js, "executor/skill 应走中性态(不给绿 ✓ 假成功)"
    assert "KarvySkillsPanel" in js, "skill_like 应指路技能库(跳转按钮)"
    # note(后端已本地化)必须被消费,而非丢弃
    assert "data.note" in js or ".note" in js, "未展示后端诚实 note"


def test_dcard_aligned_pref_kind_uses_i18n():
    """决策卡「你的标准(预对齐)」的 kind 标签走 i18n(dpref.kind_*,与偏好面板同键),
    服务端硬编码中文的 kind_label 只作兜底 —— 英文界面不冒中文([约束]→[constraint])。"""
    js = _read("app.js")
    assert 'dpref.kind_' in js, "标准行 kind 标签未走 i18n"
    assert 'p.kind_label || ""' in js, "kind_label 服务端兜底丢了(旧卡兼容)"


# ============================================================================
# docs/83 弱机瘦身第一刀:T2 轻量模式 + T1/T3/T7/T8 降级块 + T6 defer + drawflow CSS 懒加载
# ============================================================================
import re as _re


def _reduced_transparency_block(css: str) -> str:
    """抽 @media (prefers-reduced-transparency: reduce) 块正文(括号配平)。"""
    m = _re.search(r"@media\s*\(prefers-reduced-transparency:\s*reduce\)\s*\{", css)
    assert m, "缺 @media (prefers-reduced-transparency: reduce) 自动等效入口"
    depth = 1
    for i in range(m.end(), len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[m.end():i]
    raise AssertionError("prefers-reduced-transparency 块括号不配平")


def test_lite_mode_desktop_css_degrade_block():
    """desktop.css:T1 三处壁纸 blur / T7 大阴影 / T8 六 sprite 动画,
    body.lite 与 prefers-reduced-transparency 两个入口都要覆盖(双份同步)。"""
    css = _read("desktop.css")
    media = _reduced_transparency_block(css)
    # 手动入口 body.lite 与系统偏好入口各一份
    for arm_name, arm in (("body.lite", css), ("media", media)):
        prefix = "body.lite.desk-view" if arm_name == "body.lite" else "body.desk-view"
        # T1 三处 backdrop-filter → none(半透实底)
        for sel in (".desk-station", ".desk-dock", ".desk-pending-row"):
            m = _re.search(_re.escape(prefix) + r"[^{}]*" + _re.escape(sel) + r"[^{}]*\{([^}]*)\}", arm)
            assert m, f"{arm_name} 入口缺 {sel} 降级规则"
            assert "backdrop-filter: none" in m.group(1), f"{arm_name} 入口 {sel} 未关 backdrop-filter"
        # T7 大模糊阴影收敛(窗口变量 + 便签/看板/全屏聊天/署名便签/纪念物)
        assert "--win-shadow: 0 6px 16px" in arm, f"{arm_name} 入口未收敛 --win-shadow"
        for sel in (".cockpit-col", ".cockpit-grid", ".chat-panel", ".desk-signed-note", ".desk-memento"):
            assert _re.search(_re.escape(prefix) + r"[^{}]*" + _re.escape(sel) + r"[^{}]*\{[^}]*box-shadow", arm), \
                f"{arm_name} 入口缺 {sel} 阴影收敛"
        # T8 六个 sprite infinite 动画停(idle/working/carry/sleep 的 img + keys + zzz)。
        # 断言带前缀的完整选择器:不带前缀会误命中普通模式原规则(原规则也有 data-state 选择器)。
        for state in ("idle", "working", "carry", "sleep"):
            assert f'{prefix} .karvy-sprite[data-state="{state}"] .karvy-sprite-img' in arm, \
                f"{arm_name} 入口漏停 sprite {state} 动画"
        assert f'{prefix} .karvy-sprite[data-state="working"] .karvy-sprite-keys' in arm, f"{arm_name} 入口漏停 keys overlay"
        assert f'{prefix} .karvy-sprite[data-state="sleep"] .karvy-sprite-zzz' in arm, f"{arm_name} 入口漏停 zzz overlay"


def test_lite_mode_styles_css_degrade_block():
    """styles.css:T3 顶栏 blur / T7 弹窗与拖拽阴影 / T8 busy-pulse+FAB,双入口覆盖。
    body.lite 臂必须带 body.lite 前缀断言(不带会误命中普通模式的原规则=测了个寂寞)。"""
    css = _read("styles.css")
    media = _reduced_transparency_block(css)
    checks = [
        # (selector 尾巴, 降级声明特征)
        (r"\.topbar", "backdrop-filter: none"),
        (r"\.modal", "box-shadow: 0 4px 12px"),
        (r"\.karvy-tour-pop\.driver-popover", "box-shadow: 0 4px 12px"),
        (r"\.desk-dragging", "box-shadow: 0 4px 12px"),
        (r"\.busy-dot", "animation: none"),
        (r"\.karvy-fab", "drop-shadow(0 2px 5px"),
    ]
    for sel, decl in checks:
        # 手动入口:body.lite(.desk-view)? 前缀 + 选择器 + 降级声明
        m = _re.search(r"body\.lite[^{}]*" + sel + r"[^{}]*\{([^}]*)\}", css)
        assert m and decl in m.group(1), f"body.lite 入口缺 {sel} 的 {decl}"
        # 系统偏好入口:媒体块内同一批规则(块里只有降级规则,直接断言)
        m2 = _re.search(sel + r"[^{}]*\{([^}]*)\}", media)
        assert m2 and decl in m2.group(1), f"prefers-reduced-transparency 入口缺 {sel} 的 {decl}"


def test_lite_mode_normal_visuals_untouched():
    """普通模式一像素不动:原毛玻璃/大阴影/动画声明必须原样在(lite 只是**追加**覆盖)。"""
    desk = _read("desktop.css")
    for original in (
        "backdrop-filter: blur(4px);",                       # T1① desk-station
        "backdrop-filter: blur(10px);",                      # T1② desk-dock
        "backdrop-filter: blur(3px);",                       # T1③ desk-pending-row
        "box-shadow: 0 24px 70px rgba(58, 43, 31, 0.34);",   # 全屏聊天大阴影
        "box-shadow: 0 10px 26px rgba(142, 95, 60, 0.16);",  # 便签大阴影
        "--win-shadow: 0 18px 50px",                          # 聚焦窗口阴影(两主题)
        "animation: karvy-breathe 3.4s ease-in-out infinite;",
        "animation: karvy-type 0.42s ease-in-out infinite;",
        "animation: karvy-keys 0.84s linear infinite;",
        "animation: karvy-waddle 0.6s ease-in-out infinite;",
        "animation: karvy-doze 4.6s ease-in-out infinite;",
        "animation: karvy-zzz 2.6s ease-in-out infinite;",
    ):
        assert original in desk, f"普通模式视觉被动了:desktop.css 缺原声明 {original}"
    styles = _read("styles.css")
    for original in (
        "backdrop-filter: saturate(160%) blur(8px);",        # T3 顶栏
        "animation: busy-pulse 1s ease-in-out infinite;",    # busy 圆点
        "box-shadow: 0 12px 40px rgba(59, 42, 26, 0.22);",   # .modal
        "box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);",       # tour popover
        "box-shadow: 0 14px 34px rgba(0, 0, 0, 0.35);",      # desk-dragging
        "filter: drop-shadow(0 8px 18px rgba(142,95,60,0.28));",  # FAB
    ):
        assert original in styles, f"普通模式视觉被动了:styles.css 缺原声明 {original}"


def test_lite_mode_toggle_wired():
    """开关三件套:index.html 按钮 + 防闪内联预应用;app.js 记 localStorage;i18n en+zh。"""
    html = _read("index.html")
    assert 'id="lite-toggle"' in html, "index.html 缺 🪶 轻量模式开关按钮"
    assert 'data-i18n-title="lite.title"' in html, "开关 tooltip 未走 i18n"
    assert 'aria-pressed' in html, "开关缺 aria-pressed 态"
    # 防闪:<body> 内联脚本在任何 defer 脚本执行前挂 body.lite(读 localStorage)
    assert html.find('localStorage.getItem("karvyloop_lite")') != -1, "index.html 缺 lite 防闪预应用"
    assert html.find('karvyloop_lite') < html.find('src="/static/app.js"'), "lite 预应用必须在 app.js 之前"
    js = _read("app.js")
    assert '"lite-toggle"' in js.replace("'", '"'), "app.js 未接 lite-toggle"
    assert "karvyloop_lite" in js, "app.js 未持久化 lite 选择"
    i18n = _read("i18n.js")
    assert i18n.count('"lite.title"') == 2, "i18n lite.title 不是 en+zh 各一份"
    # 开着时的按下态样式(普通默认态零变化:选择器只在 aria-pressed=true 命中)
    css = _read("styles.css")
    assert '#lite-toggle[aria-pressed="true"]' in css


def test_body_scripts_all_defer():
    """T6:body 外链脚本全 defer(保序,render→app / desktop→app 依赖不变)。
    T4/T5(docs/83)后首屏只剩 8 个必需脚本:dom/modal/ui_widgets/unlock_panel/
    render/i18n/desktop/app —— 面板脚本走 _ensurePanelScript 懒加载、highlight 走
    render.js _ensureHighlight 懒加载,不再在这批里。"""
    html = _read("index.html")
    tags = _re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"[^>]*>", html)
    assert tags == [
        "/static/dom.js", "/static/modal.js", "/static/ui_widgets.js",
        "/static/unlock_panel.js", "/static/render.js", "/static/i18n.js",
        "/static/desktop.js", "/static/app.js",
    ], f"首屏脚本清单/顺序漂了(变了要同步 docs/83 清单): {tags}"
    for m in _re.finditer(r"<script\b[^>]*\bsrc=\"([^\"]+)\"[^>]*>", html):
        assert " defer" in m.group(0), f"{m.group(1)} 缺 defer"
    # 执行序锁不变:render.js / i18n.js / desktop.js 都在 app.js 前(defer 按文档序执行)
    a = tags.index("/static/app.js")
    for dep in ("/static/render.js", "/static/i18n.js", "/static/desktop.js"):
        assert tags.index(dep) < a, f"{dep} 必须排在 app.js 之前"


def test_drawflow_css_lazy_with_canvas():
    """drawflow.min.css 错配修复:JS 已懒加载,其 CSS 不再首屏常驻 —— 挪进
    app.js _ensureWorkflowCanvas 同一注入点(点「编辑画布」才拉)。"""
    html = _read("index.html")
    assert "drawflow.min.css" not in html, "drawflow.min.css 不该再压在首屏"
    js = _read("app.js")
    i = js.find("function _ensureWorkflowCanvas")
    assert i != -1
    body = js[i:i + 1200]
    assert "drawflow.min.css" in body and "drawflow-css" in body, \
        "drawflow.min.css 应在 _ensureWorkflowCanvas 里与 JS 同点注入(带 id 防重复)"
