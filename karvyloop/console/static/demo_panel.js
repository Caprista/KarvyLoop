var KarvyDemoPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON;
  const _i18n = () => window.KarvyI18n;
  const t = (k, vars) => _i18n().t(k, vars);
  function _fmtPct(x) {
    const n = typeof x === "number" ? x : NaN;
    return isFinite(n) ? (n * 100).toFixed(0) + "%" : "—";
  }
  function _num(x) {
    return x === null || x === void 0 || x === "" ? "—" : String(x);
  }
  function _section(title, ...rest) {
    return el(
      "div",
      { class: "mgmt-card demo-section" },
      el(
        "div",
        { class: "mc-main" },
        el("div", { class: "mc-name", text: title }),
        ...rest
      )
    );
  }
  function _kv(label, v1, v7) {
    return el(
      "tr",
      null,
      el("td", { text: label }),
      el("td", { text: v1 }),
      el("td", { text: v7 })
    );
  }
  function _banner(man, lang) {
    const disc = (man.disclosure || {})[lang] || (man.disclosure || {}).zh || "";
    const days = man.virtual_days || [];
    const span = days.length ? `${days[0]} → ${days[days.length - 1]}` : "";
    return el(
      "div",
      { class: "demo-banner" },
      el("div", { class: "demo-banner-title", text: t("demo.banner") }),
      el("div", { class: "mc-meta", text: disc }),
      el("div", {
        class: "mc-meta",
        text: t("demo.banner.meta", { model: man.model || "?", span, builder: man.builder || "" })
      })
    );
  }
  function _personaLine(man, lang) {
    const p = man.persona || {};
    if (lang === "zh") {
      return `${p.name || "小林"} · ${p.age || 28} · ${p.title || ""} · ${p.beat || ""} —— ${p.style || ""}(${p.routine || ""})`;
    }
    return `${p.name_en || "Lin"} · ${p.age || 28} · ${p.title_en || ""} · ${p.beat_en || ""} — ${p.style_en || ""} (${p.routine_en || ""})`;
  }
  function _compareTable(d) {
    const day1 = d.day1 || {}, day7 = d.day7 || {};
    const x1 = d.day1_extra || {}, x7 = d.day7_extra || {};
    const tbl = el("table", { class: "demo-table" });
    tbl.appendChild(el(
      "tr",
      null,
      el("th", { text: t("demo.col.metric") }),
      el("th", { text: t("demo.day1") }),
      el("th", { text: t("demo.day7") })
    ));
    tbl.appendChild(_kv(t("demo.m.skills"), _num(day1.skills_total), _num(day7.skills_total)));
    tbl.appendChild(_kv(t("demo.m.runs"), _num(day1.runs_total), _num(day7.runs_total)));
    tbl.appendChild(_kv(t("demo.m.hit_rate"), _fmtPct(day1.hit_rate), _fmtPct(day7.hit_rate)));
    tbl.appendChild(_kv(t("demo.m.success"), _fmtPct(day1.avg_success_rate), _fmtPct(day7.avg_success_rate)));
    tbl.appendChild(_kv(t("demo.m.knowledge"), _num(x1.knowledge), _num(x7.knowledge)));
    tbl.appendChild(_kv(t("demo.m.prefs"), _num(x1.prefs), _num(x7.prefs)));
    return tbl;
  }
  function _growthTable(points) {
    const tbl = el("table", { class: "demo-table" });
    tbl.appendChild(el(
      "tr",
      null,
      el("th", { text: t("demo.col.day") }),
      el("th", { text: t("demo.m.runs") }),
      el("th", { text: t("demo.m.skills") }),
      el("th", { text: t("demo.m.hit_rate") }),
      el("th", { text: t("demo.m.success") })
    ));
    for (const p of points || []) {
      tbl.appendChild(el(
        "tr",
        null,
        el("td", { text: p.day || "" }),
        el("td", { text: _num(p.runs_total) }),
        el("td", { text: _num(p.skills_total) }),
        el("td", { text: _fmtPct(p.hit_rate) }),
        el("td", { text: _fmtPct(p.avg_success_rate) })
      ));
    }
    return tbl;
  }
  function _renderInstance(body, d, lang) {
    const man = d.manifest || {};
    body.appendChild(_banner(man, lang));
    body.appendChild(el("div", { class: "mgmt-hint", text: _personaLine(man, lang) }));
    const list = el("div", { class: "mgmt-list" });
    list.appendChild(_section(t("demo.compare.head"), _compareTable(d)));
    list.appendChild(_section(t("demo.growth.head"), _growthTable(d.growth || [])));
    const userSkills = (d.skills || []).filter((s) => s.source !== "system");
    const sk = _section(t("demo.skills.head", { n: userSkills.length }));
    for (const s of userSkills) {
      sk.appendChild(el(
        "div",
        { class: "mc-meta" },
        el("b", { text: s.name }),
        " — ",
        el("span", { text: (s.description || "").slice(0, 120) })
      ));
    }
    const reused = (d.skills_curve || []).filter((s) => String(s.sig || "").startsWith("system:"));
    if (reused.length) {
      sk.appendChild(el("div", {
        class: "mc-meta",
        text: t("demo.skills.system", { names: reused.map((s) => s.name || s.sig).join(" · ") })
      }));
    }
    list.appendChild(sk);
    const pf = _section(t("demo.prefs.head", { n: (d.decision_prefs || []).length }));
    for (const p of d.decision_prefs || []) {
      pf.appendChild(el(
        "div",
        { class: "mc-meta" },
        el("span", {
          class: "dpref-badge " + (p.status === "confirmed" ? "confirmed" : "provisional"),
          text: p.kind || "taste"
        }),
        " ",
        el("span", { text: p.content })
      ));
    }
    list.appendChild(pf);
    if ((d.role_experiences || []).length) {
      const ex = _section(t("demo.exp.head", { n: d.role_experiences.length }));
      for (const e of d.role_experiences) {
        ex.appendChild(el("div", { class: "mc-meta", text: `[${e.role}·${e.kind}] ${e.content}` }));
      }
      list.appendChild(ex);
    }
    const kn = _section(t("demo.knowledge.head", { n: d.knowledge_total || 0 }));
    for (const k of d.knowledge_recent || []) {
      kn.appendChild(el("div", { class: "mc-meta", text: "· " + k.content }));
    }
    list.appendChild(kn);
    const ta = d.taste || {};
    list.appendChild(_section(
      t("demo.taste.head"),
      el("div", { class: "mc-meta", text: t("demo.taste.progress", {
        n: ta.n || 0,
        hits: ta.hits || 0,
        rate: ta.hit_rate === null || ta.hit_rate === void 0 ? "—" : _fmtPct(ta.hit_rate),
        gate_n: ta.gate_min_n,
        gate_lb: ta.gate_min_wilson_lb
      }) })
    ));
    const tok = d.tokens_by_day || [];
    const totalTok = tok.reduce((a, r) => a + (r.input || 0) + (r.output || 0), 0);
    const conv = d.conversations || {};
    list.appendChild(_section(
      t("demo.cost.head"),
      el("div", { class: "mc-meta", text: t("demo.cost.line", {
        total: totalTok.toLocaleString(),
        days: tok.length,
        convs: conv.count || 0,
        turns: conv.turns || 0
      }) })
    ));
    list.appendChild(el("div", { class: "mgmt-hint", text: t("demo.readonly.note") }));
    body.appendChild(list);
  }
  async function _load(body, iid, lang) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-hint", text: t("demo.loading") }));
    const d = await _getJSON("/api/demo/instance/" + encodeURIComponent(iid));
    body.innerHTML = "";
    if (!d || !d.ok) {
      body.appendChild(el("div", { class: "mgmt-hint", text: t("demo.missing") }));
      return;
    }
    _renderInstance(body, d, lang);
  }
  async function open() {
    const lang = _i18n().getLang();
    _KM.openMgmtModal(t("demo.name"));
    const b = _KM.mgmtBody();
    if (!b) return;
    b.innerHTML = "";
    b.appendChild(el("div", { class: "mgmt-hint", text: t("demo.loading") }));
    const data = await _getJSON("/api/demo/instances");
    const instances = data && data.instances || [];
    b.innerHTML = "";
    if (!instances.length) {
      b.appendChild(el("div", { class: "mgmt-hint", text: t("demo.missing") }));
      return;
    }
    const preferred = instances.find((i) => i.lang === lang) || instances[0];
    const bodyHost = el("div", { class: "demo-body" });
    if (instances.length > 1) {
      const sw = el("div", { class: "demo-switch" });
      for (const inst of instances) {
        sw.appendChild(el("button", {
          class: "mgmt-inline-link",
          text: inst.id,
          onclick: () => {
            void _load(bodyHost, inst.id, lang);
          }
        }));
      }
      b.appendChild(sw);
    }
    b.appendChild(bodyHost);
    await _load(bodyHost, preferred.id, lang);
  }
  const KarvyDemoPanel = { open };
  window.KarvyDemoPanel = KarvyDemoPanel;
  try {
    const btn = document.getElementById("demo-open");
    if (btn) btn.addEventListener("click", () => {
      void open();
    });
  } catch (e) {
  }
  exports.KarvyDemoPanel = KarvyDemoPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
