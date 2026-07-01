/* agents_panel.ts — 🤖 外部 Agent 导入面板(从 app.js 抽出,大尾巴 slice)。
 * 按 KarvyLoop 范式导入外部 agent(generic-json/claude/codex/agent-bundle)→ 落角色库。
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

async function open(deps?: Deps): Promise<void> {
  if (deps) _deps = deps;
  openMgmtModal(t("mgmt.agents_title"));
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
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

const KarvyAgentsPanel = { open };
(window as unknown as { KarvyAgentsPanel: typeof KarvyAgentsPanel }).KarvyAgentsPanel = KarvyAgentsPanel;
export { KarvyAgentsPanel };
