/* roles_panel_smoke.mjs — 验证角色面板大改:列表(搜索/分页/＋新建)+ 创建页(穿梭框)+ 编辑页(全范式 soul 编辑器)。 */
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
  if (url === "/api/roles") return { roles: [{ id: "pm", identity: "产品经理", atom_ids: ["web_search"], skill_ids: ["做PPT"] }] };
  if (url === "/api/atoms") return { atoms: [{ id: "web_search" }, { id: "read_file" }] };
  if (url === "/api/skills") return { skills: [{ name: "做PPT" }, { name: "写周报" }] };
  if (url === "/api/models") return { models: [{ id: "claude", name: "Claude" }], default: "claude" };
  if (url.indexOf("/api/role/paradigm/gaps") === 0) return { ok: true, gaps: ["VERIFY"],
    suggestions: { VERIFY: "验证:产出必须带出处与可复现步骤" }, complete: false };
  if (url.indexOf("/api/role/paradigm") === 0) return { ok: true, paradigm: {
    identity: "产品经理", soul: "以用户为中心", user: "创始人", memory: "(运行时记忆)",
    commitment: "尽责协作契约默认…", verify: "(待充实)",
    atom_ids: ["web_search"], skill_ids: ["做PPT"],
    editable_slots: ["IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY"] } };
  return null;
};
load("roles_panel.js");

const R = dom.window.KarvyRolesPanel;
assert.ok(R && typeof R.open === "function", "window.KarvyRolesPanel.open 契约缺失");
const body = () => dom.window.document.getElementById("mgmt-body");

// ① 列表视图:＋新建 + 搜索 + 渲染角色卡(不再直接是表单)
await R.open();
assert.ok(body().querySelector(".mgmt-new-btn"), "列表应有「＋新建」按钮");
assert.ok(body().querySelector(".paged-search"), "列表应有搜索框");
assert.ok([...body().querySelectorAll(".mc-name")].some((n) => n.textContent === "pm"), "应渲染角色卡 pm");
assert.ok(!body().querySelector("form.mgmt-form"), "列表视图不该直接有表单");

// ② 点「＋新建」→ 创建页:atom/skill 用穿梭框(不是 chip 气泡)
body().querySelector(".mgmt-new-btn").click();
await new Promise((r) => setTimeout(r, 0));
assert.ok(body().querySelector("form.mgmt-form"), "＋新建应进创建页");
assert.ok(body().querySelectorAll(".xfer").length >= 2, "创建页 atom+skill 应是两个穿梭框(非 chip)");
assert.ok(body().querySelector(".mgmt-buysugar"), "创建页应保留就地买糖");

// ③ 回列表 → 点「查看编辑」→ 全范式编辑器:5 个可编辑灵魂槽 textarea + MEMORY 只读 + atoms/skills 穿梭框
await R.open();
await new Promise((r) => setTimeout(r, 0));
const editBtn = [...body().querySelectorAll(".dpref-edit")].find((b) => b.textContent === "role.view_edit");
assert.ok(editBtn, "角色卡应有『查看编辑』");
editBtn.click();
await new Promise((r) => setTimeout(r, 5));
const eb = body();
assert.ok(eb.querySelectorAll(".soul-slot").length >= 6, `全范式编辑器应有 5 可编辑槽 + MEMORY(实际 ${eb.querySelectorAll(".soul-slot").length})`);
assert.ok(eb.querySelector(".soul-ro"), "MEMORY 应是只读展示");
assert.ok([...eb.querySelectorAll("textarea")].some((ta) => ta.value.includes("尽责协作契约")), "应加载出 COMMITMENT 契约正文可编辑");
assert.ok(eb.querySelectorAll(".xfer").length >= 2, "编辑器 atoms+skills 应是穿梭框");

// #2 补全范式:VERIFY 槽是空的"(待充实)";点「🪄 补全范式」→ 调 /gaps → 草稿填进空槽(待你核对保存)
const completeBtn = [...eb.querySelectorAll("button")].find((b) => b.textContent === "role.complete_btn");
assert.ok(completeBtn, "全范式编辑器应有「🪄 补全范式」按钮");
const verifyTa = [...eb.querySelectorAll("textarea")].find((ta) => ta.value.trim() === "(待充实)");
assert.ok(verifyTa, "VERIFY 槽应是空 stub『(待充实)』");
completeBtn.click();
await new Promise((r) => setTimeout(r, 5));
assert.ok(verifyTa.value.includes("可复现"), "点补全后空的 VERIFY 槽应填进 LLM 起草的草稿");

console.log("✓ roles panel smoke OK — 列表 + 创建页 + 全范式编辑器 + 🪄补全范式(空槽填草稿)");
