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
