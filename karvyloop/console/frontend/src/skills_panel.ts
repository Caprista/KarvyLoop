/* skills_panel.ts — 🧩 技能库面板(从 app.js 抽出,大尾巴 slice)。
 * Agent Skills 开放标准:导入(官方仓库/市场/本地)+ 目录浏览 + 可配置检索源 + 内建 Coding 能力卡
 * + 技能列表(生命周期徽章/第三方徽章)+ 详情(沙箱试跑 + 按需授网 + markdown body)。
 * 整簇自洽,只用 dom/modal/i18n 全局 + window.KarvyRender(渲染 body,点详情时才用)。无 app.js-local 耦合。
 * 暴露 window.KarvySkillsPanel.open()。
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

// 导入第三方技能(Agent Skills 开放标准:官方仓库 / 市场 / 本地)——加入大家都在用的生态
function _skillImportForm(): HTMLElement {
  const srcIn = el("input", { type: "text", placeholder: t("skills.import_ph") }) as HTMLInputElement;
  srcIn.style.flex = "1";
  const msg = _formMsg();
  const btn = el("button", { class: "mgmt-inline-link", text: t("skills.import_btn"),
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
        _setMsg(msg, false, t("mgmt.failed", { err: (res.data && (res.data.reason || res.data.detail)) || res.status }));
      }
    } });
  return el("div", { class: "mgmt-buysugar" },
    el("div", { class: "mgmt-hint", text: t("skills.import_hint") }),
    el("div", { class: "mgmt-row" }, srcIn, btn), msg,
    _skillCatalog());
}

// 目录浏览(P1-b):官方仓库 + 市场搜索 → 一键导(不用知道 GitHub 路径)
function _skillCatalog(): HTMLElement {
  const qIn = el("input", { type: "text", placeholder: t("skills.catalog_ph") }) as HTMLInputElement;
  qIn.style.flex = "1";
  const srcSel = el("select", null,
    el("option", { value: "all", text: t("skills.cat_all") }),
    el("option", { value: "official", text: t("skills.cat_official") }),
    el("option", { value: "market", text: t("skills.cat_market") })) as HTMLSelectElement;
  const results = el("div", { class: "skill-catalog" });
  const search = async () => {
    results.textContent = t("skills.catalog_loading");
    const r = await _getJSON("/api/skill/catalog?source=" + encodeURIComponent(srcSel.value) +
      "&q=" + encodeURIComponent(qIn.value.trim()));
    const entries = (r && r.entries) || [];
    results.innerHTML = "";
    if (!entries.length) { results.appendChild(el("div", { class: "mgmt-hint", text: t("skills.catalog_empty") })); return; }
    for (const e of entries) {
      const tag = el("span", { class: "mc-tag" + (e.origin === "official" ? "" : " mc-tag-skill"),
        text: (e.origin === "official" ? "✓ " : "🌐 ") + e.origin + (e.stars ? " ★" + e.stars : "") });
      const imp = el("button", { class: "mgmt-inline-link", text: t("skills.catalog_import"),
        onclick: async () => {
          imp.textContent = t("skills.importing");
          const res = await _postJSON("/api/skill/import", { source: e.source, kind: "github" });
          if (res.ok && res.data && res.data.ok) { await renderSkillsPanel(); }
          else { imp.textContent = t("mgmt.failed", { err: (res.data && (res.data.reason || res.data.detail)) || res.status }); }
        } });
      results.appendChild(el("div", { class: "skill-cat-row" },
        el("div", { class: "mc-main" },
          el("div", { class: "mc-name" }, el("span", { text: "🧩 " + e.name }), " ", tag,
            e.author ? el("span", { class: "mc-meta", text: " · " + e.author }) : null),
          e.description ? el("div", { class: "mc-meta", text: e.description }) : null),
        imp));
    }
  };
  const goBtn = el("button", { class: "mgmt-inline-link", text: t("skills.catalog_btn"), onclick: search });
  return el("div", { class: "skill-catalog-wrap" },
    el("div", { class: "mgmt-hint", text: t("skills.catalog_hint") }),
    el("div", { class: "mgmt-row" }, qIn, srcSel, goBtn), results,
    _skillSourcesManager());
}

// btw-2:可配置检索源(增删改 + 开关;≥1 开才能存)。折叠,默认收起免干扰。
function _skillSourcesManager(): HTMLElement {
  const wrap = el("div", { class: "skill-sources-wrap" });
  const panel = el("div", { class: "skill-sources hidden" });
  const toggle = el("button", { class: "mgmt-inline-link", text: "⚙ " + t("skills.src_manage"),
    onclick: async () => {
      panel.classList.toggle("hidden");
      if (!panel.classList.contains("hidden")) await render();
    } });
  const msg = _formMsg();

  async function render() {
    panel.innerHTML = "";
    const data = await _getJSON("/api/skill/sources");
    if (data && data.no_llm) { panel.appendChild(el("div", { class: "mgmt-hint", text: t("skills.no_llm") })); return; }
    const rows: Array<{ src: any; enabled: HTMLInputElement; label: HTMLInputElement; repo: HTMLInputElement }> = [];
    const list = el("div", {});
    function addRow(src: any): void {
      const enabled = el("input", { type: "checkbox" }) as HTMLInputElement; enabled.checked = src.enabled !== false;
      const label = el("input", { type: "text" }) as HTMLInputElement; label.value = src.label || src.id; label.style.flex = "1";
      const repo = el("input", { type: "text", placeholder: "owner/repo" }) as HTMLInputElement; repo.value = src.repo || "";
      repo.style.display = (src.type === "github") ? "" : "none";
      const del = el("button", { class: "mgmt-inline-link", text: "✕",
        onclick: () => { rows.splice(rows.indexOf(rec), 1); row.remove(); } });
      const row = el("div", { class: "mgmt-row skill-src-row" }, enabled,
        el("span", { class: "mc-tag", text: src.type }), label, repo, del);
      const rec = { src, enabled, label, repo };
      rows.push(rec); list.appendChild(row);
    }
    for (const s of (data && data.sources) || []) addRow(s);
    panel.appendChild(list);
    // 加源(github)
    const newId = el("input", { type: "text", placeholder: "id" }) as HTMLInputElement;
    const newRepo = el("input", { type: "text", placeholder: "owner/repo" }) as HTMLInputElement;
    const addBtn = el("button", { class: "mgmt-inline-link", text: "+ " + t("skills.src_add_github"),
      onclick: () => { const id = newId.value.trim(); const r = newRepo.value.trim();
        if (!id || !r) return; addRow({ id: id, label: id, type: "github", repo: r, root: "skills", ref: "main", enabled: true });
        newId.value = ""; newRepo.value = ""; } });
    panel.appendChild(el("div", { class: "mgmt-row" }, newId, newRepo, addBtn));
    // 存(整表)
    const save = el("button", { class: "mgmt-submit", text: t("skills.src_save"),
      onclick: async () => {
        const payload = rows.map((rec) => Object.assign({}, rec.src,
          { enabled: rec.enabled.checked, label: rec.label.value.trim() || rec.src.id,
            repo: rec.src.type === "github" ? (rec.repo.value.trim() || rec.src.repo) : undefined }));
        const res = await _postJSON("/api/skill/sources", { sources: payload });
        if (res.ok && res.data && res.data.ok) _setMsg(msg, true, t("skills.src_saved"));
        else _setMsg(msg, false, (res.data && res.data.reason) || t("mgmt.failed", { err: res.status }));
      } });
    panel.appendChild(el("div", { class: "mgmt-row" }, save));
    panel.appendChild(msg);
  }
  wrap.appendChild(toggle); wrap.appendChild(panel);
  return wrap;
}

async function renderSkillsPanel(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("skills.subtitle") }));
  const data = await _getJSON("/api/skills");
  if (data && data.no_llm) { body.appendChild(el("div", { class: "mgmt-empty", text: t("skills.no_llm") })); return; }
  await _renderCodingCapability(body);    // #1:内建「Coding」技能 —— 编码能力露在技能库里
  _renderCapabilityOverviewCard(body);    // P3-d:能力合一清单 —— 工具下限 + 技能授予一张表
  body.appendChild(_skillImportForm());   // 导入入口常驻顶部(空库时也能先导)
  const skills = (data && data.skills) || [];
  if (!skills.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("skills.empty") })); return; }
  const list = el("div", { class: "mgmt-list" });
  for (const s of skills) {
    const archived = !!s.archived;
    const badge = el("span", { class: "dpref-badge " + (archived ? "provisional" : "confirmed"),
      text: archived ? t("skills.archived_badge") : t("skills.active_badge") });
    // btw-1:生命周期状态徽章(待沉淀/待验证/已沉淀)
    const st = s.status || "pending";
    const stCls = st === "crystallized" ? "confirmed" : (st === "unverified" ? "provisional" : "provisional");
    const stBadge = el("span", { class: "dpref-badge " + stCls, text: t("skills.status_" + st) });
    // 第三方导入的技能:🌐 来源徽章(untrusted → 提示执行走沙箱)
    const tpBadge = s.third_party
      ? el("span", { class: "dpref-badge provisional", title: t("skills.untrusted_hint"),
          text: "🌐 " + t("skills.third_party_badge") })
      : null;
    const stats = t("skills.stats", { recall: s.recall_count || 0, use: s.usage_count || 0, ok: s.success_count || 0 });
    const actions = el("div", { class: "dpref-actions" });
    if (archived) {
      actions.appendChild(el("button", { class: "dpref-confirm", text: t("skills.restore"),
        onclick: async () => { await _postJSON("/api/skill/restore", { sig: s.sig }); await renderSkillsPanel(); } }));
    }
    actions.appendChild(el("button", { class: "dpref-edit", text: t("skills.view"),
      onclick: () => _openSkillDetail(s) }));
    list.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "🧩 " + s.name }), " ", stBadge,
          " ", badge, tpBadge ? " " : null, tpBadge),
        el("div", { class: "mc-meta", text: s.when_to_use || s.description || "" }),
        el("div", { class: "mc-meta", text: stats })),
      actions));
  }
  body.appendChild(list);
}

// #1:内建「Coding」技能卡 —— 把编码能力当一个技能库里看得见、(执行器)可配置的技能露出。
// tools 反映真实装上的工具(内建 + MCP),executor 如实标(Forge 内建沙箱 / 外接=绕沙箱)。
async function _renderCodingCapability(body: HTMLElement): Promise<void> {
  const cap = await _getJSON("/api/coding/capability");
  if (!cap || !cap.tools) return;
  const builtinBadge = el("span", { class: "dpref-badge confirmed", text: t("coding.builtin_badge") });
  // 实际执行器恒 Forge(沙箱内);外接命令是"已存未接入"的偏好,不影响实跑(诚实)
  const execBadge = el("span", { class: "dpref-badge confirmed", text: t("coding.exec_forge") });
  const sbBadge = el("span", { class: "dpref-badge confirmed", title: t("coding.sandboxed_hint"),
    text: "🛡 " + t("coding.sandboxed") });
  // 配了外接 coder → 多一枚"已存·实验性"徽章(明示尚未接入执行)
  const extBadge = cap.external_executor
    ? el("span", { class: "dpref-badge provisional", title: t("coding.unsandboxed_hint"),
        text: "⚙ " + t("coding.ext_saved_badge") })
    : null;
  const actions = el("div", { class: "dpref-actions" });
  actions.appendChild(el("button", { class: "dpref-edit", text: t("skills.view"),
    onclick: () => _openCodingDetail(cap) }));
  body.appendChild(el("div", { class: "mgmt-list" },
    el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "🛠 " + t("coding.name") }), " ",
          builtinBadge, " ", execBadge, " ", sbBadge, extBadge ? " " : null, extBadge),
        el("div", { class: "mc-meta", text: t("coding.subtitle") }),
        el("div", { class: "mc-meta", text: t("coding.tool_count", { n: cap.tools.length }) })),
      actions)));
}

function _openCodingDetail(cap: any): void {
  openMgmtModal(t("coding.name")); const b = mgmtBody(); if (!b) return; b.innerHTML = "";
  b.appendChild(el("div", { class: "mgmt-section-title", text: t("coding.detail_title") }));
  // 执行器一行:如实说明 —— 实跑永远是 Forge(内建沙箱)
  b.appendChild(el("div", { class: "mgmt-hint", text: t("coding.exec_line_forge") }));
  // #3:外接编码工具(可编辑)—— 高级用户填自己的 coder(如外部编码 CLI)。
  // 诚实:v1.0 只**存偏好**,不接入执行(还是 Forge 跑),所以明示"实验性·尚未接入"。
  const editWrap = el("div", { class: "mgmt-buysugar" });
  editWrap.appendChild(el("div", { class: "mgmt-section-title", text: t("coding.ext_title") }));
  editWrap.appendChild(el("div", { class: "mgmt-hint", text: t("coding.pluggable_note") }));
  const inp = el("input", { class: "mgmt-input", type: "text",
    placeholder: t("coding.ext_placeholder"), value: cap.external_executor || "" }) as HTMLInputElement;
  const status = el("div", { class: "mgmt-hint" });
  const _setStatus = () => {
    status.textContent = (inp.value || "").trim()
      ? t("coding.ext_saved_note", { cmd: (inp.value || "").trim() })
      : t("coding.ext_none_note");
  };
  _setStatus();
  const save = el("button", { class: "dpref-confirm", text: t("coding.ext_save"),
    onclick: async () => {
      const r = await _postJSON("/api/coding/config", { external_executor: (inp.value || "").trim() });
      if (r.ok && r.data && r.data.ok) { cap.external_executor = r.data.external_executor; _setStatus(); }
      else alert(t("coding.ext_save_fail"));
    } });
  const clear = el("button", { class: "dpref-edit", text: t("coding.ext_clear"),
    onclick: async () => {
      inp.value = "";
      const r = await _postJSON("/api/coding/config", { external_executor: "" });
      if (r.ok && r.data && r.data.ok) { cap.external_executor = null; _setStatus(); }
    } });
  editWrap.appendChild(inp);
  editWrap.appendChild(el("div", { class: "dpref-actions" }, save, clear));
  editWrap.appendChild(status);
  b.appendChild(editWrap);
  // 工具清单:内建 + MCP,各列名 + 描述(真实反映装上的能力)
  const list = el("div", { class: "mgmt-list" });
  for (const tl of cap.tools) {
    const kindBadge = el("span", { class: "dpref-badge " + (tl.kind === "mcp" ? "provisional" : "confirmed"),
      text: tl.kind === "mcp" ? "MCP" : t("coding.builtin_badge") });
    list.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "· " + tl.name }), " ", kindBadge),
        el("div", { class: "mc-meta", text: (tl.description || "").slice(0, 200) }))));
  }
  b.appendChild(list);
  // #42 优化:渠道预设 —— 一键接入知名 MCP server(拧开就有水)
  b.appendChild(_mcpPresetsSection());
}

// #42 优化:MCP 渠道预设区 —— 知名 server(文件/抓网页/GitHub/记忆/时间/SQLite)一键写进
// config.yaml。诚实:server 只在 console 启动时连接(无热加载)→ 接入后明示"要重启"。
function _mcpPresetsSection(): HTMLElement {
  const wrap = el("div", { class: "mgmt-buysugar" });
  wrap.appendChild(el("div", { class: "mgmt-section-title", text: t("mcpp.title") }));
  wrap.appendChild(el("div", { class: "mgmt-hint", text: t("mcpp.hint") }));
  const list = el("div", { class: "mgmt-list" });
  wrap.appendChild(list);
  (async () => {
    const data = await _getJSON("/api/mcp/presets");
    const presets = (data && data.presets) || [];
    if (!presets.length) { list.appendChild(el("div", { class: "mgmt-empty", text: t("mcpp.empty") })); return; }
    for (const p of presets) list.appendChild(_mcpPresetRow(p));
  })();
  return wrap;
}

function _mcpPresetRow(p: any): HTMLElement {
  const msg = el("div", { class: "mgmt-hint" });
  // 参数输入(有才显):folder 之类明文;token 之类走 password,值只发一次、绝不回显
  const inputs: Array<{ key: string; input: HTMLInputElement }> = [];
  const paramRow = el("div", { class: "mgmt-row" });
  for (const prm of p.params || []) {
    const ph = prm.secret ? (p.secret_hint || prm.key)
      : (prm.default_resolved ? t("mcpp.param_default_ph", { key: prm.key, def: prm.default_resolved }) : prm.key);
    const input = el("input", { type: prm.secret ? "password" : "text", placeholder: ph }) as HTMLInputElement;
    input.style.flex = "1";
    inputs.push({ key: prm.key, input });
    paramRow.appendChild(input);
  }
  const btn = el("button", { class: "dpref-confirm", text: p.configured ? t("mcpp.update") : t("mcpp.connect"),
    onclick: async () => {
      const params: Record<string, string> = {};
      for (const rec of inputs) { const v = rec.input.value.trim(); if (v) params[rec.key] = v; }
      (btn as HTMLButtonElement).disabled = true; btn.textContent = t("mcpp.applying");
      const r = await _postJSON("/api/mcp/preset/apply", { preset_id: p.id, params: params });
      if (r.ok && r.data && r.data.ok) {
        btn.textContent = t("mcpp.connected");
        msg.textContent = t("mcpp.restart_note");   // 诚实:启动时才连,要重启才装上
      } else {
        (btn as HTMLButtonElement).disabled = false;
        btn.textContent = p.configured ? t("mcpp.update") : t("mcpp.connect");
        msg.textContent = t("mgmt.failed", { err: (r.data && (r.data.reason || r.data.detail)) || r.status });
      }
    } });
  const badges: (HTMLElement | string | null)[] = [el("span", { text: "🔌 " + p.name })];
  if (p.configured) { badges.push(" "); badges.push(el("span", { class: "dpref-badge confirmed", text: t("mcpp.connected") })); }
  if (p.needs_secret) { badges.push(" "); badges.push(el("span", { class: "dpref-badge provisional", text: "🔑 " + t("mcpp.needs_secret") })); }
  return el("div", { class: "mgmt-card" },
    el("div", { class: "mc-main" },
      el("div", { class: "mc-name" }, ...badges),
      el("div", { class: "mc-meta", text: p.description || "" }),
      p.risk_note ? el("div", { class: "mc-meta", text: "⚠ " + p.risk_note }) : null,
      inputs.length ? paramRow : null,
      el("div", { class: "dpref-actions" }, btn),
      msg));
}

// P3-d:能力合一清单 —— 此前工具能力(capability 决策链)和技能授予(grants/锁)两套账,
// 审计"谁能干什么"要拼两处。一张表:工具×模式下限 + 技能×信任/联网/完整性锁。
function _renderCapabilityOverviewCard(body: HTMLElement): void {
  const actions = el("div", { class: "dpref-actions" });
  actions.appendChild(el("button", { class: "dpref-edit", text: t("skills.view"),
    onclick: () => _openCapabilityOverview() }));
  body.appendChild(el("div", { class: "mgmt-list" },
    el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "🔐 " + t("capov.name") })),
        el("div", { class: "mc-meta", text: t("capov.subtitle") })),
      actions)));
}

async function _openCapabilityOverview(): Promise<void> {
  openMgmtModal(t("capov.name")); const b = mgmtBody(); if (!b) return; b.innerHTML = "";
  const ov = await _getJSON("/api/capability/overview");
  if (!ov) { b.appendChild(el("div", { class: "mgmt-empty", text: t("mgmt.failed", { err: "" }) })); return; }
  // 工具 × 模式下限(不在表里 = FULL 最严,fail-closed)
  b.appendChild(el("div", { class: "mgmt-section-title", text: t("capov.tools_title") }));
  b.appendChild(el("div", { class: "mgmt-hint", text: t("capov.tools_hint") }));
  const tl = el("div", { class: "mgmt-list" });
  for (const t_ of (ov.tools || [])) {
    const mode = (t_.required_mode || "full");
    const modeCls = mode === "read_only" ? "confirmed" : (mode === "workspace_write" ? "provisional" : "");
    tl.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "· " + t_.name }), " ",
          el("span", { class: "dpref-badge " + modeCls, text: t("capov.mode_" + mode) }),
          t_.kind === "mcp" ? " " : null,
          t_.kind === "mcp" ? el("span", { class: "dpref-badge provisional", text: "MCP" }) : null))));
  }
  b.appendChild(tl);
  // 技能 × 信任级/联网/完整性锁
  b.appendChild(el("div", { class: "mgmt-section-title", text: t("capov.skills_title") }));
  b.appendChild(el("div", { class: "mgmt-hint", text: t("capov.skills_hint") }));
  const sl = el("div", { class: "mgmt-list" });
  const skl = ov.skills || [];
  if (!skl.length) sl.appendChild(el("div", { class: "mgmt-empty", text: t("skills.empty") }));
  for (const s of skl) {
    const trustBadge = el("span", { class: "dpref-badge " + (s.trust === "trusted" ? "confirmed" : "provisional"),
      text: t("capov.trust_" + s.trust) });
    const bits: (HTMLElement | string | null)[] = [el("span", { text: "🧩 " + s.name }), " ", trustBadge];
    if (s.net_granted) { bits.push(" "); bits.push(el("span", { class: "dpref-badge provisional", text: "🌐 " + t("capov.net_on") })); }
    if (s.lock) {
      const lockCls = s.lock === "ok" ? "confirmed" : "provisional";
      bits.push(" "); bits.push(el("span", { class: "dpref-badge " + lockCls, text: "🔒 " + t("capov.lock_" + s.lock) }));
    }
    sl.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, ...bits),
        el("div", { class: "mc-meta", text: (s.has_scripts ? t("capov.has_scripts") : t("capov.no_scripts")) }))));
  }
  b.appendChild(sl);
  // fs_grants:工作区外路径授权(台账可见可撤 + 手动放行;敏感路径硬地板由后端拒)
  b.appendChild(el("div", { class: "mgmt-section-title", text: t("capov.grants_title") }));
  b.appendChild(el("div", { class: "mgmt-hint", text: t("capov.grants_hint") }));
  const gl = el("div", { class: "mgmt-list" });
  const grants = ov.fs_grants || [];
  if (!grants.length) gl.appendChild(el("div", { class: "mgmt-empty", text: t("capov.grants_empty") }));
  for (const g of grants) {
    const opsBadge = el("span", { class: "dpref-badge " + (g.ops && g.ops.includes("write") ? "provisional" : "confirmed"),
      text: (g.ops || ["read"]).join("/") });
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", { class: "dpref-edit", text: t("capov.grant_revoke"),
      onclick: async () => {
        const r = await _postJSON("/api/fs_grants/revoke", { grant_id: g.id });
        if (r.ok && r.data && r.data.ok) _openCapabilityOverview();
      } }));
    gl.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "📂 " + g.path }), " ", opsBadge),
        el("div", { class: "mc-meta", text: (g.role ? t("capov.grant_role", { role: g.role }) + " · " : "") + (g.origin || "") })),
      actions));
  }
  b.appendChild(gl);
  // 手动放行一条路径
  const addWrap = el("div", { class: "mgmt-buysugar" });
  const pathIn = el("input", { class: "mgmt-input", type: "text",
    placeholder: t("capov.grant_path_ph") }) as HTMLInputElement;
  const writeChk = el("input", { type: "checkbox" }) as HTMLInputElement;
  const addMsg = el("div", { class: "mgmt-hint" });
  addWrap.appendChild(pathIn);
  addWrap.appendChild(el("label", {}, writeChk, el("span", { text: " " + t("capov.grant_write") })));
  addWrap.appendChild(el("button", { class: "dpref-confirm", text: t("capov.grant_add"),
    onclick: async () => {
      const ops = writeChk.checked ? ["read", "write"] : ["read"];
      const r = await _postJSON("/api/fs_grants", { path: (pathIn.value || "").trim(), ops: ops });
      if (r.ok && r.data && r.data.ok) _openCapabilityOverview();
      else addMsg.textContent = (r.data && r.data.reason) ? tB(r.data.reason) : "?";
    } }));
  addWrap.appendChild(addMsg);
  b.appendChild(addWrap);
}

// 技能详情 + 沙箱试跑(P0-c:让第三方脚本在笼子里跑给你看)
function _openSkillDetail(s: any): void {
  openMgmtModal(s.name); const b = mgmtBody(); if (!b) return; b.innerHTML = "";
  b.appendChild(el("div", { class: "mgmt-section-title", text: t("skills.when", { w: s.when_to_use || "—" }) }));
  // 携带脚本 → 沙箱试跑区(token 由信任级派生;第三方=最小授予无网络)
  const scripts = s.scripts || [];
  if (scripts.length) {
    const runWrap = el("div", { class: "mgmt-buysugar" });
    runWrap.appendChild(el("div", { class: "mgmt-hint",
      text: (s.untrusted ? t("skills.run_hint_untrusted") : t("skills.run_hint")) }));
    // P1:第三方按需授网 —— 用户显式勾选才放网络(默认拒;授权是人的决定)
    let netGranted = !!s.net_granted;
    const netChk = el("input", { type: "checkbox" }) as HTMLInputElement;
    netChk.checked = netGranted;
    netChk.addEventListener("change", async () => {
      const res = await _postJSON("/api/skill/grant", { name: s.name, net: netChk.checked });
      if (res.ok && res.data && res.data.ok) netGranted = netChk.checked;
      else netChk.checked = netGranted;  // 失败回滚
    });
    const netLabel = el("label", { class: "skill-net-grant" }, netChk,
      el("span", { text: " " + t("skills.grant_net") }));
    runWrap.appendChild(netLabel);
    const out = el("pre", { class: "skill-run-out" });
    for (const sc of scripts) {
      const btn = el("button", { class: "mgmt-inline-link", text: "▶ " + sc,
        onclick: async () => {
          out.textContent = t("skills.running");
          const res = await _postJSON("/api/skill/run", { name: s.name, script: sc, args: [] });
          const d = res.data || {};
          if (d.ok || typeof d.exit_code === "number") {
            out.textContent = "exit=" + d.exit_code + "\n" + (d.stdout || "") +
              (d.stderr ? "\n[stderr]\n" + d.stderr : "");
            // btw-1:跑通把外部技能升「已沉淀」→ 提示 + 刷新状态徽章
            if (d.promoted) out.textContent = t("skills.promoted") + "\n" + out.textContent;
          } else {
            out.textContent = t("mgmt.failed", { err: d.reason || res.status });
          }
        } });
      runWrap.appendChild(el("div", { class: "mgmt-row" }, btn));
    }
    runWrap.appendChild(out);
    b.appendChild(runWrap);
  }
  const _render = (window as unknown as { KarvyRender?: { appendMarkdown: (el: HTMLElement, md: string) => void } }).KarvyRender;
  if (_render) _render.appendMarkdown(b, s.body || s.description || "(空)");
  else b.appendChild(el("pre", { text: s.body || s.description || "" }));
  b.appendChild(el("button", { class: "mgmt-submit", text: t("skills.back"), onclick: () => open() }));
}

async function open(): Promise<void> {
  openMgmtModal(t("skills.title")); await renderSkillsPanel();
}

const KarvySkillsPanel = { open };
(window as unknown as { KarvySkillsPanel: typeof KarvySkillsPanel }).KarvySkillsPanel = KarvySkillsPanel;
export { KarvySkillsPanel };
