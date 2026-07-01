var KarvyAgentsPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _deps = { refreshPeers: () => {
  } };
  async function open(deps) {
    if (deps) _deps = deps;
    openMgmtModal(t("mgmt.agents_title"));
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("agent.import_hint") }));
    const idIn = el("input", { type: "text", placeholder: "imported_pm" });
    const srcSel = el(
      "select",
      null,
      el("option", { value: "generic-json", text: "generic-json" }),
      el("option", { value: "claude", text: "claude" }),
      el("option", { value: "codex", text: "codex" }),
      el("option", { value: "agent-bundle", text: "agent-bundle" })
    );
    const promptIn = el("textarea", {});
    const toolsIn = el("input", { type: "text", placeholder: "read_file, run_command" });
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: t("agent.import_btn"),
      onclick: async () => {
        const tools = toolsIn.value.split(",").map((s) => s.trim()).filter(Boolean);
        const res = await _postJSON("/api/agent/import", {
          role_id: idIn.value.trim(),
          source_type: srcSel.value,
          system_prompt: promptIn.value,
          tools
        });
        if (res.ok) {
          _setMsg(msg, true, t("agent.imported", { id: res.data.role_id }));
          _deps.refreshPeers();
        } else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: t("agent.import_title") }),
      el("label", { text: t("mgmt.name") }),
      idIn,
      el("label", { text: t("agent.source_type") }),
      srcSel,
      el("label", { text: t("agent.system_prompt") }),
      promptIn,
      el("label", { text: t("atom.tools_label") }),
      toolsIn,
      submit,
      msg
    ));
  }
  const KarvyAgentsPanel = { open };
  window.KarvyAgentsPanel = KarvyAgentsPanel;
  exports.KarvyAgentsPanel = KarvyAgentsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
