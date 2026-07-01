/* atoms_panel_smoke.mjs — 验证原子面板:open→列表(＋新建按钮)→点击进创建页(create/list 分离)。 */
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
load("ui_widgets.js");
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/atoms") return { atoms: [{ id: "web_search", kind: "task", prompt: "搜网", tools: ["run_command"] }] };
  return null;
};
load("atoms_panel.js");

const A = dom.window.KarvyAtomsPanel;
assert.ok(A && typeof A.open === "function", "window.KarvyAtomsPanel.open 契约缺失");

await A.open();
const modal = dom.window.document.getElementById("mgmt-modal");
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
// 列表视图:有「＋新建」按钮 + 分页搜索 + 渲染原子(不再直接是表单)
assert.ok(body.querySelector(".mgmt-new-btn"), "列表视图应有「＋新建」按钮(创建/列表分离)");
assert.ok(body.querySelector(".paged-search"), "列表应有搜索框");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("web_search")), "应渲染原子列表项");
assert.ok(!body.querySelector("form.mgmt-form"), "列表视图不该直接有创建表单");
// 点「＋新建」→ 进创建页(有表单)
body.querySelector(".mgmt-new-btn").click();
await new Promise((r) => setTimeout(r, 0));
const body2 = dom.window.document.getElementById("mgmt-body");
assert.ok(body2.querySelector("form.mgmt-form"), "点＋新建应进创建页(有表单)");
assert.ok(body2.querySelector("textarea"), "创建页应有 prompt textarea");

// #1 原子可编辑:回列表 → 点「编辑」→ 预填表单(id 只读、prompt 带原值)
await A.open();
await new Promise((r) => setTimeout(r, 0));
const editBtn = [...dom.window.document.getElementById("mgmt-body").querySelectorAll(".dpref-edit")].find((b) => b.textContent === "mgmt.edit");
assert.ok(editBtn, "原子卡应有「编辑」按钮(此前只能删了重建)");
editBtn.click();
await new Promise((r) => setTimeout(r, 0));
const eb = dom.window.document.getElementById("mgmt-body");
const idInput = eb.querySelector('input[type="text"]');
assert.ok(idInput.readOnly && idInput.value === "web_search", "编辑页 id 应只读且带原值");
assert.ok([...eb.querySelectorAll("textarea")].some((ta) => ta.value.includes("搜网")), "编辑页 prompt 应预填原值");

console.log("✓ atoms panel smoke OK — 列表(＋新建/搜索/分页)+ 创建页 + 编辑页(id 只读·预填,可改)");
