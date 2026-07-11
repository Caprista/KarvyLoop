/* roles_panel.ts — 🎭 角色面板(Hardy 大改:创建/列表分离 + 搜索分页 + 穿梭框选 atom/skill + 全范式编辑器)。
 * 列表(搜索+分页+「＋新建」)/ 创建页分离 / 查看编辑 = **完整七层范式**(IDENTITY/SOUL/USER/COMMITMENT/VERIFY
 * 可编 + MEMORY 只读 + atoms/skills 穿梭框 + 模型)+ #39⑤ 行为验收。用 dom/modal/i18n + KarvyWidgets。
 * 暴露 window.KarvyRolesPanel.open()。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  mgmtBody: () => HTMLElement | null;
  formMsg: () => HTMLElement;
  setMsg: (msgEl: HTMLElement, ok: boolean, text: string) => void;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
interface Widgets {
  transferList: (opts: { items: { id: string; label: string }[]; selected?: string[]; titleLeft?: string; titleRight?: string; searchPh?: string })
    => { el: HTMLElement; getSelected: () => string[] };
  pagedList: <T>(opts: { items: T[]; pageSize?: number; searchOf: (it: T) => string; renderItem: (it: T) => HTMLElement; searchPh?: string; emptyText?: string }) => HTMLElement;
}

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const _KW = (window as unknown as { KarvyWidgets: Widgets }).KarvyWidgets;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

const _xferTitles = () => ({ titleLeft: t("mgmt.available"), titleRight: t("mgmt.selected"), searchPh: t("mgmt.search") });

// 跨面板依赖:点角色卡「💬 直聊」→ 切到与该角色的私聊(l0/personal scope,不必先加进业务域)。
// 由 app.js 经 open({directChatRole}) 注入;缺注入时回退到全局 window.KarvyChat.directChatRole。
interface RolesDeps { directChatRole?: (roleId: string) => void }
let _deps: RolesDeps = {};
function _directChatRole(roleId: string): void {
  const fn = _deps.directChatRole
    || (window as unknown as { KarvyChat?: { directChatRole?: (id: string) => void } }).KarvyChat?.directChatRole;
  if (fn) fn(roleId);
}

// 异步填模型下拉(空=默认;软默认层叠 role→域→全局)
function _modelSelect(current: string): HTMLSelectElement {
  const sel = el("select", { class: "role-model" },
    el("option", { value: "", text: t("role.model_default") })) as HTMLSelectElement;
  (async () => {
    const md = await _getJSON("/api/models");
    for (const m of (md && md.models) || []) {
      const opt = el("option", { value: m.id,
        text: m.name + (m.id === (md.default || "") ? t("role.model_is_default") : "") }) as HTMLOptionElement;
      if (m.id === (current || "")) opt.selected = true;
      sel.appendChild(opt);
    }
  })();
  return sel;
}

// ============ 列表(搜索 + 分页 + 「＋新建」)============
async function renderList(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const rolesData = await _getJSON("/api/roles");
  const roles = (rolesData && rolesData.roles) || [];
  const bar = el("div", { class: "mgmt-toolbar" },
    el("button", { class: "mgmt-new-btn", text: t("mgmt.new") + " " + t("mgmt.roles_title"), onclick: () => renderCreate() }));
  body.appendChild(bar);
  await _renderResidentsGallery(body);   // 请原住民进来(补掉"一生一次引荐卡"之后再没门的黑洞)
  if (!roles.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") })); return; }
  body.appendChild(_KW.pagedList({
    items: roles, pageSize: 8, searchPh: t("mgmt.search"), emptyText: t("mgmt.empty"),
    searchOf: (v: any) => v.id + " " + (v.identity || "") + " " + (v.atom_ids || []).join(" ") + " " + (v.skill_ids || []).join(" "),
    renderItem: (v: any) => {
      const tags = (v.atom_ids || []).map((a: string) => el("span", { class: "mc-tag", text: "🔧 " + a }));
      const skTags = (v.skill_ids || []).map((s: string) => el("span", { class: "mc-tag mc-tag-skill", text: "🧩 " + s }));
      return el("div", { class: "mgmt-card" },
        el("div", { class: "mc-main" },
          el("div", { class: "mc-name", text: v.id }),
          v.identity ? el("div", { class: "mc-meta", text: v.identity }) : null,
          (tags.length || skTags.length) ? el("div", { class: "mc-meta" }, ...tags, ...skTags) : null),
        el("div", { class: "dpref-actions" },
          // Hardy:角色在这儿了就该能直接聊,不必先加进业务域(点这个 = 切到与它的私聊)。
          el("button", { class: "dpref-confirm", text: t("role.direct_chat"), onclick: () => _directChatRole(v.id) }),
          el("button", { class: "dpref-edit", text: t("role.view_edit"), onclick: () => _openRoleEdit(v) }),
          el("button", { class: "dpref-edit", text: t("eval.btn"), onclick: () => _openRoleEvals(v.id) }),
          el("button", { class: "mc-del", text: t("mgmt.delete"),
            onclick: async () => {
              if (!window.confirm(t("mgmt.confirm_del", { name: v.id }))) return;
              let res = await _postJSON("/api/role/remove", { role_id: v.id });
              if (res.data && res.data.blocked) {
                const names = (res.data.referenced_by || []).map((d: any) => d.name).join("、");
                if (!window.confirm(t("role.del_referenced", { names: names }))) return;
                res = await _postJSON("/api/role/remove", { role_id: v.id, force: true });
              }
              await renderList();
            } })));
    },
  }));
}

// ============ 请原住民进来(常驻门;补掉一生一次引荐卡的发现性黑洞)============
// Hardy 2026-07-09:引荐卡一生只出一次,之后加的原住民(如报销员)再没门可进、没处浏览。
// 这里随时列出**还没请进来**的随包原住民 + 一键「请进来」(在线实例化,不重启)。全请进来了则不占地方。
async function _renderResidentsGallery(body: HTMLElement): Promise<void> {
  let residents: any[] = [];
  try {
    const data = await _getJSON("/api/residents");
    residents = (data && data.residents) || [];
  } catch (e) { return; }
  const notIn = residents.filter((r: any) => !r.instantiated);
  if (!notIn.length) return;
  const sec = el("div", { class: "residents-gallery" });
  sec.appendChild(el("div", { class: "mgmt-section-title", text: t("residents.gallery_title") }));
  sec.appendChild(el("div", { class: "mgmt-hint", text: t("residents.gallery_hint") }));
  for (const r of notIn) {
    const invite = el("button", { class: "dpref-confirm", text: t("residents.invite_btn"),
      onclick: async () => {
        (invite as HTMLButtonElement).disabled = true; invite.textContent = t("residents.inviting");
        const res = await _postJSON("/api/residents/invite", { id: r.id });
        if (res.ok && res.data && res.data.ok !== false) {
          await renderList();   // 刷新:该原住民移出"可请进"、进入你的角色列表
        } else {
          (invite as HTMLButtonElement).disabled = false; invite.textContent = t("residents.invite_btn");
          window.alert(t("residents.invite_failed", { reason: (res.data && res.data.reason) || res.status }));
        }
      } }) as HTMLButtonElement;
    sec.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name", text: r.name || r.id }),
        r.pitch ? el("div", { class: "mc-meta", text: r.pitch }) : null),
      el("div", { class: "dpref-actions" }, invite)));
  }
  body.appendChild(sec);
}

// ============ 创建页(与列表分离)============
async function renderCreate(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const atomsData = await _getJSON("/api/atoms");
  const skillsData = await _getJSON("/api/skills");
  let atomIds: string[] = ((atomsData && atomsData.atoms) || []).map((a: any) => a.id);
  const skillIds: string[] = ((skillsData && skillsData.skills) || []).map((s: any) => s.name);

  const idIn = el("input", { type: "text", placeholder: "pm" }) as HTMLInputElement;
  const identityIn = el("textarea", {}) as HTMLTextAreaElement;
  const soulIn = el("textarea", {}) as HTMLTextAreaElement;
  const userIn = el("textarea", {}) as HTMLTextAreaElement;
  const modelSel = _modelSelect("");
  // atoms 穿梭框(就地买糖会重建它,保留已选)
  const atomBox = el("div", {});
  let atomTL = _KW.transferList({ items: atomIds.map((id) => ({ id, label: id })), selected: [], ..._xferTitles() });
  atomBox.appendChild(atomTL.el);
  const skillTL = _KW.transferList({ items: skillIds.map((id) => ({ id, label: "🧩 " + id })), selected: [], ..._xferTitles() });
  // 就地买糖:缺原子内联建 → 建完并入穿梭框且预选
  const buyId = el("input", { type: "text", placeholder: "new_atom" }) as HTMLInputElement;
  const buyKind = el("select", null,
    el("option", { value: "task", text: t("atom.kind_task") }),
    el("option", { value: "daemon", text: t("atom.kind_daemon") })) as HTMLSelectElement;
  const buyMsg = _formMsg();
  const buyBtn = el("button", { class: "mgmt-inline-link", text: "+ " + t("role.buy_sugar"),
    onclick: async () => {
      const id = buyId.value.trim();
      if (!id) return;
      const res = await _postJSON("/api/atom/create", { atom_id: id, kind: buyKind.value, prompt: "" });
      if (res.ok) {
        const cur = atomTL.getSelected(); if (!cur.includes(id)) cur.push(id);
        if (!atomIds.includes(id)) atomIds = atomIds.concat([id]);
        atomBox.innerHTML = "";
        atomTL = _KW.transferList({ items: atomIds.map((x) => ({ id: x, label: x })), selected: cur, ..._xferTitles() });
        atomBox.appendChild(atomTL.el);
        buyId.value = ""; _setMsg(buyMsg, true, t("mgmt.created"));
      } else _setMsg(buyMsg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
    } });
  const buyRow = el("div", { class: "mgmt-buysugar" },
    el("div", { class: "mgmt-hint", text: t("role.buy_hint") }),
    el("div", { class: "mgmt-row" }, buyId, buyKind, buyBtn), buyMsg);
  const msg = _formMsg();
  const submit = el("button", { class: "mgmt-submit", text: t("mgmt.create"),
    onclick: async () => {
      const res = await _postJSON("/api/role/create", {
        role_id: idIn.value.trim(), identity: identityIn.value, soul: soulIn.value,
        user_desc: userIn.value, atom_ids: atomTL.getSelected(), model: modelSel.value,
        skill_ids: skillTL.getSelected(),
      });
      if (res.ok) await renderList();
      else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: t("mgmt.create_new") + " · " + t("mgmt.roles_title") }),
    el("label", { text: t("mgmt.name") }), idIn,
    el("label", { text: t("role.identity_label") }), identityIn,
    el("label", { text: t("role.soul_label") }), soulIn,
    el("label", { text: t("role.user_label") }), userIn,
    el("label", { text: t("role.model_label") }), modelSel,
    el("label", { text: t("role.pick_atoms") }), atomBox, buyRow,
    el("label", { text: t("role.pick_skills") }), el("div", { class: "mgmt-hint", text: t("role.skills_hint") }), skillTL.el,
    el("div", { class: "mgmt-row" }, submit,
      el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })),
    msg));
}

// ============ 查看编辑 = 完整七层范式 ============
async function _openRoleEdit(v: any): Promise<void> {
  openMgmtModal(v.id);
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-hint", text: t("role.paradigm_hint") }));
  // 一眼看全:表单很长、首屏只露 ~3 层,用户会以为"只有 3 个字段"。顶部摆一行范式全景,
  // 明说共 7 层、往下滚都能填 —— 消除"填不了更多"的错觉(discoverability)。
  body.appendChild(el("div", { class: "paradigm-overview", text: t("role.paradigm_overview") }));
  const pmResp = await _getJSON("/api/role/paradigm?role_id=" + encodeURIComponent(v.id));
  const pm = (pmResp && pmResp.paradigm) || {};
  const atomsData = await _getJSON("/api/atoms");
  const skillsData = await _getJSON("/api/skills");
  const allAtoms: string[] = ((atomsData && atomsData.atoms) || []).map((a: any) => a.id);
  const allSkills: string[] = ((skillsData && skillsData.skills) || []).map((s: any) => s.name);

  // 5 个可编辑灵魂槽(逐槽 textarea);记原值,保存时只 POST 改过的
  const slots: { key: string; slot: string; label: string; hint?: string }[] = [
    { key: "identity", slot: "IDENTITY", label: t("role.identity_label") },
    { key: "soul", slot: "SOUL", label: t("role.soul_label") },
    { key: "user", slot: "USER", label: t("role.user_label") },
    { key: "commitment", slot: "COMMITMENT", label: t("role.commitment_label"), hint: t("role.commitment_hint") },
    { key: "verify", slot: "VERIFY", label: t("role.verify_label") },
  ];
  const areas: Record<string, { ta: HTMLTextAreaElement; orig: string; slot: string }> = {};
  const form = el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() });
  for (const s of slots) {
    const orig = (pm[s.key] || "") as string;
    const ta = el("textarea", { class: "edit-area" }) as HTMLTextAreaElement; ta.value = orig;
    // 空槽也要看得见:给占位符 + 淡样式,让只填了几层的 role 仍清晰露出全部 5 个可编辑框(别空白不可见)。
    if (!orig.trim()) { ta.placeholder = t("role.slot_empty_ph"); ta.classList.add("edit-area-empty"); }
    ta.addEventListener("input", () => ta.classList.toggle("edit-area-empty", !ta.value.trim()));
    areas[s.slot] = { ta, orig, slot: s.slot };
    form.appendChild(el("div", { class: "soul-slot" },
      el("label", {}, s.label, s.hint ? el("span", { class: "soul-hint", text: " — " + s.hint }) : null),
      ta));
  }
  // MEMORY 只读展示(运行时文件)
  form.appendChild(el("div", { class: "soul-slot" },
    el("label", { text: t("role.memory_label") }),
    el("div", { class: "soul-ro", text: (pm.memory || "—") })));
  // 模型 + atoms/skills 穿梭框(COMPOSITION 走这里)
  const modelSel = _modelSelect(v.model || "");
  const atomTL = _KW.transferList({ items: allAtoms.map((id) => ({ id, label: id })), selected: (pm.atom_ids || []), ..._xferTitles() });
  const skillTL = _KW.transferList({ items: allSkills.map((id) => ({ id, label: "🧩 " + id })), selected: (pm.skill_ids || []), ..._xferTitles() });
  form.appendChild(el("label", { text: t("role.edit_model") })); form.appendChild(modelSel);
  form.appendChild(el("label", { text: t("role.pick_atoms") })); form.appendChild(atomTL.el);
  form.appendChild(el("label", { text: t("role.pick_skills") })); form.appendChild(skillTL.el);

  const msg = _formMsg();
  const save = el("button", { class: "mgmt-submit", text: t("mgmt.save"),
    onclick: async () => {
      // ① 改过的灵魂槽逐个 POST /role/paradigm/update
      for (const k of Object.keys(areas)) {
        const a = areas[k];
        if (a.ta.value !== a.orig) {
          const r = await _postJSON("/api/role/paradigm/update", { role_id: v.id, slot: a.slot, text: a.ta.value });
          if (!(r.ok && r.data && r.data.ok)) { _setMsg(msg, false, t("mgmt.failed", { err: (r.data && r.data.reason) || r.status })); return; }
        }
      }
      // ② 模型 + composition(atoms/skills)走 /role/update
      const res = await _postJSON("/api/role/update",
        { role_id: v.id, model: modelSel.value, atom_ids: atomTL.getSelected(), skill_ids: skillTL.getSelected() });
      if (res.ok) renderList();
      else _setMsg(msg, false, t("mgmt.failed", { err: (res.data && (res.data.detail || res.data.reason)) || res.status }));
    } });
  // 🪄 补全范式:调现成的 /gaps 引擎为缺层(SOUL/USER/VERIFY…)LLM 起草 → 填进空槽,你核对后保存才落库(不补不落库)
  const completeBtn = el("button", { class: "mgmt-inline-link", text: t("role.complete_btn"),
    onclick: async () => {
      _setMsg(msg, true, t("role.completing"));
      const r = await _getJSON("/api/role/paradigm/gaps?role_id=" + encodeURIComponent(v.id));
      const sug = (r && r.suggestions) || {};
      let n = 0;
      for (const slot of Object.keys(sug)) {
        const a = areas[slot]; const draft = (sug[slot] || "").trim();
        if (a && draft) {
          const cur = a.ta.value.trim();
          if (!cur || cur === "(待充实)") { a.ta.value = draft; n++; }   // 只填空槽,不覆盖你写的
        }
      }
      if (n > 0) _setMsg(msg, true, t("role.completed_draft", { n }));
      else if (r && r.complete) _setMsg(msg, true, t("role.complete_none"));
      else _setMsg(msg, false, t("role.complete_no_llm"));
    } });
  form.appendChild(el("div", { class: "mgmt-row" }, save, completeBtn,
    el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })));
  form.appendChild(msg);
  body.appendChild(form);
}

// #39 ⑤:角色行为验收 —— 一句测试 prompt + 期望(含/不含关键词)→ 一键跑、红绿。
async function _openRoleEvals(roleId: string): Promise<void> {
  openMgmtModal("🧪 " + t("eval.title", { role: roleId }));
  await _renderRoleEvals(roleId);
}
async function _renderRoleEvals(roleId: string): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-hint", text: t("eval.subtitle") }));
  const data = await _getJSON("/api/role/evals?role_id=" + encodeURIComponent(roleId));
  const evals = (data && data.evals) || [];
  const runBar = el("div", { class: "dpref-actions" });
  const resultBox = el("div", {});
  runBar.appendChild(el("button", { class: "dpref-confirm", text: t("eval.run_all"),
    onclick: async () => {
      resultBox.innerHTML = ""; resultBox.appendChild(el("div", { class: "mgmt-hint", text: t("eval.running") }));
      const r = await _postJSON("/api/role/eval/run", { role_id: roleId });
      resultBox.innerHTML = "";
      if (!r.ok || !r.data || !r.data.ok) {
        resultBox.appendChild(el("div", { class: "mgmt-hint",
          text: (r.data && r.data.reason === "no_llm") ? t("eval.no_llm") : t("eval.run_fail") }));
        return;
      }
      resultBox.appendChild(el("div", { class: "mgmt-section-title",
        text: t("eval.score", { pass: r.data.passed, total: r.data.total }) }));
      for (const res of r.data.results) {
        const ok = res.passed;
        const badge = el("span", { class: "dpref-badge " + (ok ? "confirmed" : "provisional"),
          text: ok ? "✓ " + t("eval.pass") : "✗ " + t("eval.fail") });
        const detail = ok ? "" : (res.error ? ("⚠ " + res.error)
          : t("eval.why", { miss: (res.missing || []).join("、") || "—",
                            forb: (res.present_forbidden || []).join("、") || "—" }));
        resultBox.appendChild(el("div", { class: "mgmt-card" },
          el("div", { class: "mc-main" },
            el("div", { class: "mc-name" }, el("span", { text: res.prompt }), " ", badge),
            detail ? el("div", { class: "mc-meta", text: detail }) : null,
            el("div", { class: "mc-meta", text: (res.reply || "").slice(0, 200) }))));
      }
    } }));
  if (evals.length) body.appendChild(runBar);
  body.appendChild(resultBox);
  if (!evals.length) body.appendChild(el("div", { class: "mgmt-empty", text: t("eval.empty") }));
  else {
    const list = el("div", { class: "mgmt-list" });
    for (const ev of evals) {
      list.appendChild(el("div", { class: "mgmt-card" },
        el("div", { class: "mc-main" },
          el("div", { class: "mc-name", text: ev.prompt }),
          el("div", { class: "mc-meta", text: t("eval.expect", {
            c: (ev.contains || []).join("、") || "—", a: (ev.absent || []).join("、") || "—" }) })),
        el("div", { class: "dpref-actions" },
          el("button", { class: "mc-del", text: t("mgmt.delete"),
            onclick: async () => { await _postJSON("/api/role/eval/delete", { role_id: roleId, eval_id: ev.id }); _renderRoleEvals(roleId); } }))));
    }
    body.appendChild(list);
  }
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("eval.add_title") }));
  const promptIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.prompt_ph") }) as HTMLInputElement;
  const containsIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.contains_ph") }) as HTMLInputElement;
  const absentIn = el("input", { class: "mgmt-input", type: "text", placeholder: t("eval.absent_ph") }) as HTMLInputElement;
  const split = (s: string) => (s || "").split(/[,，、]/).map((x) => x.trim()).filter(Boolean);
  body.appendChild(promptIn); body.appendChild(containsIn); body.appendChild(absentIn);
  body.appendChild(el("button", { class: "dpref-confirm", text: t("eval.add"),
    onclick: async () => {
      if (!(promptIn.value || "").trim()) return;
      const r = await _postJSON("/api/role/eval/add", { role_id: roleId, prompt: promptIn.value,
        contains: split(containsIn.value), absent: split(absentIn.value) });
      if (r.ok && r.data && r.data.ok) _renderRoleEvals(roleId); else alert(t("eval.add_fail"));
    } }));
  body.appendChild(el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => open() }));
}

async function open(deps?: RolesDeps): Promise<void> {
  if (deps) _deps = deps;   // app.js 注入直聊等跨面板依赖;nav 无参调用保留上次注入
  openMgmtModal(t("mgmt.roles_title")); await renderList();
}

const KarvyRolesPanel = { open };
(window as unknown as { KarvyRolesPanel: typeof KarvyRolesPanel }).KarvyRolesPanel = KarvyRolesPanel;
export { KarvyRolesPanel };
