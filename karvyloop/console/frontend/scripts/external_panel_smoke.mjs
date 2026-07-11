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
dom.window.KarvyDom.getJSON = async (url) => getJSONImpl(url);
dom.window.KarvyDom.postJSON = async () => ({ ok: true, status: 200, data: { ok: true } });
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

console.log("✓ external panel smoke OK — 契约 + 🔌异色徽标+tier + 在线灯 + 直聊/刷新/删除 + untrusted提示 + 按需引导(present/absent+不bundle红线) + C1待接标注(不触网不崩)");
