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
  const KINDS = ["decision", "hybrid", "executor", "skill"];
  async function open(deps) {
    if (deps) _deps = deps;
    openMgmtModal(t("mgmt.agents_title"));
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const singleBox = el("div", null);
    const systemBox = el("div", { style: "display:none" });
    const tabSingle = el("button", { class: "mgmt-submit", text: t("agent.mode_single") });
    const tabSystem = el("button", { class: "mgmt-submit", text: t("agent.mode_system") });
    const setMode = (sys) => {
      singleBox.style.display = sys ? "none" : "";
      systemBox.style.display = sys ? "" : "none";
      tabSingle.style.opacity = sys ? "0.55" : "1";
      tabSystem.style.opacity = sys ? "1" : "0.55";
    };
    tabSingle.onclick = () => setMode(false);
    tabSystem.onclick = () => setMode(true);
    body.appendChild(el("div", { style: "display:flex;gap:8px;margin-bottom:10px" }, tabSingle, tabSystem));
    body.appendChild(singleBox);
    body.appendChild(systemBox);
    renderSingle(singleBox);
    renderSystem(systemBox);
    setMode(false);
  }
  function renderSingle(body) {
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
  function renderSystem(body) {
    body.appendChild(el("div", { class: "mgmt-hint", text: t("agent.sys_hint") }));
    const bundleIn = el("textarea", {
      placeholder: '{"name":"…","agents":[{"name":"…","system_prompt":"…"}],"topology":{…}}',
      style: "min-height:120px"
    });
    const fileIn = el("input", { type: "file", accept: ".json,application/json" });
    fileIn.onchange = () => {
      const f = fileIn.files && fileIn.files[0];
      if (!f) return;
      const rd = new FileReader();
      rd.onload = () => {
        bundleIn.value = String(rd.result || "");
      };
      rd.readAsText(f);
    };
    const domainIn = el("input", { type: "text", placeholder: t("agent.sys_domain_ph") });
    const msg = _formMsg();
    const review = el("div", null);
    const planBtn = el("button", {
      class: "mgmt-submit",
      text: t("agent.sys_plan_btn"),
      onclick: async () => {
        review.innerHTML = "";
        let bundle;
        try {
          bundle = JSON.parse(bundleIn.value);
        } catch {
          _setMsg(msg, false, t("agent.sys_bad_json"));
          return;
        }
        _setMsg(msg, true, t("agent.sys_planning"));
        const res = await _postJSON(
          "/api/agent/import_system/plan",
          { bundle, domain_name: domainIn.value.trim() }
        );
        if (!res.ok) {
          _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
          return;
        }
        msg.textContent = "";
        renderReview(review, res.data, msg);
      }
    });
    body.appendChild(el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: t("agent.mode_system") }),
      el("label", { text: t("agent.sys_bundle_label") }),
      bundleIn,
      fileIn,
      el("label", { text: t("agent.sys_domain_label") }),
      domainIn,
      planBtn,
      msg
    ));
    body.appendChild(review);
  }
  function _sectionTitle(text) {
    return el("div", { class: "mgmt-section-title", text });
  }
  function renderReview(review, data, msg) {
    var _a, _b, _c;
    review.innerHTML = "";
    const degradations = data.degradations || [];
    const degradeBox = el("div", { style: "border:1px solid #c77;border-radius:6px;padding:8px 10px;margin:10px 0" });
    degradeBox.appendChild(el("div", { style: "font-weight:600;margin-bottom:4px", text: t("agent.sys_degrade_title") }));
    if (!degradations.length) {
      degradeBox.appendChild(el("div", { class: "mgmt-hint", text: t("agent.sys_degrade_empty") }));
    } else {
      for (const d of degradations) {
        degradeBox.appendChild(el(
          "div",
          { style: "margin:6px 0" },
          el("div", { style: "font-weight:600", text: `⚠ ${d.element || ""}` }),
          el("div", { text: String(d.why || "") }),
          el("div", { class: "mgmt-hint", text: `↳ ${d.fallback || ""}` })
        ));
      }
    }
    review.appendChild(degradeBox);
    if (data.mode === "per_agent") {
      review.appendChild(el("div", { class: "mgmt-hint", text: String(data.note || "") }));
      const names = (data.agents || []).map((a) => a.name).join(", ");
      review.appendChild(el("div", { text: t("agent.sys_per_agent_list", { names }) }));
      return;
    }
    const plan = data.plan || {};
    if ((data.agents_dropped || []).length) {
      review.appendChild(el("div", {
        class: "mgmt-hint",
        text: t("agent.sys_dropped", { names: (data.agents_dropped || []).join(", ") })
      }));
    }
    review.appendChild(_sectionTitle(t("agent.sys_domain_title")));
    const domNameIn = el("input", { type: "text" });
    domNameIn.value = ((_a = plan.domain) == null ? void 0 : _a.name) || "";
    domNameIn.oninput = () => {
      plan.domain.name = domNameIn.value;
    };
    review.appendChild(domNameIn);
    if ((_b = plan.domain) == null ? void 0 : _b.value_md) review.appendChild(el("div", { class: "mgmt-hint", text: plan.domain.value_md }));
    const deo = ((_c = plan.domain) == null ? void 0 : _c.deontic) || {};
    for (const f of deo.forbid || []) review.appendChild(el("div", { class: "mgmt-hint", text: `🚫 ${f}` }));
    for (const o of deo.oblige || []) review.appendChild(el("div", { class: "mgmt-hint", text: `📌 ${o}` }));
    const subs = plan.subdomains || [];
    if (subs.length) {
      review.appendChild(_sectionTitle(t("agent.sys_subdomains_title")));
      for (const sd of subs) {
        review.appendChild(el("div", {
          class: "mgmt-hint",
          text: `└ ${sd.name}${sd.parent_team_id ? ` (⊂ ${sd.parent_team_id})` : ""} — ${(sd.members || []).join(", ")}`
        }));
      }
    }
    review.appendChild(_sectionTitle(t("agent.sys_roles_title")));
    const tbl = el("table", { style: "width:100%;border-collapse:collapse;font-size:12px" });
    tbl.appendChild(el(
      "tr",
      null,
      el("th", { style: "text-align:left", text: t("agent.sys_col_agent") }),
      el("th", { style: "text-align:left", text: t("agent.sys_col_kind") }),
      el("th", { style: "text-align:left", text: t("agent.sys_col_identity") }),
      el("th", { style: "text-align:left", text: t("agent.sys_col_atoms") })
    ));
    for (const r of plan.roles || []) {
      const kindSel = el("select", null, ...KINDS.map((k) => el("option", { value: k, text: t(`agent.kind.${k}`) })));
      kindSel.value = r.agent_kind;
      kindSel.onchange = () => {
        r.agent_kind = kindSel.value;
      };
      tbl.appendChild(el(
        "tr",
        { style: "border-top:1px solid rgba(128,128,128,.25)" },
        el("td", { text: r.name || r.role_id }),
        el("td", null, kindSel),
        el("td", { class: "mgmt-hint", text: (r.identity || "").slice(0, 80) }),
        el("td", { text: String((r.atoms || []).length) })
      ));
    }
    review.appendChild(tbl);
    const wfs = plan.workflows || [];
    if (wfs.length) {
      review.appendChild(_sectionTitle(t("agent.sys_wf_title")));
      for (const wf of wfs) {
        review.appendChild(el("div", { style: "font-weight:600", text: `${wf.name} — ${wf.goal || ""}` }));
        for (const s of wf.steps || []) {
          const taskIn = el("input", { type: "text", style: "flex:1" });
          taskIn.value = s.task || "";
          taskIn.oninput = () => {
            s.task = taskIn.value;
          };
          const meta = [];
          if ((s.depends_on || []).length) meta.push(`⇠ ${(s.depends_on || []).join(",")}`);
          if (s.inputs) meta.push(`inputs: ${(s.inputs || []).join(",")}`);
          if (s.when) meta.push(`when: ${s.when.step} ${s.when.status || s.when.contains || s.when.equals || ""}`);
          if (s.on_fail) meta.push(`on_fail: ${s.on_fail}`);
          review.appendChild(el(
            "div",
            { style: "display:flex;gap:6px;align-items:center;margin:2px 0" },
            el("span", { style: "min-width:110px", text: `${s.id} · ${s.role_key}` }),
            taskIn,
            el("span", { class: "mgmt-hint", text: meta.join(" · ") })
          ));
        }
      }
    }
    const seeds = plan.seed_intents || [];
    if (seeds.length) {
      review.appendChild(_sectionTitle(t("agent.sys_seed_title")));
      for (const s of seeds) {
        review.appendChild(el("div", {
          class: "mgmt-hint",
          text: `🎡 ${s.topic} — ${(s.participants || []).join(", ")}`
        }));
      }
    }
    for (const rl of plan.relocations || []) {
      review.appendChild(el("div", { class: "mgmt-hint", text: `↥ ${rl.element}: ${rl.moved_to}` }));
    }
    for (const n of plan.notes || []) review.appendChild(el("div", { class: "mgmt-hint", text: `ℹ ${n}` }));
    const applyBtn = el("button", {
      class: "mgmt-submit",
      text: t("agent.sys_apply_btn"),
      onclick: async () => {
        _setMsg(msg, true, t("agent.sys_applying"));
        const res = await _postJSON("/api/agent/import_system/apply", { plan });
        if (!res.ok) {
          _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
          return;
        }
        const d = res.data;
        _setMsg(msg, true, t("agent.sys_applied", {
          domain: d.domain_name,
          roles: (d.roles_created || []).length + (d.roles_reused || []).length,
          atoms: (d.atoms_created || []).length,
          wfs: (d.workflows_saved || []).length,
          seeds: d.roundtables_seeded || 0
        }));
        if (d.note) review.appendChild(el("div", { class: "mgmt-hint", text: String(d.note) }));
        _deps.refreshPeers();
      }
    });
    review.appendChild(el("div", { style: "margin-top:10px" }, applyBtn));
  }
  const KarvyAgentsPanel = { open };
  window.KarvyAgentsPanel = KarvyAgentsPanel;
  exports.KarvyAgentsPanel = KarvyAgentsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
