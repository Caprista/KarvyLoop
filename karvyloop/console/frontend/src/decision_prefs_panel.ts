/* decision_prefs_panel.ts — 🗳 决策偏好面(§11 决策接口结晶的 UI;从 app.js 抽出)。
 * 楔子那根"独苗"的可见+可控面:列出系统学到的你的决策偏好(约束/品味/站位)+ 复利信号(教会几条/接受率趋势)
 * + 确认 / 编辑 / **撤回**(产品主张"易撤回·不固化你"的第一类动作,留可审计回执)。
 * 自洽,只用 dom/modal/i18n。暴露 window.KarvyDecisionPrefs.open()。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal { openMgmtModal: (title: string) => void; mgmtBody: () => HTMLElement | null }
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

const _DPREF_LABEL: Record<string, string> = {
  constraint: "dpref.kind_constraint", taste: "dpref.kind_taste", standing: "dpref.kind_standing",
};

function _dprefSignalText(s: any): string {
  // 复利信号:教会几条 + 提案接受率趋势(样本足才报趋势,不杜撰)
  let txt = t("dpref.sig_learned", { n: s.prefs_total || 0, c: s.confirmed || 0 });
  if (s.enough_for_trend && typeof s.accept_rate === "number") {
    txt += " · " + t("dpref.sig_accept", { pct: Math.round(s.accept_rate * 100) });
    if (typeof s.trend === "number" && Math.abs(s.trend) >= 0.03) {
      txt += s.trend > 0 ? " " + t("dpref.sig_up") : " " + t("dpref.sig_down");
    }
  } else if ((s.decisions_total || 0) > 0) {
    txt += " · " + t("dpref.sig_warming", { n: s.decisions_total });
  }
  return txt;
}

// 口味命中率:"越用越像你"的可证明刻度 —— 系统在你拍板前押注"我猜你会怎么拍",拍完对账。
// 诚实:样本不足不报百分比("还在学你");趋势要两期都够样本才亮。
function _tasteHitText(s: any): string {
  if (!s || !s.taste_enough || typeof s.taste_hit_rate !== "number") {
    const need = (s && s.taste_need_more) || 0;
    return (s && (s.taste_n || 0) > 0) || need > 0
      ? t("dpref.taste_warming", { need: need })
      : "";
  }
  let txt = t("dpref.taste_rate", { pct: Math.round(s.taste_hit_rate * 100), n: s.taste_n });
  if (typeof s.taste_prev_rate === "number") {
    txt += " · " + t("dpref.taste_prev", { pct: Math.round(s.taste_prev_rate * 100) });
  }
  return txt;
}

async function renderDecisionPrefs(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const stats = await _getJSON("/api/decision_prefs/stats");
  if (stats) body.appendChild(el("div", { class: "dpref-signal", text: _dprefSignalText(stats) }));
  if (stats) {
    const tasteTxt = _tasteHitText(stats);
    if (tasteTxt) body.appendChild(el("div", { class: "dpref-signal dpref-taste", text: "🎯 " + tasteTxt }));
  }
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("dpref.subtitle") }));
  const data = await _getJSON("/api/decision_prefs");
  const prefs = (data && data.prefs) || [];
  if (!prefs.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("dpref.empty") })); return; }
  const list = el("div", { class: "mgmt-list" });
  for (const p of prefs) {
    const kindLbl = t(_DPREF_LABEL[p.kind] || "dpref.kind_taste");
    const statusBadge = el("span", {
      class: "dpref-badge " + (p.status === "confirmed" ? "confirmed" : "provisional"),
      text: p.status === "confirmed" ? t("dpref.confirmed") : t("dpref.provisional") });
    const actions = el("div", { class: "dpref-actions" });
    if (p.status !== "confirmed") {
      actions.appendChild(el("button", { class: "dpref-confirm", text: t("dpref.confirm"),
        onclick: async () => { await _postJSON("/api/decision_prefs/op", { op: "confirm", content: p.content }); await renderDecisionPrefs(); } }));
    }
    actions.appendChild(el("button", { class: "dpref-edit", text: t("dpref.edit"),
      onclick: async () => {
        const nc = window.prompt(t("dpref.edit_prompt"), p.content);
        if (nc && nc.trim() && nc.trim() !== p.content) {
          await _postJSON("/api/decision_prefs/op", { op: "edit", content: p.content, new_content: nc.trim() });
          await renderDecisionPrefs();
        }
      } }));
    // 撤回(revoke):产品主张"易撤回/不固化你"的第一类动作面 —— 主动收回它学到的偏好,
    // 留可审计回执(进 🗳 决策流水),confirmed 的也能由你撤。区别于静默 delete。
    actions.appendChild(el("button", { class: "mc-del", text: t("dpref.revoke"), title: t("dpref.revoke_hint"),
      onclick: async () => {
        if (!window.confirm(t("dpref.confirm_revoke", { c: p.content }))) return;
        await _postJSON("/api/decision_prefs/op", { op: "revoke", content: p.content });
        await renderDecisionPrefs();
      } }));
    list.appendChild(el("div", { class: "mgmt-card dpref-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { class: "dpref-kind", text: kindLbl }), " ", statusBadge),
        el("div", { class: "mc-meta dpref-content", text: p.content }),
        el("div", { class: "mc-meta dpref-strength", text: t("dpref.strength", { pct: Math.round((p.strength || 0) * 100) }) })),
      actions));
  }
  body.appendChild(list);
}

async function open(): Promise<void> {
  openMgmtModal(t("dpref.title")); await renderDecisionPrefs();
}

const KarvyDecisionPrefs = { open };
(window as unknown as { KarvyDecisionPrefs: typeof KarvyDecisionPrefs }).KarvyDecisionPrefs = KarvyDecisionPrefs;
export { KarvyDecisionPrefs };
