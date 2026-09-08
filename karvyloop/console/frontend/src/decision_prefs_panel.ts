/* decision_prefs_panel.ts — 🗳 决策偏好面(§11 决策接口结晶的 UI;从 app.js 抽出)。
 * 楔子那根"独苗"的可见+可控面:列出系统学到的你的决策偏好(约束/品味/站位)+ 复利信号(教会几条/接受率趋势)
 * + 确认 / 编辑 / **撤回**(产品主张"易撤回·不固化你"的第一类动作,留可审计回执)。
 * 自洽,只用 dom/modal/i18n。暴露 window.KarvyDecisionPrefs.open()。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal { openMgmtModal: (title: string) => void; mgmtBody: () => HTMLElement | null }
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

const _DPREF_LABEL: Record<string, string> = {
  constraint: "dpref.kind_constraint", taste: "dpref.kind_taste", standing: "dpref.kind_standing",
};

function _dprefSignalText(s: any): string {
  // 复利信号:教会几条 + 提案接受率趋势(样本足才报趋势,不杜撰)
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

// 口味命中率:"越用越像你"的可证明刻度 —— 系统在你拍板前押注"我猜你会怎么拍",拍完对账。
// 诚实:样本不足不报百分比("还在学你");趋势要两期都够样本才亮。
function _tasteHitText(s: any): string {
  if (!s || !s.taste_enough || typeof s.taste_hit_rate !== "number") {
    const need = (s && s.taste_need_more) || 0;
    return (s && (s.taste_n || 0) > 0) || need > 0
      ? t("dpref.taste_warming", { need: need })
      : "";
  }
  let txt = t("dpref.taste_rate", { pct: Math.round(s.taste_hit_rate * 100), n: s.taste_n });
  if (typeof s.taste_prev_rate === "number") {
    txt += " · " + t("dpref.taste_prev", { pct: Math.round(s.taste_prev_rate * 100) });
  }
  return txt;
}

// 证据(Q3 决策偏好证据可见):这条偏好从你哪几次拍板学来 —— 楔子的可核面,不是凭空的标准。
// API 每条 evidence = {ts, decision, gist}(最近 5 条,新的在前;旧数据只有 ts,decision/gist 空)。
const _EV_DECISION_KEY: Record<string, string> = {
  ACCEPT: "dpref.ev_accept", REJECT: "dpref.ev_reject", DEFER: "dpref.ev_defer",
  EDIT: "dpref.ev_edit", STATE: "dpref.ev_state",
};

function _evWhen(ts: number): string {
  if (!ts || !isFinite(ts)) return "";
  const d = new Date(ts * 1000);
  return (d.getMonth() + 1) + "/" + d.getDate();   // 「6/28」这样的人话日期
}

function _evidenceLine(ev: any): string {
  const when = _evWhen(Number(ev && ev.ts) || 0);
  const dec = (ev && ev.decision) || "";
  const what = dec
    ? t(_EV_DECISION_KEY[dec] || "dpref.ev_decided", { d: dec })
    : t("dpref.ev_no_detail");   // 旧数据只存了时间戳 → 诚实说没存明细,不编
  const gist = (ev && ev.gist) || "";
  return (when ? when + " · " : "") + what + (gist ? " — " + gist : "");
}

function _evidencePanel(p: any): HTMLElement {
  const panel = el("div", { class: "dpref-evidence" });
  const items = (p && p.evidence) || [];
  if (!items.length) {
    // 没有证据(学到它时还没开始存回执)→ 诚实文案,不摆空列表
    panel.appendChild(el("div", { class: "mc-meta dpref-ev-empty", text: t("dpref.ev_empty") }));
    return panel;
  }
  for (const ev of items) {
    panel.appendChild(el("div", { class: "mc-meta dpref-ev-line", text: _evidenceLine(ev) }));
  }
  return panel;
}

function _questionField(q: any): HTMLElement {
  let input: HTMLElement;
  if (q.type === "choice") {
    input = el("select", { "data-question-id": q.id });
    input.appendChild(el("option", { value: "", text: t("ddelegate.choose") }));
    for (const option of q.options || []) input.appendChild(el("option", { value: option, text: option }));
  } else {
    input = el("textarea", { "data-question-id": q.id, rows: "2", placeholder: q.placeholder || "" });
  }
  return el("label", { class: "ddelegate-question" }, el("span", { text: q.text }), input);
}

function renderQuestionnaire(container: HTMLElement, questionnaire: any, onComplete?: (contract: any) => void): HTMLElement {
  const card = el("div", { class: "ddelegate-wizard" },
    el("div", { class: "ddelegate-heading", text: t("ddelegate.questionnaire") }),
    el("div", { class: "mc-meta", text: questionnaire.goal || "" }));
  for (const q of questionnaire.questions || []) card.appendChild(_questionField(q));
  const feedback = el("div", { class: "ddelegate-feedback" });
  const submit = el("button", { class: "mgmt-add-btn", text: t("ddelegate.compile") });
  submit.addEventListener("click", async () => {
    const answers: Record<string, string> = {};
    card.querySelectorAll<HTMLElement>("[data-question-id]").forEach(node => {
      answers[node.dataset.questionId || ""] = (node as HTMLInputElement).value || "";
    });
    (submit as HTMLButtonElement).disabled = true;
    feedback.textContent = t("ddelegate.compiling");
    const result = await _postJSON("/api/decision_delegations/compile", {
      goal: questionnaire.goal, domain: questionnaire.domain || "", answers,
    });
    (submit as HTMLButtonElement).disabled = false;
    if (!result.ok) { feedback.textContent = (result.data && result.data.detail) || t("ddelegate.failed"); return; }
    const contract = result.data.contract;
    feedback.innerHTML = "";
    feedback.appendChild(el("pre", { class: "ddelegate-summary", text: result.data.summary || "" }));
    if (contract.authority && contract.authority.mode !== "recommend") {
      feedback.appendChild(el("button", { class: "mgmt-add-btn", text: t("ddelegate.activate"),
        onclick: async (event: Event) => {
          const button = event.currentTarget as HTMLButtonElement;
          button.disabled = true;
          const activated = await _postJSON("/api/decision_delegations/" + encodeURIComponent(contract.id) + "/activate", { authorization_confirmed: true });
          if (activated.ok) {
            button.textContent = t("ddelegate.active");
            if (onComplete) onComplete(activated.data.contract);
          } else {
            button.disabled = false;
            feedback.appendChild(el("div", { class: "ddelegate-error", text: (activated.data && activated.data.detail) || t("ddelegate.failed") }));
          }
        } }));
    } else {
      feedback.appendChild(el("div", { class: "mc-meta", text: t("ddelegate.recommend_saved") }));
      if (onComplete) onComplete(contract);
    }
  });
  card.appendChild(submit); card.appendChild(feedback); container.appendChild(card);
  return card;
}

function _decisionTester(contract: any): HTMLElement {
  const box = el("div", { class: "ddelegate-test hidden" });
  const scenario = el("textarea", { rows: "2", placeholder: t("ddelegate.scenario_placeholder") }) as HTMLTextAreaElement;
  const options = el("textarea", { rows: "3", placeholder: t("ddelegate.options_placeholder") }) as HTMLTextAreaElement;
  const output = el("div", { class: "ddelegate-feedback" });
  const run = el("button", { class: "mgmt-add-btn", text: t("ddelegate.decide") }) as HTMLButtonElement;
  run.addEventListener("click", async () => {
    const labels = options.value.split(/\n+/).map(value => value.trim()).filter(Boolean);
    if (!scenario.value.trim() || labels.length < 2) { output.textContent = t("ddelegate.need_scenario"); return; }
    run.disabled = true; output.textContent = t("ddelegate.deciding");
    const result = await _postJSON("/api/decision_delegations/decide", {
      contract_id: contract.id, scenario: scenario.value.trim(),
      options: labels.map((label, index) => ({ id: "option-" + (index + 1), label })),
    });
    run.disabled = false;
    if (!result.ok) { output.textContent = (result.data && result.data.detail) || t("ddelegate.failed"); return; }
    const data = result.data;
    const picked = (data.selected_option && data.selected_option.label) || t("ddelegate.no_pick");
    output.textContent = (data.status === "decided" ? t("ddelegate.decided") : t("ddelegate.escalated")) + "：" + picked +
      "\n" + ((data.assessment && data.assessment.reason) || "") +
      (data.gate_reasons && data.gate_reasons.length ? "\n" + data.gate_reasons.join("；") : "");
  });
  box.appendChild(scenario); box.appendChild(options); box.appendChild(run); box.appendChild(output);
  return box;
}

async function _renderDelegations(body: HTMLElement): Promise<void> {
  const head = el("div", { class: "ddelegate-toolbar" },
    el("div", { class: "mgmt-section-title", text: t("ddelegate.title") }),
    el("button", { class: "mgmt-add-btn", text: t("ddelegate.new"), onclick: async () => {
      const goal = window.prompt(t("ddelegate.goal_prompt"), "");
      if (!goal || !goal.trim()) return;
      const result = await _postJSON("/api/decision_delegations/questionnaire", { goal: goal.trim() });
      if (result.ok) renderQuestionnaire(body, result.data.questionnaire, () => { void renderDecisionPrefs(); });
    } }));
  body.appendChild(head);
  const data = await _getJSON("/api/decision_delegations");
  for (const contract of (data && data.contracts) || []) {
    const tester = _decisionTester(contract);
    const actions = el("div", { class: "dpref-actions" });
    if (contract.status === "active") {
      actions.appendChild(el("button", { class: "dpref-edit", text: t("ddelegate.test"), onclick: () => tester.classList.toggle("hidden") }));
      actions.appendChild(el("button", { class: "mc-del", text: t("ddelegate.revoke"), onclick: async () => {
        await _postJSON("/api/decision_delegations/" + encodeURIComponent(contract.id) + "/revoke", {});
        await renderDecisionPrefs();
      } }));
    }
    body.appendChild(el("div", { class: "mgmt-card ddelegate-contract" },
      el("div", { class: "mc-main" }, el("div", { class: "mc-name", text: contract.goal }),
        el("div", { class: "mc-meta", text: t("ddelegate.status_" + contract.status) }), tester), actions));
  }
}

async function renderDecisionPrefs(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const stats = await _getJSON("/api/decision_prefs/stats");
  if (stats) body.appendChild(el("div", { class: "dpref-signal", text: _dprefSignalText(stats) }));
  if (stats) {
    const tasteTxt = _tasteHitText(stats);
    if (tasteTxt) body.appendChild(el("div", { class: "dpref-signal dpref-taste", text: "🎯 " + tasteTxt }));
  }
  await _renderDelegations(body);
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("dpref.subtitle") }));
  const data = await _getJSON("/api/decision_prefs");
  const prefs = (data && data.prefs) || [];
  if (!prefs.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("dpref.empty") })); return; }
  const list = el("div", { class: "mgmt-list" });
  for (const p of prefs) {
    const kindLbl = t(_DPREF_LABEL[p.kind] || "dpref.kind_taste");
    const statusBadge = el("span", {
      class: "dpref-badge " + (p.status === "confirmed" ? "confirmed" : "provisional"),
      text: p.status === "confirmed" ? t("dpref.confirmed") : t("dpref.provisional") });
    const actions = el("div", { class: "dpref-actions" });
    if (p.status !== "confirmed") {
      actions.appendChild(el("button", { class: "dpref-confirm", text: t("dpref.confirm"),
        onclick: async () => { await _postJSON("/api/decision_prefs/op", { op: "confirm", content: p.content }); await renderDecisionPrefs(); } }));
    }
    actions.appendChild(el("button", { class: "dpref-edit", text: t("dpref.edit"),
      onclick: async () => {
        const nc = window.prompt(t("dpref.edit_prompt"), p.content);
        if (nc && nc.trim() && nc.trim() !== p.content) {
          await _postJSON("/api/decision_prefs/op", { op: "edit", content: p.content, new_content: nc.trim() });
          await renderDecisionPrefs();
        }
      } }));
    // 撤回(revoke):产品主张"易撤回/不固化你"的第一类动作面 —— 主动收回它学到的偏好,
    // 留可审计回执(进 🗳 决策流水),confirmed 的也能由你撤。区别于静默 delete。
    actions.appendChild(el("button", { class: "mc-del", text: t("dpref.revoke"), title: t("dpref.revoke_hint"),
      onclick: async () => {
        if (!window.confirm(t("dpref.confirm_revoke", { c: p.content }))) return;
        await _postJSON("/api/decision_prefs/op", { op: "revoke", content: p.content });
        await renderDecisionPrefs();
      } }));
    // 证据展开(Q3):默认收起;点开看"这条从你哪几次拍板学来"(数据已随 /api/decision_prefs 到手,零额外请求)
    const evPanel = _evidencePanel(p);
    evPanel.classList.add("hidden");
    const evToggle = el("button", { class: "mgmt-inline-link dpref-ev-toggle",
      text: t("dpref.ev_btn", { n: p.evidence_n || 0 }),
      onclick: () => { evPanel.classList.toggle("hidden"); } });
    list.appendChild(el("div", { class: "mgmt-card dpref-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { class: "dpref-kind", text: kindLbl }), " ", statusBadge),
        el("div", { class: "mc-meta dpref-content", text: p.content }),
        el("div", { class: "mc-meta dpref-strength", text: t("dpref.strength", { pct: Math.round((p.strength || 0) * 100) }) }),
        evToggle, evPanel),
      actions));
  }
  body.appendChild(list);
}

async function open(): Promise<void> {
  openMgmtModal(t("dpref.title")); await renderDecisionPrefs();
}

const KarvyDecisionPrefs = { open, renderQuestionnaire };
(window as unknown as { KarvyDecisionPrefs: typeof KarvyDecisionPrefs }).KarvyDecisionPrefs = KarvyDecisionPrefs;
export { KarvyDecisionPrefs };
