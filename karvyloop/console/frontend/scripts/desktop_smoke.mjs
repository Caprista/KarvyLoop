/* desktop_smoke.mjs — 真路径验证桌面视图壳(docs/51 P1)。
 * jsdom 里加载 static/desktop.js:dock 同构渲染(12 入口)、enter/leave 幂等与清痕、
 * 拖拽落 localStorage(karvyloop_desk.v1)、⚖ notifyH2A 置顶+闪烁、resetLayout 清存档。
 * jsdom 无真布局(rect 全 0)→ clamp 走"无布局环境不 clamp"分支,坐标语义仍可断言。
 */
import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const HTML = `<!doctype html><body>
<div class="app">
  <nav class="sidebar">
    <button class="nav-item" data-panel="domains"><span class="nav-ico">📂</span><span data-i18n="nav.domains">Domains</span></button>
    <button class="nav-item" data-panel="roles"><span class="nav-ico">◑</span><span data-i18n="nav.roles">Roles</span></button>
    <button class="nav-item" data-panel="atoms"><span class="nav-ico">⬡</span><span data-i18n="nav.atoms">Atoms</span></button>
    <button class="nav-item" data-panel="agents"><span class="nav-ico">⊞</span><span data-i18n="nav.agents">Agents</span></button>
    <button class="nav-item" data-panel="memory"><span class="nav-ico">🧠</span><span data-i18n="nav.memory">Knowledge</span></button>
    <button class="nav-item" data-panel="decision_prefs"><span class="nav-ico">🧭</span><span data-i18n="nav.decision_prefs">Prefs</span></button>
    <button class="nav-item" data-panel="skills"><span class="nav-ico">🧩</span><span data-i18n="nav.skills">Skills</span></button>
    <button class="nav-item" data-panel="models"><span class="nav-ico">🤖</span><span data-i18n="nav.models">Models</span></button>
    <button class="nav-item" data-panel="diagnose"><span class="nav-ico">🩺</span><span data-i18n="nav.diagnose">Diagnose</span></button>
    <button class="nav-item" data-panel="files"><span class="nav-ico">📁</span><span data-i18n="nav.files">Files</span></button>
    <button class="nav-item" data-panel="schedules"><span class="nav-ico">⏰</span><span data-i18n="nav.schedules">Scheduled</span></button>
  </nav>
  <main class="cockpit">
    <div class="modal-overlay chat-overlay chat-host hidden" id="chat-modal">
      <div class="chat-panel">
        <div class="chat-panel-head"><span class="modal-title" id="chat-title">💬</span>
          <button class="modal-close" id="chat-modal-close">✕</button></div>
        <div class="chat-panel-body">
          <aside class="chat-panel-side"><div id="peer-list"></div></aside>
          <main class="chat-panel-main"><div class="chat-log" id="chat-log"></div><div id="chat-input" contenteditable="true"></div></main>
        </div>
      </div>
    </div>
    <div class="cockpit-grid">
      <section class="cockpit-col col-decide"><h2 class="col-head">⚖</h2><div class="col-scroll">
        <div id="h2a-list"><div class="h2a-card"><div class="h2a-summary">把周报整理成模板并沉淀为技能</div></div></div></div></section>
      <section class="cockpit-col col-intel"><h2 class="col-head">📥</h2><div class="col-scroll">
        <div id="task-board"><div class="task-card"><div class="task-intent">整理季度周报</div></div></div></div></section>
      <section class="cockpit-col col-predict"><h2 class="col-head">🔮</h2><div class="col-scroll"><div id="predict-list"></div></div></section>
      <section class="cockpit-col col-busy"><h2 class="col-head">🔄</h2><div class="col-scroll"><div id="busy-list"></div></div></section>
    </div>
  </main>
  <div class="modal-overlay hidden" id="mgmt-modal">
    <div class="modal">
      <div class="modal-head"><span class="modal-title" id="mgmt-title">—</span>
        <button class="modal-close" id="mgmt-close">✕</button></div>
      <div class="modal-body" id="mgmt-body"></div>
    </div>
  </div>
  <div class="karvy-dock">
    <div class="karvy-bubble hidden" id="karvy-bubble"><span class="karvy-bubble-dots">·</span></div>
    <button class="karvy-fab" id="chat-open"></button>
  </div>
  <div class="desk-dock" id="desk-dock"></div>
</div>
</body>`;

// jsdom 无 canvas 2d 是已知环境事实(pixelpet 走"不画只跑状态机"分支),吞掉这条噪音
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (!/Not implemented/.test(String(e && e.message))) console.error(e);
});
const dom = new JSDOM(HTML, { url: "http://localhost/", pretendToBeVisual: true, virtualConsole: vc });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.location = dom.window.location;

// ---- P1.5 灵魂的环境 stub:契约形状 mock(/api/roles/presence 冻结形状)+ 只读 WS ----
const NOW_S = Math.floor(Date.now() / 1000);
let presenceOk = true;
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.indexOf("/api/roles/presence") >= 0) {
    if (!presenceOk) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, json: async () => ({ roles: [
      { role_id: "karvy", display: "小卡", domain_id: "l0", status: "busy", running: 1,
        last_activity_ts: NOW_S, last_task: { id: "t0", intent: "hold the desk" } },
      { role_id: "researcher", display: "研究员", domain_id: "d1", status: "busy", running: 1,
        last_activity_ts: NOW_S, last_task: { id: "t2", intent: "整理季度周报" } },
      { role_id: "resty", display: "老王", domain_id: "d1", status: "idle", running: 0,
        last_activity_ts: NOW_S - 7200, last_task: { id: "t3", intent: "老活" } },
      { role_id: "idler", display: "新人", domain_id: "d1", status: "idle", running: 0,
        last_activity_ts: null, last_task: null },
    ] }) };
  }
  if (u.indexOf("/api/tasks") >= 0) {
    return { ok: true, json: async () => ({ tasks: [
      { id: "wf1", who: "⚙ 工作流", role: "group", status: "running", intent: "发布 v2" },
    ] }) };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 1; FakeWS.instances.push(this); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  send() { throw new Error("灵魂 WS 是只读的,不许 send"); }
}
FakeWS.instances = [];
globalThis.WebSocket = FakeWS;

const here = dirname(fileURLToPath(import.meta.url));
// 先装真 i18n bundle(P1.5 工位 tip 要 {intent} 插值,别拿裸 key 断言)
(0, eval)(readFileSync(resolve(here, "../../static/i18n.js"), "utf8"));
const code = readFileSync(resolve(here, "../../static/desktop.js"), "utf8");
(0, eval)(code);

const KD = dom.window.KarvyDesktop;
assert.ok(KD && ["enter", "leave", "notifyH2A", "resetLayout"].every((k) => typeof KD[k] === "function"),
  "window.KarvyDesktop = { enter, leave, notifyH2A, resetLayout } 契约缺失");

// ---- dock 同构渲染:11 个 data-panel(与侧栏一致)+ 💰 = 12 入口,右段窗口指示 + ↺ ----
const dock = document.getElementById("desk-dock");
const panelItems = dock.querySelectorAll(".dock-item[data-panel]");
assert.equal(panelItems.length, 11, "dock 左段应同构复用侧栏 11 个 data-panel 按钮");
assert.ok(panelItems[0].classList.contains("nav-item"), "dock 图标必须挂 nav-item 类(setupMgmtPanels 同一批绑定命中)");
assert.equal(panelItems[0].getAttribute("data-panel"), "domains");
assert.equal(panelItems[0].getAttribute("data-i18n-tip"), "nav.domains", "tooltip 复用 nav.* i18n key");
assert.ok(dock.querySelector(".dock-tokens"), "dock 第 12 位 💰 token 表缺失");
assert.ok(dock.querySelector("#desk-dock-win-chat") && dock.querySelector("#desk-dock-win-mgmt"), "dock 右段窗口指示缺失");
assert.ok(dock.querySelector(".dock-reset"), "dock ↺ 重置布局按钮缺失");
assert.ok(document.getElementById("mgmt-min"), "mgmt 标题栏应注入 ─ 最小化按钮");

// ---- enter:便签/聊天窗拿到 transform 定位,标题栏 tab 可达 ----
document.body.classList.add("desk-view");
KD.enter();
const decide = document.querySelector(".col-decide");
const chatPanel = document.querySelector("#chat-modal .chat-panel");
assert.ok(decide.style.transform.indexOf("translate3d") === 0, "便签未拿到 translate3d 定位");
assert.ok(chatPanel.style.transform.indexOf("translate3d") === 0, "聊天窗未拿到 translate3d 定位");
assert.equal(decide.querySelector(".col-head").getAttribute("tabindex"), "0", "便签头应 tab 可达");
assert.ok(parseInt(decide.style.zIndex, 10) > 0, "便签应拿到 z-index(聚焦置顶空间)");

// ============================================================================
// 空旷单焦点新布局(Hardy 2026-07-05):大时间 / 待办轻量条 / 便签默认收起 / 看板收图标 / 聊天三态
// ============================================================================
// ---- 大时间(桌面锚点):进场就在,H:MM 结构 ----
const clock = document.getElementById("desk-clock");
assert.ok(clock, "进桌面应出现大时间锚点(#desk-clock)");
KD._layout.paintClock(new Date(2026, 6, 5, 9, 7, 0));
assert.equal(clock.querySelector(".clock-h").textContent, "9", "大时间时位应对");
assert.equal(clock.querySelector(".clock-m").textContent, "07", "大时间分位应补零");

// ---- 待处理任务项(极简条目,读现有 h2a/task DOM):可见但不喧宾,不是完整卡 ----
const pending = document.getElementById("desk-pending");
assert.ok(pending && !pending.querySelector(".desk-pending-empty"), "有待办时应列出轻量条目(非空态)");
const rows = pending.querySelectorAll(".desk-pending-row");
assert.ok(rows.length >= 2, "⚖ 待拍板 + 📥 料应各出一条极简待办");
assert.ok((rows[0].textContent || "").indexOf("周报") >= 0, "待办条应是摘要(一行),不是完整卡");

// ---- 便签默认收起(停靠态,标题+角标即可,不铺满)----
KD._layout; // seam 存在
assert.ok(decide.classList.contains("col-collapsed"), "便签默认应收起(空旷,不自动铺满)");
document.querySelectorAll(".cockpit-grid .cockpit-col").forEach((c) => {
  assert.ok(c.classList.contains("col-collapsed"), `${c.className} 默认应收起`);
});

// ---- 聊天默认 compact(单窗口无会话列表)⤢→expanded→full 三态 ----
// bug2:精简聊天空态文案已挂 chat-log[data-empty](chat-log 空时 CSS ::before 显示,不是决策卡压空白)
const chatLogEl = document.getElementById("chat-log");
assert.ok(chatLogEl && (chatLogEl.getAttribute("data-empty") || "").length > 0,
  "bug2: 精简聊天空态文案应挂 #chat-log[data-empty](正经空态)");
assert.ok(document.getElementById("desk-chat-expand"), "聊天标题栏应注入 ⤢ 放大按钮");
assert.equal(KD._layout.chatMode(), "compact", "聊天默认 = 精简单窗口态");
assert.ok(!document.body.classList.contains("desk-chat-expanded") && !document.body.classList.contains("desk-chat-full"),
  "compact 态不挂 expanded/full 类");
KD._layout.cycleChatMode();
assert.equal(KD._layout.chatMode(), "expanded", "⤢ 第一下 → 完整态(带会话列表)");
assert.ok(document.body.classList.contains("desk-chat-expanded"), "expanded 应挂 body.desk-chat-expanded");
KD._layout.cycleChatMode();
assert.equal(KD._layout.chatMode(), "full", "⤢ 第二下 → 网页内全屏");
assert.ok(document.body.classList.contains("desk-chat-full"), "full 应挂 body.desk-chat-full");
assert.equal(JSON.parse(dom.window.localStorage.getItem("karvyloop_desk.v1")).chatMode, "full", "聊天形态应持久化");
KD._layout.cycleChatMode();
assert.equal(KD._layout.chatMode(), "compact", "⤢ 第三下 → 回精简(三态循环)");

// ---- 看板收进 dock 📋 图标 + 角标(有没有新料);点开摊四标签(便签全展开)----
const boardBtn = document.getElementById("desk-board-btn");
assert.ok(boardBtn, "dock 应有 📋 看板图标");
KD._layout.updateBoardBadge();
assert.ok(boardBtn.querySelector(".dock-badge"), "有待拍板/料 → 看板图标应有角标(新数据提示)");
assert.ok(KD._layout.boardCount() >= 2, "看板角标计数应含 h2a + task 卡");
KD._layout.toggleBoard();
assert.ok(document.body.classList.contains("desk-board-open"), "点 📋 应摊开看板(desk-board-open)");
assert.ok(!decide.classList.contains("col-collapsed"), "看板摊开时便签应全展开看详情");
KD._layout.toggleBoard();
assert.ok(!document.body.classList.contains("desk-board-open"), "再点 📋 应关看板");
assert.ok(decide.classList.contains("col-collapsed"), "关看板后便签回默认收起态");

// ---- 全局小卡 z-index 最高(修 bug:现在能被面板遮挡)----
// desktop.css: body.desk-view .karvy-dock { z-index: 9550 } —— 压过 dock 9500 与所有窗口/便签 220+
const deskCss = readFileSync(resolve(here, "../../static/desktop.css"), "utf8");
const deskCssFlat = deskCss.replace(/\n/g, " ");
assert.ok(/\.karvy-dock\s*\{[^}]*z-index:\s*9550/.test(deskCssFlat),
  "全局小卡 .karvy-dock 应置顶 z-index:9550(绝不被面板遮挡)");

// ---- 5-bug 修复的 CSS 契约(Hardy 2026-07-05 实拍;jsdom 无真布局,守 CSS 源不回退)----
// bug2:compact 聊天窗把 .chat-panel-body 折成单列 —— 否则 side(display:none)留 200px 空列、
//       chat-log 被挤、右侧一大片空白(实拍)。
assert.ok(/\.chat-host\s+\.chat-panel-body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(deskCssFlat),
  "bug2: compact 聊天窗 body 应折单列(chat-log 铺满,不留 side 空列)");
// bug2:空态占位(:has(#chat-log:empty))—— 正经空态,不是决策卡压空白
assert.ok(/#chat-log:empty\)?\s*(#chat-log)?::before/.test(deskCssFlat) || /chat-log:empty/.test(deskCssFlat),
  "bug2: 应有 chat-log 空态占位规则(正经空态)");
// bug3:mgmt 面板 max-height 必须为 dock 留空(不再是裸 80vh 会钻进 dock)
assert.ok(/#mgmt-modal\s+\.modal\s*\{[^}]*max-height:\s*calc\(100vh\s*-\s*196px\)/.test(deskCssFlat),
  "bug3: mgmt 面板 max-height 应为顶栏+dock 带留空(calc(100vh - 196px))");
// bug1:本周纪念物不再居中 top:22(压时钟)—— 退到左上角(left:18/top:18)
assert.ok(/\.desk-memento\s*\{[^}]*left:\s*18px[^}]*top:\s*18px/.test(deskCssFlat),
  "bug1: 本周纪念物应退到左上角(不居中压大时间)");

// ---- 拖拽:pointerdown→move→up 落 localStorage(karvyloop_desk.v1)----
function pt(type, x, y) {
  const e = new dom.window.Event(type, { bubbles: true });
  e.clientX = x; e.clientY = y; e.button = 0; e.pointerId = 1;
  return e;
}
const head = decide.querySelector(".col-head");
head.dispatchEvent(pt("pointerdown", 100, 100));
head.dispatchEvent(pt("pointermove", 160, 140));   // 超过 4px 死区
await new Promise((r) => dom.window.requestAnimationFrame(r));
head.dispatchEvent(pt("pointerup", 160, 140));
const saved = JSON.parse(dom.window.localStorage.getItem("karvyloop_desk.v1"));
assert.ok(saved && saved.notes && saved.notes["col-decide"], "拖拽 pointerup 后应把便签位置写进 karvyloop_desk.v1");
assert.equal(typeof saved.notes["col-decide"].x, "number");

// ---- 聊天 ✕ = 最小化(不是关闭);点卡皮巴拉 = 恢复 ----
document.getElementById("chat-modal").classList.remove("hidden");
document.getElementById("chat-modal-close").dispatchEvent(new dom.window.Event("click", { bubbles: true }));
assert.ok(document.getElementById("chat-modal").classList.contains("desk-min"), "桌面视图下 ✕ 应最小化聊天窗(desk-min)");
assert.equal(JSON.parse(dom.window.localStorage.getItem("karvyloop_desk.v1")).windows.chat.min, true, "最小化态应持久化");
document.getElementById("chat-open").dispatchEvent(new dom.window.Event("click", { bubbles: true }));
assert.ok(!document.getElementById("chat-modal").classList.contains("desk-min"), "点卡皮巴拉应恢复聊天窗");

// ---- ⚖ notifyH2A(replay):开机回放存量 pending 卡 = 状态不是事件 ——
// 置顶/在位可瞟照做,但**零剧场**(不叼卡/不闪/不冒泡;Hardy 实拍"开屏飘上去"回归锁)
const zReplayBefore = parseInt(decide.style.zIndex, 10);
KD.notifyH2A({ replay: true });
assert.ok(parseInt(decide.style.zIndex, 10) > zReplayBefore, "replay 仍应置顶 ⚖(状态保证)");
assert.ok(!document.getElementById("desk-carry-actor"), "replay 不许起叼卡小演员(开屏稳在窝)");
assert.ok(!decide.classList.contains("note-alert"), "replay 不闪 ⚖(剧场只回应真事件)");
assert.ok(document.getElementById("karvy-bubble").classList.contains("hidden"), "replay 不冒泡");

// ---- ⚖ notifyH2A(真事件):置顶 + note-alert 闪烁 + 卡皮巴拉冒泡 ----
// P1.5:小卡先叼卡走过去(2s 兜底),**到位后**才闪 —— 有小演员就等它到
const zBefore = parseInt(decide.style.zIndex, 10);
KD.notifyH2A();
assert.ok(parseInt(decide.style.zIndex, 10) > zBefore, "notifyH2A 应把 ⚖ 便签置顶(z 递增)");
if (document.getElementById("desk-carry-actor")) await new Promise((r) => setTimeout(r, 2150));
assert.ok(decide.classList.contains("note-alert"), "新决策卡到达应给 ⚖ 便签加 note-alert");
assert.ok(!document.getElementById("karvy-bubble").classList.contains("hidden"), "notifyH2A 应让卡皮巴拉冒泡");

// ---- mgmt 窗:开(modal.ts 摘 hidden)→ ─ 最小化 → 观察者不许秒撤 → 重开恢复 ----
// 回归锁:观察者回调无条件 classList.remove("desk-min") 会重写 class 属性再触发自己
// = 微任务死循环(真浏览器主线程冻死;此处表现为测试卡死超时)+ 把刚 minimize 的窗秒撤。
const flush = () => new Promise((r) => setTimeout(r, 0));
const mgmt = document.getElementById("mgmt-modal");
mgmt.classList.remove("hidden");                    // 开面板(modal.ts openMgmtModal 语义)
await flush();                                      // 死循环在这会卡死 → 测试超时即回归
document.getElementById("mgmt-min").dispatchEvent(new dom.window.Event("click", { bubbles: true }));
assert.ok(mgmt.classList.contains("desk-min"), "mgmt ─ 应最小化(desk-min)");
await flush();
assert.ok(mgmt.classList.contains("desk-min"), "观察者不许把刚 minimize 的 desk-min 秒撤");
mgmt.classList.add("hidden");                       // 关面板
await flush();
mgmt.classList.remove("hidden");                    // 重开 = 恢复可见(desk-min 摘除)
await flush();
assert.ok(!mgmt.classList.contains("desk-min"), "重开面板应恢复可见(desk-min 摘除)");
mgmt.classList.add("hidden");
await flush();

// ---- resetLayout:清 karvyloop_desk.v1 回默认 ----
dom.window.confirm = () => true;
KD.resetLayout();
assert.equal(dom.window.localStorage.getItem("karvyloop_desk.v1"), null, "resetLayout 应清掉存档");

// ---- 日/夜壁纸:auto 变体纯函数 / 四档切换挂类 / dock 换挡按钮 / leave 摘类 ----
const W = KD._wall;
assert.ok(W && typeof W.apply === "function", "KarvyDesktop._wall 测试接缝缺失");
assert.equal(W.variantFor(6), "day", "6:00 = day(边界)");
assert.equal(W.variantFor(18), "day", "18:59 侧 = day");
assert.equal(W.variantFor(19), "night", "19:00 = night(边界)");
assert.equal(W.variantFor(5), "night");
assert.equal(W.variantFor(0), "night");
assert.equal(W.mode(), "auto", "默认档 = auto");
W.apply(new Date(2026, 6, 4, 10, 0, 0));   // auto + mock 白天
assert.ok(document.body.classList.contains("desk-wall-day"), "auto 档白天应挂 desk-wall-day");
W.apply(new Date(2026, 6, 4, 22, 0, 0));   // auto + mock 夜晚
assert.ok(document.body.classList.contains("desk-wall-night") && !document.body.classList.contains("desk-wall-day"),
  "auto 档夜晚应换挂 desk-wall-night");
W.set("day");
assert.ok(document.body.classList.contains("desk-wall-day"), "固定白天档应挂 desk-wall-day");
assert.equal(dom.window.localStorage.getItem("karvyloop_desk_wall.v1"), "day", "档位应持久化 localStorage");
W.set("off");
assert.ok(!document.body.classList.contains("desk-wall-day") && !document.body.classList.contains("desk-wall-night"),
  "off 档 = 摘光壁纸类(纯色回现状)");
const wallBtn = document.getElementById("desk-wall-btn");
assert.ok(wallBtn, "dock 应有 🌗 壁纸换挡按钮");
wallBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));   // off → auto(循环)
assert.equal(W.mode(), "auto", "点击换挡应循环 off → auto");
W.set("night");   // 留一档挂着,验 leave 摘干净

// ---- leave:清干净全部内联痕迹(两个老视图像素级不动的保险)----
document.body.classList.remove("desk-view");
KD.leave();
assert.ok(!document.body.classList.contains("desk-wall-day") && !document.body.classList.contains("desk-wall-night"),
  "leave 后壁纸类应摘干净(老视图零痕迹)");
assert.equal(decide.style.transform, "", "leave 后便签 transform 未清");
assert.equal(chatPanel.style.transform, "", "leave 后聊天窗 transform 未清");
assert.ok(!document.getElementById("chat-modal").classList.contains("desk-min"), "leave 后 desk-min 未清");
assert.equal(decide.querySelector(".col-head").getAttribute("tabindex"), null, "leave 后 tabindex 未清");
assert.ok(!document.getElementById("desk-clock"), "leave 后大时间 DOM 应清(老视图零痕迹)");
assert.ok(!document.body.classList.contains("desk-chat-expanded") && !document.body.classList.contains("desk-chat-full")
  && !document.body.classList.contains("desk-board-open"), "leave 后聊天三态/看板类应摘干净");

// ============================================================================
// P1.5 灵魂(docs/53):工位区 / 像素小卡 / 叼卡 / 署名便签 / 工作证 —— 全走真实事件形状
// ============================================================================
const KD2 = dom.window.KarvyDesktop;
assert.ok(KD2._soul && typeof KD2._soul.handle === "function", "KarvyDesktop._soul 测试接缝缺失");
document.body.classList.add("desk-view");
KD2.enter();
await new Promise((r) => setTimeout(r, 30));   // 等 presence/tasks 两个 stub fetch 落地

// ---- 工位区:小卡不占工位(常驻右下),无活动记录的角色不摆空工位 ----
const bar = document.getElementById("desk-presence");
assert.ok(bar && !bar.classList.contains("hidden"), "presence API 通着,工位栏应显示");
assert.equal(KD2._soul.stationCount(), 2, "工位数应 =2(researcher busy + resty 有活动;karvy/idler 不占)");
assert.equal(document.querySelectorAll(".desk-station").length, 2);
const stR = document.querySelector('.desk-station[data-role-id="researcher"]');
assert.ok(stR, "researcher 工位缺失");
assert.ok(stR.classList.contains("is-busy"), "busy 角色工位灯应亮(is-busy)");
assert.equal(stR.dataset.petState, "working", "busy → working 动画(真实状态驱动)");
assert.ok((stR.getAttribute("data-tip") || "").indexOf("整理季度周报") >= 0, "hover 应出「正在:<intent>」");
const stSprite = stR.querySelector(".karvy-sprite");
assert.ok(stSprite, "工位应有卡皮巴拉 sprite(官方原图)");
assert.ok(stSprite.querySelector("img.karvy-sprite-img"), "工位 sprite 应内含官方原图 <img>");
assert.equal(stSprite.getAttribute("data-state"), "working", "工位 sprite data-state 应随真实状态");
assert.equal(stR.querySelector(".station-name").textContent, "研究员");
const stZ = document.querySelector('.desk-station[data-role-id="resty"]');
assert.equal(stZ.dataset.petState, "sleep", "2 小时没活动 → sleep(真实状态,不是 flavor)");
assert.ok(!document.querySelector('.desk-station[data-role-id="karvy"]'), "小卡不该占工位(它常驻右下)");
assert.ok(!document.querySelector('.desk-station[data-role-id="idler"]'), "零活动记录的角色不摆空工位");
// 小卡 sprite 替身住进 .karvy-fab(占位 canvas 已被 sprite 根原位替换,id 保留)
const mascotRoot = document.querySelector("#chat-open #desk-karvy-pixel");
assert.ok(mascotRoot, "小卡 sprite 替身应住进右下 fab");
assert.ok(mascotRoot.classList.contains("karvy-sprite"), "替身应是 .karvy-sprite 根(原图版)");
assert.ok(mascotRoot.querySelector("img.karvy-sprite-img"), "替身应内含官方原图 <img>");

// ---- 只读 WS:进场即连;role_presence 增量翻状态 ----
assert.ok(FakeWS.instances.length >= 1, "desk 进场应自开一条只读 WS");
const soulWs = FakeWS.instances[FakeWS.instances.length - 1];
soulWs.onmessage({ data: JSON.stringify({ type: "role_presence", payload: {
  role_id: "researcher", display: "研究员", domain_id: "d1", status: "idle", running: 0,
  last_activity_ts: NOW_S, last_task: { id: "t2", intent: "整理季度周报" } } }) });
assert.ok(!stR.classList.contains("is-busy"), "idle 增量应灭工位灯");
assert.equal(stR.dataset.petState, "idle", "idle 增量 → idle 呼吸");

// ---- 工作证摊桌(vignette ⑥ 最小版):进场种子 + task_step 打勾 ----
const wc = document.querySelector('.desk-workcard[data-task-id="wf1"]');
assert.ok(wc, "running 的 group 任务应在进场时摊开工作证");
soulWs.onmessage({ data: JSON.stringify({ type: "task_step", payload: {
  task_id: "wf1", step_id: "s1", display: "研究员", status: "done" } }) });
const chip = wc.querySelector(".work-chip");
assert.ok(chip && chip.classList.contains("done"), "步完成 → 名字牌打勾");
assert.equal(chip.querySelector(".chip-mark").textContent, "✓");
soulWs.onmessage({ data: JSON.stringify({ type: "task_step", payload: {
  task_id: "wf1", step_id: "s2", display: "审稿人", status: "failed", error: "boom" } }) });
assert.ok(wc.querySelectorAll(".work-chip.failed").length === 1, "步失败 → ✗ 名字牌");

// ---- 署名便签(vignette ②):task_status done → 浮出;3 张上限旧的淡出 ----
soulWs.onmessage({ data: JSON.stringify({ type: "task_status", payload: {
  id: "wf1", who: "⚙ 工作流", role: "group", status: "done", intent: "发布 v2",
  result: "v2 已发布,一切正常", finished: NOW_S } }) });
let notes = document.querySelectorAll(".desk-signed-note");
assert.equal(notes.length, 1, "done → 署名便签应浮出");
assert.ok(notes[0].textContent.indexOf("⚙ 工作流") >= 0, "便签必须带署名(who)");
assert.ok(notes[0].textContent.indexOf("v2 已发布") >= 0, "便签必须带结果摘要");
assert.ok(wc.classList.contains("is-done"), "group 任务 done → 工作证转 is-done(随后收走)");
for (let i = 0; i < 3; i++) {
  soulWs.onmessage({ data: JSON.stringify({ type: "task_status", payload: {
    id: "t" + (9 + i), who: "研究员", role: "researcher", status: "done",
    intent: "活" + i, result: "结果" + i, finished: NOW_S } }) });
}
notes = document.querySelectorAll(".desk-signed-note:not(.is-fading)");
assert.equal(notes.length, 3, "署名便签 3 张上限(旧的淡出)");

// ---- 叼卡(vignette ③):notifyH2A → 小演员出现 → 到位后 ⚖ 闪 + 回窝 ----
KD2.notifyH2A();
const actor = document.getElementById("desk-carry-actor");
assert.ok(actor, "h2a 到达应出现叼卡小演员(.desk-carry)");
const actorSprite = actor.querySelector(".karvy-sprite");
assert.ok(actorSprite, "小演员应是原图 sprite");
assert.equal(actorSprite.getAttribute("data-state"), "carry", "小演员应进 carry 态(叼小白卡 overlay)");
assert.ok(document.getElementById("desk-karvy-pixel").classList.contains("is-away"),
  "叼卡途中常驻小卡应离席(is-away)");
const decide2 = document.querySelector(".col-decide");
assert.ok(!decide2.classList.contains("note-alert"), "便签闪应等小卡到位(jsdom 走 2s 兜底)");
await new Promise((r) => setTimeout(r, 2150));   // jsdom 无 transitionend → 定时器兜底收尾
assert.ok(!document.getElementById("desk-carry-actor"), "到位后小演员应撤掉");
assert.ok(!document.getElementById("desk-karvy-pixel").classList.contains("is-away"), "小卡应回窝");
assert.ok(decide2.classList.contains("note-alert"), "到位后 ⚖ 便签才闪(既有动画)");

// ---- 拍板闭环:h2a_envelope → 小卡短暂开心帧(真实事件,不是随机卖萌)----
soulWs.onmessage({ data: JSON.stringify({ type: "h2a_envelope", payload: { proposal_id: "p1" } }) });
// (开心帧是 pixelpet 内部状态,持续 2.2s 自动回真实态;这里只验不炸、不阻塞)

// ---- leave:灵魂层全清痕(老视图像素级不动的保险)----
document.body.classList.remove("desk-view");
KD2.leave();
assert.ok(!document.getElementById("desk-karvy-pixel"), "leave 后像素替身应移除(PNG 回归)");
assert.equal(document.querySelectorAll(".desk-station").length, 0, "leave 后工位应清空");
assert.equal(document.querySelectorAll(".desk-signed-note").length, 0, "leave 后署名便签应清空");
assert.equal(document.querySelectorAll(".desk-workcard").length, 0, "leave 后工作证应清空");
assert.equal(soulWs.readyState, 3, "leave 后只读 WS 应关闭");

// ---- presence API 不通(还没上线)→ 工位栏优雅隐藏,不空壳 ----
presenceOk = false;
document.body.classList.add("desk-view");
KD2.enter();
await new Promise((r) => setTimeout(r, 30));
assert.ok(document.getElementById("desk-presence").classList.contains("hidden") ||
  document.querySelectorAll(".desk-station").length === 0, "API 调不通应优雅隐藏工位栏");
document.body.classList.remove("desk-view");
KD2.leave();

console.log("✓ desktop smoke OK — dock 12 入口同构 / enter定位+a11y / 空旷单焦点(大时间·待办轻量条·便签默认收起·看板收📋图标+角标·聊天⤢三态·小卡z置顶) / 拖拽落盘 / ✕最小化+卡皮巴拉恢复 / ⚖告警(事件演·回放不演) / 日夜壁纸四档 / reset / leave清痕");
console.log("✓ desk soul OK — 工位区(busy亮灯/idle呼吸/久静睡/无活动不摆) / 只读WS增量 / 工作证✓✗ / 署名便签3张cap / 叼卡→到位闪⚖→回窝 / leave全清 / API不通优雅隐藏");
