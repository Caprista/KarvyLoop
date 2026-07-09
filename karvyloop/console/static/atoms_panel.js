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
      el("button", { class: "mgmt-new-btn", text: t("mgmt.new") + " " + t("mgmt.atoms_title"), onclick: () => renderCreate() }),
      // 整理相似原子(H2A):镜像知识库「整理相似知识」—— 一次 LLM 出合并建议,逐簇你拍板(离热路径,点才跑)。
      atoms.length >= 2 ? el("button", {
        class: "mgmt-inline-link atom-consolidate-btn",
        text: t("atom.consolidate_btn"),
        onclick: () => _runConsolidate()
      }) : null
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
  async function _runConsolidate() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("atom.consolidate_btn") }));
    const backRow = el(
      "div",
      { class: "mgmt-row" },
      el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })
    );
    const status = el("div", { class: "mgmt-hint", text: t("atom.consolidating") });
    body.appendChild(status);
    body.appendChild(backRow);
    const r = await _postJSON("/api/atoms/consolidate/suggest", {});
    status.remove();
    const clusters = r.ok && r.data && r.data.clusters || [];
    if (!clusters.length) {
      body.insertBefore(el("div", { class: "mgmt-empty", text: t("atom.consolidate_none") }), backRow);
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    body.insertBefore(list, backRow);
    for (const c of clusters) {
      const card = el("div", { class: "mgmt-card consolidate-card" });
      card.appendChild(el(
        "div",
        { class: "mc-main" },
        el("div", { class: "mc-name", text: t("atom.consolidate_into", { n: (c.member_ids || []).length }) }),
        el(
          "div",
          { class: "consolidate-target" },
          c.canonical_id ? el("span", { class: "mc-tag", text: c.canonical_id }) : null,
          el("span", { text: " " + (c.merged_purpose || "") })
        )
      ));
      const mem = el("div", { class: "consolidate-members" });
      (c.member_ids || []).forEach((m) => {
        mem.appendChild(el("div", { class: "consolidate-member", text: "・ " + m }));
      });
      if (c.reason) mem.appendChild(el("div", { class: "mgmt-hint", text: c.reason }));
      card.appendChild(mem);
      const doBtn = el("button", {
        class: "dpref-confirm",
        text: t("atom.consolidate_do"),
        onclick: async () => {
          doBtn.disabled = true;
          const ar = await _postJSON(
            "/api/atoms/consolidate/apply",
            {
              canonical_id: c.canonical_id,
              member_ids: c.member_ids,
              merged_purpose: c.merged_purpose || "",
              merged_tools: c.merged_tools || []
            }
          );
          if (ar.ok && ar.data && ar.data.ok) card.replaceWith(el("div", {
            class: "mgmt-hint",
            text: t("atom.consolidate_done", { n: (ar.data.removed_atoms || []).length })
          }));
          else doBtn.disabled = false;
        }
      });
      card.appendChild(el("div", { class: "dpref-actions" }, doBtn));
      list.appendChild(card);
    }
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
