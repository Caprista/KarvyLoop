/* desktop_smoke.mjs — 真路径验证桌面视图壳(docs/51 P1)。
 * jsdom 里加载 static/desktop.js:dock 同构渲染(12 入口)、enter/leave 幂等与清痕、
 * 拖拽落 localStorage(karvyloop_desk.v1)、⚖ notifyH2A 置顶+闪烁、resetLayout 清存档。
 * jsdom 无真布局(rect 全 0)→ clamp 走"无布局环境不 clamp"分支,坐标语义仍可断言。
 */
import { JSDOM } from "jsdom";
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
        <div id="chat-input" contenteditable="true"></div>
      </div>
    </div>
    <div class="cockpit-grid">
      <section class="cockpit-col col-decide"><h2 class="col-head">⚖</h2><div class="col-scroll"><div id="h2a-list"></div></div></section>
      <section class="cockpit-col col-intel"><h2 class="col-head">📥</h2><div class="col-scroll"><div id="task-board"></div></div></section>
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

const dom = new JSDOM(HTML, { url: "http://localhost/", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);

const here = dirname(fileURLToPath(import.meta.url));
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

// ---- ⚖ notifyH2A:置顶 + note-alert 闪烁 + 卡皮巴拉冒泡 ----
const zBefore = parseInt(decide.style.zIndex, 10);
KD.notifyH2A();
assert.ok(decide.classList.contains("note-alert"), "新决策卡到达应给 ⚖ 便签加 note-alert");
assert.ok(parseInt(decide.style.zIndex, 10) > zBefore, "notifyH2A 应把 ⚖ 便签置顶(z 递增)");
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

// ---- leave:清干净全部内联痕迹(两个老视图像素级不动的保险)----
document.body.classList.remove("desk-view");
KD.leave();
assert.equal(decide.style.transform, "", "leave 后便签 transform 未清");
assert.equal(chatPanel.style.transform, "", "leave 后聊天窗 transform 未清");
assert.ok(!document.getElementById("chat-modal").classList.contains("desk-min"), "leave 后 desk-min 未清");
assert.equal(decide.querySelector(".col-head").getAttribute("tabindex"), null, "leave 后 tabindex 未清");

console.log("✓ desktop smoke OK — dock 12 入口同构 / enter定位+a11y / 拖拽落盘 / ✕最小化+卡皮巴拉恢复 / ⚖告警 / reset / leave清痕");
