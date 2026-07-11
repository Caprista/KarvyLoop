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
  async function _copyText(text) {
    try {
      const nav = window.navigator;
      if (nav && nav.clipboard && nav.clipboard.writeText) {
        await nav.clipboard.writeText(text);
        return true;
      }
    } catch (e) {
    }
    return false;
  }
  function _pendingCard(c, host) {
    const card = el("div", { class: "mgmt-card ext-card ext-card-pending" });
    const main = el(
      "div",
      { class: "mc-main" },
      el(
        "div",
        { class: "mc-name" },
        el("span", { text: c.citizen_id || "?" }),
        " ",
        el(
          "span",
          { class: "ext-badge ext-badge-pending", title: t("external.pending_title") },
          "🔌 ",
          t("external.pending_badge")
        )
      ),
      el(
        "div",
        { class: "mc-meta ext-meta" },
        el(
          "span",
          { class: "ext-light ext-light-pending" },
          el("span", { class: "ext-dot" }),
          " ",
          el("span", { class: "ext-light-label", text: t("external.status_pending") })
        ),
        c.domain_id ? el("span", { text: " · " + t("external.in_domain", { domain: c.domain_id }) }) : null
      )
    );
    main.appendChild(el("div", { class: "mc-meta ext-pending-hint", text: t("external.pending_waiting") }));
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", {
      class: "mc-del",
      text: t("external.cancel_pending"),
      onclick: async () => {
        if (!window.confirm(t("external.confirm_cancel", { name: c.citizen_id }))) return;
        const res = await _postJSON(
          "/api/external/cancel_pending",
          { citizen_id: c.citizen_id, domain_id: c.domain_id || "" }
        );
        if (res.ok && res.data && res.data.ok) {
          await render(host);
        } else {
          window.alert(t("external.cancel_failed", { reason: res.data && res.data.reason || res.status }));
        }
      }
    }));
    card.appendChild(main);
    card.appendChild(actions);
    return card;
  }
  function _citizenCard(c, host) {
    if (c.pending) return _pendingCard(c, host);
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
  let _pollTimer = null;
  function _stopPoll() {
    if (_pollTimer !== null) {
      window.clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }
  async function _startAddFlow(host) {
    const citizenId = window.prompt(t("external.add_prompt_name"), "");
    if (!citizenId || !citizenId.trim()) return;
    const res = await _postJSON("/api/external/create_pending", { citizen_id: citizenId.trim() });
    if (!(res.ok && res.data && res.data.ok)) {
      window.alert(t("external.add_failed", { reason: res.data && res.data.reason || res.status }));
      return;
    }
    const d = res.data;
    const box = el("div", { class: "ext-claim-box" });
    box.appendChild(el("div", { class: "ext-claim-title", text: t("external.claim_ready_title", { name: citizenId.trim() }) }));
    box.appendChild(el("div", { class: "mgmt-hint ext-claim-warn", text: t("external.claim_secret_once") }));
    const mkCopyRow = (labelKey, cmd) => {
      const row = el("div", { class: "ext-claim-row" });
      row.appendChild(el("div", { class: "ext-claim-label", text: t(labelKey) }));
      const pre = el("pre", { class: "ext-claim-cmd", text: cmd });
      row.appendChild(pre);
      const btn = el("button", { class: "dpref-edit", text: t("external.copy") });
      btn.addEventListener("click", async () => {
        const ok = await _copyText(cmd);
        btn.textContent = ok ? t("external.copied") : t("external.copy_manual");
        window.setTimeout(() => {
          btn.textContent = t("external.copy");
        }, 1600);
      });
      row.appendChild(btn);
      return row;
    };
    box.appendChild(mkCopyRow("external.claim_connector_label", d.connector_cmd || ""));
    box.appendChild(mkCopyRow("external.claim_curl_label", d.curl_cmd || ""));
    box.appendChild(el("div", { class: "mgmt-hint ext-claim-waiting", text: t("external.claim_waiting") }));
    const doneBtn = el("button", { class: "dpref-confirm", text: t("external.claim_done") });
    doneBtn.addEventListener("click", async () => {
      _stopPoll();
      await render(host);
    });
    box.appendChild(doneBtn);
    host.innerHTML = "";
    host.appendChild(box);
    _stopPoll();
    const dom = d.citizen ? d.citizen.domain_id || "" : "";
    const cid = d.citizen ? d.citizen.citizen_id || citizenId.trim() : citizenId.trim();
    _pollTimer = window.setInterval(async () => {
      let data = null;
      try {
        data = await _getJSON("/api/external/citizens");
      } catch (e) {
        return;
      }
      const list = data && data.citizens || [];
      const shell = list.find((x) => x.citizen_id === cid && (x.domain_id || "") === dom);
      if (!shell || !shell.pending) {
        _stopPoll();
        if (shell && !shell.pending && _deps.refreshPeers) _deps.refreshPeers();
        await render(host);
      }
    }, 2500);
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
    _stopPoll();
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("external.intro") }));
    const addBtn = el("button", { class: "mgmt-add-btn ext-add-btn", text: t("external.add_btn") });
    addBtn.addEventListener("click", () => {
      _startAddFlow(body);
    });
    body.appendChild(addBtn);
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
