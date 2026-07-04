var KarvyDiagnosePanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody, closeMgmtModal = _KM.closeMgmtModal;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _deps = { pushChatLine: () => {
  }, fetchPendingProposals: () => {
  } };
  function renderOpsDiagnosis(log, x) {
    const box = el("div", { class: "ops-diag" });
    box.appendChild(el("div", { class: "ops-diag-head", text: t("ops.head") }));
    box.appendChild(el("div", { class: "ops-diag-summary", text: x.summary || "" }));
    if (x.cause) box.appendChild(el(
      "div",
      {},
      el("span", { class: "ops-k", text: t("ops.cause_label") + ": " }),
      el("span", { text: x.cause })
    ));
    if (x.fix) box.appendChild(el(
      "div",
      { class: "ops-fix" },
      el("span", { class: "ops-k", text: t("ops.fix_label") + ": " }),
      el("span", { text: x.fix })
    ));
    box.appendChild(el(
      "div",
      { class: "ops-risk ops-risk-" + (x.risk || "needs_approval") },
      t("ops.risk_label") + ": " + t("ops.risk_" + (x.risk || "needs_approval"))
    ));
    log.appendChild(box);
    log.scrollTop = log.scrollHeight;
  }
  const _ICON = { ok: "✓", warn: "⚠", fail: "✗" };
  async function renderHealthCard(body) {
    const card = el("div", { class: "health-card" });
    card.appendChild(el("div", { class: "mgmt-section-title", text: t("health.title") }));
    const loading = el("div", { class: "diag-status", text: t("health.running") });
    card.appendChild(loading);
    body.appendChild(card);
    const h = await _getJSON("/api/health?online=true");
    loading.remove();
    if (!h || !h.overall) {
      card.appendChild(el("div", { class: "mgmt-empty", text: t("health.failed") }));
      return;
    }
    card.appendChild(el("div", {
      class: "health-overall health-overall-" + h.overall,
      text: t("health.overall." + h.overall)
    }));
    const findings = Array.isArray(h.findings) ? h.findings : [];
    let anyFixable = false;
    for (const f of findings) {
      const row = el("div", { class: "health-row health-row-" + (f.level || "ok") });
      row.appendChild(el("span", { class: "health-icon", text: (_ICON[f.level] || "·") + " " }));
      row.appendChild(el("span", {
        class: "health-msg",
        text: t("doctor.msg." + f.code, f.params || {})
      }));
      if (f.fixable === "auto" || f.fixable === "confirm") {
        anyFixable = true;
        row.appendChild(el("span", {
          class: "health-fixable health-fixable-" + f.fixable,
          text: " · " + t("health.fixable_" + f.fixable)
        }));
      }
      card.appendChild(row);
    }
    if (anyFixable) {
      card.appendChild(el("div", { class: "health-fix-hint", text: t("health.fix_hint") }));
    }
  }
  async function renderDiagnosePanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    await renderHealthCard(body);
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("diag.title") }));
    const status = el("div", { class: "diag-status", text: t("diag.running") });
    body.appendChild(status);
    const d = await _getJSON("/api/ops/diagnose");
    status.remove();
    if (!d) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.failed") }));
    } else if (d.healthy) {
      body.appendChild(el("div", { class: "diag-ok", text: "✓ " + t("ops.healthy") }));
    } else if (d.reason === "no_model") {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.no_model") }));
    } else if (d.diagnosis) {
      renderOpsDiagnosis(body, d.diagnosis);
      const promote = el("button", {
        class: "mgmt-submit",
        text: t("diag.promote"),
        onClick: async () => {
          promote.disabled = true;
          const r = await _postJSON("/api/ops/propose_fix", {});
          if (r.ok && r.data && r.data.proposal_id) {
            _deps.pushChatLine("system", t("diag.promoted"));
            _deps.fetchPendingProposals();
            closeMgmtModal();
          } else {
            promote.disabled = false;
            alert(t("ops.failed"));
          }
        }
      });
      body.appendChild(promote);
    } else {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.failed") }));
    }
    const again = el("button", { class: "mgmt-inline-link", text: t("diag.rerun"), onclick: renderDiagnosePanel });
    body.appendChild(again);
  }
  async function open(deps) {
    if (deps) _deps = deps;
    openMgmtModal(t("diag.title"));
    await renderDiagnosePanel();
  }
  const KarvyDiagnosePanel = { open, renderOpsDiagnosis };
  window.KarvyDiagnosePanel = KarvyDiagnosePanel;
  exports.KarvyDiagnosePanel = KarvyDiagnosePanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
