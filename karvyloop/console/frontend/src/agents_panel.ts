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
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string; getLang?: () => string }
interface Deps { refreshPeers: () => void }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);
// 本面板专用的**局部**双语标签(原子/技能清单这类没进后端 note 的短标签)。
// 刻意不进 i18n.ts —— 那个文件另有代理独占;后端已本地化的 note 一律直接展示,不重写。
const _lang = (): string =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.getLang?.() || "en";
const _L = (en: string, zh: string): string => (_lang() === "zh" ? zh : en);
// 中性态(无绿 ✓ / 无红叉):executor/skill 型没建角色,不能报"成功进角色库"。
const _setNeutral = (m: HTMLElement, text: string): void => {
  m.className = "mgmt-msg"; m.textContent = text;
};

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
  const detail = el("div", { class: "agent-import-detail" });   // 判型明细(落的原子/识别 skill/跳转)
  const submit = el("button", {
    class: "mgmt-submit", text: t("agent.import_btn"),
    onclick: async () => {
      detail.innerHTML = "";
      const tools = toolsIn.value.split(",").map((s) => s.trim()).filter(Boolean);
      const res = await _postJSON("/api/agent/import", {
        role_id: idIn.value.trim(), source_type: srcSel.value,
        system_prompt: promptIn.value, tools,
      });
      if (res.ok) _renderImportResult(msg, detail, res.data);
      else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: t("agent.import_title") }),
    el("label", { text: t("mgmt.name") }), idIn,
    el("label", { text: t("agent.source_type") }), srcSel,
    el("label", { text: t("agent.system_prompt") }), promptIn,
    el("label", { text: t("atom.tools_label") }), toolsIn,
    submit, msg, detail));
}

/** J2:按后端判型(import_kind)如实显示导入结果 —— 不再无脑 imported✓(假成功病)。
 *  后端 note 已本地化(agent_import.note.*),直接展示;分三型:
 *   · decision / hybrid / v0 降级 → 真建了 role:绿 ✓「已建角色」+ 顺带列原子/skill;
 *   · pure_executor → 只落公共原子、没建 role:中性显示后端诚实 note + 列落的原子/识别 skill;
 *   · skill_like → 角色库/原子库都没写:中性显示后端诚实 note + 指路技能库导入。
 *  只有真建了 role 才 refreshPeers(executor/skill 没角色可刷,别误刷工位)。 */
function _renderImportResult(msg: HTMLElement, detail: HTMLElement, data: any): void {
  detail.innerHTML = "";
  const ik = String(data.import_kind || "");
  const note = String(data.note || "");
  const rid = String(data.role_id || "");

  // —— skill_like:一段流程剧本,不是「谁」—— 绝不说"已进角色库",指路技能库 ——
  if (ik === "skill_like") {
    _setNeutral(msg, note || _L("This is a skill, not a role.", "这是一段技能,不是角色。"));
    _renderSkillsRecognized(detail, data);
    _skillLibJump(detail);
    return;
  }

  // —— pure_executor:纯执行体只落公共原子、没建 role —— 绝不说"已进角色库" ——
  if (ik === "pure_executor") {
    _setNeutral(msg, note);               // 后端 note:已落 N 原子/任何角色可组合/要决策席自建 role
    _renderAtomsLanded(detail, data);
    _renderSkillsRecognized(detail, data);
    return;
  }

  // —— decision / hybrid / v0 降级:真建了 role —— 绿 ✓ ——
  _setMsg(msg, true, t("agent.imported", { id: rid }));
  if (note) detail.appendChild(el("div", { class: "mgmt-hint", text: note }));  // advisory_persona / v0 补充说明
  _renderAtomsLanded(detail, data);
  _renderSkillsRecognized(detail, data);
  _deps.refreshPeers();                    // 只有真建 role 才刷左栏工位
}

/** 如实列落进公共原子库的原子(新建的标 new);无原子则不显示。 */
function _renderAtomsLanded(detail: HTMLElement, data: any): void {
  const atoms: string[] = data.atoms || [];
  if (!atoms.length) return;
  const created = new Set<string>(data.atoms_created || []);
  detail.appendChild(el("div", { class: "mgmt-hint",
    text: _L(`Atoms landed (${atoms.length}, ${created.size} new):`,
             `落的公共原子(${atoms.length} 个,新建 ${created.size} 个):`) }));
  for (const a of atoms) {
    detail.appendChild(el("div", { class: "mgmt-hint",
      text: `· ${a}${created.has(a) ? _L(" (new)", "(新)") : ""}` }));
  }
}

/** 如实列识别出的内含技能名(去技能库导入的线索);无则不显示。 */
function _renderSkillsRecognized(detail: HTMLElement, data: any): void {
  const skills: string[] = data.skills_recognized || [];
  if (!skills.length) return;
  detail.appendChild(el("div", { class: "mgmt-hint",
    text: _L(`Skills recognized: ${skills.join(", ")}`,
             `识别出的技能:${skills.join("、")}`) }));
}

/** skill_like 型:给一个"去技能库导入"跳转按钮。技能库面板是懒加载模块,点时若未载入就先注入
 *  /static/skills_panel.js(它依赖的 dom/modal/i18n/render 都是首屏脚本)再 open —— 保证按钮永远可用。 */
function _skillLibJump(detail: HTMLElement): void {
  detail.appendChild(el("div", { class: "dpref-actions" },
    el("button", { class: "mgmt-submit", text: _L("Go to Skill Library →", "去技能库导入 →"),
      onclick: async () => {
        const w = window as unknown as { KarvySkillsPanel?: { open?: () => void } };
        if (!w.KarvySkillsPanel) {
          await new Promise<void>((res, rej) => {
            const s = document.createElement("script");
            s.src = "/static/skills_panel.js";
            s.onload = () => res();
            s.onerror = () => rej(new Error("skills_panel.js load failed"));
            document.head.appendChild(s);
          }).catch(() => { /* 载入失败:留在原页,note 已指明去技能库 */ });
        }
        w.KarvySkillsPanel?.open?.();
      } })));
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
