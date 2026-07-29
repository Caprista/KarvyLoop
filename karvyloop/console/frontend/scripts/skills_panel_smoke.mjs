/* skills_panel_smoke.mjs — 验证抽出的技能库面板:契约 + open() 接通模态 + 喂罐头真渲染列表/详情(jsdom)。 */
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
// t 桩:返回 key;带插值变量时附在后面(断言 fail-loud 原因等真数据要能看到)
dom.window.KarvyI18n = { t: (k, vars) => k + (vars ? " " + Object.values(vars).join(" ") : "") };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
// 喂罐头(模块加载时 const 捕获 _getJSON → 覆盖要在 load skills_panel 之前)
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/skills") return { skills: [
    { name: "做PPT", when_to_use: "要做演示", status: "crystallized", sig: "s1", recall_count: 3, usage_count: 5, success_count: 4 }] };
  if (url === "/api/coding/capability") return { tools: [{ name: "read_file", kind: "builtin", description: "读文件" }] };
  // docs/96 刀1:「接上你的应用」预设区 —— 后端 /api/mcp/presets 真实形状
  //(app/channel 分区、状态灯 mcp_status、disabled 占位卡、凭证指路链接)
  if (url === "/api/mcp/presets") return {
    hot_reload: true, requires_restart: false, remote_servers: [],
    mcp_status: { connected: { notion: ["mcp_notion_notion-search"] },
                  failed: [{ name: "github", reason: "401 unauthorized (FAKE)" }], retiring: 0 },
    presets: [
      { id: "notion", name: "Notion", icon: "📝", category: "app", configured: true,
        description: "notes", risk_note: "shared pages only", params: [{ key: "token", secret: true, required: true }],
        needs_secret: true, secret_hint: "ntn_…", credential_url: "https://www.notion.so/profile/integrations",
        outbound_tools: ["notion-create-pages"], disabled: false, disabled_reason: "", url: "https://mcp.notion.com/mcp" },
      { id: "github", name: "GitHub", icon: "🐙", category: "app", configured: true,
        description: "repos", risk_note: "token scopes", params: [{ key: "token", secret: true, required: true }],
        needs_secret: true, secret_hint: "PAT", credential_url: "https://github.com/settings/tokens",
        outbound_tools: ["create_issue"], disabled: false, disabled_reason: "", url: "https://api.githubcopilot.com/mcp/" },
      { id: "gmail", name: "Gmail", icon: "📧", category: "app", configured: false,
        description: "mail", risk_note: "mailbox", params: [], needs_secret: false, secret_hint: "",
        credential_url: "", outbound_tools: [], disabled: true,
        disabled_reason: "Needs Google OAuth — later release", url: "" },
      { id: "fetch", name: "Web Fetch", icon: "🌐", category: "channel", configured: false,
        description: "fetch pages", risk_note: "any url", params: [], needs_secret: false,
        secret_hint: "", credential_url: "", outbound_tools: [], disabled: false, disabled_reason: "", url: "" },
    ] };
  if (url === "/api/skills/curve") return { bucket: "day", promote_score: 3.0, min_success_rate: 0.8,
    skills: [{ sig: "s1", name: "做PPT", crystallized_ts: 200.0, points: [
      { day: "2026-06-14", ts: 100.0, usage_count: 1, success_count: 1, usage_score: 1.0, success_rate: 1.0, promote_progress: 0.33, reruns: 0, crystallized: false },
      { day: "2026-06-15", ts: 200.0, usage_count: 5, success_count: 4, usage_score: 4.2, success_rate: 0.8, promote_progress: 1.0, reruns: 2, crystallized: true }] }],
    growth: { points: [
      { day: "2026-06-14", ts: 100.0, skills_total: 0, promotions: 0, revisions: 0, runs_total: 1, avg_success_rate: 1.0, hit_rate: 0.0 },
      { day: "2026-06-15", ts: 200.0, skills_total: 1, promotions: 1, revisions: 0, runs_total: 5, avg_success_rate: 0.8, hit_rate: 0.4 }] } };
  return null;
};
load("skills_panel.js");

const S = dom.window.KarvySkillsPanel;
assert.ok(S && typeof S.open === "function", "window.KarvySkillsPanel.open 契约缺失");

await S.open();
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "skills.title", "标题应是 skills.title");
const body = dom.window.document.getElementById("mgmt-body");
assert.ok(body.querySelector(".skill-catalog-wrap"), "应有导入/目录区");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("做PPT")), "应渲染出技能列表项");
assert.ok([...body.querySelectorAll(".mc-name")].some((n) => n.textContent.includes("coding.name")), "应渲染内建 Coding 能力卡");
// docs/57 P1 结晶裸分曲线:顶部全库成长曲线 + 每技能迷你 sparkline(纯 SVG 手画)
assert.ok(body.querySelector(".skill-growth"), "应有全库成长曲线区(skill-growth)");
assert.ok(body.querySelector(".skill-growth-chart polyline"), "成长曲线应画出 polyline");
assert.ok(body.querySelector(".skill-spark"), "技能卡应有迷你 sparkline(skill-spark)");

// docs/96 刀1:「接上你的应用」卡片区 —— 打开 Coding 详情(内含 MCP 预设区)后断言:
// app/channel 分区、状态灯(已接🟢/失败🔴/需OAuth🔒)、disabled 占位卡、凭证指路链接、重连按钮。
const KSP = dom.window.KarvySkillsPanel;
assert.ok(typeof KSP.openCoding === "function", "openCoding 契约缺失");
await KSP.openCoding();
await new Promise((r) => setTimeout(r, 0));   // _mcpPresetsSection 的 render() 是异步填充
await new Promise((r) => setTimeout(r, 0));
const cbody = dom.window.document.getElementById("mgmt-body");
const appsWrap = cbody.querySelector(".mcp-apps");
assert.ok(appsWrap, "应有「接上你的应用」区(.mcp-apps)");
assert.ok(appsWrap.textContent.includes("mcpp.apps_title"), "apps 区应用 mcpp.apps_title 标题");
const cardsText = appsWrap.textContent;
assert.ok(cardsText.includes("Notion") && cardsText.includes("Gmail"), "apps 区应渲染 Notion/Gmail 卡");
assert.ok(cardsText.includes("mcpp.st_connected"), "已接 server 应亮绿灯(mcpp.st_connected)");
assert.ok(cardsText.includes("mcpp.st_failed"), "连失败 server 应亮红灯(mcpp.st_failed)");
assert.ok(cardsText.includes("401 unauthorized (FAKE)"), "失败原因应如实展示(fail-loud)");
const disabledCard = appsWrap.querySelector(".mcp-card-disabled");
assert.ok(disabledCard, "Gmail 占位卡应带 disabled 态(.mcp-card-disabled)");
assert.ok(disabledCard.textContent.includes("mcpp.st_oauth"), "disabled 卡应亮「需 OAuth」灯");
assert.ok(disabledCard.textContent.includes("Needs Google OAuth"), "disabled 卡应展示诚实占位文案");
assert.ok(!disabledCard.querySelector("button"), "disabled 卡不应给接入按钮(别让人撞墙)");
assert.ok(appsWrap.querySelector('a[href="https://www.notion.so/profile/integrations"]'),
  "要密钥的卡应给凭证指路链接(credential_url)");
assert.ok(cardsText.includes("mcpp.reconnect"), "热加载可用时应有手动重连按钮");
assert.ok(cbody.textContent.includes("mcpp.title"), "通用渠道预设区(mcpp.title)应仍在");
assert.ok(cbody.textContent.includes("Web Fetch"), "channel 预设应渲染在通用渠道区");

console.log("✓ skills panel smoke OK — 契约 + open() 接通模态 + 真渲染技能列表 + Coding 能力卡 + 成长曲线/sparkline + docs/96 刀1「接上你的应用」(分区/状态灯/disabled占位/凭证指路/重连按钮)(不触网不崩)");
