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

// ============================================================================
// 场景化引导(Hardy 2026-07-13:引导按**用户场景**分,别按机制堆;手机不造轮子——
// 长远手机=装 APP 入 mesh 同等待遇,网页只是过渡形态)。场景只有两个:
//   📱 手机远程访问(现阶段:扫码开网页 → 点🌐出门可用)
//   💻 新电脑加入 mesh(装 runtime → 凭一次性邀请入列;三平台同一条命令,给分 OS 提示)
// 高级:自建中转(BYO relay)。room/指纹这类机制词只出现在生成的命令里,不糊用户脸上。
// ============================================================================

// 📱 场景一:手机远程访问。二维码 = 带本次运行 token 的移动页链接(在家第一次扫);
// 端点管理权=本地(经隧道 403);token 重启即刷新,忘了链接回这里重扫(Hardy 拍过)。
// QR 本地生成(qrcode-generator,MIT,打进 bundle),链接绝不外发。
async function _phoneScene(host: HTMLElement): Promise<void> {
  const box = el("div", { class: "ext-onboarding" });
  box.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.scene.phone.title") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.scene.phone.sub") }));
  let data: any = null;
  try {
    data = await _getJSON("/api/access_url");
  } catch (e) { /* 后端不可达 → 空态 */ }
  if (!(data && data.ok)) {
    box.appendChild(el("div", { class: "mgmt-hint", text: (data && data.reason) || t("devices.qr.fail") }));
  } else if (!data.m) {
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.qr.local_only") }));
  } else {
    const qr = qrcode(0, "M");
    qr.addData(String(data.m));
    qr.make();
    const holder = el("div", { class: "devices-qr" });
    holder.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });   // 自产 SVG,非模型文本
    box.appendChild(holder);
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.qr.hint") }));
    box.appendChild(_copyRow("devices.qr.url_label", String(data.m)));
  }
  await _awayPairInto(box);
  await _pairedInto(box, host);
  box.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.scene.phone.roadmap") }));
  host.appendChild(box);
}

// 🌐 karvy.chat 托管接入页(离家在外用任意浏览器打开)的**配对出口**:签一枚一次性邀请,
// 把 {relay,room,fingerprint,code} 打成 karvy-pair:<base64url> 深链 → 出两个口:QR + 复制。
// 与上面的 LAN 二维码(同网直连)不同用途,各留各的。relay 没接 → 诚实提示先接 relay。
// base64url 编码本地做(不外发密钥;code 本身一次性、15 分钟过期)。
function _b64urlEncode(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function _awayPairInto(box: HTMLElement): Promise<void> {
  const btn = el("button", { class: "mgmt-add-btn", text: t("devices.pair2.btn") });
  const out = el("div");
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.pair2.hint") }));
  btn.addEventListener("click", async () => {
    out.innerHTML = "";
    let r: { ok: boolean; status: number; data: any };
    try {
      r = await _postJSON("/api/pair/issue", {});
    } catch (e) { out.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.pair2.no_relay") })); return; }
    const d = (r && r.data) || null;
    if (!(d && d.ok)) {                       // 没接 relay / 缺依赖 → 诚实提示(后端 reason 可空)
      out.appendChild(el("div", { class: "mgmt-hint ext-boundary",
        text: (d && d.reason) || t("devices.pair2.no_relay") }));
      return;
    }
    const link = "karvy-pair:" + _b64urlEncode(JSON.stringify({
      relay: d.relay, room: d.room, fingerprint: d.fingerprint, code: d.code }));
    const qr = qrcode(0, "M");
    qr.addData(link);
    qr.make();
    const holder = el("div", { class: "devices-qr" });
    holder.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });   // 自产 SVG
    out.appendChild(holder);
    out.appendChild(el("div", { class: "mgmt-hint", text: t("devices.pair2.qr_hint") }));
    out.appendChild(_copyRow("devices.pair2.copy_label", link));
    out.appendChild(el("div", { class: "mgmt-hint", text: t("devices.pair2.note") }));
  });
  box.appendChild(btn);
  box.appendChild(out);
}

// 已授权的手机列表(配对身份切片,docs/74)——挂在手机场景里,不再单开一块。
// 管理面只在本机/局域网可操作;一键吊销 = 那台设备下一个请求就被拒(回源在线校验)。
async function _pairedInto(box: HTMLElement, host: HTMLElement): Promise<void> {
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.title") }));
  let data: any = null;
  try {
    data = await _getJSON("/api/pair/devices");
  } catch (e) { /* 后端不可达 → 空态 */ }
  const paired: any[] = (data && data.devices) || [];
  if (!paired.length) {
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.empty") }));
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
}

// 💻 场景二:新电脑加入 mesh(两步)。①装 runtime:三平台同一条命令(跨平台三平台同发,
// 差异只在"终端在哪/怎么先有 Python",按 OS 各给一句);②签一次性邀请 → 新设备任何网络执行
// (经 relay 回家握手,15 分钟过期首用即焚;管理动作经隧道 403)。
function _pcScene(host: HTMLElement): void {
  const box = el("div", { class: "ext-onboarding" });
  box.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.scene.pc.title") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.scene.pc.step1") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.os.win") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.os.mac") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.os.linux") }));
  box.appendChild(_copyRow("devices.guide.cmd_install_label", "pip install karvyloop && karvyloop console"));
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.scene.pc.step2") }));
  const inviteBtn = el("button", { class: "mgmt-add-btn", text: t("devices.invite.btn") });
  const inviteOut = el("div");
  inviteBtn.addEventListener("click", async () => {
    inviteOut.textContent = "";
    const r = await _postJSON("/api/pair/issue", {});
    const d = (r && r.data) || null;
    if (!(d && d.ok)) {
      const i18nAny = (window as unknown as { KarvyI18n: { tBackend?: (s: string) => string } }).KarvyI18n;
      const reason = (d && d.reason) || t("devices.invite.fail");
      inviteOut.appendChild(el("div", { class: "mgmt-hint ext-boundary",
        text: (i18nAny.tBackend ? i18nAny.tBackend(reason) : reason) }));
      return;
    }
    const cmd = "karvyloop mesh-sync --relay " + d.relay + " --peer-room " + d.room +
      " --fingerprint " + d.fingerprint + " --code " + d.code;
    inviteOut.appendChild(_copyRow("devices.invite.cmd_label", cmd));
    inviteOut.appendChild(el("div", { class: "mgmt-hint", text: t("devices.invite.hint") }));
  });
  box.appendChild(inviteBtn);
  box.appendChild(inviteOut);
  box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_label") }));
  box.appendChild(_copyRow("devices.guide.cmd_label_label", 'karvyloop devices --label "my-desk-pc"'));
  host.appendChild(box);
}

// 🧭 高级:自建中转(BYO relay,开源不绑死我们的服务器)
function _advancedScene(host: HTMLElement): void {
  const away = el("div", { class: "ext-onboarding" });
  away.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.remote.title") }));
  away.appendChild(el("div", { class: "mgmt-hint", text: t("devices.remote.away") }));
  away.appendChild(_copyRow("devices.remote.cmd_relay_label", "karvyloop relay-serve --port 8767"));
  away.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.remote.honest") }));
  host.appendChild(away);
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
  await _phoneScene(body);
  _pcScene(body);
  _advancedScene(body);
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
