/* devices_panel.ts — 🖥️ 我的设备 mesh 面板(docs/74 用户可见面 —— 后端 registry/schedule 的接线)。
 * 后端 /api/mesh/devices 给花名册(能力指纹 + last_seen 在线态 + 本机标记);这里给每台:
 *   - ★ 本机徽标 / 在线状态灯(presence 第一刀 = last_seen 新鲜度)
 *   - 能力 chips(coding/shell/… = feasibility 调度的输入,用户一眼看懂"这台能干什么")
 *   - 删除按钮 = **知情删除**(docs/74 §6.2):POST /api/mesh/devices/remove 先探,后端回
 *     requires_confirm + 会永久失去的能力列表 → 弹明确风险确认 → confirm=true 真删(H2A)。
 * ＋添加设备引导(真实 CLI 命令,可复制)＋离家远端访问指引(诚实标注:跨网开网页尚未建成)。
 * 暴露 window.KarvyDevicesPanel.open()。
 */
import qrcode from "qrcode-generator";

type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  closeMgmtModal: () => void;
  mgmtBody: () => HTMLElement | null;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

interface DeviceRec {
  device_id: string; label: string; os: string; arch: string; sandbox: string;
  karvyloop: string; room: string; last_seen: number; is_self: boolean;
  capabilities: string[]; online: boolean;
}

function _agoText(lastSeen: number): string {
  if (!lastSeen) return t("devices.never_seen");
  const s = Math.max(0, Date.now() / 1000 - lastSeen);
  if (s < 120) return t("devices.ago_now");
  if (s < 7200) return t("devices.ago_min", { n: Math.round(s / 60) });
  if (s < 172800) return t("devices.ago_hour", { n: Math.round(s / 3600) });
  return t("devices.ago_day", { n: Math.round(s / 86400) });
}

function _statusLight(online: boolean, isSelf: boolean): HTMLElement {
  const cls = isSelf || online ? "online" : "offline";
  const label = isSelf ? t("devices.self_badge") : t(online ? "devices.status_online" : "devices.status_offline");
  return el("span", { class: "ext-light ext-light-" + cls },
    el("span", { class: "ext-dot" }), " ",
    el("span", { class: "ext-light-label", text: label }));
}

async function _copyText(text: string): Promise<boolean> {
  try {
    const nav = window.navigator;
    if (nav && nav.clipboard && nav.clipboard.writeText) {
      await nav.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 剪贴板不可用 → 走手动 */ }
  return false;
}

function _copyRow(labelKey: string, cmd: string): HTMLElement {
  const row = el("div", { class: "ext-claim-row" });
  row.appendChild(el("div", { class: "ext-claim-label", text: t(labelKey) }));
  row.appendChild(el("pre", { class: "ext-claim-cmd", text: cmd }));
  const btn = el("button", { class: "dpref-edit", text: t("devices.copy") });
  btn.addEventListener("click", async () => {
    const ok = await _copyText(cmd);
    btn.textContent = ok ? t("devices.copied") : t("devices.copy_manual");
    window.setTimeout(() => { btn.textContent = t("devices.copy"); }, 1600);
  });
  row.appendChild(btn);
  return row;
}

// 知情删除:先无 confirm 探 → 后端回"会永久失去什么" → 人看着风险拍板 → confirm=true 真删。
async function _removeFlow(d: DeviceRec, host: HTMLElement): Promise<void> {
  const name = d.label || d.device_id.slice(0, 12) + "…";
  if (!window.confirm(t("devices.confirm_light", { name }))) return;
  const probe = await _postJSON("/api/mesh/devices/remove", { device_id: d.device_id });
  if (probe.ok && probe.data && probe.data.requires_confirm) {
    let msg = "";
    if (probe.data.is_self) msg += t("devices.confirm_self", { name }) + "\n\n";
    const caps: string[] = probe.data.narrowed || [];
    if (caps.length) msg += t("devices.confirm_narrowed", { name, caps: caps.join(", ") });
    if (!window.confirm(msg.trim() || t("devices.confirm_light", { name }))) return;
    const res = await _postJSON("/api/mesh/devices/remove", { device_id: d.device_id, confirm: true });
    if (!(res.ok && res.data && res.data.ok)) {
      window.alert(t("devices.remove_failed", { reason: (res.data && res.data.reason) || res.status }));
      return;
    }
  } else if (!(probe.ok && probe.data && probe.data.ok)) {
    window.alert(t("devices.remove_failed", { reason: (probe.data && probe.data.reason) || probe.status }));
    return;
  }
  await render(host);
}

function _deviceCard(d: DeviceRec, host: HTMLElement): HTMLElement {
  const card = el("div", { class: "mgmt-card dev-card" });
  const name = d.label || (d.device_id ? d.device_id.slice(0, 19) + "…" : "?");
  const main = el("div", { class: "mc-main" },
    el("div", { class: "mc-name" },
      el("span", { text: (d.is_self ? "★ " : "") + name })),
    el("div", { class: "mc-meta" },
      _statusLight(d.online, d.is_self), " · ",
      el("span", { text: (d.os || "?") + "/" + (d.arch || "?") }),
      el("span", { text: " · sandbox=" + (d.sandbox || "?") }),
      d.karvyloop ? el("span", { text: " · v" + d.karvyloop }) : null,
      el("span", { text: " · " + _agoText(d.last_seen) })));
  const caps = el("div", { class: "mc-meta dev-caps" });
  if (d.capabilities && d.capabilities.length) {
    for (const c of d.capabilities) caps.appendChild(el("span", { class: "dev-cap", text: c }));
  } else {
    caps.appendChild(el("span", { text: t("devices.caps_none") }));
  }
  main.appendChild(caps);
  const actions = el("div", { class: "dpref-actions" });
  actions.appendChild(el("button", {
    class: "mc-del", text: t("devices.remove"),
    onclick: () => { void _removeFlow(d, host); },
  }));
  card.appendChild(main);
  card.appendChild(actions);
  return card;
}

function _guideBoxes(host: HTMLElement): void {
  // ➕ 添加一台设备(真实 CLI 命令 —— 与 karvyloop devices/relay-pair/mesh-sync 一字不差)
  const add = el("div", { class: "ext-onboarding" });
  add.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.guide.title") }));
  add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_install") }));
  add.appendChild(_copyRow("devices.guide.cmd_install_label", "pip install karvyloop && karvyloop console"));
  add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_label") }));
  add.appendChild(_copyRow("devices.guide.cmd_label_label", 'karvyloop devices --label "my-desk-pc"'));
  add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_lan") }));
  add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_xnet") }));
  add.appendChild(_copyRow("devices.guide.cmd_sync_label",
    "karvyloop mesh-sync --relay wss://<relay> --peer-room <room> --fingerprint <fp> --code <one-time-code>"));
  host.appendChild(add);

  // 🧭 离家怎么访问(诚实:跨网打开网页尚未建成,能做的是经 relay 的同步/单次请求)
  const away = el("div", { class: "ext-onboarding" });
  away.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.remote.title") }));
  away.appendChild(el("div", { class: "mgmt-hint", text: t("devices.remote.lan") }));
  away.appendChild(el("div", { class: "mgmt-hint", text: t("devices.remote.away") }));
  away.appendChild(_copyRow("devices.remote.cmd_relay_label", "karvyloop relay-serve --port 8767"));
  away.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.remote.honest") }));
  host.appendChild(away);
}

// 📱 手机扫码直达:二维码 = 带本次运行 token 的 /m 链接(第一次连接的零摩擦入口)。
// 端点管理权=本地(经隧道 403,见 routes_pair);token 重启即刷新 —— 手机忘了链接
// 随时回这里重扫(Hardy 拍过:授权管理界面要能再次显示二维码)。QR 由 qrcode-generator
// (MIT,打进 bundle)本地生成,链接绝不外发。
async function _qrSection(host: HTMLElement): Promise<void> {
  const box = el("div", { class: "ext-onboarding" });
  box.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.qr.title") }));
  let data: any = null;
  try {
    data = await _getJSON("/api/access_url");
  } catch (e) { /* 后端不可达 → 空态 */ }
  if (!(data && data.ok)) {
    box.appendChild(el("div", { class: "mgmt-hint", text: (data && data.reason) || t("devices.qr.fail") }));
    host.appendChild(box);
    return;
  }
  if (!data.m) {
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.qr.local_only") }));
    host.appendChild(box);
    return;
  }
  const qr = qrcode(0, "M");
  qr.addData(String(data.m));
  qr.make();
  const holder = el("div", { class: "devices-qr" });
  holder.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });   // 自产 SVG,非模型文本
  box.appendChild(holder);
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.qr.hint") }));
  box.appendChild(_copyRow("devices.qr.url_label", String(data.m)));
  host.appendChild(box);
}

// 📱 已授权的远程设备(配对身份切片,docs/74):手机在 /m 点「出门也能用」配对后出现在这;
// 这里是**管理面**(只在本机/局域网可操作,经隧道的请求打不到)——一键吊销 = 那台设备
// 下一个请求就被拒(回源在线校验),丢手机回家点一下即可。
async function _pairedSection(host: HTMLElement): Promise<void> {
  const box = el("div", { class: "ext-onboarding" });
  box.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.paired.title") }));
  let data: any = null;
  try {
    data = await _getJSON("/api/pair/devices");
  } catch (e) { /* 后端不可达 → 空态 */ }
  const paired: any[] = (data && data.devices) || [];
  if (!paired.length) {
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.empty") }));
    host.appendChild(box);
    return;
  }
  const list = el("div", { class: "mgmt-list" });
  for (const p of paired) {
    const when = p.granted_at ? new Date(p.granted_at * 1000).toLocaleDateString() : "";
    const card = el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name", text: "📱 " + (p.label || p.fingerprint || "?") }),
        el("div", { class: "mc-meta" },
          el("span", { class: "mc-tag", text: p.scope === "read" ? t("devices.paired.scope_read") : t("devices.paired.scope_full") }),
          when ? " · " + t("devices.paired.granted", { d: when }) : "")),
      el("button", { class: "mc-del", text: t("devices.paired.revoke"),
        onclick: async () => {
          if (!window.confirm(t("devices.paired.revoke_confirm", { f: p.fingerprint }))) return;
          const r = await _postJSON("/api/pair/revoke", { ident: p.fingerprint });
          if (!(r && r.ok && r.data && r.data.ok)) { window.alert(t("devices.paired.revoke_failed")); return; }
          const body = host.closest("#mgmt-body") as HTMLElement | null;
          if (body) void render(body);
        } }));
    list.appendChild(card);
  }
  box.appendChild(list);
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.how") }));
  host.appendChild(box);
}

async function render(body: HTMLElement): Promise<void> {
  body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-hint", text: t("devices.intro") }));
  let data: any = null;
  try {
    data = await _getJSON("/api/mesh/devices");
  } catch (e) { /* 后端不可达 → 空态 */ }
  const devices: DeviceRec[] = (data && data.devices) || [];
  if (data && data.has_identity === false) {
    // 本机还没有 relay 身份 → 诚实提示怎么生成(没身份就不入册,不是 bug)
    body.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.no_identity") }));
    body.appendChild(_copyRow("devices.cmd_pair_label", "karvyloop relay-pair"));
  }
  if (!devices.length) {
    body.appendChild(el("div", { class: "mgmt-empty", text: t("devices.empty") }));
  } else {
    const list = el("div", { class: "mgmt-list" });
    for (const d of devices) list.appendChild(_deviceCard(d, body));
    body.appendChild(list);
  }
  await _qrSection(body);
  await _pairedSection(body);
  _guideBoxes(body);
}

async function open(): Promise<void> {
  openMgmtModal(t("devices.title"));
  const body = mgmtBody();
  if (!body) return;
  await render(body);
}

const KarvyDevicesPanel = { open };
(window as unknown as { KarvyDevicesPanel: typeof KarvyDevicesPanel }).KarvyDevicesPanel = KarvyDevicesPanel;
export { KarvyDevicesPanel };
