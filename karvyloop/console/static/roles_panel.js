var KarvyRolesPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const _KW = window.KarvyWidgets;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const _xferTitles = () => ({ titleLeft: t("mgmt.available"), titleRight: t("mgmt.selected"), searchPh: t("mgmt.search") });
  function _modelSelect(current) {
    const sel = el(
      "select",
      { class: "role-model" },
      el("option", { value: "", text: t("role.model_default") })
    );
    (async () => {
      const md = await _getJSON("/api/models");
      for (const m of md && md.models || []) {
        const opt = el("option", {
          value: m.id,
          text: m.name + (m.id === (md.default || "") ? t("role.model_is_default") : "")
        });
        if (m.id === (current || "")) opt.selected = true;
        sel.appendChild(opt);
      }
    })();
    return sel;
  }
  async function renderList() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const rolesData = await _getJSON("/api/roles");
    const roles = rolesData && rolesData.roles || [];
    const bar = el(
      "div",
      { class: "mgmt-toolbar" },
      el("button", { class: "mgmt-new-btn", text: t("mgmt.new") + " " + t("mgmt.roles_title"), onclick: () => renderCreate() })
    );
    body.appendChild(bar);
    await _renderResidentsGallery(body);
    if (!roles.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") }));
      return;
    }
    body.appendChild(_KW.pagedList({
      items: roles,
      pageSize: 8,
      searchPh: t("mgmt.search"),
      emptyText: t("mgmt.empty"),
      searchOf: (v) => v.id + " " + (v.identity || "") + " " + (v.atom_ids || []).join(" ") + " " + (v.skill_ids || []).join(" "),
      renderItem: (v) => {
        const tags = (v.atom_ids || []).map((a) => el("span", { class: "mc-tag", text: "🔧 " + a }));
        const skTags = (v.skill_ids || []).map((s) => el("span", { class: "mc-tag mc-tag-skill", text: "🧩 " + s }));
        return el(
          "div",
          { class: "mgmt-card" },
          el(
            "div",
            { class: "mc-main" },
            el("div", { class: "mc-name", text: v.id }),
            v.identity ? el("div", { class: "mc-meta", text: v.identity }) : null,
            tags.length || skTags.length ? el("div", { class: "mc-meta" }, ...tags, ...skTags) : null
          ),
          el(
            "div",
            { class: "dpref-actions" },
            el("button", { class: "dpref-edit", text: t("role.view_edit"), onclick: () => _openRoleEdit(v) }),
            el("button", { class: "dpref-edit", text: t("eval.btn"), onclick: () => _openRoleEvals(v.id) }),
            el("button", {
              class: "mc-del",
              text: t("mgmt.delete"),
              onclick: async () => {
                if (!window.confirm(t("mgmt.confirm_del", { name: v.id }))) return;
                let res = await _postJSON("/api/role/remove", { role_id: v.id });
                if (res.data && res.data.blocked) {
                  const names = (res.data.referenced_by || []).map((d) => d.name).join("、");
                  if (!window.confirm(t("role.del_referenced", { names }))) return;
                  res = await _postJSON("/api/role/remove", { role_id: v.id, force: true });
                }
                await renderList();
              }
            })
          )
        );
      }
    }));
  }
  async function _renderResidentsGallery(body) {
    let residents = [];
    try {
      const data = await _getJSON("/api/residents");
      residents = data && data.residents || [];
    } catch (e) {
      return;
    }
    const notIn = residents.filter((r) => !r.instantiated);
    if (!notIn.length) return;
    const sec = el("div", { class: "residents-gallery" });
    sec.appendChild(el("div", { class: "mgmt-section-title", text: t("residents.gallery_title") }));
    sec.appendChild(el("div", { class: "mgmt-hint", text: t("residents.gallery_hint") }));
    for (const r of notIn) {
      const invite = el("button", {
        class: "dpref-confirm",
        text: t("residents.invite_btn"),
        onclick: async () => {
          invite.disabled = true;
          invite.textContent = t("residents.inviting");
          const res = await _postJSON("/api/residents/invite", { id: r.id });
          if (res.ok && res.data && res.data.ok !== false) {
            await renderList();
          } else {
            invite.disabled = false;
            invite.textContent = t("residents.invite_btn");
            window.alert(t("residents.invite_failed", { reason: res.data && res.data.reason || res.status }));
          }
        }
      });
      sec.appendChild(el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name", text: r.name || r.id }),
          r.pitch ? el("div", { class: "mc-meta", text: r.pitch }) : null
        ),
        el("div", { class: "dpref-actions" }, invite)
      ));
    }
    body.appendChild(sec);
  }
  async function renderCreate() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const atomsData = await _getJSON("/api/atoms");
    const skillsData = await _getJSON("/api/skills");
    let atomIds = (atomsData && atomsData.atoms || []).map((a) => a.id);
    const skillIds = (skillsData && skillsData.skills || []).map((s) => s.name);
    const idIn = el("input", { type: "text", placeholder: "pm" });
    const identityIn = el("textarea", {});
    const soulIn = el("textarea", {});
    const userIn = el("textarea", {});
    const modelSel = _modelSelect("");
    const atomBox = el("div", {});
    let atomTL = _KW.transferList({ items: atomIds.map((id) => ({ id, label: id })), selected: [], ..._xferTitles() });
    atomBox.appendChild(atomTL.el);
    const skillTL = _KW.transferList({ items: skillIds.map((id) => ({ id, label: "🧩 " + id })), selected: [], ..._xferTitles() });
    const buyId = el("input", { type: "text", placeholder: "new_atom" });
    const buyKind = el(
      "select",
      null,
      el("option", { value: "task", text: t("atom.kind_task") }),
      el("option", { value: "daemon", text: t("atom.kind_daemon") })
    );
    const buyMsg = _formMsg();
    const buyBtn = el("button", {
      class: "mgmt-inline-link",
      text: "+ " + t("role.buy_sugar"),
      onclick: async () => {
        const id = buyId.value.trim();
        if (!id) return;
        const res = await _postJSON("/api/atom/create", { atom_id: id, kind: buyKind.value, prompt: "" });
        if (res.ok) {
          const cur = atomTL.getSelected();
          if (!cur.includes(id)) cur.push(id);
          if (!atomIds.includes(id)) atomIds = atomIds.concat([id]);
          atomBox.innerHTML = "";
          atomTL = _KW.transferList({ items: atomIds.map((x) => ({ id: x, label: x })), selected: cur, ..._xferTitles() });
          atomBox.appendChild(atomTL.el);
          buyId.value = "";
          _setMsg(buyMsg, true, t("mgmt.created"));
        } else _setMsg(buyMsg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
      }
    });
    const buyRow = el(
      "div",
      { class: "mgmt-buysugar" },
      el("div", { class: "mgmt-hint", text: t("role.buy_hint") }),
      el("div", { class: "mgmt-row" }, buyId, buyKind, buyBtn),
      buyMsg
    );
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.create"),
      onclick: async () => {
        const res = await _postJSON("/api/role/create", {
          role_id: idIn.value.trim(),
          identity: identityIn.value,
          soul: soulIn.value,
          user_desc: userIn.value,
          atom_ids: atomTL.getSelected(),
          model: modelSel.value,
          skill_ids: skillTL.getSelected()
        });
        if (res.ok) await renderList();
        else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: t("mgmt.create_new") + " · " + t("mgmt.roles_title") }),
      el("label", { text: t("mgmt.name") }),
      idIn,
      el("label", { text: t("role.identity_label") }),
      identityIn,
      el("label", { text: t("role.soul_label") }),
      soulIn,
      el("label", { text: t("role.user_label") }),
      userIn,
      el("label", { text: t("role.model_label") }),
      modelSel,
      el("label", { text: t("role.pick_atoms") }),
      atomBox,
      buyRow,
      el("label", { text: t("role.pick_skills") }),
      el("div", { class: "mgmt-hint", text: t("role.skills_hint") }),
      skillTL.el,
      el(
        "div",
        { class: "mgmt-row" },
        submit,
        el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })
      ),
      msg
    ));
  }
  async function _openRoleEdit(v) {
    openMgmtModal(v.id);
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("role.paradigm_hint") }));
    body.appendChild(el("div", { class: "paradigm-overview", text: t("role.paradigm_overview") }));
    const pmResp = await _getJSON("/api/role/paradigm?role_id=" + encodeURIComponent(v.id));
    const pm = pmResp && pmResp.paradigm || {};
    const atomsData = await _getJSON("/api/atoms");
    const skillsData = await _getJSON("/api/skills");
    const allAtoms = (atomsData && atomsData.atoms || []).map((a) => a.id);
    const allSkills = (skillsData && skillsData.skills || []).map((s) => s.name);
    const slots = [
      { key: "identity", slot: "IDENTITY", label: t("role.identity_label") },
      { key: "soul", slot: "SOUL", label: t("role.soul_label") },
      { key: "user", slot: "USER", label: t("role.user_label") },
      { key: "commitment", slot: "COMMITMENT", label: t("role.commitment_label"), hint: t("role.commitment_hint") },
      { key: "verify", slot: "VERIFY", label: t("role.verify_label") }
    ];
    const areas = {};
    const form = el("form", { class: "mgmt-form", onsubmit: (e) => e.preventDefault() });
    for (const s of slots) {
      const orig = pm[s.key] || "";
      const ta = el("textarea", { class: "edit-area" });
      ta.value = orig;
      if (!orig.trim()) {
        ta.placeholder = t("role.slot_empty_ph");
        ta.classList.add("edit-area-empty");
      }
      ta.addEventListener("input", () => ta.classList.toggle("edit-area-empty", !ta.value.trim()));
      areas[s.slot] = { ta, orig, slot: s.slot };
      form.appendChild(el(
        "div",
        { class: "soul-slot" },
        el("label", {}, s.label, s.hint ? el("span", { class: "soul-hint", text: " — " + s.hint }) : null),
        ta
      ));
    }
    form.appendChild(el(
      "div",
      { class: "soul-slot" },
      el("label", { text: t("role.memory_label") }),
      el("div", { class: "soul-ro", text: pm.memory || "—" })
    ));
    const modelSel = _modelSelect(v.model || "");
    const atomTL = _KW.transferList({ items: allAtoms.map((id) => ({ id, label: id })), selected: pm.atom_ids || [], ..._xferTitles() });
    const skillTL = _KW.transferList({ items: allSkills.map((id) => ({ id, label: "🧩 " + id })), selected: pm.skill_ids || [], ..._xferTitles() });
    form.appendChild(el("label", { text: t("role.edit_model") }));
    form.appendChild(modelSel);
    form.appendChild(el("label", { text: t("role.pick_atoms") }));
    form.appendChild(atomTL.el);
    form.appendChild(el("label", { text: t("role.pick_skills") }));
    form.appendChild(skillTL.el);
    const msg = _formMsg();
    const save = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.save"),
      onclick: async () => {
        for (const k of Object.keys(areas)) {
          const a = areas[k];
          if (a.ta.value !== a.orig) {
            const r = await _postJSON("/api/role/paradigm/update", { role_id: v.id, slot: a.slot, text: a.ta.value });
            if (!(r.ok && r.data && r.data.ok)) {
              _setMsg(msg, false, t("mgmt.failed", { err: r.data && r.data.reason || r.status }));
              return;
            }
          }
        }
        const res = await _postJSON(
          "/api/role/update",
          { role_id: v.id, model: modelSel.value, atom_ids: atomTL.getSelected(), skill_ids: skillTL.getSelected() }
        );
        if (res.ok) renderList();
        else _setMsg(msg, false, t("mgmt.failed", { err: res.data && (res.data.detail || res.data.reason) || res.status }));
      }
    });
    const completeBtn = el("button", {
      class: "mgmt-inline-link",
      text: t("role.complete_btn"),
      onclick: async () => {
        _setMsg(msg, true, t("role.completing"));
        const r = await _getJSON("/api/role/paradigm/gaps?role_id=" + encodeURIComponent(v.id));
        const sug = r && r.suggestions || {};
        let n = 0;
        for (const slot of Object.keys(sug)) {
          const a = areas[slot];
          const draft = (sug[slot] || "").trim();
          if (a && draft) {
            const cur = a.ta.value.trim();
            if (!cur || cur === "(待充实)") {
              a.ta.value = draft;
              n++;
            }
          }
        }
        if (n > 0) _setMsg(msg, true, t("role.completed_draft", { n }));
        else if (r && r.complete) _setMsg(msg, true, t("role.complete_none"));
        else _setMsg(msg, false, t("role.complete_no_llm"));
      }
    });
    form.appendChild(el(
      "div",
      { class: "mgmt-row" },
      save,
      completeBtn,
      el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })
    ));
    form.appendChild(msg);
    body.appendChild(form);
  }
  async function _openRoleEvals(roleId) {
    openMgmtModal("🧪 " + t("eval.title", { role: roleId }));
    await _renderRoleEvals(roleId);
  }
  async function _renderRoleEvals(roleId) {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("eval.subtitle") }));
    const data = await _getJSON("/api/role/evals?role_id=" + encodeURIComponent(roleId));
    const evals = data && data.evals || [];
    const runBar = el("div", { class: "dpref-actions" });
    const resultBox = el("div", {});
    runBar.appendChild(el("button", {
      class: "dpref-confirm",
      text: t("eval.run_all"),
      onclick: async () => {
        resultBox.innerHTML = "";
        resultBox.appendChild(el("div", { class: "mgmt-hint", text: t("eval.running") }));
        const r = await _postJSON("/api/role/eval/run", { role_id: roleId });
        resultBox.innerHTML = "";
        if (!r.ok || !r.data || !r.data.ok) {
          resultBox.appendChild(el("div", {
            class: "mgmt-hint",
            text: r.data && r.data.reason === "no_llm" ? t("eval.no_llm") : t("eval.run_fail")
          }));
          return;
        }
        resultBox.appendChild(el("div", {
          class: "mgmt-section-title",
          text: t("eval.score", { pass: r.data.passed, total: r.data.total })
        }));
        for (const res of r.data.results) {
          const ok = res.passed;
          const badge = el("span", {
            class: "dpref-badge " + (ok ? "confirmed" : "provisional"),
            text: ok ? "✓ " + t("eval.pass") : "✗ " + t("eval.fail")
          });
          const detail = ok ? "" : res.error ? "⚠ " + res.error : t("eval.why", {
            miss: (res.missing || []).join("、") || "—",
            forb: (res.present_forbidden || []).join("、") || "—"
          });
          resultBox.appendChild(el(
            "div",
            { class: "mgmt-card" },
            el(
              "div",
              { class: "mc-main" },
              el("div", { class: "mc-name" }, el("span", { text: res.prompt }), " ", badge),
              detail ? el("div", { class: "mc-meta", text: detail }) : null,
              el("div", { class: "mc-meta", text: (res.reply || "").slice(0, 200) })
            )
          ));
        }
      }
    }));
    if (evals.length) body.appendChild(runBar);
    body.appendChild(resultBox);
    if (!evals.length) body.appendChild(el("div", { class: "mgmt-empty", text: t("eval.empty") }));
    else {
      const list = el("div", { class: "mgmt-list" });
      for (const ev of evals) {
        list.appendChild(el(
          "div",
          { class: "mgmt-card" },
          el(
            "div",
            { class: "mc-main" },
            el("div", { class: "mc-name", text: ev.prompt }),
            el("div", { class: "mc-meta", text: t("eval.expect", {
              c: (ev.contains || []).join("、") || "—",
              a: (ev.absent || []).join("、") || "—"
            }) })
          ),
          el(
            "div",
            { class: "dpref-actions" },
            el("button", {
              class: "mc-del",
              text: t("mgmt.delete"),
              onclick: async () => {
                await _postJSON("/api/role/eval/delete", { role_id: roleId, eval_id: ev.id });
                _renderRoleEvals(roleId);
              }
            })
          )
        ));
      }
      body.appendChild(list);
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("eval.add_title") }));
    const promptIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.prompt_ph") });
    const containsIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.contains_ph") });
    const absentIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.absent_ph") });
    const split = (s) => (s || "").split(/[,，、]/).map((x) => x.trim()).filter(Boolean);
    body.appendChild(promptIn);
    body.appendChild(containsIn);
    body.appendChild(absentIn);
    body.appendChild(el("button", {
      class: "dpref-confirm",
      text: t("eval.add"),
      onclick: async () => {
        if (!(promptIn.value || "").trim()) return;
        const r = await _postJSON("/api/role/eval/add", {
          role_id: roleId,
          prompt: promptIn.value,
          contains: split(containsIn.value),
          absent: split(absentIn.value)
        });
        if (r.ok && r.data && r.data.ok) _renderRoleEvals(roleId);
        else alert(t("eval.add_fail"));
      }
    }));
    body.appendChild(el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => open() }));
  }
  async function open() {
    openMgmtModal(t("mgmt.roles_title"));
    await renderList();
  }
  const KarvyRolesPanel = { open };
  window.KarvyRolesPanel = KarvyRolesPanel;
  exports.KarvyRolesPanel = KarvyRolesPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
