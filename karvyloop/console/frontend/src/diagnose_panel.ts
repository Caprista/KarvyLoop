/* diagnose_panel.ts — 🩺 诊断/运维面板(L1 自愈;从 app.js 抽出,大尾巴 slice)。
 * 跑确定性自检 + LLM 把问题翻人话 + 一键升成「待拍板」决策卡(只提议不执行,ACCEPT 只跑可逆修复)。
 *
 * 跨面板耦合(诚实标注):promote 要回写聊天 + 刷待拍板列,这两个还在 app.js → 经 open(deps) 注入,
 * 不偷偷上 window。renderOpsDiagnosis 是纯渲染(只用 el/t),onSystemError 也复用它 → 一并暴露。
 * 暴露 window.KarvyDiagnosePanel.{ open, renderOpsDiagnosis }。
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
  closeMgmtModal: () => void;
  mgmtBody: () => HTMLElement | null;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
// app.js 还没抽走的两个依赖,经 open() 注入(不上 window)
interface Deps {
  pushChatLine: (kind: string, text: string) => void;
  fetchPendingProposals: () => void;
}

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody, closeMgmtModal = _KM.closeMgmtModal;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

let _deps: Deps = { pushChatLine: () => {}, fetchPendingProposals: () => {} };

// 诊断卡(只读):人话问题 + 原因 + 分步修法 + 风险标(reversible/需批准)。LLM 只提议,不执行。
function renderOpsDiagnosis(log: HTMLElement, x: any): void {
  const box = el("div", { class: "ops-diag" });
  box.appendChild(el("div", { class: "ops-diag-head", text: t("ops.head") }));
  box.appendChild(el("div", { class: "ops-diag-summary", text: x.summary || "" }));
  if (x.cause) box.appendChild(el("div", {},
    el("span", { class: "ops-k", text: t("ops.cause_label") + ": " }), el("span", { text: x.cause })));
  if (x.fix) box.appendChild(el("div", { class: "ops-fix" },
    el("span", { class: "ops-k", text: t("ops.fix_label") + ": " }), el("span", { text: x.fix })));
  box.appendChild(el("div", { class: "ops-risk ops-risk-" + (x.risk || "needs_approval") },
    t("ops.risk_label") + ": " + t("ops.risk_" + (x.risk || "needs_approval"))));
  log.appendChild(box); log.scrollTop = log.scrollHeight;
}

// 系统健康卡(doctor 环的可视面):调 /api/health 显 overall + 逐条 finding。
// 后端 finding 是 {level, code, params, fixable};逐条走 doctor.msg.<code> i18n 渲染人话。
// fixable=auto/confirm 的项给徽标 + 「一键修/确认修」按钮(POST /api/doctor/fix)——
// docs/56 ②:后端 doctor 自愈有了但 UI 里够不着,补 UI 触发。confirm(危险,重写 config)前端二次确认。
const _ICON: Record<string, string> = { ok: "✓", warn: "⚠", fail: "✗" };

// 一键跑 doctor 自愈。confirm=true → 一并修危险项(需已二次确认)。修完刷新健康卡。
async function _runDoctorFix(confirm: boolean, host: HTMLElement): Promise<void> {
  const r = await _postJSON("/api/doctor/fix", { confirm });
  if (r.ok && r.data && r.data.ok) {
    _deps.pushChatLine("system", t("health.fix_done", {
      n: (r.data.repaired || []).length,
      before: t("health.overall." + (r.data.overall_before || "ok")),
      after: t("health.overall." + (r.data.overall_after || "ok")) }));
    await renderHealthCard(host, true);   // 修完重画健康卡(状态即时刷新)
  } else {
    alert(t("health.fix_failed"));
  }
}

// rerender=true → 清掉已有健康卡再画(fix 后刷新用)。
async function renderHealthCard(body: HTMLElement, rerender = false): Promise<void> {
  if (rerender) { const old = body.querySelector(".health-card"); if (old) old.remove(); }
  const card = el("div", { class: "health-card" });
  card.appendChild(el("div", { class: "mgmt-section-title", text: t("health.title") }));
  const loading = el("div", { class: "diag-status", text: t("health.running") });
  card.appendChild(loading);
  // 刷新时插回原位(健康卡本是 body 第一张);首次直接 append。
  if (rerender && body.firstChild) body.insertBefore(card, body.firstChild);
  else body.appendChild(card);
  const h: any = await _getJSON("/api/health?online=true");
  loading.remove();
  if (!h || !h.overall) { card.appendChild(el("div", { class: "mgmt-empty", text: t("health.failed") })); return; }
  card.appendChild(el("div", { class: "health-overall health-overall-" + h.overall,
    text: t("health.overall." + h.overall) }));
  const findings: any[] = Array.isArray(h.findings) ? h.findings : [];
  let anyAuto = false, anyConfirm = false;
  for (const f of findings) {
    const row = el("div", { class: "health-row health-row-" + (f.level || "ok") });
    row.appendChild(el("span", { class: "health-icon", text: (_ICON[f.level] || "·") + " " }));
    row.appendChild(el("span", { class: "health-msg",
      text: t("doctor.msg." + f.code, f.params || {}) }));
    if (f.fixable === "auto" || f.fixable === "confirm") {
      if (f.fixable === "auto") anyAuto = true; else anyConfirm = true;
      row.appendChild(el("span", { class: "health-fixable health-fixable-" + f.fixable,
        text: " · " + t("health.fixable_" + f.fixable) }));
    }
    card.appendChild(row);
  }
  // 有可修项 → 「一键修」按钮(auto 那批;confirm 危险项另给「确认修」并二次确认)。
  if (anyAuto || anyConfirm) {
    const actions = el("div", { class: "health-fix-actions" });
    if (anyAuto) {
      const fixBtn = el("button", { class: "mgmt-submit", text: t("health.fix_auto"),
        onClick: async () => { (fixBtn as HTMLButtonElement).disabled = true; await _runDoctorFix(false, body); } });
      actions.appendChild(fixBtn);
    }
    if (anyConfirm) {
      // 危险项(重写 config):前端**二次确认**,同意才带 confirm=true 调后端。
      const confirmBtn = el("button", { class: "mgmt-submit health-fix-danger", text: t("health.fix_confirm"),
        onClick: async () => {
          if (!window.confirm(t("health.fix_confirm_prompt"))) return;
          (confirmBtn as HTMLButtonElement).disabled = true;
          await _runDoctorFix(true, body);
        } });
      actions.appendChild(confirmBtn);
    }
    card.appendChild(actions);
    // 保留 CLI 提示作补充(有些环境更愿在终端修)。
    card.appendChild(el("div", { class: "health-fix-hint", text: t("health.fix_hint") }));
  }
}

async function renderDiagnosePanel(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  await renderHealthCard(body);   // doctor 环:系统健康卡先行(确定性 + 活性)
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("diag.title") }));
  const status = el("div", { class: "diag-status", text: t("diag.running") });
  body.appendChild(status);
  const d = await _getJSON("/api/ops/diagnose");
  status.remove();
  if (!d) { body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.failed") })); }
  else if (d.healthy) {
    body.appendChild(el("div", { class: "diag-ok", text: "✓ " + t("ops.healthy") }));
  } else if (d.reason === "no_model") {
    body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.no_model") }));
  } else if (d.diagnosis) {
    renderOpsDiagnosis(body, d.diagnosis);
    // 一键把诊断升成「待拍板」决策卡(ACCEPT 只跑确定性可逆修复,LLM 文本绝不执行)
    const promote = el("button", { class: "mgmt-submit", text: t("diag.promote"),
      onClick: async () => {
        (promote as HTMLButtonElement).disabled = true;
        const r = await _postJSON("/api/ops/propose_fix", {});
        if (r.ok && r.data && r.data.proposal_id) {
          _deps.pushChatLine("system", t("diag.promoted"));
          _deps.fetchPendingProposals();   // 刷新待拍板列
          closeMgmtModal();
        } else { (promote as HTMLButtonElement).disabled = false; alert(t("ops.failed")); }
      } });
    body.appendChild(promote);
  } else {
    body.appendChild(el("div", { class: "mgmt-empty", text: t("ops.failed") }));
  }
  const again = el("button", { class: "mgmt-inline-link", text: t("diag.rerun"), onclick: renderDiagnosePanel });
  body.appendChild(again);
}

async function open(deps: Deps): Promise<void> {
  if (deps) _deps = deps;
  openMgmtModal(t("diag.title"));
  await renderDiagnosePanel();
}

const KarvyDiagnosePanel = { open, renderOpsDiagnosis };
(window as unknown as { KarvyDiagnosePanel: typeof KarvyDiagnosePanel }).KarvyDiagnosePanel = KarvyDiagnosePanel;
export { KarvyDiagnosePanel };
