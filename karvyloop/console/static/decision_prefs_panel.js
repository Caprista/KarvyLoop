var KarvyDecisionPrefsBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const _DPREF_LABEL = {
    constraint: "dpref.kind_constraint",
    taste: "dpref.kind_taste",
    standing: "dpref.kind_standing"
  };
  function _dprefSignalText(s) {
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
  function _tasteHitText(s) {
    if (!s || !s.taste_enough || typeof s.taste_hit_rate !== "number") {
      const need = s && s.taste_need_more || 0;
      return s && (s.taste_n || 0) > 0 || need > 0 ? t("dpref.taste_warming", { need }) : "";
    }
    let txt = t("dpref.taste_rate", { pct: Math.round(s.taste_hit_rate * 100), n: s.taste_n });
    if (typeof s.taste_prev_rate === "number") {
      txt += " · " + t("dpref.taste_prev", { pct: Math.round(s.taste_prev_rate * 100) });
    }
    return txt;
  }
  const _EV_DECISION_KEY = {
    ACCEPT: "dpref.ev_accept",
    REJECT: "dpref.ev_reject",
    DEFER: "dpref.ev_defer",
    EDIT: "dpref.ev_edit",
    STATE: "dpref.ev_state"
  };
  function _evWhen(ts) {
    if (!ts || !isFinite(ts)) return "";
    const d = new Date(ts * 1e3);
    return d.getMonth() + 1 + "/" + d.getDate();
  }
  function _evidenceLine(ev) {
    const when = _evWhen(Number(ev && ev.ts) || 0);
    const dec = ev && ev.decision || "";
    const what = dec ? t(_EV_DECISION_KEY[dec] || "dpref.ev_decided", { d: dec }) : t("dpref.ev_no_detail");
    const gist = ev && ev.gist || "";
    return (when ? when + " · " : "") + what + (gist ? " — " + gist : "");
  }
  function _evidencePanel(p) {
    const panel = el("div", { class: "dpref-evidence" });
    const items = p && p.evidence || [];
    if (!items.length) {
      panel.appendChild(el("div", { class: "mc-meta dpref-ev-empty", text: t("dpref.ev_empty") }));
      return panel;
    }
    for (const ev of items) {
      panel.appendChild(el("div", { class: "mc-meta dpref-ev-line", text: _evidenceLine(ev) }));
    }
    return panel;
  }
  async function renderDecisionPrefs() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const stats = await _getJSON("/api/decision_prefs/stats");
    if (stats) body.appendChild(el("div", { class: "dpref-signal", text: _dprefSignalText(stats) }));
    if (stats) {
      const tasteTxt = _tasteHitText(stats);
      if (tasteTxt) body.appendChild(el("div", { class: "dpref-signal dpref-taste", text: "🎯 " + tasteTxt }));
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("dpref.subtitle") }));
    const data = await _getJSON("/api/decision_prefs");
    const prefs = data && data.prefs || [];
    if (!prefs.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("dpref.empty") }));
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    for (const p of prefs) {
      const kindLbl = t(_DPREF_LABEL[p.kind] || "dpref.kind_taste");
      const statusBadge = el("span", {
        class: "dpref-badge " + (p.status === "confirmed" ? "confirmed" : "provisional"),
        text: p.status === "confirmed" ? t("dpref.confirmed") : t("dpref.provisional")
      });
      const actions = el("div", { class: "dpref-actions" });
      if (p.status !== "confirmed") {
        actions.appendChild(el("button", {
          class: "dpref-confirm",
          text: t("dpref.confirm"),
          onclick: async () => {
            await _postJSON("/api/decision_prefs/op", { op: "confirm", content: p.content });
            await renderDecisionPrefs();
          }
        }));
      }
      actions.appendChild(el("button", {
        class: "dpref-edit",
        text: t("dpref.edit"),
        onclick: async () => {
          const nc = window.prompt(t("dpref.edit_prompt"), p.content);
          if (nc && nc.trim() && nc.trim() !== p.content) {
            await _postJSON("/api/decision_prefs/op", { op: "edit", content: p.content, new_content: nc.trim() });
            await renderDecisionPrefs();
          }
        }
      }));
      actions.appendChild(el("button", {
        class: "mc-del",
        text: t("dpref.revoke"),
        title: t("dpref.revoke_hint"),
        onclick: async () => {
          if (!window.confirm(t("dpref.confirm_revoke", { c: p.content }))) return;
          await _postJSON("/api/decision_prefs/op", { op: "revoke", content: p.content });
          await renderDecisionPrefs();
        }
      }));
      const evPanel = _evidencePanel(p);
      evPanel.classList.add("hidden");
      const evToggle = el("button", {
        class: "mgmt-inline-link dpref-ev-toggle",
        text: t("dpref.ev_btn", { n: p.evidence_n || 0 }),
        onclick: () => {
          evPanel.classList.toggle("hidden");
        }
      });
      list.appendChild(el(
        "div",
        { class: "mgmt-card dpref-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { class: "dpref-kind", text: kindLbl }), " ", statusBadge),
          el("div", { class: "mc-meta dpref-content", text: p.content }),
          el("div", { class: "mc-meta dpref-strength", text: t("dpref.strength", { pct: Math.round((p.strength || 0) * 100) }) }),
          evToggle,
          evPanel
        ),
        actions
      ));
    }
    body.appendChild(list);
  }
  async function open() {
    openMgmtModal(t("dpref.title"));
    await renderDecisionPrefs();
  }
  const KarvyDecisionPrefs = { open };
  window.KarvyDecisionPrefs = KarvyDecisionPrefs;
  exports.KarvyDecisionPrefs = KarvyDecisionPrefs;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
