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
// 喂罐头(load 前覆盖;可变对象切 setup_status 场景 —— CFG-05 后 boot 走 ?live=1)
let setupStatus = { must_setup: false };
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/model/config") return { models: [{ id: "anthropic/claude", provider: "anthropic", api: "anthropic-messages", context_window: 200000, has_key: true, api_key_masked: "sk-***", is_default_chat: true }], valid_apis: ["anthropic-messages", "openai-completions"] };
  if (url === "/api/search/config") return { mode: "keyless", providers: ["brave", "tavily"] };
  if (url.startsWith("/api/setup_status")) return setupStatus;
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
// CFG-01①:模型设置窗声明"点空白不关"(✕/Esc 仍可关 —— modal_smoke 验行为,这里验声明)
assert.equal(dom.window.KarvyModal.backdropCloseEnabled(), false, "模型设置窗应禁点空白关闭");

// 强制引导:must_setup=false → 不弹;=true → setSetupLocked(true) + 引导 provider 选择
let lockedTo = null;
dom.window.KarvyModal.setSetupLocked = (v) => { lockedTo = v; };
let pollHit = 0;
await M.checkSetupGate({ pollSnapshot: () => { pollHit++; } });
assert.equal(lockedTo, null, "must_setup=false 不该锁模态");
setupStatus = { must_setup: true };
await M.checkSetupGate({ pollSnapshot: () => { pollHit++; } });
assert.equal(lockedTo, true, "must_setup=true 应 setSetupLocked(true)(强制引导)");
assert.equal(dom.window.document.getElementById("mgmt-title").textContent, "setup.title", "强制引导标题应是 setup.title");

// ---- CFG-05:live 校验三态(配置在 ≠ 能用)----
const modal = dom.window.document.getElementById("mgmt-modal");

// ③ 配置在 + 真验通过 → 不设门
modal.classList.add("hidden"); lockedTo = null;
setupStatus = { must_setup: false, live_checked: true, live_ok: true, live_model: "anthropic/claude" };
await M.checkSetupGate({ pollSnapshot: () => {} });
assert.equal(lockedTo, null, "live_ok=true 不该弹 gate");

// ④ key 坏(bad_key)→ 回 setup gate,带诚实原因(哪个模型/什么错)
setupStatus = { must_setup: false, live_checked: true, live_ok: false,
  live_model: "anthropic/claude", live_reason: "401 unauthorized", live_error_class: "bad_key" };
await M.checkSetupGate({ pollSnapshot: () => {} });
assert.equal(lockedTo, true, "live bad_key 应锁回 setup gate");
assert.equal(modal.classList.contains("hidden"), false, "gate 应可见");
const failLine = body.querySelector(".setup-live-fail");
assert.ok(failLine && failLine.textContent.includes("setup.live_fail"), "应显示诚实校验失败原因");
assert.ok(failLine.textContent.includes("onb.err_bad_key"), "应带 key 坏的人话提示");
assert.ok(!body.querySelector(".setup-offline-link"), "key 坏(确定性配置病)不该给离线出口");

// ⑤ 网络错(unreachable)→ 拦住但给两个出口:重新配置 / 离线继续(黄条)
lockedTo = null;
setupStatus = { must_setup: false, live_checked: true, live_ok: false,
  live_model: "anthropic/claude", live_reason: "ConnectError", live_error_class: "unreachable" };
let polled = 0;
await M.checkSetupGate({ pollSnapshot: () => { polled++; } });
assert.equal(lockedTo, true, "网络错也要先拦住(不静默放行)");
const btnTexts = [...body.querySelectorAll("button")].map((b) => b.textContent);
assert.ok(btnTexts.includes("setup.reconfigure"), "应有「重新配置」出口");
assert.ok(btnTexts.includes("setup.offline_continue"), "应有「离线继续」出口");
// 点「离线继续」→ 解锁 + 关 gate 进主界面 + 顶栏黄条(模型不可用)
const offBtn = [...body.querySelectorAll("button")].find((b) => b.textContent === "setup.offline_continue");
offBtn.click();
assert.equal(lockedTo, false, "离线继续应解锁模态");
assert.equal(modal.classList.contains("hidden"), true, "离线继续应关掉 gate");
assert.ok(dom.window.document.getElementById("model-down-banner"), "应挂「模型不可用」黄条");
assert.ok(polled >= 1, "离线继续应刷新快照进主界面");

console.log("✓ models panel smoke OK — 契约 + 模型列表/表单/搜索配置 + 强制引导锁 + CFG-05 live 三态(bad_key 回 gate/网络错两出口/通过放行)+ CFG-01 禁蒙层声明(不触网不崩)");
