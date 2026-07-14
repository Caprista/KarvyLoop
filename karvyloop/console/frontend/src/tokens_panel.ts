/* tokens_panel.ts — 💰 token 成本表(从 app.js 抽出)。
 * "用得起 = 护城河,成本必须常驻可见":顶栏 💰 meter(pollMeter 周期刷)+ 点开弹窗(总量 + 各模型 + 各功能花在哪)。
 * 自洽,只用 dom/modal/i18n + document。暴露 window.KarvyTokens.{ pollMeter, open }。
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
  if (!totalTok) { meter.textContent = "💰 —"; return; }
  const num = _fmtTok(totalTok);
  let tail = " tok";
  if (cost != null) tail += " · ¥" + (cost * 7).toFixed(2);   // 粗略 USD→¥(P1 真汇率)
  if (model) tail += " · " + model;
  if (meter.textContent === "💰 " + num + tail) return;   // 没变不重建(更不动画)
  // 微动效 P1-1 数字 ticker:数值真变了才动 —— 旧数字上滑淡出、新数字下入(transform/opacity;
  // 初次填充不动;reduced-motion 静态换字降级)。接棒 P0 的整体 .bump pop(占位退役,一次变化一个动作)。
  const prevNum = meter.getAttribute("data-tok") || "";
  const reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  meter.textContent = "";
  meter.append("💰 ");
  const wrap = el("span", { class: "kv-ticker" });
  const cur = el("span", { text: num });
  wrap.appendChild(cur);
  if (prevNum && prevNum !== num && !reduced) {
    cur.classList.add("kv-tick-in");
    const old = el("span", { class: "kv-tick-out", text: prevNum, "aria-hidden": "true" });
    old.addEventListener("animationend", () => old.remove());
    wrap.appendChild(old);
  }
  meter.appendChild(wrap);
  meter.append(tail);
  meter.setAttribute("data-tok", num);
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

// ---- Hardy ⑥ 可见面:分时段查询("笼统的查询等于没有查询")----
// 今天(hour 粒度)/ 7 天 / 30 天(day 粒度)→ GET /api/tokens/query,
// series 画纯 CSS div 柱(不引图表库),by_source 画排行条(烧得多在前)。
type TokRange = "today" | "7d" | "30d";
let _curRange: TokRange = "7d";

function _rangeWindow(range: TokRange): { start: number; end: number; gran: "hour" | "day" } {
  const now = new Date();
  const end = now.getTime() / 1000;
  if (range === "today") {
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    return { start: midnight, end, gran: "hour" };   // 当日零点起,整点桶
  }
  return { start: end - (range === "7d" ? 7 : 30) * 86400, end, gran: "day" };
}

// 柱下短标:hour → "HH:00",day → "M-D"(完整 label 进 title 悬浮)
function _shortLabel(label: string, gran: "hour" | "day"): string {
  const s = String(label || "");
  if (gran === "hour") {
    const m = s.match(/(\d{2}):00$/);
    return m ? m[1] + ":00" : s;
  }
  const m = s.match(/^\d{4}-(\d{2})-(\d{2})$/);
  return m ? String(Number(m[1])) + "-" + String(Number(m[2])) : s;
}

async function _renderRangeBody(host: HTMLElement, range: TokRange): Promise<void> {
  host.innerHTML = "";
  host.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
  const w = _rangeWindow(range);
  const data = await _getJSON(
    "/api/tokens/query?start_ts=" + w.start + "&end_ts=" + w.end + "&granularity=" + w.gran);
  host.innerHTML = "";
  if (!data) { host.appendChild(el("div", { class: "muted", text: t("tokens.none") })); return; }
  const tot = data.totals || {};
  host.appendChild(el("div", { class: "tok-sub", text: t("tokens.window_totals", {
    total: _fmtTok(tot.total || 0), in: _fmtTok(tot.input || 0),
    out: _fmtTok(tot.output || 0), calls: tot.calls || 0 }) }));
  const series = (data.series || []) as any[];
  if (!series.length) {
    host.appendChild(el("div", { class: "muted", text: t("tokens.range_empty") }));
  } else {
    const max = series.reduce((m, b) => Math.max(m, b.total || 0), 1);
    const chart = el("div", { class: "tok-chart" });
    for (const b of series) {
      const pct = Math.max(2, Math.round(((b.total || 0) / max) * 100));
      chart.appendChild(el("div", { class: "tok-chart-col",
        title: (b.label || "") + " · " + _fmtTok(b.total || 0) + " tok · " + (b.calls || 0) + "×" },
        el("div", { class: "tok-chart-bar", style: "height:" + pct + "%" }),
        el("div", { class: "tok-chart-lbl", text: _shortLabel(b.label, w.gran) })));
    }
    host.appendChild(chart);
  }
  // by_source 排行条(该时段谁烧的,烧得多在前 —— 后端已降序)
  const rows = (data.by_source || []) as any[];
  if (rows.length) {
    host.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_source") }));
    const maxSrc = rows.reduce((m, r) => Math.max(m, r.total || 0), 1);
    const rank = el("div", { class: "tok-rank" });
    for (const r of rows) {
      const pct = Math.max(1, Math.round(((r.total || 0) / maxSrc) * 100));
      rank.appendChild(el("div", { class: "tok-rank-row" },
        el("span", { class: "tok-rank-name", text: String(r.source || "?") }),
        el("span", { class: "tok-rank-track" },
          el("span", { class: "tok-rank-bar", style: "width:" + pct + "%" })),
        el("span", { class: "tok-rank-val", text: _fmtTok(r.total || 0) })));
    }
    host.appendChild(rank);
  }
}

function _renderRangeSection(body: HTMLElement): void {
  body.appendChild(el("h3", { class: "tok-h", text: t("tokens.range_title") }));
  const tabs = el("div", { class: "tok-range-tabs" });
  const host = el("div", { class: "tok-range-body" });
  const ranges: Array<[TokRange, string]> = [
    ["today", t("tokens.range_today")], ["7d", t("tokens.range_7d")], ["30d", t("tokens.range_30d")]];
  const _redraw = () => {
    Array.from(tabs.children).forEach((b, i) =>
      (b as HTMLElement).classList.toggle("active", ranges[i][0] === _curRange));
    void _renderRangeBody(host, _curRange);
  };
  for (const [key, label] of ranges) {
    tabs.appendChild(el("button", { class: "tok-range-tab", text: label,
      onclick: () => { _curRange = key; _redraw(); } }));
  }
  body.appendChild(tabs);
  body.appendChild(host);
  _redraw();
}

// ---- 💸 预算段(docs/56 ②:后端 spend brake 有了但用户在 UI 够不着 → 加"看+改上限")----
// GET /api/budget:今日/本月已用 vs 上限 + on_limit。四维(daily/monthly × usd/tokens)进度条 +
// 改上限表单 + on_limit(warn 只告警 / pause 达 100% 拦后台)开关。POST /api/budget 落 config.yaml。
function _fmtUsed(unit: string, v: number): string {
  return unit === "usd" ? "$" + (v || 0).toFixed(2) : _fmtTok(v || 0) + " tok";
}
function _fmtLimit(unit: string, v: number | null): string {
  if (v == null) return t("budget.no_limit");
  return unit === "usd" ? "$" + Number(v).toFixed(2) : _fmtTok(Number(v)) + " tok";
}

// 把预算内容渲进给定的 host 容器(先清空 → 可重画,用量/进度即时刷新,无重复标题)。
async function _renderBudgetInto(host: HTMLElement): Promise<void> {
  host.innerHTML = "";
  host.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
  const data = await _getJSON("/api/budget");
  host.innerHTML = "";
  if (!data) { host.appendChild(el("div", { class: "muted", text: t("tokens.none") })); return; }
  const dims: any[] = Array.isArray(data.dimensions) ? data.dimensions : [];
  if (!data.enabled) {
    host.appendChild(el("div", { class: "muted budget-off", text: t("budget.disabled_hint") }));
  }
  // 每维度一条进度(有上限才画满格条;没上限只显已用 + "未设限")
  for (const d of dims) {
    const row = el("div", { class: "budget-row" });
    row.appendChild(el("div", { class: "budget-row-label",
      text: t("budget.dim_" + d.key) + ": " + _fmtUsed(d.unit, d.used) + " / " + _fmtLimit(d.unit, d.limit) }));
    if (d.limit != null && d.limit > 0) {
      const pct = Math.min(100, Math.round((d.ratio || 0) * 100));
      const tier = pct >= 100 ? "over" : pct >= 90 ? "hi" : pct >= 75 ? "mid" : "ok";
      const track = el("div", { class: "budget-track" });
      track.appendChild(el("div", { class: "budget-bar budget-bar-" + tier, style: "width:" + pct + "%" }));
      row.appendChild(track);
    }
    host.appendChild(row);
  }
  // 改上限表单(0/空 = 不设限该维度;四维全空 = 关刹车 = 无限)
  const cur: Record<string, any> = {};
  for (const d of dims) cur[d.key] = d.limit;
  const form = el("div", { class: "budget-form" });
  const inDaily = el("input", { class: "budget-in", type: "number", min: "0", step: "any",
    placeholder: t("budget.ph_no_limit"), value: cur.daily_usd != null ? String(cur.daily_usd) : "" }) as HTMLInputElement;
  const inMonthly = el("input", { class: "budget-in", type: "number", min: "0", step: "any",
    placeholder: t("budget.ph_no_limit"), value: cur.monthly_usd != null ? String(cur.monthly_usd) : "" }) as HTMLInputElement;
  form.appendChild(el("label", { class: "budget-fld" },
    el("span", { text: t("budget.set_daily_usd") }), inDaily));
  form.appendChild(el("label", { class: "budget-fld" },
    el("span", { text: t("budget.set_monthly_usd") }), inMonthly));
  // on_limit 开关(warn 只告警 / pause 达 100% 拦后台自动路径,前台永不拦)
  const onLimit = el("select", { class: "budget-in" }) as HTMLSelectElement;
  for (const v of (data.valid_on_limit || ["warn", "pause"])) {
    const opt = el("option", { value: v, text: t("budget.on_limit_" + v) }) as HTMLOptionElement;
    if (v === data.on_limit) opt.selected = true;
    onLimit.appendChild(opt);
  }
  form.appendChild(el("label", { class: "budget-fld" },
    el("span", { text: t("budget.on_limit_label") }), onLimit));
  const msg = el("div", { class: "budget-msg" });
  const save = el("button", { class: "mgmt-submit", text: t("budget.save"),
    onClick: async () => {
      (save as HTMLButtonElement).disabled = true;
      msg.textContent = "";
      const payload = {
        daily_usd: Number(inDaily.value) || 0,
        monthly_usd: Number(inMonthly.value) || 0,
        daily_tokens: 0, monthly_tokens: 0,   // token 维度先只留 USD 表单(常用);token 上限走 config
        on_limit: onLimit.value,
      };
      const r = await _postJSON("/api/budget", payload);
      if (r.ok && r.data && r.data.ok) {
        msg.textContent = t("budget.saved");
        await _renderBudgetInto(host);   // 重画本段(用量/进度即时刷新,无重复标题)
      } else {
        (save as HTMLButtonElement).disabled = false;
        msg.textContent = (r.data && r.data.reason) || t("budget.save_failed");
      }
    } });
  form.appendChild(save);
  host.appendChild(form);
  host.appendChild(msg);
}

// 预算段外壳:标题只加一次 + 一个可重画的 host 容器。
async function _renderBudgetSection(body: HTMLElement): Promise<void> {
  body.appendChild(el("h3", { class: "tok-h", text: t("budget.title") }));
  const host = el("div", { class: "budget-body" });
  body.appendChild(host);
  await _renderBudgetInto(host);
}

// ch4 #4:点钱包 → token 统计弹窗(总量 + 分时段柱状 + 各模型分别用了多少 + 各功能花在哪)
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
  // 💸 预算段(docs/56 ②):看今日/本月已用 vs 上限 + 改上限 + on_limit 开关(后端 spend brake 的 UI 面)
  await _renderBudgetSection(body);
  // 分时段(Hardy ⑥):今天(hour)/ 7 天 / 30 天(day)切换 + CSS 柱状 + 时段内功能排行
  _renderRangeSection(body);
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
