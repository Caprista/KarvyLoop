/* domains_panel.ts — 🏢 业务域面板(从 app.js 抽出,大尾巴 slice;最耦合一块)。
 * 组织架构树(域⊃子域 + 域下角色,点角色进私聊)+ 域列表(编辑价值观/成员 · 归档/恢复)+ 建域表单
 * (父域选择=子域 · 角色手选 + 就地买糖建角色)。
 *
 * 空/非空分态(Hardy):
 *   - 没有业务域 → 引导态:一句"来新建你的第一个业务域吧" + 模板作为**创建路径**(模板是主角,帮你开张)。
 *   - 已有业务域 → 直接给域列表(现有域是主角);模板收进「＋ 新建业务域」入口(点开才展开),不再顶在列表上方误导。
 *   语义:模板 = "新建业务域"的一种方式(选模板=带配置新建),不是"打开已有"。
 *
 * 跨面板依赖(诚实标注,经 open(deps) 注入,不上 window):
 *   - refreshPeers():建/改/归档/恢复后刷新左栏可聊对象
 *   - pushChatLine(kind,text):归档时回一条系统行
 *   - openPeerChat(member):点组织树角色 → 进私聊(app.js 里裹了 closeMgmtModal + _currentPeerLabel + switchPeer,
 *     那两个是 app.js 的可变状态/函数,别拆出来)
 * 「新建角色」买糖 → 直接 window.KarvyRolesPanel.open()(角色面板已迁,同级模块)。
 * 暴露 window.KarvyDomainsPanel.open(deps)。
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
interface Deps {
  refreshPeers: () => void;
  pushChatLine: (kind: string, text: string) => void;
  openPeerChat: (member: any) => void;
}

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);
const tB = (x: unknown): string => {
  const w = (window as unknown as { KarvyI18n: { tBackend?: (s: unknown) => string } }).KarvyI18n;
  return w && w.tBackend ? w.tBackend(x) : String(x == null ? "" : x);
};

let _deps: Deps = { refreshPeers: () => {}, pushChatLine: () => {}, openPeerChat: () => {} };

// 业务域编辑:价值观 value.md + 成员=**角色多选 chip**(不再让用户手编 member_query DSL,Hardy)。
// 当前成员从 member_query 的 agent: 子句解析出来、预选;user(域主)子句后端保留,前端不碰。
async function _openDomainEdit(d: any): Promise<void> {
  openMgmtModal(d.name);
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const valueIn = el("textarea", { class: "edit-area" }) as HTMLTextAreaElement; valueIn.value = d.value_md || "";
  // 解析当前成员里的角色(member_query 的 agent:<x> 子句),作为预选
  const curAgents = new Set<string>();
  ((d.member_query || "").match(/agent:(\S+)/g) || []).forEach((m: string) => curAgents.add(m.slice(6)));
  const picked = new Set<string>(curAgents);
  const picks = el("div", { class: "mgmt-picks" });
  const addChip = (id: string) => {
    const chip = el("span", { class: "mgmt-pick" + (picked.has(id) ? " on" : ""), text: id });
    chip.addEventListener("click", () => {
      if (picked.has(id)) { picked.delete(id); chip.classList.remove("on"); }
      else { picked.add(id); chip.classList.add("on"); }
    });
    picks.appendChild(chip);
  };
  // 从公共角色库拉全部角色;并集上当前成员(防成员里有库里没有的旧角色被漏掉)
  const rolesData = await _getJSON("/api/roles");
  const libRoles: string[] = ((rolesData && rolesData.roles) || []).map((v: any) => v.id);
  const allIds: string[] = [];
  for (const id of [...libRoles, ...curAgents]) if (id && !allIds.includes(id)) allIds.push(id);
  if (!allIds.length) picks.appendChild(el("div", { class: "mgmt-hint", text: t("domain.role_none") }));
  else for (const id of allIds) addChip(id);
  const buySugar = el("button", {
    class: "mgmt-inline-link", text: t("domain.role_new"),
    onclick: () => (window as unknown as { KarvyRolesPanel: { open: () => void } }).KarvyRolesPanel.open(),
  });
  const msg = _formMsg();
  const save = el("button", { class: "mgmt-submit", text: t("mgmt.save"),
    onclick: async () => {
      const res = await _postJSON("/api/domain/update",
        { domain_id: d.id, value_md: valueIn.value, agents: Array.from(picked) });
      if (res.ok) { _deps.refreshPeers(); open(); }
      else _setMsg(msg, false, t("mgmt.failed", { err: res.data.reason || res.status }));
    } });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("label", { text: t("domain.value_label") }), valueIn,
    el("label", { text: t("domain.members_label") }), picks,
    el("div", { class: "mc-meta", text: t("domain.members_hint") }),
    el("div", { class: "mgmt-row" }, buySugar),
    el("div", { class: "mgmt-row" }, save,
      el("button", { class: "mgmt-inline-link", text: t("domain.back"), onclick: () => open() })),
    msg));
}


// 域模板 = **"新建业务域"的一种方式**(选中模板 → 带配置实例化建出来 → 进域管理),不是"打开已有"。
// 开箱域模板(价值观+硬规矩+配好灵魂的角色;实例化那刻起长成你的)。
// 空态:模板是主角(headline=false 由调用方给引导语);非空态:收进「＋ 新建」入口。
async function _renderDomainTemplates(body: HTMLElement): Promise<boolean> {
  const data = await _getJSON("/api/domain/templates");
  const tpls = (data && data.templates) || [];
  if (!tpls.length) return false;
  const list = el("div", { class: "mgmt-list domtpl-list" });
  for (const tp of tpls) {
    const rolesTxt = (tp.roles || []).map((r: any) => r.nickname + "·" + r.title).join(" / ");
    // 中性:选此模板 = 带配置**新建**一个域(不是打开已有)
    const btn = el("button", { class: "dpref-confirm", text: t("domtpl.use"),
      onclick: async () => {
        btn.textContent = t("domtpl.creating");
        (btn as HTMLButtonElement).disabled = true;
        const r = await _postJSON("/api/domain/templates/instantiate", { template_id: tp.id });
        if (r.ok && r.data && r.data.ok) {
          _deps.refreshPeers(); await renderDomainsPanel();
        } else {
          btn.textContent = (r.data && r.data.reason) ? tB(r.data.reason) : "?";
          (btn as HTMLButtonElement).disabled = false;   // fail-loud + 可重试
        }
      } });
    list.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name", text: (tp.emoji || "🏢") + " " + tp.name }),
        el("div", { class: "mc-meta", text: tp.description || "" }),
        el("div", { class: "mc-meta", text: rolesTxt })),
      el("div", { class: "dpref-actions" }, btn)));
  }
  body.appendChild(list);
  return true;
}

// 从零手写建域表单(名字/父域/价值观/角色多选)。空态与「＋新建」入口都复用它。
function _renderCreateForm(body: HTMLElement, roles: any[], activeDoms: any[]): void {
  const nameIn = el("input", { type: "text" }) as HTMLInputElement;
  const valueIn = el("textarea", {}) as HTMLTextAreaElement;       // 多行!不再单行 prompt
  // 角色**多选** chip(从角色库)+ 可空(先想干啥再定)+ 就地建角色(买糖)。Hardy:建域要能加多个角色。
  const pickedRoles = new Set<string>();
  const rolePicks = el("div", { class: "mgmt-picks" });
  if (!roles.length) {
    rolePicks.appendChild(el("div", { class: "mgmt-hint", text: t("domain.role_none") }));
  } else {
    for (const v of roles) {
      const chip = el("span", { class: "mgmt-pick", text: v.id });
      chip.addEventListener("click", () => {
        if (pickedRoles.has(v.id)) { pickedRoles.delete(v.id); chip.classList.remove("on"); }
        else { pickedRoles.add(v.id); chip.classList.add("on"); }
      });
      rolePicks.appendChild(chip);
    }
  }
  const buySugar = el("button", {
    class: "mgmt-inline-link", text: t("domain.role_new"),
    onclick: () => (window as unknown as { KarvyRolesPanel: { open: () => void } }).KarvyRolesPanel.open(),
  });
  // §2.5:父域选择器 —— 空=顶级域;选一个=在它下面建**子域**(继承父域价值观/规章)
  const parentSel = el("select", null, el("option", { value: "", text: t("domain.parent_none") })) as HTMLSelectElement;
  for (const d of activeDoms) parentSel.appendChild(el("option", { value: d.id, text: d.name }));
  const msg = _formMsg();
  const submit = el("button", {
    class: "mgmt-submit", text: t("mgmt.create"),
    onclick: async () => {
      const res = await _postJSON("/api/domain/create", {
        name: nameIn.value.trim(), value_md: valueIn.value,
        agents: Array.from(pickedRoles),   // 多选角色(后端 agents 优先,member_query 每个一个 agent 子句)
        parent_id: parentSel.value,
      });
      if (res.ok) {
        _setMsg(msg, true, t("mgmt.created"));
        _deps.refreshPeers();
        await renderDomainsPanel();
      } else {
        _setMsg(msg, false, t("mgmt.failed", { err: res.data.detail || res.data.reason || res.status }));
      }
    },
  });
  body.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() },
    el("div", { class: "mgmt-section-title", text: t("mgmt.create_new") }),
    el("label", { text: t("mgmt.name") }), nameIn,
    el("label", { text: t("domain.parent_label") }), parentSel,
    el("label", { text: t("domain.value_label") }), valueIn,
    el("label", { text: t("domain.role_label") }),
    rolePicks,
    el("div", { class: "mgmt-row" }, buySugar),
    submit, msg));
}

// 引导空态:没有业务域时打开这里。模板是主角(创建路径),下面附从零手写建域表单。
async function _renderEmptyGuide(body: HTMLElement, roles: any[], activeDoms: any[]): Promise<void> {
  body.appendChild(el("div", { class: "mgmt-empty-guide" },
    el("div", { class: "mgmt-section-title", text: t("domain.empty_guide") }),
    el("div", { class: "mgmt-hint", text: t("domain.empty_guide_hint") })));
  // 模板 = 帮你开张的创建路径(主角)
  const tplTitle = el("div", { class: "mgmt-section-title", text: t("domtpl.pick_title") });
  body.appendChild(tplTitle);
  body.appendChild(el("div", { class: "mgmt-hint", text: t("domtpl.hint") }));
  const hasTpl = await _renderDomainTemplates(body);
  if (!hasTpl) tplTitle.remove();   // 没模板就别留空标题
  // 也给一条从零手写的路(分隔:或从零开始)
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.or_from_scratch") }));
  _renderCreateForm(body, roles, activeDoms);
}

async function renderDomainsPanel(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const data = await _getJSON("/api/domains");      // P0 审计:专用列表(含归档,带 value/成员)
  const rolesData = await _getJSON("/api/roles");
  const roles = (rolesData && rolesData.roles) || [];
  const doms = (data && data.domains) || [];
  const activeDoms = doms.filter((d: any) => d.lifecycle !== "archived");

  // 分态:没有(活跃)业务域 → 引导态,模板作创建路径当主角(帮你开张);别把模板顶在列表上误导"打开已有"。
  if (!activeDoms.length) {
    await _renderEmptyGuide(body, roles, activeDoms);
    return;
  }

  // 已有业务域 → 现有域是主角:直接给组织树 + 域列表;模板 + 从零建域收进「＋ 新建业务域」入口。
  // 组织架构树(Hardy):① 看清 业务域 ⊃ 子业务域 的归属层级;② 看清每个域下有哪些角色;
  // ③ 点角色 = 私聊该 agent(openPeerChat → 进左栏私聊区)。成员复用 /api/peers,层级用 parent_id。
  {
    const peersData = await _getJSON("/api/peers");
    const allPeers = (peersData && peersData.peers) || [];
    const membersByDom: Record<string, any[]> = {};
    for (const p of allPeers) {
      if (p.is_group || p.is_private) continue;   // 只取 agent 成员(非群、非私聊 Karvy)
      (membersByDom[p.domain_id] = membersByDom[p.domain_id] || []).push(p);
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("mgmt.org_title") }));
    // 同名脏域去重(保留首个 id),再按 parent_id 建层级树
    const seenName = new Set<string>();
    const clean: any[] = [];
    for (const d of activeDoms) { if (seenName.has(d.name)) continue; seenName.add(d.name); clean.push(d); }
    const ids = new Set(clean.map((d) => d.id));
    const childrenOf: Record<string, any[]> = {};
    const roots: any[] = [];
    for (const d of clean) {
      const pid = d.parent_id && ids.has(d.parent_id) ? d.parent_id : null;
      if (pid) (childrenOf[pid] = childrenOf[pid] || []).push(d);
      else roots.push(d);
    }
    const tree = el("div", { class: "org-tree" });
    const renderNode = (d: any, depth: number): void => {
      const node = el("div", { class: "org-domain" + (depth ? " is-sub" : "") });
      node.style.marginLeft = depth * 18 + "px";
      node.appendChild(el("div", { class: "org-domain-head" },
        el("span", { class: "org-ico", text: depth ? "↳ 📁" : "📂" }),
        el("span", { text: d.name }),
        depth ? el("span", { class: "org-sub-badge", text: t("domain.sub_badge") }) : null));
      const members = membersByDom[d.id] || [];
      const seenRole = new Set<string>();
      let shown = 0;
      for (const m of members) {
        const rk = m.role + "|" + (m.agent_id || "");
        if (seenRole.has(rk)) continue;
        seenRole.add(rk); shown++;
        const rid = (m.role === "agent" && m.agent_id) ? m.agent_id : (m.role || "");
        node.appendChild(el("div", { class: "org-role-row" },
          el("button", { class: "org-role", title: t("mgmt.org_chat_hint"),
            onclick: () => _deps.openPeerChat(m) },
            el("span", { class: "org-role-name",
              text: "🧑‍💼 " + (m.role || "") + (m.agent_id ? " · " + m.agent_id : "") }),
            el("span", { class: "org-role-go", text: "💬" })),
          // #4:看它在本域的合并样子(原生范式 + 本域 value.md/deontic 准则,只读)
          el("button", { class: "org-role-view", title: t("domain.role_view_hint"), text: "👁",
            onclick: () => _openRoleInDomain(rid, d.id, d.name) })));
      }
      if (!shown) node.appendChild(el("div", { class: "org-empty", text: t("mgmt.org_no_role") }));
      tree.appendChild(node);
      (childrenOf[d.id] || []).forEach((c) => renderNode(c, depth + 1));   // 子域缩进嵌套
    };
    roots.forEach((d) => renderNode(d, 0));
    body.appendChild(tree);
  }
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("mgmt.existing") }));
  {
    const list = el("div", { class: "mgmt-list" });
    for (const d of doms) {
      const archived = d.lifecycle === "archived";
      const actions = el("div", { class: "dpref-actions" });
      if (archived) {
        actions.appendChild(el("button", { class: "dpref-confirm", text: t("domain.restore"),
          onclick: async () => {
            await _postJSON("/api/domain/restore", { domain_id: d.id });
            _deps.refreshPeers(); await renderDomainsPanel();
          } }));
      } else {
        // P0 审计:编辑价值观/成员(多行表单,不再单行 prompt)
        actions.appendChild(el("button", { class: "dpref-edit", text: t("dpref.edit"),
          onclick: () => _openDomainEdit(d) }));
        actions.appendChild(el("button", { class: "mc-del", text: t("domain.archive"),
          onclick: async () => {
            if (!window.confirm(t("domain.archive_confirm", { name: d.name }))) return;
            const res = await _postJSON("/api/domain/archive", { domain_id: d.id });
            if (res.ok) {
              _deps.pushChatLine("system", t("domain.archived", { name: d.name, n: res.data.purged_cognition || 0 }));
              _deps.refreshPeers(); await renderDomainsPanel();
            } else alert(res.data.reason || "archive failed");
          } }));
      }
      const badge = el("span", { class: "dpref-badge " + (archived ? "provisional" : "confirmed"),
        text: archived ? t("domain.archived_badge") : t("domain.active_badge") });
      list.appendChild(el("div", { class: "mgmt-card" },
        el("div", { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { text: d.name }), " ", badge,
            d.parent_id ? el("span", { class: "mc-meta", text: " ⊂ 子域" }) : null),
          el("div", { class: "mc-meta", text: d.id })),
        actions));
    }
    body.appendChild(list);
  }

  // 「＋ 新建业务域」入口:默认收起,点开才展开(模板 = 带配置新建 + 从零手写)。不再顶在列表上方误导。
  const newWrap = el("div", { class: "domtpl-new-wrap" });
  const newBtn = el("button", { class: "mgmt-inline-link domtpl-new-toggle", text: t("domain.new_entry") });
  const newBody = el("div", { class: "domtpl-new-body", style: "display:none" });
  let built = false;
  newBtn.addEventListener("click", async () => {
    const shown = newBody.style.display !== "none";
    if (shown) { newBody.style.display = "none"; newBtn.classList.remove("on"); return; }
    if (!built) {
      built = true;
      // 模板 = 新建的一种方式(带配置);先来一句框定它是"新建"不是"打开已有"
      const tplTitle = el("div", { class: "mgmt-section-title", text: t("domtpl.pick_title") });
      newBody.appendChild(tplTitle);
      newBody.appendChild(el("div", { class: "mgmt-hint", text: t("domtpl.hint") }));
      const hasTpl = await _renderDomainTemplates(newBody);
      if (!hasTpl) { tplTitle.remove(); }
      newBody.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.or_from_scratch") }));
      _renderCreateForm(newBody, roles, activeDoms);
    }
    newBody.style.display = "";
    newBtn.classList.add("on");
  });
  newWrap.appendChild(newBtn);
  newWrap.appendChild(newBody);
  body.appendChild(newWrap);
}

// #4:角色**在某业务域里**的只读合并视图 —— ①原生范式(去角色库改)②本域 value.md/deontic 准则(去域改)。
async function _openRoleInDomain(roleId: string, domainId: string, domainName: string): Promise<void> {
  openMgmtModal("👁 " + roleId + " @ " + domainName);
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const r = await _getJSON("/api/role/in_domain?role_id=" + encodeURIComponent(roleId) + "&domain_id=" + encodeURIComponent(domainId));
  if (!(r && r.ok)) { body.appendChild(el("div", { class: "mgmt-empty", text: (r && r.reason) || t("mgmt.failed", { err: "?" }) })); return; }
  const pm = r.paradigm || {};
  // ① 角色原生范式(只读)
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.native_paradigm") }));
  body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.native_hint") }));
  const slots: [string, string][] = [
    [t("role.identity_label"), pm.identity], [t("role.soul_label"), pm.soul], [t("role.user_label"), pm.user],
    [t("role.commitment_label"), pm.commitment], [t("role.verify_label"), pm.verify], [t("role.memory_label"), pm.memory],
  ];
  for (const [label, val] of slots) {
    body.appendChild(el("div", { class: "soul-slot" },
      el("label", { text: label }), el("div", { class: "soul-ro", text: (val || "—") })));
  }
  if ((pm.atom_ids || []).length || (pm.skill_ids || []).length) {
    body.appendChild(el("div", { class: "mc-meta" },
      ...(pm.atom_ids || []).map((a: string) => el("span", { class: "mc-tag", text: "🔧 " + a })),
      ...(pm.skill_ids || []).map((s: string) => el("span", { class: "mc-tag mc-tag-skill", text: "🧩 " + s }))));
  }
  // ② 本域继承来的行为准则(value.md + deontic;只读,去域编辑改)
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("domain.inherited_guideline", { d: domainName }) }));
  body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.inherited_hint") }));
  body.appendChild(el("div", { class: "soul-slot" },
    el("label", { text: t("domain.value_label") }),
    el("div", { class: "soul-ro", text: (r.value_md || t("domain.no_value")) })));
  const de = r.deontic || {};
  const deRow = (label: string, arr: string[], cls: string) => (arr && arr.length)
    ? el("div", { class: "soul-slot" }, el("label", { text: label }),
        el("div", { class: "deontic-list " + cls }, ...arr.map((x) => el("div", { class: "deontic-item", text: "・ " + x }))))
    : null;
  const fb = deRow(t("domain.deontic_forbid"), de.forbid, "forbid");
  const ob = deRow(t("domain.deontic_oblige"), de.oblige, "oblige");
  const pe = deRow(t("domain.deontic_permit"), de.permit, "permit");
  if (fb) body.appendChild(fb); if (ob) body.appendChild(ob); if (pe) body.appendChild(pe);
  if (!fb && !ob && !pe) body.appendChild(el("div", { class: "mgmt-hint", text: t("domain.deontic_none") }));
  body.appendChild(el("div", { class: "mgmt-row" },
    el("button", { class: "mgmt-inline-link", text: t("domain.back"), onclick: () => open() })));
}

async function open(deps?: Deps): Promise<void> {
  if (deps) _deps = deps;
  openMgmtModal(t("mgmt.domains_title"));
  await renderDomainsPanel();
}

const KarvyDomainsPanel = { open };
(window as unknown as { KarvyDomainsPanel: typeof KarvyDomainsPanel }).KarvyDomainsPanel = KarvyDomainsPanel;
export { KarvyDomainsPanel };
