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

// ---- CFG-01①:per-open 蒙层/Esc 契约(默认行为不变;声明了才变)----
assert.ok(typeof M.backdropCloseEnabled === "function", "backdropCloseEnabled 契约缺失");
M.openMgmtModal("普通面板");
assert.equal(M.backdropCloseEnabled(), true, "默认:点空白可关(既有面板行为不变)");
M.openMgmtModal("模型设置", { backdropClose: false, escClose: true });
assert.equal(M.backdropCloseEnabled(), false, "模型设置声明 backdropClose:false 应生效");
// Esc:声明了 escClose 的窗按 Esc 关
const esc = () => dom.window.document.dispatchEvent(
  new dom.window.KeyboardEvent("keydown", { key: "Escape", cancelable: true, bubbles: true }));
esc();
assert.equal(modal.classList.contains("hidden"), true, "escClose:true 时 Esc 应能关");
// 没声明 escClose 的窗:Esc 不动它(不全局改行为)
M.openMgmtModal("普通面板2");
assert.equal(M.backdropCloseEnabled(), true, "重开无 opts 应复位为默认(不残留)");
esc();
assert.equal(modal.classList.contains("hidden"), false, "未声明 escClose 的窗 Esc 不该关");
M.closeMgmtModal();

console.log("✓ modal smoke OK — 开/关 + 强制引导锁 + 表单消息 + per-open 蒙层/Esc 契约 行为正确");
