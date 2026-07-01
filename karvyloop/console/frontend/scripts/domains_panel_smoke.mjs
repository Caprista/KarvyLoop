/* domains_panel_smoke.mjs — 验证抽出的业务域面板:契约 + open(deps) + **注入的点角色进私聊回路**(jsdom)。
 * 这块最耦合(注入 refreshPeers/pushChatLine/openPeerChat)→ 喂罐头数据真渲染组织树、真点角色,断言回调被调。 */
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

// 喂罐头数据(必须在 load domains_panel.js 之前覆盖 —— 模块加载时就捕获了 _getJSON/_postJSON 引用)
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/domains") return { domains: [
    { id: "d1", name: "装修工作室", lifecycle: "active", value_md: "质量第一",
      member_query: "user:ch AND agent:设计师" }] };
  if (url === "/api/roles") return { roles: [{ id: "设计师" }, { id: "项目经理" }, { id: "监理" }] };
  if (url === "/api/peers") return { peers: [
    { domain_id: "d1", role: "agent", agent_id: "设计师", is_group: false, is_private: false },
  ] };
  if (url.indexOf("/api/role/in_domain") === 0) return { ok: true, role_id: "设计师", domain_id: "d1", domain_name: "装修工作室",
    paradigm: { identity: "你是设计师", soul: "以用户为中心", user: "创始人", commitment: "契约", verify: "可验证", memory: "(运行时)", atom_ids: [], skill_ids: [] },
    value_md: "# 价值观\n\n诚实第一", deontic: { forbid: ["无验证门提交"], oblige: [], permit: [] } };
  return null;
};
let _createPayload = null, _updatePayload = null;
dom.window.KarvyDom.postJSON = async (url, payload) => {
  if (url === "/api/domain/create") { _createPayload = payload; return { ok: true, status: 200, data: { ok: true, id: "d2", name: payload.name } }; }
  if (url === "/api/domain/update") { _updatePayload = payload; return { ok: true, status: 200, data: { ok: true, id: payload.domain_id } }; }
  return { ok: true, status: 200, data: { ok: true } };
};
load("domains_panel.js");

const D = dom.window.KarvyDomainsPanel;
assert.ok(D && typeof D.open === "function", "window.KarvyDomainsPanel.open 契约缺失");

let picked = null, refreshed = 0;
await D.open({
  refreshPeers: () => { refreshed++; },
  pushChatLine: () => {},
  openPeerChat: (m) => { picked = m; },
});

const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "mgmt.domains_title", "标题应是 mgmt.domains_title");
const body = dom.window.document.getElementById("mgmt-body");
assert.ok(body.querySelector("form.mgmt-form"), "应有建域表单");
assert.ok(body.querySelector("select"), "建域表单应有 父域 select");

// 组织树:d1 下应有成员「设计师」的 .org-role 按钮;点它 → 注入的 openPeerChat 收到该成员
const roleBtn = body.querySelector(".org-role");
assert.ok(roleBtn, "组织树应渲染出域成员的可点角色行(.org-role)");
roleBtn.click();
assert.ok(picked && picked.agent_id === "设计师" && picked.domain_id === "d1",
  "点组织树角色应回调注入的 openPeerChat(带该成员)→ 进私聊");

// Hardy:建域要能加**多个**角色 —— 角色是多选 chip,选多个 → create 带 agents 数组
const chips = [...body.querySelectorAll(".mgmt-pick")];
assert.ok(chips.length >= 3, `建域角色应是多选 chip(应有 3 个,实际 ${chips.length})`);
const nameInput = body.querySelector('input[type="text"]');
nameInput.value = "新装修工作室";
chips[0].click(); chips[2].click();   // 选 设计师 + 监理
assert.ok(chips[0].classList.contains("on") && chips[2].classList.contains("on"), "点 chip 应高亮选中");
body.querySelector("button.mgmt-submit").click();
await new Promise((r) => setTimeout(r, 0));   // 等 onclick 的 async 跑完
assert.ok(_createPayload, "提交应调 /api/domain/create");
assert.deepEqual(_createPayload.agents, ["设计师", "监理"], "create 应带多选的 agents 数组(多角色)");

// 编辑域(Hardy:不再手编 member_query DSL)→ 点列表里的「编辑」→ 成员是预选好的角色 chip,save 发 agents
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} });
const body2 = dom.window.document.getElementById("mgmt-body");
const editBtn = [...body2.querySelectorAll(".dpref-edit")][0];
assert.ok(editBtn, "域列表应有「编辑」按钮");
editBtn.click();
await new Promise((r) => setTimeout(r, 0));   // _openDomainEdit 是 async(拉 /api/roles)
const editBody = dom.window.document.getElementById("mgmt-body");
assert.ok(!editBody.querySelector('textarea.edit-area-sm'), "编辑域不应再有 member_query 原始 textarea(DSL 已撤)");
const editChips = [...editBody.querySelectorAll(".mgmt-pick")];
assert.ok(editChips.length >= 3, `编辑域成员应是角色 chip(应有 3 个,实际 ${editChips.length})`);
const onChip = editChips.find((c) => c.textContent === "设计师");
assert.ok(onChip && onChip.classList.contains("on"), "当前成员「设计师」应被预选(.on)");
editChips.find((c) => c.textContent === "项目经理").click();   // 再加一个
editBody.querySelector("button.mgmt-submit").click();
await new Promise((r) => setTimeout(r, 0));
assert.ok(_updatePayload, "保存应调 /api/domain/update");
assert.deepEqual([..._updatePayload.agents].sort(), ["设计师", "项目经理"].sort(),
  "更新应带角色 chip 选出的 agents(不再是手编 DSL)");
assert.ok(!("member_query" in _updatePayload), "更新不该再发原始 member_query 字符串");

// #4:重开面板 → 成员行「👁 查看」→ 只读合并视图(原生范式 + 本域 value.md/deontic)
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} });
await new Promise((r) => setTimeout(r, 0));
const vroot = dom.window.document.getElementById("mgmt-body");
const viewBtn = vroot.querySelector(".org-role-view");
assert.ok(viewBtn, "组织树成员应有『👁 查看』按钮");
viewBtn.click();
await new Promise((r) => setTimeout(r, 5));
const vbody = dom.window.document.getElementById("mgmt-body");
assert.ok([...vbody.querySelectorAll(".mgmt-section-title")].some((s) => s.textContent === "domain.native_paradigm"), "应有『原生范式』段");
assert.ok([...vbody.querySelectorAll(".soul-ro")].some((d) => d.textContent === "你是设计师"), "应只读展示原生 IDENTITY");
assert.ok([...vbody.querySelectorAll(".soul-ro")].some((d) => d.textContent.includes("诚实第一")), "应展示本域 value.md");
assert.ok(vbody.querySelector(".deontic-list.forbid"), "应展示本域 deontic 禁止(硬护栏)");

console.log("✓ domains panel smoke OK — 多选角色建域 + 组织树进私聊 + 编辑成员=chip + #4 域内角色只读合并视图");
