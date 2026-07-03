var KarvyTokensBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON;
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
