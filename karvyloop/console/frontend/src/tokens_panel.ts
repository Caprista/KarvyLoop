/* tokens_panel.ts — 💰 token 成本表(从 app.js 抽出)。
 * "用得起 = 护城河,成本必须常驻可见":顶栏 💰 meter(pollMeter 周期刷)+ 点开弹窗(总量 + 各模型 + 各功能花在哪)。
 * 自洽,只用 dom/modal/i18n + document。暴露 window.KarvyTokens.{ pollMeter, open }。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
}
interface Modal { openMgmtModal: (title: string) => void; mgmtBody: () => HTMLElement | null }
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

function _fmtTok(n: number): string { return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }

// ch4:token 成本表(用得起 = 护城河,成本常驻可见)
async function pollMeter(): Promise<void> {
  const meter = document.getElementById("token-meter");
  if (!meter) return;
  const data = await _getJSON("/api/tokens");
  const tot = (data && data.totals) || {};
  const totalTok = (tot.input || 0) + (tot.output || 0);
  const byModel = (data && data.by_model) || [];
  const model = byModel.length ? (byModel[0].model || "?") : "";
  const cost = tot.cost_usd != null ? tot.cost_usd : null;
  let s = "💰 " + _fmtTok(totalTok) + " tok";
  if (cost != null) s += " · ¥" + (cost * 7).toFixed(2);   // 粗略 USD→¥(P1 真汇率)
  if (model) s += " · " + model;
  meter.textContent = totalTok ? s : "💰 —";
}

function _tokTable(rows: any[], keyCol: string): HTMLElement {
  if (!rows.length) return el("div", { class: "muted", text: t("tokens.none") });
  const tbl = el("table", { class: "tok-table" });
  tbl.appendChild(el("tr", {},
    el("th", { text: t("tokens.col_" + keyCol) }),
    el("th", { class: "num", text: t("tokens.col_in") }),
    el("th", { class: "num", text: t("tokens.col_out") }),
    el("th", { class: "num", text: t("tokens.col_total") }),
    el("th", { class: "num", text: t("tokens.col_calls") })));
  for (const r of rows) {
    tbl.appendChild(el("tr", {},
      el("td", { text: String(r[keyCol] || "?") }),
      el("td", { class: "num", text: _fmtTok(r.input || 0) }),
      el("td", { class: "num", text: _fmtTok(r.output || 0) }),
      el("td", { class: "num tok-strong", text: _fmtTok(r.total || 0) }),
      el("td", { class: "num", text: String(r.calls || 0) })));
  }
  return tbl;
}

// ch4 #4:点钱包 → token 统计弹窗(总量 + 各模型分别用了多少 + 各功能花在哪)
async function open(): Promise<void> {
  openMgmtModal(t("tokens.title"));
  const body = mgmtBody(); if (!body) return;
  body.innerHTML = "";
  body.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
  const data = await _getJSON("/api/tokens");
  body.innerHTML = "";
  if (!data) { body.appendChild(el("div", { class: "muted", text: t("tokens.none") })); return; }
  const tot = data.totals || {};
  const total = tot.total != null ? tot.total : (tot.input || 0) + (tot.output || 0);
  // 总量卡
  const sum = el("div", { class: "tok-summary" });
  sum.appendChild(el("div", { class: "tok-big", text: "💰 " + _fmtTok(total) + " tok" }));
  sum.appendChild(el("div", { class: "tok-sub", text:
    t("tokens.breakdown", { in: _fmtTok(tot.input || 0), out: _fmtTok(tot.output || 0), calls: tot.calls || 0 }) }));
  if ((tot.cache_read || 0) || (tot.cache_write || 0)) {
    sum.appendChild(el("div", { class: "tok-sub", text:
      t("tokens.cache", { r: _fmtTok(tot.cache_read || 0), w: _fmtTok(tot.cache_write || 0) }) }));
  }
  body.appendChild(sum);
  // 各模型用了多少(Hardy:要看不同模型分别用了多少量)
  body.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_model") }));
  body.appendChild(_tokTable(data.by_model || [], "model"));
  // 各功能花在哪(KarvyLoop 专属:成本可见 = 护城河)
  const bySource = data.by_source || [];
  if (bySource.length) {
    body.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_source") }));
    body.appendChild(_tokTable(bySource, "source"));
  }
}

const KarvyTokens = { pollMeter, open };
(window as unknown as { KarvyTokens: typeof KarvyTokens }).KarvyTokens = KarvyTokens;
export { KarvyTokens };
