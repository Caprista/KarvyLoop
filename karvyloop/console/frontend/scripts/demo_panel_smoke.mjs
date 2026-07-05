/* demo_panel_smoke.mjs — 验证演示实例面板:契约 + open() 渲染真数据形状(jsdom,不触网)。
 * fixture 回放后端 /api/demo/instances 与 /api/demo/instance/{id} 的真实形状。
 * 断言(新版式,Hardy 拍板):①人设当大标题、诚实声明是小字脚注 ②7 张每日时间线卡渲染
 * ③递减参与曲线当高潮(D1 亲手多→D7 少 + 决策模式 冷→预对齐)④beliefs/知识是可展开的次级
 * 折叠区、不是首屏主体 ⑤只读注记 ⑥⤢放大-全屏三态(body-class 接缝)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <button id="demo-open">👀</button>
  <div id="mgmt-modal" class="modal-overlay hidden">
    <div class="modal"><div class="modal-head"><h2 id="mgmt-title"></h2>
    <button id="mgmt-close">✕</button></div><div id="mgmt-body"></div></div>
  </div>
</body>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
dom.window.KarvyI18n = { t: (k, vars) => k + (vars ? ":" + JSON.stringify(vars) : ""), getLang: () => "zh" };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");

const LIST = { instances: [
  { id: "lin-zh", lang: "zh", persona: { name: "小林" }, virtual_days: ["2026-06-28"], model: "m", built_at: "x", size_bytes: 1 },
  { id: "lin-en", lang: "en", persona: { name_en: "Lin" }, virtual_days: ["2026-06-28"], model: "m", built_at: "x", size_bytes: 1 },
] };
const OVERVIEW = {
  ok: true, id: "lin-zh",
  manifest: {
    persona: { name: "小林", age: 28, title: "自由撰稿人", beat: "科技+文化",
               style: "犀利、爱用比喻", routine: "早读午写下午改稿晚看书" },
    disclosure: { zh: "这是演示实例:人物「小林」为虚构;全部产物由真实机制跑出。", en: "Demo." },
    virtual_days: ["2026-06-28", "2026-07-04"], model: "minimax/MiniMax-M3",
    built_at: "2026-07-05", builder: "_local/build_demo_lin.py",
  },
  effort_curve: [
    { day: 1, day_label: "2026-06-28", hands_on_turns: 5, corrections: 2, decision_modes: [], new_skills: [] },
    { day: 4, day_label: "2026-07-01", hands_on_turns: 2, corrections: 0, decision_modes: [], new_skills: [] },
    { day: 7, day_label: "2026-07-04", hands_on_turns: 2, corrections: 0, decision_modes: ["pre_aligned_glance"], new_skills: [] },
  ],
  timeline: [
    { day: 1, day_label: "2026-06-28", hands_on_turns: 5, corrections: 2, decision_modes: [], entries: [
      { vtime: "06-28 08:40", channel: "晨读", intent: "喂料《AI 编码代理的生产力悖论》", written: 4 },
      { vtime: "06-28 11:30", channel: "写作助手", intent: "拟提纲:把《数字游民的祛魅》搭成三段式骨架", correction: false },
      { vtime: "06-28 12:10", channel: "写作助手", intent: "换个反常识例子", correction: true },
    ] },
    { day: 4, day_label: "2026-07-01", hands_on_turns: 2, corrections: 0, decision_modes: [], entries: [
      { vtime: "07-01 10:30", channel: "小卡", intent: "整理归档工作区", skill: "file-butler" },
    ] },
    { day: 7, day_label: "2026-07-04", hands_on_turns: 2, corrections: 0, decision_modes: ["pre_aligned_glance"], entries: [
      { vtime: "07-04 15:20", channel: "H2A", intent: "[route_to_role] 转给写作助手写初稿", decision: "ACCEPT", reason: "按老规矩,放行。", decision_mode: "pre_aligned_glance" },
    ] },
  ],
  workspace: {
    "数字游民的祛魅_提纲.md": { name: "数字游民的祛魅_提纲.md", snippet: "# 《数字游民的祛魅》三段式提纲\n字数预算:800字", bytes: 8046 },
  },
  growth: [
    { day: "2026-06-28", runs_total: 6, skills_total: 0, hit_rate: 0.16, avg_success_rate: 1.0 },
    { day: "2026-07-04", runs_total: 32, skills_total: 1, hit_rate: 0.18, avg_success_rate: 1.0 },
  ],
  day1: { day: "2026-06-28", runs_total: 6, skills_total: 0, hit_rate: 0.16, avg_success_rate: 1.0 },
  day7: { day: "2026-07-04", runs_total: 32, skills_total: 1, hit_rate: 0.18, avg_success_rate: 1.0 },
  skills: [
    { name: "draft-digital-nomad-debunk", sig: "abc", description: "写数字游民初稿", source: "user", tags: [], verified: false },
    { name: "study-buddy", sig: "system:study-buddy", description: "bundled", source: "system", tags: [], verified: true },
  ],
  skills_curve: [
    { sig: "system:study-buddy", name: "study-buddy", points: 3 },
    { sig: "abc", name: "draft-digital-nomad-debunk", points: 6 },
  ],
  decision_prefs: [
    { content: "选题未定不动笔", kind: "standing", strength: 0.7, status: "provisional", explicit: false },
    { content: "低于千字300一律拒", kind: "constraint", strength: 0.7, status: "provisional", explicit: false },
  ],
  role_experiences: [{ content: "开头比喻先狠后收", role: "写作助手", kind: "method" }],
  knowledge_total: 49,
  knowledge_recent: [{ content: "生产力瓶颈从写转移到信", source: "fed" }],
  taste: { n: 9, hits: 6, hit_rate: 0.667, wilson_lb: 0.35, gate_min_n: 35, gate_min_wilson_lb: 0.9, need_more: 26, earned: false },
  tokens_by_day: [{ day: "2026-06-28", input: 20000, output: 6000, calls: 25 }],
  conversations: { count: 3, turns: 40 },
};
dom.window.KarvyDom.getJSON = async (url) =>
  url.indexOf("/api/demo/instances") >= 0 ? LIST : OVERVIEW;
load("demo_panel.js");

const P = dom.window.KarvyDemoPanel;
assert.ok(P && typeof P.open === "function", "window.KarvyDemoPanel.open 契约缺失");

await P.open();
await new Promise((r) => setTimeout(r, 0));
const modal = dom.window.document.getElementById("mgmt-modal");
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
const text = body.textContent;

// ① 人设当大标题(.demo-persona-name = 名字),诚实声明降为小字脚注(.demo-disclosure)
const nameEl = body.querySelector(".demo-persona-name");
assert.ok(nameEl && nameEl.textContent.includes("小林"), "人设大标题(名字)缺失");
assert.ok(text.includes("自由撰稿人") && text.includes("科技+文化"), "人设 title/beat 没进大标题");
const disc = body.querySelector(".demo-disclosure");
assert.ok(disc && disc.textContent.includes("虚构"), "诚实声明没降为脚注(.demo-disclosure)");

// ② 递减参与曲线当高潮(.demo-hero + 迷你柱 + 首尾对比)
const hero = body.querySelector(".demo-hero");
assert.ok(hero, "递减曲线高潮条(.demo-hero)缺失");
const bars = body.querySelectorAll(".demo-curve-bar");
assert.ok(bars.length >= 3, "递减曲线柱状没渲染(demo-curve-bar)");
// 首柱(D1 亲手5)应比末柱(D7 亲手2)高 —— 递减弧一眼可见
const h1 = parseInt(bars[0].style.height), hLast = parseInt(bars[bars.length - 1].style.height);
assert.ok(h1 > hLast, `递减弧不成立:首柱${h1}% 应高于末柱${hLast}%`);
assert.ok(text.includes("demo.hero.punch"), "高潮点题句缺失");
// 决策模式漂移 冷→预对齐 + 静音门诚实门槛
assert.ok(text.includes("demo.mode.glance"), "预对齐抬眼点决策模式没展示");
assert.ok(text.includes("35"), "静音门门槛数字(35)没直出");

// ③ 7 张每日卡渲染(这里 fixture 给 3 天,断言 =timeline 长度、且 Day 号在)
const dayCards = body.querySelectorAll(".demo-day-card");
assert.equal(dayCards.length, 3, "每日卡数量与 timeline 不符");
assert.ok(text.includes("Day 1") && text.includes("Day 7"), "每日卡缺 Day 号");
assert.ok(text.includes("喂料") && text.includes("拟提纲"), "每日卡没渲染当天 intent");
// H2A 卡:决策 + 理由都在
assert.ok(text.includes("ACCEPT") && text.includes("按老规矩,放行"), "H2A 每日条决策/理由缺失");
// 纠正标记
assert.ok(text.includes("demo.tag.correction"), "纠正标记缺失");
// 产出稿件可点开(workspace 匹配到《数字游民的祛魅》)
const outBtn = body.querySelector(".demo-entry-output");
assert.ok(outBtn, "产出稿件『可点开』入口缺失");

// ④ beliefs/知识是可展开的次级折叠区(默认折叠、不是首屏主体)
const folds = body.querySelectorAll(".demo-fold");
assert.ok(folds.length >= 4, "细节折叠区缺失(决策偏好/技能/知识/成长)");
// 默认折叠:决策偏好内容此刻不应已渲染进 DOM(点开才建)
assert.ok(!text.includes("选题未定不动笔"), "决策偏好碎条不该是首屏主体(应折叠)");
// 点开决策偏好折叠 → 内容出现
const prefFold = [...folds].find((f) => f.textContent.includes("demo.prefs.head"));
assert.ok(prefFold, "决策偏好折叠头缺失");
prefFold.querySelector(".demo-fold-head").click();
assert.ok(body.textContent.includes("选题未定不动笔"), "点开折叠后决策偏好没出现");

// ⑤ 只读注记
assert.ok(text.includes("demo.readonly.note"), "只读注记缺失");

// ⑥ ⤢放大-全屏三态(body-class 接缝):按钮注入 + 循环 compact→expanded→full
const expandBtn = dom.window.document.getElementById("demo-modal-expand");
assert.ok(expandBtn, "⤢ 放大按钮没注入进模态标题栏");
expandBtn.click();
assert.ok(dom.window.document.body.classList.contains("demo-modal-expanded"), "第一下应进 expanded 态");
expandBtn.click();
assert.ok(dom.window.document.body.classList.contains("demo-modal-full"), "第二下应进 full 全屏态");
expandBtn.click();
assert.ok(!dom.window.document.body.classList.contains("demo-modal-expanded")
  && !dom.window.document.body.classList.contains("demo-modal-full"), "第三下应回 compact");

// ⑦ 双实例切换入口(lin-zh / lin-en)
assert.ok(text.includes("lin-en") || body.querySelectorAll(".demo-switch button").length >= 2,
  "双语实例切换入口缺失");

console.log("demo_panel smoke ✓");
