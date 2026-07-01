/* schedules_panel.ts — ⏰ 定时任务面板(从 app.js 抽出,大尾巴 slice)。
 * 只有 Karvy 能起定时器(全系统定时器唯一审计面);NL 描述 → 解析预览 → 确认建 + 列表 暂停/立即跑/删。
 * 只用 dom/modal/i18n 全局,无跨面板耦合。暴露 window.KarvySchedulesPanel.open()。
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

function _fmtWhen(ts: number): string {
  if (!ts) return "—";
  try { return new Date(ts * 1000).toLocaleString(); } catch { return "—"; }
}

async function renderSchedulesPanel(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("sched.subtitle") }));
  // —— NL 创建:你说一句话 → 解析预览 → 确认创建 ——
  const mk = el("div", { class: "mgmt-buysugar" });
  mk.appendChild(el("div", { class: "mgmt-hint", text: t("sched.nl_hint") }));
  const inp = el("input", { class: "mgmt-input", type: "text", placeholder: t("sched.nl_ph") }) as HTMLInputElement;
  const preview = el("div", { class: "mgmt-hint" });
  const btns = el("div", { class: "dpref-actions" });
  let _parsed: Record<string, unknown> | null = null;
  const confirmBtn = el("button", { class: "dpref-confirm", text: t("sched.create"), disabled: true,
    onclick: async () => {
      if (!_parsed) return;
      const r = await _postJSON("/api/schedule/create", _parsed);
      if (r.ok && r.data && r.data.ok) {
        inp.value = ""; preview.textContent = ""; _parsed = null;
        (confirmBtn as HTMLButtonElement).disabled = true; renderSchedulesPanel();
      } else alert(t("sched.create_fail"));
    } });
  const parseBtn = el("button", { class: "dpref-edit", text: t("sched.parse"),
    onclick: async () => {
      const d = (inp.value || "").trim();
      if (!d) return;
      preview.textContent = t("sched.parsing");
      const r = await _postJSON("/api/schedule/parse", { description: d });
      if (r.ok && r.data && r.data.ok) {
        _parsed = { cron: r.data.cron, intent: r.data.intent, title: r.data.title,
                    target_role: r.data.target_role || "" };
        preview.textContent = t("sched.preview", { cron: r.data.cron, intent: r.data.intent,
          who: r.data.target_role || t("chat.karvy") });
        (confirmBtn as HTMLButtonElement).disabled = false;
      } else {
        _parsed = null; (confirmBtn as HTMLButtonElement).disabled = true;
        preview.textContent = (r.data && r.data.reason === "no_llm") ? t("sched.no_llm") : t("sched.not_understood");
      }
    } });
  btns.appendChild(parseBtn); btns.appendChild(confirmBtn);
  mk.appendChild(inp); mk.appendChild(btns); mk.appendChild(preview);
  body.appendChild(mk);
  // —— 任务列表 ——
  const data = await _getJSON("/api/schedules");
  const list = (data && data.schedules) || [];
  if (!list.length) { body.appendChild(el("div", { class: "mgmt-empty", text: t("sched.empty") })); return; }
  const wrap = el("div", { class: "mgmt-list" });
  for (const s of list) {
    const stBadge = s.last_status === "error"
      ? el("span", { class: "dpref-badge provisional", title: s.last_error || "", text: "⚠ " + t("sched.err") })
      : (s.last_status === "ok" ? el("span", { class: "dpref-badge confirmed", text: "✓ " + t("sched.ok") }) : null);
    const actions = el("div", { class: "dpref-actions" });
    actions.appendChild(el("button", { class: "dpref-edit", text: s.enabled ? t("sched.pause") : t("sched.resume"),
      onclick: async () => { await _postJSON("/api/schedule/toggle", { id: s.id, enabled: !s.enabled }); renderSchedulesPanel(); } }));
    actions.appendChild(el("button", { class: "dpref-edit", text: t("sched.run_now"),
      onclick: async () => { await _postJSON("/api/schedule/run_now", { id: s.id }); renderSchedulesPanel(); } }));
    actions.appendChild(el("button", { class: "files-act files-del", text: t("sched.delete"),
      onclick: async () => {
        if (!window.confirm(t("sched.del_confirm", { name: s.title }))) return;
        await _postJSON("/api/schedule/delete", { id: s.id }); renderSchedulesPanel();
      } }));
    wrap.appendChild(el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" }, el("span", { text: "⏰ " + (s.title || s.intent) }),
          s.enabled ? null : " ", s.enabled ? null : el("span", { class: "dpref-badge provisional", text: t("sched.paused") }),
          stBadge ? " " : null, stBadge),
        el("div", { class: "mc-meta", text: t("sched.line", { cron: s.cron, who: s.target || t("chat.karvy") }) }),
        el("div", { class: "mc-meta", text: t("sched.next", { when: _fmtWhen(s.next_run) }) })),
      actions));
  }
  body.appendChild(wrap);
}

async function open(): Promise<void> {
  openMgmtModal(t("sched.title")); await renderSchedulesPanel();
}

const KarvySchedulesPanel = { open };
(window as unknown as { KarvySchedulesPanel: typeof KarvySchedulesPanel }).KarvySchedulesPanel = KarvySchedulesPanel;
export { KarvySchedulesPanel };
