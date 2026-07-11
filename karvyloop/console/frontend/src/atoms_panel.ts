/* atoms_panel.ts — ⚛ 原子面板(Hardy 同理:创建/列表分离 + 搜索 + 分页)。
 * 列表(搜索+分页+「＋新建」)/ 创建页分离。只用 dom/modal/i18n + KarvyWidgets。暴露 window.KarvyAtomsPanel.open()。
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

// ============ tag 系统(#3b):双语显示 + 筛选条 ============
// 标签形态兼容:"en|zh" 双语串 / {en,zh} dict / 旧纯英文串。en=语言中立匹配键,zh=中文界面显示(缺回退 en)。
function _tagEn(tag: unknown): string {
  if (tag && typeof tag === "object") return String((tag as { en?: string }).en || "").trim().toLowerCase();
  return String(tag == null ? "" : tag).split("|")[0].trim().toLowerCase();
}
function _tagText(tag: unknown): string {
  const _i18n = (window as unknown as { KarvyI18n?: { getLang?: () => string } }).KarvyI18n;
  const zh = !!(_i18n && _i18n.getLang && _i18n.getLang() === "zh");
  if (tag && typeof tag === "object") {
    const o = tag as { en?: string; zh?: string };
    return (zh ? (o.zh || o.en) : (o.en || o.zh)) || "";
  }
  const s = String(tag == null ? "" : tag);
  const parts = s.split("|");
  if (parts.length >= 2) return (zh ? parts[1] : parts[0]).trim() || parts[0].trim();
  return s.trim();
}
function _collectTags(items: any[], tagsOf: (it: any) => unknown[]): unknown[] {
  const seen = new Set<string>(); const out: unknown[] = [];
  for (const it of items) for (const tg of (tagsOf(it) || [])) {
    const k = _tagEn(tg); if (!k || seen.has(k)) continue; seen.add(k); out.push(tg);
  }
  return out;
}
// 筛选条:点一个 tag → onChange(activeEnKeyOrNull) 让调用方重渲列表。没标签 → 返回 null(优雅缺席)。
// TODO(#3b 第二步):完整手动 tag 增删编辑管理(这里只做打标+筛选)。
function _tagFilterBar(items: any[], tagsOf: (it: any) => unknown[],
  onChange: (active: string | null) => void): HTMLElement | null {
  const tags = _collectTags(items, tagsOf);
  if (!tags.length) return null;
  const bar = el("div", { class: "tag-filter-bar" });
  bar.appendChild(el("span", { class: "tag-filter-label", text: t("mgmt.filter_by_tag") }));
  let active: string | null = null;
  const chips: HTMLElement[] = [];
  const paint = () => { for (const c of chips) c.classList.toggle("active", c.dataset.k === active); };
  for (const tg of tags) {
    const k = _tagEn(tg);
    const chip = el("span", { class: "tag-chip", text: _tagText(tg),
      onclick: () => { active = (active === k) ? null : k; paint(); onChange(active); } }) as HTMLElement;
    chip.dataset.k = k;
    chips.push(chip); bar.appendChild(chip);
  }
  return bar;
}

// 列表(搜索 + 分页 + 「＋新建」)
async function renderList(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const data = await _getJSON("/api/atoms");
  const atoms = (data && data.atoms) || [];
  body.appendChild(el("div", { class: "mgmt-toolbar" },
    el("button", { class: "mgmt-new-btn", text: t("mgmt.new") + " " + t("mgmt.atoms_title"), onclick: () => renderCreate() }),
    // 整理相似原子(H2A):镜像知识库「整理相似知识」—— 一次 LLM 出合并建议,逐簇你拍板(离热路径,点才跑)。
    atoms.length >= 2 ? el("button", { class: "mgmt-inline-link atom-consolidate-btn",
      text: t("atom.consolidate_btn"), onclick: () => _runConsolidate() }) : null));
  if (!atoms.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.empty") })); return; }
  // tag 筛选条(#3b):点一个语义标签只留带它的原子。重渲分页列表实现过滤。
  const listHost = el("div", {});
  const _render = (active: string | null) => {
    listHost.innerHTML = "";
    const shown = active ? atoms.filter((a: any) => (a.tags || []).some((tg: unknown) => _tagEn(tg) === active)) : atoms;
    listHost.appendChild(_pagedAtoms(shown));
  };
  const filterBar = _tagFilterBar(atoms, (a: any) => a.tags || [], _render);
  if (filterBar) body.appendChild(filterBar);
  body.appendChild(listHost);
  _render(null);
}

// 原子分页列表(抽出:tag 筛选后重渲同一份渲染)
function _pagedAtoms(atoms: any[]): HTMLElement {
  return _KW.pagedList({
    items: atoms, pageSize: 8, searchPh: t("mgmt.search"), emptyText: t("mgmt.empty"),
    searchOf: (a: any) => a.id + " " + (a.kind || "") + " " + (a.prompt || "") + " " + (a.tools || []).join(" ")
      + " " + (a.tags || []).map((tg: unknown) => _tagEn(tg) + " " + _tagText(tg)).join(" "),
    renderItem: (a: any) => el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, a.id + " ", el("span", { class: "mc-tag", text: a.kind })),
        a.prompt ? el("div", { class: "mc-meta", text: a.prompt }) : null,
        (a.tags && a.tags.length)
          ? el("div", { class: "mc-meta" },
              ...a.tags.map((tg: unknown) => el("span", { class: "mc-tag mc-tag-sem", text: "🏷 " + _tagText(tg) })))
          : null,
        (a.tools && a.tools.length) ? el("div", { class: "mc-meta", text: "🔧 " + a.tools.join(", ") }) : null),
      el("div", { class: "dpref-actions" },
        el("button", { class: "dpref-edit", text: t("mgmt.edit"), onclick: () => _renderForm(a) }),
        el("button", { class: "mc-del", text: t("mgmt.delete"),
          onclick: async () => {
            if (!window.confirm(t("mgmt.confirm_del", { name: a.id }))) return;
            await _postJSON("/api/atom/remove", { atom_id: a.id });
            await renderList();
          } }))),
  });
}

// 创建/编辑页(与列表分离)。existing=null → 新建;existing=atom → 编辑(id 只读,改 prompt/kind/tools)。
function renderCreate(): void { _renderForm(null); }
function _renderForm(existing: any): void {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const editing = !!existing;
  const idIn = el("input", { type: "text", placeholder: "web_search" }) as HTMLInputElement;
  if (editing) { idIn.value = existing.id; idIn.readOnly = true; idIn.classList.add("readonly"); }
  const kindSel = el("select", null,
    el("option", { value: "task", text: t("atom.kind_task"), selected: !editing || existing.kind === "task" }),
    el("option", { value: "daemon", text: t("atom.kind_daemon"), selected: editing && existing.kind === "daemon" })) as HTMLSelectElement;
  const promptIn = el("textarea", {}) as HTMLTextAreaElement; if (editing) promptIn.value = existing.prompt || "";
  const toolsIn = el("input", { type: "text", placeholder: "run_command, read_file" }) as HTMLInputElement;
  if (editing) toolsIn.value = (existing.tools || []).join(", ");
  const msg = _formMsg();
  const submit = el("button", { class: "mgmt-submit", text: editing ? t("mgmt.save") : t("mgmt.create"),
    onclick: async () => {
      const tools = toolsIn.value.split(",").map((s) => s.trim()).filter(Boolean);
      const res = editing
        ? await _postJSON("/api/atom/update", { atom_id: existing.id, kind: kindSel.value, prompt: promptIn.value, tools })
        : await _postJSON("/api/atom/create", { atom_id: idIn.value.trim(), kind: kindSel.value, prompt: promptIn.value, tools });
      if (res.ok) await renderList();
      else _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: (editing ? t("mgmt.edit") : t("mgmt.create_new")) + " · " + t("mgmt.atoms_title") }),
    el("label", { text: t("mgmt.name") }), idIn,
    editing ? null : el("div", { class: "mgmt-hint", text: t("atom.id_hint") }),
    el("label", { text: t("atom.kind") }), kindSel,
    el("label", { text: t("atom.prompt_label") }), promptIn,
    el("label", { text: t("atom.tools_label") }), toolsIn,
    el("div", { class: "mgmt-row" }, submit,
      el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() })),
    msg));
}

// 整理相似原子(H2A)——镜像 memory_panel 的「整理相似知识」:一次 LLM 出合并建议(dry-run),
// 逐簇你拍板合并(离创建热路径,用户点才跑)。cluster 形态见 atoms/consolidate.py:
// { canonical_id, member_ids, merged_purpose, merged_tools, reason };apply 收 canonical_id/member_ids/
// merged_purpose/merged_tools,返回 { ok, removed_atoms, rewired_roles, merged_n }。
async function _runConsolidate(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("atom.consolidate_btn") }));
  const backRow = el("div", { class: "mgmt-row" },
    el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderList() }));
  const status = el("div", { class: "mgmt-hint", text: t("atom.consolidating") });
  body.appendChild(status); body.appendChild(backRow);
  const r = await _postJSON("/api/atoms/consolidate/suggest", {});
  status.remove();
  const clusters = (r.ok && r.data && r.data.clusters) || [];
  if (!clusters.length) { body.insertBefore(el("div", { class: "mgmt-empty", text: t("atom.consolidate_none") }), backRow); return; }
  const list = el("div", { class: "mgmt-list" });
  body.insertBefore(list, backRow);
  for (const c of clusters) {
    const card = el("div", { class: "mgmt-card consolidate-card" });
    // 合并去向:规范原子名 + 合并后 purpose
    card.appendChild(el("div", { class: "mc-main" },
      el("div", { class: "mc-name", text: t("atom.consolidate_into", { n: (c.member_ids || []).length }) }),
      el("div", { class: "consolidate-target" },
        (c.canonical_id ? el("span", { class: "mc-tag", text: c.canonical_id }) : null),
        el("span", { text: " " + (c.merged_purpose || "") }))));
    // 被并的成员原子 id(小字列出,让你看清合的是哪几个)
    const mem = el("div", { class: "consolidate-members" });
    (c.member_ids || []).forEach((m: string) => {
      mem.appendChild(el("div", { class: "consolidate-member", text: "・ " + m }));
    });
    if (c.reason) mem.appendChild(el("div", { class: "mgmt-hint", text: c.reason }));
    card.appendChild(mem);
    const doBtn = el("button", { class: "dpref-confirm", text: t("atom.consolidate_do"),
      onclick: async () => {
        (doBtn as HTMLButtonElement).disabled = true;
        const ar = await _postJSON("/api/atoms/consolidate/apply",
          { canonical_id: c.canonical_id, member_ids: c.member_ids,
            merged_purpose: c.merged_purpose || "", merged_tools: c.merged_tools || [] });
        if (ar.ok && ar.data && ar.data.ok) card.replaceWith(el("div", { class: "mgmt-hint",
          text: t("atom.consolidate_done", { n: (ar.data.removed_atoms || []).length }) }));
        else (doBtn as HTMLButtonElement).disabled = false;
      } });
    card.appendChild(el("div", { class: "dpref-actions" }, doBtn));
    list.appendChild(card);
  }
}

async function open(): Promise<void> {
  openMgmtModal(t("mgmt.atoms_title")); await renderList();
}

const KarvyAtomsPanel = { open };
(window as unknown as { KarvyAtomsPanel: typeof KarvyAtomsPanel }).KarvyAtomsPanel = KarvyAtomsPanel;
export { KarvyAtomsPanel };
