/* models_panel_smoke.mjs — 验证抽出的模型面板:契约(open + checkSetupGate)+ 真渲染模型列表/表单
 * + 强制引导锁(checkSetupGate must_setup → setSetupLocked(true) + 引导)(jsdom)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <div id="mgmt-modal" class="hidden"><h2 id="mgmt-title"></h2><button id="mgmt-close"></button><div id="mgmt-body"></div></div>
</body>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
dom.window.KarvyI18n = { t: (k) => k };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
// 喂罐头(load 前覆盖;可变 flag 切 must_setup)
let mustSetup = false;
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/model/config") return { models: [{ id: "anthropic/claude", provider: "anthropic", api: "anthropic-messages", context_window: 200000, has_key: true, api_key_masked: "sk-***", is_default_chat: true }], valid_apis: ["anthropic-messages", "openai-completions"] };
  if (url === "/api/search/config") return { mode: "keyless", providers: ["brave", "tavily"] };
  if (url === "/api/setup_status") return { must_setup: mustSetup };
  if (url === "/api/providers/presets") return { presets: [{ id: "anthropic", name: "Anthropic", api: "anthropic-messages", model_id: "claude", get_key_url: "https://x", key_env: "ANTHROPIC_API_KEY" }] };
  return null;
};
load("models_panel.js");

const M = dom.window.KarvyModelsPanel;
assert.ok(M && typeof M.open === "function" && typeof M.checkSetupGate === "function",
  "window.KarvyModelsPanel.{open,checkSetupGate} 契约缺失");

// 模型面板:渲染已配模型 + 新增表单 + 搜索配置
await M.open();
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(dom.window.document.getElementById("mgmt-title").textContent, "models.title", "标题应是 models.title");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("anthropic/claude")), "应渲染已配模型");
assert.ok(body.querySelector("form.mgmt-form"), "应有新增模型全字段表单(_modelForm)");
assert.ok([...body.querySelectorAll(".mgmt-section-title")].some((n) => n.textContent === "search.title"), "应有联网搜索配置区");

// 强制引导:must_setup=false → 不弹;=true → setSetupLocked(true) + 引导 provider 选择
let lockedTo = null;
dom.window.KarvyModal.setSetupLocked = (v) => { lockedTo = v; };
let pollHit = 0;
await M.checkSetupGate({ pollSnapshot: () => { pollHit++; } });
assert.equal(lockedTo, null, "must_setup=false 不该锁模态");
mustSetup = true;
await M.checkSetupGate({ pollSnapshot: () => { pollHit++; } });
assert.equal(lockedTo, true, "must_setup=true 应 setSetupLocked(true)(强制引导)");
assert.equal(dom.window.document.getElementById("mgmt-title").textContent, "setup.title", "强制引导标题应是 setup.title");

console.log("✓ models panel smoke OK — 契约 + 模型列表/表单/搜索配置 + 强制引导锁(must_setup→setSetupLocked)(不触网不崩)");
