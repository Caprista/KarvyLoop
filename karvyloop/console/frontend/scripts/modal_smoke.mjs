/* modal_smoke.mjs — 真路径验证抽出的模态基建(dev-report #4 slice 4)。
 * jsdom 里加载 dom.js + modal.js,断言开/关 + 强制引导锁(锁住时关不掉)+ 表单消息。
 */
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

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");      // modal 依赖 KarvyDom.el
load("modal.js");

const M = dom.window.KarvyModal;
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.ok(M && typeof M.openMgmtModal === "function", "window.KarvyModal 契约缺失");

// 开:设标题 + 去 hidden
M.openMgmtModal("设置");
assert.equal(title.textContent, "设置");
assert.equal(modal.classList.contains("hidden"), false, "open 应去掉 hidden");

// 关:加 hidden
M.closeMgmtModal();
assert.equal(modal.classList.contains("hidden"), true, "close 应加 hidden");

// 强制引导锁:锁住时关不掉
M.openMgmtModal("强制配置");
M.setSetupLocked(true);
M.closeMgmtModal();
assert.equal(modal.classList.contains("hidden"), false, "锁住时不该关掉(没 Key 用不了)");
M.setSetupLocked(false);
M.closeMgmtModal();
assert.equal(modal.classList.contains("hidden"), true, "解锁后应能关");

// 表单消息
const msg = M.formMsg();
assert.equal(msg.className, "mgmt-msg");
M.setMsg(msg, false, "出错了");
assert.ok(msg.className.includes("err") && msg.textContent === "出错了");
M.setMsg(msg, true, "成了");
assert.ok(msg.className.includes("ok"));

console.log("✓ modal smoke OK — 开/关 + 强制引导锁 + 表单消息 行为正确");
