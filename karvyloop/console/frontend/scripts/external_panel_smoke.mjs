/* external_panel_smoke.mjs — 验证 🔌 外部 runtime 管理面:契约 + open() 渲染真数据形状(jsdom,不触网)。
 * fixture 走后端 /api/external/citizens + /api/external/onboarding 的真实形状。
 * 断言:醒目外部徽标(🔌 external + tier)/ 在线状态灯 / 删除 + 直聊 + 刷新按钮 /
 * untrusted 诚实提示 / 按需接入引导(present + absent 两态,含"我们不 bundle"红线声明)/
 * C1 集成待接标注 / 后端不可达不崩。 */
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
dom.window.KarvyI18n = { t: (k, vars) => k + (vars ? " " + JSON.stringify(vars) : "") };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");

// 按 URL 分发 fixture(citizens / onboarding / 单个 liveness)
const CITIZENS = {
  citizens: [
    { citizen_id: "helper", domain_id: "d1", runtime_kind: "raw_text_sidecar", tier: "guest",
      status: "active", liveness: "online", is_external: true, version: "some-model",
      chat_peer: { domain_id: "d1", role: "external", agent_id: "helper" } },
    { citizen_id: "scout", domain_id: "", runtime_kind: "generic_cli", tier: "scoped",
      status: "unreachable", liveness: "unreachable", is_external: true, version: "",
      chat_peer: { domain_id: "", role: "external", agent_id: "scout" } },
  ],
  _integration_pending: "registry.list/.detach/.liveness/.tier 目标命名未 merge(走回退面)",
};
const ONBOARDING_ABSENT = { present: false, found_bins: [], guidance_key: "external.onboarding.absent", we_bundle_it: false };

let getJSONImpl = async (url) => {
  if (url.includes("/api/external/citizens")) return CITIZENS;
  if (url.includes("/api/external/onboarding")) return ONBOARDING_ABSENT;
  if (url.includes("/api/external/liveness")) return { ok: true, citizen_id: "helper", status: "offline" };
  return null;
};
// 可变的 post 实现(模块 load 时把 _postJSON 绑死,所以走一层间接 → 后续改 postJSONImpl 生效)
let postJSONImpl = async () => ({ ok: true, status: 200, data: { ok: true } });
dom.window.KarvyDom.getJSON = async (url) => getJSONImpl(url);
dom.window.KarvyDom.postJSON = async (url, payload) => postJSONImpl(url, payload);
load("external_panel.js");

const P = dom.window.KarvyExternalPanel;
assert.ok(P && typeof P.open === "function", "window.KarvyExternalPanel.open 契约缺失");

// 注入直聊 + 刷新钩子,验证直聊按钮真调 directChatPeer
let chattedPeer = null;
await P.open({ refreshPeers: () => {}, directChatPeer: (peer) => { chattedPeer = peer; } });
const modal = dom.window.document.getElementById("mgmt-modal");
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
const text = body.textContent;

// 醒目外部徽标:🔌 + external + tier(guest / scoped)
assert.ok(text.includes("🔌"), "应有醒目外部徽标图标 🔌");
assert.ok(text.includes("external.badge"), "应渲染 external 徽标文案");
assert.ok(text.includes("external.tier_guest"), "guest 公民应显示 guest tier");
assert.ok(text.includes("external.tier_scoped"), "scoped 公民应显示 scoped tier");
// 徽标 DOM class 存在(异色,不与原生角色混脸)
assert.ok(body.querySelector(".ext-badge"), "应有 .ext-badge(异色徽标)");
assert.ok(body.querySelector(".ext-card"), "应有 .ext-card(左沿异色条整卡标外部)");
// 在线状态灯:online + unreachable 两态灯都在
assert.ok(body.querySelector(".ext-light-online"), "应有 online 状态灯");
assert.ok(body.querySelector(".ext-light-unreachable"), "应有 unreachable 状态灯");
// untrusted 诚实提示
assert.ok(text.includes("external.untrusted_note"), "每张卡应有 untrusted 诚实提示");
// 三个动作按钮:直聊 / 刷新 / 删除
assert.ok(text.includes("external.direct_chat"), "应有直聊按钮");
assert.ok(text.includes("external.refresh_status"), "应有刷新状态按钮");
assert.ok(text.includes("mgmt.delete"), "应有删除按钮");
// C1 集成待接标注(注册表回退面时后端给 _integration_pending)
assert.ok(text.includes("external.integration_pending"), "回退面应诚实标 C1 集成待接");

// 直聊按钮点击 → 调 directChatPeer 带外部 peer(role 段固定 external)
const btns = [...body.querySelectorAll("button")];
const chatBtn = btns.find((b) => b.textContent.includes("external.direct_chat"));
assert.ok(chatBtn, "直聊按钮应可点");
chatBtn.click();
assert.ok(chattedPeer && chattedPeer.role === "external", "直聊应切到 role=external 的外部 peer");
assert.equal(chattedPeer.agent_id, "helper", "直聊 peer.agent_id 应是 citizen_id");

// 按需接入引导(absent 态):官方安装指引 + "我们不 bundle" 红线声明
assert.ok(text.includes("external.onboarding.absent"), "没装应给按需安装引导");
assert.ok(text.includes("external.onboarding.we_dont_bundle"), "应有'我们不 bundle 别人家软件'红线声明");
assert.ok(text.includes("external.onboarding.docs_hint"), "应指向官方源安装");

// present 态:检测到 bin → 给'去接入'引导
getJSONImpl = async (url) => {
  if (url.includes("/api/external/citizens")) return { citizens: [] };
  if (url.includes("/api/external/onboarding")) return { present: true, found_bins: ["claude"], we_bundle_it: false };
  return null;
};
await P.open();
const body2 = dom.window.document.getElementById("mgmt-body").textContent;
assert.ok(body2.includes("external.empty"), "无公民应显示空态");
assert.ok(body2.includes("external.onboarding.present"), "检测到 runtime 应给'去接入'引导");
assert.ok(body2.includes("external.onboarding.we_dont_bundle"), "present 态也应给不 bundle 声明");

// 后端不可达(getJSON → null):不崩,仍渲染引导 + 空态
getJSONImpl = async () => null;
await P.open();
assert.ok(dom.window.document.getElementById("mgmt-body").textContent.includes("external.onboarding"),
  "后端不可达时接入引导仍应渲染(不崩)");

// ---- 认领码握手:＋添加按钮 → 建壳发码 → 复制指令 + 等待接入(pending 卡)----

// ＋添加按钮在列表视图恒在(反向接入入口)。detect 返回本机探到的定型 bin(辅助自选)。
getJSONImpl = async (url) => {
  if (url.includes("/api/external/citizens")) return { citizens: [] };
  if (url.includes("/api/external/onboarding")) return ONBOARDING_ABSENT;
  if (url.includes("/api/external/detect")) return { detected: [{ runtime_kind: "generic_cli", bin: "claude" }], n: 1, we_bundle_it: false };
  return null;
};
await P.open();
const body3 = dom.window.document.getElementById("mgmt-body");
assert.ok(body3.textContent.includes("external.add_btn"), "列表视图应有 ＋添加外部 runtime 按钮");
const addBtn = [...body3.querySelectorAll("button")].find((b) => b.textContent.includes("external.add_btn"));
assert.ok(addBtn, "＋添加按钮应可点");

// 点添加 → prompt 花名 → 定型步(选 runtime 类型)。create_pending 此刻还不该被调(先定型)。
dom.window.prompt = () => "cc";
let postedCreate = null;
postJSONImpl = async (url, payload) => {
  if (url.includes("/api/external/create_pending")) {
    postedCreate = payload;
    return { ok: true, status: 200, data: {
      ok: true,
      citizen: { citizen_id: "cc", domain_id: "", pending: true, is_external: true, runtime_kind: payload.runtime_kind || "" },
      claim_url: "http://127.0.0.1:8766/api/external/claim",
      claim_secret: "abc.FAKE-DO-NOT-LEAK-SECRET",
      connector_cmd: "python -m karvyloop.external_runtime.connector --claim-url \"http://127.0.0.1:8766/api/external/claim\" --secret \"abc.FAKE-DO-NOT-LEAK-SECRET\" --citizen-id \"cc\" --runtime-kind \"" + (payload.runtime_kind || "") + "\"",
      curl_cmd: "curl -X POST \"http://127.0.0.1:8766/api/external/claim\" -H \"Content-Type: application/json\" -d '{\"secret\": \"abc.FAKE-DO-NOT-LEAK-SECRET\"}'",
    } };
  }
  return { ok: true, status: 200, data: { ok: true } };
};
addBtn.click();
await new Promise((r) => setTimeout(r, 20));   // 让 _startAddFlow(含 detect getJSON)走完
// 定型步:三种形态描述都在;探到的 bin 提示 + "检测到" 徽标在
const kindBody = dom.window.document.getElementById("mgmt-body");
assert.equal(postedCreate, null, "定型前不该 POST create_pending(先选类型)");
assert.ok(kindBody.textContent.includes("external.add_step_kind_title"), "应进选 runtime 类型步");
assert.ok(kindBody.textContent.includes("external.kind.single_json.label"), "应列 JSON 输出型");
assert.ok(kindBody.textContent.includes("external.kind.raw_text.label"), "应列纯文本边车型");
assert.ok(kindBody.textContent.includes("external.kind.generic.label"), "应列 stream-json 型");
assert.ok(kindBody.textContent.includes("external.detect_found"), "探到本机 bin 应提示'检测到:X'");
assert.ok(kindBody.querySelector(".ext-kind-detected"), "探到的类型卡应标'检测到'徽标");

// 选"纯文本边车型"(无 --agent 槽)→ 直接建壳 → 弹复制指令面板,create_pending 带 runtime_kind
const chooseBtns = [...kindBody.querySelectorAll("button")].filter((b) => b.textContent.includes("external.kind_choose"));
assert.ok(chooseBtns.length >= 3, "每种类型都应有'选这个'按钮");
// raw_text_sidecar 是第二个选项(single_json / raw_text / generic 顺序)
chooseBtns[1].click();
await new Promise((r) => setTimeout(r, 20));
assert.equal(postedCreate && postedCreate.citizen_id, "cc", "选完类型应 POST create_pending 带花名");
assert.equal(postedCreate && postedCreate.runtime_kind, "raw_text_sidecar", "create_pending 应带选定的 runtime_kind(定型)");
assert.ok(!("agent_id" in postedCreate), "非 single_json 型不应带 agent_id");
const claimBody = dom.window.document.getElementById("mgmt-body").textContent;
assert.ok(claimBody.includes("external.claim_ready_title"), "应弹出接入码已生成标题");
assert.ok(claimBody.includes("external.claim_secret_once"), "应醒目提示秘钥一次性/过期");
assert.ok(claimBody.includes("external.claim_connector_label"), "应给连接器脚本命令");
assert.ok(claimBody.includes("external.claim_curl_label"), "应给应急 curl 命令");
assert.ok(claimBody.includes("karvyloop.external_runtime.connector"), "复制指令应含连接器脚本入口");
assert.ok(dom.window.document.querySelector(".ext-claim-box"), "应有 .ext-claim-box 复制面板");

// pending 壳渲染成"等待接入"卡(异色 pending 徽标 + 状态灯)
getJSONImpl = async (url) => {
  if (url.includes("/api/external/citizens")) return { citizens: [
    { citizen_id: "cc", domain_id: "", runtime_kind: "", tier: "guest", status: "pending",
      liveness: "pending", pending: true, is_external: true, version: "",
      chat_peer: { domain_id: "", role: "external", agent_id: "cc" } },
  ] };
  if (url.includes("/api/external/onboarding")) return ONBOARDING_ABSENT;
  return null;
};
await P.open();
const pendBody = dom.window.document.getElementById("mgmt-body");
assert.ok(pendBody.querySelector(".ext-card-pending"), "pending 壳应渲染成'等待接入'卡");
assert.ok(pendBody.querySelector(".ext-badge-pending"), "应有 pending 异色徽标");
assert.ok(pendBody.querySelector(".ext-light-pending"), "应有 pending 状态灯");
assert.ok(pendBody.textContent.includes("external.pending_waiting"), "pending 卡应有等待接入提示");
assert.ok(pendBody.textContent.includes("external.cancel_pending"), "pending 卡应有取消按钮");

// ---- 多 agent 支线:选 JSON 输出型(single_json,有 --agent 槽)→ 问 agent_id → create_pending 带 agent_id ----
getJSONImpl = async (url) => {
  if (url.includes("/api/external/citizens")) return { citizens: [] };
  if (url.includes("/api/external/onboarding")) return ONBOARDING_ABSENT;
  if (url.includes("/api/external/detect")) return { detected: [], n: 0, we_bundle_it: false };
  return null;
};
await P.open();
const listBody = dom.window.document.getElementById("mgmt-body");
const addBtn2 = [...listBody.querySelectorAll("button")].find((b) => b.textContent.includes("external.add_btn"));
dom.window.prompt = () => "worker";
postedCreate = null;
addBtn2.click();
await new Promise((r) => setTimeout(r, 20));
const kindBody2 = dom.window.document.getElementById("mgmt-body");
// 探不到时(detected 空)不该出"检测到"提示,但三种类型仍在(纯形态自选,不影响主流程)
assert.ok(!kindBody2.textContent.includes("external.detect_found"), "探不到时不该有'检测到'提示");
assert.ok(kindBody2.textContent.includes("external.kind.single_json.label"), "探不到仍应能形态自选");
// 选第一个(single_json)→ 进 agent 步(不该立刻建壳)
const chooseBtns2 = [...kindBody2.querySelectorAll("button")].filter((b) => b.textContent.includes("external.kind_choose"));
chooseBtns2[0].click();
await new Promise((r) => setTimeout(r, 20));
const agentBody = dom.window.document.getElementById("mgmt-body");
assert.equal(postedCreate, null, "选 single_json 后应先问 agent,不立刻建壳");
assert.ok(agentBody.textContent.includes("external.add_step_agent_title"), "single_json 型应进选 agent 步");
const agentInput = agentBody.querySelector(".ext-agent-input");
assert.ok(agentInput, "应有 agent id 输入框");
agentInput.value = "  agent-7  ";
const confirmAgent = [...agentBody.querySelectorAll("button")].find((b) => b.textContent.includes("external.agent_confirm"));
confirmAgent.click();
await new Promise((r) => setTimeout(r, 20));
assert.equal(postedCreate && postedCreate.citizen_id, "worker", "选 agent 后应建壳");
assert.equal(postedCreate && postedCreate.runtime_kind, "single_json_cli", "应带 single_json_cli 定型");
assert.equal(postedCreate && postedCreate.agent_id, "agent-7", "create_pending 应带 trim 后的 agent_id");

console.log("✓ external panel smoke OK — 契约 + 🔌异色徽标+tier + 在线灯 + 直聊/刷新/删除 + untrusted提示 + 按需引导(present/absent+不bundle红线) + C1待接标注 + ＋添加认领码握手(建壳发码/复制指令/pending卡)(不触网不崩)");
