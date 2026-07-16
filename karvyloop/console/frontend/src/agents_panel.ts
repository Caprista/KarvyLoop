/* agents_panel.ts — 🤖 外部 Agent 导入面板(从 app.js 抽出,大尾巴 slice)。
 * 两种模式(docs/84):
 *   ① 单个 agent:按 KarvyLoop 范式导入(generic-json/claude/codex/agent-bundle)→ 落角色库。
 *   ② 多 agent 系统:bundle(agents[] + topology)→ /import_system/plan 出方案(零写盘)
 *      → 人审(判型可改/模板可看/降级醒目逐条)→ /import_system/apply 确定性落地(H2A:人拍了才落)。
 * 唯一跨面板依赖:导入成功后 refreshPeers() 刷新左栏 → 经 open(deps) 注入。
 * 暴露 window.KarvyAgentsPanel.open(deps)。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  mgmtBody: () => HTMLElement | null;
  formMsg: () => HTMLElement;
  setMsg: (msgEl: HTMLElement, ok: boolean, text: string) => void;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
interface Deps { refreshPeers: () => void }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

let _deps: Deps = { refreshPeers: () => {} };

const KINDS = ["decision", "hybrid", "executor", "skill"] as const;

async function open(deps?: Deps): Promise<void> {
  if (deps) _deps = deps;
  openMgmtModal(t("mgmt.agents_title"));
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  // —— 模式切换:单个 agent / 多 agent 系统 ——
  const singleBox = el("div", null);
  const systemBox = el("div", { style: "display:none" });
  const tabSingle = el("button", { class: "mgmt-submit", text: t("agent.mode_single") });
  const tabSystem = el("button", { class: "mgmt-submit", text: t("agent.mode_system") });
  const setMode = (sys: boolean) => {
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

// ---- 模式①:单个 agent(原面板,原样保留)----
function renderSingle(body: HTMLElement): void {
  body.appendChild(el("div", { class: "mgmt-hint", text: t("agent.import_hint") }));
  const idIn = el("input", { type: "text", placeholder: "imported_pm" }) as HTMLInputElement;
  const srcSel = el("select", null,
    el("option", { value: "generic-json", text: "generic-json" }),
    el("option", { value: "claude", text: "claude" }),
    el("option", { value: "codex", text: "codex" }),
    el("option", { value: "agent-bundle", text: "agent-bundle" })) as HTMLSelectElement;
  const promptIn = el("textarea", {}) as HTMLTextAreaElement;
  const toolsIn = el("input", { type: "text", placeholder: "read_file, run_command" }) as HTMLInputElement;
  const msg = _formMsg();
  const submit = el("button", {
    class: "mgmt-submit", text: t("agent.import_btn"),
    onclick: async () => {
      const tools = toolsIn.value.split(",").map((s) => s.trim()).filter(Boolean);
      const res = await _postJSON("/api/agent/import", {
        role_id: idIn.value.trim(), source_type: srcSel.value,
        system_prompt: promptIn.value, tools,
      });
      if (res.ok) { _setMsg(msg, true, t("agent.imported", { id: res.data.role_id })); _deps.refreshPeers(); }
      else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: t("agent.import_title") }),
    el("label", { text: t("mgmt.name") }), idIn,
    el("label", { text: t("agent.source_type") }), srcSel,
    el("label", { text: t("agent.system_prompt") }), promptIn,
    el("label", { text: t("atom.tools_label") }), toolsIn,
    submit, msg));
}

// ---- 模式②:多 agent 系统(plan 审阅 → 人拍板 → apply)----
function renderSystem(body: HTMLElement): void {
  body.appendChild(el("div", { class: "mgmt-hint", text: t("agent.sys_hint") }));
  const bundleIn = el("textarea", {
    placeholder: '{"name":"…","agents":[{"name":"…","system_prompt":"…"}],"topology":{…}}',
    style: "min-height:120px",
  }) as HTMLTextAreaElement;
  const fileIn = el("input", { type: "file", accept: ".json,application/json" }) as HTMLInputElement;
  fileIn.onchange = () => {
    const f = fileIn.files && fileIn.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => { bundleIn.value = String(rd.result || ""); };
    rd.readAsText(f);
  };
  const domainIn = el("input", { type: "text", placeholder: t("agent.sys_domain_ph") }) as HTMLInputElement;
  const msg = _formMsg();
  const review = el("div", null);
  const planBtn = el("button", {
    class: "mgmt-submit", text: t("agent.sys_plan_btn"),
    onclick: async () => {
      review.innerHTML = "";
      let bundle: unknown;
      try { bundle = JSON.parse(bundleIn.value); }
      catch { _setMsg(msg, false, t("agent.sys_bad_json")); return; }
      _setMsg(msg, true, t("agent.sys_planning"));
      const res = await _postJSON("/api/agent/import_system/plan",
        { bundle, domain_name: domainIn.value.trim() });
      if (!res.ok) {
        _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
        return;
      }
      msg.textContent = "";
      renderReview(review, res.data, msg);
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: t("agent.mode_system") }),
    el("label", { text: t("agent.sys_bundle_label") }), bundleIn, fileIn,
    el("label", { text: t("agent.sys_domain_label") }), domainIn,
    planBtn, msg));
  body.appendChild(review);
}

function _sectionTitle(text: string): HTMLElement {
  return el("div", { class: "mgmt-section-title", text });
}

function renderReview(review: HTMLElement, data: any, msg: HTMLElement): void {
  review.innerHTML = "";
  const degradations: any[] = data.degradations || [];
  // 降级清单永远最醒目(有就顶到最前;人要知道拍的是什么)
  const degradeBox = el("div", { style: "border:1px solid #c77;border-radius:6px;padding:8px 10px;margin:10px 0" });
  degradeBox.appendChild(el("div", { style: "font-weight:600;margin-bottom:4px", text: t("agent.sys_degrade_title") }));
  if (!degradations.length) {
    degradeBox.appendChild(el("div", { class: "mgmt-hint", text: t("agent.sys_degrade_empty") }));
  } else {
    for (const d of degradations) {
      degradeBox.appendChild(el("div", { style: "margin:6px 0" },
        el("div", { style: "font-weight:600", text: `⚠ ${d.element || ""}` }),
        el("div", { text: String(d.why || "") }),
        el("div", { class: "mgmt-hint", text: `↳ ${d.fallback || ""}` })));
    }
  }
  review.appendChild(degradeBox);

  if (data.mode === "per_agent") {
    // 拓扑丢失降级:如实报 + 指路逐个导(零写盘,没有可 apply 的东西)
    review.appendChild(el("div", { class: "mgmt-hint", text: String(data.note || "") }));
    const names = (data.agents || []).map((a: any) => a.name).join(", ");
    review.appendChild(el("div", { text: t("agent.sys_per_agent_list", { names }) }));
    return;
  }

  const plan = data.plan || {};
  if ((data.agents_dropped || []).length) {
    review.appendChild(el("div", { class: "mgmt-hint",
      text: t("agent.sys_dropped", { names: (data.agents_dropped || []).join(", ") }) }));
  }

  // 域(名字可改 —— 同名活跃域会被 apply 拒)
  review.appendChild(_sectionTitle(t("agent.sys_domain_title")));
  const domNameIn = el("input", { type: "text" }) as HTMLInputElement;
  domNameIn.value = plan.domain?.name || "";
  domNameIn.oninput = () => { plan.domain.name = domNameIn.value; };
  review.appendChild(domNameIn);
  if (plan.domain?.value_md) review.appendChild(el("div", { class: "mgmt-hint", text: plan.domain.value_md }));
  const deo = plan.domain?.deontic || {};
  for (const f of (deo.forbid || [])) review.appendChild(el("div", { class: "mgmt-hint", text: `🚫 ${f}` }));
  for (const o of (deo.oblige || [])) review.appendChild(el("div", { class: "mgmt-hint", text: `📌 ${o}` }));
  const subs: any[] = plan.subdomains || [];
  if (subs.length) {
    review.appendChild(_sectionTitle(t("agent.sys_subdomains_title")));
    for (const sd of subs) {
      review.appendChild(el("div", { class: "mgmt-hint",
        text: `└ ${sd.name}${sd.parent_team_id ? ` (⊂ ${sd.parent_team_id})` : ""} — ${(sd.members || []).join(", ")}` }));
    }
  }

  // 判型表(kind 可改判 —— 改判即改 plan,apply 按人改后的执行)
  review.appendChild(_sectionTitle(t("agent.sys_roles_title")));
  const tbl = el("table", { style: "width:100%;border-collapse:collapse;font-size:12px" });
  tbl.appendChild(el("tr", null,
    el("th", { style: "text-align:left", text: t("agent.sys_col_agent") }),
    el("th", { style: "text-align:left", text: t("agent.sys_col_kind") }),
    el("th", { style: "text-align:left", text: t("agent.sys_col_identity") }),
    el("th", { style: "text-align:left", text: t("agent.sys_col_atoms") })));
  for (const r of (plan.roles || [])) {
    const kindSel = el("select", null, ...KINDS.map((k) =>
      el("option", { value: k, text: t(`agent.kind.${k}`) }))) as HTMLSelectElement;
    kindSel.value = r.agent_kind;
    kindSel.onchange = () => { r.agent_kind = kindSel.value; };
    tbl.appendChild(el("tr", { style: "border-top:1px solid rgba(128,128,128,.25)" },
      el("td", { text: r.name || r.role_id }),
      el("td", null, kindSel),
      el("td", { class: "mgmt-hint", text: (r.identity || "").slice(0, 80) }),
      el("td", { text: String((r.atoms || []).length) })));
  }
  review.appendChild(tbl);

  // workflow 模板(步骤表:task 可编;when/on_fail 如实显示)
  const wfs: any[] = plan.workflows || [];
  if (wfs.length) {
    review.appendChild(_sectionTitle(t("agent.sys_wf_title")));
    for (const wf of wfs) {
      review.appendChild(el("div", { style: "font-weight:600", text: `${wf.name} — ${wf.goal || ""}` }));
      for (const s of (wf.steps || [])) {
        const taskIn = el("input", { type: "text", style: "flex:1" }) as HTMLInputElement;
        taskIn.value = s.task || "";
        taskIn.oninput = () => { s.task = taskIn.value; };
        const meta: string[] = [];
        if ((s.depends_on || []).length) meta.push(`⇠ ${(s.depends_on || []).join(",")}`);
        if (s.inputs) meta.push(`inputs: ${(s.inputs || []).join(",")}`);
        if (s.when) meta.push(`when: ${s.when.step} ${s.when.status || s.when.contains || s.when.equals || ""}`);
        if (s.on_fail) meta.push(`on_fail: ${s.on_fail}`);
        review.appendChild(el("div", { style: "display:flex;gap:6px;align-items:center;margin:2px 0" },
          el("span", { style: "min-width:110px", text: `${s.id} · ${s.role_key}` }), taskIn,
          el("span", { class: "mgmt-hint", text: meta.join(" · ") })));
      }
    }
  }

  // 圆桌种子 / 移位 / 注记
  const seeds: any[] = plan.seed_intents || [];
  if (seeds.length) {
    review.appendChild(_sectionTitle(t("agent.sys_seed_title")));
    for (const s of seeds) {
      review.appendChild(el("div", { class: "mgmt-hint",
        text: `🎡 ${s.topic} — ${(s.participants || []).join(", ")}` }));
    }
  }
  for (const rl of (plan.relocations || [])) {
    review.appendChild(el("div", { class: "mgmt-hint", text: `↥ ${rl.element}: ${rl.moved_to}` }));
  }
  for (const n of (plan.notes || [])) review.appendChild(el("div", { class: "mgmt-hint", text: `ℹ ${n}` }));

  // H2A:人拍了才落
  const applyBtn = el("button", {
    class: "mgmt-submit", text: t("agent.sys_apply_btn"),
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
        seeds: d.roundtables_seeded || 0,
      }));
      if (d.note) review.appendChild(el("div", { class: "mgmt-hint", text: String(d.note) }));
      _deps.refreshPeers();
    } });
  review.appendChild(el("div", { style: "margin-top:10px" }, applyBtn));
}

const KarvyAgentsPanel = { open };
(window as unknown as { KarvyAgentsPanel: typeof KarvyAgentsPanel }).KarvyAgentsPanel = KarvyAgentsPanel;
export { KarvyAgentsPanel };
