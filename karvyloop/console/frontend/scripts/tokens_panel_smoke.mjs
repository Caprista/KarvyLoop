/* tokens_panel_smoke.mjs — 验证抽出的 token 成本表:契约 + pollMeter 刷顶栏 + open() 弹窗(总量/各模型/各功能)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <span id="token-meter"></span>
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
  if (url === "/api/tokens") return {
    totals: { input: 12000, output: 3400, calls: 42, cost_usd: 0.85 },
    by_model: [{ model: "claude", input: 12000, output: 3400, total: 15400, calls: 42 }],
    by_source: [{ source: "forge", input: 8000, output: 2000, total: 10000, calls: 20 }],
  };
  return null;
};
load("tokens_panel.js");

const K = dom.window.KarvyTokens;
assert.ok(K && typeof K.pollMeter === "function" && typeof K.open === "function", "window.KarvyTokens.{pollMeter,open} 契约缺失");

// pollMeter:顶栏 meter 显示 tok + 成本 + 模型
await K.pollMeter();
const meter = dom.window.document.getElementById("token-meter");
assert.ok(meter.textContent.includes("15.4k") && meter.textContent.includes("💰"), "meter 应显示总量(15.4k tok)");
assert.ok(meter.textContent.includes("claude"), "meter 应带默认模型名");

// open:弹窗 = 总量卡 + 各模型表 + 各功能表(护城河:成本可见)
await K.open();
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(dom.window.document.getElementById("mgmt-title").textContent, "tokens.title", "标题应是 tokens.title");
assert.ok(body.querySelector(".tok-summary .tok-big"), "应有总量卡");
assert.ok(body.querySelectorAll("table.tok-table").length >= 2, "应有各模型 + 各功能两张表");
assert.ok([...body.querySelectorAll("h3.tok-h")].some((n) => n.textContent === "tokens.by_source"), "应有『各功能花在哪』表(KarvyLoop 专属)");

console.log("✓ tokens panel smoke OK — 契约 + pollMeter 刷顶栏 + open() 弹窗(总量/各模型/各功能)(不触网不崩)");
