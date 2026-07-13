var KarvyDevicesPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  function _agoText(lastSeen) {
    if (!lastSeen) return t("devices.never_seen");
    const s = Math.max(0, Date.now() / 1e3 - lastSeen);
    if (s < 120) return t("devices.ago_now");
    if (s < 7200) return t("devices.ago_min", { n: Math.round(s / 60) });
    if (s < 172800) return t("devices.ago_hour", { n: Math.round(s / 3600) });
    return t("devices.ago_day", { n: Math.round(s / 86400) });
  }
  function _statusLight(online, isSelf) {
    const cls = isSelf || online ? "online" : "offline";
    const label = isSelf ? t("devices.self_badge") : t(online ? "devices.status_online" : "devices.status_offline");
    return el(
      "span",
      { class: "ext-light ext-light-" + cls },
      el("span", { class: "ext-dot" }),
      " ",
      el("span", { class: "ext-light-label", text: label })
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
  function _copyRow(labelKey, cmd) {
    const row = el("div", { class: "ext-claim-row" });
    row.appendChild(el("div", { class: "ext-claim-label", text: t(labelKey) }));
    row.appendChild(el("pre", { class: "ext-claim-cmd", text: cmd }));
    const btn = el("button", { class: "dpref-edit", text: t("devices.copy") });
    btn.addEventListener("click", async () => {
      const ok = await _copyText(cmd);
      btn.textContent = ok ? t("devices.copied") : t("devices.copy_manual");
      window.setTimeout(() => {
        btn.textContent = t("devices.copy");
      }, 1600);
    });
    row.appendChild(btn);
    return row;
  }
  async function _removeFlow(d, host) {
    const name = d.label || d.device_id.slice(0, 12) + "…";
    if (!window.confirm(t("devices.confirm_light", { name }))) return;
    const probe = await _postJSON("/api/mesh/devices/remove", { device_id: d.device_id });
    if (probe.ok && probe.data && probe.data.requires_confirm) {
      let msg = "";
      if (probe.data.is_self) msg += t("devices.confirm_self", { name }) + "\n\n";
      const caps = probe.data.narrowed || [];
      if (caps.length) msg += t("devices.confirm_narrowed", { name, caps: caps.join(", ") });
      if (!window.confirm(msg.trim() || t("devices.confirm_light", { name }))) return;
      const res = await _postJSON("/api/mesh/devices/remove", { device_id: d.device_id, confirm: true });
      if (!(res.ok && res.data && res.data.ok)) {
        window.alert(t("devices.remove_failed", { reason: res.data && res.data.reason || res.status }));
        return;
      }
    } else if (!(probe.ok && probe.data && probe.data.ok)) {
      window.alert(t("devices.remove_failed", { reason: probe.data && probe.data.reason || probe.status }));
      return;
    }
    await render(host);
  }
  function _deviceCard(d, host) {
    const card = el("div", { class: "mgmt-card dev-card" });
    const name = d.label || (d.device_id ? d.device_id.slice(0, 19) + "…" : "?");
    const main = el(
      "div",
      { class: "mc-main" },
      el(
        "div",
        { class: "mc-name" },
        el("span", { text: (d.is_self ? "★ " : "") + name })
      ),
      el(
        "div",
        { class: "mc-meta" },
        _statusLight(d.online, d.is_self),
        " · ",
        el("span", { text: (d.os || "?") + "/" + (d.arch || "?") }),
        el("span", { text: " · sandbox=" + (d.sandbox || "?") }),
        d.karvyloop ? el("span", { text: " · v" + d.karvyloop }) : null,
        el("span", { text: " · " + _agoText(d.last_seen) })
      )
    );
    const caps = el("div", { class: "mc-meta dev-caps" });
    if (d.capabilities && d.capabilities.length) {
      for (const c of d.capabilities) caps.appendChild(el("span", { class: "dev-cap", text: c }));
    } else {
      caps.appendChild(el("span", { text: t("devices.caps_none") }));
    }
    main.appendChild(caps);
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", {
      class: "mc-del",
      text: t("devices.remove"),
      onclick: () => {
        void _removeFlow(d, host);
      }
    }));
    card.appendChild(main);
    card.appendChild(actions);
    return card;
  }
  function _guideBoxes(host) {
    const add = el("div", { class: "ext-onboarding" });
    add.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.guide.title") }));
    add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_install") }));
    add.appendChild(_copyRow("devices.guide.cmd_install_label", "pip install karvyloop && karvyloop console"));
    add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_label") }));
    add.appendChild(_copyRow("devices.guide.cmd_label_label", 'karvyloop devices --label "my-desk-pc"'));
    add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_lan") }));
    add.appendChild(el("div", { class: "mgmt-hint", text: t("devices.guide.step_xnet") }));
    add.appendChild(_copyRow(
      "devices.guide.cmd_sync_label",
      "karvyloop mesh-sync --relay wss://<relay> --peer-room <room> --fingerprint <fp> --code <one-time-code>"
    ));
    host.appendChild(add);
    const away = el("div", { class: "ext-onboarding" });
    away.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.remote.title") }));
    away.appendChild(el("div", { class: "mgmt-hint", text: t("devices.remote.lan") }));
    away.appendChild(el("div", { class: "mgmt-hint", text: t("devices.remote.away") }));
    away.appendChild(_copyRow("devices.remote.cmd_relay_label", "karvyloop relay-serve --port 8767"));
    away.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("devices.remote.honest") }));
    host.appendChild(away);
  }
  async function _pairedSection(host) {
    const box = el("div", { class: "ext-onboarding" });
    box.appendChild(el("div", { class: "mgmt-section-title", text: t("devices.paired.title") }));
    let data = null;
    try {
      data = await _getJSON("/api/pair/devices");
    } catch (e) {
    }
    const paired = data && data.devices || [];
    if (!paired.length) {
      box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.empty") }));
      host.appendChild(box);
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    for (const p of paired) {
      const when = p.granted_at ? new Date(p.granted_at * 1e3).toLocaleDateString() : "";
      const card = el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name", text: "📱 " + (p.label || p.fingerprint || "?") }),
          el(
            "div",
            { class: "mc-meta" },
            el("span", { class: "mc-tag", text: p.scope === "read" ? t("devices.paired.scope_read") : t("devices.paired.scope_full") }),
            when ? " · " + t("devices.paired.granted", { d: when }) : ""
          )
        ),
        el("button", {
          class: "mc-del",
          text: t("devices.paired.revoke"),
          onclick: async () => {
            if (!window.confirm(t("devices.paired.revoke_confirm", { f: p.fingerprint }))) return;
            const r = await _postJSON("/api/pair/revoke", { ident: p.fingerprint });
            if (!(r && r.ok && r.data && r.data.ok)) {
              window.alert(t("devices.paired.revoke_failed"));
              return;
            }
            const body = host.closest("#mgmt-body");
            if (body) void render(body);
          }
        })
      );
      list.appendChild(card);
    }
    box.appendChild(list);
    box.appendChild(el("div", { class: "mgmt-hint", text: t("devices.paired.how") }));
    host.appendChild(box);
  }
  async function render(body) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("devices.intro") }));
    let data = null;
    try {
      data = await _getJSON("/api/mesh/devices");
    } catch (e) {
    }
    const devices = data && data.devices || [];
    if (data && data.has_identity === false) {
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
    await _pairedSection(body);
    _guideBoxes(body);
  }
  async function open() {
    openMgmtModal(t("devices.title"));
    const body = mgmtBody();
    if (!body) return;
    await render(body);
  }
  const KarvyDevicesPanel = { open };
  window.KarvyDevicesPanel = KarvyDevicesPanel;
  exports.KarvyDevicesPanel = KarvyDevicesPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
