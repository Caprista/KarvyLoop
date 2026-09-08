var KarvyDecisionPrefsBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const _DPREF_LABEL = {
    constraint: "dpref.kind_constraint",
    taste: "dpref.kind_taste",
    standing: "dpref.kind_standing"
  };
  function _dprefSignalText(s) {
    let txt = t("dpref.sig_learned", { n: s.prefs_total || 0, c: s.confirmed || 0 });
    if (s.enough_for_trend && typeof s.accept_rate === "number") {
      txt += " · " + t("dpref.sig_accept", { pct: Math.round(s.accept_rate * 100) });
      if (typeof s.trend === "number" && Math.abs(s.trend) >= 0.03) {
        txt += s.trend > 0 ? " " + t("dpref.sig_up") : " " + t("dpref.sig_down");
      }
    } else if ((s.decisions_total || 0) > 0) {
      txt += " · " + t("dpref.sig_warming", { n: s.decisions_total });
    }
    return txt;
  }
  function _tasteHitText(s) {
    if (!s || !s.taste_enough || typeof s.taste_hit_rate !== "number") {
      const need = s && s.taste_need_more || 0;
      return s && (s.taste_n || 0) > 0 || need > 0 ? t("dpref.taste_warming", { need }) : "";
    }
    let txt = t("dpref.taste_rate", { pct: Math.round(s.taste_hit_rate * 100), n: s.taste_n });
    if (typeof s.taste_prev_rate === "number") {
      txt += " · " + t("dpref.taste_prev", { pct: Math.round(s.taste_prev_rate * 100) });
    }
    return txt;
  }
  const _EV_DECISION_KEY = {
    ACCEPT: "dpref.ev_accept",
    REJECT: "dpref.ev_reject",
    DEFER: "dpref.ev_defer",
    EDIT: "dpref.ev_edit",
    STATE: "dpref.ev_state"
  };
  function _evWhen(ts) {
    if (!ts || !isFinite(ts)) return "";
    const d = new Date(ts * 1e3);
    return d.getMonth() + 1 + "/" + d.getDate();
  }
  function _evidenceLine(ev) {
    const when = _evWhen(Number(ev && ev.ts) || 0);
    const dec = ev && ev.decision || "";
    const what = dec ? t(_EV_DECISION_KEY[dec] || "dpref.ev_decided", { d: dec }) : t("dpref.ev_no_detail");
    const gist = ev && ev.gist || "";
    return (when ? when + " · " : "") + what + (gist ? " — " + gist : "");
  }
  function _evidencePanel(p) {
    const panel = el("div", { class: "dpref-evidence" });
    const items = p && p.evidence || [];
    if (!items.length) {
      panel.appendChild(el("div", { class: "mc-meta dpref-ev-empty", text: t("dpref.ev_empty") }));
      return panel;
    }
    for (const ev of items) {
      panel.appendChild(el("div", { class: "mc-meta dpref-ev-line", text: _evidenceLine(ev) }));
    }
    return panel;
  }
  function _questionField(q) {
    let input;
    if (q.type === "choice") {
      input = el("select", { "data-question-id": q.id });
      input.appendChild(el("option", { value: "", text: t("ddelegate.choose") }));
      for (const option of q.options || []) input.appendChild(el("option", { value: option, text: option }));
    } else {
      input = el("textarea", { "data-question-id": q.id, rows: "2", placeholder: q.placeholder || "" });
    }
    return el("label", { class: "ddelegate-question" }, el("span", { text: q.text }), input);
  }
  function renderQuestionnaire(container, questionnaire, onComplete) {
    const card = el(
      "div",
      { class: "ddelegate-wizard" },
      el("div", { class: "ddelegate-heading", text: t("ddelegate.questionnaire") }),
      el("div", { class: "mc-meta", text: questionnaire.goal || "" })
    );
    for (const q of questionnaire.questions || []) card.appendChild(_questionField(q));
    const feedback = el("div", { class: "ddelegate-feedback" });
    const submit = el("button", { class: "mgmt-add-btn", text: t("ddelegate.compile") });
    submit.addEventListener("click", async () => {
      const answers = {};
      card.querySelectorAll("[data-question-id]").forEach((node) => {
        answers[node.dataset.questionId || ""] = node.value || "";
      });
      submit.disabled = true;
      feedback.textContent = t("ddelegate.compiling");
      const result = await _postJSON("/api/decision_delegations/compile", {
        goal: questionnaire.goal,
        domain: questionnaire.domain || "",
        answers
      });
      submit.disabled = false;
      if (!result.ok) {
        feedback.textContent = result.data && result.data.detail || t("ddelegate.failed");
        return;
      }
      const contract = result.data.contract;
      feedback.innerHTML = "";
      feedback.appendChild(el("pre", { class: "ddelegate-summary", text: result.data.summary || "" }));
      if (contract.authority && contract.authority.mode !== "recommend") {
        feedback.appendChild(el("button", {
          class: "mgmt-add-btn",
          text: t("ddelegate.activate"),
          onclick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            const activated = await _postJSON("/api/decision_delegations/" + encodeURIComponent(contract.id) + "/activate", { authorization_confirmed: true });
            if (activated.ok) {
              button.textContent = t("ddelegate.active");
              if (onComplete) onComplete(activated.data.contract);
            } else {
              button.disabled = false;
              feedback.appendChild(el("div", { class: "ddelegate-error", text: activated.data && activated.data.detail || t("ddelegate.failed") }));
            }
          }
        }));
      } else {
        feedback.appendChild(el("div", { class: "mc-meta", text: t("ddelegate.recommend_saved") }));
        if (onComplete) onComplete(contract);
      }
    });
    card.appendChild(submit);
    card.appendChild(feedback);
    container.appendChild(card);
    return card;
  }
  function _decisionTester(contract) {
    const box = el("div", { class: "ddelegate-test hidden" });
    const scenario = el("textarea", { rows: "2", placeholder: t("ddelegate.scenario_placeholder") });
    const options = el("textarea", { rows: "3", placeholder: t("ddelegate.options_placeholder") });
    const output = el("div", { class: "ddelegate-feedback" });
    const run = el("button", { class: "mgmt-add-btn", text: t("ddelegate.decide") });
    run.addEventListener("click", async () => {
      const labels = options.value.split(/\n+/).map((value) => value.trim()).filter(Boolean);
      if (!scenario.value.trim() || labels.length < 2) {
        output.textContent = t("ddelegate.need_scenario");
        return;
      }
      run.disabled = true;
      output.textContent = t("ddelegate.deciding");
      const result = await _postJSON("/api/decision_delegations/decide", {
        contract_id: contract.id,
        scenario: scenario.value.trim(),
        options: labels.map((label, index) => ({ id: "option-" + (index + 1), label }))
      });
      run.disabled = false;
      if (!result.ok) {
        output.textContent = result.data && result.data.detail || t("ddelegate.failed");
        return;
      }
      const data = result.data;
      const picked = data.selected_option && data.selected_option.label || t("ddelegate.no_pick");
      output.textContent = (data.status === "decided" ? t("ddelegate.decided") : t("ddelegate.escalated")) + "：" + picked + "\n" + (data.assessment && data.assessment.reason || "") + (data.gate_reasons && data.gate_reasons.length ? "\n" + data.gate_reasons.join("；") : "");
    });
    box.appendChild(scenario);
    box.appendChild(options);
    box.appendChild(run);
    box.appendChild(output);
    return box;
  }
  async function _renderDelegations(body) {
    const head = el(
      "div",
      { class: "ddelegate-toolbar" },
      el("div", { class: "mgmt-section-title", text: t("ddelegate.title") }),
      el("button", { class: "mgmt-add-btn", text: t("ddelegate.new"), onclick: async () => {
        const goal = window.prompt(t("ddelegate.goal_prompt"), "");
        if (!goal || !goal.trim()) return;
        const result = await _postJSON("/api/decision_delegations/questionnaire", { goal: goal.trim() });
        if (result.ok) renderQuestionnaire(body, result.data.questionnaire, () => {
          void renderDecisionPrefs();
        });
      } })
    );
    body.appendChild(head);
    const data = await _getJSON("/api/decision_delegations");
    for (const contract of data && data.contracts || []) {
      const tester = _decisionTester(contract);
      const actions = el("div", { class: "dpref-actions" });
      if (contract.status === "active") {
        actions.appendChild(el("button", { class: "dpref-edit", text: t("ddelegate.test"), onclick: () => tester.classList.toggle("hidden") }));
        actions.appendChild(el("button", { class: "mc-del", text: t("ddelegate.revoke"), onclick: async () => {
          await _postJSON("/api/decision_delegations/" + encodeURIComponent(contract.id) + "/revoke", {});
          await renderDecisionPrefs();
        } }));
      }
      body.appendChild(el(
        "div",
        { class: "mgmt-card ddelegate-contract" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name", text: contract.goal }),
          el("div", { class: "mc-meta", text: t("ddelegate.status_" + contract.status) }),
          tester
        ),
        actions
      ));
    }
  }
  async function renderDecisionPrefs() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const stats = await _getJSON("/api/decision_prefs/stats");
    if (stats) body.appendChild(el("div", { class: "dpref-signal", text: _dprefSignalText(stats) }));
    if (stats) {
      const tasteTxt = _tasteHitText(stats);
      if (tasteTxt) body.appendChild(el("div", { class: "dpref-signal dpref-taste", text: "🎯 " + tasteTxt }));
    }
    await _renderDelegations(body);
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("dpref.subtitle") }));
    const data = await _getJSON("/api/decision_prefs");
    const prefs = data && data.prefs || [];
    if (!prefs.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("dpref.empty") }));
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    for (const p of prefs) {
      const kindLbl = t(_DPREF_LABEL[p.kind] || "dpref.kind_taste");
      const statusBadge = el("span", {
        class: "dpref-badge " + (p.status === "confirmed" ? "confirmed" : "provisional"),
        text: p.status === "confirmed" ? t("dpref.confirmed") : t("dpref.provisional")
      });
      const actions = el("div", { class: "dpref-actions" });
      if (p.status !== "confirmed") {
        actions.appendChild(el("button", {
          class: "dpref-confirm",
          text: t("dpref.confirm"),
          onclick: async () => {
            await _postJSON("/api/decision_prefs/op", { op: "confirm", content: p.content });
            await renderDecisionPrefs();
          }
        }));
      }
      actions.appendChild(el("button", {
        class: "dpref-edit",
        text: t("dpref.edit"),
        onclick: async () => {
          const nc = window.prompt(t("dpref.edit_prompt"), p.content);
          if (nc && nc.trim() && nc.trim() !== p.content) {
            await _postJSON("/api/decision_prefs/op", { op: "edit", content: p.content, new_content: nc.trim() });
            await renderDecisionPrefs();
          }
        }
      }));
      actions.appendChild(el("button", {
        class: "mc-del",
        text: t("dpref.revoke"),
        title: t("dpref.revoke_hint"),
        onclick: async () => {
          if (!window.confirm(t("dpref.confirm_revoke", { c: p.content }))) return;
          await _postJSON("/api/decision_prefs/op", { op: "revoke", content: p.content });
          await renderDecisionPrefs();
        }
      }));
      const evPanel = _evidencePanel(p);
      evPanel.classList.add("hidden");
      const evToggle = el("button", {
        class: "mgmt-inline-link dpref-ev-toggle",
        text: t("dpref.ev_btn", { n: p.evidence_n || 0 }),
        onclick: () => {
          evPanel.classList.toggle("hidden");
        }
      });
      list.appendChild(el(
        "div",
        { class: "mgmt-card dpref-card" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { class: "dpref-kind", text: kindLbl }), " ", statusBadge),
          el("div", { class: "mc-meta dpref-content", text: p.content }),
          el("div", { class: "mc-meta dpref-strength", text: t("dpref.strength", { pct: Math.round((p.strength || 0) * 100) }) }),
          evToggle,
          evPanel
        ),
        actions
      ));
    }
    body.appendChild(list);
  }
  async function open() {
    openMgmtModal(t("dpref.title"));
    await renderDecisionPrefs();
  }
  const KarvyDecisionPrefs = { open, renderQuestionnaire };
  window.KarvyDecisionPrefs = KarvyDecisionPrefs;
  exports.KarvyDecisionPrefs = KarvyDecisionPrefs;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
