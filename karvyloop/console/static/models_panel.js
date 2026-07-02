var KarvyModelsPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _deps = { pollSnapshot: () => {
  } };
  let _modelApis = ["anthropic-messages", "openai-completions", "openai-responses", "google-generative-ai", "ollama", "bedrock-converse"];
  async function renderModelsPanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("models.subtitle") }));
    const data = await _getJSON("/api/model/config");
    if (data && data.no_llm) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("models.no_llm") }));
      return;
    }
    if (data && data.valid_apis && data.valid_apis.length) _modelApis = data.valid_apis;
    const models = data && data.models || [];
    if (!models.length) body.appendChild(el("div", { class: "mgmt-empty", text: t("models.empty") }));
    else {
      const list = el("div", { class: "mgmt-list" });
      for (const m of models) {
        const badges = [];
        if (m.is_default_chat) badges.push(el("span", { class: "dpref-badge confirmed", text: t("models.default_chat") }));
        if (m.is_default_embedding) badges.push(el("span", { class: "dpref-badge confirmed", text: t("models.default_embed") }));
        const meta = m.provider + " · " + m.api + " · " + t("models.ctx", { n: m.context_window || "?" }) + " · " + (m.has_key ? "🔑 " + m.api_key_masked : t("models.no_key"));
        const actions = el(
          "div",
          { class: "dpref-actions" },
          el("button", { class: "dpref-edit", text: t("models.edit"), onclick: () => _openModelEdit(m) }),
          el("button", {
            class: "dpref-confirm",
            text: t("models.set_chat"),
            onclick: async () => {
              await _postJSON("/api/model/set_default", { role: "chat", model_id: m.id });
              await renderModelsPanel();
            }
          }),
          el("button", {
            class: "mc-del",
            text: t("mgmt.delete"),
            onclick: async () => {
              if (!window.confirm(t("models.confirm_del", { name: m.id }))) return;
              const r = await _postJSON("/api/model/delete", { model_id: m.id });
              if (!(r.ok && r.data && r.data.ok)) alert(r.data && r.data.reason || "fail");
              await renderModelsPanel();
            }
          })
        );
        list.appendChild(el(
          "div",
          { class: "mgmt-card" },
          el(
            "div",
            { class: "mc-main" },
            el("div", { class: "mc-name" }, el("span", { text: "🤖 " + m.id }), " ", ...badges),
            el("div", { class: "mc-meta", text: meta })
          ),
          actions
        ));
      }
      body.appendChild(list);
    }
    body.appendChild(_modelForm({}, t("models.add_title")));
    await _renderSearchConfig(body);
  }
  async function _renderSearchConfig(body) {
    const data = await _getJSON("/api/search/config");
    if (!data) return;
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("search.title") }));
    const wrap = el("div", { class: "mgmt-form" });
    const cur = data.mode === "keyed" ? t("search.cur_keyed", { provider: data.provider }) : t("search.cur_keyless");
    wrap.appendChild(el("div", { class: "search-cur", text: cur }));
    const provSel = el(
      "select",
      null,
      el("option", { value: "", text: t("search.keyless_opt"), selected: data.mode !== "keyed" }),
      ...(data.providers || ["brave", "tavily"]).map((p) => el("option", { value: p, text: p, selected: data.provider === p }))
    );
    const keyIn = el("input", { type: "password", placeholder: t("search.key_ph") });
    const msg = _formMsg();
    const save = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.save"),
      onclick: async () => {
        const r = await _postJSON(
          "/api/search/config",
          { provider: provSel.value, api_key: keyIn.value }
        );
        if (r.ok && r.data && r.data.ok) {
          _setMsg(msg, true, provSel.value ? t("search.saved_keyed", { provider: provSel.value }) : t("search.saved_keyless"));
          keyIn.value = "";
          await renderModelsPanel();
        } else _setMsg(msg, false, t("mgmt.failed", { err: r.data && r.data.reason || r.status }));
      }
    });
    wrap.appendChild(el("label", { class: "mgmt-label", text: t("search.provider_label") }));
    wrap.appendChild(provSel);
    wrap.appendChild(el("label", { class: "mgmt-label", text: t("search.key_label") }));
    wrap.appendChild(keyIn);
    wrap.appendChild(el("div", { class: "search-hint", text: t("search.hint") }));
    wrap.appendChild(save);
    wrap.appendChild(msg);
    body.appendChild(wrap);
  }
  function _modelForm(m, title, onSaved) {
    const f = (k, ph) => {
      const i = el("input", { type: "text", placeholder: ph || "" });
      if (m[k] != null) i.value = m[k];
      return i;
    };
    const idIn = f("id", "provider/model-id"), nameIn = f("name", "");
    const provIn = f("provider", "anthropic"), baseIn = f("base_url", "https://...");
    const keyIn = el("input", { type: "password", placeholder: m.has_key ? m.api_key_masked + " (" + t("models.key_keep") + ")" : "sk-... 或 ${ENV_VAR}" });
    const apiSel = el("select", null, ..._modelApis.map((a) => el("option", { value: a, text: a, selected: a === m.api })));
    const roleSel = el("select", null, el("option", { value: "chat", text: "chat", selected: m.role !== "embedding" }), el("option", { value: "embedding", text: "embedding", selected: m.role === "embedding" }));
    const authSel = el("select", null, el("option", { value: "x-api-key", text: "x-api-key", selected: m.auth_header !== "Authorization" }), el("option", { value: "Authorization", text: "Authorization", selected: m.auth_header === "Authorization" }));
    const ctxIn = el("input", { type: "number" });
    ctxIn.value = String(m.context_window || 2e5);
    const maxIn = el("input", { type: "number" });
    maxIn.value = String(m.max_tokens || 8192);
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: t("mgmt.save"),
      onclick: async () => {
        const r = await _postJSON("/api/model/save", {
          provider: provIn.value.trim(),
          model_id: idIn.value.trim(),
          model_name: nameIn.value.trim(),
          api: apiSel.value,
          role: roleSel.value,
          base_url: baseIn.value.trim(),
          api_key: keyIn.value,
          auth_header: authSel.value,
          context_window: Number(ctxIn.value) || 2e5,
          max_tokens: Number(maxIn.value) || 8192
        });
        if (r.ok && r.data && r.data.ok) {
          if (r.data.reloaded === false) _setMsg(msg, true, r.data.reload_note || "saved");
          if (onSaved) await onSaved();
          else await renderModelsPanel();
        } else _setMsg(msg, false, t("mgmt.failed", { err: r.data && (r.data.reason || r.data.detail) || r.status }));
      }
    });
    return el(
      "form",
      { class: "mgmt-form", onsubmit: (e) => e.preventDefault() },
      el("div", { class: "mgmt-section-title", text: title }),
      el("div", { class: "mgmt-hint", text: t("models.key_hint") }),
      el("label", { text: t("models.f_id") }),
      idIn,
      el("label", { text: t("models.f_name") }),
      nameIn,
      el("label", { text: t("models.f_provider") }),
      provIn,
      el("label", { text: t("models.f_base") }),
      baseIn,
      el("label", { text: t("models.f_key") }),
      keyIn,
      el("label", { text: t("models.f_api") }),
      apiSel,
      el("label", { text: t("models.f_role") }),
      roleSel,
      el("label", { text: t("models.f_auth") }),
      authSel,
      el("label", { text: t("models.f_ctx") }),
      ctxIn,
      el("label", { text: t("models.f_max") }),
      maxIn,
      submit,
      msg
    );
  }
  function _openModelEdit(m) {
    openMgmtModal(m.id);
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(_modelForm(m, t("models.edit_title")));
    b.appendChild(el("button", { class: "mgmt-inline-link", text: t("models.back"), onclick: () => open() }));
  }
  async function _guidedSetup(container, onDone) {
    const resp = await _getJSON("/api/providers/presets");
    const presets = resp && resp.presets || [];
    _onbPicker(container, presets, onDone);
  }
  function _onbPicker(wrap, presets, onDone) {
    wrap.innerHTML = "";
    wrap.appendChild(el("div", { class: "mgmt-hint", text: t("onb.pick_provider") }));
    const ollamaSlot = el("div", { class: "onb-ollama-slot" });
    wrap.appendChild(ollamaSlot);
    _getJSON("/api/providers/detect_local").then((d) => {
      if (!d || !d.found || !d.models || !d.models.length) return;
      const model = d.models[0];
      ollamaSlot.appendChild(el("button", {
        class: "mgmt-submit onb-ollama",
        text: "🦙 " + t("onb.ollama_found", { n: d.models.length }),
        onClick: () => {
          const msg = _formMsg();
          ollamaSlot.appendChild(msg);
          _onbSave({
            id: "ollama",
            model_id: "ollama/" + model,
            model_name: model,
            api: "openai-completions",
            base_url: "http://127.0.0.1:11434/v1",
            auth_header: "Authorization",
            messages_path: "",
            context_window: 32768,
            max_tokens: 4096
          }, "ollama", msg, onDone);
        }
      }));
    }).catch(() => {
    });
    const picker = el("div", { class: "onb-picker" });
    presets.forEach((p) => picker.appendChild(el("button", {
      class: "onb-prov" + (p.is_local ? " onb-prov-local" : ""),
      text: p.name,
      onClick: () => _onbProvider(wrap, presets, p, onDone)
    })));
    wrap.appendChild(picker);
    wrap.appendChild(el("button", {
      class: "mgmt-inline-link",
      text: t("onb.advanced"),
      onClick: () => {
        wrap.innerHTML = "";
        wrap.appendChild(_modelForm({}, t("setup.add_model"), onDone));
      }
    }));
  }
  function _onbProvider(wrap, presets, p, onDone) {
    wrap.innerHTML = "";
    wrap.appendChild(el("button", {
      class: "mgmt-inline-link",
      text: t("onb.back"),
      onClick: () => _onbPicker(wrap, presets, onDone)
    }));
    wrap.appendChild(el("div", { class: "onb-prov-title", text: p.name }));
    const msg = _formMsg();
    if (p.is_local) {
      wrap.appendChild(el("div", { class: "mgmt-hint", text: t("onb.local_hint", { hint: p.install_hint || "" }) }));
      wrap.appendChild(el("button", {
        class: "mgmt-submit",
        text: t("onb.use_local"),
        onClick: () => _onbSave(p, "", msg, onDone)
      }));
      wrap.appendChild(msg);
      return;
    }
    if (p.get_key_url) {
      wrap.appendChild(el("a", {
        class: "onb-getkey",
        href: p.get_key_url,
        target: "_blank",
        rel: "noopener",
        text: t("onb.get_key", { provider: p.name })
      }));
    }
    wrap.appendChild(el("label", { text: t("onb.paste_key", { env: p.key_env || "API key" }) }));
    const keyIn = el("input", { type: "password", placeholder: "sk-..." });
    wrap.appendChild(keyIn);
    wrap.appendChild(el("button", {
      class: "mgmt-submit",
      text: t("onb.save_validate"),
      onClick: () => _onbSave(p, keyIn.value, msg, onDone)
    }));
    wrap.appendChild(msg);
  }
  async function _onbSave(p, key, msg, onDone) {
    _setMsg(msg, true, t("onb.saving"));
    const r = await _postJSON("/api/model/save", {
      provider: p.id,
      model_id: p.model_id,
      model_name: p.model_name || "",
      api: p.api,
      role: "chat",
      base_url: p.base_url,
      api_key: key,
      auth_header: p.auth_header,
      messages_path: p.messages_path || "",
      context_window: p.context_window || 2e5,
      max_tokens: p.max_tokens || 8192
    });
    if (!(r.ok && r.data && r.data.ok)) {
      _setMsg(msg, false, t("mgmt.failed", { err: r.data && (r.data.reason || r.data.detail) || r.status }));
      return;
    }
    await _postJSON("/api/model/set_default", { model_id: p.model_id, role: "chat" });
    _setMsg(msg, true, t("onb.validating"));
    const v = await _postJSON("/api/model/validate", {});
    if (v.ok && v.data && v.data.ok) {
      _setMsg(msg, true, t("onb.ok"));
    } else {
      const cls = v.data && v.data.error_class || "";
      const hintKey = cls === "bad_key" ? "onb.err_bad_key" : cls === "bad_url" ? "onb.err_bad_url" : cls === "unreachable" ? "onb.err_unreachable" : "";
      const hint = hintKey ? t(hintKey) + " — " : "";
      _setMsg(msg, false, hint + t("onb.validate_failed", { err: v.data && v.data.reason || "?" }));
    }
    if (onDone) await onDone();
  }
  async function checkSetupGate(deps) {
    if (deps) _deps = deps;
    const s = await _getJSON("/api/setup_status");
    if (s && s.must_setup) openForcedSetup();
  }
  function openForcedSetup() {
    _KM.setSetupLocked(true);
    openMgmtModal(t("setup.title"));
    const closeBtn = document.getElementById("mgmt-close");
    if (closeBtn) closeBtn.style.display = "none";
    const b = mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(el("div", { class: "mgmt-hint", text: t("setup.hint") }));
    const guided = el("div");
    const done = async () => {
      const s = await _getJSON("/api/setup_status");
      if (s && !s.must_setup) {
        _KM.setSetupLocked(false);
        if (closeBtn) closeBtn.style.display = "";
        const modalEl = document.getElementById("mgmt-modal");
        if (modalEl) modalEl.classList.add("hidden");
        _deps.pollSnapshot();
      }
    };
    b.appendChild(guided);
    _guidedSetup(guided, done);
  }
  async function open() {
    openMgmtModal(t("models.title"));
    await renderModelsPanel();
  }
  const KarvyModelsPanel = { open, checkSetupGate };
  window.KarvyModelsPanel = KarvyModelsPanel;
  exports.KarvyModelsPanel = KarvyModelsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
