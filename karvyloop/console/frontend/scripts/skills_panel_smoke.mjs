/* skills_panel_smoke.mjs — 验证抽出的技能库面板:契约 + open() 接通模态 + 喂罐头真渲染列表/详情(jsdom)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <div id="mgmt-modal" class="hidden"><h2 id="mgmt-title"></h2><div id="mgmt-body"></div></div>
</body>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
dom.window.KarvyI18n = { t: (k) => k };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
// 喂罐头(模块加载时 const 捕获 _getJSON → 覆盖要在 load skills_panel 之前)
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/skills") return { skills: [
    { name: "做PPT", when_to_use: "要做演示", status: "crystallized", sig: "s1", recall_count: 3, usage_count: 5, success_count: 4 }] };
  if (url === "/api/coding/capability") return { tools: [{ name: "read_file", kind: "builtin", description: "读文件" }] };
  if (url === "/api/skills/curve") return { bucket: "day", promote_score: 3.0, min_success_rate: 0.8,
    skills: [{ sig: "s1", name: "做PPT", crystallized_ts: 200.0, points: [
      { day: "2026-06-14", ts: 100.0, usage_count: 1, success_count: 1, usage_score: 1.0, success_rate: 1.0, promote_progress: 0.33, reruns: 0, crystallized: false },
      { day: "2026-06-15", ts: 200.0, usage_count: 5, success_count: 4, usage_score: 4.2, success_rate: 0.8, promote_progress: 1.0, reruns: 2, crystallized: true }] }],
    growth: { points: [
      { day: "2026-06-14", ts: 100.0, skills_total: 0, promotions: 0, revisions: 0, runs_total: 1, avg_success_rate: 1.0, hit_rate: 0.0 },
      { day: "2026-06-15", ts: 200.0, skills_total: 1, promotions: 1, revisions: 0, runs_total: 5, avg_success_rate: 0.8, hit_rate: 0.4 }] } };
  return null;
};
load("skills_panel.js");

const S = dom.window.KarvySkillsPanel;
assert.ok(S && typeof S.open === "function", "window.KarvySkillsPanel.open 契约缺失");

await S.open();
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "skills.title", "标题应是 skills.title");
const body = dom.window.document.getElementById("mgmt-body");
assert.ok(body.querySelector(".skill-catalog-wrap"), "应有导入/目录区");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("做PPT")), "应渲染出技能列表项");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("coding.name")), "应渲染内建 Coding 能力卡");
// docs/57 P1 结晶裸分曲线:顶部全库成长曲线 + 每技能迷你 sparkline(纯 SVG 手画)
assert.ok(body.querySelector(".skill-growth"), "应有全库成长曲线区(skill-growth)");
assert.ok(body.querySelector(".skill-growth-chart polyline"), "成长曲线应画出 polyline");
assert.ok(body.querySelector(".skill-spark"), "技能卡应有迷你 sparkline(skill-spark)");

console.log("✓ skills panel smoke OK — 契约 + open() 接通模态 + 真渲染技能列表 + Coding 能力卡 + 成长曲线/sparkline(不触网不崩)");
