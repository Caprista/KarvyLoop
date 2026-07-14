/* away.ts — 🌅 托管接入页(karvy.chat 静态托管):出门在外、用任意浏览器打开它,
 * 首次配对 → 经 relay E2E 隧道连回家 → 在浏览器里拍板。
 *
 * 与家里 console **永远不同源**(公网 origin)——所以数据面**隧道-only**:
 * 不能像 m.ts 的 kfetch 那样"直连优先、隧道兜底"(直连永远打不到家),所有 /api 调用
 * 必须经 Tunnel.tunnelFetch。本文件里**没有一处裸 fetch**(配对信息靠粘贴,不靠网络)。
 *
 * 依赖加载序:e2e.js → tunnel.js → i18n.js → away.js(away.html 有脚本序测试锁)。
 * 隧道栈复用 tunnel.ts(Tunnel / pairAndSave / loadIdentity / saveIdentity / clearIdentity),
 * 不重造;卡片/拍板视觉复用 m-* class(away.html 内联同款样式)。
 */
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

interface RemoteIdentity { priv_hex: string; relay: string; room: string; fingerprint: string }
interface TunnelInst {
  connected: boolean;
  onstate: ((s: string) => void) | null;
  connect: (code?: string | null) => Promise<void>;
  tunnelFetch: (path: string, init?: { method?: string; headers?: Record<string, string>; body?: string }) =>
    Promise<{ ok: boolean; status: number; json: () => Promise<unknown>; text: () => Promise<string> }>;
  close: () => void;
}
interface TunnelApi {
  loadIdentity: () => RemoteIdentity | null;
  saveIdentity: (id: RemoteIdentity) => void;
  clearIdentity: () => void;
  pairAndSave: (relay: string, room: string, fp: string, code: string) => Promise<TunnelInst>;
  Tunnel: new (id: RemoteIdentity) => TunnelInst;
}
const TN = (): TunnelApi =>
  (globalThis as unknown as { KarvyTunnel: TunnelApi }).KarvyTunnel;

function el(tag: string, attrs?: Record<string, unknown> | null, ...children: (Node | string | null)[]): HTMLElement {
  const e = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      const v = attrs[k];
      if (k === "class") e.className = String(v);
      else if (k === "text") e.textContent = String(v);
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
      else if (v != null) e.setAttribute(k, String(v));
    }
  }
  for (const c of children) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); }
  return e;
}

function _root(): HTMLElement | null { return document.getElementById("away-root"); }

// ---- 配对信息解析:接受 /api/pair/issue 的 JSON 原样,或 karvy-pair:<base64url(json)> 深链 ----
interface PairBundle { relay: string; room: string; fingerprint: string; code: string }

function _b64urlDecode(s: string): string {
  let x = s.replace(/-/g, "+").replace(/_/g, "/");
  while (x.length % 4) x += "=";
  const bin = atob(x);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function _parseBundle(raw: string): PairBundle | null {
  const s = (raw || "").trim();
  if (!s) return null;
  let jsonText = s;
  const m = /^karvy-pair:(.+)$/s.exec(s);
  if (m) {
    try { jsonText = _b64urlDecode(m[1].trim()); } catch (e) { return null; }
  }
  let d: Record<string, unknown>;
  try { d = JSON.parse(jsonText); } catch (e) { return null; }
  if (!d || typeof d !== "object") return null;
  const relay = String(d.relay || ""), room = String(d.room || "");
  const fingerprint = String(d.fingerprint || ""), code = String(d.code || "");
  if (!relay || !room || !fingerprint || !code) return null;     // 四字段齐全才算数
  return { relay, room, fingerprint, code };
}

// 握手失败 → 诚实分类(客户端能拿到的信号见 tunnel.ts/relay:console 用 T_ERR "pairing_rejected"
// 明说码错/未配对;socket 开不了=relay 不可达;console 不在线=relay 转发 console_offline 后关连)。
function _classifyErr(e: unknown): string {
  const msg = (e instanceof Error ? e.message : String(e || "")).toLowerCase();
  if (msg.includes("pairing_rejected")) return t("away.err_code");
  if (msg.includes("unreachable")) return t("away.err_relay");
  if (msg.includes("fingerprint") || msg.includes("confirm mac")) return t("away.err_fingerprint");
  if (msg.includes("closed during handshake") || msg.includes("connection lost")
      || msg.includes("console_offline") || msg.includes("timeout")) return t("away.err_offline");
  return t("away.err_generic");
}

// ============================================================================
// 状态机:配对屏(无身份) ⇄ 拍板屏(有身份)。boot 读 loadIdentity 定初始屏。
//   配对屏 --粘贴解析→pairAndSave 成功--> 拍板屏
//   拍板屏 --连不上/被吊销 · 点「重新配对」(clearIdentity)--> 配对屏
// ============================================================================

let _tunnel: TunnelInst | null = null;
let _timer: number | null = null;

// ---- 配对屏 ----
function showPairing(errText?: string): void {
  _stopPolling();
  if (_tunnel) { try { _tunnel.close(); } catch (e) { /* */ } _tunnel = null; }
  const root = _root();
  if (!root) return;
  root.innerHTML = "";
  const box = el("div", { class: "aw-pair" });
  box.appendChild(el("div", { class: "aw-brand", text: "🦫 KarvyLoop" }));
  box.appendChild(el("h1", { class: "aw-title", text: t("away.pair_title") }));
  box.appendChild(el("p", { class: "aw-intro", text: t("away.pair_intro") }));
  const ta = el("textarea", { class: "aw-input", id: "aw-input", rows: "5",
    placeholder: t("away.pair_ph") }) as HTMLTextAreaElement;
  box.appendChild(ta);
  const errNode = el("div", { class: "aw-err", id: "aw-err", text: errText || "" });
  if (!errText) errNode.style.display = "none";
  box.appendChild(errNode);
  const btn = el("button", { class: "aw-btn", id: "aw-connect", text: t("away.pair_btn") });
  btn.addEventListener("click", () => { void _doPair(); });
  box.appendChild(btn);
  box.appendChild(el("p", { class: "aw-note", text: t("away.pair_note") }));
  root.appendChild(box);
}

function _showPairErr(text: string): void {
  const n = document.getElementById("aw-err");
  if (n) { n.textContent = text; n.style.display = ""; }
  const btn = document.getElementById("aw-connect") as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = t("away.pair_btn"); }
}

async function _doPair(): Promise<void> {
  const ta = document.getElementById("aw-input") as HTMLTextAreaElement | null;
  const btn = document.getElementById("aw-connect") as HTMLButtonElement | null;
  if (!ta) return;
  const bundle = _parseBundle(ta.value);
  if (!bundle) { _showPairErr(t("away.err_format")); return; }
  if (btn) { btn.disabled = true; btn.textContent = t("away.pairing"); }
  try {
    // pairAndSave:生成密钥对 → 一次性码握手(码即焚)→ 存身份进本 origin 的 localStorage。
    // 返回的隧道**已连上**(占了 relay 房间的 client 位)——必须复用它,绝不能 showDeck 再
    // 新建一条连同一房间:relay 一房只允一个 client,第二条撞 room_busy 卡死(真旅程实捕)。
    _tunnel = await TN().pairAndSave(bundle.relay, bundle.room, bundle.fingerprint, bundle.code);
    showDeck();
  } catch (e) {
    _showPairErr(_classifyErr(e));
  }
}

// ---- 拍板屏 ----
function showDeck(): void {
  const root = _root();
  if (!root) return;
  root.innerHTML = "";
  const header = el("header", { class: "aw-header" },
    el("span", { class: "aw-hbrand", text: "🦫 KarvyLoop" }),
    el("span", { class: "aw-chip", id: "aw-chip", text: t("away.chip_connecting") }),
    el("span", { class: "aw-waiting" },
      el("span", { id: "aw-waiting-label", text: t("away.waiting") }),
      el("span", { id: "aw-count" })),
    el("button", { class: "aw-icon", id: "aw-refresh", title: t("m.refresh"), text: "↻" }));
  const list = el("main", { class: "aw-list", id: "aw-list" });
  root.appendChild(header);
  root.appendChild(list);
  const rbtn = document.getElementById("aw-refresh");
  if (rbtn) rbtn.addEventListener("click", () => { void refresh(); });
  // 刚配对好的隧道已连上 → 直接复用(别重连同一房间撞 room_busy);否则(免码进入)才新建连。
  if (_tunnel && _tunnel.connected) { _useTunnel(_tunnel); void refresh(); _startPolling(); }
  else void _connectDeck();
}

// 把一条已建/新建的隧道接进拍板屏:挂 onstate → chip;调用方负责 refresh + startPolling。
function _useTunnel(tn: TunnelInst): void {
  _tunnel = tn;
  _setChip(tn.connected ? "open" : "connecting");
  tn.onstate = (s: string) => {
    if (s === "open") _setChip("open");
    else if (s === "connecting") _setChip("connecting");
    else _setChip("closed");                          // "closed" | "error:*"
  };
}

function _setChip(state: string): void {
  const chip = document.getElementById("aw-chip");
  if (!chip) return;
  chip.className = "aw-chip aw-chip-" + state;
  const key = state === "open" ? "away.chip_open" : state === "closed" ? "away.chip_closed" : "away.chip_connecting";
  chip.textContent = t(key);
}

async function _connectDeck(): Promise<void> {
  const id = TN().loadIdentity();
  if (!id) { showPairing(); return; }
  const tn = new (TN().Tunnel)(id);
  _useTunnel(tn);
  try {
    await tn.connect(null);                         // 已配对设备免码重连
    void refresh();
    _startPolling();
  } catch (e) {
    _tunnel = null;
    _showDeckOffline(e);
  }
}

function _showDeckOffline(_e: unknown): void {
  _stopPolling();
  const list = document.getElementById("aw-list");
  if (!list) return;
  // 免码重连失败:客户端分不清"console 不在线"与"授权已被吊销"(两者都以关连告终),
  // 所以给一条覆盖两者的诚实文案,不猜。
  list.innerHTML = "";
  const box = el("div", { class: "aw-offline" },
    el("div", { class: "aw-empty-ico", text: "🔌" }),
    el("div", { text: t("away.deck_offline") }));
  const repair = el("button", { class: "aw-btn aw-btn-repair", text: t("away.repair") });
  repair.addEventListener("click", () => { TN().clearIdentity(); showPairing(); });
  box.appendChild(repair);
  list.appendChild(box);
}

// ---- 卡片 / 拍板 / 刷新(隧道-only:全经 _tunnel.tunnelFetch)----
// 复用 m.ts 对抗验收过的两条纪律:按卡互斥(A 卡在飞不挡 B 卡),按 proposal_id diff 不整列重建。
async function _decide(p: Record<string, unknown>, decision: string, card: HTMLElement): Promise<void> {
  if (card.classList.contains("m-card-busy")) return;
  if (!_tunnel || !_tunnel.connected) { _toast(t("away.err_offline")); return; }
  card.classList.add("m-card-busy");
  try {
    const r = await _tunnel.tunnelFetch("/api/h2a_decide", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal_id: p.proposal_id, decision, reason: "" }),
    });
    if (r.ok) {
      card.classList.add("m-card-done");
      window.setTimeout(() => { void refresh(); }, 350);
    } else {
      card.classList.remove("m-card-busy");
      _toast(t("m.decide_failed", { code: r.status }));
    }
  } catch (e) {
    card.classList.remove("m-card-busy");
    _toast(t("m.net_failed"));
  }
}

function _toast(msg: string): void {
  const old = document.querySelector(".m-toast");
  if (old) old.remove();
  const n = el("div", { class: "m-toast", text: msg });
  document.body.appendChild(n);
  window.setTimeout(() => n.remove(), 2600);
}

function _card(p: Record<string, unknown>): HTMLElement {
  const card = el("div", { class: "m-card", "data-pid": String(p.proposal_id || "") });
  card.appendChild(el("div", { class: "m-card-summary", text: String(p.summary || "?") }));
  if (p.basis) card.appendChild(el("div", { class: "m-card-basis", text: String(p.basis) }));
  const row = el("div", { class: "m-btn-row" });
  row.appendChild(el("button", { class: "m-btn m-btn-accept", text: t("m.accept"),
    onclick: () => { void _decide(p, "ACCEPT", card); } }));
  row.appendChild(el("button", { class: "m-btn m-btn-defer", text: t("m.defer"),
    onclick: () => { void _decide(p, "DEFER", card); } }));
  row.appendChild(el("button", { class: "m-btn m-btn-reject", text: t("m.reject"),
    onclick: () => { void _decide(p, "REJECT", card); } }));
  card.appendChild(row);
  return card;
}

async function refresh(): Promise<void> {
  const list = document.getElementById("aw-list");
  if (!list) return;
  if (!_tunnel || !_tunnel.connected) {          // 掉线 → 尝试免码重连一次;仍不行=离线态
    try { await _reconnect(); _startPolling(); } catch (e) { _showDeckOffline(e); return; }
  }
  let data: Record<string, unknown> | null = null;
  try {
    const r = await _tunnel!.tunnelFetch("/api/proposals/pending");
    if (r.ok) data = await r.json() as Record<string, unknown>;
  } catch (e) { return; }                        // 单次失败 → 保持上一屏,不闪空
  if (data == null) return;
  const proposals = (data.proposals as Record<string, unknown>[]) || [];
  const badge = document.getElementById("aw-count");
  if (badge) badge.textContent = proposals.length ? String(proposals.length) : "";
  // 按 proposal_id diff(m.ts P2 纪律):不整列重建 → 手指下的卡不因列表上移变成别的卡。
  const want = new Set(proposals.map((p) => String(p.proposal_id || "")));
  const have = new Map<string, HTMLElement>();
  list.querySelectorAll<HTMLElement>(".m-card[data-pid]").forEach((n) => {
    const pid = n.getAttribute("data-pid") || "";
    if (want.has(pid)) have.set(pid, n);
    else n.remove();
  });
  const emptyNode = list.querySelector(".aw-empty");
  if (proposals.length && emptyNode) emptyNode.remove();
  for (const p of proposals) {
    const pid = String(p.proposal_id || "");
    if (have.has(pid)) continue;
    const card = _card(p);
    list.appendChild(card);
    have.set(pid, card);
  }
  if (!proposals.length && !emptyNode) {
    list.appendChild(el("div", { class: "aw-empty" },
      el("div", { class: "aw-empty-ico", text: "🦫" }),
      el("div", { text: t("away.empty") })));
  }
}

async function _reconnect(): Promise<void> {
  const id = TN().loadIdentity();
  if (!id) throw new Error("no identity");
  const tn = new (TN().Tunnel)(id);
  _useTunnel(tn);
  await tn.connect(null);
}

function _startPolling(): void {
  if (_timer !== null) return;
  _timer = window.setInterval(() => {
    if (!document.hidden) void refresh();          // 页面隐藏即停(省电/省隧道)
  }, 8000);
}

function _stopPolling(): void {
  if (_timer !== null) { window.clearInterval(_timer); _timer = null; }
}

function boot(): void {
  const id = TN().loadIdentity();
  if (id) showDeck();
  else showPairing();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && _tunnel) void refresh();
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();

const KarvyAway = { refresh, showPairing, showDeck };
(window as unknown as { KarvyAway: typeof KarvyAway }).KarvyAway = KarvyAway;
export { KarvyAway };
