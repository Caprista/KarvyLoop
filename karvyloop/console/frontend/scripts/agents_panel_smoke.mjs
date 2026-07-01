/* agents_panel_smoke.mjs — 验证抽出的 Agent 导入面板:契约 + open(deps) 接通模态 + 导入表单(jsdom,不触网)。 */
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
load("agents_panel.js");

const A = dom.window.KarvyAgentsPanel;
assert.ok(A && typeof A.open === "function", "window.KarvyAgentsPanel.open 契约缺失");

await A.open({ refreshPeers: () => {} });
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "mgmt.agents_title", "标题应是 mgmt.agents_title");
const body = dom.window.document.getElementById("mgmt-body");
assert.ok(body.querySelector("form.mgmt-form"), "应有导入表单");
assert.ok(body.querySelector("select"), "导入表单应有来源类型 select");
assert.ok(body.querySelector("textarea"), "导入表单应有 system_prompt textarea");

console.log("✓ agents panel smoke OK — 契约 + open(deps) 接通模态 + 导入表单(不触网不崩)");
