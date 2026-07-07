/* decision_prefs_panel_smoke.mjs — 验证抽出的决策偏好面:契约 + open() + 复利信号 + 偏好卡(确认/编辑/撤回)。 */
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
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/decision_prefs/stats") return { prefs_total: 3, confirmed: 1, decisions_total: 12 };
  if (url === "/api/decision_prefs") return { prefs: [
    { kind: "constraint", content: "碰生产必须先有测试", status: "provisional", strength: 0.7,
      evidence_n: 2, evidence: [
        { ts: 1751100000, decision: "ACCEPT", gist: "按你说的先加了测试" },
        { ts: 123.0, decision: "", gist: "" }] },                       // 旧数据:只有时间戳
    { kind: "taste", content: "默认 markdown 表格", status: "confirmed", strength: 0.9,
      evidence_n: 0, evidence: [] }] };
  return null;
};
load("decision_prefs_panel.js");

const D = dom.window.KarvyDecisionPrefs;
assert.ok(D && typeof D.open === "function", "window.KarvyDecisionPrefs.open 契约缺失");

await D.open();
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(dom.window.document.getElementById("mgmt-title").textContent, "dpref.title", "标题应是 dpref.title");
assert.ok(body.querySelector(".dpref-signal"), "应有复利信号行(教会几条/接受率)");
assert.ok([...body.querySelectorAll(".dpref-content")].some((n) => n.textContent.includes("碰生产必须先有测试")), "应渲染决策偏好卡");
// provisional 的有「确认」按钮;每条都有「撤回」(易撤回·不固化你)
assert.ok(body.querySelector(".dpref-confirm"), "provisional 偏好应有确认按钮");
assert.ok([...body.querySelectorAll(".mc-del")].some((b) => b.textContent === "dpref.revoke"), "每条应有撤回按钮");

// Q3 证据可见:每条偏好有展开钮,点开显"从你哪几次拍板学来";无证据显诚实文案
const toggles = [...body.querySelectorAll(".dpref-ev-toggle")];
assert.equal(toggles.length, 2, "每条偏好都应有证据展开钮");
const panels = [...body.querySelectorAll(".dpref-evidence")];
assert.ok(panels.every((p) => p.classList.contains("hidden")), "证据默认收起");
toggles[0].dispatchEvent(new dom.window.Event("click"));
assert.ok(!panels[0].classList.contains("hidden"), "点展开钮后证据面应显出");
const lines = [...panels[0].querySelectorAll(".dpref-ev-line")].map((n) => n.textContent);
assert.equal(lines.length, 2, "有 2 条证据就显 2 行");
assert.ok(lines[0].includes("dpref.ev_accept") && lines[0].includes("按你说的先加了测试"),
  "证据行应是人话:何时 · 你拍了什么 — 摘要");
assert.ok(lines[1].includes("dpref.ev_no_detail"), "旧数据(只有时间戳)应诚实说没存明细,不编");
toggles[1].dispatchEvent(new dom.window.Event("click"));
assert.ok(panels[1].querySelector(".dpref-ev-empty"), "无证据的偏好点开应显诚实空文案");
assert.equal(panels[1].querySelector(".dpref-ev-empty").textContent, "dpref.ev_empty");
toggles[0].dispatchEvent(new dom.window.Event("click"));
assert.ok(panels[0].classList.contains("hidden"), "再点一次应收起");

console.log("✓ decision-prefs panel smoke OK — 契约 + open() + 复利信号 + 偏好卡(确认/编辑/撤回)+ 证据展开(不触网不崩)");
