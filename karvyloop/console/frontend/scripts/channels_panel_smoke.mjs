/* channels_panel_smoke.mjs — 渠道面板契约、列表、表单和 pending sender 一键允许。 */
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
const requests = [];
globalThis.fetch = dom.window.fetch = async (url, options = {}) => {
  requests.push({ url, method: options.method, payload: options.body ? JSON.parse(options.body) : undefined });
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
dom.window.KarvyI18n = { t: (key, vars = {}) =>
  Object.entries(vars).reduce((text, [name, value]) => text.replace(`{${name}}`, String(value)), key) };

const here = dirname(fileURLToPath(import.meta.url));
const load = (file) => (0, eval)(readFileSync(resolve(here, "../../static/" + file), "utf8"));
const appSource = readFileSync(resolve(here, "../../static/app.js"), "utf8");
assert.ok(appSource.includes('msg.type === "channel_sender_pending"') &&
  appSource.includes('new CustomEvent("karvy:channel-sender-pending"'),
"app.js 应把 channel_sender_pending 派发为渠道面板 DOM 事件");
load("dom.js");
load("modal.js");
let dingtalkReads = 0;
let pendingSenders = [{ instance_id: "ding-1", sender: "staff-2", sender_nick: "乙" }];
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/roles") return { roles: [{ id: "资料管家", display_name: "资料管家（小资）" }] };
  if (url === "/api/domains") return { domains: [{ id: "docs", name: "资料库", lifecycle: "active" }] };
  dingtalkReads++;
  return {
    instances: [{
      id: "ding-1", name: "资料机器人", client_id: "client-1", has_client_secret: true,
      role: "资料管家", domain_id: "docs", allow_senders: ["staff-1"], enabled: true,
    }],
    pending_senders: pendingSenders,
  };
};
const posts = [];
dom.window.KarvyDom.postJSON = async (url, payload) => {
  posts.push({ url, payload });
  return { ok: true, status: 200, data: { ok: true } };
};
load("channels_panel.js");

const panel = dom.window.KarvyChannelsPanel;
assert.ok(panel && typeof panel.open === "function", "window.KarvyChannelsPanel.open 契约缺失");
await panel.open();
assert.equal(document.getElementById("mgmt-modal").classList.contains("hidden"), false, "open 应打开模态");
assert.equal(document.getElementById("mgmt-title").textContent, "channels.title", "标题应使用渠道 i18n");
assert.equal(document.querySelector(".channel-tag").textContent, "staff-1", "allow_senders 应渲染为标签");
assert.equal(document.querySelector(".channel-pending"), null, "pending 候选不应在列表中直接授权");

document.querySelector(".dpref-edit").click();
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.equal(document.querySelector("select[name='role']").value, "资料管家", "角色应由 /api/roles 下拉并选中当前值");
assert.equal(document.querySelector("select[name='domain_id']").value, "docs", "业务域应由 /api/domains 下拉并选中当前值");
const senderInput = document.querySelector("input[name='allow_senders']");
document.querySelector(".channel-pending-options .channel-allow-btn").click();
assert.equal(senderInput.value, "staff-1, staff-2", "点击 pending 只应加入 allow_senders 输入框");
assert.equal(posts.length, 0, "pending 候选在保存前不应调用授权 API");
assert.equal(requests.length, 0, "pending 候选在保存前不应调用保存 API");

document.querySelector(".channel-form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.deepEqual(requests[0].payload.allow_senders, ["staff-1", "staff-2"], "保存时才应提交加入的 pending sender");

document.querySelector(".mgmt-new-btn").click();
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.ok(document.querySelector(".channel-form input[name='client_secret']"), "新增表单应包含渠道密钥字段");
assert.ok(document.querySelector(".channel-form select[name='role']"), "新增表单角色应为 API 下拉");

// pending sender 实时事件：列表和对应编辑表单都刷新；重复 open 不得叠加监听。
await panel.open();
await panel.open();
let readsBeforeEvent = dingtalkReads;
pendingSenders = [...pendingSenders, { instance_id: "ding-1", sender: "staff-3", sender_nick: "丙" }];
window.dispatchEvent(new dom.window.CustomEvent("karvy:channel-sender-pending", {
  detail: pendingSenders.at(-1),
}));
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.equal(dingtalkReads, readsBeforeEvent + 1, "重复 open 后一次事件只应触发一次列表刷新");

document.querySelector(".dpref-edit").click();
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
readsBeforeEvent = dingtalkReads;
const editingForm = document.querySelector(".channel-form");
const editingName = editingForm.querySelector("input[name='name']");
const editingSecret = editingForm.querySelector("input[name='client_secret']");
const editingSenders = editingForm.querySelector("input[name='allow_senders']");
editingName.value = "未保存名称";
editingSecret.value = "unsaved-secret";
editingSenders.value = "staff-1, 手工输入";
const pendingCountBeforeEvent = document.querySelectorAll(".channel-pending").length;
const latestPending = { instance_id: "ding-1", sender: "staff-4", sender_nick: "丁" };
pendingSenders = [...pendingSenders, latestPending];
window.dispatchEvent(new dom.window.CustomEvent("karvy:channel-sender-pending", { detail: latestPending }));
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.equal(dingtalkReads, readsBeforeEvent, "对应渠道表单收到事件不应回拉数据或重建表单");
assert.equal(document.querySelector(".channel-form"), editingForm, "pending 事件应保留原表单 DOM");
assert.equal(editingName.value, "未保存名称", "pending 事件不应覆盖未保存的名称");
assert.equal(editingSecret.value, "unsaved-secret", "pending 事件不应清空未保存的密钥");
assert.equal(editingSenders.value, "staff-1, 手工输入", "pending 事件不应覆盖未保存的授权输入");
assert.equal(document.querySelectorAll(".channel-pending").length, pendingCountBeforeEvent + 1,
  "对应渠道编辑表单应只增量添加实时 pending sender");
window.dispatchEvent(new dom.window.CustomEvent("karvy:channel-sender-pending", { detail: latestPending }));
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.equal(document.querySelectorAll(".channel-pending").length, pendingCountBeforeEvent + 1,
  "重复 pending 事件应按 sender 去重");

// 关闭后应解绑，不再刷新；MutationObserver 在微任务中处理关闭。
document.getElementById("mgmt-modal").classList.add("hidden");
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
readsBeforeEvent = dingtalkReads;
window.dispatchEvent(new dom.window.CustomEvent("karvy:channel-sender-pending", {
  detail: { instance_id: "ding-1", sender: "staff-5" },
}));
await new Promise((resolveWait) => setTimeout(resolveWait, 0));
assert.equal(dingtalkReads, readsBeforeEvent, "渠道面板关闭后 pending 事件不应继续刷新");

console.log("✓ channels panel smoke OK — pending sender 列表刷新、表单增量去重且保留未保存输入");
