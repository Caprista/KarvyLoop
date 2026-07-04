/* unlock_panel.ts — 🔓 能力解锁面板(Hardy 2026-07-04:降级功能给用户引导和选择)。
 * 后端 /api/capability/unlocks 给确定性状态(on/off/missing_dep);这里配上价值一句话 +
 * 怎么做(可复制命令 / config.yaml 片段 / 一键跳配置入口)+ MCP 生态目录链接。
 * 业界模式:setup-checklist / 集成市场 —— 每行一个明确动作;语音输入是浏览器能力,就地探测。
 * 生态外链只进文案(渲染成 <a>),不进任何请求/逻辑;2026-07 真访问核验过。
 * 暴露 window.KarvyUnlockPanel.open()。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  mgmtBody: () => HTMLElement | null;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

// MCP 生态目录(官方 registry + 主流目录站;2026-07-05 WebFetch 逐一核验可达/在服务)。
// 只作 <a> 展示 —— 代码不请求这些站点。
const MCP_LINKS: Array<{ label: string; url: string }> = [
  { label: "Official MCP Registry", url: "https://registry.modelcontextprotocol.io/" },
  { label: "PulseMCP", url: "https://www.pulsemcp.com/servers" },
  { label: "Glama", url: "https://glama.ai/mcp/servers" },
  { label: "GitHub · modelcontextprotocol/servers", url: "https://github.com/modelcontextprotocol/servers" },
];

// config.yaml 示例片段(YAML 是代码不翻译;字段与 config_channels.py 消费形状一致,别发明)。
const EMAIL_SNIPPET = `channels:
  email:
    enabled: true
    smtp: {host: smtp.example.com, port: 465, user: me@example.com, password: "app password"}
    to: me@example.com`;
const WEBHOOK_SNIPPET = `channels:
  webhook:
    enabled: true
    url: https://ntfy.sh/your-private-topic
    preset: ntfy`;

function _statusBadge(status: string): HTMLElement {
  return el("span", { class: "dpref-badge " + (status === "on" ? "confirmed" : "provisional"),
    text: t("unlock.status_" + status) });
}

// 可复制命令行:<code> + 复制按钮(clipboard 不可用就静默保持"复制"字样,命令仍可手选)。
function _cmdRow(cmd: string): HTMLElement {
  const btn = el("button", { class: "mgmt-inline-link", text: t("unlock.copy"),
    onclick: async () => {
      try { await navigator.clipboard.writeText(cmd); btn.textContent = t("unlock.copied"); }
      catch (e) { /* clipboard 不可用(http/权限)→ 用户手选复制 */ }
    } });
  return el("div", { class: "mc-meta unlock-cmd-row" },
    el("code", { class: "unlock-cmd", text: cmd }), " ", btn);
}

function _card(title: string, status: string, ...rest: Child[]): HTMLElement {
  return el("div", { class: "mgmt-card" },
    el("div", { class: "mc-main" },
      el("div", { class: "mc-name" }, el("span", { text: title }), " ", _statusBadge(status)),
      ...rest));
}

function _mcpCard(u: any): HTMLElement {
  const bits: Child[] = [el("div", { class: "mc-meta", text: t("unlock.mcp.value") })];
  if (u.status === "missing_dep") {
    bits.push(el("div", { class: "mc-meta", text: t("unlock.install_hint") }));
    bits.push(_cmdRow(u.install || ""));
  } else {
    if (u.status === "on") {
      bits.push(el("div", { class: "mc-meta",
        text: t("unlock.mcp.configured", { n: (u.detail && u.detail.servers) || 0 }) }));
    }
    bits.push(el("div", { class: "mc-meta", text: t("unlock.mcp.how") }));
    const skills = (window as unknown as { KarvySkillsPanel?: { openCoding?: () => void } }).KarvySkillsPanel;
    if (skills && skills.openCoding) {
      bits.push(el("div", { class: "dpref-actions" },
        el("button", { class: "dpref-confirm", text: t("unlock.mcp.action"),
          onclick: () => skills.openCoding!() })));
    }
  }
  // 生态目录:装没装都给 —— "去哪找"正是 Hardy 点名的盲区(不搜索都不知道能去哪找)。
  const links = el("div", { class: "mc-meta unlock-links" },
    el("span", { text: t("unlock.mcp.browse") + " " }));
  MCP_LINKS.forEach((l, i) => {
    if (i) links.appendChild(document.createTextNode(" · "));
    links.appendChild(el("a", { href: l.url, target: "_blank", rel: "noopener noreferrer", text: l.label }));
  });
  bits.push(links);
  return _card("🔌 " + t("unlock.mcp.name"), u.status, ...bits);
}

// 依赖型能力(附件解析/中继/网页验收):价值一句话 + 缺依赖时给可复制安装命令。
function _depCard(icon: string, key: string, u: any, extraHowKey?: string): HTMLElement {
  const bits: Child[] = [el("div", { class: "mc-meta", text: t("unlock." + key + ".value") })];
  if (u.status === "missing_dep") {
    bits.push(el("div", { class: "mc-meta", text: t("unlock.install_hint") }));
    bits.push(_cmdRow(u.install || ""));
  }
  if (extraHowKey) bits.push(el("div", { class: "mc-meta", text: t(extraHowKey) }));
  return _card(icon + " " + t("unlock." + key + ".name"), u.status, ...bits);
}

// 配置型渠道(邮件/推送):未配置给 config.yaml 片段(可复制)+ "改完重启"注记。
function _channelCard(icon: string, key: string, u: any, snippet: string): HTMLElement {
  const bits: Child[] = [el("div", { class: "mc-meta", text: t("unlock." + key + ".value") })];
  if (u.status !== "on") {
    bits.push(el("div", { class: "mc-meta", text: t("unlock.config_note") }));
    bits.push(el("pre", { class: "unlock-snippet", text: snippet }));
    bits.push(_cmdRow(snippet));
  }
  return _card(icon + " " + t("unlock." + key + ".name"), u.status, ...bits);
}

// 语音输入:纯浏览器能力(Web Speech API),就地探测 —— 后端不知道、也不该假装知道。
function _voiceCard(): HTMLElement {
  const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  const supported = !!(w.SpeechRecognition || w.webkitSpeechRecognition);
  return _card("🎤 " + t("unlock.voice.name"), supported ? "on" : "unsupported",
    el("div", { class: "mc-meta", text: t("unlock.voice.value") }),
    el("div", { class: "mc-meta", text: t(supported ? "unlock.voice.how_on" : "unlock.voice.how_off") }));
}

async function open(): Promise<void> {
  openMgmtModal(t("unlock.name"));
  const b = mgmtBody(); if (!b) return; b.innerHTML = "";
  b.appendChild(el("div", { class: "mgmt-hint", text: t("unlock.intro") }));
  const data = await _getJSON("/api/capability/unlocks");
  const byId: Record<string, any> = {};
  for (const u of (data && data.unlocks) || []) byId[u.id] = u;
  const list = el("div", { class: "mgmt-list" });
  if (byId["mcp"]) list.appendChild(_mcpCard(byId["mcp"]));
  if (byId["files"]) list.appendChild(_depCard("📎", "files", byId["files"]));
  if (byId["webhook_channel"]) list.appendChild(_channelCard("📮", "webhook", byId["webhook_channel"], WEBHOOK_SNIPPET));
  if (byId["email_channel"]) list.appendChild(_channelCard("📧", "email", byId["email_channel"], EMAIL_SNIPPET));
  if (byId["relay"]) list.appendChild(_depCard("📡", "relay", byId["relay"], "unlock.relay.how"));
  if (byId["web_verify"]) list.appendChild(_depCard("🌐", "web", byId["web_verify"]));
  list.appendChild(_voiceCard());   // 浏览器侧探测,后端清单拿不到 → 永远渲染
  b.appendChild(list);
}

const KarvyUnlockPanel = { open };
(window as unknown as { KarvyUnlockPanel: typeof KarvyUnlockPanel }).KarvyUnlockPanel = KarvyUnlockPanel;
export { KarvyUnlockPanel };
