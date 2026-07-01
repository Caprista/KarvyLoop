/* diagnose_panel_smoke.mjs — 验证抽出的诊断面板:契约 + open(deps) 接通模态 + renderOpsDiagnosis 纯渲染(jsdom,不触网)。 */
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
load("diagnose_panel.js");

const D = dom.window.KarvyDiagnosePanel;
assert.ok(D && typeof D.open === "function", "window.KarvyDiagnosePanel.open 契约缺失");
assert.ok(typeof D.renderOpsDiagnosis === "function", "renderOpsDiagnosis 契约缺失(onSystemError 复用)");

// renderOpsDiagnosis 纯渲染:不触网,渲染人话卡 + 风险标
const log = dom.window.document.createElement("div");
D.renderOpsDiagnosis(log, { summary: "磁盘满了", cause: "日志没轮转", fix: "清旧日志", risk: "reversible" });
assert.ok(log.querySelector(".ops-diag"), "应渲染诊断卡 .ops-diag");
assert.ok(log.querySelector(".ops-risk-reversible"), "应带可逆风险标");
assert.ok(log.textContent.includes("磁盘满了"), "应含 summary 文案");

// open(deps):注入的 deps 不崩;_getJSON 触网失败→空→走 ops.failed 分支,不崩
let pushed = 0, fetched = 0;
await D.open({ pushChatLine: () => { pushed++; }, fetchPendingProposals: () => { fetched++; } });
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "diag.title", "标题应是 diag.title");
assert.ok(dom.window.document.getElementById("mgmt-body").children.length >= 1, "body 应有内容");

console.log("✓ diagnose panel smoke OK — 契约 + open(deps) 接通模态 + renderOpsDiagnosis 纯渲染(不触网不崩)");
