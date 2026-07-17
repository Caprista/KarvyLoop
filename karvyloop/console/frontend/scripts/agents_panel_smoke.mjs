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
dom.window.KarvyI18n = { t: (k) => k, getLang: () => "en" };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
// J2:覆盖 postJSON 模拟三型导入返回(agents_panel 在 load 时捕获 KarvyDom.postJSON,故须先覆盖)。
let _nextRes = { ok: true, status: 200, data: {} };
dom.window.KarvyDom.postJSON = async () => _nextRes;
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

// ---- J2:三型导入结果如实显示(不再无脑 imported✓)----
// 单 agent 表单是第一个 form;拿它的 submit 按钮 + msg + detail 容器。
const singleForm = body.querySelector("form.mgmt-form");
const importBtn = [...singleForm.querySelectorAll("button")]
  .find((b) => b.textContent === "agent.import_btn");
assert.ok(importBtn, "找不到单 agent 导入按钮");
const msgEl = singleForm.querySelector(".mgmt-msg");
const detailEl = singleForm.querySelector(".agent-import-detail");
assert.ok(msgEl && detailEl, "表单应有 .mgmt-msg + .agent-import-detail 结果容器");

const flush = () => new Promise((r) => setTimeout(r, 0));
async function runImport(payload) {
  _nextRes = { ok: true, status: 200, data: payload };
  importBtn.click();          // onclick 是 async:click() 先返回,await 冲刷微任务
  await flush(); await flush();
}

// (1) executor(pure_executor):没建 role → 不能绿 ✓、不能报"进角色库",显示后端诚实 note + 列原子
const EXEC_NOTE = "This agent is a pure executor — no role seat; 2 atoms landed.";
await runImport({
  ok: true, role_id: "imported_x", decomposed: true,
  agent_kind: "executor", import_kind: "pure_executor", note: EXEC_NOTE,
  atoms: ["read_file", "run_shell"], atoms_created: ["run_shell"],
  atoms_advisory: [], atoms_executable: ["read_file", "run_shell"],
  skills_recognized: ["deploy_flow"], skills_bound: [], identity: "x",
});
assert.equal(msgEl.textContent, EXEC_NOTE, "executor:应展示后端诚实 note 原文");
assert.ok(!msgEl.className.includes("ok"), "executor:绝不能是绿 ✓ 成功态(假成功病)");
assert.ok(!msgEl.textContent.includes("agent.imported"), "executor:绝不能报'已进角色库'");
assert.ok(detailEl.textContent.includes("read_file") && detailEl.textContent.includes("run_shell"),
  "executor:应如实列落的原子名");
assert.ok(detailEl.textContent.includes("deploy_flow"), "executor:应列识别出的 skill 名");

// (2) skill(skill_like):角色库/原子库都没写 → 中性 note + 指路技能库
const SKILL_NOTE = "This is essentially a skill — import it through the skill library.";
await runImport({
  ok: true, role_id: "imported_y", decomposed: true,
  agent_kind: "skill", import_kind: "skill_like", note: SKILL_NOTE,
  atoms: [], atoms_created: [], skills_recognized: ["writeup"], skills_bound: [], identity: "y",
});
assert.equal(msgEl.textContent, SKILL_NOTE, "skill:应展示后端诚实 note 原文");
assert.ok(!msgEl.className.includes("ok"), "skill:绝不能是绿 ✓ 成功态");
assert.ok(!msgEl.textContent.includes("agent.imported"), "skill:绝不能报'已进角色库'");
assert.ok(detailEl.textContent.includes("writeup"), "skill:应列识别出的 skill 名");

// (3) decision:真建了 role → 绿 ✓「已建角色」
await runImport({
  ok: true, role_id: "imported_z", decomposed: true,
  agent_kind: "decision", import_kind: "tool_agent", note: "",
  atoms: ["ask"], atoms_created: [], skills_recognized: [], skills_bound: [], identity: "z",
});
assert.ok(msgEl.className.includes("ok"), "decision:真建 role 才是绿 ✓ 成功态");
assert.equal(msgEl.textContent, "agent.imported", "decision:应报'已进角色库'(t 桩回 key)");

console.log("✓ agents panel smoke OK — 契约 + 三型导入如实显示(executor/skill 不假报进角色库)");
