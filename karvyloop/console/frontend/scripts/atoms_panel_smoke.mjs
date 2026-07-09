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
  if (url === "/api/atoms") return { atoms: [
    { id: "web_search", kind: "task", prompt: "搜网", tools: ["run_command"] },
    { id: "web_lookup", kind: "task", prompt: "查网", tools: ["run_command"] },
  ] };
  return null;
};
// 整理相似原子:suggest 出一簇合并建议 → apply 兑现(镜像知识库同款)
dom.window.KarvyDom.postJSON = async (url) => {
  if (url === "/api/atoms/consolidate/suggest") return { ok: true, status: 200, data: { ok: true, clusters: [
    { canonical_id: "web_search", member_ids: ["web_search", "web_lookup"],
      merged_purpose: "搜网", merged_tools: ["run_command"], reason: "同一件事" },
  ] } };
  if (url === "/api/atoms/consolidate/apply") return { ok: true, status: 200, data: { ok: true, removed_atoms: ["web_lookup"] } };
  return { ok: true, status: 200, data: { ok: true } };
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

// 整理相似原子:工具栏有按钮 → 点它出合并建议簇 → 点「合并」调 apply → 卡片变完成提示
await A.open();
await new Promise((r) => setTimeout(r, 0));
const consBtn = dom.window.document.getElementById("mgmt-body").querySelector(".atom-consolidate-btn");
assert.ok(consBtn, "工具栏应有「整理相似原子」按钮(≥2 原子时,镜像知识库同款)");
consBtn.click();
await new Promise((r) => setTimeout(r, 0));
const cb = dom.window.document.getElementById("mgmt-body");
const card = cb.querySelector(".consolidate-card");
assert.ok(card, "suggest 应渲染合并建议簇卡片");
assert.ok([...card.querySelectorAll(".consolidate-member")].some((m) => m.textContent.includes("web_lookup")), "簇应列出被并成员原子 id");
const doBtn = card.querySelector(".dpref-confirm");
assert.ok(doBtn, "簇卡应有「合并」按钮");
doBtn.click();
await new Promise((r) => setTimeout(r, 0));
assert.ok(!cb.querySelector(".consolidate-card"), "点合并后簇卡应替换为完成提示");

console.log("✓ atoms panel smoke OK — 列表(＋新建/搜索/分页)+ 创建页 + 编辑页(id 只读·预填,可改)+ 整理相似原子(suggest→合并)");
