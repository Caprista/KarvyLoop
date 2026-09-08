var KarvyChannelsPanelBundle = (function(exports) {
  "use strict";
  const KD = window.KarvyDom;
  const KM = window.KarvyModal;
  const el = KD.el;
  const t = (key, vars) => window.KarvyI18n.t(key, vars);
  const PENDING_EVENT = "karvy:channel-sender-pending";
  let pendingObserver = null;
  function stopPendingListener() {
    window.removeEventListener(PENDING_EVENT, onPendingSender);
    pendingObserver == null ? void 0 : pendingObserver.disconnect();
    pendingObserver = null;
  }
  function panelIsOpen() {
    var _a;
    const modal = document.getElementById("mgmt-modal");
    return !!modal && !modal.classList.contains("hidden") && ((_a = document.getElementById("mgmt-title")) == null ? void 0 : _a.textContent) === t("channels.title");
  }
  function startPendingListener() {
    stopPendingListener();
    window.addEventListener(PENDING_EVENT, onPendingSender);
    const modal = document.getElementById("mgmt-modal");
    if (!modal) return;
    const observer = new window.MutationObserver(() => {
      if (pendingObserver === observer && !panelIsOpen()) stopPendingListener();
    });
    pendingObserver = observer;
    observer.observe(modal, { attributes: true, childList: true, subtree: true });
  }
  async function onPendingSender(event) {
    if (!panelIsOpen()) {
      stopPendingListener();
      return;
    }
    const body = KM.mgmtBody();
    if (!body) return;
    const form = body.querySelector(".channel-form");
    if (!form) {
      if (body.querySelector(".channel-list")) await renderList();
      return;
    }
    const pending = event.detail || {};
    if (!pending.instance_id || pending.instance_id !== form.dataset.channelId) return;
    appendPendingSender(form, pending);
  }
  async function request(url, method, payload) {
    if (method === "POST") return KD.postJSON(url, payload || {});
    try {
      const response = await fetch(url, {
        method,
        headers: payload === void 0 ? void 0 : { "Content-Type": "application/json" },
        body: payload === void 0 ? void 0 : JSON.stringify(payload)
      });
      let data = {};
      try {
        data = await response.json();
      } catch {
      }
      return { ok: response.ok && data.ok !== false, status: response.status, data };
    } catch {
      return { ok: false, status: 0, data: {} };
    }
  }
  function errorText(result) {
    var _a, _b;
    return String(((_a = result.data) == null ? void 0 : _a.detail) || ((_b = result.data) == null ? void 0 : _b.reason) || result.status || t("channels.network_error"));
  }
  function tags(values) {
    const wrap = el("div", { class: "channel-tags" });
    values.forEach((value) => wrap.appendChild(el("span", { class: "channel-tag", text: value })));
    if (!values.length) wrap.appendChild(el("span", { class: "channel-no-senders", text: t("channels.no_senders") }));
    return wrap;
  }
  async function renderList() {
    const body = KM.mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el(
      "div",
      { class: "channel-toolbar" },
      el("div", { class: "mgmt-hint", text: t("channels.subtitle") }),
      el("button", { class: "mgmt-new-btn", text: t("channels.add"), onclick: () => openForm() })
    ));
    const data = await KD.getJSON("/api/channels/dingtalk");
    if (!data) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("channels.load_failed") }));
      return;
    }
    const instances = data.instances || [];
    const pending = data.pending_senders || [];
    if (!instances.length) body.appendChild(el("div", { class: "mgmt-empty", text: t("channels.empty") }));
    const list = el("div", { class: "mgmt-list channel-list" });
    instances.forEach((channel) => {
      const ownPending = pending.filter((item) => item.instance_id === channel.id);
      const actions = el(
        "div",
        { class: "channel-actions" },
        el("button", { class: "dpref-edit", text: t("channels.edit"), onclick: () => openForm(channel, ownPending) }),
        el("button", {
          class: channel.enabled ? "channel-disable" : "dpref-confirm",
          text: channel.enabled ? t("channels.disable") : t("channels.enable"),
          onclick: () => toggle(channel)
        }),
        el("button", { class: "mc-del", text: t("mgmt.delete"), onclick: () => remove(channel) })
      );
      const main = el(
        "div",
        { class: "mc-main" },
        el(
          "div",
          { class: "mc-name" },
          el("span", { text: "📣 " + (channel.name || channel.role || channel.client_id) }),
          el("span", { class: "channel-state " + (channel.enabled ? "on" : "off"), text: channel.enabled ? t("channels.enabled") : t("channels.disabled") })
        ),
        el("div", { class: "mc-meta", text: `DingTalk · ${channel.role}${channel.domain_id ? " · " + channel.domain_id : ""} · ${channel.client_id}` }),
        el("div", { class: "channel-label", text: t("channels.allow_senders") }),
        tags(channel.allow_senders)
      );
      list.appendChild(el("div", { class: "mgmt-card channel-card" }, main, actions));
    });
    body.appendChild(list);
  }
  function appendPendingSender(form, item) {
    if (!item.sender) return;
    let pendingWrap = form.querySelector(".channel-pending-options");
    if (!pendingWrap) {
      pendingWrap = el("div", { class: "channel-pending-options" });
      const hint = form.querySelector(".mgmt-hint");
      form.insertBefore(pendingWrap, hint);
    }
    const exists = Array.from(pendingWrap.querySelectorAll(".channel-pending")).some((candidate) => candidate.dataset.sender === item.sender);
    if (exists) return;
    const senderInput = form.querySelector("input[name='allow_senders']");
    if (!senderInput) return;
    const button = el("button", { type: "button", class: "channel-allow-btn", text: t("channels.add_pending") });
    button.addEventListener("click", () => {
      const values = senderInput.value.split(/[，,\n]/).map((value) => value.trim()).filter(Boolean);
      if (!values.includes(item.sender)) senderInput.value = [...values, item.sender].join(", ");
      button.disabled = true;
    });
    pendingWrap.appendChild(el(
      "div",
      { class: "channel-pending", "data-sender": item.sender },
      el("span", { text: t("channels.pending_sender", { name: item.sender_nick || item.sender, sender: item.sender }) }),
      button
    ));
  }
  async function openForm(channel, pending = []) {
    const body = KM.mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("button", { class: "mgmt-inline-link", text: t("channels.back"), onclick: () => renderList() }));
    const [rolesData, domainsData] = await Promise.all([KD.getJSON("/api/roles"), KD.getJSON("/api/domains")]);
    const form = el("form", { class: "mgmt-form channel-form", "data-channel-id": (channel == null ? void 0 : channel.id) || "" });
    const field = (label, name2, value, type = "text") => {
      const input = el("input", { type, name: name2, value, autocomplete: type === "password" ? "new-password" : "off" });
      form.appendChild(el("label", null, label, input));
      return input;
    };
    const select = (label, name2, current, options) => {
      const input = el("select", { name: name2 });
      options.forEach((option) => input.appendChild(el("option", {
        value: option.value,
        text: option.text,
        selected: option.value === current
      })));
      if (current && !options.some((option) => option.value === current)) {
        input.appendChild(el("option", { value: current, text: current, selected: true }));
      }
      form.appendChild(el("label", null, label, input));
      return input;
    };
    const name = field(t("channels.name"), "name", (channel == null ? void 0 : channel.name) || "");
    const clientId = field(t("channels.client_id"), "client_id", (channel == null ? void 0 : channel.client_id) || "");
    const secret = field(t("channels.client_secret"), "client_secret", "", "password");
    secret.placeholder = (channel == null ? void 0 : channel.has_client_secret) ? t("channels.secret_keep") : "";
    const role = select(t("channels.role"), "role", (channel == null ? void 0 : channel.role) || "", ((rolesData == null ? void 0 : rolesData.roles) || []).map((item) => ({
      value: String(item.id),
      text: String(item.display_name || item.nickname || item.id)
    })));
    const domain = select(t("channels.domain"), "domain_id", (channel == null ? void 0 : channel.domain_id) || "", [
      { value: "", text: t("channels.domain_none") },
      ...((domainsData == null ? void 0 : domainsData.domains) || []).filter((item) => item.lifecycle !== "archived").map((item) => ({
        value: String(item.id),
        text: String(item.name || item.id)
      }))
    ]);
    const senderInput = field(t("channels.allow_senders"), "allow_senders", ((channel == null ? void 0 : channel.allow_senders) || []).join(", "));
    pending.forEach((item) => appendPendingSender(form, item));
    form.appendChild(el("div", { class: "mgmt-hint", text: t("channels.senders_hint") }));
    const enabled = el("input", { type: "checkbox", checked: channel ? channel.enabled : true });
    form.appendChild(el("label", { class: "channel-check" }, enabled, t("channels.enabled")));
    const msg = KM.formMsg();
    form.appendChild(el("button", { type: "submit", class: "mgmt-submit", text: t("mgmt.save") }));
    form.appendChild(msg);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        name: name.value,
        client_id: clientId.value,
        client_secret: secret.value || null,
        role: role.value,
        domain_id: domain.value,
        allow_senders: senderInput.value.split(/[，,\n]/).map((value) => value.trim()).filter(Boolean),
        enabled: enabled.checked
      };
      const result = await request(channel ? `/api/channels/dingtalk/${encodeURIComponent(channel.id)}` : "/api/channels/dingtalk", channel ? "PUT" : "POST", payload);
      if (result.ok) await renderList();
      else KM.setMsg(msg, false, t("channels.save_failed", { reason: errorText(result) }));
    });
    body.appendChild(form);
  }
  async function toggle(channel) {
    const result = await request(`/api/channels/dingtalk/${encodeURIComponent(channel.id)}`, "PUT", {
      name: channel.name,
      client_id: channel.client_id,
      client_secret: null,
      role: channel.role,
      domain_id: channel.domain_id,
      allow_senders: channel.allow_senders,
      enabled: !channel.enabled
    });
    if (result.ok) await renderList();
    else alert(t("channels.save_failed", { reason: errorText(result) }));
  }
  async function remove(channel) {
    if (!window.confirm(t("channels.confirm_delete", { name: channel.name || channel.role }))) return;
    const result = await request(`/api/channels/dingtalk/${encodeURIComponent(channel.id)}`, "DELETE");
    if (result.ok) await renderList();
    else alert(t("channels.save_failed", { reason: errorText(result) }));
  }
  async function open() {
    KM.openMgmtModal(t("channels.title"), { escClose: true });
    startPendingListener();
    await renderList();
  }
  const KarvyChannelsPanel = { open };
  window.KarvyChannelsPanel = KarvyChannelsPanel;
  exports.KarvyChannelsPanel = KarvyChannelsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
