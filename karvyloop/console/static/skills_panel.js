var KarvySkillsPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  function _skillImportForm() {
    const srcIn = el("input", { type: "text", placeholder: t("skills.import_ph") });
    srcIn.style.flex = "1";
    const msg = _formMsg();
    const btn = el("button", {
      class: "mgmt-inline-link",
      text: t("skills.import_btn"),
      onclick: async () => {
        const src = srcIn.value.trim();
        if (!src) return;
        _setMsg(msg, true, t("skills.importing"));
        const res = await _postJSON("/api/skill/import", { source: src, kind: "auto" });
        if (res.ok && res.data && res.data.ok) {
          const d = res.data;
          let note = t("skills.imported", { name: d.name });
          if (d.has_scripts) note += " " + t("skills.imported_scripts");
          _setMsg(msg, true, note);
          srcIn.value = "";
          await renderSkillsPanel();
        } else {
          _setMsg(msg, false, t("mgmt.failed", { err: res.data && (res.data.reason || res.data.detail) || res.status }));
        }
      }
    });
    return el(
      "div",
      { class: "mgmt-buysugar" },
      el("div", { class: "mgmt-hint", text: t("skills.import_hint") }),
      el("div", { class: "mgmt-row" }, srcIn, btn),
      msg,
      _skillCatalog()
    );
  }
  function _skillCatalog() {
    const qIn = el("input", { type: "text", placeholder: t("skills.catalog_ph") });
    qIn.style.flex = "1";
    const srcSel = el(
      "select",
      null,
      el("option", { value: "all", text: t("skills.cat_all") }),
      el("option", { value: "official", text: t("skills.cat_official") }),
      el("option", { value: "market", text: t("skills.cat_market") })
    );
    const results = el("div", { class: "skill-catalog" });
    const search = async () => {
      results.textContent = t("skills.catalog_loading");
      const r = await _getJSON("/api/skill/catalog?source=" + encodeURIComponent(srcSel.value) + "&q=" + encodeURIComponent(qIn.value.trim()));
      const entries = r && r.entries || [];
      results.innerHTML = "";
      if (!entries.length) {
        results.appendChild(el("div", { class: "mgmt-hint", text: t("skills.catalog_empty") }));
        return;
      }
      for (const e of entries) {
        const tag = el("span", {
          class: "mc-tag" + (e.origin === "official" ? "" : " mc-tag-skill"),
          text: (e.origin === "official" ? "✓ " : "🌐 ") + e.origin + (e.stars ? " ★" + e.stars : "")
        });
        const imp = el("button", {
          class: "mgmt-inline-link",
          text: t("skills.catalog_import"),
          onclick: async () => {
            imp.textContent = t("skills.importing");
            const res = await _postJSON("/api/skill/import", { source: e.source, kind: "github" });
            if (res.ok && res.data && res.data.ok) {
              await renderSkillsPanel();
            } else {
              imp.textContent = t("mgmt.failed", { err: res.data && (res.data.reason || res.data.detail) || res.status });
            }
          }
        });
        results.appendChild(el(
          "div",
          { class: "skill-cat-row" },
          el(
            "div",
            { class: "mc-main" },
            el(
              "div",
              { class: "mc-name" },
              el("span", { text: "🧩 " + e.name }),
              " ",
              tag,
              e.author ? el("span", { class: "mc-meta", text: " · " + e.author }) : null
            ),
            e.description ? el("div", { class: "mc-meta", text: e.description }) : null
          ),
          imp
        ));
      }
    };
    const goBtn = el("button", { class: "mgmt-inline-link", text: t("skills.catalog_btn"), onclick: search });
    return el(
      "div",
      { class: "skill-catalog-wrap" },
      el("div", { class: "mgmt-hint", text: t("skills.catalog_hint") }),
      el("div", { class: "mgmt-row" }, qIn, srcSel, goBtn),
      results,
      _skillSourcesManager()
    );
  }
  function _skillSourcesManager() {
    const wrap = el("div", { class: "skill-sources-wrap" });
    const panel = el("div", { class: "skill-sources hidden" });
    const toggle = el("button", {
      class: "mgmt-inline-link",
      text: "⚙ " + t("skills.src_manage"),
      onclick: async () => {
        panel.classList.toggle("hidden");
        if (!panel.classList.contains("hidden")) await render();
      }
    });
    const msg = _formMsg();
    async function render() {
      panel.innerHTML = "";
      const data = await _getJSON("/api/skill/sources");
      if (data && data.no_llm) {
        panel.appendChild(el("div", { class: "mgmt-hint", text: t("skills.no_llm") }));
        return;
      }
      const rows = [];
      const list = el("div", {});
      function addRow(src) {
        const enabled = el("input", { type: "checkbox" });
        enabled.checked = src.enabled !== false;
        const label = el("input", { type: "text" });
        label.value = src.label || src.id;
        label.style.flex = "1";
        const repo = el("input", { type: "text", placeholder: "owner/repo" });
        repo.value = src.repo || "";
        repo.style.display = src.type === "github" ? "" : "none";
        const del = el("button", {
          class: "mgmt-inline-link",
          text: "✕",
          onclick: () => {
            rows.splice(rows.indexOf(rec), 1);
            row.remove();
          }
        });
        const row = el(
          "div",
          { class: "mgmt-row skill-src-row" },
          enabled,
          el("span", { class: "mc-tag", text: src.type }),
          label,
          repo,
          del
        );
        const rec = { src, enabled, label, repo };
        rows.push(rec);
        list.appendChild(row);
      }
      for (const s of data && data.sources || []) addRow(s);
      panel.appendChild(list);
      const newId = el("input", { type: "text", placeholder: "id" });
      const newRepo = el("input", { type: "text", placeholder: "owner/repo" });
      const addBtn = el("button", {
        class: "mgmt-inline-link",
        text: "+ " + t("skills.src_add_github"),
        onclick: () => {
          const id = newId.value.trim();
          const r = newRepo.value.trim();
          if (!id || !r) return;
          addRow({ id, label: id, type: "github", repo: r, root: "skills", ref: "main", enabled: true });
          newId.value = "";
          newRepo.value = "";
        }
      });
      panel.appendChild(el("div", { class: "mgmt-row" }, newId, newRepo, addBtn));
      const save = el("button", {
        class: "mgmt-submit",
        text: t("skills.src_save"),
        onclick: async () => {
          const payload = rows.map((rec) => Object.assign(
            {},
            rec.src,
            {
              enabled: rec.enabled.checked,
              label: rec.label.value.trim() || rec.src.id,
              repo: rec.src.type === "github" ? rec.repo.value.trim() || rec.src.repo : void 0
            }
          ));
          const res = await _postJSON("/api/skill/sources", { sources: payload });
          if (res.ok && res.data && res.data.ok) _setMsg(msg, true, t("skills.src_saved"));
          else _setMsg(msg, false, res.data && res.data.reason || t("mgmt.failed", { err: res.status }));
        }
      });
      panel.appendChild(el("div", { class: "mgmt-row" }, save));
      panel.appendChild(msg);
    }
    wrap.appendChild(toggle);
    wrap.appendChild(panel);
    return wrap;
  }
  async function renderSkillsPanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("skills.subtitle") }));
    const data = await _getJSON("/api/skills");
    if (data && data.no_llm) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("skills.no_llm") }));
      return;
    }
    await _renderCodingCapability(body);
    _renderCapabilityOverviewCard(body);
    body.appendChild(_skillImportForm());
    const skills = data && data.skills || [];
    if (!skills.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("skills.empty") }));
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    for (const s of skills) {
      const archived = !!s.archived;
      const badge = el("span", {
        class: "dpref-badge " + (archived ? "provisional" : "confirmed"),
        text: archived ? t("skills.archived_badge") : t("skills.active_badge")
      });
      const st = s.status || "pending";
      const stCls = st === "crystallized" ? "confirmed" : st === "unverified" ? "provisional" : "provisional";
      const stBadge = el("span", { class: "dpref-badge " + stCls, text: t("skills.status_" + st) });
      const tpBadge = s.third_party ? el("span", {
        class: "dpref-badge provisional",
        title: t("skills.untrusted_hint"),
        text: "🌐 " + t("skills.third_party_badge")
      }) : null;
      const stats = t("skills.stats", { recall: s.recall_count || 0, use: s.usage_count || 0, ok: s.success_count || 0 });
      const actions = el("div", { class: "dpref-actions" });
      if (archived) {
        actions.appendChild(el("button", {
          class: "dpref-confirm",
          text: t("skills.restore"),
          onclick: async () => {
            await _postJSON("/api/skill/restore", { sig: s.sig });
            await renderSkillsPanel();
          }
        }));
      }
      actions.appendChild(el("button", {
        class: "dpref-edit",
        text: t("skills.view"),
        onclick: () => _openSkillDetail(s)
      }));
      list.appendChild(el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el(
            "div",
            { class: "mc-name" },
            el("span", { text: "🧩 " + s.name }),
            " ",
            stBadge,
            " ",
            badge,
            tpBadge ? " " : null,
            tpBadge
          ),
          el("div", { class: "mc-meta", text: s.when_to_use || s.description || "" }),
          el("div", { class: "mc-meta", text: stats })
        ),
        actions
      ));
    }
    body.appendChild(list);
  }
  async function _renderCodingCapability(body) {
    const cap = await _getJSON("/api/coding/capability");
    if (!cap || !cap.tools) return;
    const builtinBadge = el("span", { class: "dpref-badge confirmed", text: t("coding.builtin_badge") });
    const execBadge = el("span", { class: "dpref-badge confirmed", text: t("coding.exec_forge") });
    const sbBadge = el("span", {
      class: "dpref-badge confirmed",
      title: t("coding.sandboxed_hint"),
      text: "🛡 " + t("coding.sandboxed")
    });
    const extBadge = cap.external_executor ? el("span", {
      class: "dpref-badge provisional",
      title: t("coding.unsandboxed_hint"),
      text: "⚙ " + t("coding.ext_saved_badge")
    }) : null;
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", {
      class: "dpref-edit",
      text: t("skills.view"),
      onclick: () => _openCodingDetail(cap)
    }));
    body.appendChild(el(
      "div",
      { class: "mgmt-list" },
      el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el(
            "div",
            { class: "mc-name" },
            el("span", { text: "🛠 " + t("coding.name") }),
            " ",
            builtinBadge,
            " ",
            execBadge,
            " ",
            sbBadge,
            extBadge ? " " : null,
            extBadge
          ),
          el("div", { class: "mc-meta", text: t("coding.subtitle") }),
          el("div", { class: "mc-meta", text: t("coding.tool_count", { n: cap.tools.length }) })
        ),
        actions
      )
    ));
  }
  function _openCodingDetail(cap) {
    openMgmtModal(t("coding.name"));
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(el("div", { class: "mgmt-section-title", text: t("coding.detail_title") }));
    b.appendChild(el("div", { class: "mgmt-hint", text: t("coding.exec_line_forge") }));
    const editWrap = el("div", { class: "mgmt-buysugar" });
    editWrap.appendChild(el("div", { class: "mgmt-section-title", text: t("coding.ext_title") }));
    editWrap.appendChild(el("div", { class: "mgmt-hint", text: t("coding.pluggable_note") }));
    const inp = el("input", {
      class: "mgmt-input",
      type: "text",
      placeholder: t("coding.ext_placeholder"),
      value: cap.external_executor || ""
    });
    const status = el("div", { class: "mgmt-hint" });
    const _setStatus = () => {
      status.textContent = (inp.value || "").trim() ? t("coding.ext_saved_note", { cmd: (inp.value || "").trim() }) : t("coding.ext_none_note");
    };
    _setStatus();
    const save = el("button", {
      class: "dpref-confirm",
      text: t("coding.ext_save"),
      onclick: async () => {
        const r = await _postJSON("/api/coding/config", { external_executor: (inp.value || "").trim() });
        if (r.ok && r.data && r.data.ok) {
          cap.external_executor = r.data.external_executor;
          _setStatus();
        } else alert(t("coding.ext_save_fail"));
      }
    });
    const clear = el("button", {
      class: "dpref-edit",
      text: t("coding.ext_clear"),
      onclick: async () => {
        inp.value = "";
        const r = await _postJSON("/api/coding/config", { external_executor: "" });
        if (r.ok && r.data && r.data.ok) {
          cap.external_executor = null;
          _setStatus();
        }
      }
    });
    editWrap.appendChild(inp);
    editWrap.appendChild(el("div", { class: "dpref-actions" }, save, clear));
    editWrap.appendChild(status);
    b.appendChild(editWrap);
    const list = el("div", { class: "mgmt-list" });
    for (const tl of cap.tools) {
      const kindBadge = el("span", {
        class: "dpref-badge " + (tl.kind === "mcp" ? "provisional" : "confirmed"),
        text: tl.kind === "mcp" ? "MCP" : t("coding.builtin_badge")
      });
      list.appendChild(el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { text: "· " + tl.name }), " ", kindBadge),
          el("div", { class: "mc-meta", text: (tl.description || "").slice(0, 200) })
        )
      ));
    }
    b.appendChild(list);
  }
  function _renderCapabilityOverviewCard(body) {
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", {
      class: "dpref-edit",
      text: t("skills.view"),
      onclick: () => _openCapabilityOverview()
    }));
    body.appendChild(el(
      "div",
      { class: "mgmt-list" },
      el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { text: "🔐 " + t("capov.name") })),
          el("div", { class: "mc-meta", text: t("capov.subtitle") })
        ),
        actions
      )
    ));
  }
  async function _openCapabilityOverview() {
    openMgmtModal(t("capov.name"));
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    const ov = await _getJSON("/api/capability/overview");
    if (!ov) {
      b.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.failed", { err: "" }) }));
      return;
    }
    b.appendChild(el("div", { class: "mgmt-section-title", text: t("capov.tools_title") }));
    b.appendChild(el("div", { class: "mgmt-hint", text: t("capov.tools_hint") }));
    const tl = el("div", { class: "mgmt-list" });
    for (const t_ of ov.tools || []) {
      const mode = t_.required_mode || "full";
      const modeCls = mode === "read_only" ? "confirmed" : mode === "workspace_write" ? "provisional" : "";
      tl.appendChild(el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el(
            "div",
            { class: "mc-name" },
            el("span", { text: "· " + t_.name }),
            " ",
            el("span", { class: "dpref-badge " + modeCls, text: t("capov.mode_" + mode) }),
            t_.kind === "mcp" ? " " : null,
            t_.kind === "mcp" ? el("span", { class: "dpref-badge provisional", text: "MCP" }) : null
          )
        )
      ));
    }
    b.appendChild(tl);
    b.appendChild(el("div", { class: "mgmt-section-title", text: t("capov.skills_title") }));
    b.appendChild(el("div", { class: "mgmt-hint", text: t("capov.skills_hint") }));
    const sl = el("div", { class: "mgmt-list" });
    const skl = ov.skills || [];
    if (!skl.length) sl.appendChild(el("div", { class: "mgmt-empty", text: t("skills.empty") }));
    for (const s of skl) {
      const trustBadge = el("span", {
        class: "dpref-badge " + (s.trust === "trusted" ? "confirmed" : "provisional"),
        text: t("capov.trust_" + s.trust)
      });
      const bits = [el("span", { text: "🧩 " + s.name }), " ", trustBadge];
      if (s.net_granted) {
        bits.push(" ");
        bits.push(el("span", { class: "dpref-badge provisional", text: "🌐 " + t("capov.net_on") }));
      }
      if (s.lock) {
        const lockCls = s.lock === "ok" ? "confirmed" : "provisional";
        bits.push(" ");
        bits.push(el("span", { class: "dpref-badge " + lockCls, text: "🔒 " + t("capov.lock_" + s.lock) }));
      }
      sl.appendChild(el(
        "div",
        { class: "mgmt-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, ...bits),
          el("div", { class: "mc-meta", text: s.has_scripts ? t("capov.has_scripts") : t("capov.no_scripts") })
        )
      ));
    }
    b.appendChild(sl);
  }
  function _openSkillDetail(s) {
    openMgmtModal(s.name);
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(el("div", { class: "mgmt-section-title", text: t("skills.when", { w: s.when_to_use || "—" }) }));
    const scripts = s.scripts || [];
    if (scripts.length) {
      const runWrap = el("div", { class: "mgmt-buysugar" });
      runWrap.appendChild(el("div", {
        class: "mgmt-hint",
        text: s.untrusted ? t("skills.run_hint_untrusted") : t("skills.run_hint")
      }));
      let netGranted = !!s.net_granted;
      const netChk = el("input", { type: "checkbox" });
      netChk.checked = netGranted;
      netChk.addEventListener("change", async () => {
        const res = await _postJSON("/api/skill/grant", { name: s.name, net: netChk.checked });
        if (res.ok && res.data && res.data.ok) netGranted = netChk.checked;
        else netChk.checked = netGranted;
      });
      const netLabel = el(
        "label",
        { class: "skill-net-grant" },
        netChk,
        el("span", { text: " " + t("skills.grant_net") })
      );
      runWrap.appendChild(netLabel);
      const out = el("pre", { class: "skill-run-out" });
      for (const sc of scripts) {
        const btn = el("button", {
          class: "mgmt-inline-link",
          text: "▶ " + sc,
          onclick: async () => {
            out.textContent = t("skills.running");
            const res = await _postJSON("/api/skill/run", { name: s.name, script: sc, args: [] });
            const d = res.data || {};
            if (d.ok || typeof d.exit_code === "number") {
              out.textContent = "exit=" + d.exit_code + "\n" + (d.stdout || "") + (d.stderr ? "\n[stderr]\n" + d.stderr : "");
              if (d.promoted) out.textContent = t("skills.promoted") + "\n" + out.textContent;
            } else {
              out.textContent = t("mgmt.failed", { err: d.reason || res.status });
            }
          }
        });
        runWrap.appendChild(el("div", { class: "mgmt-row" }, btn));
      }
      runWrap.appendChild(out);
      b.appendChild(runWrap);
    }
    const _render = window.KarvyRender;
    if (_render) _render.appendMarkdown(b, s.body || s.description || "(空)");
    else b.appendChild(el("pre", { text: s.body || s.description || "" }));
    b.appendChild(el("button", { class: "mgmt-submit", text: t("skills.back"), onclick: () => open() }));
  }
  async function open() {
    openMgmtModal(t("skills.title"));
    await renderSkillsPanel();
  }
  const KarvySkillsPanel = { open };
  window.KarvySkillsPanel = KarvySkillsPanel;
  exports.KarvySkillsPanel = KarvySkillsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
