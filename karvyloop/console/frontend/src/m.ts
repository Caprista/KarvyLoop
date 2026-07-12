/* m.ts — 📱 手机页(/m,R1 切片一):一屏 = 等你拍板的卡 + 大按钮,没了。
 * 低地板纪律([[avoid-ivory-tower]]):第一屏零生造名词 —— "等你拍板/同意/拒绝/稍后",
 * 不出现 H2A/L0-L4/atom/结晶。桌面 console 是全工作台;这页只做手机上最高频的一件事:拍板。
 * 布局 = 动态比例连续流动(grid auto-fill minmax,折叠屏开合实时重排),不是断点三套。
 * 契约复用(零新后端):GET /api/proposals/pending + POST /api/h2a_decide(与桌面同一条
 * K5 路径;REJECT 空理由后端补诚实占位,永不逼人打字)。轮询 8s,页面隐藏即停(省电)。
 */
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string; applyStatic?: () => void }
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

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

let _timer: number | null = null;

async function _decide(p: any, decision: string, card: HTMLElement): Promise<void> {
  // 互斥按卡不按全局(对抗验收 P3):A 卡在飞时点 B 卡照常生效;同卡连点仍只发一次。
  if (card.classList.contains("m-card-busy")) return;
  card.classList.add("m-card-busy");
  try {
    const r = await fetch("/api/h2a_decide", {
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

function _card(p: any): HTMLElement {
  const card = el("div", { class: "m-card" });
  card.appendChild(el("div", { class: "m-card-summary", text: p.summary || "?" }));
  if (p.basis) card.appendChild(el("div", { class: "m-card-basis", text: p.basis }));
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
  const list = document.getElementById("m-list");
  if (!list) return;
  let data: any = null;
  try {
    const r = await fetch("/api/proposals/pending");
    if (r.ok) data = await r.json();
  } catch (e) { /* 掉线 → 保持上一屏,不闪空 */ }
  if (data == null) return;
  const proposals: any[] = data.proposals || [];
  list.innerHTML = "";
  const badge = document.getElementById("m-count");
  if (badge) badge.textContent = proposals.length ? String(proposals.length) : "";
  if (!proposals.length) {
    list.appendChild(el("div", { class: "m-empty" },
      el("div", { class: "m-empty-ico", text: "🦫" }),
      el("div", { text: t("m.empty") })));
    return;
  }
  for (const p of proposals) list.appendChild(_card(p));
}

function _startPolling(): void {
  if (_timer !== null) return;
  _timer = window.setInterval(() => {
    if (!document.hidden) void refresh();
  }, 8000);
}

function boot(): void {
  const title = document.getElementById("m-waiting-label");
  if (title) title.textContent = t("m.waiting");
  void refresh();
  _startPolling();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
  const btn = document.getElementById("m-refresh");
  if (btn) { btn.setAttribute("title", t("m.refresh")); btn.addEventListener("click", () => { void refresh(); }); }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();

const KarvyMobile = { refresh };
(window as unknown as { KarvyMobile: typeof KarvyMobile }).KarvyMobile = KarvyMobile;
export { KarvyMobile };
