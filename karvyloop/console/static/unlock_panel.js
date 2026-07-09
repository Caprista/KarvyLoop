var KarvyUnlockPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const MCP_LINKS = [
    { label: "Official MCP Registry", url: "https://registry.modelcontextprotocol.io/" },
    { label: "PulseMCP", url: "https://www.pulsemcp.com/servers" },
    { label: "Glama", url: "https://glama.ai/mcp/servers" },
    { label: "GitHub · modelcontextprotocol/servers", url: "https://github.com/modelcontextprotocol/servers" }
  ];
  const EMAIL_SNIPPET = `channels:
  email:
    enabled: true
    smtp: {host: smtp.example.com, port: 465, user: me@example.com, password: "app password"}
    to: me@example.com`;
  const WEBHOOK_SNIPPET = `channels:
  webhook:
    enabled: true
    url: https://ntfy.sh/your-private-topic
    preset: ntfy`;
  function _statusBadge(status) {
    return el("span", {
      class: "dpref-badge " + (status === "on" ? "confirmed" : "provisional"),
      text: t("unlock.status_" + status)
    });
  }
  function _cmdRow(cmd) {
    const btn = el("button", {
      class: "mgmt-inline-link",
      text: t("unlock.copy"),
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(cmd);
          btn.textContent = t("unlock.copied");
        } catch (e) {
        }
      }
    });
    return el(
      "div",
      { class: "mc-meta unlock-cmd-row" },
      el("code", { class: "unlock-cmd", text: cmd }),
      " ",
      btn
    );
  }
  function _enableBlock(u) {
    const wrap = el("div", { class: "unlock-enable" });
    const btn = el("button", { class: "dpref-confirm", text: t("unlock.enable_btn") });
    const note = el("span", { class: "mc-meta unlock-enable-note" });
    let tries = 0;
    async function poll() {
      try {
        const r = await fetch("/api/capability/enable_status?id=" + encodeURIComponent(u.id), { cache: "no-store" });
        const st = await r.json();
        if (st && st.state === "done") {
          note.textContent = t("unlock.install_done") + (st.extra_step ? " " + t("unlock.install_extra_step", { cmd: st.extra_step }) : "");
          btn.remove();
          return;
        }
        if (st && st.state === "failed") {
          note.textContent = t("unlock.install_failed", { reason: String(st.tail || st.reason || "").slice(-160) });
          btn.disabled = false;
          btn.textContent = t("unlock.enable_retry");
          return;
        }
      } catch (e) {
      }
      if (tries++ < 200) setTimeout(poll, 3e3);
      else note.textContent = t("unlock.install_slow");
    }
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = t("unlock.installing");
      note.textContent = t("unlock.installing_note");
      try {
        const r = await fetch("/api/capability/enable", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Karvyloop-Upgrade": "1" },
          body: JSON.stringify({ id: u.id })
        });
        const d = await r.json();
        if (d && d.ok === false) {
          note.textContent = t("unlock.install_failed", { reason: d.reason || "" });
          btn.disabled = false;
          btn.textContent = t("unlock.enable_retry");
          return;
        }
      } catch (e) {
      }
      tries = 0;
      poll();
    };
    wrap.appendChild(btn);
    wrap.appendChild(note);
    return wrap;
  }
  function _card(title, status, ...rest) {
    return el(
      "div",
      { class: "mgmt-card" },
      el(
        "div",
        { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: title }), " ", _statusBadge(status)),
        ...rest
      )
    );
  }
  function _mcpCard(u) {
    const bits = [el("div", { class: "mc-meta", text: t("unlock.mcp.value") })];
    if (u.status === "missing_dep") {
      bits.push(_enableBlock(u));
      bits.push(el("div", { class: "mc-meta unlock-manual", text: t("unlock.or_manual") }));
      bits.push(_cmdRow(u.install || ""));
    } else {
      if (u.status === "on") {
        bits.push(el("div", {
          class: "mc-meta",
          text: t("unlock.mcp.configured", { n: u.detail && u.detail.servers || 0 })
        }));
      }
      bits.push(el("div", { class: "mc-meta", text: t("unlock.mcp.how") }));
      const skills = window.KarvySkillsPanel;
      if (skills && skills.openCoding) {
        bits.push(el(
          "div",
          { class: "dpref-actions" },
          el("button", {
            class: "dpref-confirm",
            text: t("unlock.mcp.action"),
            onclick: () => skills.openCoding()
          })
        ));
      }
    }
    const links = el(
      "div",
      { class: "mc-meta unlock-links" },
      el("span", { text: t("unlock.mcp.browse") + " " })
    );
    MCP_LINKS.forEach((l, i) => {
      if (i) links.appendChild(document.createTextNode(" · "));
      links.appendChild(el("a", { href: l.url, target: "_blank", rel: "noopener noreferrer", text: l.label }));
    });
    bits.push(links);
    return _card("🔌 " + t("unlock.mcp.name"), u.status, ...bits);
  }
  function _depCard(icon, key, u, extraHowKey) {
    const bits = [el("div", { class: "mc-meta", text: t("unlock." + key + ".value") })];
    if (u.status === "missing_dep") {
      bits.push(_enableBlock(u));
      bits.push(el("div", { class: "mc-meta unlock-manual", text: t("unlock.or_manual") }));
      bits.push(_cmdRow(u.install || ""));
    }
    if (extraHowKey) bits.push(el("div", { class: "mc-meta", text: t(extraHowKey) }));
    return _card(icon + " " + t("unlock." + key + ".name"), u.status, ...bits);
  }
  function _channelCard(icon, key, u, snippet) {
    const bits = [el("div", { class: "mc-meta", text: t("unlock." + key + ".value") })];
    if (u.status !== "on") {
      bits.push(el("div", { class: "mc-meta", text: t("unlock.config_note") }));
      bits.push(el("pre", { class: "unlock-snippet", text: snippet }));
      bits.push(_cmdRow(snippet));
    }
    return _card(icon + " " + t("unlock." + key + ".name"), u.status, ...bits);
  }
  function _voiceCard() {
    const w = window;
    const supported = !!(w.SpeechRecognition || w.webkitSpeechRecognition);
    return _card(
      "🎤 " + t("unlock.voice.name"),
      supported ? "on" : "unsupported",
      el("div", { class: "mc-meta", text: t("unlock.voice.value") }),
      el("div", { class: "mc-meta", text: t(supported ? "unlock.voice.how_on" : "unlock.voice.how_off") })
    );
  }
  async function open() {
    openMgmtModal(t("unlock.name"));
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(el("div", { class: "mgmt-hint", text: t("unlock.intro") }));
    const data = await _getJSON("/api/capability/unlocks");
    const byId = {};
    for (const u of data && data.unlocks || []) byId[u.id] = u;
    const list = el("div", { class: "mgmt-list" });
    if (byId["mcp"]) list.appendChild(_mcpCard(byId["mcp"]));
    if (byId["files"]) list.appendChild(_depCard("📎", "files", byId["files"]));
    if (byId["asr"]) list.appendChild(_depCard("🎙️", "asr", byId["asr"], "unlock.asr.how"));
    if (byId["ocr"]) list.appendChild(_depCard("🔤", "ocr", byId["ocr"], "unlock.ocr.how"));
    if (byId["webhook_channel"]) list.appendChild(_channelCard("📮", "webhook", byId["webhook_channel"], WEBHOOK_SNIPPET));
    if (byId["email_channel"]) list.appendChild(_channelCard("📧", "email", byId["email_channel"], EMAIL_SNIPPET));
    if (byId["relay"]) list.appendChild(_depCard("📡", "relay", byId["relay"], "unlock.relay.how"));
    if (byId["web_verify"]) list.appendChild(_depCard("🌐", "web", byId["web_verify"]));
    list.appendChild(_voiceCard());
    b.appendChild(list);
  }
  const KarvyUnlockPanel = { open };
  window.KarvyUnlockPanel = KarvyUnlockPanel;
  exports.KarvyUnlockPanel = KarvyUnlockPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
