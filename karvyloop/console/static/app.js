/* KarvyLoop Console — kanban vanilla JS client (M3+ 批 8.5-C-frontend)
 * 借 Q5:不引框架;diff-patch DOM 即可
 */
(function () {
  "use strict";

  // ============ i18n (纯表现层;默认 en,可切 zh)============
  var T = window.KarvyI18n;
  function t(key, vars) { return T.t(key, vars); }
  // 后端中文 reason/detail 透传 → 双语(P2-c;zh 原样,en 查表译,查不到诚实回退原文)
  function tB(text) { return (T.tBackend ? T.tBackend(text) : text); }

  // ============ 叶子工具 + 模态基建(已迁 TS,源 frontend/src/{dom,modal}.ts)============
  // 裸名重绑到全局 → 下面成百上千处调用点一行不改(el 760+、模态基建 ~100)。
  var _KDom = window.KarvyDom;
  var el = _KDom.el, _getJSON = _KDom.getJSON, _postJSON = _KDom.postJSON;
  var _KModal = window.KarvyModal;
  var openMgmtModal = _KModal.openMgmtModal, closeMgmtModal = _KModal.closeMgmtModal,
      mgmtBody = _KModal.mgmtBody, _formMsg = _KModal.formMsg, _setMsg = _KModal.setMsg;

  // ============ WS client (auto-reconnect) ============

  let ws = null;
  let wsReconnectDelay = 1000;

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      console.log("[ws] connected");
      wsReconnectDelay = 1000;
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        handleServerMessage(msg);
      } catch (e) {
        console.error("[ws] bad message", e);
      }
    };
    ws.onclose = () => {
      console.log("[ws] disconnected, reconnecting in", wsReconnectDelay);
      setTimeout(connectWS, wsReconnectDelay);
      wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
    };
    ws.onerror = (e) => {
      console.error("[ws] error", e);
    };
  }

  function sendWS(type, payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type, payload }));
      return true;
    }
    return false;
  }

  // ============ Snapshot poller (safety net) ============

  let snapshotInterval = null;
  async function pollSnapshot() {
    try {
      const r = await fetch("/api/snapshot");
      if (!r.ok) return;
      const snap = await r.json();
      renderSnapshot(snap);
    } catch (e) {
      console.warn("[poll] snapshot failed", e);
    }
  }
  async function pollStats() {
    try {
      const r = await fetch("/api/stats");
      if (!r.ok) return;
      const s = await r.json();
      renderStats(s);
    } catch (e) {
      console.warn("[poll] stats failed", e);
    }
  }
  async function pollChatHistory() {
    try {
      const r = await fetch("/api/chat_history");
      if (!r.ok) return;
      const lines = await r.json();
      renderChatHistory(lines);
    } catch (e) {
      console.warn("[poll] chat_history failed", e);
    }
  }

  function startPolling() {
    if (snapshotInterval) return;
    // 9.4b:聊天**不轮询**(业界做法 — 事件驱动增量追加,不重建 log)。
    // 只有看板/统计这类小列表周期刷新;它们没有选中/滚动包袱。chat 由 WS drive_done 实时追加。
    snapshotInterval = setInterval(() => {
      pollSnapshot();
      pollStats();
      pollTasks();   // 9.5 P2:任务看板(料/谁在忙)
      pollKnowledge();  // 抽屉:又懂了你
      window.KarvyTokens.pollMeter();     // ch4:token 成本表(已迁 TS)
    }, 2000);
    pollKnowledge();    // 首屏立即拉一次
    window.KarvyTokens.pollMeter();
  }

  // 9.4b:用户已滚到底部附近才自动跟随;上滚看历史时**绝不**强拉到底。
  function isNearBottom(log) {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  }

  // Drawflow 画布**按需加载**:那 70KB(Drawflow+往返逻辑)只在点「编辑画布」时才拉,
  // 不再压在每次页面加载里(Hardy 报"比之前卡" → 可视化 workflow 的包不该常驻)。注入一次后缓存。
  let _wfCanvasLoading = null;
  function _ensureWorkflowCanvas() {
    if (window.KarvyWorkflowCanvas) return Promise.resolve();
    if (_wfCanvasLoading) return _wfCanvasLoading;
    _wfCanvasLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/static/workflow_canvas.js";
      s.onload = () => (window.KarvyWorkflowCanvas ? resolve() : reject(new Error("canvas global missing")));
      s.onerror = () => { _wfCanvasLoading = null; reject(new Error("workflow_canvas.js load failed")); };
      document.head.appendChild(s);
    });
    return _wfCanvasLoading;
  }

  // ============ Server message handler ============

  function handleServerMessage(msg) {
    if (msg.type === "snapshot") {
      renderSnapshot(msg.payload);
    } else if (msg.type === "drive_event") {
      // P4 逐字流式:drive 进行中的增量事件 → 实时追加(终态 drive_done 会清掉草稿、渲染权威版)
      onDriveEvent(msg.payload);
    } else if (msg.type === "drive_done") {
      // 9.4b:WS 实时追加即为权威渲染,不再回拉 chat_history 重建(那会抢选中/强制滚动)
      _clearLiveStream();   // P4:清掉逐字流式草稿 → renderDriveDone 渲染权威终态(含 markdown/高亮)
      openChatModal();   // step5:回复到了 → 弹起对话窗(含后台/主动回复)
      renderDriveDone(msg.payload);
    } else if (msg.type === "h2a_proposal") {
      // ch4 预判:小卡主动建议按 kind 分流 —— 真决策(派活/解冲突)→【要我拍什么板】;
      // 其余(习惯预判"你可能想做",含 crystallize_skill/run_task)→【你可能想做】预判列。
      _routeProposal(msg.payload);
    } else if (msg.type === "h2a_envelope") {
      console.log("[h2a] envelope", msg.payload);
      // D5:回显兑现结果(让 ACCEPT 不再是"空响应")
      const d = msg.payload && msg.payload.dispatch;
      if (d) pushChatLine("system", t("proposal.dispatch", { kind: d.kind, detail: tB(d.detail) }));
      // 决策已发 → **只撤刚拍的那张卡**(带 proposal_id),保留还挂着的兄弟卡(多卡不覆盖);
      // 若兑现产了执行后回报卡,就地追加"它到底验过没";列真空了才回填"已处置"空态。
      const list = document.getElementById("h2a-list");
      if (list) {
        const pid = (msg.payload && (msg.payload.proposal_id || (d && d.proposal_id))) || "";
        _removeCardById(list, pid);
        if (msg.payload && msg.payload.report_card) _renderReportCard(list, msg.payload.report_card);
        if (_countCards("h2a-list", "h2a-empty") === 0) {
          list.innerHTML = '<div class="h2a-empty">' + t("h2a.handled") + '</div>';
        }
      }
      updatePulse();   // step5:拍板清空 → 刷脉搏
      pollSnapshot();
      fetchRecentDecisions();   // 拍完即刷"最近拍板"回看流水
    } else if (msg.type === "envelope_arrived") {
      pollSnapshot();
    } else if (msg.type === "task_status") {
      // §0.7 fail-loud:任务状态 = 事件(push),不靠 2s 轮询碰巧发现
      onTaskStatus(msg.payload);
    } else if (msg.type === "task_step") {
      // §0.7 P2:workflow/圆桌步级进度,实时看哪步在跑/挂了
      onTaskStep(msg.payload);
    } else if (msg.type === "system_error") {
      // §0.7:后台 fire-and-forget 任务失败 → 主动冒泡,灭静默死角
      onSystemError(msg.payload);
    } else if (msg.type === "error") {
      console.warn("[server] error", msg.payload);
    }
    // ignore unknown / pong
  }

  // ============ §0.7:决策 loop 的 fail-loud + push 处理 ============
  // 步级进度按 task 暂存(2s 轮询会重建看板,从这里读回,避免被刷掉)
  const _taskSteps = new Map();   // taskId → [{display, status}]
  function onTaskStatus(tk) {
    if (!tk || !tk.id) return;
    if (tk.status !== "running") _taskSteps.delete(tk.id);  // 终态 → 清步级缓存
    pollTasks();   // push 触发的即时刷新(权威数据走 /api/tasks;2s 轮询仍兜底)
    if (tk.status === "error") {
      // 失败必须看得见:即便没盯着看板,也冒一条系统提示
      pushChatLine("system", t("task.failed_notice", { who: _localizeWho(tk.who), err: tk.result || "" }));
      updatePulse();
    }
  }
  function onTaskStep(st) {
    if (!st || !st.task_id) return;
    const arr = _taskSteps.get(st.task_id) || [];
    arr.push({ display: st.display || "?", status: st.status || "done" });
    _taskSteps.set(st.task_id, arr);
    renderTaskBoardCached();   // 实时把这步画进"谁在忙"卡
    if (st.status === "failed") {
      pushChatLine("system", t("task.step_failed", { who: st.display || "?", err: st.error || "" }));
    }
  }
  function onSystemError(p) {
    if (!p) return;
    pushChatLine("system", t("system.bg_error", { source: p.source || "?", err: p.message || "" }));
    // L1 自愈:出错就给一个"🩺 诊断"入口 —— 用活着的模型把问题翻成人话 + 提修法(只提议不执行)
    const log = document.getElementById("chat-log");
    if (!log) return;
    const btn = el("button", { class: "ops-diagnose-btn", text: t("ops.diagnose_btn"),
      onClick: async () => {
        btn.disabled = true; btn.textContent = t("ops.diagnosing");
        try {
          const d = await _getJSON("/api/ops/diagnose");
          if (d && d.diagnosis) window.KarvyDiagnosePanel.renderOpsDiagnosis(log, d.diagnosis);
          else if (d && d.healthy) pushChatLine("system", t("ops.healthy"));
          else if (d && d.reason === "no_model") pushChatLine("system", t("ops.no_model"));
          else pushChatLine("system", t("ops.failed"));
        } catch (e) { pushChatLine("system", t("ops.failed")); }
        btn.remove();
      } });
    log.appendChild(btn);
  }
  // 🩺 诊断/运维面板已迁 TS(源 frontend/src/diagnose_panel.ts)→ window.KarvyDiagnosePanel.open(deps)。
  // 跨面板依赖(pushChatLine / fetchPendingProposals 还在 app.js)经 open(deps) 注入;诊断卡渲染
  // (onSystemError 也复用)= window.KarvyDiagnosePanel.renderOpsDiagnosis。
  function openDiagnosePanel() {
    return window.KarvyDiagnosePanel.open({ pushChatLine, fetchPendingProposals });
  }
  // ⏰ 定时任务面板已迁 TS(源 frontend/src/schedules_panel.ts)→ window.KarvySchedulesPanel.open()

  // 📁 文件管理面板已迁 TS(源 frontend/src/files_panel.ts)→ window.KarvyFilesPanel.open()
  // 用最近一次 /api/tasks 数据重画(供步级事件即时刷新,不等下个轮询周期)
  let _lastTasks = [];
  function renderTaskBoardCached() { renderTaskBoard(_lastTasks); }

  // ============ P4 逐字流式:drive 进行中实时追加(终态 drive_done 清草稿、渲染权威版) ============
  let _liveStreamEl = null;
  function _ensureLiveStream() {
    if (_liveStreamEl) return _liveStreamEl;
    const log = document.getElementById("chat-log");
    if (!log) return null;
    openChatModal();
    _liveStreamEl = el("div", { class: "chat-line agent live-stream" });
    log.appendChild(_liveStreamEl);
    log.scrollTop = log.scrollHeight;
    return _liveStreamEl;
  }
  function onDriveEvent(ev) {
    if (!ev) return;
    const log = document.getElementById("chat-log");
    if (ev.type === "text_delta" && ev.text) {
      const box = _ensureLiveStream(); if (!box) return;
      const follow = log ? isNearBottom(log) : false;
      box.textContent += ev.text;             // 逐字追加(纯文本=安全;终态再 markdown+高亮)
      if (follow && log) log.scrollTop = log.scrollHeight;
    } else if (ev.type === "tool_call") {
      const box = _ensureLiveStream(); if (!box) return;
      box.appendChild(el("div", { class: "live-tool", text: "🔧 " + (ev.name || "tool") }));
      if (log) log.scrollTop = log.scrollHeight;
    } else if (ev.type === "thinking_delta") {
      // P4:推理中 → 草稿里显一次"💭 思考中…"(完整推理在终态折叠卡);不逐字铺(太吵)
      const box = _ensureLiveStream(); if (!box) return;
      if (!box.querySelector(".live-thinking")) {
        box.appendChild(el("div", { class: "live-thinking", text: t("render.thinking_live") }));
        if (log) log.scrollTop = log.scrollHeight;
      }
    }
    // tool_result / terminal:终态渲染处理,流式不逐条画
  }
  function _clearLiveStream() {
    if (_liveStreamEl && _liveStreamEl.parentNode) _liveStreamEl.parentNode.removeChild(_liveStreamEl);
    _liveStreamEl = null;
  }

  // 开机拉取待决提案:待你拍的板跨刷新/切语言存活(决策 loop 不让人问"怎么样了")。
  // WS 实时推只覆盖"在线时新来的";本 fetch 覆盖"刷新前就挂着的"(含 DEFER 挂起的)。
  async function fetchPendingProposals() {
    try {
      const r = await fetch("/api/proposals/pending");
      if (!r.ok) return;
      const data = await r.json();
      (data.proposals || []).forEach((p) => _routeProposal(p));   // 按 kind 分流(拍板/预判)
    } catch (e) {
      console.warn("[boot] pending proposals failed", e);
    }
  }

  // 版本检测:有新版 → 顶部可关掉的横幅(detect→notify→你按下,绝不自动升级)。
  // 关掉后按版本记进 localStorage,同一版本不再骚扰(notify ≠ nag)。
  async function fetchUpdateStatus() {
    try {
      const r = await fetch("/api/update_status");
      if (!r.ok) return;
      const u = await r.json();
      // E(升级预检+回滚):上次升级失败已自动回滚 → 大声横幅告知(fail-loud,不静默);
      // 可回滚且刚升过 → 横幅带「回滚到上一版」按钮(一键后悔药)。
      const lu = u && u.last_upgrade;
      if (lu && lu.rolled_back && !document.getElementById("update-banner")) {
        const bar = el("div", { class: "update-banner update-banner-err", id: "update-banner" });
        bar.appendChild(el("span", { class: "update-banner-msg",
          text: t("update.rolled_back", { reason: lu.rollback_reason || lu.msg || "" }) }));
        bar.appendChild(el("button", { class: "update-x", text: "✕", onClick: () => bar.remove() }));
        document.body.insertBefore(bar, document.body.firstChild);
      }
      if (!u || !u.newer || !u.latest) return;
      let dismissed = null;
      try { dismissed = localStorage.getItem("karvyloop_update_dismissed"); } catch (e) {}
      if (dismissed === u.latest) return;   // 这个版本已忽略过 → 不再提示
      _showUpdateBanner(u);
    } catch (e) {
      /* 检测失败静默(本地优先,不打扰) */
    }
  }
  function _showUpdateBanner(u) {
    if (document.getElementById("update-banner")) return;
    const bar = el("div", { class: "update-banner", id: "update-banner" });
    bar.appendChild(el("span", { class: "update-banner-msg",
      text: t("update.banner", { current: u.current, latest: u.latest }) }));
    // 一键升级:点了才升(=手动,不是静默自动);点完后端跑 停→装→起 整套,不用敲命令
    bar.appendChild(el("button", { class: "update-go", text: t("update.upgrade_btn"),
      onClick: (e) => _doUpgrade(u, e.target) }));
    if (u.rollback_available) {
      bar.appendChild(el("button", { class: "update-rollback", text: t("update.rollback_btn", { prev: u.prev_version || "?" }),
        onClick: async (e) => {
          if (!confirm(t("update.rollback_confirm", { prev: u.prev_version || "?" }))) return;
          e.target.disabled = true;
          try {
            const rr = await fetch("/api/update/rollback", { method: "POST",
              headers: { "X-Karvyloop-Upgrade": "1" } });
            const dd = await rr.json();
            if (dd && dd.ok === false) { alert(t("update.upgrade_failed", { reason: dd.reason || "" })); e.target.disabled = false; return; }
          } catch (err) { /* 服务重启中 → 轮询 */ }
          _pollUpgrade(u.prev_version || "", 0);
        } }));
    }
    if (u.command) bar.appendChild(el("code", { class: "update-cmd", text: u.command }));
    if (u.url) bar.appendChild(el("a", { class: "update-link", href: u.url,
      target: "_blank", rel: "noopener", text: t("update.banner_notes") }));
    bar.appendChild(el("button", { class: "update-x", text: "✕", onClick: () => {
      try { localStorage.setItem("karvyloop_update_dismissed", u.latest); } catch (e) {}
      bar.remove();
    } }));
    document.body.insertBefore(bar, document.body.firstChild);
  }
  async function _doUpgrade(u, btn) {
    if (!confirm(t("update.upgrade_confirm", { current: u.current, latest: u.latest }))) return;
    btn.disabled = true;
    const msg = document.querySelector("#update-banner .update-banner-msg");
    if (msg) msg.textContent = t("update.upgrading");
    try {
      // 带自定义头(防 CSRF:恶意跨源网页 POST 会因 preflight 被挡)
      const r = await fetch("/api/update/apply", { method: "POST",
        headers: { "X-Karvyloop-Upgrade": "1" } });
      const d = await r.json();
      if (d && d.ok === false) {
        const failed = t("update.upgrade_failed", { reason: d.reason || "" });
        if (msg) { msg.textContent = failed; const b = document.getElementById("update-banner"); if (b) b.classList.add("update-banner-err"); }
        alert(failed);   // 醒目:别让拒绝原因(如"只能本机/局域网升级")埋在薄横幅里被错过(没反应的根因)
        btn.disabled = false; return;
      }
    } catch (e) { /* console 可能正在重启 → 直接进轮询 */ }
    _pollUpgrade(u.latest, 0);   // 服务会重启:轮询到新版起来再自动刷新
  }
  async function _pollUpgrade(latest, tries) {
    const msg = document.querySelector("#update-banner .update-banner-msg");
    if (tries > 150) {           // ~5 分钟还没起来 → 提示看日志,别死等
      if (msg) msg.textContent = t("update.upgrade_timeout");
      return;
    }
    try {
      const r = await fetch("/api/update_status", { cache: "no-store" });
      if (r.ok) {
        const u = await r.json();
        if (u && String(u.current) === String(latest)) { location.reload(); return; }
        // 重启回来但版本没变 + 有失败状态 → 升级没成,停轮询、提示看日志(别假装"还在升级")
        const lu = u && u.last_upgrade;
        if (lu && lu.restarted && lu.ok === false) {
          if (msg) msg.textContent = t("update.upgrade_failed", { reason: lu.msg || "see upgrade.log" });
          return;
        }
      }
    } catch (e) { /* 重启窗口里连不上是正常的 */ }
    setTimeout(() => _pollUpgrade(latest, tries + 1), 2000);
  }

  // 最近拍板流水(只读):拍完卡会从待决列消失,这里留下回看(不可改,拍过的是事实)。
  const _DECISION_BADGE = { ACCEPT: "✅", REJECT: "✖", DEFER: "🕒" };
  function _relTime(ts) {
    if (!ts) return "";
    const sec = Math.max(0, Date.now() / 1000 - ts);
    if (sec < 60) return t("time.just_now");
    if (sec < 3600) return t("time.min_ago", { n: Math.floor(sec / 60) });
    if (sec < 86400) return t("time.hr_ago", { n: Math.floor(sec / 3600) });
    return t("time.day_ago", { n: Math.floor(sec / 86400) });
  }
  async function fetchRecentDecisions() {
    const list = document.getElementById("recent-decisions");
    if (!list) return;
    try {
      const r = await fetch("/api/decisions/recent?limit=10");
      if (!r.ok) return;
      const data = await r.json();
      const items = data.decisions || [];
      if (!items.length) {
        list.innerHTML = '<div class="empty-state">' + t("empty.recent_decisions") + "</div>";
        return;
      }
      list.innerHTML = "";
      items.forEach((d) => {
        const row = el("div", { class: "recent-row" });
        row.appendChild(el("span", { class: "recent-badge recent-" + (d.decision || "").toLowerCase(),
          text: _DECISION_BADGE[d.decision] || "·" }));
        row.appendChild(el("span", { class: "recent-summary", text: d.summary || d.proposal_id || "" }));
        row.appendChild(el("span", { class: "recent-time", text: _relTime(d.ts) }));
        list.appendChild(row);
      });
    } catch (e) {
      console.warn("[recent-decisions] fetch failed", e);
    }
  }

  // ============ 决策卡:执行→可判断的翻译层 + 逼判断闸 ============

  // 拉一张决策卡并渲染进 container:已核验区(接地✓/✗)/ 小卡复述区(标未核验)/ 逐条认改删。
  // engaged(改或删过任一依据)写回 judgeState —— 决定回喂 + 反投降是否计数。
  function _renderDecisionCard(container, proposalId, judgeState) {
    fetch("/api/decision_card?proposal_id=" + encodeURIComponent(proposalId))
      .then((r) => r.json())
      .then((res) => {
        if (!res || !res.ok || !res.card) return;   // 没卡 = 沉默,不打扰
        const c = res.card;
        // 把"逼判断"所需的态记进 judgeState:决策前(decide)据此在**拍之前**拦
        judgeState.highValue = !!c.high_value;
        judgeState.hvStandard = c.high_value_standard || "";
        judgeState.needsRecheck = !!c.needs_recheck;
        const box = el("div", { class: "dcard" + (c.high_value ? " dcard-highvalue" : "") });
        // Cut 2 违背即拦:踩了你定的标准 → 拍板**之前**最显眼处标红(带回执,可核"它替我把关")
        const violations = c.violations || [];
        violations.forEach((v) => {
          const vb = el("div", { class: "dcard-violation" });
          vb.appendChild(el("div", { class: "dcard-violation-head",
            text: t("dcard.violation") + "『" + (v.standard || "") + "』" + (v.why ? " — " + v.why : "") }));
          if (v.receipt && v.receipt.length) {
            vb.appendChild(el("div", { class: "dcard-pref-receipt",
              text: t("dcard.pref_receipt") + v.receipt.join("；") }));
          }
          box.appendChild(vb);
        });
        // 反投降:已处在"连着无脑拍"streak → 拍之前先提醒(banner),ACCEPT 会再要一次确认
        if (c.needs_recheck) {
          box.appendChild(el("div", { class: "dcard-surrender", text: t("dcard.surrender_banner") }));
        }
        // resolvable 标:接地的才显「经核验」,unverifiable 显「无法自动核验」
        const resRow = el("div", { class: "dcard-resolvable dcard-" + c.resolvable,
          text: t("dcard.resolvable." + c.resolvable) });
        if (c.high_value) resRow.appendChild(el("span", { class: "dcard-hv-badge", text: t("dcard.high_value") }));
        box.appendChild(resRow);
        // 已核验区:只列接地依据(verify_gate 源),✓ passed / ✗ failed,逐条认改删
        const verified = (c.criteria || []).filter((x) => x.grounded);
        if (verified.length) {
          const vzone = el("div", { class: "dcard-zone dcard-zone-verified" });
          vzone.appendChild(el("div", { class: "dcard-zone-label", text: "✓ " + t("dcard.verified") }));
          verified.forEach((crit) => vzone.appendChild(_critRow(crit, judgeState)));
          box.appendChild(vzone);
        }
        // 小卡复述区:problem/approach —— 未核验时显眼标记(防 overtrust)
        const narr = el("div", { class: "dcard-narrated dcard-zone dcard-zone-narrated" });
        if (c.narrated_warning) {
          narr.appendChild(el("span", { class: "dcard-unverified", text: t("dcard.unverified_badge") }));
          // 教用户"已核验 vs 复述"的区别(常见情形,无 ✓ 不是 bug,是诚实)
          narr.appendChild(el("div", { class: "dcard-narrated-explain", text: t("dcard.narrated_explain") }));
        }
        if (c.problem) narr.appendChild(el("div", {},
          el("span", { class: "dcard-k", text: t("dcard.problem") + ": " }),
          el("span", { text: c.problem })));
        if (c.approach) narr.appendChild(el("div", {},
          el("span", { class: "dcard-k", text: t("dcard.approach") + ": " }),
          el("span", { text: c.approach })));
        box.appendChild(narr);
        // 🧭 你的标准(已预对齐):楔子结晶出的决策偏好,在拍板这一刻摆给你 —— 用你自己的标准帮你拍。
        // 只读(改偏好走左栏🧭决策偏好管理面);命中高价值的高亮。
        const prefs = c.aligned_prefs || [];
        if (prefs.length) {
          box.appendChild(el("div", { class: "dcard-section-label", text: t("dcard.aligned") }));
          prefs.forEach((p) => {
            const row = el("div", { class: "dcard-pref" + (p.high_value ? " dcard-pref-hv" : "") });
            row.appendChild(el("span", { class: "dcard-pref-kind", text: "[" + (p.kind_label || "") + "]" }));
            row.appendChild(el("span", { class: "dcard-pref-text", text: p.content || "" }));
            box.appendChild(row);
            // 回执:这条标准从你哪几次拍板来 —— 不是凭空的,可核(答"凭什么信你")
            if (p.receipt && p.receipt.length) {
              box.appendChild(el("div", { class: "dcard-pref-receipt",
                text: t("dcard.pref_receipt") + p.receipt.join("；") }));
            }
          });
          // 不静默漏:适用标准超出展示数 → 明示还有几条(已按相关性挑了最相关的)
          if (c.aligned_omitted > 0) {
            box.appendChild(el("div", { class: "dcard-aligned-omitted",
              text: t("dcard.aligned_omitted").replace("{n}", String(c.aligned_omitted)) }));
          }
          box.appendChild(el("div", { class: "dcard-aligned-hint", text: t("dcard.aligned_hint") }));
        }
        // unverifiable 卡没有接地依据可 认/改/删 → 给"你的判断依据"输入:你也能真判断,
        // 而且这是喂楔子的**显式(STATE)信号**——救最常见卡(以前它永远拿不到 engaged)。
        if (c.narrated_warning) {
          box.appendChild(el("div", { class: "dcard-section-label", text: t("dcard.your_basis") }));
          const basisIn = el("textarea", { class: "dcard-basis", rows: 2 });
          basisIn.placeholder = t("dcard.your_basis_ph");
          basisIn.addEventListener("input", () => { judgeState.basis = basisIn.value; });
          box.appendChild(basisIn);
        }
        container.appendChild(box);
      })
      .catch(() => {});   // 拉卡失败不挡拍板(降级到老提案卡)
  }

  // 一条判定依据行:✓/✗ + 文本 + 认/改/删。改/删 → engaged + 记进 judgeState.edited。
  function _critRow(crit, judgeState) {
    const row = el("div", { class: "dcard-crit" + (crit.status === "failed" ? " dcard-crit-fail" : "") });
    const mark = crit.status === "passed" ? "✓" : (crit.status === "failed" ? "✗" : "·");
    const txt = el("span", { class: "dcard-crit-text", text: mark + " " + (crit.text || "") });
    row.appendChild(txt);
    const mark_edited = () => {
      judgeState.engaged = true;
      if (judgeState.edited.indexOf(crit.text) < 0) judgeState.edited.push({ text: crit.text });
    };
    const btns = el("span", { class: "dcard-crit-btns" });
    btns.appendChild(el("button", { class: "dcard-keep", text: t("dcard.crit_keep") }));
    btns.appendChild(el("button", { class: "dcard-edit", text: t("dcard.crit_edit"),
      onClick: () => {
        const nv = window.prompt(t("dcard.approach") + ":", crit.text || "");
        if (nv !== null && nv !== crit.text) { txt.textContent = mark + " " + nv; mark_edited(); }
      } }));
    btns.appendChild(el("button", { class: "dcard-drop", text: t("dcard.crit_drop"),
      onClick: () => { row.classList.add("dcard-crit-dropped"); mark_edited(); } }));
    row.appendChild(btns);
    return row;
  }

  // 真判断了吗:改/删过依据(engaged)或在卡上陈述了判断依据(basis)都算 —— 救 unverifiable 卡。
  function _engagedNow(js) { return !!js.engaged || !!((js.basis || "").trim()); }

  // 回喂判断:engaged/basis → /api/decision_card/judge。needs_recheck=true → 反投降轻确认。
  function _judgeDecisionCard(proposalId, decision, judgeState) {
    return fetch("/api/decision_card/judge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal_id: proposalId, decision: decision,
        engaged: _engagedNow(judgeState), edited_criteria: judgeState.edited || [],
        basis: (judgeState.basis || "").trim() }),   // 你陈述的判断依据 = 喂楔子的显式信号
    }).then((r) => r.json()).then((res) => {
      // 反投降的拦截已移到"拍之前"(decide 里用 card.needs_recheck 预先 confirm);
      // 这里只更新本卡 judgeState,供同卡内连续操作即时生效(无需重拉)。
      if (res) judgeState.needsRecheck = !!res.needs_recheck;
    }).catch(() => {});   // 回喂失败不挡拍板
  }

  // 执行后回报卡(只读):你 ACCEPT 的活跑完独立验收后,把"它到底验过没"翻成卡。
  // grounded ✓ 的自然产地;✓ 只来自真验收(非 inconclusive),没验过老实标"未核验"。
  function _renderReportCard(container, c) {
    const box = el("div", { class: "dcard report-card" });
    box.appendChild(el("div", { class: "report-card-head", text: t("report.title") }));
    box.appendChild(el("div", { class: "dcard-resolvable dcard-" + c.resolvable,
      text: t("dcard.resolvable." + c.resolvable) }));
    (c.criteria || []).filter((x) => x.grounded).forEach((crit) => {
      const mark = crit.status === "passed" ? "✓" : (crit.status === "failed" ? "✗" : "·");
      box.appendChild(el("div", { class: "dcard-crit" + (crit.status === "failed" ? " dcard-crit-fail" : "") },
        el("span", { class: "dcard-crit-text", text: mark + " " + (crit.text || "") })));
    });
    const narr = el("div", { class: "dcard-narrated" });
    if (c.narrated_warning) {
      narr.appendChild(el("span", { class: "dcard-unverified", text: t("dcard.unverified_badge") }));
      narr.appendChild(el("div", { class: "dcard-narrated-explain", text: t("dcard.narrated_explain") }));
    }
    if (c.problem) narr.appendChild(el("div", {},
      el("span", { class: "dcard-k", text: t("dcard.problem") + ": " }), el("span", { text: c.problem })));
    if (c.approach) narr.appendChild(el("div", {},
      el("span", { class: "dcard-k", text: t("dcard.approach") + ": " }), el("span", { text: c.approach })));
    box.appendChild(narr);
    if (c.feedback) box.appendChild(el("div", { class: "report-feedback" },
      el("span", { class: "dcard-k", text: t("report.feedback_label") + ": " }), el("span", { text: c.feedback })));
    container.appendChild(box);
  }

  // ============ 9.0e:小卡主动建议(h2a_proposal)渲染 ============

  // 成本预估(60s 缓存;样本来自 per-task 归因账本)
  let _costEstCache = null, _costEstAt = 0;
  async function _getTaskCostEstimate() {
    const now = Date.now();
    if (_costEstCache && now - _costEstAt < 60000) return _costEstCache;
    const d = await _getJSON("/api/task_cost_estimate");
    _costEstCache = d; _costEstAt = now;
    return d;
  }
  function _fmtTok(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }

  // ── 多卡不覆盖:同 proposal_id 替换、新 id 追加;清空态占位;绝不 innerHTML="" 抹掉兄弟卡 ──
  // (病根:renderProposal/renderPredict 原来每次都 innerHTML="",第二张卡一来就抹掉第一张;
  //  fetchPendingProposals 遍历所有 pending 也只剩最后一张。决策 loop 不该让待拍的板互相顶掉。)
  function _stripEmpty(list, emptyClass) {
    Array.from(list.children).forEach((ch) => {
      if (ch.classList && ch.classList.contains(emptyClass)) list.removeChild(ch);
    });
  }
  function _removeCardById(list, proposalId) {
    if (!list || !proposalId) return;
    Array.from(list.children).forEach((ch) => {
      if (ch.getAttribute && ch.getAttribute("data-proposal-id") === String(proposalId)) list.removeChild(ch);
    });
  }
  function _placeCard(list, proposalId, card) {
    card.setAttribute("data-proposal-id", String(proposalId));
    _removeCardById(list, proposalId);   // 同 id 先撤旧卡(幂等重推不叠)
    list.appendChild(card);
  }

  function renderProposal(payload) {
    const list = document.getElementById("h2a-list");
    if (!list) return;
    if (!payload) {
      // 沉默 / 未接 analyst:保持空态,不刷屏
      return;
    }
    _stripEmpty(list, "h2a-empty");   // 清空态占位,但**保留已挂的兄弟卡**(多卡不覆盖)
    const card = el("div", { class: "h2a-card" });
    card.appendChild(el("div", { class: "h2a-summary", text: "💡 " + (payload.summary || t("proposal.no_desc")) }));
    // ch4 #6.1:拍板必须带决策依据(为什么)—— 否则凭啥拍
    if (payload.basis) {
      card.appendChild(el("div", { class: "h2a-basis" },
        el("span", { class: "h2a-basis-label", text: t("proposal.basis_label") }),
        el("span", { text: payload.basis })));
    }
    // ch4:上下文跳转 —— 跳进那条任务/对话看全貌再拍
    const ctxRef = payload.context_ref || {};
    if (ctxRef.kind === "task" && ctxRef.id) {
      card.appendChild(el("button", { class: "h2a-jump", text: t("proposal.jump"),
        onClick: () => openTaskById(ctxRef.id) }));
    }
    if (typeof payload.strength === "number") {
      card.appendChild(el("div", {
        class: "h2a-strength",
        text: t("proposal.strength", { pct: Math.round(payload.strength * 100) }),
      }));
    }
    // #42 打计费黑箱:"花钱之前告诉你" —— 执行类提案带最近同类任务的真实消耗分布。
    // 诚实:样本<3 不显示;数字来自 per-task 归因账本,不是猜的。
    const _COSTLY_KINDS = ["route_to_role", "run_task", "roundtable"];
    if (_COSTLY_KINDS.indexOf(payload.kind) >= 0) {
      const costLine = el("div", { class: "h2a-cost" });
      card.appendChild(costLine);
      _getTaskCostEstimate().then((est) => {
        if (est && est.n >= 3) {
          costLine.textContent = t("proposal.cost_estimate",
            { mean: _fmtTok(est.mean), min: _fmtTok(est.min), max: _fmtTok(est.max), n: est.n });
        }
      }).catch(() => {});
    }
    const proposalId = payload.proposal_id || ("p-" + (payload.habit_id || 0));

    // 决策卡:把执行翻成「你能判断的东西」—— 已核验区(接地✓/✗)与小卡复述区分开,
    // 逐条 认/改/删。改/删过 = engaged(真判断,非 rubber-stamp)。回喂结晶 + 反投降。
    const judgeState = { engaged: false, edited: [], basis: "" };
    _renderDecisionCard(card, proposalId, judgeState);

    // #42 优化①「改了再批」:kind→可编辑的"行动文本"字段。你不只认/拒,还能亲手改到该有的样子
    // 再批 —— 修改本身是楔子最富的偏好信号(原文→改文的对照会进偏好结晶)。
    const _EDITABLE_FIELD = { route_to_role: "requirement", merge_knowledge: "merged_content",
                              merge_atoms: "merged_purpose", run_task: "intent" };
    const _editField = _EDITABLE_FIELD[payload.kind];
    const _editSrc = _editField && payload.payload && typeof payload.payload[_editField] === "string"
      ? payload.payload[_editField] : "";
    let editArea = null;   // 展开后 = textarea;拍板时若有改动随 edits 带上
    if (_editSrc) {
      const editWrap = el("div", { class: "h2a-edit-wrap" });
      const editBtn = el("button", { class: "h2a-edit-toggle", text: "✏️ " + t("proposal.edit_then_accept"),
        onClick: () => {
          if (editArea) return;
          editArea = el("textarea", { class: "h2a-edit-area" });
          editArea.value = _editSrc;
          editWrap.appendChild(editArea);
          editWrap.appendChild(el("div", { class: "h2a-edit-hint", text: t("proposal.edit_hint") }));
          editBtn.disabled = true;
        } });
      editWrap.appendChild(editBtn);
      card.appendChild(editWrap);
    }

    const btnRow = el("div", { class: "h2a-buttons" });
    // 拍板:点了就拍。REJECT 不强制 reason(Hardy:不想说为什么就能拒)——
    // reason 通过卡上可选输入框带上(填了就传,空也照拒)。K5(人拍板/by=[])与 reason 无关。
    const reasonInput = el("input", {
      class: "h2a-reason", type: "text",
      "data-i18n-ph": "proposal.reason_optional",
    });
    reasonInput.placeholder = t("proposal.reason_optional");
    const decide = (decision) => {
      // 「改了再批」:改动过 → 随 ACCEPT 带 edits。改过=亲手判断过(最强的 engaged 信号),
      // 放在逼判断闸**之前**标记 —— 改过的人不该再被闸拦。
      let _edits = null;
      if (decision === "ACCEPT" && editArea && editArea.value.trim() &&
          editArea.value.trim() !== _editSrc.trim()) {
        _edits = {}; _edits[_editField] = editArea.value.trim();
        judgeState.engaged = true;
      }
      // 逼判断闸(过度判断=没判断的反面:稀有的高价值/已投降 streak 别被橡皮图章)。
      // 在**拍之前**拦,取消=不拍;只拦"没真判断过"的 ACCEPT(改/删依据 或 陈述判断依据都算判断过)。
      if (decision === "ACCEPT" && !_engagedNow(judgeState)) {
        if (judgeState.highValue &&
            !window.confirm(t("dcard.hv_confirm", { standard: judgeState.hvStandard || "" }))) return;
        if (judgeState.needsRecheck && !window.confirm(t("dcard.surrender_confirm"))) return;
      }
      // 回喂判断(engaged + 改/删的依据)→ 反投降计数;再走既有 K5 拍板路径(不动)。
      _judgeDecisionCard(proposalId, decision, judgeState).then(() => {
        const msg = {
          proposal_id: proposalId,
          decision: decision,
          reason: (decision === "REJECT") ? (reasonInput.value || "") : "",
        };
        if (_edits) msg.edits = _edits;
        sendWS("h2a_decision", msg);
      });
    };
    btnRow.appendChild(el("button", { class: "h2a-accept", onClick: () => decide("ACCEPT"), text: t("proposal.accept") }));
    btnRow.appendChild(el("button", { class: "h2a-defer", onClick: () => decide("DEFER"), text: t("proposal.defer") }));
    btnRow.appendChild(el("button", { class: "h2a-reject", onClick: () => decide("REJECT"), text: t("proposal.reject") }));
    card.appendChild(btnRow);
    card.appendChild(reasonInput);   // 可选拒绝理由(不填也能拒)
    _placeCard(list, proposalId, card);   // 多卡不覆盖:同 id 替换、新 id 追加
    updatePulse();   // step5:拍板数变了 → 刷脉搏
  }

  // ch4 预判:主动建议按 kind 分流。**显式映射 + fail-safe 默认进【拍板】**:真决策(需你判断
  // + 可拒 + 带依据)进决策列;只有**习惯预判**(proactive 用 KIND_RUN_TASK,小卡从习惯猜你想做)
  // 进【你可能想做】。旧实现用"决策 kind 白名单",任何新 kind(merge_knowledge / merge_atoms /
  // confirm_result / crystallize_skill / set_preference / confirm_decision_pref / infeasible_report)
  // 都被误丢进预判列 —— 无拒绝按钮、丢 payload。改成"预判白名单",新 kind 一律进决策列。
  const _PREDICT_KINDS = ["run_task"];
  function _routeProposal(payload) {
    if (!payload) return;   // null = 沉默/未接,保持空态
    if (_PREDICT_KINDS.indexOf(payload.kind) >= 0) renderPredict(payload);
    else renderProposal(payload);   // 其余全部(含未来新 kind)→ 决策列
  }

  // 【你可能想做】预判卡:小卡从你的习惯预判的想做的事 —— 轻提示,你去做 / 忽略。
  function renderPredict(payload) {
    const list = document.getElementById("predict-list");
    if (!list || !payload) return;
    _stripEmpty(list, "empty-state");   // 清空态占位,保留兄弟卡(多卡不覆盖)
    const card = el("div", { class: "predict-card" });
    card.appendChild(el("div", { class: "predict-summary", text: "🔮 " + (payload.summary || t("proposal.no_desc")) }));
    if (payload.basis) card.appendChild(el("div", { class: "predict-basis", text: payload.basis }));
    const ctxRef = payload.context_ref || {};
    if (ctxRef.kind === "task" && ctxRef.id) {
      card.appendChild(el("button", { class: "predict-jump", text: t("proposal.jump"),
        onClick: () => openTaskById(ctxRef.id) }));
    }
    if (typeof payload.strength === "number") {
      card.appendChild(el("div", { class: "predict-strength",
        text: t("proposal.strength", { pct: Math.round(payload.strength * 100) }) }));
    }
    const pid = payload.proposal_id || ("p-" + (payload.habit_id || 0));
    const row = el("div", { class: "predict-buttons" });
    row.appendChild(el("button", { class: "predict-yes", text: t("predict.do"),
      onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "ACCEPT", reason: "" });
                       _clearPredict(); } }));
    row.appendChild(el("button", { class: "predict-no", text: t("predict.ignore"),
      onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "DEFER", reason: "" });
                       _clearPredict(); } }));
    card.appendChild(row);
    _placeCard(list, pid, card);   // 多卡不覆盖:同 id 替换、新 id 追加
    updatePulse();
  }
  function _clearPredict() {
    const list = document.getElementById("predict-list");
    if (list) list.innerHTML = '<div class="empty-state">' + t("empty.predict") + "</div>";
  }

  // ============ 9.2c:建业务域(像建公司)============

  async function newDomain() {
    const name = window.prompt(t("domain.name_prompt"), "");
    if (!name || !name.trim()) return;
    // 9.4d:value.md 可选 —— 留空 = 域暂无价值观(以后可补),不再因为空就中断
    const value_md = window.prompt(t("domain.value_prompt"), "");
    if (value_md === null) return;  // 仅"取消"才中断;留空照常建
    const agent = window.prompt(t("domain.agent_prompt"), "");
    if (!agent || !agent.trim()) return;
    try {
      const r = await fetch("/api/domain/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), value_md: (value_md || "").trim(), agent: agent.trim() }),
      });
      if (r.ok) {
        pushChatLine("system", t("domain.created", { name: name.trim(), agent: agent.trim() }));
        // 门2(D4):建域时检出的技能×域冲突 → 提示用户到 H2A 处置
        const body = await r.json().catch(() => ({}));
        for (const c of (body.conflicts || [])) {
          pushChatLine("system", t("domain.conflict_warn", { summary: c.summary }));
        }
        if ((body.conflicts || []).length) requestProposal && pollSnapshot();
        refreshPeers();
      } else {
        const body = await r.json().catch(() => ({}));
        pushChatLine("system", t("domain.create_failed", { err: tB(body.detail) || ("HTTP " + r.status) }));
      }
    } catch (e) {
      pushChatLine("system", t("domain.create_failed", { err: e.message }));
    }
  }

  // ============ 9.2b:场+角色 picker(私聊 / 业务域)============

  // 当前选中场的人话标签(替代旧 select 读取):标题/回复方身份用它。
  let _currentPeerLabel = "";

  // 左栏可聊对象一行行展开(私聊 Karvy / 业务域角色 / 圆桌),点谁跟谁聊 → switchPeer。
  function _peerKey(p) {
    return [p.domain_id, p.role, p.agent_id, p.is_group ? 1 : 0].join("|");
  }
  // 任务/忙列里的"谁":小卡是产品人设,跟随语言切(小卡/Karvy);用户角色名是数据,不切。
  function _localizeWho(who) { return who === "小卡" ? t("chat.karvy") : (who || "?"); }
  // 左栏显示名:已经在聊天框语境里,不写"私聊/群/域"这种废话,直接给名字。
  // 小卡/Karvy 跟随语言切换;用户自建的业务域名/角色名**不**切换(那是数据)。
  function _peerDisplayLabel(p) {
    if (p.is_private) return "🦫 " + t("chat.karvy");          // 小卡 / Karvy
    if (p.is_world) return "👥 Karvy World";                   // 概念标题,品牌名不翻
    if (p.is_group) return "👥 " + (p.domain_name || "");      // 群:只显域名(👥 已示意是群)
    const who = p.agent_id || p.role || "";                   // 角色:域 / 角色名(去掉 agent· 冗余)
    return "🏢 " + (p.domain_name || "") + (who ? " / " + who : "");
  }
  function _addPeerRow(list, peer, label, isGroup, active) {
    const row = el("div", { class: "peer-row" + (isGroup ? " is-group" : "") + (active ? " active" : "") });
    row.dataset.peer = JSON.stringify(peer);
    row.dataset.key = _peerKey(peer);
    row.appendChild(el("span", { class: "peer-nm", text: label }));   // label 自带 emoji,不另加图标
    row.addEventListener("click", () => {
      list.querySelectorAll(".peer-row").forEach((x) => x.classList.remove("active"));
      row.classList.add("active");
      _currentPeerLabel = peer.domain_id === "l0" ? "" : label;   // 小卡走 isKarvy 分支,无需标签
      switchPeer(row.dataset.peer);
    });
    list.appendChild(row);
    return row;
  }

  // 2d:分块折叠态(存 localStorage,默认展开)
  function _secCollapsed(key) {
    try { return localStorage.getItem("karvy.sec." + key) === "1"; } catch (e) { return false; }
  }
  function _toggleSec(key) {
    try { localStorage.setItem("karvy.sec." + key, _secCollapsed(key) ? "0" : "1"); } catch (e) {}
  }
  // 2c/2f:行/卡上的 X —— 从左栏隐藏(不删内容)。点 X 不触发行的点击(stopPropagation)。
  function _addLineX(row, line) {
    const x = el("button", { class: "peer-x", text: "✕", title: t("chat.hide_line") });
    x.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        await fetch("/api/line/hide", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ domain_id: line.domain_id, role: line.role, agent_id: line.agent_id || "" }) });
      } catch (e) {}
      refreshPeers();
    });
    row.appendChild(x);
  }
  // 2d:工作流/圆桌运行卡 —— 主题 + 发起群;点开 → openLine;可 X
  function _addRunCard(container, line) {
    const active = _currentRunConv && _currentRunConv === line.conversation_id;
    const row = el("div", { class: "peer-row run-card" + (active ? " active" : "") });
    const icon = line.role === "workflow" ? "⚙ " : "🎡 ";
    row.appendChild(el("div", { class: "run-main" },
      el("div", { class: "peer-nm", text: icon + (line.title || "") }),
      el("div", { class: "run-origin", text: t("chat.from_group", { g: line.origin_group || "" }) })));
    row.addEventListener("click", () => { openLine(line); });
    _addLineX(row, { domain_id: line.domain_id, role: line.role, agent_id: line.agent_id || "" });
    container.appendChild(row);
  }

  // #1:工作流/圆桌是**一次性产物**,没有"新对话/历史"的说法 → 进运行线就藏掉这俩(连圆桌按钮)。
  function _toggleChannelTools(isRunLine) {
    ["conv-new-btn", "conv-history", "roundtable-btn"].forEach((id) => {
      const e = document.getElementById(id);
      if (e) e.classList.toggle("hidden", !!isRunLine);
    });
  }
  // 2e:打开一条工作流/圆桌线(点卡 / 料里追问都走这)。切到该线 + 渲染历史 + 标题。
  let _currentRunConv = "";
  async function openLine(line) {
    openChatModal();
    try {
      const r = await fetch("/api/line/open", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: line.role, domain_id: line.domain_id,
          agent_id: line.agent_id || "", conversation_id: line.conversation_id || "" }) });
      if (!r.ok) return;
      const data = await r.json();
      if (!data.ok) return;
      _currentRunConv = data.conversation_id || line.conversation_id || "";
      _currentPeer = { domain_id: data.domain_id, role: data.role, agent_id: data.agent_id || "",
                       is_group: !!data.is_group };
      const log = document.getElementById("chat-log");
      if (log) log.innerHTML = "";
      // 标题:⚙/🎡 主题 · 来自<群>(运行线是产物,不是"你 & 某人")
      const ttl = document.getElementById("chat-title");
      if (ttl) ttl.textContent = (line.role === "workflow" ? "⚙ " : "🎡 ") + (line.title || "")
        + "  ·  " + t("chat.from_group", { g: line.origin_group || "" });
      _chatSpeaker = "";
      _toggleChannelTools(true);   // #1:运行线无 新对话/历史/圆桌
      _ceClear(); _hideMentionPop(); _hideRoundtableBanner();
      _renderConversationTurns(data.turns);
      refreshPeers();
      refreshConversations();
    } catch (e) { console.warn("[openLine] failed", e); }
  }

  // 2e:按 conversation_id 打开(料里追问)。后端定位真 peer;运行线给产物标题,普通线走常规标题。
  // targetTaskId(可选):料→去聊天时传入 → 渲染完滚到并高亮那一轮(不只是开对话丢你在底部)。
  async function openConvById(convId, targetTaskId) {
    openChatModal();
    try {
      const r = await fetch("/api/line/open_by_conv", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: convId }) });
      if (!r.ok) return;
      const data = await r.json();
      if (!data.ok) return;
      _currentRunConv = data.is_run_line ? data.conversation_id : "";
      _currentPeer = { domain_id: data.domain_id, role: data.role, agent_id: data.agent_id || "",
                       is_group: !!data.is_group };
      const log = document.getElementById("chat-log");
      if (log) log.innerHTML = "";
      const ttl = document.getElementById("chat-title");
      if (ttl) {
        if (data.is_run_line) {
          ttl.textContent = (data.kind === "workflow" ? "⚙ " : "🎡 ") + (data.title || "")
            + "  ·  " + t("chat.from_group", { g: data.origin_group || "" });
        } else { _setChatTitle(_currentPeer); }
      }
      _chatSpeaker = "";
      // #1:运行线无 新对话/历史/圆桌;普通线照常显
      _toggleChannelTools(!!data.is_run_line);
      if (!data.is_run_line) _toggleRoundtableBtn(_currentPeer);
      _ceClear(); _hideMentionPop(); _hideRoundtableBanner();
      _renderConversationTurns(data.turns);
      refreshPeers();
      refreshConversations();
      if (targetTaskId) _locateTurnByTask(targetTaskId);
    } catch (e) { console.warn("[openConvById] failed", e); }
  }

  async function refreshPeers() {
    try {
      const r = await fetch("/api/peers");
      if (!r.ok) return;
      const data = await r.json();
      const list = document.getElementById("peer-list");
      if (!list) return;
      // 高亮跟随**当前场**(_currentPeer);没切过 → 保住 DOM 上的高亮 → 都没有就高亮首项
      let curKey = null;
      if (_currentPeer) {
        try {
          curKey = _peerKey({ domain_id: _currentPeer.domain_id, role: _currentPeer.role,
            agent_id: _currentPeer.agent_id, is_group: !!_currentPeer.is_group });
        } catch (e) {}
      }
      const domActive = list.querySelector(".peer-row.active");
      const prevKey = curKey || (domActive ? domActive.dataset.key : null);
      list.innerHTML = "";
      // 去重(按显示名:注册表脏数据时同名行只留一条)
      const seen = new Set();
      const peers = [];
      for (const p of data.peers || []) {
        if (seen.has(p.label)) continue;
        seen.add(p.label);
        peers.push(p);
      }
      // 分类(Hardy):私聊 Karvy 永远置顶;其余分「私聊」「群聊」两类,各按最近沟通倒序。
      //   - 私聊:小卡 + **私聊过的** agent(没私聊过的 agent 不显示)
      //   - 群聊:所有群(业务域群 / karvy world 大群)都显示,没聊过也在
      const ts = (p) => (typeof p.last_active_at === "number" ? p.last_active_at : -1);
      const byRecency = (a, b) => ts(b) - ts(a);
      const karvy = peers.find((p) => p.is_private);
      const agents = peers.filter((p) => !p.is_private && !p.is_group && ts(p) >= 0).sort(byRecency);
      const groups = peers.filter((p) => p.is_group).sort(byRecency);
      // 2d:工作流/圆桌线(各自跑出来的独立会话卡)
      let lines = { workflows: [], roundtables: [] };
      try {
        const lr = await fetch("/api/lines");
        if (lr.ok) lines = await lr.json();
      } catch (e) {}
      let first = true;
      const mk = (p) => ({ domain_id: p.domain_id, role: p.role, agent_id: p.agent_id, is_group: !!p.is_group });
      const addRow = (container, p, xable) => {
        const peer = mk(p);
        const active = prevKey ? _peerKey(peer) === prevKey : first;
        first = false;
        const row = _addPeerRow(container, peer, _peerDisplayLabel(p), !!p.is_group, active);
        if (xable) _addLineX(row, { domain_id: p.domain_id, role: p.role, agent_id: p.agent_id || "" });
      };
      // 折叠分块:点头收起/展开(状态存 localStorage);body 装行,折叠就隐藏整段
      const addSection = (key, count) => {
        const collapsed = _secCollapsed(key);
        const head = el("div", { class: "peer-sec up peer-sec-head" },
          el("span", { text: (collapsed ? "▸ " : "▾ ") + t(key) + (count ? "  " + count : "") }));
        head.addEventListener("click", () => { _toggleSec(key); refreshPeers(); });
        list.appendChild(head);
        const body = el("div", { class: "peer-sec-body" + (collapsed ? " hidden" : "") });
        list.appendChild(body);
        return body;
      };
      // 私聊(小卡置顶不可 X;私聊过的 agent 可 X — 2f)
      const dBody = addSection("chat.sec_direct", (karvy ? 1 : 0) + agents.length);
      if (karvy) addRow(dBody, karvy, false);
      agents.forEach((p) => addRow(dBody, p, true));
      // 群聊(结构性,全显、不可 X)
      const gBody = addSection("chat.sec_group", groups.length);
      groups.forEach((p) => addRow(gBody, p, false));
      // 工作流 / 圆桌(运行产物卡:主题 + 发起群;可 X)
      const wfBody = addSection("chat.sec_workflow", (lines.workflows || []).length);
      (lines.workflows || []).forEach((l) => _addRunCard(wfBody, l));
      const rtBody = addSection("chat.sec_roundtable", (lines.roundtables || []).length);
      (lines.roundtables || []).forEach((l) => _addRunCard(rtBody, l));
      // 圆桌按钮跟随当前高亮的对象:非群场(私聊 Karvy / agent)隐藏圆桌(Hardy:私聊/agent 无圆桌)
      const activeRow = list.querySelector(".peer-row.active");
      if (activeRow) {
        try { _toggleRoundtableBtn(JSON.parse(activeRow.dataset.peer)); } catch (e) {}
      }
    } catch (e) {
      console.warn("[peers] failed", e);
    }
  }

  // #4:聊天标题随场更新 —— "你 & 小卡" / "你 & 张三(产品经理)",不再一律"你&小卡"
  function _peerLabel() { return _currentPeerLabel; }
  function _setChatTitle(peer) {
    const ttl = document.getElementById("chat-title");
    if (!ttl) return;
    // 群场(Karvy World / 业务域群)是**多人**,标题就是群名,不是"你 & 某人"(那是 1:1 的框)。
    if (peer && peer.is_world) { ttl.textContent = "👥 Karvy World"; _chatSpeaker = ""; return; }
    if (peer && peer.is_group) { ttl.textContent = "👥 " + (peer.domain_name || peer.role || ""); _chatSpeaker = ""; return; }
    const isKarvy = !peer || peer.is_private || peer.domain_id === "l0";
    const who = isKarvy ? t("chat.karvy") : (_peerLabel() || peer.role || t("chat.karvy"));
    ttl.textContent = "💬 " + t("chat.you") + " & " + who;
    _chatSpeaker = isKarvy ? "" : who;   // 回复方身份(agent tag)同步
  }
  async function switchPeer(peerJson) {
    if (!peerJson) return;
    let peer;
    try { peer = JSON.parse(peerJson); } catch { return; }
    openChatModal();   // #5.1:切场在聊天里完成,不用关掉再切
    try {
      const r = await fetch("/api/peer/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(peer),
      });
      if (!r.ok) return;
      const data = await r.json();
      // 重画聊天日志为这条线的历史(切场 = 独立上下文)
      const log = document.getElementById("chat-log");
      if (log) log.innerHTML = "";
      _currentRunConv = "";  // 切到普通场 → 清运行卡高亮(2d)
      _toggleChannelTools(false);   // #1:普通场恢复 新对话/历史
      _currentPeer = peer;   // ch4:记住当前场(圆桌按钮按它显隐)
      _setChatTitle(peer);   // #4:标题 + 回复方身份随场更新
      _toggleRoundtableBtn(peer);
      _loadGroupRoster(peer);   // ch4 #1:进群场 → 拉名册供 @ 选择
      _ceClear();               // 切场 → 输入框清空(@ 属于上一个场)
      _hideMentionPop();
      _hideRoundtableBanner();  // 切场 → 收起圆桌"开始讨论"横幅
      _maybeShowRoundtablePending(data);  // 切到的对话若是待讨论圆桌 → 重亮横幅
      _renderConversationTurns(data.turns);
      const target = peer.domain_id === "l0" ? t("peer.private") : peer.domain_id + " / " + peer.role;
      pushChatLine("system", t("peer.switched", { target: target, n: data.turn_count }));
      refreshConversations();
      // 切到某 agent 后,左栏私聊区要追加这张标签卡并高亮(像微信点好友 → 进聊天列表)。
      // set_peer 已为它建了对话线 → /api/peers 会带上 last_active_at → refreshPeers 收进私聊区。
      refreshPeers();
    } catch (e) {
      console.warn("[peer] switch failed", e);
    }
  }

  // ============ 9.1d:对话(➕新对话 / 🕘历史 resume)============

  async function refreshConversations() {
    try {
      const r = await fetch("/api/conversations");
      if (!r.ok) return;
      const data = await r.json();
      const sel = document.getElementById("conv-history");
      if (!sel) return;
      // 保留首项占位,重建列表
      sel.innerHTML = '<option value="">' + t("sel.history") + '</option>';
      for (const c of data.conversations || []) {
        const opt = el("option", { value: c.id });
        const label = (c.title && c.title.trim()) ? c.title : t("conv.untitled");
        opt.textContent = `${label} · ${t("conv.turns", { n: c.turn_count })}${c.id === data.current_id ? t("conv.current") : ""}`;
        sel.appendChild(opt);
      }
    } catch (e) {
      console.warn("[conv] list failed", e);
    }
  }

  async function newConversation() {
    try {
      const r = await fetch("/api/conversation/new", { method: "POST" });
      if (r.ok) {
        // 清屏聊天日志 + 刷历史(当前已是新对话)
        const log = document.getElementById("chat-log");
        if (log) log.innerHTML = "";
        pushChatLine("system", t("conv.new_done"));
        refreshConversations();
      }
    } catch (e) {
      console.warn("[conv] new failed", e);
    }
  }

  async function resumeConversation(convId) {
    if (!convId) return;
    try {
      const r = await fetch("/api/conversation/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: convId }),
      });
      if (!r.ok) return;
      const data = await r.json();
      // 重画聊天日志为这段对话的历史(圆桌回合 → 群聊串渲染)
      const log = document.getElementById("chat-log");
      if (log) log.innerHTML = "";
      _renderConversationTurns(data.turns);
      pushChatLine("system", t("conv.resumed", { n: data.turn_count }));
      _hideRoundtableBanner();
      _maybeShowRoundtablePending(data);   // 重开待讨论圆桌 → 重亮"开始讨论"
      refreshConversations();
    } catch (e) {
      console.warn("[conv] resume failed", e);
    }
  }

  // 主动问小卡"现在有啥建议"(WS propose;失败回退 POST /api/propose)
  async function requestProposal() {
    const sent = sendWS("propose", {});
    if (!sent) {
      try {
        const r = await fetch("/api/propose", { method: "POST" });
        if (r.ok) {
          const body = await r.json();
          _routeProposal(body.proposal);   // ch4 预判:按 kind 分流到拍板/预判
        }
      } catch (e) {
        console.warn("[propose] failed", e);
      }
    }
  }

  // ============ Renderers ============

  // ============ 9.5 #3:管理面(原子库 / 角色库 / 业务域)============
  // 模态基建(openMgmtModal/closeMgmtModal/mgmtBody/_formMsg/_setMsg + 强制引导锁)已抽到 modal.ts。

  // ---- 原子库 ----
  // ⚛ 原子面板已迁 TS(源 frontend/src/atoms_panel.ts)→ window.KarvyAtomsPanel.open()

  // ---- 个人知识库 / 认知 ----
  // 🧠 个人知识库面板已迁 TS(源 frontend/src/memory_panel.ts,整簇:沉淀工作流/认知图谱/已知列表)
  // → window.KarvyMemoryPanel.open()(自洽,只用 dom/modal/i18n + window.KarvyRender + SVG)。nav 派发直调。

  // ---- 角色库 ----
  // 🎭 角色面板已迁 TS(源 frontend/src/roles_panel.ts,整簇 _skillPicker/_openRoleEvals/_openRoleEdit 一起)
  // → window.KarvyRolesPanel.open()。留薄 wrapper:nav 派发 + 业务域面板的「新建角色」链接都还调 openRolesPanel。
  function openRolesPanel() { return window.KarvyRolesPanel.open(); }

  // ---- 外部 Agent 导入(按 KarvyLoop 范式改造 → 落角色库)----
  // 🤖 外部 Agent 导入面板已迁 TS(源 frontend/src/agents_panel.ts)→ window.KarvyAgentsPanel.open({refreshPeers})

  // ---- 业务域 ----
  // 🏢 业务域面板已迁 TS(源 frontend/src/domains_panel.ts,簇 _openDomainEdit/renderDomainsPanel 一起)。
  // 跨面板依赖(refreshPeers/pushChatLine/点角色进私聊)经 open(deps) 注入;留薄 wrapper 接 nav 派发。
  function openDomainsPanel() {
    return window.KarvyDomainsPanel.open({
      refreshPeers,
      pushChatLine,
      openPeerChat: (m) => {
        closeMgmtModal();
        _currentPeerLabel = (m.role || "") + (m.agent_id ? "·" + m.agent_id : "");
        switchPeer(JSON.stringify({ domain_id: m.domain_id, role: m.role, agent_id: m.agent_id, is_group: false }));
      },
    });
  }

  function setupMgmtPanels() {
    const close = document.getElementById("mgmt-close");
    if (close) close.addEventListener("click", closeMgmtModal);
    const overlay = document.getElementById("mgmt-modal");
    if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) closeMgmtModal(); });
    document.querySelectorAll(".nav-item[data-panel]").forEach((btn) => {
      if (btn.disabled) return;
      btn.addEventListener("click", () => {
        const p = btn.getAttribute("data-panel");
        if (p === "atoms") window.KarvyAtomsPanel.open();
        else if (p === "roles") openRolesPanel();
        else if (p === "domains") openDomainsPanel();
        else if (p === "agents") window.KarvyAgentsPanel.open({ refreshPeers });
        else if (p === "memory") window.KarvyMemoryPanel.open();
        else if (p === "decision_prefs") window.KarvyDecisionPrefs.open();
        else if (p === "skills") window.KarvySkillsPanel.open();
        else if (p === "models") window.KarvyModelsPanel.open();
        else if (p === "diagnose") openDiagnosePanel();
        else if (p === "files") window.KarvyFilesPanel.open();
        else if (p === "schedules") window.KarvySchedulesPanel.open();
      });
    });
  }

  function renderSnapshot(snap) {
    // Domains
    const domainList = document.getElementById("domain-list");
    if (!snap.domains || snap.domains.length === 0) {
      domainList.innerHTML = '<div class="empty-state">' + t("empty.domain") + '</div>';
    } else {
      domainList.innerHTML = "";
      for (const d of snap.domains) {
        const chip = el(
          "div",
          {
            class: "domain-chip" + (d === snap.current_domain ? " active" : ""),
            text: d,
          },
        );
        domainList.appendChild(chip);
      }
    }

    // Broadcasts
    const bcastList = document.getElementById("broadcast-list");
    if (!snap.broadcasts || snap.broadcasts.length === 0) {
      bcastList.innerHTML = '<div class="empty-state">' + t("empty.broadcast") + '</div>';
    } else {
      bcastList.innerHTML = "";
      for (const env of snap.broadcasts) {
        const payload = env.payload || {};
        const tag = payload.tag || env.type || "?";
        const msg = payload.message || JSON.stringify(payload);
        bcastList.appendChild(
          el("div", { class: "bcast" },
            el("span", { class: "tag", text: tag }),
            el("span", { class: "msg", text: msg }),
          ),
        );
      }
    }

    // Crystallized skills
    const skillList = document.getElementById("skill-list");
    if (!snap.crystallized_skills || snap.crystallized_skills.length === 0) {
      skillList.innerHTML = '<div class="empty-state">' + t("empty.skill") + '</div>';
    } else {
      skillList.innerHTML = "";
      for (const s of snap.crystallized_skills) {
        const isFast = s === snap.last_fast_brain_skill;
        skillList.appendChild(
          el("span", {
            class: "skill-chip" + (isFast ? " fast" : ""),
            text: "💎 " + s,
          }),
        );
      }
    }

    // Last drive
    renderLastDrive(snap);
  }

  function renderLastDrive(snap) {
    const root = document.getElementById("last-drive");
    root.innerHTML = "";
    // 错误优先(批 8.5-A:不截断)
    if (snap.last_error) {
      root.appendChild(
        el("div", { class: "last-drive-block error" },
          el("span", { class: "label", text: t("drive.error") }),
          snap.last_error,
        ),
      );
      return;
    }
    // input echo(批 8.5-A)
    if (snap.last_intent) {
      root.appendChild(
        el("div", { class: "last-drive-block intent" },
          el("span", { class: "label", text: t("drive.you_said") }),
          snap.last_intent,
        ),
      );
    }
    // 结果(快脑 / 慢脑)
    if (snap.last_drive_text) {
      if (snap.last_fast_brain_skill) {
        root.appendChild(
          el("div", { class: "last-drive-block fast" },
            el("span", { class: "label", text: t("drive.fast_hit", { skill: snap.last_fast_brain_skill }) }),
            snap.last_drive_text,
          ),
        );
      } else {
        root.appendChild(
          el("div", { class: "last-drive-block" },
            el("span", { class: "label", text: t("drive.slow_out") }),
            snap.last_drive_text,
          ),
        );
      }
    }
    if (!snap.last_error && !snap.last_intent && !snap.last_drive_text) {
      root.innerHTML = '<div class="empty-state">' + t("empty.intent") + '</div>';
    }
  }

  function renderStats(s) {
    // 顶栏仪表盘:紧凑、值在前(slow/restored 细节收进 token 弹窗,顶栏只留三个核心)
    const pct = (s.fast_brain_hit_rate * 100).toFixed(0);
    document.getElementById("stat-drives").innerHTML =
      `<b>${s.drive_calls}</b> ${t("stat.drives")}`;
    document.getElementById("stat-fast-brain").innerHTML =
      `<b>${pct}%</b> ${t("stat.fast_brain")}`;
    document.getElementById("stat-crystallized").innerHTML =
      `<b>${s.crystallizations}</b> ${t("stat.skills")}`;
  }

  // ============ ch4 圆桌:小卡兼主持,围绕主题多轮收敛(你只跟主持沟通)============

  let _currentPeer = null;

  function _toggleRoundtableBtn(peer) {
    const btn = document.getElementById("roundtable-btn");
    if (!btn) return;
    // Hardy:大群 + 业务域 都能起圆桌(任何群场;只私聊/非群不开)
    const show = !!(peer && peer.is_group);
    btn.classList.toggle("hidden", !show);
  }

  // ch4 #3:点 🎡 → 引导弹窗(写主题 + 勾选谁参与),不是先在输入框写再点。
  async function openRoundtable() {
    openMgmtModal(t("rt.setup_title"));
    const body = mgmtBody();
    body.innerHTML = "";
    // 主题输入
    body.appendChild(el("label", { class: "rt-setup-label", text: t("rt.topic_label") }));
    const topicBox = el("textarea", { class: "rt-topic", rows: "2", placeholder: t("rt.topic_ph") });
    body.appendChild(topicBox);
    // 参与者名册(随当前群场)
    body.appendChild(el("label", { class: "rt-setup-label", text: t("rt.who_label") }));
    const roster = el("div", { class: "rt-roster" });
    roster.appendChild(el("div", { class: "muted", text: t("tokens.loading") }));
    body.appendChild(roster);
    // 开始按钮
    const startBtn = el("button", { class: "rt-start", text: t("rt.start") });
    const errLine = el("div", { class: "rt-setup-err" });
    body.appendChild(errLine);
    body.appendChild(startBtn);
    // 拉名册
    const data = await _getJSON("/api/roundtable/roster");
    roster.innerHTML = "";
    const mem = (data && data.members) || [];
    if (!data || !data.ok || !mem.length) {
      roster.appendChild(el("div", { class: "muted",
        text: (data && data.reason) || t("rt.no_members") }));
    } else {
      for (const m of mem) {
        // §2.6:复合键 域::agent_id —— 同名角色跨域才能独立选中(修圆桌选不中 bug)
        const key = (m.domain_id || "") + "::" + m.agent_id;
        const id = "rtm-" + key.replace(/[^a-zA-Z0-9_-]/g, "_");
        const row = el("label", { class: "rt-member", for: id });
        const cb = el("input", { type: "checkbox", id: id, value: key, checked: "checked" });
        cb.checked = true;   // 默认全勾,用户取消不想上桌的
        row.appendChild(cb);
        const name = m.domain_name ? `${m.display} · ${m.domain_name}` : m.display;
        row.appendChild(el("span", { class: "rt-member-name", text: name }));
        roster.appendChild(row);
      }
    }
    startBtn.addEventListener("click", async () => {
      const topic = (topicBox.value || "").trim();
      if (!topic) { errLine.textContent = t("rt.need_topic_modal"); topicBox.focus(); return; }
      const picked = Array.from(roster.querySelectorAll('input[type="checkbox"]:checked')).map((c) => c.value);
      if (mem.length && !picked.length) { errLine.textContent = t("rt.need_member"); return; }
      closeMgmtModal();
      openChatModal();
      showBusy();
      try {
        // 阶段0:小卡先跟你对齐目标(需求分析),不立刻拉成员讨论
        const r = await fetch("/api/roundtable/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intent: topic, participants: picked }),
        });
        clearBusy();
        if (!r.ok) { pushChatLine("system", t("chat.http_error", { status: r.status })); return; }
        const res = await r.json();
        if (!res.ok) { pushChatLine("system", "⚠ " + (tB(res.reason) || "roundtable failed")); return; }
        const log = document.getElementById("chat-log"); if (log) log.innerHTML = "";
        pushChatLine("user", t("rt.opened", { topic: topic }));
        _chatSpeaker = "";                       // 开场是主持小卡
        pushChatLine("agent", res.opening || "");
        _showRoundtableBanner(res.conversation_id, res.participants || []);
        refreshConversations();
      } catch (e) {
        clearBusy();
        pushChatLine("system", "⚠ " + e.message);
      }
    });
  }

  // ch4 圆桌阶段0→1:对齐目标中的横幅 + 你拍板【开始讨论】
  let _pendingRoundtable = null;
  // ch4 圆桌对话式(Hardy:少按钮)—— 横幅只是**提示**正在对齐,不带按钮;
  // 你直接在输入框跟小卡聊,它判断聊清了就自己开始讨论(/align)。
  function _showRoundtableBanner(convId, participants) {
    // 圆桌只在群场:选中频道不是群聊 → 不显示对齐横幅(与 🎡 按钮同一门:非群无圆桌功能)
    if (!_currentPeer || !_currentPeer.is_group) { _hideRoundtableBanner(); return; }
    _pendingRoundtable = { conv_id: convId, participants: participants || [] };
    const bar = document.getElementById("roundtable-bar");
    if (!bar) return;
    bar.innerHTML = "";
    bar.appendChild(el("span", { class: "rt-bar-label",
      text: t("rt.aligning", { n: (participants || []).length }) }));
    bar.classList.remove("hidden");
  }
  function _hideRoundtableBanner() {
    _pendingRoundtable = null;
    const bar = document.getElementById("roundtable-bar");
    if (bar) { bar.classList.add("hidden"); bar.innerHTML = ""; }
  }
  // 重开/切到的对话若是"待对齐圆桌"→ 重亮提示,继续跟小卡对齐
  function _maybeShowRoundtablePending(data) {
    const rp = data && data.roundtable_pending;
    if (rp && rp.conversation_id) _showRoundtableBanner(rp.conversation_id, rp.participants || []);
  }

  // 圆桌渲染成**群聊窗口**(Hardy):🎡 主题头 → 每位成员发言一个气泡(像群聊)→
  // 小卡主持收敛的结论作为高亮收尾气泡 → 追问提示。内联(刚跑完)+ 重开(从历史/首页)同一渲染。
  function renderRoundtable(result) {
    const log = document.getElementById("chat-log");
    if (!log) return;
    const follow = isNearBottom(log);
    const card = el("div", { class: "rt-card" });
    if (result.topic) card.appendChild(el("div", { class: "rt-topic-head", text: "🎡 " + result.topic }));
    card.appendChild(el("div", { class: "rt-head", text: t("rt.host_label", {
      rounds: result.rounds || 0,
      status: result.converged ? t("rt.converged") : t("rt.capped"),
    }) }));
    // 群聊串:每条发言一个气泡(花名在上、消息在下,像群聊)
    const thread = el("div", { class: "rt-thread" });
    for (const x of (result.transcript || [])) {
      const msg = el("div", { class: "rt-msg" });
      msg.appendChild(el("span", { class: "rt-msg-who", text: (x.speaker || "?") + (x.round ? "  · R" + x.round : "") }));
      const bubble = el("div", { class: "rt-bubble" });
      if (window.KarvyRender) KarvyRender.appendMarkdown(bubble, x.text || "");
      else bubble.textContent = x.text || "";
      msg.appendChild(bubble);
      thread.appendChild(msg);
    }
    card.appendChild(thread);
    // 小卡主持收敛的结论(高亮收尾气泡)
    const conclText = (result.conclusion || "").trim();
    if (conclText) {
      const cm = el("div", { class: "rt-msg rt-host" });
      cm.appendChild(el("span", { class: "rt-msg-who", text: t("rt.host_who") }));
      const cb = el("div", { class: "rt-bubble rt-bubble-concl" });
      if (window.KarvyRender) KarvyRender.appendMarkdown(cb, conclText);
      else cb.textContent = conclText;
      cm.appendChild(cb);
      card.appendChild(cm);
    }
    // 追问提示:接着在这个圆桌窗里问小卡(主持)即可
    card.appendChild(el("div", { class: "rt-foot", text: t("rt.followup_hint") }));
    log.appendChild(card);
    if (follow) log.scrollTop = log.scrollHeight;
  }

  // @ 多个角色的回应:每位一个气泡(复用圆桌群聊串样式,无主题头/结论)。
  function renderMentionReplies(replies) {
    const log = document.getElementById("chat-log");
    if (!log || !replies || !replies.length) return;
    const follow = isNearBottom(log);
    const card = el("div", { class: "rt-card" });
    const thread = el("div", { class: "rt-thread" });
    for (const x of replies) {
      const msg = el("div", { class: "rt-msg" });
      msg.appendChild(el("span", { class: "rt-msg-who", text: x.speaker || "?" }));
      const bubble = el("div", { class: "rt-bubble" });
      if (window.KarvyRender) KarvyRender.appendMarkdown(bubble, x.text || "");
      else bubble.textContent = x.text || "";
      msg.appendChild(bubble);
      thread.appendChild(msg);
    }
    card.appendChild(thread);
    log.appendChild(card);
    if (follow) log.scrollTop = log.scrollHeight;
  }

  // 重新设计(跳过快脑匹配,小卡现场设计)
  async function _workflowReplan(intent, mentions) {
    showBusy();
    try {
      const r = await fetch("/api/workflow/plan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: intent, mentions: mentions, force_fresh: true }),
      });
      clearBusy();
      const res = await r.json();
      if (res.ok) _renderWorkflowPlan(res.plan, intent, null, mentions);
      else pushChatLine("system", "⚠ " + (tB(res.reason) || "plan failed"));
    } catch (e) { clearBusy(); pushChatLine("system", "⚠ " + e.message); }
  }

  // §11 P2:diff 原 plan(小卡所提)vs 改后 plan → 改动文本列表(决策信号;无改动=空,不浪费 token)
  function _workflowEdits(orig, final) {
    const edits = [];
    if (!orig) return edits;
    const origTask = {};
    (orig.steps || []).forEach((s) => { origTask[s.id] = (s.task || "").trim(); });
    if ((orig.goal || "").trim() !== (final.goal || "").trim() && (final.goal || "").trim()) {
      edits.push(t("wf.edit_goal", { g: final.goal }));
    }
    (final.steps || []).forEach((s) => {
      const ot = origTask[s.id], nt = (s.task || "").trim();
      if (!nt) return;
      if (ot === undefined) edits.push(t("wf.edit_added", { task: nt }));
      else if (ot !== nt) edits.push(t("wf.edit_changed", { task: nt }));
    });
    return edits;
  }

  // ch4 workflow:@多人 → 小卡设计的 DAG 给你**可编辑步骤表**拍板 → 执行。matched=命中的复用模板。
  function _renderWorkflowPlan(plan, intent, matched, mentions) {
    openMgmtModal(t("wf.plan_title"));
    const body = mgmtBody(); body.innerHTML = "";
    // 默认已是**现设计**(针对新意图)。若快脑匹配上 → 只附带一个"套用上次模板"的可选项(你点才用)。
    if (matched && matched.plan) {
      const bar = el("div", { class: "wf-matched" });
      bar.appendChild(el("span", { text: t("wf.matched", { name: matched.name || "?", n: matched.use_count || 0 }) }));
      bar.appendChild(el("button", { class: "wf-replan", text: t("wf.apply_template"),
        onClick: () => { _renderWorkflowPlan(matched.plan, intent, null, mentions); } }));
      body.appendChild(bar);
    }
    body.appendChild(el("label", { class: "rt-setup-label", text: t("wf.goal_label") }));
    const goalIn = el("input", { class: "wf-goal", value: plan.goal || intent || "" });
    body.appendChild(goalIn);
    body.appendChild(el("label", { class: "rt-setup-label", text: t("wf.steps_label") }));
    const stepsBox = el("div", { class: "wf-steps" });
    const steps = (plan.steps || []).map((s) => Object.assign({}, s));   // 工作副本
    // 可选角色 = 计划里出现过的角色(去重)—— 给加步骤/改角色的下拉用
    const availRoles = [];
    const _seenRole = {};
    for (const s of steps) {
      const k = (s.domain_id || "") + "|" + (s.agent_id || "");
      if (s.agent_id && !_seenRole[k]) { _seenRole[k] = 1; availRoles.push({ agent_id: s.agent_id, domain_id: s.domain_id, display: s.display || s.agent_id }); }
    }
    let _nextN = steps.reduce((m, s) => Math.max(m, parseInt((s.id || "s0").slice(1), 10) || 0), 0);
    function redraw() {
      stepsBox.innerHTML = "";
      steps.forEach((s, i) => {
        const row = el("div", { class: "wf-step" });
        // —— 头:序号 + 派给谁(可改)+ 删 ——
        const head = el("div", { class: "wf-step-head" });
        head.appendChild(el("span", { class: "wf-step-num", text: (i + 1) + "." }));
        const sel = el("select", { class: "wf-step-role" });
        availRoles.forEach((r) => {
          const opt = el("option", { value: r.domain_id + "|" + r.agent_id, text: r.display });
          if (r.agent_id === s.agent_id && r.domain_id === s.domain_id) opt.selected = true;
          sel.appendChild(opt);
        });
        sel.addEventListener("change", () => {
          const r = availRoles.find((x) => (x.domain_id + "|" + x.agent_id) === sel.value);
          if (r) { s.agent_id = r.agent_id; s.domain_id = r.domain_id; s.display = r.display; }
        });
        head.appendChild(sel);
        head.appendChild(el("button", { class: "wf-step-del", text: "✕",
          onClick: () => {
            const delId = steps[i].id;
            steps.splice(i, 1);
            // 删步骤 → 清掉其他步骤对它的依赖引用(防悬空依赖让 DAG 跑不动)
            steps.forEach((s2) => { s2.depends_on = (s2.depends_on || []).filter((dp) => dp !== delId); });
            redraw();
          } }));
        row.appendChild(head);
        // —— 任务(整行可改)——
        const taskIn = el("input", { class: "wf-step-task", value: s.task || "" });
        taskIn.addEventListener("input", () => { s.task = taskIn.value; });
        row.appendChild(taskIn);
        // —— 依赖:**单击切换的 chip**(替原生 multi-select 的 ctrl+click 地狱)。
        //     点亮的数字 = 这步要等它们做完才开始;一个不点 = 开头并行起步。
        const deps = el("div", { class: "wf-step-deps" });
        if (i > 0) {
          deps.appendChild(el("span", { class: "wf-step-dep-label", text: t("wf.deps_label") }));
          for (let j = 0; j < i; j++) {
            const ej = steps[j];
            const on = (s.depends_on || []).indexOf(ej.id) >= 0;
            deps.appendChild(el("button", {
              class: "wf-dep-chip" + (on ? " on" : ""), text: String(j + 1),
              title: (ej.display || ej.agent_id || ""),
              onClick: () => {
                const set = (s.depends_on || []).slice();
                const at = set.indexOf(ej.id);
                if (at >= 0) set.splice(at, 1); else set.push(ej.id);
                s.depends_on = set;
                redraw();   // 重画:点亮态更新
              } }));
          }
          if (!(s.depends_on || []).length) deps.appendChild(el("span", { class: "wf-dep-none", text: t("wf.dep_parallel") }));
        } else {
          deps.appendChild(el("span", { class: "wf-dep-none", text: t("wf.dep_start") }));
        }
        row.appendChild(deps);
        stepsBox.appendChild(row);
      });
    }
    body.appendChild(el("div", { class: "wf-flow-legend", text: t("wf.flow_legend") }));
    // 🎨 全屏拖拽画布(Drawflow):复杂 DAG 用画布拖/连更直观;存→回写步骤表,取消→不动(Hardy)。
    body.appendChild(el("button", { class: "wf-edit-canvas", text: t("wf.edit_canvas"),
      onClick: async () => {
        try { await _ensureWorkflowCanvas(); }   // 首次点才拉 Drawflow 包
        catch { alert(t("wf.canvas_missing")); return; }
        window.KarvyWorkflowCanvas.open({ goal: goalIn.value, steps: steps }, availRoles, (np) => {
          steps.length = 0; (np.steps || []).forEach((s) => steps.push(s));
          if (np.goal != null) goalIn.value = np.goal;
          redraw();   // 画布存回 → 同步刷新下方步骤表
        });
      } }));
    redraw();
    body.appendChild(stepsBox);
    // + 加一步(默认派给第一个角色、依赖上一步,串到末尾;你可改角色/任务)
    if (availRoles.length) {
      body.appendChild(el("button", { class: "wf-add-step", text: t("wf.add_step"),
        onClick: () => {
          _nextN += 1;
          const prev = steps.length ? steps[steps.length - 1].id : null;
          const r = availRoles[0];
          steps.push({ id: "s" + _nextN, agent_id: r.agent_id, domain_id: r.domain_id,
                       display: r.display, task: "", depends_on: prev ? [prev] : [] });
          redraw();
        } }));
    }
    const msg = _formMsg();
    body.appendChild(el("button", { class: "rt-start", text: t("wf.approve"),
      onClick: async () => {
        const finalPlan = { goal: (goalIn.value || "").trim(), steps: steps };
        if (!finalPlan.steps.length) { _setMsg(msg, false, t("wf.need_step")); return; }
        // §11 P2:diff 小卡所提 DAG vs 你改后的 → 改动即决策信号(只在真改时才有,省 token)
        const edits = _workflowEdits(plan, finalPlan);
        closeMgmtModal();
        openChatModal();
        pushChatLine("user", t("wf.running", { goal: finalPlan.goal }));
        showBusy();
        try {
          const r = await fetch("/api/workflow/run", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ plan: finalPlan, intent: intent || "", edits: edits }),
          });
          clearBusy();
          const res = await r.json();
          if (res.ok) renderWorkflow(res.workflow, res.crystallizable, res.plan);
          else pushChatLine("system", "⚠ " + (tB(res.reason) || "workflow failed"));
        } catch (e) { clearBusy(); pushChatLine("system", "⚠ " + e.message); }
      } }));
    body.appendChild(msg);
  }

  // 沉淀:跑稳的现设计 workflow → 问你要不要结晶成可复用模板(下次快脑匹配)
  function _offerCrystallize(plan) {
    const log = document.getElementById("chat-log");
    if (!log || !plan) return;
    const card = el("div", { class: "wf-crystallize" });
    card.appendChild(el("span", { class: "wf-cry-q", text: t("wf.crystallize_q") }));
    const row = el("div", { class: "wf-cry-btns" });
    row.appendChild(el("button", { class: "predict-yes", text: t("wf.crystallize_yes"),
      onClick: async () => {
        card.remove();
        const r = await _postJSON("/api/workflow/crystallize", { plan: plan, name: plan.goal || "" });
        pushChatLine("system", r.ok ? t("wf.crystallized") : ("⚠ " + ((r.data && r.data.reason) || "")));
      } }));
    row.appendChild(el("button", { class: "predict-no", text: t("wf.crystallize_no"),
      onClick: () => card.remove() }));
    card.appendChild(row);
    log.appendChild(card);
    if (isNearBottom(log)) log.scrollTop = log.scrollHeight;
  }

  // workflow 执行结果:每步一个气泡(谁·做什么 → 产出),复用圆桌群聊串样式。
  function renderWorkflow(wf, crystallizable, plan) {
    const log = document.getElementById("chat-log");
    if (!log || !wf) return;
    const follow = isNearBottom(log);
    const card = el("div", { class: "rt-card" });
    card.appendChild(el("div", { class: "rt-topic-head", text: "⚙ " + (wf.goal || "") }));
    const thread = el("div", { class: "rt-thread" });
    for (const s of (wf.steps || [])) {
      const msg = el("div", { class: "rt-msg" });
      const mark = s.status === "done" ? "" : " ✗";
      msg.appendChild(el("span", { class: "rt-msg-who", text: (s.display || "?") + " · " + (s.task || "") + mark }));
      const bubble = el("div", { class: "rt-bubble" });
      const txt = (s.output || "").trim() || t("wf.no_output");
      if (window.KarvyRender) KarvyRender.appendMarkdown(bubble, txt);
      else bubble.textContent = txt;
      msg.appendChild(bubble);
      thread.appendChild(msg);
    }
    card.appendChild(thread);
    log.appendChild(card);
    if (follow) log.scrollTop = log.scrollHeight;
    if (crystallizable && plan) _offerCrystallize(plan);   // 跑稳了 → 问你沉淀不
  }

  // 重画一段对话的回合:圆桌/workflow/( @多人回应)→ 群聊串卡;普通回合 → user/agent 行。
  function _renderConversationTurns(turns) {
    const log = document.getElementById("chat-log");
    for (const tn of (turns || [])) {
      // 料→去聊天定位:渲染前记下起点,渲染后给这一轮新增的所有节点打 data-task-id,
      // 让 openConvById 能找到对应那一轮并滚过去 + 高亮(不只是开对话丢你在底部)。
      const start = log ? log.children.length : 0;
      if (tn.data && tn.data.roundtable) {
        renderRoundtable(tn.data.roundtable);   // 卡里已有 🎡 主题头,不再单列 user 行
      } else if (tn.data && tn.data.workflow) {
        renderWorkflow(tn.data.workflow);       // ⚙ 工作流执行结果
      } else if (tn.data && tn.data.mention_fanout) {
        pushChatLine("user", tn.data.mention_fanout.intent || tn.user_intent);
        renderMentionReplies(tn.data.mention_fanout.replies || []);
      } else if (tn.data && tn.data.attachments) {
        // 多模态:回放也看得到当时发了什么图/文档(缩略图 + 文档块)
        const a = tn.data.attachments;
        _pushUserWithAttachments(a.q || tn.user_intent, a.items || []);
        pushChatLine(tn.brain === "fast" ? "system" : "agent", tn.agent_response);
      } else {
        pushChatLine("user", tn.user_intent);
        pushChatLine(tn.brain === "fast" ? "system" : "agent", tn.agent_response);
      }
      if (log && tn.task_id) {
        for (let i = start; i < log.children.length; i++) {
          log.children[i].dataset.taskId = tn.task_id;
        }
      }
    }
  }

  // 料→去聊天:滚到并高亮某条 task 对应的那一轮(找它的**第一个**节点 = 提问行)。
  function _locateTurnByTask(taskId) {
    if (!taskId) return;
    const log = document.getElementById("chat-log");
    if (!log) return;
    const node = log.querySelector('[data-task-id="' + (window.CSS && CSS.escape ? CSS.escape(taskId) : taskId) + '"]');
    if (!node) return;
    // 等渲染/布局稳定后再滚(markdown 异步排版),高亮脉冲一下随后自动消。
    setTimeout(() => {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.classList.add("turn-locate-flash");
      setTimeout(() => node.classList.remove("turn-locate-flash"), 1800);
    }, 60);
  }

  // ============ ch4 #1:群里 @ 角色(微信式选择器,contenteditable 行内高亮)============
  // 群场里输 @ → 弹角色列表、可筛、↑↓/点选 → 插入**行内高亮 @花名 chip**(不可编辑、整体删,
  // 可多个);发送时从 DOM 读出被 @ 的 agent_id。后端:带 mention → 那个角色照自己人格/域回话。

  let _groupRoster = [];       // 当前群场可 @ 的角色 [{agent_id, display, domain_name, role}]
  let _mentionMatches = [];    // 当前下拉候选
  let _mentionActive = -1;     // 键盘高亮项
  let _mentionRange = null;    // @词在 contenteditable 里的位置(选中后替换)

  async function _loadGroupRoster(peer) {
    _groupRoster = [];
    if (!peer || !peer.is_group) return;
    const data = await _getJSON("/api/roundtable/roster");
    if (data && data.ok) _groupRoster = data.members || [];
  }

  function _ceInput() { return document.getElementById("chat-input"); }
  function _ceUpdateEmpty() {
    const ce = _ceInput(); if (!ce) return;
    const empty = (ce.textContent || "").trim() === "" && !ce.querySelector(".mention-tag");
    ce.classList.toggle("is-empty", empty);   // 空 → CSS :before 显 placeholder
  }
  function _ceClear() { const ce = _ceInput(); if (ce) { ce.innerHTML = ""; _ceUpdateEmpty(); } }
  // 发送时:从 contenteditable 读出纯文本(chip 文本含 @花名)+ 被 @ 的 agent_id 列表
  function _readChatInput() {
    const ce = _ceInput();
    if (!ce) return { text: "", mentions: [] };
    const text = (ce.textContent || "").replace(/ /g, " ").trim();
    const mentions = Array.from(ce.querySelectorAll(".mention-tag"))
      .map((s) => ({ agent_id: s.getAttribute("data-agent"), domain_id: s.getAttribute("data-domain") || "" }))
      .filter((m) => m.agent_id);
    return { text: text, mentions: mentions };
  }

  function _clearMention() { _hideMentionPop(); }   // 行内 chip 由清空输入框带走
  function _hideMentionPop() {
    const pop = document.getElementById("mention-pop");
    if (pop) { pop.classList.add("hidden"); pop.innerHTML = ""; }
    _mentionMatches = []; _mentionActive = -1; _mentionRange = null;
  }

  // 输入时(contenteditable):光标前末尾出现 @词(无空格)→ 弹筛选后的角色
  function _onChatInputMention() {
    _ceUpdateEmpty();
    if (!_currentPeer || !_currentPeer.is_group || !_groupRoster.length) { _hideMentionPop(); return; }
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) { _hideMentionPop(); return; }
    const range = sel.getRangeAt(0);
    const node = range.startContainer;
    if (!node || node.nodeType !== Node.TEXT_NODE) { _hideMentionPop(); return; }
    const before = (node.textContent || "").slice(0, range.startOffset);
    const m = before.match(/@([^@\s ]*)$/);   // 最后一个 @ 到光标、无空格
    if (!m) { _hideMentionPop(); return; }
    _mentionRange = { node: node, atOffset: m.index, caretOffset: range.startOffset };
    const q = (m[1] || "").toLowerCase();
    _mentionMatches = _groupRoster.filter((x) =>
      !q || (x.display || "").toLowerCase().includes(q) || (x.agent_id || "").toLowerCase().includes(q));
    _renderMentionPop();
  }

  function _renderMentionPop() {
    const pop = document.getElementById("mention-pop");
    if (!pop) return;
    pop.innerHTML = "";
    if (!_mentionMatches.length) { pop.classList.add("hidden"); return; }
    if (_mentionActive < 0 || _mentionActive >= _mentionMatches.length) _mentionActive = 0;
    let _activeRow = null;
    _mentionMatches.forEach((m, i) => {
      const row = el("div", { class: "mention-item" + (i === _mentionActive ? " active" : ""),
        onMousedown: (ev) => { ev.preventDefault(); _selectMention(m); } });
      row.appendChild(el("span", { class: "mention-at", text: "@" }));
      row.appendChild(el("span", { class: "mention-disp", text: m.display }));
      if (m.domain_name) row.appendChild(el("span", { class: "mention-dom", text: m.domain_name }));
      if (i === _mentionActive) _activeRow = row;
      pop.appendChild(row);
    });
    pop.classList.remove("hidden");
    // ↑↓ 切换后:把高亮项滚进可视区(否则 innerHTML 重建把滚动复位到顶,高亮跑到 fold 之下看不见、
    // 滚动条也不跟;Hardy 报"切换了但页面选中/焦点/滚动条都不动")。
    if (_activeRow && _activeRow.scrollIntoView) _activeRow.scrollIntoView({ block: "nearest" });
  }

  // 选中 → 把 @词替换成行内高亮 chip(contenteditable=false → 整体删)+ 尾随 nbsp
  function _selectMention(m) {
    const ce = _ceInput();
    const r = _mentionRange;
    if (!ce || !r || !r.node || !r.node.parentNode) { _hideMentionPop(); return; }
    const node = r.node;
    const full = node.textContent || "";
    const before = full.slice(0, r.atOffset);
    const after = full.slice(r.caretOffset);
    const chip = document.createElement("span");
    chip.className = "mention-tag";
    chip.setAttribute("contenteditable", "false");
    chip.setAttribute("data-agent", m.agent_id);
    chip.setAttribute("data-domain", m.domain_id || "");
    // 大群(l0)跨域聚合 → 同名(两个设计师)消歧:@设计师（哟吼）;域群里单域无需挂
    const showDom = _currentPeer && _currentPeer.domain_id === "l0" && m.domain_name;
    chip.textContent = "@" + m.display + (showDom ? "（" + m.domain_name + "）" : "");
    node.textContent = before;
    const parent = node.parentNode;
    const tail = document.createTextNode(" " + after);   // chip 后补 nbsp,光标好落
    parent.insertBefore(tail, node.nextSibling);
    parent.insertBefore(chip, tail);
    const sel = window.getSelection();
    const range = document.createRange();
    range.setStart(tail, 1); range.collapse(true);
    sel.removeAllRanges(); sel.addRange(range);
    _hideMentionPop();
    _ceUpdateEmpty();
    ce.focus();
  }

  // 键盘:下拉开 → ↑↓/Enter 选 / Esc 关;下拉关 → Enter 发送(Shift+Enter 换行)
  function _onChatInputKeydown(e) {
    // 输入法合成中(中文拼音选字)的 Enter 是确认候选,**不能**当发送(Hardy 打中文必踩)
    if (e.isComposing || e.keyCode === 229) return;
    const pop = document.getElementById("mention-pop");
    const popOpen = pop && !pop.classList.contains("hidden") && _mentionMatches.length;
    if (popOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); _mentionActive = (_mentionActive + 1) % _mentionMatches.length; _renderMentionPop(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); _mentionActive = (_mentionActive - 1 + _mentionMatches.length) % _mentionMatches.length; _renderMentionPop(); return; }
      if (e.key === "Enter") { e.preventDefault(); _selectMention(_mentionMatches[_mentionActive] || _mentionMatches[0]); return; }
      if (e.key === "Escape") { e.preventDefault(); _hideMentionPop(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); _submitChat(); }
  }

  function renderDriveDone(payload) {
    clearBusy();  // 9.5 P2:结果到了,撤掉"执行中"
    // 群里不 @ 任何人:没人回,小卡只轻提醒一句(本地化;不当成一轮真回复)
    if (payload.no_mention_nudge) {
      pushChatLine("system", "🦫 " + t("group.no_mention_nudge"));
      return;
    }
    _chatSpeaker = payload.speaker || "";   // brick2:这轮回复方身份(""=小卡)
    // 推到 chat log(同步乐观渲染;之后 pollChatHistory 会用带 events 的历史同样结构化重渲)
    const log = document.getElementById("chat-log");
    const follow = isNearBottom(log);
    if (payload.error) {
      pushChatLine("system", "⚠ " + payload.error);
    } else if ((payload.events && payload.events.length) || payload.text) {
      appendAgentTurn(log, payload);
      if (follow) log.scrollTop = log.scrollHeight;
    }
    if (payload.crystallized && payload.skill_name) {
      pushChatLine("system", t("drive.crystallized", { skill: payload.skill_name }));
    }
    // 刷新 snapshot 拿 last_drive_text
    pollSnapshot();
  }

  // 9.4:渲染一个 agent 回合 —— 有 events 走结构化(markdown 正文 + tool 卡 + 输出面板),
  // 否则走 markdown(text);KarvyRender 缺失时安全回退裸文本。
  function appendAgentTurn(log, entry) {
    // 署名用**这条回合自己的** speaker(历史里 per-turn 持久)→ @ 角色的回复重渲时不再错标"小卡";
    // 缺(老历史/无 speaker)才回退当前全局 _chatSpeaker / 小卡。
    const _who = (entry && entry.speaker) || _chatSpeaker || t("chat.karvy");
    const line = el("div", { class: "chat-line agent" },
      el("span", { class: "role", text: _who }));
    if (entry.events && entry.events.length && window.KarvyRender) {
      const body = el("div", { class: "agent-turn" });
      KarvyRender.renderEvents(body, entry.events);
      line.appendChild(body);
    } else if (window.KarvyRender) {
      KarvyRender.appendMarkdown(line, entry.text || "");
    } else {
      line.appendChild(document.createTextNode(entry.text || ""));
    }
    log.appendChild(line);
  }

  // brick2:消息身份。当前回复方显示名(""=小卡,本地化);drive_done 时由 payload.speaker 更新。
  let _chatSpeaker = "";
  function _roleLabel(role) {
    if (role === "user") return t("chat.you");          // 你 / You(不是 [user])
    if (role === "agent") return _chatSpeaker || t("chat.karvy");  // 小卡 / 花名(不是 [agent])
    return "";
  }
  function pushChatLine(role, text) {
    const log = document.getElementById("chat-log");
    const follow = isNearBottom(log);
    // 系统提示不是"说话人":做成居中淡提示,不挂 [system] 的 speaker tag(Hardy:[system] 是啥?)
    if (role === "system") {
      const notice = el("div", { class: "chat-notice" });
      if (window.KarvyRender) KarvyRender.appendMarkdown(notice, text || "");
      else notice.appendChild(document.createTextNode(text || ""));
      log.appendChild(notice);
      if (follow) log.scrollTop = log.scrollHeight;
      return;
    }
    const line = el("div", { class: "chat-line " + role },
      el("span", { class: "role", text: _roleLabel(role) }));
    // 9.4:正文走 markdown + 消毒(KarvyRender);缺库回退裸文本
    if (window.KarvyRender) KarvyRender.appendMarkdown(line, text || "");
    else line.appendChild(document.createTextNode(text || ""));
    log.appendChild(line);
    if (follow) log.scrollTop = log.scrollHeight;
  }

  function renderChatHistory(lines) {
    if (!lines || lines.length === 0) {
      // 不要清空 — 用户可能正在输入
      return;
    }
    const log = document.getElementById("chat-log");
    // 简单 diff:行数变了就重渲整个 log(500 条 cap,可接受)
    log.innerHTML = "";
    for (const e of lines) {
      if (e.role === "agent") {
        appendAgentTurn(log, e);  // 9.4:agent 回合结构化(events 持久在历史里)
      } else {
        const line = el("div", { class: "chat-line " + e.role },
          el("span", { class: "role", text: "[" + e.role + "]" }));
        if (window.KarvyRender) KarvyRender.appendMarkdown(line, e.text || "");
        else line.appendChild(document.createTextNode(e.text || ""));
        log.appendChild(line);
      }
    }
    log.scrollTop = log.scrollHeight;
  }

  // ============ 9.5 P2:任务看板(谁在忙/状态/结果/关联聊天)============
  async function pollTasks() {
    const data = await _getJSON("/api/tasks");
    if (data) { _lastTasks = data.tasks || []; renderTaskBoard(_lastTasks); }
  }
  // ch4:勾选"流进来的料" → 一键带进 KarvyChat 问小卡(决策者勾选数据问 AI 的核心咬合)
  const _material = new Map();   // taskId → {intent, result}
  let _pendingMaterial = "";     // 下一条消息要带上的料(发完即清)
  function _toggleMaterial(tk, checked) {
    if (checked) _material.set(tk.id, { intent: tk.intent || "", result: tk.result || "" });
    else _material.delete(tk.id);
    _renderMaterialBar();
  }
  function _renderMaterialBar() {
    const bar = document.getElementById("material-bar");
    if (!bar) return;
    bar.innerHTML = "";
    if (!_material.size) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
    bar.appendChild(el("button", { class: "material-ask",
      text: t("material.ask", { n: _material.size }), onClick: _askKarvyAboutMaterial }));
  }
  function _askKarvyAboutMaterial() {
    const items = Array.from(_material.values());
    _pendingMaterial = items.map((m, i) => `【料${i + 1}】${m.intent}\n${m.result}`).join("\n\n");
    _material.clear(); _renderMaterialBar();
    openChatModal();
    pushChatLine("system", t("material.attached", { n: items.length }));
    const input = document.getElementById("chat-input");
    if (input) setTimeout(() => input.focus(), 50);
  }
  function _taskCard(tk) {
    const badge = tk.status === "running"
      ? el("span", { class: "task-badge running" }, el("span", { class: "busy-dot" }), t("task.running"))
      : el("span", { class: "task-badge " + tk.status, text: tk.status === "error" ? t("task.error") : t("task.done") });
    const top = el("div", { class: "task-top" },
      el("span", { class: "task-who", text: _localizeWho(tk.who) }), badge);
    // 跑完的料可勾选(checkbox 不触发开详情)
    if (tk.status !== "running") {
      const chk = el("input", { type: "checkbox", class: "task-check",
        onclick: (e) => e.stopPropagation() });
      chk.addEventListener("change", (e) => _toggleMaterial(tk, e.target.checked));
      top.insertBefore(chk, top.firstChild);
    }
    // §0.7 P2:运行中任务的步级进度(workflow/圆桌每步即时画;终态后清缓存)
    let stepsEl = null;
    if (tk.status === "running") {
      const steps = _taskSteps.get(tk.id) || [];
      if (steps.length) {
        stepsEl = el("div", { class: "task-steps" },
          ...steps.map((s) => el("div", { class: "task-step " + (s.status || "done") },
            el("span", { class: "step-mark", text: s.status === "failed" ? "✗" : "✓" }),
            el("span", { class: "step-name", text: s.display || "?" }))));
      }
    }
    // 主动报阻塞(借鉴 Multica):最新事件是 blocked → 卡片直接冒 ⚠「卡在哪」,不用点开、不用去问
    let blockedEl = null;
    if (tk.status === "running" && tk.blocked && tk.last_event) {
      blockedEl = el("div", { class: "task-blocked", text: "⚠ " + t("task.blocked_on", { what: tk.last_event.text || "?" }) });
    }
    return el("div", { class: "task-card" + (blockedEl ? " has-blocked" : ""),
      onclick: (e) => { if (e.target && e.target.classList.contains("task-check")) return; openTaskDetail(tk); } },
      top,
      el("div", { class: "task-intent", text: tk.intent || "" }),
      blockedEl,
      stepsEl,
      (tk.status !== "running" && tk.result) ? el("div", { class: "task-result", text: tk.result }) : null,
      (tk.status !== "running") ? el("div", { class: "task-jump", text: t("task.view_result") }) : null);
  }
  // ch4:任务分两象限 —— 跑完的进【流进来的料】,跑着的进【谁在忙】(干完即撤)
  function renderTaskBoard(tasks) {
    const board = document.getElementById("task-board");   // 料 = 已出结果
    const busy = document.getElementById("busy-list");     // 谁在忙 = running
    const done = tasks.filter((tk) => tk.status !== "running");
    const running = tasks.filter((tk) => tk.status === "running");
    if (board) {
      board.innerHTML = "";
      if (!done.length) board.appendChild(el("div", { class: "empty-state", text: t("empty.task_board") }));
      else done.forEach((tk) => board.appendChild(_taskCard(tk)));
    }
    if (busy) {
      busy.innerHTML = "";
      if (!running.length) busy.appendChild(el("div", { class: "empty-state", text: t("empty.busy") }));
      else running.forEach((tk) => busy.appendChild(_taskCard(tk)));
    }
    updatePulse();
  }
  // 💰 token 成本表已迁 TS(源 frontend/src/tokens_panel.ts:顶栏 meter + 点开弹窗/各模型/各功能)
  // → window.KarvyTokens.pollMeter()(轮询刷 meter)/ window.KarvyTokens.open()(💰 点开)。

  // ============ step5 驾驶舱:脉搏 + "又懂了你"知识列 ============
  function _countCards(containerId, emptyClass) {
    const c = document.getElementById(containerId);
    if (!c) return 0;
    return Array.from(c.children).filter((ch) => !ch.classList.contains(emptyClass)).length;
  }
  function updatePulse() {
    const pulse = document.getElementById("pulse-text");
    if (!pulse) return;
    const ran = _countCards("task-board", "empty-state");
    const pending = _countCards("h2a-list", "h2a-empty");
    if (pending > 0) pulse.textContent = t("cockpit.pulse_active", { ran: ran, pending: pending });
    else if (ran > 0) pulse.textContent = t("cockpit.pulse_ran", { ran: ran });
    else pulse.textContent = t("cockpit.pulse_idle");
  }
  async function pollKnowledge() {
    const data = await _getJSON("/api/memory");
    const list = document.getElementById("knowledge-list");
    if (!list) return;
    const beliefs = (data && data.beliefs) || [];
    list.innerHTML = "";
    if (!beliefs.length) {
      list.appendChild(el("div", { class: "empty-state", text: t("mem.empty") }));
      return;
    }
    for (const b of beliefs.slice(0, 6)) {
      list.appendChild(el("div", { class: "know-chip", text: b.content }));  // el text= → textContent, XSS 安全
    }
  }

  // ============ 楔子:Skill 库(L0 结晶技能 —— 楔子的家) ============
  // 🧩 技能库面板已迁 TS(源 frontend/src/skills_panel.ts,整簇:导入/目录/检索源/Coding能力卡/详情沙箱试跑)
  // → window.KarvySkillsPanel.open()(自洽,只用 dom/modal/i18n + window.KarvyRender)。nav 派发直调。

  // ============ 全局模型配置 + onboarding + 无 Key 强制引导 ============
  // 🤖 已迁 TS(源 frontend/src/models_panel.ts,整簇:模型 CRUD/搜索配置/_modelForm/引导式 onboarding/强制引导)
  // → window.KarvyModelsPanel.open()(nav 派发);boot 走 window.KarvyModelsPanel.checkSetupGate({pollSnapshot})。

  // ============ §11 决策接口结晶:你可编辑的「决策偏好」面 ============
  // 🗳 已迁 TS(源 frontend/src/decision_prefs_panel.ts:复利信号 + 确认/编辑/撤回)→ window.KarvyDecisionPrefs.open()。

  // 9.5 P3 M2:任务结果文档(点任务卡 → 看完整结果 + 去聊天)
  // ch4:从拍板卡跳进对应任务窗看全貌(context_ref)
  async function openTaskById(id) {
    const data = await _getJSON("/api/task/" + encodeURIComponent(id));
    const tk = data && data.task;
    if (tk) openTaskDetail(tk);
  }
  async function openTaskDetail(tk) {
    openMgmtModal(t("task.result_doc"));
    const body = mgmtBody(); body.innerHTML = "";
    const statusLbl = tk.status === "error" ? t("task.error")
      : tk.status === "running" ? t("task.running") : t("task.done");
    body.appendChild(el("div", { class: "mgmt-section-title",
      text: _localizeWho(tk.who) + " · " + statusLbl }));
    body.appendChild(el("div", { class: "task-detail-intent", text: tk.intent || "" }));
    const data = await _getJSON("/api/task/" + encodeURIComponent(tk.id));
    const detail = (data && data.task) || {};
    // 活动时间线(借鉴 Multica"可读的同事"):这个任务经历了什么 —— 持久、刷新/重启后仍在
    const events = detail.events || [];
    if (events.length) {
      body.appendChild(el("div", { class: "mgmt-section-title", text: t("task.timeline") }));
      const tl = el("div", { class: "task-timeline" });
      const marks = { start: "▶", step: "✓", blocked: "⚠", done: "✔", error: "✗" };
      for (const ev of events) {
        const when = new Date((ev.ts || 0) * 1000).toLocaleTimeString();
        tl.appendChild(el("div", { class: "task-ev " + (ev.kind || "") },
          el("span", { class: "task-ev-mark", text: marks[ev.kind] || "·" }),
          el("span", { class: "task-ev-time", text: when }),
          el("span", { class: "task-ev-text", text: ev.text || t("task.ev_" + (ev.kind || "step")) })));
      }
      body.appendChild(tl);
    }
    // #42 优化③「时间线→Trace 下钻」:把"信我"的叙述变成可检视证据 —— 展开看底层真实动作
    // (工具调用/事件,读的是 Trace 切片)。Devin 级 Follow 的最小版;数据本来就在,纯接线。
    const traceWrap = el("div", { class: "task-trace-wrap" });
    const traceBtn = el("button", { class: "mgmt-inline-link", text: "🔬 " + t("task.view_trace"),
      onclick: async () => {
        traceBtn.disabled = true;
        const tr = await _getJSON("/api/task/" + encodeURIComponent(tk.id) + "/trace");
        const box = el("div", { class: "task-trace" });
        const entries = (tr && tr.entries) || [];
        if (!tr || !tr.ok || !entries.length) {
          box.appendChild(el("div", { class: "mgmt-hint",
            text: (tr && tr.reason) ? tB(tr.reason) : t("task.trace_empty") }));
        }
        for (const en of entries) {
          const row = el("div", { class: "task-trace-row" });
          row.appendChild(el("span", { class: "task-trace-kind", text: en.kind || "?" }));
          if (en.tools && en.tools.length) {
            const tlist = el("div", { class: "task-trace-tools" });
            for (const c of en.tools) {
              tlist.appendChild(el("div", { class: "task-trace-tool",
                text: "· " + c.name + (c.input ? "(" + c.input + ")" : "") }));
            }
            row.appendChild(tlist);
          }
          if (en.gist) row.appendChild(el("div", { class: "task-trace-gist", text: en.gist }));
          box.appendChild(row);
        }
        traceWrap.appendChild(box);
      } });
    traceWrap.appendChild(traceBtn);
    body.appendChild(traceWrap);
    const resBox = el("div", { class: "task-detail-result" });
    if (tk.status === "running") {
      resBox.appendChild(el("span", { class: "busy-dot" }));
      resBox.appendChild(el("span", { text: " " + t("chat.executing") }));
    } else {
      const full = detail.result_full || tk.result || "";
      if (window.KarvyRender) KarvyRender.appendMarkdown(resBox, full);
      else resBox.textContent = full;
    }
    body.appendChild(resBox);
    body.appendChild(el("button", { class: "mgmt-submit",
      text: tk.conversation_id ? t("task.open_chat_topic") : t("task.open_chat"),
      onclick: async () => {
        closeMgmtModal();
        // 2e:有关联对话 → 按 id 定位它**真正所在的线**再开(工作流线挂在独立 peer 下,
        // 切群 + resume 找不到 = "追问没上下文"的根)。无 conv → 退回切场。
        // 定位键:l0 私聊轮 turn.task_id = drive trace id(→ tk.trace_id);工作流/圆桌轮
        // turn.task_id = 任务 registry id(→ tk.id)。两个 id 空间不同,先 trace_id 再回退 id。
        if (tk.conversation_id) { await openConvById(tk.conversation_id, tk.trace_id || tk.id); return; }
        await switchPeer(JSON.stringify({ domain_id: tk.domain_id, role: tk.role, agent_id: "" }));
      } }));
  }

  // ============ 9.5 P2:执行中状态条(别让你对着沉默猜)============
  let _busyEl = null;
  function showBusy() {
    clearBusy();
    const log = document.getElementById("chat-log");
    if (!log) return;
    _busyEl = el("div", { class: "chat-line system busy" },
      el("span", { class: "busy-dot" }),
      el("span", { text: t("chat.executing") }));
    log.appendChild(_busyEl);
    log.scrollTop = log.scrollHeight;
  }
  function clearBusy() {
    if (_busyEl && _busyEl.parentNode) _busyEl.parentNode.removeChild(_busyEl);
    _busyEl = null;
  }

  // ============ 对话弹窗(step5:显眼按钮 → 大对话窗,够大聊透)============
  function openChatModal() {
    const m = document.getElementById("chat-modal");
    if (!m) return;
    m.classList.remove("hidden");
    const input = document.getElementById("chat-input");
    if (input) setTimeout(() => input.focus(), 30);
  }
  function closeChatModal() {
    const m = document.getElementById("chat-modal");
    if (m) m.classList.add("hidden");
  }
  // 右下角卡皮巴拉:每隔一阵冒个省略号泡(· → ······ 循环),让人知道它能点(沟通是核心)。
  // 佛系人设:不说生硬话术,只发省略号动效。聊天开着 / 鼠标在它上面时不弹。
  function _startKarvyIdleBubble() {
    const bubble = document.getElementById("karvy-bubble");
    const dock = document.querySelector(".karvy-dock");
    if (!bubble || !dock) return;
    const dots = bubble.querySelector(".karvy-bubble-dots");
    let dotTimer = null, hideTimer = null, hovering = false;
    dock.addEventListener("mouseenter", () => { hovering = true; });
    dock.addEventListener("mouseleave", () => { hovering = false; });
    const hide = () => {
      bubble.classList.add("hidden");
      if (dotTimer) { clearInterval(dotTimer); dotTimer = null; }
    };
    const show = () => {
      const modal = document.getElementById("chat-modal");
      const chatOpen = modal && !modal.classList.contains("hidden");
      if (chatOpen || hovering) return;   // 聊天开着 / 鼠标悬上 → 不打扰
      bubble.classList.remove("hidden");
      let n = 1;
      if (dots) {
        dots.textContent = "·";
        // 卡皮巴拉是慢性子:1→6 个点用 ~15s 慢慢放完(每点 ~2.5s),不抢戏
        dotTimer = setInterval(() => { n = (n % 6) + 1; dots.textContent = "·".repeat(n); }, 2500);
      }
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(hide, 15000);   // 放 15s 收回
    };
    setTimeout(show, 4000);        // 进来 4s 先冒一次(立刻让人发现能点)
    setInterval(show, 30000);      // 放 15s + 歇 15s = 每 30s 冒一次
  }
  function setupChatModal() {
    const open = document.getElementById("chat-open");
    if (open) open.addEventListener("click", openChatModal);
    const close = document.getElementById("chat-modal-close");
    if (close) close.addEventListener("click", closeChatModal);
    const overlay = document.getElementById("chat-modal");
    if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) closeChatModal(); });
    const rt = document.getElementById("roundtable-btn");
    if (rt) rt.addEventListener("click", openRoundtable);
  }

  // ============ Intent submit (form) ============

  // 发送一条聊天(表单提交按钮 + Enter 都走这里)。从 contenteditable 读文本 + 被 @ 的角色。
  async function _submitChat() {
    const { text, mentions } = _readChatInput();
    // 多模态:抓附件(文本内联 / 图片走 images),建展示清单(缩略图,落历史),再清附件区
    const _imgs = _attachmentsImages();
    const _txtInline = _attachmentsTextInline();
    if (!text && !_attachments.length) return;   // 纯空不发;有附件(哪怕没文字)也能发
    const _qText = text || t("attach.implicit_q");
    const _manifest = await _buildAttachManifest();   // 异步:图降缩略图
    _clearAttachments();
    const send = document.getElementById("chat-send");
    if (send) send.disabled = true;
    openChatModal();
    // 乐观渲染:**真**显示发了什么(缩略图/文档块),不再只写"(带了 N 个附件)"
    if (_manifest.length) _pushUserWithAttachments(_qText, _manifest);
    else pushChatLine("user", text);
    showBusy();
    // ch4 圆桌对话式对齐(Hardy:少按钮)—— 待对齐圆桌里,你的话走 /align;小卡聊清了自己开始。
    if (_pendingRoundtable) {
      _ceClear();
      try {
        const r = await fetch("/api/roundtable/align", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: _pendingRoundtable.conv_id, message: text }),
        });
        clearBusy();
        const res = r.ok ? await r.json() : null;
        if (!res || !res.ok) { pushChatLine("system", "⚠ " + ((res && res.reason) || t("chat.http_error", { status: r.status }))); }
        else {
          _chatSpeaker = "";
          pushChatLine("agent", res.reply || "");
          if (res.started) {            // 小卡判定聊清了 → 自己开始,渲讨论结果
            _hideRoundtableBanner();
            if (res.result && res.result.ok) renderRoundtable(res.result);
            else pushChatLine("system", "⚠ " + (tB(res.result && res.result.reason) || t("rt.discuss_failed")));
          }
        }
      } catch (e) { clearBusy(); pushChatLine("system", "⚠ " + e.message); }
      if (send) send.disabled = false;
      const ce0 = _ceInput(); if (ce0) ce0.focus();
      return;
    }
    let sendText = text || (_imgs.length || _txtInline ? "请看我发的附件并回答。" : "");
    if (_pendingMaterial) {
      sendText = "[我勾选了这些料,请基于它们回答]\n" + _pendingMaterial + "\n\n[我的问题] " + text;
      _pendingMaterial = "";
    }
    // 文本/Markdown 附件 → 内联进 prompt(任何模型都吃;放问题前)
    if (_txtInline) sendText = _txtInline + "\n\n[我的问题] " + sendText;
    _ceClear();
    _hideMentionPop();
    // ch4:@ 多个角色(≥2)→ workflow 模式(小卡设计 DAG → 你拍板/编辑 → 执行)。单 @ 走定向单聊。
    if (mentions.length >= 2) {
      try {
        const r = await fetch("/api/workflow/plan", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intent: sendText, mentions: mentions }),
        });
        clearBusy();
        if (!r.ok) { pushChatLine("system", t("chat.http_error", { status: r.status })); }
        else {
          const res = await r.json();
          if (res.ok) _renderWorkflowPlan(res.plan, sendText, res.matched, mentions);   // 弹可编辑步骤表(命中则提议复用)
          else pushChatLine("system", "⚠ " + (tB(res.reason) || "plan failed"));
        }
      } catch (e) { clearBusy(); pushChatLine("system", "⚠ " + e.message); }
      if (send) send.disabled = false;
      const ce2 = _ceInput(); if (ce2) ce2.focus();
      return;
    }
    // 单 @:路由到那个角色(主响应者);带 domain 在大群里同名消歧
    const mention = mentions[0] ? mentions[0].agent_id : "";
    const mentionDomain = mentions[0] ? mentions[0].domain_id : "";
    const _attach = _manifest.length ? { q: _qText, items: _manifest } : null;
    const sent = sendWS("intent", { intent: sendText, mention: mention, mention_domain: mentionDomain, images: _imgs, attachments: _attach });
    if (!sent) {
      try {
        const r = await fetch("/api/intent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intent: sendText, mention: mention, mention_domain: mentionDomain, images: _imgs, attachments: _attach }),
        });
        if (r.ok) {
          const payload = await r.json();
          renderDriveDone(payload);
        } else {
          clearBusy();
          pushChatLine("system", t("chat.http_error", { status: r.status }));
        }
      } catch (e) {
        clearBusy();
        pushChatLine("system", "⚠ " + e.message);
      }
    }
    if (send) send.disabled = false;
    const ce = _ceInput(); if (ce) ce.focus();
  }

  // ============ 多模态附件:图(发图问)+ 文本/Markdown(发文档问)============
  // _attachments: [{kind:"image"|"text", name, dataUrl?, mediaType?, text?}]
  let _attachments = [];
  const _ATTACH_MAX = 6, _IMG_MAX_BYTES = 8 * 1024 * 1024, _TXT_MAX_CHARS = 60000;
  function _renderAttachments() {
    const box = document.getElementById("chat-attachments");
    if (!box) return;
    box.innerHTML = "";
    box.classList.toggle("hidden", _attachments.length === 0);
    _attachments.forEach((a, i) => {
      const chip = el("div", { class: "attach-chip" });
      if (a.kind === "image") chip.appendChild(el("img", { class: "attach-thumb", src: a.dataUrl, alt: a.name }));
      else chip.appendChild(el("span", { class: "attach-doc", text: "📄 " + a.name }));
      chip.appendChild(el("button", { class: "attach-x", text: "✕", title: t("attach.remove"),
        onClick: () => { _attachments.splice(i, 1); _renderAttachments(); } }));
      box.appendChild(chip);
    });
  }
  function _readFileAsync(file, asText) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result); r.onerror = rej;
      if (asText) r.readAsText(file); else r.readAsDataURL(file);
    });
  }
  async function _addFiles(files) {
    for (const f of Array.from(files || [])) {
      if (_attachments.length >= _ATTACH_MAX) { pushChatLine("system", t("attach.too_many", { n: _ATTACH_MAX })); break; }
      const isImg = (f.type || "").startsWith("image/");
      try {
        if (isImg) {
          if (f.size > _IMG_MAX_BYTES) { pushChatLine("system", t("attach.img_too_big", { name: f.name })); continue; }
          const dataUrl = await _readFileAsync(f, false);
          _attachments.push({ kind: "image", name: f.name, dataUrl, mediaType: f.type || "image/png" });
        } else {
          let txt = await _readFileAsync(f, true);
          if (txt.length > _TXT_MAX_CHARS) txt = txt.slice(0, _TXT_MAX_CHARS) + "\n…(truncated)";
          _attachments.push({ kind: "text", name: f.name, text: txt });
        }
      } catch (e) { pushChatLine("system", t("attach.read_fail", { name: f.name })); }
    }
    _renderAttachments();
  }
  function _clearAttachments() { _attachments = []; _renderAttachments(); }
  // 缩略图:canvas 把图降到 ≤160px(给历史存的小图,别撑爆 JSONL;全图照常给模型)
  function _makeThumb(dataUrl) {
    return new Promise((res) => {
      const img = new Image();
      img.onload = () => {
        const max = 160, scale = Math.min(1, max / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale)), h = Math.max(1, Math.round(img.height * scale));
        const c = document.createElement("canvas"); c.width = w; c.height = h;
        try { c.getContext("2d").drawImage(img, 0, 0, w, h); res(c.toDataURL("image/jpeg", 0.7)); }
        catch (e) { res(dataUrl); }
      };
      img.onerror = () => res(dataUrl);
      img.src = dataUrl;
    });
  }
  // 发送时的展示清单(给乐观渲染 + 落历史):图带小缩略图、文档带名
  async function _buildAttachManifest() {
    const items = [];
    for (const a of _attachments) {
      if (a.kind === "image") items.push({ kind: "image", name: a.name, thumb: await _makeThumb(a.dataUrl) });
      else items.push({ kind: "text", name: a.name });
    }
    return items;
  }
  // 把附件清单画进一条消息(气泡里 / 历史回放同一渲染)
  function _renderAttachItems(items) {
    if (!items || !items.length) return null;
    const box = el("div", { class: "chat-attachments msg-attachments" });
    for (const it of items) {
      const chip = el("div", { class: "attach-chip" });
      if (it.kind === "image" && it.thumb) chip.appendChild(el("img", { class: "attach-thumb", src: it.thumb, alt: it.name || "" }));
      else chip.appendChild(el("span", { class: "attach-doc", text: "📄 " + (it.name || "file") }));
      box.appendChild(chip);
    }
    return box;
  }
  // 一条带附件的 user 消息(问题文字 + 缩略图/文档块)
  function _pushUserWithAttachments(text, items) {
    const log = document.getElementById("chat-log");
    if (!log) return;
    const line = el("div", { class: "chat-line user" },
      el("span", { class: "role", text: t("chat.you") }));
    if (text) line.appendChild(document.createTextNode(text));
    const att = _renderAttachItems(items);
    if (att) line.appendChild(att);
    log.appendChild(line);
    if (isNearBottom(log)) log.scrollTop = log.scrollHeight;
  }
  // 文本附件 → 内联进 prompt(任何模型都吃);图片附件 → 单独走 images(视觉链路)
  function _attachmentsTextInline() {
    return _attachments.filter((a) => a.kind === "text")
      .map((a) => `[附件:${a.name}]\n${a.text}`).join("\n\n");
  }
  function _attachmentsImages() {
    return _attachments.filter((a) => a.kind === "image")
      .map((a) => ({ data_url: a.dataUrl, media_type: a.mediaType, name: a.name }));
  }

  function setupChatForm() {
    const form = document.getElementById("chat-form");
    const ce = document.getElementById("chat-input");
    const wrap = ce.closest(".chat-input-wrap") || form;
    // ch4 #1:contenteditable @ 选择器 —— 输入弹角色、键盘导航、Enter 发送
    ce.addEventListener("input", _onChatInputMention);
    ce.addEventListener("keydown", _onChatInputKeydown);
    ce.addEventListener("blur", () => setTimeout(_hideMentionPop, 120));  // 点选有 mousedown 抢先
    // 粘贴:图片 → 当附件;其余只取纯文本(防富文本/HTML 注进 contenteditable)
    ce.addEventListener("paste", (e) => {
      const items = (e.clipboardData || window.clipboardData || {}).items || [];
      const imgs = [];
      for (const it of items) { if (it.type && it.type.startsWith("image/")) { const f = it.getAsFile(); if (f) imgs.push(f); } }
      if (imgs.length) { e.preventDefault(); _addFiles(imgs); return; }
      e.preventDefault();
      const txt = ((e.clipboardData || window.clipboardData).getData("text/plain") || "");
      document.execCommand("insertText", false, txt);
    });
    // 拖拽文件进来 → 当附件
    ["dragover", "drop"].forEach((evt) => wrap.addEventListener(evt, (e) => {
      e.preventDefault();
      if (evt === "drop" && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) _addFiles(e.dataTransfer.files);
    }));
    // 📎 按钮 → 选文件
    const attBtn = document.getElementById("chat-attach-btn");
    const attInput = document.getElementById("chat-attach-input");
    if (attBtn && attInput) {
      attBtn.addEventListener("click", () => attInput.click());
      attInput.addEventListener("change", () => { _addFiles(attInput.files); attInput.value = ""; });
    }
    form.addEventListener("submit", (ev) => { ev.preventDefault(); _submitChat(); });
    _ceUpdateEmpty();
  }

  // ============ H2A demo buttons (per snapshot proposal) ============
  // Note:真实 H2A 走 PROPOSE envelope 到达时由 server push;这里 demo 是 manual trigger

  // ============ Boot ============

  function boot() {
    // 9.4 i18n:先把静态文案填成当前语言 + 挂语言切换器(默认 en)
    T.applyStatic();
    T.mountSwitcher(document.getElementById("lang-switcher"));
    setupChatForm();
    setupChatModal();   // step5:对话弹窗
    _startKarvyIdleBubble();   // 右下角卡皮巴拉定时冒省略号泡,提示可点
    connectWS();
    startPolling();
    // 9.0e:绑"看建议"按钮
    const proposeBtn = document.getElementById("propose-btn");
    if (proposeBtn) proposeBtn.addEventListener("click", requestProposal);
    // predict(你可能想做)手动刷新:现在就问一次(WS propose,回退 POST /api/propose)
    const predictRefreshBtn = document.getElementById("predict-refresh-btn");
    if (predictRefreshBtn) predictRefreshBtn.addEventListener("click", requestProposal);
    // ch4 #4:点钱包 → token 统计弹窗
    const tokMeter = document.getElementById("token-meter");
    if (tokMeter) tokMeter.addEventListener("click", () => window.KarvyTokens.open());
    // 9.1d:绑对话控件(➕新对话 / 🕘历史)
    const newBtn = document.getElementById("conv-new-btn");
    if (newBtn) newBtn.addEventListener("click", newConversation);
    const histSel = document.getElementById("conv-history");
    if (histSel) histSel.addEventListener("change", (e) => {
      const v = e.target.value; e.target.selectedIndex = 0;   // 选完复位成 🕘 图标,不显长标题
      if (v) resumeConversation(v);
    });
    // 9.2b:绑场+角色 picker
    const peerSel = document.getElementById("peer-picker");
    if (peerSel) peerSel.addEventListener("change", (e) => switchPeer(e.target.value));
    // (移除冗余的"🏢 建域"按钮:左导航「业务域」面板已能新建业务域,这个是历史遗产,Hardy)
    // 9.5 #3:左导航管理面(原子库 / 角色库 / 业务域)
    setupMgmtPanels();
    refreshPeers();
    refreshConversations();
    // 立即拉 1 次
    pollSnapshot();
    pollStats();
    pollChatHistory();
    pollTasks();
    fetchPendingProposals();   // 待你拍的板跨刷新存活(不靠 WS 在线推)
    fetchRecentDecisions();    // 最近拍板流水(只读回看)
    fetchUpdateStatus();       // 有新版 → 顶部横幅(绝不自动升级)
    window.KarvyModelsPanel.checkSetupGate({ pollSnapshot });   // 无 Key → 强制引导录入模型(进系统就判)
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
