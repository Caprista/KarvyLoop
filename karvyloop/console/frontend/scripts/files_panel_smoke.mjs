/* files_panel_smoke.mjs — 验证抽出的文件面板:契约 + open() 接通模态(jsdom,不触网)。 */
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
// i18n 桩(t 回 key);文件面板依赖 dom/modal,先加载它们
dom.window.KarvyI18n = { t: (k) => k };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
load("files_panel.js");

const F = dom.window.KarvyFilesPanel;
assert.ok(F && typeof F.open === "function", "window.KarvyFilesPanel.open 契约缺失");

const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
// open():开模态 + 渲染(_getJSON 触网失败→null→空面板,不崩)
await F.open();
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "files.title", "标题应是 files.title");
assert.ok(dom.window.document.getElementById("mgmt-body").children.length >= 1, "body 应有内容(标题/空提示)");

console.log("✓ files panel smoke OK — 契约 + open() 接通模态(不触网不崩)");
