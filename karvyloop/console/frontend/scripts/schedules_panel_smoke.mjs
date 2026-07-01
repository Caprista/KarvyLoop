/* schedules_panel_smoke.mjs — 验证抽出的定时任务面板:契约 + open() 接通模态(jsdom,不触网)。 */
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
load("schedules_panel.js");

const S = dom.window.KarvySchedulesPanel;
assert.ok(S && typeof S.open === "function", "window.KarvySchedulesPanel.open 契约缺失");

const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
await S.open();   // _getJSON 触网失败→[]→空列表,不崩
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "sched.title", "标题应是 sched.title");
assert.ok(dom.window.document.getElementById("mgmt-body").children.length >= 1, "body 应有内容(NL 创建区/空提示)");

console.log("✓ schedules panel smoke OK — 契约 + open() 接通模态(不触网不崩)");
