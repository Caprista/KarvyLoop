var KarvyAtomsPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const _KW = window.KarvyWidgets;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  async function renderList() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const data = await _getJSON("/api/atoms");
    const atoms = data && data.atoms || [];
    body.appendChild(el(
      "div",
      { class: "mgmt-toolbar" },
      el("button", { class: "mgmt-new-btn", text: t("mgmt.new") + " " + t("mgmt.atoms_title"), onclick: () => renderCreate() })
    ));
    if (!atoms.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") }));
      return;
    }
    body.appendChild(_KW.pagedList({
      items: atoms,
      pageSize: 8,
      searchPh: t("mgmt.search"),
      emptyText: t("mgmt.empty"),
      searchOf: (a) => a.id + " " + (a.kind || "") + " " + (a.prompt || "") + " " + (a.tools || []).join(" "),
      renderItem: (a) => el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, a.id + " ", el("span", { class: "mc-tag", text: a.kind })),
          a.prompt ? el("div", { class: "mc-meta", text: a.prompt }) : null,
          a.tools && a.tools.length ? el("div", { class: "mc-meta", text: "🔧 " + a.tools.join(", ") }) : null
        ),
        el(
          "div",
          { class: "dpref-actions" },
          el("button", { class: "dpref-edit", text: t("mgmt.edit"), onclick: () => _renderForm(a) }),
          el("button", {
            class: "mc-del",
            text: t("mgmt.delete"),
            onclick: async () => {
              if (!window.confirm(t("mgmt.confirm_del", { name: a.id }))) return;
              await _postJSON("/api/atom/remove", { atom_id: a.id });
              await renderList();
            }
          })
        )
      )
    }));
  }
  function renderCreate() {
    _renderForm(null);
  }
  function _renderForm(existing) {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const editing = !!existing;
    const idIn = el("input", { type: "text", placeholder: "web_search" });
    if (editing) {
      idIn.value = existing.id;
      idIn.readOnly = true;
      idIn.classList.add("readonly");
    }
    const kindSel = el(
      "select",
      null,
      el("option", { value: "task", text: t("atom.kind_task"), selected: !editing || existing.kind === "task" }),
      el("option", { value: "daemon", text: t("atom.kind_daemon"), selected: editing && existing.kind === "daemon" })
    );
    const promptIn = el("textarea", {});
    if (editing) promptIn.value = existing.prompt || "";
    const toolsIn = el("input", { type: "text", placeholder: "run_command, read_file" });
    if (editing) toolsIn.value = (existing.tools || []).join(", ");
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: editing ? t("mgmt.save") : t("mgmt.create"),
      onclick: async () => {
        const tools = toolsIn.value.split(",").map((s) => s.trim()).filter(Boolean);
        const res = editing ? await _postJSON("/api/atom/update", { atom_id: existing.id, kind: kindSel.value, prompt: promptIn.value, tools }) : await _postJSON("/api/atom/create", { atom_id: idIn.value.trim(), kind: kindSel.value, prompt: promptIn.value, tools });
        if (res.ok) await renderList();
        else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: (editing ? t("mgmt.edit") : t("mgmt.create_new")) + " · " + t("mgmt.atoms_title") }),
      el("label", { text: t("mgmt.name") }),
      idIn,
      editing ? null : el("div", { class: "mgmt-hint", text: t("atom.id_hint") }),
      el("label", { text: t("atom.kind") }),
      kindSel,
      el("label", { text: t("atom.prompt_label") }),
      promptIn,
      el("label", { text: t("atom.tools_label") }),
      toolsIn,
      el(
        "div",
        { class: "mgmt-row" },
        submit,
        el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })
      ),
      msg
    ));
  }
  async function open() {
    openMgmtModal(t("mgmt.atoms_title"));
    await renderList();
  }
  const KarvyAtomsPanel = { open };
  window.KarvyAtomsPanel = KarvyAtomsPanel;
  exports.KarvyAtomsPanel = KarvyAtomsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
