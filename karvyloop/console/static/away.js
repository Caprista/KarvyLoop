var KarvyAwayBundle = (function(exports) {
  "use strict";
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const TN = () => globalThis.KarvyTunnel;
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
  function _root() {
    return document.getElementById("away-root");
  }
  function _b64urlDecode(s) {
    let x = s.replace(/-/g, "+").replace(/_/g, "/");
    while (x.length % 4) x += "=";
    const bin = atob(x);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  function _parseBundle(raw) {
    const s = (raw || "").trim();
    if (!s) return null;
    let jsonText = s;
    const m = /^karvy-pair:(.+)$/s.exec(s);
    if (m) {
      try {
        jsonText = _b64urlDecode(m[1].trim());
      } catch (e) {
        return null;
      }
    }
    let d;
    try {
      d = JSON.parse(jsonText);
    } catch (e) {
      return null;
    }
    if (!d || typeof d !== "object") return null;
    const relay = String(d.relay || ""), room = String(d.room || "");
    const fingerprint = String(d.fingerprint || ""), code = String(d.code || "");
    if (!relay || !room || !fingerprint || !code) return null;
    return { relay, room, fingerprint, code };
  }
  function _classifyErr(e) {
    const msg = (e instanceof Error ? e.message : String(e || "")).toLowerCase();
    if (msg.includes("pairing_rejected")) return t("away.err_code");
    if (msg.includes("unreachable")) return t("away.err_relay");
    if (msg.includes("fingerprint") || msg.includes("confirm mac")) return t("away.err_fingerprint");
    if (msg.includes("closed during handshake") || msg.includes("connection lost") || msg.includes("console_offline") || msg.includes("timeout")) return t("away.err_offline");
    return t("away.err_generic");
  }
  let _tunnel = null;
  let _timer = null;
  function showPairing(errText) {
    _stopPolling();
    if (_tunnel) {
      try {
        _tunnel.close();
      } catch (e) {
      }
      _tunnel = null;
    }
    const root = _root();
    if (!root) return;
    root.innerHTML = "";
    const box = el("div", { class: "aw-pair" });
    box.appendChild(el("div", { class: "aw-brand", text: "🦫 KarvyLoop" }));
    box.appendChild(el("h1", { class: "aw-title", text: t("away.pair_title") }));
    box.appendChild(el("p", { class: "aw-intro", text: t("away.pair_intro") }));
    const ta = el("textarea", {
      class: "aw-input",
      id: "aw-input",
      rows: "5",
      placeholder: t("away.pair_ph")
    });
    box.appendChild(ta);
    const errNode = el("div", { class: "aw-err", id: "aw-err", text: errText || "" });
    if (!errText) errNode.style.display = "none";
    box.appendChild(errNode);
    const btn = el("button", { class: "aw-btn", id: "aw-connect", text: t("away.pair_btn") });
    btn.addEventListener("click", () => {
      void _doPair();
    });
    box.appendChild(btn);
    box.appendChild(el("p", { class: "aw-note", text: t("away.pair_note") }));
    root.appendChild(box);
  }
  function _showPairErr(text) {
    const n = document.getElementById("aw-err");
    if (n) {
      n.textContent = text;
      n.style.display = "";
    }
    const btn = document.getElementById("aw-connect");
    if (btn) {
      btn.disabled = false;
      btn.textContent = t("away.pair_btn");
    }
  }
  async function _doPair() {
    const ta = document.getElementById("aw-input");
    const btn = document.getElementById("aw-connect");
    if (!ta) return;
    const bundle = _parseBundle(ta.value);
    if (!bundle) {
      _showPairErr(t("away.err_format"));
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = t("away.pairing");
    }
    try {
      await TN().pairAndSave(bundle.relay, bundle.room, bundle.fingerprint, bundle.code);
      showDeck();
    } catch (e) {
      _showPairErr(_classifyErr(e));
    }
  }
  function showDeck() {
    const root = _root();
    if (!root) return;
    root.innerHTML = "";
    const header = el(
      "header",
      { class: "aw-header" },
      el("span", { class: "aw-hbrand", text: "🦫 KarvyLoop" }),
      el("span", { class: "aw-chip", id: "aw-chip", text: t("away.chip_connecting") }),
      el(
        "span",
        { class: "aw-waiting" },
        el("span", { id: "aw-waiting-label", text: t("away.waiting") }),
        el("span", { id: "aw-count" })
      ),
      el("button", { class: "aw-icon", id: "aw-refresh", title: t("m.refresh"), text: "↻" })
    );
    const list = el("main", { class: "aw-list", id: "aw-list" });
    root.appendChild(header);
    root.appendChild(list);
    const rbtn = document.getElementById("aw-refresh");
    if (rbtn) rbtn.addEventListener("click", () => {
      void refresh();
    });
    void _connectDeck();
  }
  function _setChip(state) {
    const chip = document.getElementById("aw-chip");
    if (!chip) return;
    chip.className = "aw-chip aw-chip-" + state;
    const key = state === "open" ? "away.chip_open" : state === "closed" ? "away.chip_closed" : "away.chip_connecting";
    chip.textContent = t(key);
  }
  async function _connectDeck() {
    const id = TN().loadIdentity();
    if (!id) {
      showPairing();
      return;
    }
    _setChip("connecting");
    _tunnel = new (TN()).Tunnel(id);
    _tunnel.onstate = (s) => {
      if (s === "open") _setChip("open");
      else if (s === "connecting") _setChip("connecting");
      else _setChip("closed");
    };
    try {
      await _tunnel.connect(null);
      void refresh();
      _startPolling();
    } catch (e) {
      _tunnel = null;
      _showDeckOffline();
    }
  }
  function _showDeckOffline(_e) {
    _stopPolling();
    const list = document.getElementById("aw-list");
    if (!list) return;
    list.innerHTML = "";
    const box = el(
      "div",
      { class: "aw-offline" },
      el("div", { class: "aw-empty-ico", text: "🔌" }),
      el("div", { text: t("away.deck_offline") })
    );
    const repair = el("button", { class: "aw-btn aw-btn-repair", text: t("away.repair") });
    repair.addEventListener("click", () => {
      TN().clearIdentity();
      showPairing();
    });
    box.appendChild(repair);
    list.appendChild(box);
  }
  async function _decide(p, decision, card) {
    if (card.classList.contains("m-card-busy")) return;
    if (!_tunnel || !_tunnel.connected) {
      _toast(t("away.err_offline"));
      return;
    }
    card.classList.add("m-card-busy");
    try {
      const r = await _tunnel.tunnelFetch("/api/h2a_decide", {
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
    const card = el("div", { class: "m-card", "data-pid": String(p.proposal_id || "") });
    card.appendChild(el("div", { class: "m-card-summary", text: String(p.summary || "?") }));
    if (p.basis) card.appendChild(el("div", { class: "m-card-basis", text: String(p.basis) }));
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
    const list = document.getElementById("aw-list");
    if (!list) return;
    if (!_tunnel || !_tunnel.connected) {
      try {
        await _reconnect();
        _startPolling();
      } catch (e) {
        _showDeckOffline();
        return;
      }
    }
    let data = null;
    try {
      const r = await _tunnel.tunnelFetch("/api/proposals/pending");
      if (r.ok) data = await r.json();
    } catch (e) {
      return;
    }
    if (data == null) return;
    const proposals = data.proposals || [];
    const badge = document.getElementById("aw-count");
    if (badge) badge.textContent = proposals.length ? String(proposals.length) : "";
    const want = new Set(proposals.map((p) => String(p.proposal_id || "")));
    const have = /* @__PURE__ */ new Map();
    list.querySelectorAll(".m-card[data-pid]").forEach((n) => {
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
      list.appendChild(el(
        "div",
        { class: "aw-empty" },
        el("div", { class: "aw-empty-ico", text: "🦫" }),
        el("div", { text: t("away.empty") })
      ));
    }
  }
  async function _reconnect() {
    const id = TN().loadIdentity();
    if (!id) throw new Error("no identity");
    _setChip("connecting");
    _tunnel = new (TN()).Tunnel(id);
    _tunnel.onstate = (s) => {
      if (s === "open") _setChip("open");
      else if (s === "connecting") _setChip("connecting");
      else _setChip("closed");
    };
    await _tunnel.connect(null);
  }
  function _startPolling() {
    if (_timer !== null) return;
    _timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 8e3);
  }
  function _stopPolling() {
    if (_timer !== null) {
      window.clearInterval(_timer);
      _timer = null;
    }
  }
  function boot() {
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
  window.KarvyAway = KarvyAway;
  exports.KarvyAway = KarvyAway;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
