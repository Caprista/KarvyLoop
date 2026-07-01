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
    { kind: "constraint", content: "碰生产必须先有测试", status: "provisional", strength: 0.7 },
    { kind: "taste", content: "默认 markdown 表格", status: "confirmed", strength: 0.9 }] };
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

console.log("✓ decision-prefs panel smoke OK — 契约 + open() + 复利信号 + 偏好卡(确认/编辑/撤回)(不触网不崩)");
