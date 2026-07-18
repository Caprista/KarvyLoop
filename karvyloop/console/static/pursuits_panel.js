var KarvyPursuitsPanelBundle = (function(exports) {
  "use strict";
  // 🎯 我的追求(Pursuit 招牌第二刀,docs/88 §7):跨多天自己推进的持久目标的用户可见面。
  // 只消费 routes_pursuit 的三个端点(POST /api/pursuit、GET /api/pursuits、GET /api/pursuit/{id});
  // 名词预算:用户可见文案只说「完成判据 / 待你拍板 / 承诺」,不露 verify_gate/H2A/commit 内部词。
  // 安全:后端返回的一切文本都经 el() 的 textContent 落 DOM,绝不 innerHTML 拼接。
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);

  function _fmtWhen(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1e3).toLocaleString();
    } catch {
      return "—";
    }
  }

  // 状态人话化:active=卡还挂着等承诺 / committed=机器在推进 / revised=等你定新方向 /
  // done=完成判据真过了 / dropped=放弃;suspended 是叠加态(到硬地板被暂停,等你拍板)。
  const _STATUS_KEYS = {
    active: "pursuit.st.active",
    committed: "pursuit.st.committed",
    revised: "pursuit.st.revised",
    done: "pursuit.st.done",
    dropped: "pursuit.st.dropped",
  };
  function _statusBadge(rec) {
    const st = rec.status || "";
    const key = _STATUS_KEYS[st];
    const label = key ? t(key) : st;
    const cls = st === "done" ? "confirmed" : "provisional";
    return el("span", { class: "dpref-badge " + cls, text: label });
  }

  // 完成判据的人话描述(从 verify_gate 白名单字段派生,值经 textContent 落 DOM)
  function _gateDesc(gate) {
    const g = gate || {};
    if (g.type === "test_pass" && g.cmd) return t("pursuit.gate_desc.test_pass", { cmd: g.cmd });
    if (g.type === "file_exists" && g.path) return t("pursuit.gate_desc.file_exists", { path: g.path });
    return "";
  }

  // ---- 创建表单(最简:目标一句话 + 判据二选一 + 对应输入框)----
  // initialNote:重渲后仍要给人看的回执(如"已创建,等你在决策卡上承诺"——整面板重画会吞掉
  // 就地写的提示,所以经 renderPursuitsPanel(notice) 穿过重渲染带回来)。
  function _buildCreateForm(onCreated, initialNote) {
    const wrap = el("div", { class: "mgmt-buysugar" });
    wrap.appendChild(el("div", { class: "mgmt-hint", text: t("pursuit.create_head") }));
    const stmt = el("input", { class: "pursuit-input", type: "text",
      placeholder: t("pursuit.stmt_ph") });
    const gateRow = el("div", { class: "pursuit-gate-row" });
    const gateSel = el("select", { class: "pursuit-input pursuit-gate-sel" });
    gateSel.appendChild(el("option", { value: "test_pass", text: t("pursuit.gate_test") }));
    gateSel.appendChild(el("option", { value: "file_exists", text: t("pursuit.gate_file") }));
    const gateInp = el("input", { class: "pursuit-input", type: "text",
      placeholder: t("pursuit.gate_cmd_ph") });
    gateSel.addEventListener("change", () => {
      gateInp.placeholder = gateSel.value === "file_exists"
        ? t("pursuit.gate_path_ph") : t("pursuit.gate_cmd_ph");
    });
    gateRow.appendChild(gateSel);
    gateRow.appendChild(gateInp);
    const note = el("div", { class: "mgmt-hint pursuit-note" });
    if (initialNote) note.textContent = initialNote;
    const btns = el("div", { class: "dpref-actions" });
    const createBtn = el("button", {
      class: "dpref-confirm",
      text: t("pursuit.create"),
      onclick: async () => {
        const statement = (stmt.value || "").trim();
        const gv = (gateInp.value || "").trim();
        if (!statement) { note.textContent = t("pursuit.need_stmt"); return; }
        if (!gv) {
          note.textContent = gateSel.value === "file_exists"
            ? t("pursuit.need_path") : t("pursuit.need_cmd");
          return;
        }
        const gate = gateSel.value === "file_exists"
          ? { type: "file_exists", path: gv }
          : { type: "test_pass", cmd: gv };
        createBtn.disabled = true;
        const r = await _postJSON("/api/pursuit", { statement: statement, verify_gate: gate });
        createBtn.disabled = false;
        if (r.ok && r.data && r.data.ok) {
          stmt.value = "";
          gateInp.value = "";
          if (onCreated) onCreated();   // 重渲列表;"已创建,等你在决策卡上承诺"回执由重渲带回
        } else {
          // 后端 reason 已是人话(它自己的 i18n);textContent 落 DOM,不解析
          const reason = (r.data && r.data.reason) || "";
          note.textContent = t("pursuit.create_fail", { reason: reason });
        }
      }
    });
    btns.appendChild(createBtn);
    wrap.appendChild(stmt);
    wrap.appendChild(gateRow);
    wrap.appendChild(btns);
    wrap.appendChild(note);
    return wrap;
  }

  // ---- 列表 ----
  async function renderPursuitsPanel(notice) {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("pursuit.subtitle") }));
    body.appendChild(_buildCreateForm(() => renderPursuitsPanel(t("pursuit.created")), notice));
    const data = await _getJSON("/api/pursuits");
    const list = (data && data.pursuits) || [];
    if (!list.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("pursuit.empty") }));
      return;
    }
    const wrap = el("div", { class: "mgmt-list" });
    for (const p of list) {
      const badges = [_statusBadge(p)];
      if (p.suspended) badges.push(el("span", { class: "dpref-badge provisional", text: "⏸ " + t("pursuit.suspended") }));
      const metaBits = [t("pursuit.advances", { n: p.advances || 0 }),
        t("pursuit.updated", { when: _fmtWhen(p.updated_ts) })].join(" · ");
      const card = el(
        "div",
        { class: "mgmt-card pursuit-card", role: "button", tabindex: "0" },
        el(
          "div",
          { class: "mc-main" },
          el("div", { class: "mc-name" },
            el("span", { text: "🎯 " + (p.title || p.statement || p.id) }),
            ...badges),
          _gateDesc(p.verify_gate) ? el("div", { class: "mc-meta", text: _gateDesc(p.verify_gate) }) : null,
          el("div", { class: "mc-meta", text: metaBits }),
          p.progress_note ? el("div", { class: "mc-meta", text: t("pursuit.progress", { note: p.progress_note }) }) : null,
        ),
      );
      card.addEventListener("click", () => renderPursuitDetail(p.id));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); renderPursuitDetail(p.id); }
      });
      wrap.appendChild(card);
    }
    body.appendChild(wrap);
  }

  // ---- 详情:目标全文 + 状态 + 判据 + 派生 task(时间倒序)----
  async function renderPursuitDetail(pursuitId) {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("button", { class: "mgmt-inline-link", text: t("pursuit.back"),
      onclick: () => renderPursuitsPanel() }));
    const r = await _getJSON("/api/pursuit/" + encodeURIComponent(pursuitId));
    if (!r || !r.ok || !r.pursuit) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("pursuit.load_fail") }));
      return;
    }
    const p = r.pursuit;
    const head = el("div", { class: "mgmt-card" },
      el("div", { class: "mc-main" },
        el("div", { class: "mc-name" },
          el("span", { text: "🎯 " + (p.title || p.id) }),
          _statusBadge(p),
          p.suspended ? el("span", { class: "dpref-badge provisional", text: "⏸ " + t("pursuit.suspended") }) : null),
        el("div", { class: "mc-meta", text: p.statement || "" }),
        _gateDesc(p.verify_gate) ? el("div", { class: "mc-meta", text: _gateDesc(p.verify_gate) }) : null,
        el("div", { class: "mc-meta", text: t("pursuit.advances", { n: p.advances || 0 })
          + " · " + t("pursuit.updated", { when: _fmtWhen(p.updated_ts) }) }),
        p.progress_note ? el("div", { class: "mc-meta", text: t("pursuit.progress", { note: p.progress_note }) }) : null,
        p.revision_reason ? el("div", { class: "mc-meta", text: t("pursuit.revision", { reason: p.revision_reason }) }) : null,
      ));
    body.appendChild(head);
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("pursuit.tasks_head") }));
    const tasks = (p.tasks || []).slice().sort(
      (a, b) => ((b.finished || b.started || 0) - (a.finished || a.started || 0)));
    if (!tasks.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("pursuit.tasks_empty") }));
      return;
    }
    const wrap = el("div", { class: "mgmt-list" });
    for (const tk of tasks) {
      const stLbl = tk.status === "running" ? t("task.running")
        : tk.status === "error" ? "⚠ " + t("task.error") : t("task.done");
      wrap.appendChild(el("div", { class: "mgmt-card" },
        el("div", { class: "mc-main" },
          el("div", { class: "mc-name" },
            el("span", { text: (tk.intent || tk.id || "?") }),
            el("span", { class: "dpref-badge " + (tk.status === "done" ? "confirmed" : "provisional"), text: stLbl })),
          el("div", { class: "mc-meta", text: _fmtWhen(tk.finished || tk.started) }),
          tk.result ? el("div", { class: "mc-meta", text: String(tk.result).slice(0, 200) }) : null,
        )));
    }
    body.appendChild(wrap);
  }

  async function open() {
    openMgmtModal(t("pursuit.title"));
    await renderPursuitsPanel();
  }
  const KarvyPursuitsPanel = { open };
  window.KarvyPursuitsPanel = KarvyPursuitsPanel;
  exports.KarvyPursuitsPanel = KarvyPursuitsPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
