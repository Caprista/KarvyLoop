/* unlock_panel_smoke.mjs — 验证能力解锁面板:契约 + open() 渲染真数据形状(jsdom,不触网)。
 * fixture 走后端 /api/capability/unlocks 的真实形状({unlocks:[{id,status,install,detail}]}),
 * 断言:状态徽章 / 缺依赖给可复制安装命令 / MCP 生态目录链接(官方 registry + 目录站)/
 * 渠道给 config.yaml 片段 / 语音行浏览器侧探测(jsdom 无 SpeechRecognition → unsupported)。 */
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

// 面板 load 时绑定 _KD.getJSON —— 先打桩再 load,回放后端真实响应形状(不触网)
const FIXTURE = {
  unlocks: [
    { id: "mcp", status: "off", install: 'pip install "karvyloop[mcp]"', detail: { servers: 0 } },
    { id: "files", status: "missing_dep", install: 'pip install "karvyloop[files]"',
      detail: { missing: ["pypdf", "python-docx", "openpyxl"] } },
    { id: "asr", status: "missing_dep", install: 'pip install "karvyloop[asr]"', detail: {} },
    { id: "webhook_channel", status: "off", install: "", detail: {} },
    { id: "email_channel", status: "on", install: "", detail: { inbox: false } },
    { id: "relay", status: "missing_dep", install: 'pip install "karvyloop[relay]"', detail: {} },
    { id: "web_verify", status: "on", install: "", detail: {} },
  ],
};
let mockResponse = FIXTURE;   // 面板 load 时绑定函数引用 → 用闭包变量切换响应
dom.window.KarvyDom.getJSON = async () => mockResponse;
load("unlock_panel.js");

const U = dom.window.KarvyUnlockPanel;
assert.ok(U && typeof U.open === "function", "window.KarvyUnlockPanel.open 契约缺失");

await U.open();
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
const body = dom.window.document.getElementById("mgmt-body");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "unlock.name", "标题应是 unlock.name");
const text = body.textContent;

// 状态徽章:三态 + 浏览器侧 unsupported(jsdom 无 SpeechRecognition)
for (const k of ["unlock.status_on", "unlock.status_off", "unlock.status_missing_dep", "unlock.status_unsupported"]) {
  assert.ok(text.includes(k), `应渲染状态徽章 ${k}`);
}
// 缺依赖行:可复制安装命令原样在场(files + relay)
assert.ok(text.includes('pip install "karvyloop[files]"'), "缺依赖应给 files 安装命令");
assert.ok(text.includes('pip install "karvyloop[asr]"'), "缺依赖应给 asr 安装命令");
assert.ok(text.includes("unlock.asr.how"), "asr 卡应带模型首次下载的诚实注记");
assert.ok(text.includes('pip install "karvyloop[relay]"'), "缺依赖应给 relay 安装命令");
// MCP 生态目录链接:官方 registry + 目录站,渲染成外链 <a>(只进文案不进逻辑)
const hrefs = [...body.querySelectorAll("a")].map((a) => a.getAttribute("href"));
for (const u of ["https://registry.modelcontextprotocol.io/", "https://www.pulsemcp.com/servers",
                 "https://glama.ai/mcp/servers"]) {
  assert.ok(hrefs.includes(u), `MCP 目录链接缺失:${u}`);
}
for (const a of body.querySelectorAll("a")) {
  assert.equal(a.getAttribute("target"), "_blank", "外链应新开页");
  assert.ok((a.getAttribute("rel") || "").includes("noopener"), "外链应带 noopener");
}
// 未配置渠道:config.yaml 片段在场(webhook 未配 → 有;email 已 on → 不给片段但卡片在)
assert.ok(text.includes("preset: ntfy"), "webhook 未配置应给 config.yaml 片段");
assert.ok(text.includes("unlock.email.name"), "email 已启用也应在清单可见");
assert.ok(!text.includes("smtp.example.com"), "email 已启用不应再塞配置片段");
// 语音行:jsdom 无 SpeechRecognition → how_off 引导(换 Chrome/Edge)
assert.ok(text.includes("unlock.voice.how_off"), "语音不支持时应给引导文案");

// 后端挂了(getJSON → null):不崩,语音行(浏览器侧)仍在
mockResponse = null;
await U.open();
assert.ok(dom.window.document.getElementById("mgmt-body").textContent.includes("unlock.voice.name"),
  "后端不可达时语音行仍应渲染(不崩)");

console.log("✓ unlock panel smoke OK — 契约 + 三态徽章 + 安装命令 + MCP 目录外链 + 渠道片段 + 语音就地探测(不触网不崩)");
