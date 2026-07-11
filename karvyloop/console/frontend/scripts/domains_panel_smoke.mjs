/* domains_panel_smoke.mjs — 验证抽出的业务域面板:契约 + open(deps) + **注入的点角色进私聊回路**(jsdom)。
 * 这块最耦合(注入 refreshPeers/pushChatLine/openPeerChat)→ 喂罐头数据真渲染组织树、真点角色,断言回调被调。
 *
 * 空/非空分态(Hardy):
 *   - 没有(活跃)业务域 → 引导态:一句"来新建你的第一个业务域吧" + 模板作**创建路径**(主角)+ 从零建域表单。
 *   - 已有业务域 → 直接给域列表(主角);模板收进「＋ 新建业务域」入口,点开才展开(不再顶在列表上方)。
 * 用可切换的罐头 `_domsFixture` 跑两态各一遍。 */
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

// 可切换的域罐头 —— 默认非空(一条域),测空态时置 []。
const NONEMPTY = [
  { id: "d1", name: "装修工作室", lifecycle: "active", value_md: "质量第一",
    member_query: "user:ch AND agent:设计师" }];
let _domsFixture = NONEMPTY;

// 喂罐头数据(必须在 load domains_panel.js 之前覆盖 —— 模块加载时就捕获了 _getJSON/_postJSON 引用)
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/domains") return { domains: _domsFixture };
  if (url === "/api/roles") return { roles: [{ id: "设计师" }, { id: "项目经理" }, { id: "监理" }] };
  if (url === "/api/domain/templates") return { templates: [
    { id: "t_studio", name: "装修工作室", emoji: "🏠", description: "开箱即用的装修团队",
      roles: [{ nickname: "小设", title: "设计师" }, { nickname: "小监", title: "监理" }] }] };
  if (url === "/api/peers") return { peers: [
    { domain_id: "d1", role: "agent", agent_id: "设计师", is_group: false, is_private: false },
  ] };
  if (url.indexOf("/api/role/in_domain") === 0) return { ok: true, role_id: "设计师", domain_id: "d1", domain_name: "装修工作室",
    paradigm: { identity: "你是设计师", soul: "以用户为中心", user: "创始人", commitment: "契约", verify: "可验证", memory: "(运行时)", atom_ids: [], skill_ids: [] },
    value_md: "# 价值观\n\n诚实第一", deontic: { forbid: ["无验证门提交"], oblige: [], permit: [] } };
  return null;
};
let _createPayload = null, _updatePayload = null, _instantiatePayload = null;
dom.window.KarvyDom.postJSON = async (url, payload) => {
  if (url === "/api/domain/create") { _createPayload = payload; return { ok: true, status: 200, data: { ok: true, id: "d2", name: payload.name } }; }
  if (url === "/api/domain/update") { _updatePayload = payload; return { ok: true, status: 200, data: { ok: true, id: payload.domain_id } }; }
  if (url === "/api/domain/templates/instantiate") { _instantiatePayload = payload; return { ok: true, status: 200, data: { ok: true, id: "d_new" } }; }
  return { ok: true, status: 200, data: { ok: true } };
};
load("domains_panel.js");

const D = dom.window.KarvyDomainsPanel;
assert.ok(D && typeof D.open === "function", "window.KarvyDomainsPanel.open 契约缺失");

const bodyEl = () => dom.window.document.getElementById("mgmt-body");
const settle = () => new Promise((r) => setTimeout(r, 5));

// ── 空态:没有业务域 → 引导 + 模板作创建路径(主角)+ 选模板=实例化(带配置新建,不是"打开已有")──
_domsFixture = [];
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} });
await settle();
{
  const body = bodyEl();
  const titles = [...body.querySelectorAll(".mgmt-section-title")].map((s) => s.textContent);
  assert.ok(titles.includes("domain.empty_guide"), "空态应有引导 headline(domain.empty_guide)");
  // 空态不该出组织树 / 现有域列表 / 「＋新建」入口(那些是非空态的)
  assert.ok(!body.querySelector(".org-tree"), "空态不该渲染组织树");
  assert.ok(!titles.includes("mgmt.existing"), "空态不该有『现有』域列表段");
  assert.ok(!body.querySelector(".domtpl-new-toggle"), "空态不该有『＋新建』收起入口(模板已是主角直接展开)");
  // 模板作为创建路径:卡片直接可见,按钮文案是中性"新建"(domtpl.use)而非"开张/打开"
  const tplCards = [...body.querySelectorAll(".domtpl-list .mgmt-card")];
  assert.ok(tplCards.length >= 1, "空态应直接展开模板卡(创建路径,主角)");
  const useBtn = body.querySelector(".domtpl-list .dpref-confirm");
  assert.ok(useBtn, "模板卡应有创建按钮");
  assert.equal(useBtn.textContent, "domtpl.use", "模板按钮文案应是中性『新建』(domtpl.use),不是『开张/打开』");
  // 空态也给从零手写建域表单(创建路径之二)
  assert.ok(body.querySelector("form.mgmt-form"), "空态应附从零手写建域表单");
  // 点模板 → 走实例化(带配置新建一个域)
  useBtn.click();
  await settle();
  assert.ok(_instantiatePayload && _instantiatePayload.template_id === "t_studio",
    "空态点模板应调 /api/domain/templates/instantiate(带配置新建),不是打开已有");
}

// ── 非空态:已有业务域 → 组织树 + 现有列表(主角);模板 + 从零建域收进「＋新建」入口(默认收起)──
_domsFixture = NONEMPTY;
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} });
await settle();
{
  const body = bodyEl();
  const modal = dom.window.document.getElementById("mgmt-modal");
  const title = dom.window.document.getElementById("mgmt-title");
  assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
  assert.equal(title.textContent, "mgmt.domains_title", "标题应是 mgmt.domains_title");
  // 非空态:现有域是主角 —— 组织树 + 现有列表都在
  assert.ok(body.querySelector(".org-tree"), "非空态应渲染组织树(现有域是主角)");
  const titles = [...body.querySelectorAll(".mgmt-section-title")].map((s) => s.textContent);
  assert.ok(titles.includes("mgmt.existing"), "非空态应有『现有』域列表段");
  // 模板不再顶在列表上方:默认没有展开的模板卡,而是收进「＋新建」入口(默认收起)
  assert.ok(!body.querySelector(".domtpl-list"), "非空态默认不该展开模板卡(收进＋新建入口)");
  assert.ok(!body.querySelector("form.mgmt-form"), "非空态默认不该展开建域表单(收进＋新建入口)");
  const newToggle = body.querySelector(".domtpl-new-toggle");
  assert.ok(newToggle, "非空态应有『＋ 新建业务域』入口");
  assert.equal(newToggle.textContent, "domain.new_entry", "『＋新建』入口文案应是 domain.new_entry");
}

// 组织树:d1 下应有成员「设计师」的 .org-role 按钮;点它 → 注入的 openPeerChat 收到该成员
let picked = null;
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: (m) => { picked = m; } });
await settle();
{
  const body = bodyEl();
  const roleBtn = body.querySelector(".org-role");
  assert.ok(roleBtn, "组织树应渲染出域成员的可点角色行(.org-role)");
  roleBtn.click();
  assert.ok(picked && picked.agent_id === "设计师" && picked.domain_id === "d1",
    "点组织树角色应回调注入的 openPeerChat(带该成员)→ 进私聊");

  // 点「＋新建」入口展开 → 模板卡 + 从零建域表单出现
  const newToggle = body.querySelector(".domtpl-new-toggle");
  newToggle.click();
  await settle();
  assert.ok(body.querySelector(".domtpl-list .mgmt-card"), "展开『＋新建』应出现模板卡(新建的一种方式)");
  assert.ok(body.querySelector("form.mgmt-form"), "展开『＋新建』应出现从零建域表单");
  assert.ok(body.querySelector("form.mgmt-form select"), "建域表单应有 父域 select");

  // 再点入口应能收起(toggle 语义):内容不移除,只隐藏(display:none)。先测收起,再重开去提交。
  newToggle.click();
  await settle();
  const collapsedBody = body.querySelector(".domtpl-new-body");
  assert.ok(collapsedBody && collapsedBody.style.display === "none", "再点『＋新建』应收起(domtpl-new-body 隐藏)");
  newToggle.click();   // 重新展开
  await settle();
  assert.ok(collapsedBody.style.display !== "none", "第三次点应重新展开");

  // 展开态:建域仍能多选角色 + 带 agents 数组(注:提交会 re-render,故放最后)
  const chips = [...body.querySelectorAll("form.mgmt-form .mgmt-pick")];
  assert.ok(chips.length >= 3, `建域角色应是多选 chip(应有 3 个,实际 ${chips.length})`);
  const nameInput = body.querySelector('form.mgmt-form input[type="text"]');
  nameInput.value = "新装修工作室";
  chips[0].click(); chips[2].click();   // 选 设计师 + 监理
  assert.ok(chips[0].classList.contains("on") && chips[2].classList.contains("on"), "点 chip 应高亮选中");
  body.querySelector("form.mgmt-form button.mgmt-submit").click();
  await settle();
  assert.ok(_createPayload, "提交应调 /api/domain/create");
  assert.deepEqual(_createPayload.agents, ["设计师", "监理"], "create 应带多选的 agents 数组(多角色)");
}

// 编辑域(Hardy:不再手编 member_query DSL)→ 点列表里的「编辑」→ 成员是预选好的角色 chip,save 发 agents
await D.open({ refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} });
await settle();
const body2 = bodyEl();
const editBtn = [...body2.querySelectorAll(".dpref-edit")][0];
assert.ok(editBtn, "域列表应有「编辑」按钮");
editBtn.click();
await new Promise((r) => setTimeout(r, 0));   // _openDomainEdit 是 async(拉 /api/roles)
const editBody = bodyEl();
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
await settle();
const vroot = bodyEl();
const viewBtn = vroot.querySelector(".org-role-view");
assert.ok(viewBtn, "组织树成员应有『👁 查看』按钮");
viewBtn.click();
await settle();
const vbody = bodyEl();
assert.ok([...vbody.querySelectorAll(".mgmt-section-title")].some((s) => s.textContent === "domain.native_paradigm"), "应有『原生范式』段");
assert.ok([...vbody.querySelectorAll(".soul-ro")].some((d) => d.textContent === "你是设计师"), "应只读展示原生 IDENTITY");
assert.ok([...vbody.querySelectorAll(".soul-ro")].some((d) => d.textContent.includes("诚实第一")), "应展示本域 value.md");
assert.ok(vbody.querySelector(".deontic-list.forbid"), "应展示本域 deontic 禁止(硬护栏)");

console.log("✓ domains panel smoke OK — 空态引导+模板创建路径 / 非空态列表+模板收进＋新建 / 组织树进私聊 / 编辑成员=chip / #4 域内角色只读合并视图");
