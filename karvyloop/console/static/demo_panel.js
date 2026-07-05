var KarvyDemoPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON;
  const _i18n = () => window.KarvyI18n;
  const t = (k, vars) => _i18n().t(k, vars);
  function _num(x) {
    return x === null || x === void 0 || x === "" ? "—" : String(x);
  }
  function _personaHeader(man, lang) {
    const p = man.persona || {};
    const zh = lang === "zh";
    const name = zh ? p.name || "小林" : p.name_en || "Lin";
    const title = zh ? p.title || "" : p.title_en || "";
    const beat = zh ? p.beat || "" : p.beat_en || "";
    const style = zh ? p.style || "" : p.style_en || "";
    const routine = zh ? p.routine || "" : p.routine_en || "";
    const head = el("div", { class: "demo-persona-head" });
    head.appendChild(el(
      "div",
      { class: "demo-persona-title" },
      el("span", { class: "demo-persona-name", text: name }),
      el("span", {
        class: "demo-persona-meta",
        text: t("demo.persona.chips", { age: _num(p.age), title, beat })
      })
    ));
    head.appendChild(el("div", { class: "demo-persona-style", text: style }));
    if (routine) head.appendChild(el("div", { class: "demo-persona-routine", text: routine }));
    return head;
  }
  function _disclosureFootnote(man, lang) {
    const disc = (man.disclosure || {})[lang] || (man.disclosure || {}).zh || "";
    const days = man.virtual_days || [];
    const span = days.length ? `${days[0]} → ${days[days.length - 1]}` : "";
    const foot = el("div", { class: "demo-disclosure" });
    foot.appendChild(el("span", { class: "demo-disclosure-tag", text: t("demo.disclosure.tag") }));
    foot.appendChild(el("span", { text: " " + disc + " " }));
    foot.appendChild(el("span", {
      class: "demo-disclosure-meta",
      text: t("demo.banner.meta", { model: man.model || "?", span, builder: man.builder || "" })
    }));
    return foot;
  }
  const _MODE_LABELS = {
    cold_deliberate: "demo.mode.cold",
    pre_aligned_glance: "demo.mode.glance"
  };
  function _modeChip(modes) {
    if (!modes || !modes.length) return el("span", { class: "demo-mode demo-mode-none", text: t("demo.mode.warmup") });
    const key = _MODE_LABELS[modes[modes.length - 1]] || "";
    const cls = modes[modes.length - 1] === "pre_aligned_glance" ? "demo-mode-glance" : "demo-mode-cold";
    return el("span", { class: "demo-mode " + cls, text: key ? t(key) : modes[modes.length - 1] });
  }
  function _effortHero(d) {
    const curve = d.effort_curve || [];
    const first = curve[0] || {}, last = curve[curve.length - 1] || {};
    const prefs = d.decision_prefs || [];
    const userSkills = (d.skills || []).filter((s) => s.source !== "system");
    const ta = d.taste || {};
    const hero = el("div", { class: "demo-hero" });
    hero.appendChild(el("div", { class: "demo-hero-head", text: t("demo.hero.head") }));
    const maxH = Math.max(1, ...curve.map((c) => Number(c.hands_on_turns) || 0));
    const chart = el("div", { class: "demo-curve" });
    for (const c of curve) {
      const h = Number(c.hands_on_turns) || 0;
      const col = el("div", { class: "demo-curve-col" });
      const bar = el("div", { class: "demo-curve-bar" });
      bar.style.height = Math.round(h / maxH * 100) + "%";
      if (Number(c.corrections) > 0) bar.classList.add("has-corr");
      bar.appendChild(el("span", { class: "demo-curve-val", text: String(h) }));
      if (Number(c.corrections) > 0)
        bar.appendChild(el("span", { class: "demo-curve-corr", text: "✎" + c.corrections }));
      col.appendChild(el("div", { class: "demo-curve-barwrap" }, bar));
      col.appendChild(el("div", { class: "demo-curve-day", text: "D" + (c.day || "") }));
      chart.appendChild(col);
    }
    hero.appendChild(chart);
    hero.appendChild(el("div", {
      class: "demo-curve-legend",
      text: t("demo.hero.legend")
    }));
    const grid = el("div", { class: "demo-hero-grid" });
    const cell = (label, from, arrow, to, extra) => {
      const c = el("div", { class: "demo-hero-cell" });
      c.appendChild(el("div", { class: "demo-hero-label", text: label }));
      const row = el("div", { class: "demo-hero-delta" });
      if (typeof from === "string") row.appendChild(el("span", { class: "demo-hero-from", text: from }));
      else if (from) row.appendChild(from);
      row.appendChild(el("span", { class: "demo-hero-arrow", text: arrow }));
      if (typeof to === "string") row.appendChild(el("span", { class: "demo-hero-to", text: to }));
      else if (to) row.appendChild(to);
      c.appendChild(row);
      if (extra) c.appendChild(el("div", { class: "demo-hero-note", text: extra }));
      return c;
    };
    grid.appendChild(cell(
      t("demo.hero.handson"),
      String(first.hands_on_turns ?? "—"),
      "→",
      String(last.hands_on_turns ?? "—")
    ));
    grid.appendChild(cell(
      t("demo.hero.corrections"),
      String(first.corrections ?? "—"),
      "→",
      String(last.corrections ?? "—")
    ));
    grid.appendChild(cell(
      t("demo.hero.mode"),
      _modeChip(first.decision_modes || []),
      "→",
      _modeChip(last.decision_modes || [])
    ));
    grid.appendChild(cell(t("demo.hero.prefs"), "0", "→", String(prefs.length)));
    grid.appendChild(cell(t("demo.hero.skills"), "0", "→", String(userSkills.length)));
    grid.appendChild(cell(
      t("demo.hero.silence"),
      "0",
      "→",
      `${ta.n || 0}/${ta.gate_min_n || 35}`,
      t("demo.hero.silence.note", { need: ta.need_more ?? ta.gate_min_n - (ta.n || 0) })
    ));
    hero.appendChild(grid);
    hero.appendChild(el("div", { class: "demo-hero-punch", text: t("demo.hero.punch") }));
    return hero;
  }
  function _matchWorkspace(intent, workspace) {
    const m = /《([^》]+)》/.exec(intent || "");
    if (!m) return null;
    const key = m[1];
    for (const fname of Object.keys(workspace || {})) {
      if (fname.indexOf(key) >= 0) return workspace[fname];
    }
    return null;
  }
  function _entryRow(e, workspace, body) {
    const row = el("div", { class: "demo-entry" });
    const isH2A = !!e.decision;
    const isFeed = e.written !== null && e.written !== void 0;
    const chan = el("span", { class: "demo-entry-chan", text: e.channel || "" });
    if (isH2A) chan.classList.add("chan-h2a");
    else if (e.channel === "晨读") chan.classList.add("chan-feed");
    else if (e.routed) chan.classList.add("chan-route");
    row.appendChild(chan);
    row.appendChild(el("span", { class: "demo-entry-time", text: e.vtime || "" }));
    const intentWrap = el("span", { class: "demo-entry-intent" });
    const intentText = (e.intent || "").replace(/^\[[a-z_]+\]\s*/, "");
    intentWrap.appendChild(el("span", { text: intentText }));
    if (isH2A) {
      const dcls = e.decision === "ACCEPT" ? "acc" : e.decision === "REJECT" ? "rej" : "";
      intentWrap.appendChild(el("span", { class: "demo-entry-decision " + dcls, text: e.decision }));
      if (e.decision_mode)
        intentWrap.appendChild(el("span", {
          class: "demo-entry-mode",
          text: e.decision_mode === "pre_aligned_glance" ? t("demo.mode.glance") : t("demo.mode.cold")
        }));
      if (e.reason)
        intentWrap.appendChild(el("div", { class: "demo-entry-reason", text: "「" + e.reason + "」" }));
    }
    if (e.correction)
      intentWrap.appendChild(el("span", { class: "demo-entry-tag tag-corr", text: t("demo.tag.correction") }));
    if (isFeed)
      intentWrap.appendChild(el("span", { class: "demo-entry-tag tag-feed", text: t("demo.tag.deposited", { n: e.written }) }));
    if (e.skill)
      intentWrap.appendChild(el("span", { class: "demo-entry-tag tag-skill", text: "⚡" + e.skill }));
    const ws = _matchWorkspace(e.intent || "", workspace);
    if (ws) {
      const link = el("button", {
        class: "demo-entry-output mgmt-inline-link",
        text: t("demo.output.open", { name: ws.name }),
        onclick: () => _showOutput(body, ws)
      });
      intentWrap.appendChild(link);
    }
    row.appendChild(intentWrap);
    return row;
  }
  function _showOutput(body, ws) {
    const existing = body.querySelector(".demo-output-pop");
    if (existing) existing.remove();
    const pop = el(
      "div",
      { class: "demo-output-pop" },
      el("div", { class: "demo-output-name", text: ws.name }),
      el("pre", { class: "demo-output-body", text: ws.snippet || "" }),
      el("button", {
        class: "mgmt-inline-link",
        text: t("demo.output.close"),
        onclick: () => pop.remove()
      })
    );
    body.insertBefore(pop, body.firstChild);
  }
  function _dayCard(day, workspace, body) {
    const card = el("div", { class: "demo-day-card" });
    const head = el("div", { class: "demo-day-head" });
    head.appendChild(el("span", { class: "demo-day-num", text: "Day " + (day.day || "") }));
    head.appendChild(el("span", { class: "demo-day-label", text: day.day_label || "" }));
    const eff = el("span", { class: "demo-day-eff" });
    eff.appendChild(el("span", {
      class: "demo-day-stat",
      text: t("demo.day.handson", { n: _num(day.hands_on_turns) })
    }));
    eff.appendChild(el("span", {
      class: "demo-day-stat",
      text: t("demo.day.corr", { n: _num(day.corrections) })
    }));
    if (day.decision_modes && day.decision_modes.length)
      eff.appendChild(_modeChip(day.decision_modes));
    head.appendChild(eff);
    card.appendChild(head);
    const list = el("div", { class: "demo-day-entries" });
    for (const e of day.entries || []) list.appendChild(_entryRow(e, workspace, body));
    card.appendChild(list);
    return card;
  }
  function _collapsible(title, count, buildBody) {
    const wrap = el("div", { class: "demo-fold" });
    const bodyEl = el("div", { class: "demo-fold-body" });
    let built = false;
    const head = el(
      "button",
      { class: "demo-fold-head" },
      el("span", { class: "demo-fold-caret", text: "▸" }),
      el("span", { text: title }),
      count ? el("span", { class: "demo-fold-count", text: String(count) }) : null
    );
    head.addEventListener("click", () => {
      const open2 = wrap.classList.toggle("open");
      head.querySelector(".demo-fold-caret").textContent = open2 ? "▾" : "▸";
      if (open2 && !built) {
        bodyEl.appendChild(buildBody());
        built = true;
      }
    });
    wrap.appendChild(head);
    wrap.appendChild(bodyEl);
    return wrap;
  }
  function _prefsBody(d) {
    const box = el("div");
    for (const p of d.decision_prefs || []) {
      box.appendChild(el(
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
    return box;
  }
  function _skillsBody(d) {
    const box = el("div");
    const userSkills = (d.skills || []).filter((s) => s.source !== "system");
    for (const s of userSkills) {
      box.appendChild(el(
        "div",
        { class: "mc-meta" },
        el("b", { text: s.name }),
        " — ",
        el("span", { text: (s.description || "").slice(0, 160) })
      ));
    }
    const reused = (d.skills_curve || []).filter((s) => String(s.sig || "").startsWith("system:"));
    if (reused.length)
      box.appendChild(el("div", {
        class: "mc-meta",
        text: t("demo.skills.system", { names: reused.map((s) => s.name || s.sig).join(" · ") })
      }));
    return box;
  }
  function _expBody(d) {
    const box = el("div");
    for (const e of d.role_experiences || [])
      box.appendChild(el("div", { class: "mc-meta", text: `[${e.role}·${e.kind}] ${e.content}` }));
    return box;
  }
  function _knowledgeBody(d) {
    const box = el("div");
    for (const k of d.knowledge_recent || [])
      box.appendChild(el("div", { class: "mc-meta", text: "· " + k.content }));
    return box;
  }
  function _growthBody(d) {
    const tbl = el("table", { class: "demo-table" });
    tbl.appendChild(el(
      "tr",
      null,
      el("th", { text: t("demo.col.day") }),
      el("th", { text: t("demo.m.runs") }),
      el("th", { text: t("demo.m.skills") }),
      el("th", { text: t("demo.m.hit_rate") })
    ));
    for (const p of d.growth || []) {
      const rate = typeof p.hit_rate === "number" ? (p.hit_rate * 100).toFixed(0) + "%" : "—";
      tbl.appendChild(el(
        "tr",
        null,
        el("td", { text: p.day || "" }),
        el("td", { text: _num(p.runs_total) }),
        el("td", { text: _num(p.skills_total) }),
        el("td", { text: rate })
      ));
    }
    return tbl;
  }
  let _mode = "compact";
  function _applyMode() {
    document.body.classList.toggle("demo-modal-expanded", _mode === "expanded");
    document.body.classList.toggle("demo-modal-full", _mode === "full");
    const btn = document.getElementById("demo-modal-expand");
    if (btn) {
      btn.textContent = _mode === "compact" ? "⤢" : _mode === "expanded" ? "⛶" : "⤡";
      const tip = _mode === "compact" ? t("demo.expand") : _mode === "expanded" ? t("demo.full") : t("demo.collapse");
      btn.setAttribute("title", tip);
      btn.setAttribute("aria-label", tip);
    }
  }
  function _cycleMode() {
    _mode = _mode === "compact" ? "expanded" : _mode === "expanded" ? "full" : "compact";
    _applyMode();
  }
  function _clearMode() {
    _mode = "compact";
    document.body.classList.remove("demo-modal-expanded", "demo-modal-full");
  }
  function _injectExpandBtn() {
    const head = document.querySelector("#mgmt-modal .modal-head");
    const close = document.getElementById("mgmt-close");
    if (!head || document.getElementById("demo-modal-expand")) return;
    const b = document.createElement("button");
    b.className = "modal-close demo-modal-expand-btn";
    b.id = "demo-modal-expand";
    b.textContent = "⤢";
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      _cycleMode();
    });
    if (close && close.parentElement === head) head.insertBefore(b, close);
    else head.appendChild(b);
    if (close && !close.getAttribute("data-demo-cleanup")) {
      close.setAttribute("data-demo-cleanup", "1");
      close.addEventListener("click", () => _clearMode());
    }
  }
  function _renderInstance(body, d, lang) {
    const man = d.manifest || {};
    body.appendChild(_personaHeader(man, lang));
    body.appendChild(_effortHero(d));
    const tlHead = el("div", { class: "demo-timeline-head", text: t("demo.timeline.head") });
    body.appendChild(tlHead);
    const timeline = el("div", { class: "demo-timeline" });
    for (const day of d.timeline || [])
      timeline.appendChild(_dayCard(day, d.workspace || {}, body));
    body.appendChild(timeline);
    const folds = el("div", { class: "demo-folds" });
    folds.appendChild(el("div", { class: "demo-folds-head", text: t("demo.folds.head") }));
    folds.appendChild(_collapsible(
      t("demo.prefs.head", { n: (d.decision_prefs || []).length }),
      (d.decision_prefs || []).length,
      () => _prefsBody(d)
    ));
    const userSkills = (d.skills || []).filter((s) => s.source !== "system");
    folds.appendChild(_collapsible(
      t("demo.skills.head", { n: userSkills.length }),
      userSkills.length,
      () => _skillsBody(d)
    ));
    if ((d.role_experiences || []).length)
      folds.appendChild(_collapsible(
        t("demo.exp.head", { n: d.role_experiences.length }),
        d.role_experiences.length,
        () => _expBody(d)
      ));
    folds.appendChild(_collapsible(
      t("demo.knowledge.head", { n: d.knowledge_total || 0 }),
      d.knowledge_total || 0,
      () => _knowledgeBody(d)
    ));
    folds.appendChild(_collapsible(t("demo.growth.head"), (d.growth || []).length, () => _growthBody(d)));
    body.appendChild(folds);
    body.appendChild(_disclosureFootnote(man, lang));
    body.appendChild(el("div", { class: "mgmt-hint demo-readonly", text: t("demo.readonly.note") }));
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
    document.body.classList.add("demo-modal-open");
    _injectExpandBtn();
    _applyMode();
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
