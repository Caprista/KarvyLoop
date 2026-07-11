var KarvyExternalPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody, closeMgmtModal = _KM.closeMgmtModal;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _deps = {};
  const OFFICIAL_DOCS_HINT_KEY = "external.onboarding.docs_hint";
  function _statusLight(status) {
    const s = status === "online" ? "online" : status === "unreachable" ? "unreachable" : "offline";
    return el(
      "span",
      { class: "ext-light ext-light-" + s, title: t("external.status_" + s) },
      el("span", { class: "ext-dot" }),
      " ",
      el("span", { class: "ext-light-label", text: t("external.status_" + s) })
    );
  }
  function _externalBadge(tier) {
    const tierKey = tier === "scoped" ? "external.tier_scoped" : "external.tier_guest";
    return el(
      "span",
      { class: "ext-badge", title: t("external.badge_title") },
      "🔌 ",
      t("external.badge"),
      " · ",
      t(tierKey)
    );
  }
  function _citizenCard(c, host) {
    const card = el("div", { class: "mgmt-card ext-card" });
    const light = _statusLight(c.liveness || "offline");
    const main = el(
      "div",
      { class: "mc-main" },
      el(
        "div",
        { class: "mc-name" },
        el("span", { text: c.citizen_id || "?" }),
        " ",
        _externalBadge(c.tier || "guest")
      ),
      el(
        "div",
        { class: "mc-meta ext-meta" },
        light,
        " · ",
        el("span", { text: t("external.runtime_kind", { kind: c.runtime_kind || "—" }) }),
        c.domain_id ? el("span", { text: " · " + t("external.in_domain", { domain: c.domain_id }) }) : null,
        c.version ? el("span", { text: " · " + c.version }) : null
      )
    );
    main.appendChild(el("div", { class: "mc-meta ext-untrusted", text: t("external.untrusted_note") }));
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", {
      class: "dpref-confirm",
      text: t("external.direct_chat"),
      onclick: () => {
        const peer = c.chat_peer || { domain_id: c.domain_id || "", role: "external", agent_id: c.citizen_id };
        const label = "🔌 " + (c.citizen_id || "external");
        if (_deps.directChatPeer) {
          closeMgmtModal();
          _deps.directChatPeer(peer, label);
        }
      }
    }));
    actions.appendChild(el("button", {
      class: "dpref-edit",
      text: t("external.refresh_status"),
      onclick: async () => {
        const r = await _getJSON("/api/external/liveness?citizen_id=" + encodeURIComponent(c.citizen_id) + "&domain=" + encodeURIComponent(c.domain_id || ""));
        const st = r && r.status || "offline";
        const fresh = _statusLight(st);
        light.replaceWith(fresh);
      }
    }));
    actions.appendChild(el("button", {
      class: "mc-del",
      text: t("mgmt.delete"),
      onclick: async () => {
        if (!window.confirm(t("external.confirm_detach", { name: c.citizen_id }))) return;
        const res = await _postJSON(
          "/api/external/detach",
          { citizen_id: c.citizen_id, domain_id: c.domain_id || "" }
        );
        if (res.ok && res.data && res.data.ok) {
          if (_deps.refreshPeers) _deps.refreshPeers();
          await render(host);
        } else {
          window.alert(t("external.detach_failed", { reason: res.data && res.data.reason || res.status }));
        }
      }
    }));
    card.appendChild(main);
    card.appendChild(actions);
    return card;
  }
  async function _renderOnboarding(host) {
    const box = el("div", { class: "ext-onboarding" });
    box.appendChild(el("div", { class: "mgmt-section-title", text: t("external.onboarding.title") }));
    let d = null;
    try {
      d = await _getJSON("/api/external/onboarding");
    } catch (e) {
    }
    const present = !!(d && d.present);
    if (present) {
      box.appendChild(el("div", { class: "mgmt-hint", text: t(
        "external.onboarding.present",
        { bins: (d.found_bins || []).join(", ") || "—" }
      ) }));
    } else {
      box.appendChild(el("div", { class: "mgmt-hint", text: t("external.onboarding.absent") }));
    }
    box.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("external.onboarding.we_dont_bundle") }));
    box.appendChild(el("div", { class: "mgmt-hint", text: t(OFFICIAL_DOCS_HINT_KEY) }));
    host.appendChild(box);
  }
  async function render(body) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("external.intro") }));
    let data = null;
    try {
      data = await _getJSON("/api/external/citizens");
    } catch (e) {
    }
    const citizens = data && data.citizens || [];
    if (data && data._integration_pending) {
      body.appendChild(el("div", {
        class: "mgmt-hint ext-pending",
        text: t("external.integration_pending")
      }));
    }
    if (!citizens.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("external.empty") }));
    } else {
      const list = el("div", { class: "mgmt-list" });
      for (const c of citizens) list.appendChild(_citizenCard(c, body));
      body.appendChild(list);
    }
    await _renderOnboarding(body);
  }
  async function open(deps) {
    if (deps) _deps = deps;
    openMgmtModal(t("external.title"));
    const body = mgmtBody();
    if (!body) return;
    await render(body);
  }
  const KarvyExternalPanel = { open };
  window.KarvyExternalPanel = KarvyExternalPanel;
  exports.KarvyExternalPanel = KarvyExternalPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
