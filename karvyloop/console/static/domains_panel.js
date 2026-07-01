var KarvyDomainsPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _deps = { refreshPeers: () => {
  }, pushChatLine: () => {
  }, openPeerChat: () => {
  } };
  async function _openDomainEdit(d) {
    openMgmtModal(d.name);
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const valueIn = el("textarea", { class: "edit-area" });
    valueIn.value = d.value_md || "";
    const curAgents = /* @__PURE__ */ new Set();
    ((d.member_query || "").match(/agent:(\S+)/g) || []).forEach((m) => curAgents.add(m.slice(6)));
    const picked = new Set(curAgents);
    const picks = el("div", { class: "mgmt-picks" });
    const addChip = (id) => {
      const chip = el("span", { class: "mgmt-pick" + (picked.has(id) ? " on" : ""), text: id });
      chip.addEventListener("click", () => {
        if (picked.has(id)) {
          picked.delete(id);
          chip.classList.remove("on");
        } else {
          picked.add(id);
          chip.classList.add("on");
        }
      });
      picks.appendChild(chip);
    };
    const rolesData = await _getJSON("/api/roles");
    const libRoles = (rolesData && rolesData.roles || []).map((v) => v.id);
    const allIds = [];
    for (const id of [...libRoles, ...curAgents]) if (id && !allIds.includes(id)) allIds.push(id);
    if (!allIds.length) picks.appendChild(el("div", { class: "mgmt-hint", text: t("domain.role_none") }));
    else for (const id of allIds) addChip(id);
    const buySugar = el("button", {
      class: "mgmt-inline-link",
      text: t("domain.role_new"),
      onclick: () => window.KarvyRolesPanel.open()
    });
    const msg = _formMsg();
    const save = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.save"),
      onclick: async () => {
        const res = await _postJSON(
          "/api/domain/update",
          { domain_id: d.id, value_md: valueIn.value, agents: Array.from(picked) }
        );
        if (res.ok) {
          _deps.refreshPeers();
          open();
        } else _setMsg(msg, false, t("mgmt.failed", { err: res.data.reason || res.status }));
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("label", { text: t("domain.value_label") }),
      valueIn,
      el("label", { text: t("domain.members_label") }),
      picks,
      el("div", { class: "mc-meta", text: t("domain.members_hint") }),
      el("div", { class: "mgmt-row" }, buySugar),
      el(
        "div",
        { class: "mgmt-row" },
        save,
        el("button", { class: "mgmt-inline-link", text: t("domain.back"), onclick: () => open() })
      ),
      msg
    ));
  }
  async function renderDomainsPanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const data = await _getJSON("/api/domains");
    const rolesData = await _getJSON("/api/roles");
    const roles = rolesData && rolesData.roles || [];
    const doms = data && data.domains || [];
    {
      const peersData = await _getJSON("/api/peers");
      const allPeers = peersData && peersData.peers || [];
      const membersByDom = {};
      for (const p of allPeers) {
        if (p.is_group || p.is_private) continue;
        (membersByDom[p.domain_id] = membersByDom[p.domain_id] || []).push(p);
      }
      body.appendChild(el("div", { class: "mgmt-section-title", text: t("mgmt.org_title") }));
      const active = doms.filter((d) => d.lifecycle !== "archived");
      const seenName = /* @__PURE__ */ new Set();
      const clean = [];
      for (const d of active) {
        if (seenName.has(d.name)) continue;
        seenName.add(d.name);
        clean.push(d);
      }
      const ids = new Set(clean.map((d) => d.id));
      const childrenOf = {};
      const roots = [];
      for (const d of clean) {
        const pid = d.parent_id && ids.has(d.parent_id) ? d.parent_id : null;
        if (pid) (childrenOf[pid] = childrenOf[pid] || []).push(d);
        else roots.push(d);
      }
      if (!clean.length) {
        body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") }));
      } else {
        const tree = el("div", { class: "org-tree" });
        const renderNode = (d, depth) => {
          const node = el("div", { class: "org-domain" + (depth ? " is-sub" : "") });
          node.style.marginLeft = depth * 18 + "px";
          node.appendChild(el(
            "div",
            { class: "org-domain-head" },
            el("span", { class: "org-ico", text: depth ? "↳ 📁" : "📂" }),
            el("span", { text: d.name }),
            depth ? el("span", { class: "org-sub-badge", text: t("domain.sub_badge") }) : null
          ));
          const members = membersByDom[d.id] || [];
          const seenRole = /* @__PURE__ */ new Set();
          let shown = 0;
          for (const m of members) {
            const rk = m.role + "|" + (m.agent_id || "");
            if (seenRole.has(rk)) continue;
            seenRole.add(rk);
            shown++;
            const rid = m.role === "agent" && m.agent_id ? m.agent_id : m.role || "";
            node.appendChild(el(
              "div",
              { class: "org-role-row" },
              el(
                "button",
                {
                  class: "org-role",
                  title: t("mgmt.org_chat_hint"),
                  onclick: () => _deps.openPeerChat(m)
                },
                el("span", {
                  class: "org-role-name",
                  text: "🧑‍💼 " + (m.role || "") + (m.agent_id ? " · " + m.agent_id : "")
                }),
                el("span", { class: "org-role-go", text: "💬" })
              ),
              // #4:看它在本域的合并样子(原生范式 + 本域 value.md/deontic 准则,只读)
              el("button", {
                class: "org-role-view",
                title: t("domain.role_view_hint"),
                text: "👁",
                onclick: () => _openRoleInDomain(rid, d.id, d.name)
              })
            ));
          }
          if (!shown) node.appendChild(el("div", { class: "org-empty", text: t("mgmt.org_no_role") }));
          tree.appendChild(node);
          (childrenOf[d.id] || []).forEach((c) => renderNode(c, depth + 1));
        };
        roots.forEach((d) => renderNode(d, 0));
        body.appendChild(tree);
      }
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("mgmt.existing") }));
    if (!doms.length) body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") }));
    else {
      const list = el("div", { class: "mgmt-list" });
      for (const d of doms) {
        const archived = d.lifecycle === "archived";
        const actions = el("div", { class: "dpref-actions" });
        if (archived) {
          actions.appendChild(el("button", {
            class: "dpref-confirm",
            text: t("domain.restore"),
            onclick: async () => {
              await _postJSON("/api/domain/restore", { domain_id: d.id });
              _deps.refreshPeers();
              await renderDomainsPanel();
            }
          }));
        } else {
          actions.appendChild(el("button", {
            class: "dpref-edit",
            text: t("dpref.edit"),
            onclick: () => _openDomainEdit(d)
          }));
          actions.appendChild(el("button", {
            class: "mc-del",
            text: t("domain.archive"),
            onclick: async () => {
              if (!window.confirm(t("domain.archive_confirm", { name: d.name }))) return;
              const res = await _postJSON("/api/domain/archive", { domain_id: d.id });
              if (res.ok) {
                _deps.pushChatLine("system", t("domain.archived", { name: d.name, n: res.data.purged_cognition || 0 }));
                _deps.refreshPeers();
                await renderDomainsPanel();
              } else alert(res.data.reason || "archive failed");
            }
          }));
        }
        const badge = el("span", {
          class: "dpref-badge " + (archived ? "provisional" : "confirmed"),
          text: archived ? t("domain.archived_badge") : t("domain.active_badge")
        });
        list.appendChild(el(
          "div",
          { class: "mgmt-card" },
          el(
            "div",
            { class: "mc-main" },
            el(
              "div",
              { class: "mc-name" },
              el("span", { text: d.name }),
              " ",
              badge,
              d.parent_id ? el("span", { class: "mc-meta", text: " ⊂ 子域" }) : null
            ),
            el("div", { class: "mc-meta", text: d.id })
          ),
          actions
        ));
      }
      body.appendChild(list);
    }
    const activeDoms = doms.filter((d) => d.lifecycle !== "archived");
    const nameIn = el("input", { type: "text" });
    const valueIn = el("textarea", {});
    const pickedRoles = /* @__PURE__ */ new Set();
    const rolePicks = el("div", { class: "mgmt-picks" });
    if (!roles.length) {
      rolePicks.appendChild(el("div", { class: "mgmt-hint", text: t("domain.role_none") }));
    } else {
      for (const v of roles) {
        const chip = el("span", { class: "mgmt-pick", text: v.id });
        chip.addEventListener("click", () => {
          if (pickedRoles.has(v.id)) {
            pickedRoles.delete(v.id);
            chip.classList.remove("on");
          } else {
            pickedRoles.add(v.id);
            chip.classList.add("on");
          }
        });
        rolePicks.appendChild(chip);
      }
    }
    const buySugar = el("button", {
      class: "mgmt-inline-link",
      text: t("domain.role_new"),
      onclick: () => window.KarvyRolesPanel.open()
    });
    const parentSel = el("select", null, el("option", { value: "", text: t("domain.parent_none") }));
    for (const d of activeDoms) parentSel.appendChild(el("option", { value: d.id, text: d.name }));
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.create"),
      onclick: async () => {
        const res = await _postJSON("/api/domain/create", {
          name: nameIn.value.trim(),
          value_md: valueIn.value,
          agents: Array.from(pickedRoles),
          // 多选角色(后端 agents 优先,member_query 每个一个 agent 子句)
          parent_id: parentSel.value
        });
        if (res.ok) {
          _setMsg(msg, true, t("mgmt.created"));
          _deps.refreshPeers();
          await renderDomainsPanel();
        } else {
          _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
        }
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: t("mgmt.create_new") }),
      el("label", { text: t("mgmt.name") }),
      nameIn,
      el("label", { text: t("domain.parent_label") }),
      parentSel,
      el("label", { text: t("domain.value_label") }),
      valueIn,
      el("label", { text: t("domain.role_label") }),
      rolePicks,
      el("div", { class: "mgmt-row" }, buySugar),
      submit,
      msg
    ));
  }
  async function _openRoleInDomain(roleId, domainId, domainName) {
    openMgmtModal("👁 " + roleId + " @ " + domainName);
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const r = await _getJSON("/api/role/in_domain?role_id=" + encodeURIComponent(roleId) + "&domain_id=" + encodeURIComponent(domainId));
    if (!(r && r.ok)) {
      body.appendChild(el("div", { class: "mgmt-empty", text: r && r.reason || t("mgmt.failed", { err: "?" }) }));
      return;
    }
    const pm = r.paradigm || {};
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.native_paradigm") }));
    body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.native_hint") }));
    const slots = [
      [t("role.identity_label"), pm.identity],
      [t("role.soul_label"), pm.soul],
      [t("role.user_label"), pm.user],
      [t("role.commitment_label"), pm.commitment],
      [t("role.verify_label"), pm.verify],
      [t("role.memory_label"), pm.memory]
    ];
    for (const [label, val] of slots) {
      body.appendChild(el(
        "div",
        { class: "soul-slot" },
        el("label", { text: label }),
        el("div", { class: "soul-ro", text: val || "—" })
      ));
    }
    if ((pm.atom_ids || []).length || (pm.skill_ids || []).length) {
      body.appendChild(el(
        "div",
        { class: "mc-meta" },
        ...(pm.atom_ids || []).map((a) => el("span", { class: "mc-tag", text: "🔧 " + a })),
        ...(pm.skill_ids || []).map((s) => el("span", { class: "mc-tag mc-tag-skill", text: "🧩 " + s }))
      ));
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.inherited_guideline", { d: domainName }) }));
    body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.inherited_hint") }));
    body.appendChild(el(
      "div",
      { class: "soul-slot" },
      el("label", { text: t("domain.value_label") }),
      el("div", { class: "soul-ro", text: r.value_md || t("domain.no_value") })
    ));
    const de = r.deontic || {};
    const deRow = (label, arr, cls) => arr && arr.length ? el(
      "div",
      { class: "soul-slot" },
      el("label", { text: label }),
      el("div", { class: "deontic-list " + cls }, ...arr.map((x) => el("div", { class: "deontic-item", text: "・ " + x })))
    ) : null;
    const fb = deRow(t("domain.deontic_forbid"), de.forbid, "forbid");
    const ob = deRow(t("domain.deontic_oblige"), de.oblige, "oblige");
    const pe = deRow(t("domain.deontic_permit"), de.permit, "permit");
    if (fb) body.appendChild(fb);
    if (ob) body.appendChild(ob);
    if (pe) body.appendChild(pe);
    if (!fb && !ob && !pe) body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.deontic_none") }));
    body.appendChild(el(
      "div",
      { class: "mgmt-row" },
      el("button", { class: "mgmt-inline-link", text: t("domain.back"), onclick: () => open() })
    ));
  }
  async function open(deps) {
    if (deps) _deps = deps;
    openMgmtModal(t("mgmt.domains_title"));
    await renderDomainsPanel();
  }
  const KarvyDomainsPanel = { open };
  window.KarvyDomainsPanel = KarvyDomainsPanel;
  exports.KarvyDomainsPanel = KarvyDomainsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
