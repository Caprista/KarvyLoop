var KarvyMobileBundle = (function(exports) {
  "use strict";
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (k === "class") e.className = String(v);
        else if (k === "text") e.textContent = String(v);
        else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2).toLowerCase(), v);
        else if (v != null) e.setAttribute(k, String(v));
      }
    }
    for (const c of children) {
      if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  let _timer = null;
  async function _decide(p, decision, card) {
    if (card.classList.contains("m-card-busy")) return;
    card.classList.add("m-card-busy");
    try {
      const r = await fetch("/api/h2a_decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposal_id: p.proposal_id, decision, reason: "" })
      });
      if (r.ok) {
        card.classList.add("m-card-done");
        window.setTimeout(() => {
          void refresh();
        }, 350);
      } else {
        card.classList.remove("m-card-busy");
        _toast(t("m.decide_failed", { code: r.status }));
      }
    } catch (e) {
      card.classList.remove("m-card-busy");
      _toast(t("m.net_failed"));
    }
  }
  function _toast(msg) {
    const old = document.querySelector(".m-toast");
    if (old) old.remove();
    const n = el("div", { class: "m-toast", text: msg });
    document.body.appendChild(n);
    window.setTimeout(() => n.remove(), 2600);
  }
  function _card(p) {
    const card = el("div", { class: "m-card" });
    card.appendChild(el("div", { class: "m-card-summary", text: p.summary || "?" }));
    if (p.basis) card.appendChild(el("div", { class: "m-card-basis", text: p.basis }));
    const row = el("div", { class: "m-btn-row" });
    row.appendChild(el("button", {
      class: "m-btn m-btn-accept",
      text: t("m.accept"),
      onclick: () => {
        void _decide(p, "ACCEPT", card);
      }
    }));
    row.appendChild(el("button", {
      class: "m-btn m-btn-defer",
      text: t("m.defer"),
      onclick: () => {
        void _decide(p, "DEFER", card);
      }
    }));
    row.appendChild(el("button", {
      class: "m-btn m-btn-reject",
      text: t("m.reject"),
      onclick: () => {
        void _decide(p, "REJECT", card);
      }
    }));
    card.appendChild(row);
    return card;
  }
  async function refresh() {
    const list = document.getElementById("m-list");
    if (!list) return;
    let data = null;
    try {
      const r = await fetch("/api/proposals/pending");
      if (r.ok) data = await r.json();
    } catch (e) {
    }
    if (data == null) return;
    const proposals = data.proposals || [];
    list.innerHTML = "";
    const badge = document.getElementById("m-count");
    if (badge) badge.textContent = proposals.length ? String(proposals.length) : "";
    if (!proposals.length) {
      list.appendChild(el(
        "div",
        { class: "m-empty" },
        el("div", { class: "m-empty-ico", text: "🦫" }),
        el("div", { text: t("m.empty") })
      ));
      return;
    }
    for (const p of proposals) list.appendChild(_card(p));
  }
  function _startPolling() {
    if (_timer !== null) return;
    _timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 8e3);
  }
  function boot() {
    const title = document.getElementById("m-waiting-label");
    if (title) title.textContent = t("m.waiting");
    void refresh();
    _startPolling();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void refresh();
    });
    const btn = document.getElementById("m-refresh");
    if (btn) {
      btn.setAttribute("title", t("m.refresh"));
      btn.addEventListener("click", () => {
        void refresh();
      });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
  const KarvyMobile = { refresh };
  window.KarvyMobile = KarvyMobile;
  exports.KarvyMobile = KarvyMobile;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
