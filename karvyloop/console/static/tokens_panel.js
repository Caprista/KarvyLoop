var KarvyTokensBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  function _fmtTok(n) {
    return n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
  }
  async function pollMeter() {
    const meter = document.getElementById("token-meter");
    if (!meter) return;
    const data = await _getJSON("/api/tokens");
    const tot = data && data.totals || {};
    const totalTok = (tot.input || 0) + (tot.output || 0);
    const byModel = data && data.by_model || [];
    const model = byModel.length ? byModel[0].model || "?" : "";
    const cost = tot.cost_usd != null ? tot.cost_usd : null;
    let s = "💰 " + _fmtTok(totalTok) + " tok";
    if (cost != null) s += " · ¥" + (cost * 7).toFixed(2);
    if (model) s += " · " + model;
    meter.textContent = totalTok ? s : "💰 —";
  }
  function _tokTable(rows, keyCol) {
    if (!rows.length) return el("div", { class: "muted", text: t("tokens.none") });
    const tbl = el("table", { class: "tok-table" });
    tbl.appendChild(el(
      "tr",
      {},
      el("th", { text: t("tokens.col_" + keyCol) }),
      el("th", { class: "num", text: t("tokens.col_in") }),
      el("th", { class: "num", text: t("tokens.col_out") }),
      el("th", { class: "num", text: t("tokens.col_total") }),
      el("th", { class: "num", text: t("tokens.col_calls") })
    ));
    for (const r of rows) {
      tbl.appendChild(el(
        "tr",
        {},
        el("td", { text: String(r[keyCol] || "?") }),
        el("td", { class: "num", text: _fmtTok(r.input || 0) }),
        el("td", { class: "num", text: _fmtTok(r.output || 0) }),
        el("td", { class: "num tok-strong", text: _fmtTok(r.total || 0) }),
        el("td", { class: "num", text: String(r.calls || 0) })
      ));
    }
    return tbl;
  }
  let _curRange = "7d";
  function _rangeWindow(range) {
    const now = /* @__PURE__ */ new Date();
    const end = now.getTime() / 1e3;
    if (range === "today") {
      const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1e3;
      return { start: midnight, end, gran: "hour" };
    }
    return { start: end - (range === "7d" ? 7 : 30) * 86400, end, gran: "day" };
  }
  function _shortLabel(label, gran) {
    const s = String(label || "");
    if (gran === "hour") {
      const m2 = s.match(/(\d{2}):00$/);
      return m2 ? m2[1] + ":00" : s;
    }
    const m = s.match(/^\d{4}-(\d{2})-(\d{2})$/);
    return m ? String(Number(m[1])) + "-" + String(Number(m[2])) : s;
  }
  async function _renderRangeBody(host, range) {
    host.innerHTML = "";
    host.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
    const w = _rangeWindow(range);
    const data = await _getJSON(
      "/api/tokens/query?start_ts=" + w.start + "&end_ts=" + w.end + "&granularity=" + w.gran
    );
    host.innerHTML = "";
    if (!data) {
      host.appendChild(el("div", { class: "muted", text: t("tokens.none") }));
      return;
    }
    const tot = data.totals || {};
    host.appendChild(el("div", { class: "tok-sub", text: t("tokens.window_totals", {
      total: _fmtTok(tot.total || 0),
      in: _fmtTok(tot.input || 0),
      out: _fmtTok(tot.output || 0),
      calls: tot.calls || 0
    }) }));
    const series = data.series || [];
    if (!series.length) {
      host.appendChild(el("div", { class: "muted", text: t("tokens.range_empty") }));
    } else {
      const max = series.reduce((m, b) => Math.max(m, b.total || 0), 1);
      const chart = el("div", { class: "tok-chart" });
      for (const b of series) {
        const pct = Math.max(2, Math.round((b.total || 0) / max * 100));
        chart.appendChild(el(
          "div",
          {
            class: "tok-chart-col",
            title: (b.label || "") + " · " + _fmtTok(b.total || 0) + " tok · " + (b.calls || 0) + "×"
          },
          el("div", { class: "tok-chart-bar", style: "height:" + pct + "%" }),
          el("div", { class: "tok-chart-lbl", text: _shortLabel(b.label, w.gran) })
        ));
      }
      host.appendChild(chart);
    }
    const rows = data.by_source || [];
    if (rows.length) {
      host.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_source") }));
      const maxSrc = rows.reduce((m, r) => Math.max(m, r.total || 0), 1);
      const rank = el("div", { class: "tok-rank" });
      for (const r of rows) {
        const pct = Math.max(1, Math.round((r.total || 0) / maxSrc * 100));
        rank.appendChild(el(
          "div",
          { class: "tok-rank-row" },
          el("span", { class: "tok-rank-name", text: String(r.source || "?") }),
          el(
            "span",
            { class: "tok-rank-track" },
            el("span", { class: "tok-rank-bar", style: "width:" + pct + "%" })
          ),
          el("span", { class: "tok-rank-val", text: _fmtTok(r.total || 0) })
        ));
      }
      host.appendChild(rank);
    }
  }
  function _renderRangeSection(body) {
    body.appendChild(el("h3", { class: "tok-h", text: t("tokens.range_title") }));
    const tabs = el("div", { class: "tok-range-tabs" });
    const host = el("div", { class: "tok-range-body" });
    const ranges = [
      ["today", t("tokens.range_today")],
      ["7d", t("tokens.range_7d")],
      ["30d", t("tokens.range_30d")]
    ];
    const _redraw = () => {
      Array.from(tabs.children).forEach((b, i) => b.classList.toggle("active", ranges[i][0] === _curRange));
      void _renderRangeBody(host, _curRange);
    };
    for (const [key, label] of ranges) {
      tabs.appendChild(el("button", {
        class: "tok-range-tab",
        text: label,
        onclick: () => {
          _curRange = key;
          _redraw();
        }
      }));
    }
    body.appendChild(tabs);
    body.appendChild(host);
    _redraw();
  }
  function _fmtUsed(unit, v) {
    return unit === "usd" ? "$" + (v || 0).toFixed(2) : _fmtTok(v || 0) + " tok";
  }
  function _fmtLimit(unit, v) {
    if (v == null) return t("budget.no_limit");
    return unit === "usd" ? "$" + Number(v).toFixed(2) : _fmtTok(Number(v)) + " tok";
  }
  async function _renderBudgetInto(host) {
    host.innerHTML = "";
    host.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
    const data = await _getJSON("/api/budget");
    host.innerHTML = "";
    if (!data) {
      host.appendChild(el("div", { class: "muted", text: t("tokens.none") }));
      return;
    }
    const dims = Array.isArray(data.dimensions) ? data.dimensions : [];
    if (!data.enabled) {
      host.appendChild(el("div", { class: "muted budget-off", text: t("budget.disabled_hint") }));
    }
    for (const d of dims) {
      const row = el("div", { class: "budget-row" });
      row.appendChild(el("div", {
        class: "budget-row-label",
        text: t("budget.dim_" + d.key) + ": " + _fmtUsed(d.unit, d.used) + " / " + _fmtLimit(d.unit, d.limit)
      }));
      if (d.limit != null && d.limit > 0) {
        const pct = Math.min(100, Math.round((d.ratio || 0) * 100));
        const tier = pct >= 100 ? "over" : pct >= 90 ? "hi" : pct >= 75 ? "mid" : "ok";
        const track = el("div", { class: "budget-track" });
        track.appendChild(el("div", { class: "budget-bar budget-bar-" + tier, style: "width:" + pct + "%" }));
        row.appendChild(track);
      }
      host.appendChild(row);
    }
    const cur = {};
    for (const d of dims) cur[d.key] = d.limit;
    const form = el("div", { class: "budget-form" });
    const inDaily = el("input", {
      class: "budget-in",
      type: "number",
      min: "0",
      step: "any",
      placeholder: t("budget.ph_no_limit"),
      value: cur.daily_usd != null ? String(cur.daily_usd) : ""
    });
    const inMonthly = el("input", {
      class: "budget-in",
      type: "number",
      min: "0",
      step: "any",
      placeholder: t("budget.ph_no_limit"),
      value: cur.monthly_usd != null ? String(cur.monthly_usd) : ""
    });
    form.appendChild(el(
      "label",
      { class: "budget-fld" },
      el("span", { text: t("budget.set_daily_usd") }),
      inDaily
    ));
    form.appendChild(el(
      "label",
      { class: "budget-fld" },
      el("span", { text: t("budget.set_monthly_usd") }),
      inMonthly
    ));
    const onLimit = el("select", { class: "budget-in" });
    for (const v of data.valid_on_limit || ["warn", "pause"]) {
      const opt = el("option", { value: v, text: t("budget.on_limit_" + v) });
      if (v === data.on_limit) opt.selected = true;
      onLimit.appendChild(opt);
    }
    form.appendChild(el(
      "label",
      { class: "budget-fld" },
      el("span", { text: t("budget.on_limit_label") }),
      onLimit
    ));
    const msg = el("div", { class: "budget-msg" });
    const save = el("button", {
      class: "mgmt-submit",
      text: t("budget.save"),
      onClick: async () => {
        save.disabled = true;
        msg.textContent = "";
        const payload = {
          daily_usd: Number(inDaily.value) || 0,
          monthly_usd: Number(inMonthly.value) || 0,
          daily_tokens: 0,
          monthly_tokens: 0,
          // token 维度先只留 USD 表单(常用);token 上限走 config
          on_limit: onLimit.value
        };
        const r = await _postJSON("/api/budget", payload);
        if (r.ok && r.data && r.data.ok) {
          msg.textContent = t("budget.saved");
          await _renderBudgetInto(host);
        } else {
          save.disabled = false;
          msg.textContent = r.data && r.data.reason || t("budget.save_failed");
        }
      }
    });
    form.appendChild(save);
    host.appendChild(form);
    host.appendChild(msg);
  }
  async function _renderBudgetSection(body) {
    body.appendChild(el("h3", { class: "tok-h", text: t("budget.title") }));
    const host = el("div", { class: "budget-body" });
    body.appendChild(host);
    await _renderBudgetInto(host);
  }
  async function open() {
    openMgmtModal(t("tokens.title"));
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
    const data = await _getJSON("/api/tokens");
    body.innerHTML = "";
    if (!data) {
      body.appendChild(el("div", { class: "muted", text: t("tokens.none") }));
      return;
    }
    const tot = data.totals || {};
    const total = tot.total != null ? tot.total : (tot.input || 0) + (tot.output || 0);
    const sum = el("div", { class: "tok-summary" });
    sum.appendChild(el("div", { class: "tok-big", text: "💰 " + _fmtTok(total) + " tok" }));
    sum.appendChild(el("div", { class: "tok-sub", text: t("tokens.breakdown", { in: _fmtTok(tot.input || 0), out: _fmtTok(tot.output || 0), calls: tot.calls || 0 }) }));
    if (tot.cache_read || 0 || (tot.cache_write || 0)) {
      sum.appendChild(el("div", { class: "tok-sub", text: t("tokens.cache", { r: _fmtTok(tot.cache_read || 0), w: _fmtTok(tot.cache_write || 0) }) }));
    }
    body.appendChild(sum);
    await _renderBudgetSection(body);
    _renderRangeSection(body);
    body.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_model") }));
    body.appendChild(_tokTable(data.by_model || [], "model"));
    const bySource = data.by_source || [];
    if (bySource.length) {
      body.appendChild(el("h3", { class: "tok-h", text: t("tokens.by_source") }));
      body.appendChild(_tokTable(bySource, "source"));
    }
  }
  const KarvyTokens = { pollMeter, open };
  window.KarvyTokens = KarvyTokens;
  exports.KarvyTokens = KarvyTokens;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
