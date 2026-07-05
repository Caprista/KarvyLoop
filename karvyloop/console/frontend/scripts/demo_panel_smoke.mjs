/* demo_panel_smoke.mjs — 验证演示实例面板:契约 + open() 渲染真数据形状(jsdom,不触网)。
 * fixture 回放后端 /api/demo/instances 与 /api/demo/instance/{id} 的真实形状。
 * 断言:诚实 banner 常驻(demo.banner)/ Day1 vs Day7 对比表 / 决策偏好与技能列表 /
 * 静音门进度是"在爬"不是"已挣到"(gate 数字直出)/ 只读注记。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <button id="demo-open">👀</button>
  <div id="mgmt-modal" class="hidden"><h2 id="mgmt-title"></h2><div id="mgmt-body"></div></div>
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
    persona: { name: "小林", age: 28, title: "自由撰稿人", beat: "科技+文化", style: "犀利", routine: "早读午写" },
    disclosure: { zh: "这是演示实例:人物「小林」为虚构;全部产物由真实机制跑出。", en: "Demo." },
    honest_notes: { zh: ["静音门 7 天到不了"] },
    virtual_days: ["2026-06-28", "2026-07-04"], model: "minimax/MiniMax-M3",
    built_at: "2026-07-05", builder: "_local/build_demo_lin.py",
  },
  growth: [
    { day: "2026-06-28", runs_total: 4, skills_total: 0, hit_rate: 0.25, avg_success_rate: 1.0 },
    { day: "2026-07-04", runs_total: 30, skills_total: 2, hit_rate: 0.4, avg_success_rate: 0.95 },
  ],
  day1: { day: "2026-06-28", runs_total: 4, skills_total: 0, hit_rate: 0.25, avg_success_rate: 1.0 },
  day7: { day: "2026-07-04", runs_total: 30, skills_total: 2, hit_rate: 0.4, avg_success_rate: 0.95 },
  day1_extra: { knowledge: 9, prefs: 0 },
  day7_extra: { knowledge: 41, prefs: 5 },
  skills: [
    { name: "column-outline", sig: "abc", description: "800字专栏提纲", source: "user", tags: [], verified: true },
    { name: "study-buddy", sig: "system:study-buddy", description: "bundled", source: "system", tags: [], verified: true },
  ],
  skills_curve: [
    { sig: "system:study-buddy", name: "study-buddy", points: [{}] },
    { sig: "abc", name: "column-outline", points: [{}] },
  ],
  decision_prefs: [
    { content: "选题未定不动笔", kind: "constraint", strength: 0.8, status: "confirmed", explicit: true },
    { content: "标题要反常识角度", kind: "taste", strength: 0.7, status: "provisional", explicit: false },
  ],
  role_experiences: [{ content: "开头比喻先狠后收", role: "写作助手", kind: "method" }],
  knowledge_total: 41,
  knowledge_recent: [{ content: "生产力瓶颈从写转移到信", source: "fed" }],
  taste: { n: 9, hits: 6, hit_rate: 0.667, wilson_lb: 0.35, gate_min_n: 35, gate_min_wilson_lb: 0.9, earned: false },
  tokens_by_day: [{ day: "2026-06-28", input: 20000, output: 6000, calls: 25 }],
  conversations: { count: 5, turns: 40 },
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

// ① 诚实 banner:面板标注 + manifest disclosure 原文都在
assert.ok(text.includes("demo.banner"), "缺诚实 banner(demo.banner)");
assert.ok(text.includes("虚构"), "缺 manifest disclosure(虚构人物声明)");
// ② Day1 vs Day7 对比表
assert.ok(text.includes("demo.compare.head"), "缺对比表标题");
assert.ok(text.includes("demo.m.skills") && text.includes("demo.m.hit_rate"), "对比表缺指标行");
// ③ 决策偏好两个方向都展示(constraint + taste)
assert.ok(text.includes("选题未定不动笔") && text.includes("标题要反常识角度"), "决策偏好没渲染全");
// ④ 技能:用户结晶的列出、系统技能作复用行
assert.ok(text.includes("column-outline"), "用户技能没列出");
assert.ok(text.includes("demo.skills.system"), "系统技能复用行缺失");
// ⑤ 静音门:诚实"在爬",门槛数字(35 / 0.9)直出
assert.ok(text.includes("demo.taste.progress"), "静音门进度行缺失");
assert.ok(text.includes("35") && text.includes("0.9"), "静音门槛数字没直出");
// ⑥ 只读注记
assert.ok(text.includes("demo.readonly.note"), "只读注记缺失");
// ⑦ 双实例切换按钮(lin-zh / lin-en)
assert.ok(text.includes("lin-en") || body.querySelectorAll(".demo-switch button").length >= 2,
  "双语实例切换入口缺失");

console.log("demo_panel smoke ✓");
