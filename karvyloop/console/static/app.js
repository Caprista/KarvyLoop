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
  let _wsEverConnected = false;   // 断线恢复:区分首连(启动已拉过历史)vs 重连(要补拉)

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      console.log("[ws] connected");
      wsReconnectDelay = 1000;
      // 断线恢复:重连时补拉 chat_history —— 断线窗口里 drive 在服务端照跑完(worker 线程,
      // 不系在这条 WS 上),完成的回合已落 chat_history(带结构化 events),但那条 drive_done
      // 广播给的是**当时在线**的 socket,断开的这个错过了。renderChatHistory 从权威持久历史整段
      // 重建(幂等),把断线期间跑完的回合补回来 —— 灭「怎么样了?」反模式的断线死角。
      // 逐字草稿是纯装饰(终态以 chat_history/drive_done 为准),丢了无所谓,不需重放增量。
      // 首连不重复拉(启动 init 已 pollChatHistory 一次);仅重连补。
      if (_wsEverConnected) { pollChatHistory(); }
      _wsEverConnected = true;
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
      // ch4:token 成本表(已迁 TS)。T4 懒加载:boot 已 ensure(tokens),首拍可能还在载入 → 守空
      if (window.KarvyTokens) window.KarvyTokens.pollMeter();
    }, 2000);
    pollKnowledge();    // 首屏立即拉一次
    if (window.KarvyTokens) window.KarvyTokens.pollMeter();
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
    // drawflow.min.css 与 JS 同点按需注入(docs/83 顺手项:首屏不再常驻这份画布样式)
    if (!document.getElementById("drawflow-css")) {
      const l = document.createElement("link");
      l.id = "drawflow-css";
      l.rel = "stylesheet";
      l.href = "/static/vendor/drawflow.min.css";
      document.head.appendChild(l);
    }
    _wfCanvasLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/static/workflow_canvas.js";
      s.onload = () => (window.KarvyWorkflowCanvas ? resolve() : reject(new Error("canvas global missing")));
      s.onerror = () => { _wfCanvasLoading = null; reject(new Error("workflow_canvas.js load failed")); };
      document.head.appendChild(s);
    });
    return _wfCanvasLoading;
  }

  // ============ T4(docs/83):面板脚本懒加载 ============
  // 首屏脚本里,面板脚本(devices 63K / memory 51K / skills 38K / models / external / …
  // 合计 ~250KB)只有开对应面板才用 —— 弱机上白吃解析/编译。照 _ensureWorkflowCanvas /
  // _ensureDriverJs 同一范式:面板首开时注入,以**全局契约真出现**为准(不是 onload 就信),
  // 失败清缓存可重试(网络恢复后再点就好)。
  // 注册表:name(=data-panel 名)→ { src, global(载入后必须出现的 window.* 契约),
  //   deps(该面板内部会直调的兄弟面板脚本,先载齐 —— 全局函数是脚本载入后才有的) }。
  const _PANEL_SCRIPTS = {
    domains: { src: "/static/domains_panel.js", global: "KarvyDomainsPanel", deps: ["roles"] },  // 「新建角色」直调 KarvyRolesPanel
    roles: { src: "/static/roles_panel.js", global: "KarvyRolesPanel" },
    atoms: { src: "/static/atoms_panel.js", global: "KarvyAtomsPanel" },
    agents: { src: "/static/agents_panel.js", global: "KarvyAgentsPanel" },
    external: { src: "/static/external_panel.js", global: "KarvyExternalPanel" },
    devices: { src: "/static/devices_panel.js", global: "KarvyDevicesPanel" },
    memory: { src: "/static/memory_panel.js", global: "KarvyMemoryPanel" },
    decision_prefs: { src: "/static/decision_prefs_panel.js", global: "KarvyDecisionPrefs" },
    skills: { src: "/static/skills_panel.js", global: "KarvySkillsPanel" },
    models: { src: "/static/models_panel.js", global: "KarvyModelsPanel" },   // boot 也 ensure(无 Key 强制引导)
    diagnose: { src: "/static/diagnose_panel.js", global: "KarvyDiagnosePanel" },
    files: { src: "/static/files_panel.js", global: "KarvyFilesPanel" },
    schedules: { src: "/static/schedules_panel.js", global: "KarvySchedulesPanel" },
    pursuits: { src: "/static/pursuits_panel.js", global: "KarvyPursuitsPanel" },  // 🎯 我的追求(左导航第 14 项「你的团队」组 + 决策舱列头就近入口,docs/88 第三刀)
    demo: { src: "/static/demo_panel.js", global: "KarvyDemoPanel" },     // 👀 顶栏入口(脚本载入时自绑 #demo-open)
    tokens: { src: "/static/tokens_panel.js", global: "KarvyTokens" },    // 💰 顶栏 meter:boot 即 ensure,meter 不长睡
  };
  const _panelScriptLoading = {};   // name → Promise(载入中缓存;失败清空可重试)
  function _ensurePanelScript(name) {
    const spec = _PANEL_SCRIPTS[name];
    if (!spec) return Promise.reject(new Error("unknown panel script: " + name));
    const deps = (spec.deps || []).map(_ensurePanelScript);
    let own;
    if (window[spec.global]) own = Promise.resolve();
    else if (_panelScriptLoading[name]) own = _panelScriptLoading[name];
    else {
      own = _panelScriptLoading[name] = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.id = "panel-js-" + name;   // 防重复注入锚(载入中重入由上面的 Promise 缓存挡)
        s.src = spec.src;
        s.onload = () => (window[spec.global] ? resolve() : reject(new Error(spec.global + " global missing")));
        s.onerror = () => { _panelScriptLoading[name] = null; s.remove(); reject(new Error(spec.src + " load failed")); };
        document.head.appendChild(s);
      });
    }
    return Promise.all(deps.concat([own]));
  }
  // 面板打开统一走它:先 ensure、回调里才取全局(提前解引用必 undefined)。
  // 加载失败给人话提示(不留静默死按钮);fn 自身的错误不吞,照旧往上冒。
  function _openLazyPanel(name, fn) {
    return _ensurePanelScript(name).then(fn, (e) => {
      console.error("[panel] " + name + " script load failed", e);
      alert(t("panel.load_failed", { name: name }));
    });
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
      // 手动求建议的回执:payload=null 表示"小卡看过、暂时没有"(只发给点的人)→ 按钮如实说
      _proposeSettled(!!msg.payload);
      _routeProposal(msg.payload);
    } else if (msg.type === "h2a_envelope") {
      console.log("[h2a] envelope", msg.payload);
      // D5:回显兑现结果(让 ACCEPT 不再是"空响应")
      const d = msg.payload && msg.payload.dispatch;
      if (d) pushChatLine("system", t("proposal.dispatch", { kind: d.kind, detail: tB(d.detail) }));
      // 文件管家第一课:引荐卡 ACCEPT 真入住成功 → 顺势递上第一任务 chip(wow 时刻入口)
      if (d && d.ok && d.kind === "resident_referral" && msg.payload.decision === "ACCEPT") {
        _butlerOfferFirstLesson();
      }
      // 决策已发 → **只撤刚拍的那张卡**(带 proposal_id),保留还挂着的兄弟卡(多卡不覆盖);
      // 若兑现产了执行后回报卡,就地追加"它到底验过没";列真空了才回填"已处置"空态。
      const pid = (msg.payload && (msg.payload.proposal_id || (d && d.proposal_id))) || "";
      _finalizeInlineCards(pid, null);   // S3:聊天流里的同卡同步转终态(本端拍的已终态,幂等跳过)
      const list = document.getElementById("h2a-list");
      if (list) {
        _removeCardById(list, pid, true);   // P0-2:拍板后的卡动画退场(非幂等重推)
        if (msg.payload && msg.payload.report_card) _renderReportCard(list, msg.payload.report_card);
        // 退场动画 160ms 后再判空回填(动画中的节点还在 DOM,立刻数会把空态吞掉)
        setTimeout(() => {
          _regroupChains(list);     // docs/92 刀1:拍完出组 —— 组内剩 1 张时组壳解散回普通单卡
          _refreshDecideFilter();   // #6:卡拍掉了 → 重算筛选条(distinct<2 会自动收起 / 选中类拍光则复位)
          if (_countCards("h2a-list", "h2a-empty") === 0) {
            list.innerHTML = '<div class="h2a-empty">' + t("h2a.handled") + '</div>';
          }
        }, 200);
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
    } else if (msg.type === "ambient_recall") {
      // ⑤c 环境感知召回:相关技能/知识主动浮出到工作台"料"区(新 intent 的料替换旧料)
      renderAmbientRecall(msg.payload);
    } else if (msg.type === "silence_notice") {
      // 挣来的静音(docs/49):已授权桶的卡被小卡按你的口味静音处理了 → 轻通知(不打断,可回看)。
      onSilenceNotice(msg.payload);
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
  // 挣来的静音轻通知:小卡已按你的口味替你处理了一张已授权桶的卡。**轻**——一条系统提示,
  // 不弹卡、不打断;附一个「翻案」入口(最强负信号:推翻这次静音 → 连坐吊销整桶授权)。
  function onSilenceNotice(p) {
    if (!p) return;
    const log = document.getElementById("chat-log");
    if (!log) return;
    const follow = isNearBottom(log);
    const kind = tB(p.kind || "") || (p.kind || "?");
    const dom = (p.domain || "").trim();
    const key = p.ok === false ? "silence.notice_failed" : "silence.notice";
    const notice = el("div", { class: "chat-notice silence-notice" });
    notice.appendChild(document.createTextNode(t(key, {
      kind: kind, domain: dom ? t("silence.in_domain", { domain: dom }) : "",
      detail: tB(p.detail || "") })));
    // 翻案:仅对成功静音处理的卡给(失败的没执行,无需翻);带 proposal_id 才可翻。
    const pid = p.proposal_id || "";
    if (p.ok !== false && pid) {
      const btn = el("button", { class: "silence-overturn", text: t("silence.overturn"),
        onClick: async () => {
          if (!window.confirm(t("silence.overturn_confirm"))) return;
          btn.disabled = true;
          try {
            const r = await _postJSON("/api/silence/overturn", { proposal_id: pid });
            btn.textContent = (r.ok && r.data && r.data.ok) ? t("silence.overturned") : t("silence.overturn_gone");
          } catch (e) { btn.textContent = t("silence.overturn_gone"); }
        } });
      notice.appendChild(document.createTextNode(" "));
      notice.appendChild(btn);
    }
    log.appendChild(notice);
    if (follow) log.scrollTop = log.scrollHeight;
  }
  function onSystemError(p) {
    if (!p) return;
    // #54 逃生门:重启挂起的中断流程经 system_error(source=workflow_resume)冒泡 →
    // 拉挂起清单渲染成顶部可操作横幅(续跑/丢弃),不当普通后台错。
    if (p.source === "workflow_resume") { fetchPendingResume(); return; }
    pushChatLine("system", t("system.bg_error", { source: p.source || "?", err: p.message || "" }));
    // L1 自愈:出错就给一个"🩺 诊断"入口 —— 用活着的模型把问题翻成人话 + 提修法(只提议不执行)
    const log = document.getElementById("chat-log");
    if (!log) return;
    const btn = el("button", { class: "ops-diagnose-btn", text: t("ops.diagnose_btn"),
      onClick: async () => {
        btn.disabled = true; btn.textContent = t("ops.diagnosing");
        try {
          const d = await _getJSON("/api/ops/diagnose");
          // T4 懒加载:诊断卡渲染住在懒加载的 diagnose 面板脚本里,用前先 ensure
          if (d && d.diagnosis) {
            await _ensurePanelScript("diagnose");
            window.KarvyDiagnosePanel.renderOpsDiagnosis(log, d.diagnosis);
          }
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
      // 微动效 P0-1:逐 chunk 物化(150ms 浮现,"正在对我说"而不是打印机);顺手修隐患:
      // 旧 textContent += 是 setter,会把已插入的 live-tool/💭 子节点整个吞掉。
      // 仍是纯文本节点(el text= 走 textContent)= 安全;终态照旧整体换 markdown 权威版。
      box.appendChild(el("span", { class: "stream-chunk", text: ev.text }));
      if (follow && log) log.scrollTop = log.scrollHeight;
    } else if (ev.type === "tool_call") {
      const box = _ensureLiveStream(); if (!box) return;
      box.appendChild(el("div", { class: "live-tool", text: "🔧 " + (ev.name || "tool") }));
      if (log) log.scrollTop = log.scrollHeight;
    } else if (ev.type === "thinking_delta") {
      // P4:推理中 → 草稿里显一次"💭 思考中…"(完整推理在终态折叠卡);不逐字铺(太吵)
      const box = _ensureLiveStream(); if (!box) return;
      if (!box.querySelector(".live-thinking")) {
        // 微动效 P1-6:文案里的静态「…」换成三点相位呼吸(进行中状态指示,同骨架屏例外;
        // 元素随流式草稿终态整体移除即停,绝无 idle 循环)。i18n 文案本身不动,只剥尾部省略号。
        box.appendChild(el("div", { class: "live-thinking" },
          t("render.thinking_live").replace(/(\.{3}|…)\s*$/, ""),
          el("span", { class: "kv-dots", "aria-hidden": "true" },
            el("span", { text: "." }), el("span", { text: "." }), el("span", { text: "." }))));
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
  // replay:这是**状态回放**不是新卡事件 —— 桌面视图的叼卡剧场只回应真事件,
  // 回放只把卡摆回列表(Hardy 实拍:开屏卡皮巴拉被存量 pending 卡拽去演一趟 = 病根在这)。
  async function fetchPendingProposals() {
    try {
      const r = await fetch("/api/proposals/pending");
      if (!r.ok) return;
      const data = await r.json();
      (data.proposals || []).forEach((p) => _routeProposal(p, { replay: true }));   // 按 kind 分流(拍板/预判)
      _refreshDecideFilter();   // #6:开机回放存量卡后一次性算筛选条(多 kind 积压才现身)
    } catch (e) {
      console.warn("[boot] pending proposals failed", e);
    }
  }

  // 版本检测:有新版 → 顶部可关掉的横幅(detect→notify→你按下,绝不自动升级)。
  // 关掉后按版本记进 localStorage,同一版本不再骚扰(notify ≠ nag)。
  // 常显当前运行版本(顶栏品牌旁):一眼可判"我在哪个版本",不用靠横幅反推(Hardy 反馈:
  // 一键升级失败却只剩模糊横幅,人被晾在"到底升没升成"里 —— 版本号常驻 = 判定的唯一真源)。
  function _setBrandVersion(cur) {
    const bv = document.getElementById("brand-version");
    if (bv && cur) bv.textContent = "v" + cur;
  }
  async function fetchUpdateStatus() {
    try {
      const r = await fetch("/api/update_status");
      if (!r.ok) return;
      const u = await r.json();
      if (u && u.current) _setBrandVersion(u.current);   // 常显当前版本(成功/失败都先落这个真源)
      // fail-loud(不静默、不止 10 分钟):上次升级/回滚**没成功**且你**仍没到目标版本** → 明确告知
      // 为什么没升成,而不是只弹一条模棱两可的"有新版"。current==to 时抑制(其实已到目标 = 过期误报)。
      const lu = u && u.last_upgrade;
      const lastFailed = !!(lu && lu.ok === false && lu.to && String(u.current) !== String(lu.to));
      if (!u || !u.newer || !u.latest) {
        // 无新版可提示;但若上次确实失败了仍要 fail-loud(稳妥兜底,正常此时 newer 应为 true)
        if (lastFailed && !document.getElementById("update-banner")) _showUpdateBanner(u, lu);
        return;
      }
      let dismissed = null;
      try { dismissed = localStorage.getItem("karvyloop_update_dismissed"); } catch (e) {}
      // 失败态**永远**显示(不被"这版忽略过"压掉 —— 你需要知道为什么没升成、能不能重试)
      if (dismissed === u.latest && !lastFailed) return;
      _showUpdateBanner(u, lastFailed ? lu : null);
    } catch (e) {
      /* 检测失败静默(本地优先,不打扰) */
    }
  }
  // failInfo 非空 = 上次升级/回滚失败(fail-loud 红条,升级按钮变"重试");null = 普通"有新版"。
  function _showUpdateBanner(u, failInfo) {
    if (document.getElementById("update-banner")) return;
    const bar = el("div", { class: "update-banner" + (failInfo ? " update-banner-err" : ""), id: "update-banner" });
    let msgText;
    if (failInfo) {
      const reason = failInfo.msg || failInfo.rollback_reason || "";
      msgText = failInfo.rolled_back
        ? t("update.rolled_back", { reason })
        : t("update.last_failed", { current: u.current, latest: u.latest, reason });
    } else {
      msgText = t("update.banner", { current: u.current, latest: u.latest });
    }
    bar.appendChild(el("span", { class: "update-banner-msg", text: msgText }));
    // 一键升级/重试:点了才升(=手动,不是静默自动);点完后端跑 停→装→起 整套,不用敲命令
    if (u.newer) bar.appendChild(el("button", { class: "update-go", text: t(failInfo ? "update.retry_btn" : "update.upgrade_btn"),
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
  // #54 逃生门(docs/56 ②):重启后没自动复活的中断 workflow → 顶部横幅让人「续跑/丢弃」。
  // 后端 GET /api/workflow/pending_resume 返挂起清单;/resume|/discard 逐条处置。
  // 不自动烧 token(逃生门本意):人不点就一直挂着,点了才动。
  async function fetchPendingResume() {
    try {
      const r = await fetch("/api/workflow/pending_resume");
      if (!r.ok) return;
      const d = await r.json();
      const pending = (d && d.pending) || [];
      _showPendingResumeBanner(pending);
    } catch (e) { /* 查失败静默(本地优先,不打扰) */ }
  }
  function _showPendingResumeBanner(pending) {
    const bar0 = document.getElementById("resume-banner");
    if (bar0) bar0.remove();                 // 每次重画(处置一条后刷新剩余数)
    if (!pending || !pending.length) return;
    const bar = el("div", { class: "update-banner resume-banner", id: "resume-banner" });
    bar.appendChild(el("span", { class: "update-banner-msg",
      text: t("resume.banner", { n: pending.length }) }));
    // 逐条:标题 + 「续跑」「丢弃」
    const list = el("div", { class: "resume-list" });
    for (const p of pending) {
      const rid = p.run_id || "";
      const row = el("div", { class: "resume-row" });
      row.appendChild(el("span", { class: "resume-title", text: p.title || p.goal || rid || "?" }));
      const resumeBtn = el("button", { class: "update-go", text: t("resume.resume_btn"),
        onClick: async (e) => {
          e.target.disabled = true;
          const rr = await _postJSON("/api/workflow/resume", { run_id: rid });
          if (rr.ok && rr.data && rr.data.ok !== false) {
            pushChatLine("system", t("resume.resumed", { title: p.title || rid }));
          } else { pushChatLine("system", t("resume.failed")); e.target.disabled = false; return; }
          fetchPendingResume();              // 刷新剩余
          pollTasks();
        } });
      const discardBtn = el("button", { class: "update-rollback", text: t("resume.discard_btn"),
        onClick: async (e) => {
          if (!confirm(t("resume.discard_confirm"))) return;
          e.target.disabled = true;
          const rr = await _postJSON("/api/workflow/discard", { run_id: rid });
          if (rr.ok && rr.data && rr.data.ok !== false) {
            pushChatLine("system", t("resume.discarded", { title: p.title || rid }));
          } else { pushChatLine("system", t("resume.failed")); e.target.disabled = false; return; }
          fetchPendingResume();
        } });
      row.appendChild(resumeBtn); row.appendChild(discardBtn);
      list.appendChild(row);
    }
    bar.appendChild(list);
    bar.appendChild(el("button", { class: "update-x", text: "✕", onClick: () => bar.remove() }));
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
        const row = _markIn("recent", (d.ts || "") + "|" + (d.kind || "") + "|" + (d.decision || ""),
          el("div", { class: "recent-row" }));
        row.appendChild(el("span", { class: "recent-badge recent-" + (d.decision || "").toLowerCase(),
          text: _DECISION_BADGE[d.decision] || "·" }));
        row.appendChild(el("span", { class: "recent-summary", text: d.summary || d.proposal_id || "" }));
        row.appendChild(el("span", { class: "recent-time", text: _relTime(d.ts) }));
        // 决策时间线主入口(docs/85):拍过的板可点开回放"怎么建成的";没 proposal_id 的
        // 老流水(极老数据)不可点(诚实:没键联不回去)。待决卡不放入口(决策没建成别催)。
        if (d.proposal_id) {
          row.classList.add("recent-click");
          row.title = t("dlife.entry_title");
          row.addEventListener("click", () => openDecisionLifeline(d.proposal_id, d.summary || ""));
        }
        list.appendChild(row);
      });
    } catch (e) {
      console.warn("[recent-decisions] fetch failed", e);
    }
  }

  // ============ 决策卡:执行→可判断的翻译层 + 逼判断闸 ============

  // 拉一张决策卡并渲染进 container:已核验区(接地✓/✗)/ 小卡复述区(标未核验)/ 逐条认改删。
  // engaged(改或删过任一依据)写回 judgeState —— 决定回喂 + 反投降是否计数。
  function _renderDecisionCard(container, proposalId, judgeState, ui) {
    ui = ui || {};
    fetch("/api/decision_card?proposal_id=" + encodeURIComponent(proposalId))
      .then((r) => r.json())
      .then((res) => {
        if (!res || !res.ok || !res.card) return;   // 没卡 = 沉默,不打扰
        const c = res.card;
        // 把"逼判断"所需的态记进 judgeState:决策前(decide)据此在**拍之前**拦
        judgeState.highValue = !!c.high_value;
        judgeState.hvStandard = c.high_value_standard || "";
        judgeState.needsRecheck = !!c.needs_recheck;
        // 登场编排 = 全站唯一的大动作:只给**第一次**出现的卡(重渲染不重放,不打断在读的人)
        if (!window._dcardSeen) window._dcardSeen = new Set();
        const _fresh = !window._dcardSeen.has(proposalId);
        window._dcardSeen.add(proposalId);
        const box = el("div", { class: "dcard" + (c.high_value ? " dcard-highvalue" : "") + (_fresh ? " dcard-in" : "") });
        // Cut 2 违背即拦:踩了你定的标准。docs/90 刀1b 起收进折叠,但 decide() ACCEPT 前强制展开+确认
        // (Hardy「像 App 安全协议强制阅读后才能拍」)—— 存进 judgeState 供那道门读。
        const violations = c.violations || [];
        judgeState.violations = violations;
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
            // kind 标签走 i18n(en/zh 随界面语言;dpref.kind_* 与决策偏好面板同键)——
            // 服务端 kind_label 是硬编码中文,只作 kind 缺失时的兜底,别让英文界面冒中文
            const kindKey = "dpref.kind_" + (p.kind || "");
            const kindLbl = (p.kind && t(kindKey) !== kindKey) ? t(kindKey) : (p.kind_label || "");
            row.appendChild(el("span", { class: "dcard-pref-kind", text: "[" + kindLbl + "]" }));
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
          // 楔子的脸(Hardy 2026-07-20 拍):详情折叠了,但摘要行留一句**可见** chip「🧭 已按你 N 条标准对齐」
          // —— 系统在长成你,别藏进折叠;点它直接展开看是哪几条 + 回执。
          if (ui.chipSlot) {
            const n = prefs.length + (c.aligned_omitted > 0 ? c.aligned_omitted : 0);
            const chip = el("button", { class: "dcard-aligned-chip", type: "button",
              text: t("dcard.aligned_chip", { n: n }),
              onClick: () => { if (ui.openFold) ui.openFold(); } });
            ui.chipSlot.appendChild(chip);
          }
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
        // 💬 追问(docs/77 可追问决策卡):opt-in,不挡两键快路径。就这张卡问小卡,答案锚卡证据、
        // 中立不推 ACCEPT(后端 system prompt 守)。追问后仍是同一张卡的同一个拍板口(问责单点)。
        box.appendChild(_dcardAsk(proposalId));
        container.appendChild(box);
        // ⚠ 注意标:有违背 / 无脑拍 streak / 高价值 → 折叠 toggle 变红加「⚠ 有要你确认的」,
        // 让折叠是「强制阅读前的提示」而非「措手不及」(拍时 decide() 再弹门)。
        if (ui.foldToggle && (violations.length || c.needs_recheck || c.high_value)) {
          ui.foldToggle.classList.add("has-attention");
          if (ui.foldToggle.getAttribute("aria-expanded") !== "true") {
            ui.foldToggle.textContent = t("dcard.attention") + " · " + t("dcard.details") + " ▾";
          }
        }
      })
      .catch(() => {})   // 拉卡失败不挡拍板(降级到老提案卡)
      .finally(() => {
        // ready 必在所有路径 resolve(成功/无卡/报错)—— decide() ACCEPT 等它,绝不永久挂起
        judgeState.loaded = true;
        if (judgeState._resolveReady) judgeState._resolveReady();
      });
  }

  // 决策卡的追问区:折叠入口 → 展开输入 + 问答气泡。单飞互斥;失败诚实提示。
  function _dcardAsk(proposalId) {
    const wrap = el("div", { class: "dcard-ask" });
    const toggle = el("button", { class: "dcard-ask-toggle", text: t("dcard.ask_toggle") });
    const panel = el("div", { class: "dcard-ask-panel hidden" });
    const log = el("div", { class: "dcard-ask-log" });
    const inp = el("input", { class: "dcard-ask-input", type: "text", maxlength: "1000" });
    inp.placeholder = t("dcard.ask_ph");
    const send = el("button", { class: "dcard-ask-send", text: t("dcard.ask_send") });
    const transcript = [];
    let busy = false;
    const ask = async () => {
      const q = inp.value.trim();
      if (!q || busy) return;
      busy = true; inp.value = ""; inp.disabled = true;
      log.appendChild(el("div", { class: "dcard-ask-you", text: q }));
      transcript.push({ who: "user", text: q });
      const thinking = el("div", { class: "dcard-ask-karvy dcard-ask-thinking", text: t("dcard.ask_thinking") });
      log.appendChild(thinking); log.scrollTop = log.scrollHeight;
      try {
        const res = await _postJSON("/api/decision_card/ask", { proposal_id: proposalId, question: q, transcript: transcript.slice(0, -1) });
        thinking.remove();
        const reply = (res && res.ok && res.data && res.data.ok && (res.data.reply || "").trim())
          ? String(res.data.reply).trim() : t("dcard.ask_failed");
        log.appendChild(el("div", { class: "dcard-ask-karvy", text: reply }));
        transcript.push({ who: "karvy", text: reply });
      } catch (e) {
        thinking.remove();
        log.appendChild(el("div", { class: "dcard-ask-karvy", text: t("dcard.ask_failed") }));
      } finally {
        busy = false; inp.disabled = false; log.scrollTop = log.scrollHeight;
      }
    };
    toggle.addEventListener("click", () => {
      panel.classList.toggle("hidden");
      if (!panel.classList.contains("hidden")) setTimeout(() => inp.focus(), 30);
    });
    send.addEventListener("click", () => { void ask(); });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") void ask(); });
    panel.appendChild(log);
    panel.appendChild(el("div", { class: "dcard-ask-bar" }, inp, send));
    wrap.appendChild(toggle);
    wrap.appendChild(panel);
    return wrap;
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

  // ACCEPT 前等 decision_card 的 ready(核对你标准回来),但设兜底超时:真卡死也不永久挂住 ACCEPT。
  // resolve(true)=核对回来了;resolve(false)=超时没等到 —— 调用方**不许静默放行**(fail-loud:
  // 超时要明着问"不等核对直接拍?"),否则慢 LLM 就成了绕过违背门的通道(对抗验收 B3 实锤过)。
  function _readyWithin(js, ms) {
    if (!js || !js.ready || js.loaded) return Promise.resolve(true);
    let to;
    const timeout = new Promise((res) => { to = setTimeout(() => res(false), ms); });
    return Promise.race([js.ready.then(() => true), timeout])
      .then((ok) => { if (to) clearTimeout(to); return ok || !!js.loaded; });
  }
  // 让浏览器先渲染一帧再弹阻塞式 window.confirm(否则 confirm 同步阻塞主线程,刚展开的红 banner 来不及画)。
  function _nextPaint() {
    return new Promise((res) => {
      if (typeof requestAnimationFrame !== "function") { setTimeout(res, 16); return; }
      requestAnimationFrame(() => requestAnimationFrame(res));
    });
  }

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
    // 微动效 P1-7:回报卡到场 = kv-rise + 一次 accent 描边淡入(report-in,只在 envelope
    // 追加这一次挂;比决策卡 dcard-in 轻 —— 无 scale/无 ping,不抢主角)
    const box = el("div", { class: "dcard report-card report-in" });
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
  const _MOTION_REDUCED = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: true };
  // 微动效 P0-2:卡被处置的退场(160ms 淡出+微降)——拍板的因果被看见,不是凭空蒸发。
  // 幂等重推(_placeCard 同 id 换新)走瞬删(animate 不传),否则新旧两张短暂同屏。
  function _removeCardById(list, proposalId, animate) {
    if (!list || !proposalId) return;
    // docs/92 刀1:卡可能被收进同链组壳(.h2a-chain-group)里 → 深查不只扫直系子级。
    // 语义不变:同 id 的卡(无论在不在组里)撤下;组壳自身无 data-proposal-id,不受影响。
    Array.from(list.querySelectorAll("[data-proposal-id]")).forEach((ch) => {
      if (!(ch.getAttribute && ch.getAttribute("data-proposal-id") === String(proposalId))) return;
      if (animate && !_MOTION_REDUCED.matches && ch.animate) {
        ch.animate([{ opacity: 1, transform: "none" },
                    { opacity: 0, transform: "translateY(-4px) scale(.98)" }],
          { duration: 160, easing: "cubic-bezier(0.4,0,1,1)" }).finished
          .catch(() => {}).finally(() => ch.remove());
      } else {
        list.removeChild(ch);
      }
    });
  }
  function _placeCard(list, proposalId, card) {
    card.setAttribute("data-proposal-id", String(proposalId));
    _removeCardById(list, proposalId);   // 同 id 先撤旧卡(幂等重推不叠)
    list.appendChild(card);
  }

  // ── docs/92 刀1:右栏同链组折叠(纯视觉收纳,不丢拍板粒度)──
  // 同链键(data-chain-key)且 ≥2 张待决 → 收成一组:组头「🔗 关于:{链源意图} — {n} 件待拍」
  // + 空理解保护句(「这些都来自你说的『…』」,直引链源意图,零 LLM)。组只是 DOM 分组壳:
  // 卡的 id/事件/生命周期不变(原节点整体移动、不重建 → _buildProposalCard/decide/懒加载照旧);
  // 每张卡仍独立拍(独立 h2a_decision),拍完出组,组内剩 1 张时组壳解散回普通单卡。
  // 高风险卡(data-high-risk,后端按 silence.HIGH_RISK_KINDS 判)**永远展开置顶**在组头下
  // 的 chain-pin 区,绝不收进折叠体(同刀1b 安全不折叠哲学);组头带红标 ⚠。
  // 聊天流 inline 卡(S3 双面出)不经此 —— 组折叠只做右栏 #h2a-list。
  const _chainOpen = {};   // 会话级展开态:chainKey → true(默认收起、组头可见;不记 localStorage)
  function _chainEsc(k) {
    return (window.CSS && CSS.escape) ? CSS.escape(String(k)) : String(k).replace(/["\\]/g, "\\$&");
  }
  function _setChainOpen(group, open) {
    _chainOpen[group.getAttribute("data-chain") || ""] = !!open;
    group.classList.toggle("chain-open", !!open);
    const body = group.querySelector(".chain-body");
    if (body) body.hidden = !open;
    const head = group.querySelector(".chain-head");
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
    const tog = group.querySelector(".chain-toggle");
    if (tog) tog.textContent = open ? "▴" : "▾";
  }
  function _buildChainGroup(key) {
    const group = el("div", { class: "h2a-chain-group" });
    group.setAttribute("data-chain", String(key));
    const head = el("div", { class: "chain-head", role: "button", tabindex: "0" },
      el("span", { class: "chain-risk hidden", text: "⚠", title: t("chain.group_risk") }),
      el("span", { class: "chain-head-text" }),
      el("span", { class: "chain-toggle", "aria-hidden": "true", text: "▾" }));
    const toggle = () => _setChainOpen(group, !group.classList.contains("chain-open"));
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
    });
    group.appendChild(head);
    group.appendChild(el("div", { class: "chain-protect" }));   // 空理解保护句(组头下常显)
    group.appendChild(el("div", { class: "chain-pin" }));       // 高风险区:永远展开、置顶
    const body = el("div", { class: "chain-body" });            // 折叠体:普通成员逐卡原样渲染
    body.hidden = true;
    group.appendChild(body);
    return group;
  }
  function _regroupChains(list) {
    if (!list || list.id !== "h2a-list") return;
    // 1) 按链键归堆(只看真正的决策卡;组壳/空态/提示/回报卡都没有 data-chain-key)
    const byChain = new Map();
    Array.from(list.querySelectorAll(".h2a-card[data-chain-key]")).forEach((c) => {
      const k = c.getAttribute("data-chain-key") || "";
      if (!k) return;
      if (!byChain.has(k)) byChain.set(k, []);
      byChain.get(k).push(c);
    });
    // 2) 掉到 <2 张的组壳解散:成员放回列表原位、壳移除(拍完出组 → 回普通单卡,0 视觉回归)
    Array.from(list.querySelectorAll(".h2a-chain-group")).forEach((g) => {
      const members = byChain.get(g.getAttribute("data-chain") || "") || [];
      if (members.length >= 2) return;
      members.forEach((c) => { c.style.removeProperty("display"); list.insertBefore(c, g); });
      g.remove();
    });
    // 3) ≥2 张同链 → 建/更新组壳,成员按高风险分区收纳(高风险进 pin 永不折,其余进折叠体)
    byChain.forEach((members, k) => {
      if (members.length < 2) return;
      let g = list.querySelector('.h2a-chain-group[data-chain="' + _chainEsc(k) + '"]');
      if (!g) {
        g = _buildChainGroup(k);
        list.insertBefore(g, members[0]);   // 壳落在第一张成员卡的位置(不跳排序)
      }
      const pin = g.querySelector(".chain-pin");
      const body = g.querySelector(".chain-body");
      let hasRisk = false;
      members.forEach((c) => {
        const risky = c.getAttribute("data-high-risk") === "1";
        hasRisk = hasRisk || risky;
        const target = risky ? pin : body;
        if (c.parentNode !== target) target.appendChild(c);
      });
      // 组头文案:链源意图 = 后端 chain_intent(直引原话);缺了(老卡)退最早成员的摘要
      let intent = "";
      members.forEach((c) => { if (!intent) intent = c.getAttribute("data-chain-intent") || ""; });
      if (!intent) {
        const s = members[0].querySelector(".h2a-summary");
        intent = (s ? s.textContent.replace(/^💡\s*/, "") : "").slice(0, 60);
      }
      const headText = g.querySelector(".chain-head-text");
      if (headText) headText.textContent = "🔗 " + t("chain.group_head", { intent: intent, n: members.length });
      const protect = g.querySelector(".chain-protect");
      if (protect) protect.textContent = t("chain.group_protect", { intent: intent });
      const risk = g.querySelector(".chain-risk");
      if (risk) risk.classList.toggle("hidden", !hasRisk);
      _setChainOpen(g, !!_chainOpen[k]);   // 会话级展开态(默认收起;高风险 pin 区不受折叠影响)
    });
  }

  // ── 两张新卡的专属渲染(通用 h2a 卡列表里按 kind 分支)──
  // revise_skill:old/new steps 上下对照(简单 del/ins 行级对比)+ "依据:N 次运行信号" + trace 引用。
  // weekly_digest:payload.markdown 经 KarvyRender(DOMPurify 消毒)渲染,缺库回退 pre 裸文本。
  function _renderProposalKindDetail(card, payload) {
    const p = (payload && payload.payload) || {};
    if (payload.kind === "revise_skill") {
      const box = el("div", { class: "revise-card" });
      box.appendChild(el("div", { class: "revise-title",
        text: "🧬 " + t("revise.title", { name: p.skill_name || "" }) }));
      const oldLines = String(p.old_steps || "").split("\n").filter((s) => s.trim());
      const newLines = String(p.new_steps || "").split("\n").filter((s) => s.trim());
      const oldSet = new Set(oldLines), newSet = new Set(newLines);
      const oldBox = el("div", { class: "revise-block revise-old" },
        el("div", { class: "revise-label", text: t("revise.old_label") }));
      oldLines.forEach((ln) => oldBox.appendChild(
        el("div", { class: "revise-line" + (newSet.has(ln) ? "" : " revise-del"), text: ln })));
      const newBox = el("div", { class: "revise-block revise-new" },
        el("div", { class: "revise-label", text: t("revise.new_label") }));
      newLines.forEach((ln) => newBox.appendChild(
        el("div", { class: "revise-line" + (oldSet.has(ln) ? "" : " revise-ins"), text: ln })));
      box.appendChild(oldBox);
      box.appendChild(newBox);
      // 依据:N 次运行信号(trace_refs 逗号串)+ trace 引用可核
      const refs = String(p.trace_refs || "").split(",").map((s) => s.trim()).filter(Boolean);
      const basisRow = el("div", { class: "revise-basis", text: t("revise.basis", { n: refs.length }) });
      if (refs.length) basisRow.appendChild(el("span", { class: "revise-traces", text: " · 🔬 " + refs.join(", ") }));
      box.appendChild(basisRow);
      card.appendChild(box);
    } else if (payload.kind === "weekly_digest") {
      const box = el("div", { class: "digest-card" });
      box.appendChild(el("div", { class: "digest-title", text: "📅 " + t("digest.title") }));
      const bodyEl = el("div", { class: "digest-body" });
      const md = String(p.markdown || "");
      if (window.KarvyRender) KarvyRender.appendMarkdown(bodyEl, md);   // markdown-it + DOMPurify 消毒
      else bodyEl.appendChild(el("pre", { class: "digest-pre", text: md }));
      box.appendChild(bodyEl);
      box.appendChild(el("div", { class: "digest-hint", text: t("digest.accept_hint") }));
      card.appendChild(box);
    } else if (payload.kind === "inbox_decision" || payload.kind === "inbox_reply") {
      // 收件箱管道卡(inbox_pipe):需拍板 / 需回复。全文永不进卡 —— 只有发件人/主题/摘要。
      // reply 的 draft 走上面的「改了再批」textarea(_EDITABLE_FIELD),这里只画只读元信息。
      const isReply = payload.kind === "inbox_reply";
      const box = el("div", { class: "inbox-card" });
      box.appendChild(el("div", { class: "inbox-title",
        text: (isReply ? "✉️ " : "📧 ") + t(isReply ? "inbox.reply_title" : "inbox.decision_title") }));
      if (p.from) box.appendChild(el("div", { class: "inbox-meta" },
        el("span", { class: "inbox-label", text: t("inbox.from") }), el("span", { text: p.from })));
      if (p.subject) box.appendChild(el("div", { class: "inbox-meta" },
        el("span", { class: "inbox-label", text: t("inbox.subject") }), el("span", { text: p.subject })));
      if (p.snippet) box.appendChild(el("div", { class: "inbox-snippet", text: p.snippet }));
      if (p.suggested_action) box.appendChild(el("div", { class: "inbox-action" },
        el("span", { class: "inbox-label", text: t("inbox.suggested") }), el("span", { text: p.suggested_action })));
      if (isReply) {
        // 草稿默认摊开可读(改则走 textarea);ACCEPT=存台账+显示,系统绝不代发
        if (p.draft) box.appendChild(el("div", { class: "inbox-draft" },
          el("div", { class: "inbox-label", text: t("inbox.draft_label") }),
          el("pre", { class: "inbox-draft-body", text: String(p.draft) })));
        box.appendChild(el("div", { class: "inbox-hint", text: t("inbox.reply_hint") }));
      } else {
        box.appendChild(el("div", { class: "inbox-hint", text: t("inbox.decision_hint") }));
      }
      card.appendChild(box);
    } else if (payload.kind === "butler_plan") {
      // 文件管家第一课方案卡:moves 预览(封顶 12 条,余量如实计数,绝不静默漏)+
      // 查重/占位大户发现。数据全来自后端确定性扫描(payload.plan JSON,零 LLM)——
      // 卡上每一行都能在磁盘上核对;解析失败只降级(通用 summary/basis 仍在,不瞎画)。
      let plan = null;
      try { plan = JSON.parse(p.plan || "{}"); } catch (e) { plan = null; }
      if (plan && plan.moves) {
        const box = el("div", { class: "butler-plan" });
        box.appendChild(el("div", { class: "butler-plan-title", text: "📁 " + t("butler.plan_title") }));
        const moves = plan.moves || [];
        const CAP = 12;
        moves.slice(0, CAP).forEach((m) => {
          box.appendChild(el("div", { class: "butler-plan-move",
            text: (m.name || "?") + " → " + (m.bucket || "?") + "/" }));
        });
        if (moves.length > CAP) {
          box.appendChild(el("div", { class: "butler-plan-more",
            text: t("butler.plan_more", { n: moves.length - CAP }) }));
        }
        const dups = plan.duplicates || [];
        if (dups.length) {
          box.appendChild(el("div", { class: "butler-plan-sec", text: t("butler.plan_dups") }));
          dups.slice(0, 5).forEach((g) => box.appendChild(el("div", { class: "butler-plan-dup",
            text: (g.names || []).join(" = ") })));
        }
        const hogs = plan.hogs || [];
        if (hogs.length) {
          box.appendChild(el("div", { class: "butler-plan-sec", text: t("butler.plan_hogs") }));
          hogs.forEach((h) => box.appendChild(el("div", { class: "butler-plan-hog",
            text: (h.name || "?") + " (" + _butlerFmtBytes(h.size || 0) + ")" })));
        }
        box.appendChild(el("div", { class: "butler-plan-hint", text: t("butler.plan_hint") }));
        card.appendChild(box);
      }
    } else if (payload.kind === "memory_conflict") {
      // D2 记忆冲突卡:supersede 要推翻你钉住/人审的记忆 → 描述冲突(旧 vs 新原文),你三选一裁。
      // 你的选择存 card.dataset.mcResolution,ACCEPT 时随「改了再批」edits 带上 resolution 字段。
      const box = el("div", { class: "mconflict-card" });
      const oldRow = el("div", { class: "mconflict-row mconflict-old" },
        el("span", { class: "mconflict-label", text: t("memory.conflict.old_label") }),
        el("span", { class: "mconflict-text", text: String(p.old_content || "") }));
      const newRow = el("div", { class: "mconflict-row mconflict-new" },
        el("span", { class: "mconflict-label", text: t("memory.conflict.new_label") }),
        el("span", { class: "mconflict-text", text: String(p.new_content || "") }));
      box.appendChild(oldRow);
      box.appendChild(newRow);
      const chooseWrap = el("div", { class: "mconflict-choose" });
      chooseWrap.appendChild(el("span", { class: "mconflict-choose-label",
        text: t("proposal.memory_conflict.choose") }));
      card.dataset.mcResolution = "keep_both";   // 默认维持现状(两条都留)
      const opts = [["keep_old", "proposal.memory_conflict.keep_old"],
                    ["adopt_new", "proposal.memory_conflict.adopt_new"],
                    ["keep_both", "proposal.memory_conflict.keep_both"]];
      const optBtns = [];
      opts.forEach(function (pair) {
        const b = el("button", {
          class: "mconflict-opt" + (pair[0] === "keep_both" ? " active" : ""),
          text: t(pair[1]),
          onClick: function () {
            card.dataset.mcResolution = pair[0];
            optBtns.forEach(function (x) { x.classList.remove("active"); });
            b.classList.add("active");
          },
        });
        optBtns.push(b);
        chooseWrap.appendChild(b);
      });
      box.appendChild(chooseWrap);
      card.appendChild(box);
    }
  }
  function _butlerFmtBytes(n) {
    if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + "GB";
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + "MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + "KB";
    return n + "B";
  }

  // ── #6 待你拍板列按 kind 客户端筛选(积压多时能只看一类;Hardy 实拍见过"45/61 等你拍板"堆一长条)──
  // 只做显隐:_placeCard 多卡不覆盖 / 按 proposal_id diff 那套照旧不动,筛选绝不增删 h2a-list 的 DOM。
  // distinct kind < 2 → 整条筛选条 hidden(没得筛不占地方)。数据源=每张卡的 data-kind。只筛右栏 #h2a-list。
  let _decideFilter = null;   // null=全部;否则=当前选中的某个 kind 字符串
  function _kindLabel(kind) {
    const k = String(kind || "");
    if (!k) return t("proposal.no_desc");
    const lbl = t("proposal.kind." + k);
    if (lbl && lbl !== "proposal.kind." + k) return lbl;   // 有人话标签
    return k.replace(/_/g, " ");                            // humanize 兜底(别裸显键名 / 别崩)
  }
  function _applyDecideFilter() {
    const list = document.getElementById("h2a-list");
    if (!list) return;
    const cards = Array.from(list.querySelectorAll(".h2a-card[data-kind]"));
    // 当前选中的 kind 已被拍光(全处置了)→ 自动复位全部(否则空列没法回全)
    if (_decideFilter !== null && !cards.some((c) => c.dataset.kind === _decideFilter)) _decideFilter = null;
    cards.forEach((c) => {
      c.style.display = (_decideFilter === null || c.dataset.kind === _decideFilter) ? "" : "none";
    });
    // docs/92 刀1:组壳跟随成员显隐 —— 组内全被筛掉时壳(组头/保护句)也藏,别留空壳占地。
    // 只做显隐(不动 DOM/不解散组),与本筛选"绝不增删 h2a-list 的 DOM"同一纪律。
    Array.from(list.querySelectorAll(".h2a-chain-group")).forEach((g) => {
      const anyVisible = Array.from(g.querySelectorAll(".h2a-card[data-kind]"))
        .some((c) => c.style.display !== "none");
      g.style.display = anyVisible ? "" : "none";
    });
  }
  function _refreshDecideFilter() {
    const bar = document.getElementById("h2a-filter");
    const list = document.getElementById("h2a-list");
    if (!bar || !list) return;
    // 统计每个 kind 的卡数(Map 保插入序=第一次见到该 kind 的顺序)
    const counts = new Map();
    Array.from(list.querySelectorAll(".h2a-card[data-kind]")).forEach((c) => {
      const k = c.dataset.kind || "";
      if (!k) return;
      counts.set(k, (counts.get(k) || 0) + 1);
    });
    // distinct kind < 2 → 没得筛,整条藏起并复位显隐(从"曾筛过"状态回全)
    if (counts.size < 2) {
      _decideFilter = null;
      bar.hidden = true;
      bar.textContent = "";
      _applyDecideFilter();
      return;
    }
    if (_decideFilter !== null && !counts.has(_decideFilter)) _decideFilter = null;   // 选中类已无卡 → 复位
    let total = 0;
    counts.forEach((n) => { total += n; });
    bar.hidden = false;
    bar.textContent = "";
    bar.appendChild(el("span", { class: "h2a-filter-label", text: t("proposal.filter_label") }));
    // 「全部 N」chip
    bar.appendChild(el("span", {
      class: "h2a-filter-chip" + (_decideFilter === null ? " active" : ""),
      text: t("proposal.filter_all") + " " + total,
      onClick: () => { _decideFilter = null; _applyDecideFilter(); _refreshDecideFilter(); },
    }));
    // 每个 kind 一个「标签 · 数量」chip(再点/点全部 → 复位)
    counts.forEach((n, k) => {
      bar.appendChild(el("span", {
        class: "h2a-filter-chip" + (_decideFilter === k ? " active" : ""),
        text: _kindLabel(k) + " · " + n,
        onClick: () => { _decideFilter = (_decideFilter === k) ? null : k; _applyDecideFilter(); _refreshDecideFilter(); },
      }));
    });
    _applyDecideFilter();
  }

  function renderProposal(payload, opts) {
    const list = document.getElementById("h2a-list");
    if (!list) return;
    if (!payload) {
      // 沉默 / 未接 analyst:保持空态,不刷屏
      return;
    }
    _stripEmpty(list, "h2a-empty");   // 清空态占位,但**保留已挂的兄弟卡**(多卡不覆盖)
    // 首张决策卡一次性提示(docs/85 Part A ③):教「决策点」+ 引流 🗳 回放(教学功能互相引流)。
    // localStorage 记「见过」→ 只出这一次;✕ 手动收起。
    try {
      if (!localStorage.getItem("karvyloop_dcard_hint") && !document.getElementById("dcard-first-hint")) {
        localStorage.setItem("karvyloop_dcard_hint", "1");
        const hint = el("div", { class: "dcard-first-hint", id: "dcard-first-hint" },
          el("span", { text: t("dcard.first_hint") }),
          el("button", { class: "dcard-first-hint-x", text: "✕", "aria-label": "dismiss",
            onClick: () => hint.remove() }));
        list.appendChild(hint);
      }
    } catch (e) { /* 无 localStorage(隐私模式)→ 不提示,不炸 */ }
    const built = _buildProposalCard(payload);
    _placeCard(list, built.proposalId, built.card);   // 多卡不覆盖:同 id 替换、新 id 追加
    _regroupChains(list);                             // docs/92 刀1:同链 ≥2 张 → 收进组壳(纯视觉)
    _renderProposalInChat(payload, built.proposalId); // S3:决策卡双面出 —— 同时冒进聊天流
    // 桌面视图(docs/51 §4.2):⚖便签置顶 + 闪烁 + 卡皮巴拉冒泡(fail-loud,推回决策舱);
    // replay(开机回放存量卡)只保"在位可瞟",不演叼卡剧场 —— 事件 vs 快照在 desktop.ts 一处区分
    if (window.KarvyDesktop) window.KarvyDesktop.notifyH2A({ replay: !!(opts && opts.replay) });
    updatePulse();   // step5:拍板数变了 → 刷脉搏
    _refreshDecideFilter();   // #6:新卡可能引入第 2 个 kind → 筛选条现身 / 让新卡守当前筛选
  }

  // 一张 H2A 决策卡的 DOM(docs/46 S3 抽出:右栏列表和聊天流 inline 共用同一套渲染 +
  // 同一个拍板 API;两处各建实例、各自 judgeState,拍任意一张 = 同一条 h2a_decision)。
  function _buildProposalCard(payload) {
    const card = el("div", { class: "h2a-card" });
    card.setAttribute("data-kind", String(payload.kind || ""));   // #6:按 kind 客户端筛选的数据源
    // docs/92 刀1 同链合并:链键 = chain_id(派生卡带)|| 自己的 proposal_id(链根卡不回填)。
    // 右栏 _regroupChains 按它把同链 ≥2 张收成一组;单链 1 张 = 无组壳,和现在一模一样。
    card.setAttribute("data-chain-key",
      String(payload.chain_id || payload.proposal_id || ("p-" + (payload.habit_id || 0))));
    if (payload.chain_intent) card.setAttribute("data-chain-intent", String(payload.chain_intent));
    if (payload.high_risk) card.setAttribute("data-high-risk", "1");   // 后端 silence.HIGH_RISK_KINDS 判定源
    card.appendChild(el("div", { class: "h2a-summary", text: "💡 " + (payload.summary || t("proposal.no_desc")) }));
    // ── 卡片折叠(docs/90 刀1b,Hardy 卡片模型):默认只留「摘要 + 拍板」,详情/依据收进「详情 ▾」。
    //    看懂摘要直接拍;要深究才点开。安全项(违背/无脑拍)不靠"始终可见"守 —— 靠 decide() 的
    //    强制阅读门(Hardy「像 App 安全协议一样,强制阅读后才能拍」):折叠不损失,安全也不损失。──
    const chipSlot = el("div", { class: "h2a-chip-slot" });   // 🧭 楔子的脸(可见 chip)由 decision_card 回填
    card.appendChild(chipSlot);
    const fold = el("div", { class: "dcard-fold hidden" });
    const foldToggle = el("button", { class: "dcard-fold-toggle", type: "button",
      text: t("dcard.details") + " ▾", "aria-expanded": "false" });
    const _setFold = (open) => {
      fold.classList.toggle("hidden", !open);
      foldToggle.setAttribute("aria-expanded", open ? "true" : "false");
      // ⚠ 注意标(有违背/无脑拍时,decision_card 回填加的 has-attention)在收起态保留
      const mark = foldToggle.classList.contains("has-attention") ? t("dcard.attention") + " · " : "";
      foldToggle.textContent = mark + t("dcard.details") + (open ? " ▴" : " ▾");
    };
    foldToggle.addEventListener("click", () => _setFold(fold.classList.contains("hidden")));
    const _openFold = () => { if (fold.classList.contains("hidden")) _setFold(true); };
    card.appendChild(foldToggle);
    card.appendChild(fold);

    // ── 以下全部收进折叠(详情/依据),默认不铺在正文 ──
    // ch4 #6.1:决策依据(为什么)—— 折进"详情",不追不展开、不影响拍板(Hardy 卡片模型:依据=关联)
    if (payload.basis) {
      fold.appendChild(el("div", { class: "h2a-basis" },
        el("span", { class: "h2a-basis-label", text: t("proposal.basis_label") }),
        el("span", { text: payload.basis })));
    }
    // 两张新卡的专属渲染:revise_skill(old/new steps 对照)/ weekly_digest(markdown 周报)
    _renderProposalKindDetail(fold, payload);
    // ch4:上下文跳转 —— 跳进那条任务/对话看全貌再拍
    const ctxRef = payload.context_ref || {};
    if (ctxRef.kind === "task" && ctxRef.id) {
      fold.appendChild(el("button", { class: "h2a-jump", text: t("proposal.jump"),
        onClick: () => openTaskById(ctxRef.id) }));
    }
    if (typeof payload.strength === "number") {
      fold.appendChild(el("div", {
        class: "h2a-strength",
        text: t("proposal.strength", { pct: Math.round(payload.strength * 100) }),
        title: t("proposal.strength.title"),   // 生词审计(docs/85 ⑥):strength 加一句人话解释
      }));
    }
    // #42 打计费黑箱:"花钱之前告诉你" —— 执行类提案带最近同类任务的真实消耗分布(折进详情)。
    // 诚实:样本<3 不显示;数字来自 per-task 归因账本,不是猜的。
    const _COSTLY_KINDS = ["route_to_role", "run_task", "roundtable"];
    if (_COSTLY_KINDS.indexOf(payload.kind) >= 0) {
      const costLine = el("div", { class: "h2a-cost" });
      fold.appendChild(costLine);
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
    const judgeState = { engaged: false, edited: [], basis: "", violations: [], loaded: false };
    // ready:decision_card(核对你标准)回来才 resolve。decide() ACCEPT 前必等它 —— 折叠后违背绝不因
    // "还没加载完"而漏过强制阅读门(比原来只靠可见 banner 更严)。加载失败/无卡也 resolve(降级不挡拍)。
    const uiRefs = { chipSlot: chipSlot, foldToggle: foldToggle, openFold: _openFold };
    judgeState.ready = new Promise((res) => { judgeState._resolveReady = res; });
    // 懒加载(2026-07-13 Hardy 报 LAN 开屏卡):/api/decision_card 建卡含召回 +（有预对齐时）违背 LLM,
    // 单卡 ~1.5s。积压 N 张若开屏全拉 → N 并发把 worker 池 + 限流堵成秒级齐返(实测 39 张→10s)。
    // 改为**卡真滚进视口才建 detail**:收在 dock 里没打开的卡一次都不拉;跑评分离(违背 LLM 只对你在看的
    // 那张跑,不对没人看的 38 张跑)。judgeState 随 detail 回填 —— 要操作必先看见=先触发,拍前拦不丢。
    if (typeof IntersectionObserver === "function") {
      let _dcDone = false;
      const _io = new IntersectionObserver(function (ents) {
        if (_dcDone) return;
        for (var i = 0; i < ents.length; i++) {
          if (ents[i].isIntersecting) {
            _dcDone = true; _io.disconnect();
            _renderDecisionCard(fold, proposalId, judgeState, uiRefs);
            return;
          }
        }
      }, { rootMargin: "300px" });
      _io.observe(card);
    } else {
      _renderDecisionCard(fold, proposalId, judgeState, uiRefs);   // 老浏览器无 IO → 退回即时(不劣化)
    }

    // #42 优化①「改了再批」:kind→可编辑的"行动文本"字段。你不只认/拒,还能亲手改到该有的样子
    // 再批 —— 修改本身是楔子最富的偏好信号(原文→改文的对照会进偏好结晶)。
    const _EDITABLE_FIELD = { route_to_role: "requirement", merge_knowledge: "merged_content",
                              merge_atoms: "merged_purpose", run_task: "intent",
                              revise_skill: "new_steps",     // 技能修订卡:改了再批 new_steps
                              inbox_reply: "draft" };        // 收件箱回复卡:改了再批 draft(代拟草稿)
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
    // 拍板提交(shake→回喂→WS→终态)。从闸门里抽出:ACCEPT 过完强制阅读门再调;DEFER/REJECT 直调。
    const _commitDecision = (decision, _edits) => {
      // 微动效 P1-3 品位 shake:REJECT 拍下那刻本卡轻微横移抖一次(reduced-motion 时 CSS 关掉)
      if (decision === "REJECT") {
        card.classList.remove("kv-reject-shake");
        void card.offsetWidth;   // 重启动画(同卡连点不哑)
        card.classList.add("kv-reject-shake");
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
        _finalizeInlineCards(proposalId, decision);   // S3:任一侧拍板,聊天流里的同卡即刻转终态
      });
    };
    const decide = (decision) => {
      // 「改了再批」:改动过 → 随 ACCEPT 带 edits。改过=亲手判断过(最强 engaged 信号),闸前标记。
      let _edits = null;
      if (decision === "ACCEPT" && editArea && editArea.value.trim() &&
          editArea.value.trim() !== _editSrc.trim()) {
        _edits = {}; _edits[_editField] = editArea.value.trim();
        judgeState.engaged = true;
      }
      // D2 记忆冲突卡:你选的裁决随 ACCEPT 带上 edits.resolution;选/看过 = 真判断,标 engaged。
      if (decision === "ACCEPT" && payload.kind === "memory_conflict") {
        const _res = card.dataset.mcResolution || "keep_both";
        _edits = _edits || {};
        _edits.resolution = _res;
        judgeState.engaged = true;
      }
      // DEFER/REJECT 不过闸,直接提交(拒绝一个违背/无脑拍是安全的,不必强制阅读)。
      if (decision !== "ACCEPT") { _commitDecision(decision, _edits); return; }
      // ACCEPT:先等"核对你标准"的懒加载回来(≤6s 兜底)—— 卡片折叠后,违背绝不因"还没加载完"而漏过下面的门。
      _readyWithin(judgeState, 6000).then(async (checked) => {
        // 超时降级也 fail-loud(对抗验收 B3 补):核对没回来 ≠ 静默放行 —— 明着问,你确认"不等了"才继续。
        if (!checked && !window.confirm(t("dcard.gate_timeout"))) return;
        // 强制阅读门①(Hardy「像 App 安全协议:强制阅读后才能拍」):踩了你定的标准 → 展开折叠露出红 banner
        // + 必须确认才继续(取消=不拍)。总是弹(不受 engaged 影响):违背太重,每次批都要你亲眼确认过。
        const vios = judgeState.violations || [];
        if (vios.length) {
          _openFold();
          await _nextPaint();   // 让红 banner 先画出来,再弹阻塞式 confirm —— 真"强制阅读",不是盲弹
          const std = vios.map((v) => "『" + (v.standard || "") + "』" + (v.why ? " — " + v.why : "")).join("\n  ");
          if (!window.confirm(t("dcard.violation_gate", { standard: std }))) return;
        }
        // 逼判断闸②(高价值/无脑拍,仅"没真判断过"时拦):与原逻辑一致(改/删依据 或 陈述判断依据都算判断过)。
        if (!_engagedNow(judgeState)) {
          if (judgeState.highValue &&
              !window.confirm(t("dcard.hv_confirm", { standard: judgeState.hvStandard || "" }))) return;
          if (judgeState.needsRecheck && !window.confirm(t("dcard.surrender_confirm"))) return;
        }
        _commitDecision("ACCEPT", _edits);
      });
    };
    btnRow.appendChild(el("button", { class: "h2a-accept", onClick: () => decide("ACCEPT"), text: t("proposal.accept") }));
    btnRow.appendChild(el("button", { class: "h2a-defer", onClick: () => decide("DEFER"), text: t("proposal.defer") }));
    btnRow.appendChild(el("button", { class: "h2a-reject", onClick: () => decide("REJECT"), text: t("proposal.reject") }));
    card.appendChild(btnRow);
    card.appendChild(reasonInput);   // 可选拒绝理由(不填也能拒)
    return { card: card, proposalId: proposalId };
  }

  // ============ S3 决策卡双面出(docs/46 §4.1,业界 HITL 范式)============
  // 新 H2A 提案除右栏列表外,同时以"小卡递来一张待签单"的卡片消息冒进当前 chat-log;
  // 两处操作同一条数据:任一侧拍板 → 聊天流里的卡转终态、右栏卡经 h2a_envelope 撤下。
  function _renderProposalInChat(payload, proposalId) {
    const log = document.getElementById("chat-log");
    if (!log) return;
    // 同 id 未拍的旧 inline 卡先撤(幂等重推不叠);已拍的终态卡保留(历史可回看)
    Array.from(log.querySelectorAll("[data-proposal-id]")).forEach((n) => {
      if (n.getAttribute("data-proposal-id") === String(proposalId) &&
          !n.getAttribute("data-decided")) n.remove();
    });
    const follow = isNearBottom(log);
    const line = el("div", { class: "chat-line agent chat-h2a" },
      el("span", { class: "role", text: t("chat.karvy") }));
    line.setAttribute("data-proposal-id", String(proposalId));
    const wrap = el("div", { class: "chat-h2a-card" });
    wrap.appendChild(el("div", { class: "chat-h2a-head", text: t("h2a.inline_head") }));
    wrap.appendChild(_buildProposalCard(payload).card);   // 复用同一套卡渲染 + 拍板路径
    line.appendChild(wrap);
    log.appendChild(line);
    if (follow) log.scrollTop = log.scrollHeight;
  }

  // 聊天流里的 inline 卡转终态:撤操作面(按钮/理由/改了再批/判断依据),盖终态戳。
  // decision 已知(本端拍的)显 ✅/✖/🕒;不知道(别端拍的,envelope 兜底)显 ✔ 已处置。
  const _DECISION_DONE_KEY = { ACCEPT: "h2a.done_accept", REJECT: "h2a.done_reject", DEFER: "h2a.done_defer" };
  function _finalizeInlineCards(proposalId, decision) {
    const log = document.getElementById("chat-log");
    if (!log || !proposalId) return;
    Array.from(log.querySelectorAll("[data-proposal-id]")).forEach((line) => {
      if (line.getAttribute("data-proposal-id") !== String(proposalId)) return;
      if (line.getAttribute("data-decided")) return;   // 已终态(幂等,双路径都会调这里)
      line.setAttribute("data-decided", decision || "handled");
      line.querySelectorAll(".h2a-buttons, .h2a-reason, .h2a-edit-wrap, .dcard-basis, .dcard-crit-btns")
        .forEach((n) => n.remove());
      const wrap = line.querySelector(".chat-h2a-card") || line;
      // 微动效 P0-2:ACCEPT 的终态戳带手签对勾(SVG 笔画 360ms 画出来 —— 签字落笔的瞬间;
      // 自产 SVG 非模型文本;emoji 当 UI 图标退位)。拒绝/稍后只升入不庆祝。
      const done = el("div", { class: "chat-h2a-done" });
      if (decision === "ACCEPT") {
        done.innerHTML = '<svg class="kv-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
          '<path d="M2.5 8.5 L6.5 12 L13.5 4" fill="none" stroke="var(--success)" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round"/></svg> ';
        done.appendChild(document.createTextNode(t(_DECISION_DONE_KEY[decision])));
      } else {
        done.textContent = (_DECISION_BADGE[decision] || "✔") + " " + t(_DECISION_DONE_KEY[decision] || "h2a.done_generic");
      }
      // 决策时间线副入口(docs/85):聊天流终态卡挂「🧬 回放」—— 拍完就能看这板怎么建成的
      done.appendChild(document.createTextNode(" "));
      done.appendChild(el("button", { class: "dlife-link", text: "🧬 " + t("dlife.replay_link"),
        title: t("dlife.entry_title"),
        onClick: () => openDecisionLifeline(String(proposalId), "") }));
      wrap.appendChild(done);
    });
  }

  // ch4 预判:主动建议按 kind 分流。**显式映射 + fail-safe 默认进【拍板】**:真决策(需你判断
  // + 可拒 + 带依据)进决策列;只有**习惯预判**(proactive 用 KIND_RUN_TASK,小卡从习惯猜你想做)
  // 进【你可能想做】。旧实现用"决策 kind 白名单",任何新 kind(merge_knowledge / merge_atoms /
  // confirm_result / crystallize_skill / confirm_decision_pref / infeasible_report)
  // 都被误丢进预判列 —— 无拒绝按钮、丢 payload。改成"预判白名单",新 kind 一律进决策列。
  // docs/90 刀3c:schedule_suggest(时机能力提示)也进预判象限 —— 温和的"要不要每周自动跑",
  // 不是要拍板的决策卡。ACCEPT 特判成"预填聊天补节奏"(不直接建定时任务),见 renderPredict。
  const _PREDICT_KINDS = ["run_task", "schedule_suggest"];
  // opts.replay = 状态回放(boot fetch 存量卡),非新卡事件 —— 透传给 renderProposal,
  // 桌面吉祥物剧场(叼卡/闪⚖/冒泡)只回应真事件(WS h2a_proposal / 手动求建议)。
  function _routeProposal(payload, opts) {
    if (!payload) return;   // null = 沉默/未接,保持空态
    if (_PREDICT_KINDS.indexOf(payload.kind) >= 0) renderPredict(payload);
    else renderProposal(payload, opts);   // 其余全部(含未来新 kind)→ 决策列
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
        text: t("proposal.strength", { pct: Math.round(payload.strength * 100) }),
        title: t("proposal.strength.title") }));   // 生词审计(docs/85 ⑥)
    }
    const pid = payload.proposal_id || ("p-" + (payload.habit_id || 0));
    const row = el("div", { class: "predict-buttons" });
    // docs/90 刀3c:schedule_suggest 特判 —— ACCEPT ≠ 直接建定时任务(cron 要用户定"多久一次/
    // 几点"),而是把这条 intent 预填进与小卡的聊天让用户补节奏再走 create_schedule;忽略 = REJECT
    // (彻底收起,不再挂待决;already_suggested 后端已置,永不再提)。
    const isSchedSuggest = String(payload.kind || "") === "schedule_suggest";
    if (isSchedSuggest) {
      const sIntent = String((payload.payload || {}).intent || "");
      row.appendChild(el("button", { class: "predict-yes", text: t("predict.do"),
        onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "ACCEPT", reason: "" });
                         _prefillScheduleSuggest(sIntent);
                         _clearPredict(); } }));
      row.appendChild(el("button", { class: "predict-no", text: t("predict.ignore"),
        onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "REJECT", reason: "" });
                         _clearPredict(); } }));
    } else {
      row.appendChild(el("button", { class: "predict-yes", text: t("predict.do"),
        onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "ACCEPT", reason: "" });
                         _clearPredict(); } }));
      row.appendChild(el("button", { class: "predict-no", text: t("predict.ignore"),
        onClick: () => { sendWS("h2a_decision", { proposal_id: pid, decision: "DEFER", reason: "" });
                         _clearPredict(); } }));
    }
    card.appendChild(row);
    _placeCard(list, pid, card);   // 多卡不覆盖:同 id 替换、新 id 追加
    updatePulse();
  }
  // docs/90 刀3c:接受"每周自动跑"建议 = 切到小卡私聊 + 把这条事预填进输入框(带上"补节奏"提示),
  // 用户改成"每周一早八"这类再发 → 走既有 create_schedule(NL→cron)。**绝不替用户假设 cron**。
  function _prefillScheduleSuggest(intent) {
    if (!intent) return;
    try { _talkToKarvy(); } catch (e) { /* 切场失败也别炸,尽力预填 */ }
    // _talkToKarvy 内部 switchPeer 会 _ceClear(输入框),稍等它落定再写预填文本 + 光标到末尾。
    setTimeout(() => {
      const ce = _ceInput();
      if (!ce) return;
      ce.textContent = t("schedule_suggest.prefill", { intent: intent });
      _ceUpdateEmpty();
      ce.focus();
      try {
        const sel = window.getSelection();
        const rng = document.createRange();
        rng.selectNodeContents(ce); rng.collapse(false);
        sel.removeAllRanges(); sel.addRange(rng);
      } catch (e) { /* 光标定位失败无所谓,文本已在 */ }
    }, 220);
  }
  function _clearPredict() {
    const list = document.getElementById("predict-list");
    if (!list) return;
    list.innerHTML = "";
    const emp = el("div", { class: "empty-state", text: t("empty.predict") });
    emp.appendChild(_emptyAction("empty.predict_act", requestProposal));   // S4:空态给下一步动作
    list.appendChild(emp);
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
  // 返回 true/false(Q2 出处回链的调用方要知道"定位到没有"以便友好提示;老调用方忽略返回值,零回归)。
  async function openConvById(convId, targetTaskId) {
    openChatModal();
    try {
      const r = await fetch("/api/line/open_by_conv", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: convId }) });
      if (!r.ok) return false;
      const data = await r.json();
      if (!data.ok) return false;
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
      return true;
    } catch (e) { console.warn("[openConvById] failed", e); return false; }
  }

  // Q2 记忆出处回链:知识库面板"对话沉淀"来源可点 → 面板发 karvy:open-conversation 事件,
  // 这里统一收口跳转(复用 openConvById 按 id 定位真 peer,跨面板零耦合)。
  // 定位不到(会话已删 / 老数据 id 失效)→ 聊天流里友好提示,不崩不骗。
  window.addEventListener("karvy:open-conversation", async (e) => {
    const convId = e && e.detail && e.detail.conversation_id;
    if (!convId) return;
    closeMgmtModal();
    const ok = await openConvById(String(convId));
    if (!ok) pushChatLine("system", t("conv.locate_failed"));
  });

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
      // (docs/66 §F Hardy 三次收敛:认知聊天整个住在知识库模块里 —— 聊天列表不加知识分类)
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
    // l0 直聊某角色(role==agent + agent_id)= 是那个角色,不是小卡(判据同后端 is_direct_role_peer)。
    const isDirectRole = peer && peer.domain_id === "l0" && peer.role === "agent" && !!peer.agent_id;
    const isKarvy = (!peer || peer.is_private || peer.domain_id === "l0") && !isDirectRole;
    const who = isKarvy ? t("chat.karvy")
      : (isDirectRole ? (_peerLabel() || peer.agent_id) : (_peerLabel() || peer.role || t("chat.karvy")));
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
        // docs/66 §E:沉淀关闭的标 ✓(历史可翻但不算欠账)
        const closed = c.closed_at ? t("conv.closed_suffix") : "";
        opt.textContent = `${label} · ${t("conv.turns", { n: c.turn_count })}${closed}${c.id === data.current_id ? t("conv.current") : ""}`;
        sel.appendChild(opt);
      }
    } catch (e) {
      console.warn("[conv] list failed", e);
    }
  }

  // ============ docs/66 §F(Hardy 三次收敛):认知聊天住在知识库模块里 ============
  // 全局聊天唯一联动 = 意图提示条:你说"聊点新知识/认知…"→ 问一句"要打开知识库·聊知识吗?"
  // 你点开启才打开知识库面板(H2A:问,不自动);馆员/收敛/沉淀/欠账全在面板里,主聊天零耦合。
  let _kHintShown = false;
  function _maybeKnowledgeHint(text) {
    if (_kHintShown) return false;
    if (!/(聊|学|讲)[点些一]{0,2}(个)?(新)?(知识|认知)|新知识|开启知识(库)?(收集)?模式/.test(text || "")) return false;
    _kHintShown = true;   // 一次会话只提一次,不追着问
    const log = document.getElementById("chat-log");
    if (!log) return false;
    const box = el("div", { class: "chat-notice knowledge-hint" });
    box.appendChild(el("span", { text: t("knowledge.hint") + " " }));
    const yes = el("button", { type: "button", class: "khint-btn khint-yes", text: t("knowledge.hint_open") });
    yes.addEventListener("click", () => {
      box.remove();
      _ceClear();   // 这句话跟着去知识库,主聊天不再发它
      _openLazyPanel("memory", () => window.KarvyMemoryPanel.open());   // T4:面板脚本按需注入
      // 面板异步渲染 → 轮几拍把原话预填进「聊知识」输入框(带话入场,按发送即开聊)
      let tries = 0;
      const carry = () => {
        const cin = document.querySelector(".kchat-in");
        if (cin) { cin.value = text; cin.focus(); return; }
        if (++tries < 20) setTimeout(carry, 150);
      };
      carry();
    });
    const no = el("button", { type: "button", class: "khint-btn", text: t("knowledge.hint_skip") });
    no.addEventListener("click", () => { box.remove(); _submitChat(); });   // 原话走正常聊天(闸已标,不再拦)
    box.appendChild(yes); box.appendChild(no);
    log.appendChild(box);
    log.scrollTop = log.scrollHeight;
    return true;   // 闸门:调用方停住,等你选
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

  // 主动问小卡"现在有啥建议"(WS propose;失败回退 POST /api/propose)。
  // Hardy 实拍("点下去能干什么?"):点了必有下文 —— 按钮转入"小卡在看…",
  // 有建议 → 出卡;没建议 → 按钮原地如实说"暂时没有"(服务端 null 回执只发点的人)。
  let _proposeWaiting = false;
  let _proposeTimer = 0;
  function _proposeBusy(on) {
    _proposeWaiting = on;
    if (_proposeTimer) { clearTimeout(_proposeTimer); _proposeTimer = 0; }
    const pb = document.getElementById("propose-btn");
    if (pb) {
      if (on) { if (!pb.dataset.label) pb.dataset.label = pb.textContent; pb.textContent = t("propose.busy"); }
      else if (pb.dataset.label) { pb.textContent = pb.dataset.label; delete pb.dataset.label; }
    }
    [pb, document.getElementById("predict-refresh-btn")].forEach((b) => {
      if (b) { b.disabled = on; b.classList.toggle("is-waiting", on); }
    });
  }
  // 求建议的回执落地:gotCard=true 出了卡(静默恢复);false=小卡看过但没有 → 按钮如实说
  function _proposeSettled(gotCard) {
    const waited = _proposeWaiting;
    _proposeBusy(false);
    if (gotCard || !waited) return;
    const pb = document.getElementById("propose-btn");
    if (!pb || pb.dataset.none) return;
    pb.dataset.none = "1";
    const orig = pb.textContent;
    pb.textContent = t("propose.none");
    setTimeout(() => { pb.textContent = orig; delete pb.dataset.none; }, 2800);
  }
  async function requestProposal() {
    if (_proposeWaiting) return;                 // 已在看,别叠加
    _proposeBusy(true);
    // 兜底:回执丢了(断线等)也 30s 自愈,不留残废按钮
    _proposeTimer = setTimeout(() => { _proposeTimer = 0; _proposeBusy(false); }, 30000);
    const sent = sendWS("propose", {});
    if (!sent) {
      try {
        const r = await fetch("/api/propose", { method: "POST" });
        if (r.ok) {
          const body = await r.json();
          _routeProposal(body.proposal);   // ch4 预判:按 kind 分流到拍板/预判
          _proposeSettled(!!body.proposal);
        } else {
          _proposeSettled(false);
        }
      } catch (e) {
        console.warn("[propose] failed", e);
        _proposeSettled(false);
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
  // 注入 directChatRole(Hardy:角色卡「💬 直聊」→ 切到与该角色的私聊,不必先加进业务域)。
  function openRolesPanel() { return window.KarvyRolesPanel.open({ directChatRole }); }

  // Hardy:直聊某角色 = 切到 l0/personal scope 的「你 & 该角色」私聊线(不挂任何业务域治理)。
  // peer=(l0, agent, <roleId>):后端 is_direct_role_peer 认它 → 用该角色人格,不路由给小卡。
  function directChatRole(roleId) {
    if (!roleId) return;
    closeMgmtModal();                     // 从角色面板切走 → 关面板进聊天
    _currentPeerLabel = "🏢 " + roleId;   // 标题/回复方身份用它(l0 场也显角色名,不落成"小卡")
    switchPeer(JSON.stringify({ domain_id: "l0", role: "agent", agent_id: roleId, is_group: false }));
  }
  // nav 派发/别处调 open() 无参时的兜底通道(全局钩子;roles_panel 缺注入时回退到它)。
  window.KarvyChat = Object.assign(window.KarvyChat || {}, { directChatRole });

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

  // 🔌 外部 runtime 管理面(跨 runtime 协作:列/删/在线灯/直聊 外部公民 + 按需接入引导)。
  // 直聊外部公民 = 切到 peer=(域, "external", citizen_id)(后端 EXTERNAL_ROLE,不与原生角色混脸)。
  function openExternalPanel() {
    return window.KarvyExternalPanel.open({
      refreshPeers,
      directChatPeer: (peer, label) => {
        _currentPeerLabel = label || ("🔌 " + (peer.agent_id || "external"));
        switchPeer(JSON.stringify({ domain_id: peer.domain_id || "", role: peer.role || "external",
          agent_id: peer.agent_id || "", is_group: false }));
      },
    });
  }

  function setupMgmtPanels() {
    const close = document.getElementById("mgmt-close");
    if (close) close.addEventListener("click", closeMgmtModal);
    const overlay = document.getElementById("mgmt-modal");
    // CFG-01①(内测):模型设置窗禁"点空白关闭"(防切页误关,✕/Esc 仍可关);
    // 开窗方经 openMgmtModal(title, {backdropClose:false}) 声明,其余面板维持原交互。
    if (overlay) overlay.addEventListener("click", (e) => {
      if (e.target !== overlay) return;
      if (_KModal.backdropCloseEnabled && !_KModal.backdropCloseEnabled()) return;
      closeMgmtModal();
    });
    // T4 懒加载:openers 全是**惰性箭头**(点开时才解引用 window.Karvy* —— 全局函数是
    // 面板脚本载入后才有的,提前取必 undefined);派发统一走 _openLazyPanel(先 ensure 再调)。
    const _panelOpeners = {
      atoms: () => window.KarvyAtomsPanel.open(),
      roles: () => openRolesPanel(),
      domains: () => openDomainsPanel(),
      agents: () => window.KarvyAgentsPanel.open({ refreshPeers }),
      external: () => openExternalPanel(),
      devices: () => window.KarvyDevicesPanel.open(),
      memory: () => window.KarvyMemoryPanel.open(),
      decision_prefs: () => window.KarvyDecisionPrefs.open(),
      skills: () => window.KarvySkillsPanel.open(),
      models: () => window.KarvyModelsPanel.open(),
      diagnose: () => openDiagnosePanel(),
      files: () => window.KarvyFilesPanel.open(),
      schedules: () => window.KarvySchedulesPanel.open(),
      pursuits: () => window.KarvyPursuitsPanel.open(),
    };
    let _lastPanel = "";
    document.querySelectorAll(".nav-item[data-panel]").forEach((btn) => {
      if (btn.disabled) return;
      btn.addEventListener("click", () => {
        const p = btn.getAttribute("data-panel");
        // 微动效 P0-7:换面板才做 150ms 定向入场(同面板重开/内部重渲不动;不用 VT——
        // 全页快照会把背后正在流式的聊天冻一帧,class 法零副作用)
        const _mb = document.getElementById("mgmt-body");
        if (_mb && _lastPanel !== p) {
          _lastPanel = p;
          _mb.classList.remove("panel-swap"); void _mb.offsetWidth; _mb.classList.add("panel-swap");
        }
        if (_panelOpeners[p]) _openLazyPanel(p, _panelOpeners[p]);
      });
    });
  }

  // docs/90 刀2:左导航三组可折叠。组标题=折叠开关(role=button + aria-expanded,键盘可达);
  // 默认态:你的团队+它学到的你 展开、引擎室 收起(docs/90 §C:引擎室整组降 S3 折叠);
  // 用户折/展记 localStorage(karvyloop_navfold_<group>),重开保持。纯 display 切换零动画。
  // 收起组的 nav-item 仍在 DOM(display:none 不移除)→ desk dock 克隆(querySelectorAll)不受影响。
  const _NAVFOLD_DEFAULT = { team: false, learned: false, engine: true };   // true = 收起
  function setupNavFold() {
    document.querySelectorAll(".sidebar .nav-group-title[data-fold]").forEach((title) => {
      const group = title.closest(".nav-group");
      const name = title.getAttribute("data-fold");
      if (!group || !name) return;
      const key = "karvyloop_navfold_" + name;
      const apply = (folded) => {
        group.classList.toggle("is-folded", folded);
        title.setAttribute("aria-expanded", folded ? "false" : "true");
        const arrow = title.querySelector(".nav-fold-arrow");
        if (arrow) arrow.textContent = folded ? "▸" : "▾";
      };
      let saved = null;
      try { saved = localStorage.getItem(key); } catch (e) { /* storage 不可用 → 走默认 */ }
      apply(saved === null ? !!_NAVFOLD_DEFAULT[name] : saved === "1");
      const toggle = () => {
        const folded = !group.classList.contains("is-folded");
        apply(folded);
        try { localStorage.setItem(key, folded ? "1" : "0"); } catch (e) { /* 记不住也照常折 */ }
      };
      title.addEventListener("click", toggle);
      title.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
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

  // 微动效 P1-4:统计 chip 数值**增长**才轻 bump 一次(首次填充/持平/回落不动;
  // prev 记在 data-v 上,2s 轮询同值不触发;reduced-motion 由 CSS 总闸关)
  function _setStatChip(id, num, display, label, title) {
    const n = document.getElementById(id);
    if (!n) return;
    const raw = n.getAttribute("data-v");
    const prev = raw === null ? NaN : Number(raw);   // 首次 = NaN → 任何比较都 false(Number(null) 是 0,别踩)
    // docs/85 Part A ⑤:人话在前、数在后(「🏃 跑活 12」),hover 有一句解释
    n.innerHTML = `${label} <b>${display}</b>`;
    if (title) n.title = title;
    n.setAttribute("data-v", String(num));
    if (Number(num) > prev) {
      n.classList.remove("stat-bump");
      void n.offsetWidth;   // 重启动画
      n.classList.add("stat-bump");
    }
  }
  function renderStats(s) {
    // 顶栏仪表盘:人话化(docs/85 Part A ⑤)—— 🏃跑活 N · ⚡直觉 N% · 💎结晶 N + tooltip
    const pct = (s.fast_brain_hit_rate * 100).toFixed(0);
    _setStatChip("stat-drives", s.drive_calls, s.drive_calls, t("stat.drives"), t("stat.drives.title"));
    _setStatChip("stat-fast-brain", pct, pct + "%", t("stat.fast_brain"), t("stat.fast_brain.title"));
    _setStatChip("stat-crystallized", s.crystallizations, s.crystallizations, t("stat.skills"), t("stat.skills.title"));
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
        // 🔌 外部公民客人席(untrusted 供稿、走采纳门、不占决策席)——标清别和自家 role 混脸
        const badge = m.is_external ? "🔌 " : "";
        const base = m.domain_name ? `${m.display} · ${m.domain_name}` : m.display;
        row.appendChild(el("span", { class: "rt-member-name", text: badge + base }));
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
      _appendRecallChip(log, payload.recall_used, payload.recall_as_of);   // Q1 召回解释:垫了哪几条记忆(空/缺=不渲染);docs/69 Q4:带时点则标"按 X 时点的记忆"
      if (follow) log.scrollTop = log.scrollHeight;
    }
    if (payload.crystallized && payload.skill_name) {
      const _nc = pushChatLine("system", t("drive.crystallized", { skill: payload.skill_name }));
      if (_nc && _nc.classList) _nc.classList.add("notice-crystal");   // P0-3:结晶回执的一秒余温
    }
    // 召回回执(Cut1 可见化):skill_name 来自后端真 recall 命中(drive 的 RecallHit),
    // 不是前端编的 —— stable 命中走 drive.fast_hit,dynamic 命中走 drive.method_reuse(方法重跑)。
    if (!payload.error && payload.skill_name && !payload.crystallized) {
      const _nr = pushChatLine("system", t(payload.fast_brain_hit ? "drive.fast_hit" : "drive.method_reuse",
        { skill: payload.skill_name }));
      if (_nr && _nr.classList) _nr.classList.add("notice-crystal");   // P0-3:「越用越像你」的可感知一秒
    }
    // 「第一个 10 分钟」旅程:等的演示任务回来了 → 推进状态机(非旅程消息不动)
    _journeyOnDriveDone(payload);
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
      // 后端静态整句(如共创模式的递口/退出/定稿句)过 BACKEND_ZH_EN:en 界面整句/前缀译,
      // zh 界面与不匹配的正常回复 = 原样(tB 恒等),零回归
      KarvyRender.appendMarkdown(line, tB(entry.text || ""));
    } else {
      line.appendChild(document.createTextNode(tB(entry.text || "")));
    }
    log.appendChild(line);
  }

  // ==== Q1 召回解释:回答气泡下的低调 chip(默认收起)→ 点开列出每条记忆 + 想起理由 ====
  // 理由行按后端真中间量拼:命中词面 / 语义标签交集 / 图谱扩散 N 跳;都没有 → 诚实兜底文案。
  function _recallWhy(r) {
    const parts = [];
    if (r.surface_terms && r.surface_terms.length) {
      parts.push(t("recall.why_terms", { terms: r.surface_terms.slice(0, 5).join("、") }));
    }
    if (r.concept_tags && r.concept_tags.length) {
      parts.push(t("recall.why_tags", { tags: r.concept_tags.join("、") }));
    }
    if (r.via_spread) parts.push(t("recall.why_spread", { hops: r.hops || 1 }));
    if (!parts.length) parts.push(t("recall.why_related"));
    return parts.join(" · ");
  }
  function _appendRecallChip(log, used, asOf) {
    if (!used || !used.length) return;
    const list = el("div", { class: "recall-list hidden" });
    for (const r of used) {
      const ts = r.provenance_ts ? new Date(r.provenance_ts * 1000).toLocaleDateString() : "";
      list.appendChild(el("div", { class: "recall-item" },
        el("div", { class: "recall-preview", text: r.content_preview || "" }),
        el("div", { class: "recall-meta" },
          el("span", { class: "recall-why", text: _recallWhy(r) }),
          ts ? el("span", { class: "recall-ts", text: t("recall.since", { date: ts }) }) : null)));
    }
    // docs/69 Q4:这轮是按某个过去时点召回的("你当时/上个月怎么理解的")→ chip 头显式标出,
    // 让人知道垫进去的是**那时**的旧认知(不是当下已更新的事实)。缺 = 当下召回,原文案。
    const asOfLabel = (asOf && isFinite(asOf))
      ? " · " + t("recall.as_of", { date: new Date(asOf * 1000).toLocaleDateString() }) : "";
    const chip = el("button", {
      class: "recall-chip" + (asOfLabel ? " recall-chip-asof" : ""), type: "button",
      text: "📚 " + t("recall.used_n", { n: used.length }) + asOfLabel,
      onclick: () => {
        list.classList.toggle("hidden");
        // 展开时把列表滚进视野(chip 常在聊天底部,不滚用户看不见展开了什么)
        if (!list.classList.contains("hidden")) list.scrollIntoView({ block: "nearest" });
      },
    });
    log.appendChild(el("div", { class: "recall-used" }, chip, list));
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
      const notice = el("div", { class: "chat-notice live" });   // live=真实追加才升入;历史重建不动
      if (window.KarvyRender) KarvyRender.appendMarkdown(notice, text || "");
      else notice.appendChild(document.createTextNode(text || ""));
      log.appendChild(notice);
      if (follow) log.scrollTop = log.scrollHeight;
      return notice;   // 返回节点:旅程收官要对「方法复用回执」聚光(调用方多数忽略)
    }
    const line = el("div", { class: "chat-line live " + role },
      el("span", { class: "role", text: _roleLabel(role) }));
    // 9.4:正文走 markdown + 消毒(KarvyRender);缺库回退裸文本。
    // agent 正文过 tB:后端静态整句(共创等)在 en 界面查 BACKEND_ZH_EN 整句/前缀译;
    // 用户自己的话绝不动(只译 agent 侧)。
    const _txt = role === "agent" ? tB(text || "") : (text || "");
    if (window.KarvyRender) KarvyRender.appendMarkdown(line, _txt);
    else line.appendChild(document.createTextNode(_txt));
    log.appendChild(line);
    if (follow) log.scrollTop = log.scrollHeight;
    return line;
  }

  function renderChatHistory(lines) {
    if (!lines || lines.length === 0) {
      // 不要清空 — 用户可能正在输入
      return;
    }
    const log = document.getElementById("chat-log");
    // S3 保卡:整块重建会把"还没拍的 inline 决策卡"冲掉(boot 时 state 快照与历史两个
    // 请求赛跑,历史后到即吃卡 —— 决策卡静默消失=决策 loop 反模式)。重建前摘下未终态
    // 的卡,重建后接回尾部;已拍的终态卡不保(历史里有回执,不重复)。
    const _keepCards = Array.from(
      log.querySelectorAll(".chat-line[data-proposal-id]:not([data-decided])"));
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
    _keepCards.forEach((n) => log.appendChild(n));   // 未拍的卡接回(等你拍板的东西绝不静默消失)
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
    // docs/90 刀3a:停止是安全网 —— **每条**活任务都有 ⏹ 停止(火灾键秒达,无确认弹窗;
    // 停错了损失小,任务可重跑)。路由按显式 tk.kind;老记录无 kind → 退回 who 嗅探(旧数据不崩)。
    let abortEl = null;
    if (tk.status !== "running") {
      _cancellingTasks.delete(tk.id);   // 终态到了 → 清本端"正在停"标(防 Set 单页会话缓涨)
    }
    if (tk.status === "running") {
      const who = tk.who || "";
      const isWf = tk.kind === "workflow" ||
        (!tk.kind && (who.indexOf("工作流") >= 0 || who.indexOf("Workflow") >= 0 || who.indexOf("⚙") >= 0));
      const isRt = tk.kind === "roundtable" ||
        (!tk.kind && (who.indexOf("圆桌") >= 0 || who.indexOf("Roundtable") >= 0 || who.indexOf("🎡") >= 0));
      const stopping = _cancellingTasks.has(tk.id) ||
        (tk.last_event && tk.last_event.kind === "cancelling");
      abortEl = el("button", { class: "task-abort" + (stopping ? " cancelling" : ""),
        text: stopping ? t("task.stopping") : t("task.stop"),
        disabled: stopping ? "disabled" : null,
        onClick: (e) => { e.stopPropagation(); if (!stopping) _abortTask(tk, isWf, isRt); } });
    }
    return el("div", { class: "task-card" + (blockedEl ? " has-blocked" : ""),
      onclick: (e) => { if (e.target && (e.target.classList.contains("task-check") || e.target.classList.contains("task-abort"))) return; openTaskDetail(tk); } },
      top,
      el("div", { class: "task-intent", text: tk.intent || "" }),
      blockedEl,
      stepsEl,
      abortEl,
      (tk.status !== "running" && tk.result) ? el("div", { class: "task-result", text: tk.result }) : null,
      (tk.status !== "running") ? el("div", { class: "task-jump", text: t("task.view_result") }) : null);
  }
  // docs/90 刀3a:点 ⏹ 停止 → 按 kind 打对应 cancel 端点(workflow → /workflow/cancel,
  // 圆桌 → /roundtable/cancel,其余一律通用 /api/task/cancel)。即时反馈:卡转 cancelling 态
  // (钮变"正在停止…"),终态由既有 task_status WS 推送/轮询刷新。无确认弹窗(火灾键秒达)。
  const _cancellingTasks = new Set();   // taskId → 已点停(本端即时反馈;终态到了自然出列表)
  async function _abortTask(tk, isWf, isRt) {
    _cancellingTasks.add(tk.id);
    renderTaskBoard(_lastTasks);        // 立即重画:钮转"正在停止…"(不等下一次 poll)
    try {
      const url = isWf ? "/api/workflow/cancel"
        : (isRt ? "/api/roundtable/cancel" : "/api/task/cancel");
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: tk.id }) });
      if (!r.ok) {                      // fail-loud:404 等别静默装停了
        _cancellingTasks.delete(tk.id);
        pushChatLine("system", "⚠ " + t("task.stop_failed", { who: _localizeWho(tk.who) }));
        renderTaskBoard(_lastTasks);
        return;
      }
      pushChatLine("system", t("task.aborting", { who: _localizeWho(tk.who) }));
    } catch (e) {
      _cancellingTasks.delete(tk.id);
      pushChatLine("system", "⚠ " + e.message);
      renderTaskBoard(_lastTasks);
    }
  }
  // ch4:任务分两象限 —— 跑完的进【流进来的料】,跑着的进【谁在忙】(干完即撤)
  // 微动效 P0-5:轮询整块重建的列表,只给**首次出现**的卡入场(每次重渲都播=群魔乱舞,明禁)
  const _seenCards = { board: new Set(), busy: new Set(), recent: new Set() };
  function _markIn(setName, key, node) {
    const s = _seenCards[setName];
    if (key && s && !s.has(String(key))) { s.add(String(key)); node.classList.add("card-in"); }
    return node;
  }

  function renderTaskBoard(tasks) {
    const board = document.getElementById("task-board");   // 料 = 已出结果
    const busy = document.getElementById("busy-list");     // 谁在忙 = running
    const done = tasks.filter((tk) => tk.status !== "running");
    const running = tasks.filter((tk) => tk.status === "running");
    if (board) {
      board.innerHTML = "";
      if (!done.length) {
        const emp = el("div", { class: "empty-state", text: t("empty.task_board") });
        // S4 空态引导:跟小卡说句话,料就会流进来(指向聊天)
        emp.appendChild(_emptyAction("empty.task_board_act", () => openChatModal()));
        board.appendChild(emp);
      } else done.forEach((tk) => board.appendChild(_markIn("board", tk.id, _taskCard(tk))));
    }
    if (busy) {
      busy.innerHTML = "";
      if (!running.length) {
        // 空态引导(朋友调研):不只说"空",告诉你第一步干嘛 + 一键跳建域
        busy.appendChild(el("div", { class: "empty-state busy-empty-guide" },
          el("div", { text: t("empty.busy_guide") }),
          el("button", { class: "busy-guide-btn", text: t("empty.busy_guide_btn"),
            onClick: () => openDomainsPanel() })));
      } else running.forEach((tk) => busy.appendChild(_markIn("busy", tk.id, _taskCard(tk))));
    }
    updatePulse();
    _updateDockYield();   // S1:料区有内容 → 卡皮巴拉自动让位
  }
  // 💰 token 成本表已迁 TS(源 frontend/src/tokens_panel.ts:顶栏 meter + 点开弹窗/各模型/各功能)
  // → window.KarvyTokens.pollMeter()(轮询刷 meter)/ window.KarvyTokens.open()(💰 点开)。

  // ============ step5 驾驶舱:脉搏 + "又懂了你"知识列 ============
  function _countCards(containerId, emptyClass) {
    const c = document.getElementById(containerId);
    if (!c) return 0;
    // docs/92 刀1:同链组壳(.h2a-chain-group)按**组内卡数**计 —— 收纳是视觉的,
    // 计数(脉搏/列头徽章/空态判定)必须仍数真实待拍张数,不因折叠少报。
    return Array.from(c.children).reduce((n, ch) => {
      if (ch.classList.contains(emptyClass)) return n;
      if (ch.classList.contains("h2a-chain-group")) return n + ch.querySelectorAll(".h2a-card").length;
      return n + 1;
    }, 0);
  }
  // 微动效 P1-2:脉搏文案换字才交叉淡化(新旧两层叠同格,旧淡出新淡入;同字直接跳过,
  // 不整段闪重绘)。首次填充/reduced-motion 静态换字(降级);层由 CSS #pulse-text>span 叠。
  function _setPulseText(pulse, next) {
    const prev = pulse.getAttribute("data-pulse") || "";
    if (prev === next) return;                     // 没变不重建(更不动画)
    pulse.setAttribute("data-pulse", next);
    pulse.textContent = "";
    const cur = el("span", { text: next });
    pulse.appendChild(cur);
    if (prev && !_MOTION_REDUCED.matches) {
      cur.classList.add("pulse-swap-in");
      const old = el("span", { class: "pulse-swap-out", text: prev, "aria-hidden": "true" });
      old.addEventListener("animationend", () => old.remove());
      pulse.appendChild(old);
    }
  }
  function updatePulse() {
    const pulse = document.getElementById("pulse-text");
    if (!pulse) return;
    const ran = _countCards("task-board", "empty-state");
    const pending = _countCards("h2a-list", "h2a-empty");
    // 顶栏主位让给"有没有任务在跑 / 有没有卡等拍板"(朋友调研;数据源=谁在忙/拍板两列现成)
    const running = _countCards("busy-list", "empty-state");
    if (running > 0 || pending > 0) _setPulseText(pulse, t("cockpit.pulse_topline", { running: running, pending: pending }));
    else if (ran > 0) _setPulseText(pulse, t("cockpit.pulse_ran", { ran: ran }));
    else _setPulseText(pulse, t("cockpit.pulse_idle"));
    // 拍板权重(Hardy):有卡 → 决策列点亮 + 列头计数徽章;拍完 → 回安静主位
    const dcol = document.querySelector(".col-decide");
    if (dcol) dcol.classList.toggle("has-pending", pending > 0);
    const badge = document.getElementById("decide-count");
    if (badge) {
      if (pending > 0) { badge.textContent = String(pending); badge.hidden = false; }
      else badge.hidden = true;
    }
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

  // ============ ⑤c 环境感知召回:「相关的料」区(WS ambient_recall)============
  // 契约(8c537ae):{"type":"ambient_recall","payload":{"for_intent","hits":[{kind,id,name,summary,score}]}}
  // 新 intent 的料到达即整块替换旧料;无料不占位(display:none,朋友调研:空态别占地)。
  function renderAmbientRecall(payload) {
    const box = document.getElementById("ambient-recall");
    if (!box) return;
    const hits = (payload && payload.hits) || [];
    box.innerHTML = "";
    if (!hits.length) { box.classList.add("hidden"); _updateDockYield(); return; }
    box.classList.remove("hidden");
    _updateDockYield();   // S1:料浮出 → 卡皮巴拉自动让位
    const head = el("div", { class: "ambient-head" },
      el("span", { class: "ambient-title", text: t("ambient.title") }));
    if (payload.for_intent) {
      head.appendChild(el("span", { class: "ambient-for", text: t("ambient.for", { intent: payload.for_intent }) }));
    }
    box.appendChild(head);
    for (const h of hits) {
      const isSkill = h.kind === "skill";
      const row = el("div", { class: "ambient-hit ambient-" + (h.kind || "belief"),
        title: isSkill ? t("ambient.open_skill") : t("ambient.open_belief"),
        onClick: () => { if (isSkill) _ambientOpenSkill(h); else _ambientOpenBelief(h); } });
      row.appendChild(el("span", { class: "ambient-ico", text: isSkill ? "⚡" : "📚" }));
      row.appendChild(el("span", { class: "ambient-name", text: h.name || h.id || "" }));
      if (h.summary) row.appendChild(el("span", { class: "ambient-summary", text: h.summary }));
      box.appendChild(row);
    }
  }
  // 点技能料 → 打开技能库面板并定位/高亮那张技能卡(T4:面板脚本先 ensure,失败有人话提示)
  function _ambientOpenSkill(hit) {
    return _openLazyPanel("skills", async () => {
      await window.KarvySkillsPanel.open();
      _locateMgmtCard(hit.name || "");
    });
  }
  // 点知识料 → 打开知识库面板,用列表自带搜索过滤到该条并高亮(T4:同上)
  function _ambientOpenBelief(hit) {
    return _openLazyPanel("memory", async () => {
      await window.KarvyMemoryPanel.open();
      const q = (hit.name || hit.summary || "").trim();
      const search = document.querySelector("#mgmt-body .paged-search");
      if (search && q) {
        search.value = q;
        search.dispatchEvent(new Event("input", { bubbles: true }));
      }
      _locateMgmtCard(q);
    });
  }
  // 在管理面板里按名字找卡 → 滚过去 + 脉冲高亮(复用 turn-locate-flash 动画)
  function _locateMgmtCard(name) {
    if (!name) return;
    const body = document.getElementById("mgmt-body");
    if (!body) return;
    const cards = Array.from(body.querySelectorAll(".mgmt-card"));
    const target = cards.find((c) => (c.textContent || "").indexOf(name) >= 0) || cards[0];
    if (!target) return;
    setTimeout(() => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("turn-locate-flash");
      setTimeout(() => target.classList.remove("turn-locate-flash"), 1800);
    }, 60);
  }

  // ============ 楔子透明化:技能「生命线」时间线(朋友调研:结晶不透明,promote/evict 没有 why)============
  // 契约(与后端约定死,别改形状):GET /api/skill_lifecycle →
  //   {"skills":[{"name","sig","events":[{"ts","type","detail","trace_ref"}]}]},
  //   type ∈ crystallized / revised / rerun / improved。
  const _LIFECYCLE_ICONS = { crystallized: "💎", revised: "✏️", rerun: "🔁", improved: "📈" };
  async function openSkillLifecycle(skillName) {
    openMgmtModal(t("lifeline.title", { name: skillName }));
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const data = await _getJSON("/api/skill_lifecycle");
    if (!data || !data.skills) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("lifeline.load_failed") }));
    } else {
      const rec = (data.skills || []).find((s) => s.name === skillName);
      const events = (rec && rec.events) || [];
      if (!events.length) {
        body.appendChild(el("div", { class: "mgmt-empty", text: t("lifeline.empty") }));
      } else {
        body.appendChild(el("div", { class: "mgmt-hint", text: t("lifeline.hint") }));
        const tl = el("div", { class: "life-timeline" });
        for (const ev of events) {
          const when = ev.ts ? new Date(ev.ts * 1000).toLocaleString() : "";
          tl.appendChild(el("div", { class: "life-ev life-" + (ev.type || "") },
            el("span", { class: "life-ev-icon", text: _LIFECYCLE_ICONS[ev.type] || "·" }),
            el("div", { class: "life-ev-main" },
              el("div", { class: "life-ev-head" },
                el("span", { class: "life-ev-type", text: t("lifeline.type_" + (ev.type || "rerun")) }),
                el("span", { class: "life-ev-time", text: when })),
              ev.detail ? el("div", { class: "life-ev-detail", text: ev.detail }) : null,
              ev.trace_ref ? el("div", { class: "life-ev-trace", text: "🔬 " + ev.trace_ref }) : null)));
        }
        body.appendChild(tl);
      }
    }
    body.appendChild(el("button", { class: "mgmt-submit", text: t("skills.back"),
      onClick: () => window.KarvySkillsPanel.open() }));
  }
  // ============ 决策时间线 = 决策的生命线(docs/85 Part B):八站垂直轴 + ▶逐站回放 ============
  // 与技能生命线同心智同 modal 同数据纪律(K4 只读,全从 Trace 聚合)。契约(与后端约定死):
  //   GET /api/decision/{pid}/lifeline → {ok, stub, events:[{ts,type,detail,trace_ref,…}],
  //   steps:[{ts,name,gist,input,(ok,err)}], tokens, task}。
  //   type ∈ born/aligned/judged/decided/dispatched/learned。
  // 缺哪站显诚实空位「此段无记录」;埋点前老决策 = 拍板存根 + 一句实话(stub_hint)。
  // ♻ learned 站(三刀)= **批次级**归因(偏好按批结晶,逐条对应没记录)—— 必带免责句,绝不编精确归因。
  const _DLIFE_STATIONS = ["born", "aligned", "judged", "decided", "dispatched", "executed", "result", "learned"];
  const _DLIFE_ICONS = { born: "💡", aligned: "🧭", judged: "✍️", decided: "⚖",
                         dispatched: "🚚", executed: "🔧", result: "✅", learned: "♻" };
  async function openDecisionLifeline(proposalId, summary) {
    openMgmtModal(t("dlife.title"));
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const data = await _getJSON("/api/decision/" + encodeURIComponent(proposalId) + "/lifeline");
    if (!data || !data.ok) {
      body.appendChild(el("div", { class: "mgmt-empty",
        text: (data && data.reason) ? tB(data.reason) : t("dlife.load_failed") }));
      return;
    }
    const head = (data.events || []).find((ev) => ev.type === "born");
    const shown = summary || (head && head.summary) || "";
    if (shown) body.appendChild(el("div", { class: "dlife-summary", text: shown }));
    body.appendChild(el("div", { class: "mgmt-hint",
      text: t(data.stub ? "dlife.stub_hint" : "dlife.hint") }));
    // ▶ 逐站回放:~400ms/站 淡入高亮;prefers-reduced-motion → 全显不演
    const tl = el("div", { class: "life-timeline dlife-timeline" });
    body.appendChild(el("button", { class: "mgmt-inline-link dlife-replay",
      text: "▶ " + t("dlife.replay"), onClick: () => _dlifeReplay(tl) }));
    const byType = {};
    (data.events || []).forEach((ev) => { (byType[ev.type] = byType[ev.type] || []).push(ev); });
    for (const st of _DLIFE_STATIONS) {
      const station = el("div", { class: "dlife-station", "data-station": st });
      station.appendChild(el("div", { class: "dlife-st-head" },
        el("span", { class: "life-ev-icon", text: _DLIFE_ICONS[st] || "·" }),
        el("span", { class: "life-ev-type", text: t("dlife.st_" + st) })));
      let filled = false;
      if (st === "executed") {
        // 🔧 执行工具步(run_id 投影,"each agent's reasoning steps")+ 💰 token。
        // 下钻(二刀):点一行展开 输入摘要 + ok/error_reason(slice C 成败事实;
        // 老格式条目无 ok 字段 → 不标 ✓/✗,不编)。
        for (const s of (data.steps || [])) {
          const stepRow = el("div", { class: "dlife-row dlife-step" });
          const head = el("div", { class: "dlife-step-head", title: t("dlife.step_expand_title") },
            el("span", { class: "dlife-step-name", text: "· " + (s.name || "?") }),
            (s.ok === true) ? el("span", { class: "dlife-step-ok", text: "✓" })
              : (s.ok === false) ? el("span", { class: "dlife-step-err", text: "✗" }) : null,
            s.gist ? el("span", { class: "dlife-step-gist", text: s.gist }) : null);
          const det = el("div", { class: "dlife-step-detail" });
          if (typeof s.ok === "boolean") {
            det.appendChild(el("div", { class: s.ok ? "dlife-step-ok" : "dlife-step-err",
              text: s.ok ? t("dlife.step_ok") : (t("dlife.step_failed") + (s.err ? ": " + s.err : "")) }));
          }
          if (s.input) det.appendChild(el("div", { class: "dlife-step-input", text: s.input }));
          head.addEventListener("click", () => stepRow.classList.toggle("dlife-step-open"));
          stepRow.appendChild(head);
          stepRow.appendChild(det);
          station.appendChild(stepRow);
          filled = true;
        }
        if (typeof data.tokens === "number" && data.tokens > 0) {
          station.appendChild(el("div", { class: "dlife-row dlife-tokens",
            text: t("dlife.tokens", { n: data.tokens }) }));
          filled = true;
        }
      } else if (st === "aligned") {
        // 🧭 建卡事实(T2 卡缓存命中才有):命中你几条标准 / 几处违背被标出
        for (const ev of (byType.aligned || [])) {
          const row = el("div", { class: "dlife-row" });
          row.appendChild(el("div", { class: "life-ev-detail",
            text: t("dlife.aligned_hits", { n: ev.aligned || 0 })
                  + (ev.aligned_omitted ? t("dlife.aligned_omitted", { n: ev.aligned_omitted }) : "") }));
          if (ev.violations) row.appendChild(el("div", { class: "dlife-violation-note",
            text: t("dlife.aligned_violations", { n: ev.violations }) }));
          station.appendChild(row);
          filled = true;
        }
      } else if (st === "judged") {
        // ✍️ 你的判断(T2 真数据,二刀):陈述依据 / 改动摘要;零判断与无卡记录各说各的实话
        for (const ev of (byType.judged || [])) {
          const when = ev.ts ? new Date(ev.ts * 1000).toLocaleString() : "";
          const row = el("div", { class: "dlife-row" },
            el("span", { class: "life-ev-time", text: when }));
          if (ev.detail) row.appendChild(el("div", { class: "life-ev-detail", text: "✍️ " + ev.detail }));
          if (ev.edits_n) row.appendChild(el("div", { class: "dlife-judged-note",
            text: t("dlife.judged_edits", { n: ev.edits_n }) + (ev.edited ? ": " + ev.edited : "") }));
          if (!ev.detail && !ev.edits_n) {
            row.appendChild(el("div", { class: "dlife-judged-note",
              text: t(ev.card_seen ? "dlife.judged_blind" : "dlife.judged_nocard") }));
          }
          if (ev.trace_ref) row.appendChild(el("div", { class: "life-ev-trace", text: "🔬 " + ev.trace_ref }));
          station.appendChild(row);
          filled = true;
        }
      } else if (st === "learned") {
        // ♻ 回流(三刀):这批拍板参与喂养了 N 条偏好 —— **批次级**归因,免责句必带(绝不编逐条对应)
        const evs = byType.learned || [];
        if (evs.length) {
          const total = evs[0].learned_total || evs.length;
          station.appendChild(el("div", { class: "dlife-row dlife-learned-head",
            text: t("dlife.learned_batch", { n: total }) }));
          const marks = { reinforced: "▲", weakened: "▼", revoked: "✕" };
          for (const ev of evs) {
            const row = el("div", { class: "dlife-row dlife-learned dlife-learned-" + (ev.pref_event || "") },
              el("span", { class: "dlife-learned-mark", text: marks[ev.pref_event] || "·" }),
              el("span", { class: "dlife-learned-label", text: t("dlife.learned_" + (ev.pref_event || "reinforced")) }),
              el("span", { class: "life-ev-detail", text: ev.detail || "" }));
            if (typeof ev.strength_before === "number" && typeof ev.strength_after === "number") {
              row.appendChild(el("span", { class: "dlife-strength",
                text: " " + ev.strength_before + " → " + ev.strength_after }));
            }
            station.appendChild(row);
          }
          station.appendChild(el("div", { class: "dlife-row dlife-learned-hint",
            text: t("dlife.learned_hint") }));
          filled = true;
        }
      } else if (st === "result") {
        const tk = data.task;
        if (tk && tk.status === "running") {
          station.appendChild(el("div", { class: "dlife-row", text: t("dlife.result_running") }));
          filled = true;
        } else if (tk && (tk.result || tk.status)) {
          station.appendChild(el("div", { class: "dlife-row",
            text: (tk.status === "error" ? "✗ " : "✔ ") + (tk.result || tk.status) }));
          filled = true;
        } else {
          // 无任务态 → 回退 dispatched 回执/验收 verdict 当结果行(诚实:只摆真有的)
          const disp = (byType.dispatched || [])[0];
          if (disp && (disp.detail || disp.verdict)) {
            station.appendChild(el("div", { class: "dlife-row",
              text: (disp.ok === false ? "✗ " : "✔ ") + (disp.verdict ? "[" + disp.verdict + "] " : "")
                    + tB(disp.detail || "") }));
            filled = true;
          }
        }
      } else {
        for (const ev of (byType[st] || [])) {
          const when = ev.ts ? new Date(ev.ts * 1000).toLocaleString() : "";
          const row = el("div", { class: "dlife-row" },
            el("span", { class: "life-ev-time", text: when }));
          if (ev.decision) row.appendChild(el("span", {
            class: "recent-badge recent-" + String(ev.decision).toLowerCase(),
            text: (_DECISION_BADGE[ev.decision] || "·") + " " + ev.decision }));
          if (ev.auto) row.appendChild(el("span", { class: "dlife-auto", text: t("dlife.auto") }));
          if (ev.detail) row.appendChild(el("div", { class: "life-ev-detail", text: tB(ev.detail) }));
          if (typeof ev.strength === "number") row.appendChild(el("div", { class: "dlife-strength",
            text: t("proposal.strength", { pct: Math.round(ev.strength * 100) }),
            title: t("proposal.strength.title") }));
          if (ev.trace_ref) row.appendChild(el("div", { class: "life-ev-trace", text: "🔬 " + ev.trace_ref }));
          station.appendChild(row);
          filled = true;
        }
      }
      if (!filled) {
        station.classList.add("dlife-missing");   // 诚实空位:此段无记录(不编)
        station.appendChild(el("div", { class: "dlife-row dlife-empty-note", text: t("dlife.no_record") }));
      }
      tl.appendChild(station);
    }
    body.appendChild(tl);
  }
  function _dlifeReplay(tl) {
    const stations = Array.from(tl.querySelectorAll(".dlife-station"));
    let reduced = false;
    try { reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}
    if (reduced) {   // reduced-motion:不演,全显(等价终态)
      tl.classList.remove("dlife-replaying");
      stations.forEach((s) => s.classList.add("dlife-lit"));
      return;
    }
    tl.classList.add("dlife-replaying");           // 先全暗(CSS 降透明度)
    stations.forEach((s) => s.classList.remove("dlife-lit"));
    stations.forEach((s, i) => setTimeout(() => s.classList.add("dlife-lit"), 400 * i + 80));
  }

  // 技能库面板(TS 构建产物,不在此改)每张技能卡挂「🧬 生命线」入口:
  // MutationObserver 观察 #mgmt-body,面板每次(重)渲染都补挂,不漏内部 re-render。
  function _wireSkillLifelineEntries() {
    const bodyEl = document.getElementById("mgmt-body");
    if (!bodyEl) return;
    const decorate = () => {
      const title = document.getElementById("mgmt-title");
      if (!title || title.textContent !== t("skills.title")) return;   // 只在技能库面板动手
      bodyEl.querySelectorAll(".mgmt-card").forEach((card) => {
        if (card.dataset.lifelineWired) return;
        const nameEl = card.querySelector(".mc-name span");
        const nm = nameEl ? (nameEl.textContent || "") : "";
        if (nm.indexOf("🧩 ") !== 0) return;   // 只挂真技能卡(Coding 能力卡/权限总览卡不是)
        const actions = card.querySelector(".dpref-actions");
        if (!actions) return;
        card.dataset.lifelineWired = "1";
        actions.appendChild(el("button", { class: "dpref-edit life-btn",
          title: t("lifeline.btn_title"), text: "🧬 " + t("lifeline.btn"),
          onClick: () => openSkillLifecycle(nm.slice(2).trim()) }));
      });
    };
    new MutationObserver(decorate).observe(bodyEl, { childList: true, subtree: true });
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
    // 决策回链(docs/85 二刀):这活来自你的哪次拍板 —— 「⚖ 由你拍板于 {时间} · 🧬 回放决策」。
    // 键在但生命线载不出 → 撤行不摆坏入口;没有 decided(静音代办)→ 只给回放链,不冒认"你拍的"。
    if (tk.proposal_id) {
      const backRow = el("div", { class: "task-decision-backlink" });
      body.appendChild(backRow);
      _getJSON("/api/decision/" + encodeURIComponent(tk.proposal_id) + "/lifeline").then((lf) => {
        if (!lf || !lf.ok) { backRow.remove(); return; }
        const dec = (lf.events || []).find((ev) => ev.type === "decided");
        if (dec && dec.ts) {
          backRow.appendChild(el("span", { class: "task-backlink-when",
            text: t("task.from_decision", { when: new Date(dec.ts * 1000).toLocaleString() }) + " · " }));
        }
        backRow.appendChild(el("button", { class: "dlife-link", text: "🧬 " + t("task.replay_decision"),
          title: t("dlife.entry_title"),
          onClick: () => openDecisionLifeline(String(tk.proposal_id), tk.intent || "") }));
      }).catch(() => backRow.remove());
    }
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

  // ============ 聊天双形态(docs/46 S1):对话视图=常驻中央(docked);rail 放大态/移动端=弹层 ============
  function _isMobile() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 720px)").matches);
  }
  // 聊天是否"常驻中央"(docked):桌面 + 对话视图(非 rail 放大态)。此时 open 退化为聚焦输入框、close 无操作。
  function _chatDocked() {
    return !_isMobile() && !document.body.classList.contains("board-view");
  }
  function openChatModal() {
    const m = document.getElementById("chat-modal");
    if (!m) return;
    m.classList.remove("hidden");
    // 桌面视图最小化态(desk-min):任何"去聊天"路径都要能拉起窗(Hardy 实拍:知识库点进去拉不起;
    // 12 处调用方全走这儿,一处修全修)。restoreWin 会同步持久化 min:false,别只删 class。
    const _kd = window.KarvyDesktop;
    if (_kd && _kd.restoreChat && m.classList.contains("desk-min")) _kd.restoreChat();
    if (!_chatDocked()) document.body.classList.add("chat-open");   // 弹层态:FAB 让位(CSS)
    const input = document.getElementById("chat-input");
    if (input) setTimeout(() => input.focus(), 30);
  }
  function closeChatModal() {
    if (_chatDocked()) return;   // 常驻聊天没有"关闭"一说
    const m = document.getElementById("chat-modal");
    if (m) m.classList.add("hidden");
    document.body.classList.remove("chat-open");
  }

  // ============ 对话⇄桌面 视图切换(docs/46 §4.2 + docs/51 + docs/59 方案A):body class 一行换挡 ============
  // 一主一副:对话=唯一的家(默认、永远回得来);桌面=唯一可切的第二形态(旁观你的团队上班)。
  // 旧"看板视图"退位成 rail 的 ⛶ 临时放大态(_setBoardZoom):功能一个不删,只是不再冒充一个"家"。
  function _applyView(mode) {
    const desk = mode === "desk" && !_isMobile();   // docs/51 §3.1:≤720px 桌面隐喻不存在 → 降级 chat
    document.body.classList.remove("board-view");   // 放大是临时态:切视图先收(不跨视图残留)
    document.body.classList.toggle("desk-view", desk);
    // 桌面壳(desktop.js)的进出:enter 摆便签/窗口/dock,leave 清干净内联痕迹(老视图像素级不动)
    if (window.KarvyDesktop) { if (desk) window.KarvyDesktop.enter(); else window.KarvyDesktop.leave(); }
    const m = document.getElementById("chat-modal");
    if (m) {
      if (_chatDocked()) m.classList.remove("hidden");   // 常驻:永远可见(桌面视图=可拖窗,也常驻)
      else m.classList.add("hidden");                    // 弹层态:起始收起(FAB/入口再弹)
    }
    document.body.classList.remove("chat-open");   // 切视图 = 回到干净弹层态
    // switch 两态明示(Hardy:别让用户猜):active 落在**当前**视图上
    const optChat = document.getElementById("view-opt-chat");
    const optDesk = document.getElementById("view-opt-desk");
    if (optChat) optChat.classList.toggle("active", !desk);
    if (optDesk) optDesk.classList.toggle("active", desk);
    _updateZoomBtn();
    _updateDockYield();
  }
  function _setView(mode) {
    if (mode !== "desk") mode = "chat";   // 只有两个家:chat|desk(board 已退位成放大态,不再是档位)
    try { localStorage.setItem("karvyloop_view", mode); } catch (e) {}
    // 对话⇄桌面用 View Transitions 原生 crossfade(渐进增强:不支持/减弱动效则瞬切)
    const _reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (document.startViewTransition && !_reduced) document.startViewTransition(() => _applyView(mode));
    else _applyView(mode);
  }

  // ============ rail ⛶ 放大(docs/59 方案A):四象限临时全屏,复用 body.board-view 的 2×2 CSS ============
  // 临时形态,不是家:**不写** karvyloop_view(刷新回对话);⛶/✕ 或 Esc 回对话;
  // 只在对话视图存在(桌面视图/移动端无此形态)。放大态下聊天收进弹层(FAB 再弹)= 原看板行为原样复用。
  function _boardZoomed() { return document.body.classList.contains("board-view"); }
  function _setBoardZoom(on) {
    on = !!on && !document.body.classList.contains("desk-view") && !_isMobile();
    if (on !== _boardZoomed()) {
      const _apply = () => {
        document.body.classList.toggle("board-view", on);
        const m = document.getElementById("chat-modal");
        if (m) {
          if (_chatDocked()) m.classList.remove("hidden");   // 回对话:聊天回中央常驻
          else m.classList.add("hidden");                    // 放大态:聊天起始收起(FAB/入口再弹)
        }
        document.body.classList.remove("chat-open");
        _updateDockYield();
      };
      // 微动效 P0-6:rail⛶ 放大走 View Transitions —— 四象限列带 view-transition-name,
      // 从 rail 位置连续变形到 2×2 大格("同一个东西换姿势"的空间连续性),不是瞬间重排。
      // ⛶→✕ 的按钮态必须在 _apply **里面**刷(VT 把变更推迟到回调,外面同步读=读到旧态)
      const _applyAndSync = () => { _apply(); _updateZoomBtn(); };
      if (document.startViewTransition && !_MOTION_REDUCED.matches) document.startViewTransition(_applyAndSync);
      else _applyAndSync();
      return;
    }
    _updateZoomBtn();
  }
  // ⛶ 钮双态:rail 态=⛶(放大);放大态=✕(回对话)。data-i18n-title 同步换 key,语言切换后 title 不错位。
  function _updateZoomBtn() {
    const btn = document.getElementById("rail-zoom-btn");
    if (!btn) return;
    const zoomed = _boardZoomed();
    btn.textContent = zoomed ? "✕" : "⛶";
    btn.dataset.i18nTitle = zoomed ? "rail.zoom.exit_title" : "rail.zoom.title";
    btn.title = t(zoomed ? "rail.zoom.exit_title" : "rail.zoom.title");
    btn.classList.toggle("active", zoomed);
  }

  // 卡皮巴拉让位(S1):对话视图下,料区(📥)有内容时右下吉祥物自动让位,不压 rail。
  function _updateDockYield() {
    const dock = document.querySelector(".karvy-dock");
    if (!dock) return;
    const ambient = document.getElementById("ambient-recall");
    const hasIntel = _countCards("task-board", "empty-state") > 0 ||
      !!(ambient && !ambient.classList.contains("hidden"));
    dock.classList.toggle("dock-yield", _chatDocked() && hasIntel);
  }

  // rail 格折叠(对话视图):点列头收/展,记 localStorage;列头上的按钮(🔮/⟳)不触发折叠。
  function _setupRailCollapse() {
    document.querySelectorAll(".cockpit-grid .cockpit-col").forEach((col) => {
      const cls = Array.from(col.classList).find((c) => c.indexOf("col-") === 0) || "col";
      const key = "karvy.rail." + cls;
      try { if (localStorage.getItem(key) === "1") col.classList.add("col-collapsed"); } catch (e) {}
      const head = col.querySelector(".col-head");
      if (!head) return;
      head.addEventListener("click", (e) => {
        if (e.target && e.target.closest && e.target.closest(".col-act")) return;   // 🔮/⟳ 照常
        const on = col.classList.toggle("col-collapsed");
        try { localStorage.setItem(key, on ? "1" : "0"); } catch (e2) {}
      });
    });
  }
  // 顶栏小卡(docs/46 S2):每隔一阵冒个省略号泡(· → ······ 循环),提示副驾能点。
  // 佛系人设:不说生硬话术,只发省略号动效。正在打字 / 弹层聊天开着 / 鼠标悬上时不弹。
  function _startKarvyIdleBubble() {
    const bubble = document.getElementById("karvy-bubble");
    const host = document.getElementById("topbar-karvy");
    if (!bubble || !host) return;
    const dots = bubble.querySelector(".karvy-bubble-dots");
    let dotTimer = null, hideTimer = null, hovering = false;
    host.addEventListener("mouseenter", () => { hovering = true; });
    host.addEventListener("mouseleave", () => { hovering = false; });
    const hide = () => {
      bubble.classList.add("hidden");
      if (dotTimer) { clearInterval(dotTimer); dotTimer = null; }
    };
    const show = () => {
      if (!document.body.classList.contains("desk-view")) return;   // 只在桌面视图冒泡(挂卡皮巴拉头上)
      const modal = document.getElementById("chat-modal");
      const overlayOpen = modal && !modal.classList.contains("hidden") && !_chatDocked();
      const typing = document.activeElement === document.getElementById("chat-input");
      if (overlayOpen || typing || hovering) return;   // 聊着呢 / 打字中 / 鼠标悬上 → 不打扰
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
  // docs/46 §5:顶栏副驾按钮 —— 点击 = 切到与小卡的私聊并聚焦输入框
  // (对话视图下 = switchPeer(小卡)+focus;rail 放大态/移动端下 openChatModal 先弹层)。
  function _talkToKarvy() {
    openChatModal();
    const list = document.getElementById("peer-list");
    if (list) {
      const rows = Array.from(list.querySelectorAll(".peer-row"));
      for (const row of rows) {
        let p = null;
        try { p = JSON.parse(row.dataset.peer || ""); } catch (e) { continue; }
        // 小卡私聊线:l0 非群 **且非直聊角色**(role==agent 是直聊某角色,不是小卡)。
        if (p && p.domain_id === "l0" && !p.is_group && !(p.role === "agent" && p.agent_id)) {
          if (!row.classList.contains("active")) row.click();   // 已在小卡场就不重切(免重拉历史)
          break;
        }
      }
    }
    const input = document.getElementById("chat-input");
    if (input) setTimeout(() => input.focus(), 80);
  }

  function setupChatModal() {
    const open = document.getElementById("chat-open");
    if (open) open.addEventListener("click", openChatModal);
    // 【你的副驾】按钮已撤(Hardy 2026-07-03);_talkToKarvy 保留给移动端/后续桌面隐喻复用。
    void _talkToKarvy;
    const close = document.getElementById("chat-modal-close");
    if (close) close.addEventListener("click", closeChatModal);
    const overlay = document.getElementById("chat-modal");
    if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) closeChatModal(); });
    const rt = document.getElementById("roundtable-btn");
    if (rt) rt.addEventListener("click", openRoundtable);
  }

  // ============ Intent submit (form) ============

  // 微动效 P1-5:发送**成功**那刻按钮一次轻微回弹(kv-sent,单次;失败不庆祝,
  // 无任何 loading 转圈;reduced-motion 由 CSS 总闸关)。按压 scale(.97) 在 CSS :active。
  function _sendBtnCelebrate() {
    const b = document.getElementById("chat-send");
    if (!b) return;
    b.classList.remove("kv-sent");
    void b.offsetWidth;   // 重启动画
    b.classList.add("kv-sent");
  }

  // 发送一条聊天(表单提交按钮 + Enter 都走这里)。从 contenteditable 读文本 + 被 @ 的角色。
  async function _submitChat() {
    const { text, mentions } = _readChatInput();
    // 多模态:抓附件(文本内联 / 图片走 images),建展示清单(缩略图,落历史),再清附件区
    const _imgs = _attachmentsImages();
    const _txtInline = _attachmentsTextInline();
    if (!text && !_attachments.length) return;   // 纯空不发;有附件(哪怕没文字)也能发
    // docs/66 §F 闸门(Hardy 2026-07-07 体验反馈):识别出"聊知识"意图就**先停住等你选**——
    // [打开]带这句话进知识库聊天,[不用]才走正常 drive。此前提示条与正常回答并行 = 两个脑子抢答。
    if (_maybeKnowledgeHint(text)) return;       // 消息留在输入框:不派发、不渲染、附件不动
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
          _sendBtnCelebrate();   // P1-5:真送达才回弹
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
          if (res.ok) {
            _sendBtnCelebrate();   // P1-5:真送达才回弹
            _renderWorkflowPlan(res.plan, sendText, res.matched, mentions);   // 弹可编辑步骤表(命中则提议复用)
          } else pushChatLine("system", "⚠ " + (tB(res.reason) || "plan failed"));
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
    if (sent) _sendBtnCelebrate();   // P1-5:WS 真送出才回弹
    if (!sent) {
      try {
        const r = await fetch("/api/intent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intent: sendText, mention: mention, mention_domain: mentionDomain, images: _imgs, attachments: _attach }),
        });
        if (r.ok) {
          const payload = await r.json();
          _sendBtnCelebrate();   // P1-5:HTTP 兜底真送达才回弹
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

  // ============ docs/46 S4:新手引导 ============
  // tour 用 driver.js(vendored static/vendor/,MIT v1.3.6;intro.js/shepherd 是 AGPL 不许用)。
  // 按需注入:首启 / 点「重看引导」才加载那 ~21KB,不压常驻包。IIFE 全局 = window.driver.js.driver。
  let _driverLoading = null;
  function _ensureDriverJs() {
    if (window.driver && window.driver.js && window.driver.js.driver) return Promise.resolve();
    if (_driverLoading) return _driverLoading;
    _driverLoading = new Promise((resolve, reject) => {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = "/static/vendor/driver.min.css";
      document.head.appendChild(css);
      const s = document.createElement("script");
      s.src = "/static/vendor/driver.min.js";
      s.onload = () => (window.driver && window.driver.js && window.driver.js.driver
        ? resolve() : reject(new Error("driver.js global missing")));
      s.onerror = () => { _driverLoading = null; reject(new Error("driver.min.js load failed")); };
      document.head.appendChild(s);
    });
    return _driverLoading;
  }

  // —— 蒙版聚光统一档(Hardy 2026-07-04:「引导气泡不认真看找不到」)——
  // 标准做法 = 黑半透蒙版罩住其余界面、目标镂空高亮、popover 对比度拉足。driver.js 原生
  // 支持,这里**显式锁配置**(防 vendor 默认漂移);光圈/popover 样式在 styles.css
  // (.driver-active-element / .karvy-tour-pop)。蒙版只在引导激活时存在:Esc / 点蒙版即撤。
  function _spotCfg() {
    const reduced = !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    return {
      overlayColor: "#000", overlayOpacity: 0.7,
      stagePadding: 6, stageRadius: 10,
      popoverClass: "karvy-tour-pop",
      animate: !reduced,   // 减动效偏好 → 蒙版/镂空不做过渡动画(光圈脉动由 CSS 同一偏好关)
    };
  }
  // 单元素聚光(旅程时刻用):无 popover —— 旅程条/回执自己就是气泡,蒙版只负责「躲不开」。
  let _spot = null;
  function _spotlightDismiss() {
    if (_spot) { try { _spot.destroy(); } catch (e) {} _spot = null; }
  }
  function _spotlightEl(target) {
    if (!target || !_tourVisible(target)) return;
    // 强制配 Key 锁(mgmt-modal)开着不抢戏;6 步 tour 正开着也不叠蒙版
    const mgmt = document.getElementById("mgmt-modal");
    if (mgmt && !mgmt.classList.contains("hidden")) return;
    if (!_spot && document.body.classList.contains("driver-active")) return;
    _ensureDriverJs().then(() => {
      if (!_tourVisible(target)) return;   // 异步回来再核一次形态(视图可能已切走)
      if (!_spot && document.body.classList.contains("driver-active")) return;
      _spotlightDismiss();
      _spot = window.driver.js.driver(_spotCfg());
      _spot.highlight({ element: target });
    }).catch(() => {});
  }

  const _TOUR_DONE_KEY = "karvyloop_tour_done";
  // 元素"当前形态下是否真可见"(非"存在"):各形态藏锚方式不同(rail 放大态 hidden 聊天/桌面
  // display:none 侧栏/聊天窗最小化),隐藏元素 rect 全 0 → driver.js 把 popover 钉左上角
  // 0×0 压 logo = UI 错乱。offsetParent 判 display 链;dock 是 fixed(offsetParent 恒 null
  // 但可见)→ rect 面积兜底。
  function _tourVisible(el) {
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  // 候选链取第一个可见者(文档序会先撞被 display:none 藏的同名元素,所以挑"可见"非"第一个")
  function _tourAnchor(sels) {
    for (let i = 0; i < sels.length; i++) {
      const nodes = document.querySelectorAll(sels[i]);
      for (let j = 0; j < nodes.length; j++) { if (_tourVisible(nodes[j])) return nodes[j]; }
    }
    return null;
  }
  // 引导前归位:决策列(步5 核心)折叠了先展开——只动 DOM 不写 localStorage,刷新后仍复用户偏好。
  function _prepForTour() {
    try {
      document.querySelectorAll(".col-decide.col-collapsed").forEach((c) => c.classList.remove("col-collapsed"));
    } catch (e) { /* 某视图无此列不挡 */ }
  }
  // 6 步 spotlight(docs/46 §6 脚本):锚点用**候选链**,两视图+放大态各取当前可见者;全不可见 → 跳过该步
  // (绝不 0×0 钉左上角)。修 UI 自查 6 BLOCKER(对话折叠列/放大态藏聊天/桌面侧栏与最小化)。
  function _tourSteps() {
    const CAND = {
      1: ["#pulse-text"],                                                          // 脉搏(顶栏,三视图都在)
      2: ["#chat-input", "#chat-open", "#mobile-chat-open"],                        // 说话:输入框→打开聊天入口
      3: ['.nav-item[data-panel="domains"]', '#desk-dock .dock-item[data-panel="domains"]'], // 建域:侧栏→dock
      4: ["#chat-input", "#chat-open", "#mobile-chat-open"],                        // @角色 交活
      5: ["#h2a-list", ".col-decide .col-head", ".col-decide"],                    // 拍板列:内容→列头→便签本体
      6: ["#token-meter"],                                                         // 成本+结晶(顶栏)
    };
    const steps = [];
    for (let n = 1; n <= 6; n++) {
      const anchor = _tourAnchor(CAND[n]);
      if (!anchor) continue;   // 三视图下都不可见 → 跳过,不制造错乱
      steps.push({ element: anchor, popover: {
        title: t("tour.s" + n + ".title"), description: t("tour.s" + n + ".desc") } });
    }
    return steps;
  }
  function startTour(force) {
    if (!force) {
      try { if (localStorage.getItem(_TOUR_DONE_KEY)) return; } catch (e) {}
      // 无 Key 强制引导(must_setup 锁)开着 → 不抢戏;配好 key 重载后 tour 自然起
      const mgmt = document.getElementById("mgmt-modal");
      if (mgmt && !mgmt.classList.contains("hidden")) return;
    }
    _ensureDriverJs().then(() => {
      // "看过"标记在**弹出即写**:driver.js 的 onDestroyed 要等入场动画提交后才回调,
      // 弹出 0.4s 内按 ESC 会静默不触发 → 标记不落、每次访问重弹(真浏览器烟测抓到的竞态)。
      // 顶栏 💡 随时可重看,所以提前写零代价。
      try { localStorage.setItem(_TOUR_DONE_KEY, "1"); } catch (e) {}
      _spotlightDismiss();   // 旅程聚光正开着 → 先撤,不叠两层蒙版
      _prepForTour();
      const steps = _tourSteps();
      if (!steps.length) return;   // 极端:一步锚都不可见 → 不启动空引导(driver 空 steps 会炸)
      const drv = window.driver.js.driver(Object.assign(_spotCfg(), {
        showProgress: true,
        progressText: "{{current}} / {{total}}",
        nextBtnText: t("tour.next"), prevBtnText: t("tour.prev"), doneBtnText: t("tour.done"),
        steps: steps,
      }));
      drv.drive();
    }).catch((e) => console.warn("[tour] driver.js unavailable", e));
  }

  // —— 空态行动链接(docs/46 §6):空态不只解释"为什么空",还给下一步动作 ——
  function _emptyAction(labelKey, onClick) {
    return el("button", { class: "empty-act", text: t(labelKey), onClick: onClick });
  }
  // index.html 静态空态占位(h2a / predict)开机补挂行动链接(有卡时会被 _stripEmpty 一并撤走)
  function _decorateStaticEmpties() {
    const h2aEmpty = document.querySelector("#h2a-list .h2a-empty");
    if (h2aEmpty) h2aEmpty.appendChild(_emptyAction("empty.h2a_act", requestProposal));
    const predictEmpty = document.querySelector("#predict-list .empty-state");
    if (predictEmpty) predictEmpty.appendChild(_emptyAction("empty.predict_act", requestProposal));
  }

  // —— 输入框 placeholder 轮换真实示例(域模板 seed_intents 的前端静态表,i18n en/zh)——
  const _PH_EXAMPLES = 5;
  let _phIdx = 0;
  function _rotatePlaceholder() {
    const ce = document.getElementById("chat-input");
    if (!ce) return;
    if (document.activeElement === ce) return;   // 正在打字/聚焦不换,不闪人
    _phIdx = (_phIdx % _PH_EXAMPLES) + 1;
    ce.setAttribute("data-placeholder", t("input.ex" + _phIdx));
  }
  function _startPlaceholderRotation() { setInterval(_rotatePlaceholder, 8000); }

  // ============ 「第一个 10 分钟」新手旅程(装完 10 分钟亲眼看到飞轮)============
  // 剧本:第一步跑一个真演示任务(样例 CSV 附件 + data-analyst 方法召回)→ 第二步再跑一次
  // 同类任务 → 聊天里出现**方法复用回执**(payload.skill_name 来自后端真 recall 命中)→
  // 指给用户看成长曲线上的第一批数据点(/api/skills/curve,真数据才指)。
  // 诚实红线:任务真跑用户配置的模型,绝无罐头输出;没配模型如实引导先配。
  // 薄状态机:fresh → step1(任务1已发)→ step2(任务2已发)→ done / skipped,后端持久化。
  let _journey = null;      // GET /api/onboarding/journey 的 {stage, llm_ready, tasks} | null
  let _journeyAwait = 0;    // 1/2 = 旅程第 n 个任务已发、在等 drive_done;0 = 没在等

  function _journeyActive() {
    return !!(_journey && ["fresh", "step1", "step2"].indexOf(_journey.stage) >= 0);
  }
  async function _initJourney() {
    const j = await _getJSON("/api/onboarding/journey");
    if (!j || !j.stage) return false;
    const bar = document.getElementById("journey-bar");
    const mounted = !!(bar && !bar.classList.contains("hidden"));   // 旅程条已在场?
    const changed = !_journey || _journey.stage !== j.stage ||
      !!_journey.llm_ready !== !!j.llm_ready;
    _journey = j;
    if (!mounted || changed) _renderJourneyBar();   // 轮询重入且无变化:连重渲都省(不打断悬停/点击)
    if (_journeyActive()) {
      if (!mounted) {
        // docs/59 方案A:旅程永远落在对话视图(桌面/放大偏好不许截胡第一个 10 分钟;
        // 只回放视图不改写 karvyloop_view 偏好,旅程结束后下次开机仍回用户自己的家)。
        // **只在首次挂载旅程条时回放**:未配模型态的 15s 轮询重入绝不再拽视图/弹层/
        // 抢焦点 —— 用户切去 🖥 桌面或 ⛶ 放大后不被循环拉回(2026-07-04 独立验收 W2)。
        if (document.body.classList.contains("desk-view") || _boardZoomed()) _applyView("chat");
        openChatModal();   // 旅程活跃 → 聊天当主场(对话视图本就常驻;移动端弹起)
      }
      // 还没配模型:轮询等它配好(配好那刻 CTA 自动换成第一步 chip,不用刷新页面;只刷状态不动视图)
      if (!_journey.llm_ready) setTimeout(_initJourney, 15000);
    }
    return _journeyActive();
  }
  function _journeySetStage(stage) {
    if (_journey) _journey.stage = stage;
    try { _postJSON("/api/onboarding/journey", { stage: stage }); } catch (e) {}
    _renderJourneyBar();
  }
  function _journeyTaskText(n) {
    const lang = (T.getLang && T.getLang() === "zh") ? "zh" : "en";
    const tasks = (_journey && _journey.tasks && _journey.tasks[lang]) || {};
    return tasks["task" + n] || "";
  }
  function _journeyChip(labelKey, onClick) {
    // 点行动 chip 先撤聚光蒙版(接下来要弹面板/切视图,蒙版留着会把它们罩黑)
    return el("button", { class: "journey-chip", text: t(labelKey),
      onClick: () => { _spotlightDismiss(); onClick(); } });
  }
  // 旅程引导时刻聚光:一个时刻只聚一次(15s 轮询/重渲**绝不重弹蒙版**——W2 纪律:
  // 轮询重入不许拽视图/抢焦点,蒙版同罪)。Esc / 点蒙版即撤,撤了不追。
  let _journeySpotKey = "";
  function _journeySpotMoment(key) {
    if (_journeySpotKey === key) return;
    _journeySpotKey = key;
    _spotlightEl(document.getElementById("journey-bar"));
  }
  function _renderJourneyBar() {
    const bar = document.getElementById("journey-bar");
    if (!bar) return;
    const active = _journeyActive();
    bar.innerHTML = "";
    bar.classList.toggle("hidden", !active);
    if (!active) { _spotlightDismiss(); _journeySpotKey = ""; return; }
    bar.appendChild(el("div", { class: "journey-head" },
      el("span", { class: "journey-title", text: t("journey.title") }),
      el("button", { class: "journey-skip", text: t("journey.skip"),
        onClick: () => { _journeyAwait = 0; _journeySetStage("skipped"); } })));
    if (!_journey.llm_ready) {
      // 诚实引导:没配模型演示跑不了 → 先配模型(不演假戏)。不聚光:强制配 Key
      // 弹窗(checkSetupGate)才是这一态的主角,旅程条只是回声。
      bar.appendChild(el("div", { class: "journey-desc", text: t("journey.need_model") }));
      bar.appendChild(_journeyChip("journey.model_cta",
        () => _openLazyPanel("models", () => window.KarvyModelsPanel.open())));
      return;
    }
    if (_journeyAwait) {
      bar.appendChild(el("div", { class: "journey-desc",
        text: t(_journeyAwait === 1 ? "journey.running1" : "journey.running2") }));
      _spotlightDismiss();   // 任务跑着,无需行动 → 不拿蒙版罩人
      return;
    }
    if (_journey.stage === "fresh") {
      // 人格采集器(第一个 chip 前):4 问种下第一批决策标准。可跳过可重来(旅程重看一致)。
      if (_journey.intake && _journey.intake.questions &&
          _journey.intake.questions.length && !_journey.intake.done) {
        _renderIntake(bar);
        _journeySpotMoment("intake");   // 引导时刻⓪:采集器(蒙版聚光,同 chip 待遇)
        return;
      }
      bar.appendChild(el("div", { class: "journey-desc", text: t("journey.desc") }));
      bar.appendChild(_journeyChip("journey.chip1", () => _journeyRunTask(1)));
      _journeySpotMoment("chip1");   // 引导时刻①:跑第一个演示任务(蒙版聚光,躲不开)
    } else {   // step1(任务1回来了)/ step2(重进来:任务2没跑完)→ 都给第二步 chip
      bar.appendChild(el("div", { class: "journey-desc", text: t("journey.step2_hint") }));
      bar.appendChild(_journeyChip("journey.chip2", () => _journeyRunTask(2)));
      _journeySpotMoment("chip2");   // 引导时刻②:再跑一次同类 → 亲眼看方法复用
    }
  }
  // ============ 人格采集器(旅程开头、第一个 chip 前的 4 问)============
  // 每个答案 = 一条决策偏好种子(explicit/confirmed;POST /api/onboarding/intake 真种进
  // 认知库 → 落盘 beliefs.json,prealign/违背即拦立即认它)。跳过 = 零种子,不惩罚。
  // 文案纪律:回执说"记下你的标准、拍板时摆你手边"(预对齐),**绝不说"我懂你了"**。
  let _intakeIdx = 0;
  const _intakeAnswers = {};
  function _intakeQs() {
    return (_journey && _journey.intake && _journey.intake.questions) || [];
  }
  function _intakeLoc(d) {
    const lang = (T.getLang && T.getLang() === "zh") ? "zh" : "en";
    return (d && (d[lang] || d.en)) || "";
  }
  function _renderIntake(bar) {
    const qs = _intakeQs();
    if (!qs.length || _intakeIdx >= qs.length) { _intakeSubmit(); return; }
    const q = qs[_intakeIdx];
    bar.appendChild(el("div", { class: "journey-desc", text: t("intake.lead") }));
    const box = el("div", { class: "intake-q" });
    box.appendChild(el("div", { class: "intake-progress",
      text: t("intake.progress", { i: _intakeIdx + 1, n: qs.length }) }));
    box.appendChild(el("div", { class: "intake-question", text: _intakeLoc(q.question) }));
    const opts = el("div", { class: "intake-options" });
    (q.options || []).forEach((o) => {
      opts.appendChild(el("button", { class: "journey-chip intake-opt", text: _intakeLoc(o.label),
        onClick: () => { _intakeAnswers[q.id] = o.id; _intakeNext(); } }));
    });
    box.appendChild(opts);
    const skips = el("div", { class: "intake-skips" });
    skips.appendChild(el("button", { class: "intake-skip", text: t("intake.skip_q"),
      onClick: () => { delete _intakeAnswers[q.id]; _intakeNext(); } }));
    skips.appendChild(el("button", { class: "intake-skip", text: t("intake.skip_all"),
      onClick: () => { _intakeIdx = qs.length; _intakeSubmit(); } }));
    box.appendChild(skips);
    bar.appendChild(box);
  }
  function _intakeNext() {
    _intakeIdx += 1;
    if (_intakeIdx >= _intakeQs().length) { _intakeSubmit(); return; }
    _renderJourneyBar();
  }
  let _intakePosting = false;
  async function _intakeSubmit() {
    if (_intakePosting || !_journey || !_journey.intake || _journey.intake.done) return;
    _intakePosting = true;
    try {
      const r = await _postJSON("/api/onboarding/intake", { answers: _intakeAnswers });
      if (!r.ok) {   // 诚实:没种上就不说记下了;答案还在,重渲染可重试
        pushChatLine("system", "⚠ " + t("intake.fail"));
        _intakeIdx = 0;
        return;
      }
      const n = (r.data && r.data.seeded_n) || 0;
      pushChatLine("system", n > 0 ? t("intake.receipt", { n: n }) : t("intake.receipt_skip"));
      // 后端断⑥ fail-loud:种进内存但没落盘 → 别只说"记下了",诚实告知重启会丢
      if (r.data && r.data.persist_error) pushChatLine("system", t("intake.persist_warn"));
      _journey.intake.done = true;   // 本地即时收起(权威状态已落后端)
      _journeySpotKey = "";          // 采集完毕 → 下一时刻(chip1)重新聚光
    } finally {
      _intakePosting = false;
      _renderJourneyBar();
    }
  }

  async function _journeyRunTask(n) {
    if (_journeyAwait) return;
    const taskText = _journeyTaskText(n);
    if (!taskText) return;
    const s = await _getJSON("/api/onboarding/sample");
    if (!s || !s.ok) { pushChatLine("system", "⚠ " + t("journey.sample_missing")); return; }
    // 走**真实附件路径**(与人手动 📎 完全同路):文本附件内联进 prompt,真模型真跑
    _attachments = [{ kind: "text", name: s.name, text: s.text }];
    _renderAttachments();
    const ce = _ceInput();
    if (ce) { ce.textContent = taskText; _ceUpdateEmpty(); }
    _journeyAwait = n;
    _journeySetStage(n === 1 ? "step1" : "step2");
    await _submitChat();
  }
  function _journeyOnDriveDone(payload) {
    if (!_journeyAwait || !_journeyActive()) return;
    const step = _journeyAwait;
    _journeyAwait = 0;
    if (payload && payload.error) { _renderJourneyBar(); return; }   // 失败:留在原步,可重试
    if (step === 1) { _renderJourneyBar(); return; }                 // 任务1回来 → 亮出第二步
    _journeyFinale(payload);                                          // 任务2回来 → 收官
  }
  async function _journeyFinale(payload) {
    _journeySetStage("done");
    const reused = !!(payload && payload.skill_name);
    // 诚实核数:去 /api/skills/curve 真查一眼,曲线上真有点才指给用户看
    let hasPoint = false;
    try {
      const c = await _getJSON("/api/skills/curve");
      hasPoint = !!((c && c.skills) || []).some((s) => s.points && s.points.length);
    } catch (e) {}
    const receipt = pushChatLine("system", t(reused ? "journey.done_receipt" : "journey.done_noreuse"));
    if (hasPoint) {
      const log = document.getElementById("chat-log");
      if (log) {
        const notice = el("div", { class: "chat-notice journey-finale" });
        notice.appendChild(document.createTextNode(t("journey.done_curve") + " "));
        notice.appendChild(_journeyChip("journey.curve_btn", () => {
          const nav = document.querySelector('.nav-item[data-panel="skills"]');
          if (nav) nav.click();
        }));
        log.appendChild(notice);
        log.scrollTop = log.scrollHeight;
      }
    }
    pushChatLine("system", t("journey.tagline"));
    // docs/59 方案A:桌面视图不再是冷启动三选一,由旅程收官当"奖励时刻"介绍
    // (🖥 去看看你的团队上班;≤720px 无桌面隐喻 → 不提)。
    // 用 _applyView 只应用**不写偏好**:点一次奖励时刻不把开机视图持久换成 desk ——
    // "家"永远是对话,想常住桌面由顶栏 🖥 档位(_setView)明确表态(2026-07-04 W2 顺修)。
    if (!_isMobile()) {
      const log = document.getElementById("chat-log");
      if (log) {
        const deskNotice = el("div", { class: "chat-notice journey-desk" });
        deskNotice.appendChild(document.createTextNode(t("journey.desk_moment") + " "));
        deskNotice.appendChild(_journeyChip("journey.desk_btn", () => _applyView("desk")));
        log.appendChild(deskNotice);
        log.scrollTop = log.scrollHeight;
      }
    }
    // 收官 next-steps:解锁更多能力(MCP 工具/推送渠道/附件解析…)—— 不配置就降级的
    // 可选能力,旅程结束顺手给一条"去解锁"的路(Hardy 2026-07-04:你不引导,用户
    // 就真的不知道有这个配置)。面板脚本没装上则优雅缺席,不给死按钮。
    if (window.KarvyUnlockPanel) {
      const log = document.getElementById("chat-log");
      if (log) {
        const unlockNotice = el("div", { class: "chat-notice journey-unlock" });
        unlockNotice.appendChild(document.createTextNode(t("journey.unlock_moment") + " "));
        unlockNotice.appendChild(_journeyChip("journey.unlock_btn", () => window.KarvyUnlockPanel.open()));
        log.appendChild(unlockNotice);
        log.scrollTop = log.scrollHeight;
      }
    }
    // 引导时刻③:方法复用回执 = 10 分钟 wow 的主菜,同一套蒙版聚光。
    // 诚实红线:真 recall 命中(reused)才聚 —— 没命中不拿蒙版庆祝空气。
    if (reused && receipt) _spotlightEl(receipt);
  }

  // ============ 文件管家第一课(引荐 ACCEPT → 第一任务 chip → 方案预览卡)============
  // 只有本地运行时能做的 wow:扫你**真实**的桌面/下载(只读,白名单内)→ H2A 方案预览卡
  // (你拍板才动手;"只看看不动"= REJECT,同样合法)。聚光蒙版待遇(旅程时刻同款)。
  function _butlerOfferFirstLesson() {
    const log = document.getElementById("chat-log");
    if (!log || document.getElementById("butler-lesson-offer")) return;   // 幂等,不重复递
    const notice = el("div", { class: "chat-notice butler-lesson", id: "butler-lesson-offer" });
    notice.appendChild(document.createTextNode(t("butler.lesson_offer") + " "));
    notice.appendChild(el("button", { class: "journey-chip", text: t("butler.lesson_chip"),
      onClick: () => { _spotlightDismiss(); _butlerRunFirstLesson(); } }));
    log.appendChild(notice);
    log.scrollTop = log.scrollHeight;
    _spotlightEl(notice);   // 入住后的第一课入口别被错过(Esc/点蒙版即撤,撤了不追)
  }
  let _butlerLessonBusy = false;
  async function _butlerRunFirstLesson() {
    if (_butlerLessonBusy) return;
    _butlerLessonBusy = true;
    try {
      const r = await _postJSON("/api/butler/first_lesson", {});
      if (!r.ok) {
        pushChatLine("system", "⚠ " + t("butler.lesson_fail") +
          (r.data && r.data.reason ? " (" + r.data.reason + ")" : ""));
        return;
      }
      if (r.data && r.data.empty) {   // 空桌面/空下载:诚实说没啥可整理 + 替代建议,不硬凑
        pushChatLine("system", t("butler.lesson_empty"));
        return;
      }
      // 方案卡已经 WS h2a_proposal 广播进聊天流/决策列 → 对聊天流里的那张卡聚光
      const pid = r.data && r.data.proposal_id;
      if (pid) {
        setTimeout(() => {
          const cardLine = document.querySelector('#chat-log [data-proposal-id="' + pid + '"]');
          if (cardLine) _spotlightEl(cardLine);
        }, 350);
      }
    } finally {
      _butlerLessonBusy = false;
    }
  }

  // ============ 语音输入 v1(Hardy ⑪)============
  // 浏览器 Web Speech API(Chrome/Edge = webkitSpeechRecognition)。零后端改动。纪律:
  // - 识别结果只填输入框,**绝不自动发送** —— 你按 Enter/发送才发(人拍板);
  // - 不支持的浏览器按钮保持隐藏(能力探测);识别错误诚实提示,不装聋;
  // - 浏览器限制:需 https 或 localhost(安全上下文)+ 麦克风授权;本地 console(127.0.0.1)天然满足。
  //   Chrome 的转写引擎走浏览器自带云服务 —— 那是浏览器行为,语音不经 KarvyLoop 后端。
  function _SRCls() { return window.SpeechRecognition || window.webkitSpeechRecognition; }
  function _voiceLang() { return T.getLang() === "zh" ? "zh-CN" : "en-US"; }

  let _dictRec = null;      // 听写会话(非 null = 正在听)
  let _dictNode = null;     // 输入框里承接转写的文本节点(interim 原位刷新,不碰 @chip)
  let _wakeRec = null;      // 唤醒监听会话(实验开关开着才活)

  function _voiceBtn() { return document.getElementById("chat-voice-btn"); }
  function _voiceSetRecording(on) {
    const b = _voiceBtn();
    if (!b) return;
    b.classList.toggle("recording", !!on);
    b.title = t(on ? "voice.stop.title" : "voice.btn.title");
  }
  // 识别错误 → 人话(诚实提示,系统淡条,不冒充小卡说话)
  function _voiceErrText(code) {
    if (code === "not-allowed" || code === "service-not-allowed") return t("voice.err_mic");
    if (code === "no-speech") return t("voice.err_nospeech");
    if (code === "network") return t("voice.err_network");
    return t("voice.err", { err: code || "?" });
  }

  // 听写:interim 实时填输入框(独立文本节点原位刷新,保住已敲文字和 @chip),final 落定。
  function _dictStart() {
    const SR = _SRCls();
    const ce = _ceInput();
    if (!SR || !ce || _dictRec) return;
    const rec = new SR();
    rec.lang = _voiceLang();
    rec.interimResults = true;
    rec.continuous = false;      // 一段话一次;说完自然收(要继续再按一次)
    let finalText = "";
    _dictNode = document.createTextNode("");
    ce.appendChild(_dictNode);
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (_dictNode) _dictNode.textContent = finalText + interim;
      _ceUpdateEmpty();
    };
    rec.onerror = (ev) => {
      // aborted = 我们自己 stop 的,不值当喊
      if (ev.error !== "aborted") pushChatLine("system", "🎤 " + _voiceErrText(ev.error));
    };
    rec.onend = () => {
      _dictRec = null;
      _dictNode = null;
      _voiceSetRecording(false);
      _ceUpdateEmpty();
      if (ce) ce.focus();        // 光标回输入框:落定文本你随手改,按 Enter 才发(人拍板)
      _wakeApply();              // 唤醒开着 → 听写结束回去继续守唤醒词
    };
    _dictRec = rec;
    _voiceSetRecording(true);
    _wakeStop();                 // 同一麦克风:听写期间挂起唤醒监听,结束再恢复
    try { rec.start(); } catch (e) { _dictRec = null; _voiceSetRecording(false); }
  }
  function _dictStop() {
    if (_dictRec) { try { _dictRec.stop(); } catch (e) {} }
  }

  // —— 免按键唤醒(实验特性,默认关;开关在「模型(全局)」面板,localStorage)——
  // 唤醒词命中判定抽成独立函数:未来外设(AI 眼镜/耳机等)接入时,外设侧的转写流走**同一入口**
  // (把 transcript 喂给 _wakeHit,命中即进入听写模式),不必重写浏览器这一套。
  function _wakeHit(transcript) {
    const s = String(transcript || "").toLowerCase();
    return s.indexOf("小卡") !== -1 || s.indexOf("karvy") !== -1;
  }
  function _wakeEnabled() {
    try { return localStorage.getItem("karvyloop_voice_wake") === "1"; } catch (e) { return false; }
  }
  function _wakeStop() {
    if (_wakeRec) { const r = _wakeRec; _wakeRec = null; try { r.stop(); } catch (e) {} }
  }
  function _wakeStart() {
    const SR = _SRCls();
    if (!SR || _wakeRec || _dictRec || !_wakeEnabled()) return;
    const rec = new SR();
    rec.lang = _voiceLang();
    rec.interimResults = false;
    rec.continuous = true;       // 持续监听唤醒词(浏览器会周期性掐会话,onend 里重拉)
    rec.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal && _wakeHit(ev.results[i][0].transcript)) {
          _wakeStop();
          _talkToKarvy();        // 命中「小卡/Karvy」→ 切到小卡私聊 + 进听写模式
          setTimeout(_dictStart, 150);
          return;
        }
      }
    };
    rec.onerror = (ev) => {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
        _wakeRec = null;         // 没麦克风授权:诚实提示一次,不无限重试骚扰
        pushChatLine("system", "🎤 " + t("voice.err_mic"));
      }
    };
    rec.onend = () => {
      if (_wakeRec === rec) {    // 不是我们主动停的(浏览器掐了)→ 开关还开着就重拉
        _wakeRec = null;
        if (_wakeEnabled() && !_dictRec) setTimeout(_wakeStart, 400);
      }
    };
    _wakeRec = rec;
    try { rec.start(); } catch (e) { _wakeRec = null; }
  }
  function _wakeApply() { if (_wakeEnabled()) _wakeStart(); else _wakeStop(); }

  function setupVoiceInput() {
    const btn = _voiceBtn();
    if (!btn || !_SRCls()) return;         // 能力探测:不支持 → 按钮保持隐藏
    btn.classList.remove("hidden");
    btn.title = t("voice.btn.title");
    btn.addEventListener("click", () => { if (_dictRec) _dictStop(); else _dictStart(); });
    _wakeApply();                          // 实验开关开着 → 开始守唤醒词
    // 设置面板(models_panel)切开关后立即生效的回调口;外设接入也从这里走 wakeHit
    window.KarvyVoice = { applyWakeSetting: _wakeApply, wakeHit: _wakeHit };
  }

  // ============ Boot ============

  function boot() {
    // 9.4 i18n:先把静态文案填成当前语言 + 挂语言切换器(默认 en)
    T.applyStatic();
    T.mountSwitcher(document.getElementById("lang-switcher"));
    setupChatForm();
    setupVoiceInput();  // 语音输入 v1:🎤 听写 + 免按键唤醒(实验开关)
    setupChatModal();   // step5:对话弹窗
    // docs/46 S1 + docs/59 方案A:视图初始化(默认对话视图,记住上次选择,只有 chat|desk 两个家)
    // 存量 karvyloop_view=board 平滑迁移回 chat:老用户开机不落进一个已退位的视图(放大态一键 ⛶ 即达)。
    let _viewPref = "chat";
    try { _viewPref = localStorage.getItem("karvyloop_view") || "chat"; } catch (e) {}
    if (_viewPref !== "chat" && _viewPref !== "desk") {
      _viewPref = "chat";
      try { localStorage.setItem("karvyloop_view", "chat"); } catch (e) {}
    }
    _applyView(_viewPref);
    const optChat = document.getElementById("view-opt-chat");
    const optDesk = document.getElementById("view-opt-desk");
    if (optChat) optChat.addEventListener("click", () => _setView("chat"));
    if (optDesk) optDesk.addEventListener("click", () => _setView("desk"));
    // 主题切换:light(晨绿)默认;防闪预应用在 index.html <head>(CSS 前),这里只管点按轮换+记忆
    const themeBtn = document.getElementById("theme-toggle");
    if (themeBtn) themeBtn.addEventListener("click", () => {
      const root = document.documentElement;
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("karvyloop_theme", next); } catch (e) {}
    });
    // T2 轻量模式(docs/83):body.lite 降毛玻璃/大阴影/常驻动画(降级块在 styles.css/desktop.css 末尾)。
    // 防闪预应用在 index.html <body> 内联脚本;这里只管点按开关+记忆+按钮按下态。
    const liteBtn = document.getElementById("lite-toggle");
    function _syncLiteBtn() {
      if (!liteBtn) return;
      liteBtn.setAttribute("aria-pressed", document.body.classList.contains("lite") ? "true" : "false");
    }
    _syncLiteBtn();   // 开机态(内联脚本可能已挂 body.lite)同步到按钮
    if (liteBtn) liteBtn.addEventListener("click", () => {
      const on = document.body.classList.toggle("lite");
      _syncLiteBtn();
      try { localStorage.setItem("karvyloop_lite", on ? "1" : "0"); } catch (e) {}
    });
    // rail ⛶:四象限临时放大(点 ⛶/✕ 或 Esc 回对话;临时态不写 karvyloop_view)
    const zoomBtn = document.getElementById("rail-zoom-btn");
    if (zoomBtn) zoomBtn.addEventListener("click", () => _setBoardZoom(!_boardZoomed()));
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || e.defaultPrevented || !_boardZoomed()) return;   // defaultPrevented:@提及下拉的 Esc 归它
      const mgmt = document.getElementById("mgmt-modal");
      if (mgmt && !mgmt.classList.contains("hidden")) return;                    // 管理面开着:Esc 归它
      const chat = document.getElementById("chat-modal");
      if (chat && !chat.classList.contains("hidden") && !_chatDocked()) { closeChatModal(); return; }  // 先收聊天弹层
      _setBoardZoom(false);   // Esc 回家:回对话视图
    });
    const mobileChat = document.getElementById("mobile-chat-open");
    if (mobileChat) mobileChat.addEventListener("click", openChatModal);
    _setupRailCollapse();
    // 跨过 720px 断点(旋转/拖窗):按偏好重放当前视图,保证聊天 docked/弹层形态一致
    // (桌面偏好保留:窄屏降级 chat,回到宽屏自动还原 desk —— docs/51 §3.1;放大态是临时形态,跨断点即收)
    if (window.matchMedia) {
      const mq = window.matchMedia("(max-width: 720px)");
      const onMq = () => {
        let v = "chat";
        try { v = localStorage.getItem("karvyloop_view") || "chat"; } catch (e) {}
        _applyView(v === "desk" ? "desk" : "chat");
      };
      if (mq.addEventListener) mq.addEventListener("change", onMq);
      else if (mq.addListener) mq.addListener(onMq);
    }
    // 副驾按钮撤下(Hardy 2026-07-03)后,冒泡只在桌面视图活着:CSS 把 #karvy-bubble
    // 重定位到右下卡皮巴拉头上(docs/51),show() 里有 desk-view 守卫,其它视图静默。
    _startKarvyIdleBubble();
    connectWS();
    startPolling();
    // 9.0e:绑"看建议"按钮
    const proposeBtn = document.getElementById("propose-btn");
    if (proposeBtn) proposeBtn.addEventListener("click", requestProposal);
    // predict(你可能想做)手动刷新:现在就问一次(WS propose,回退 POST /api/propose)
    const predictRefreshBtn = document.getElementById("predict-refresh-btn");
    if (predictRefreshBtn) predictRefreshBtn.addEventListener("click", requestProposal);
    // ch4 #4:点钱包 → token 统计弹窗(T4:脚本按需注入;通常 boot ensure 早就载好了)
    const tokMeter = document.getElementById("token-meter");
    if (tokMeter) tokMeter.addEventListener("click", () => _openLazyPanel("tokens", () => window.KarvyTokens.open()));
    // T4:💰 meter 是首屏常驻仪表(startPolling 每 2s 刷)—— tokens 脚本 boot 即 ensure,
    // 只把它挪出首屏解析热路径,不改"成本常驻可见"的产品行为;失败只降级 meter,不弹窗打扰。
    _ensurePanelScript("tokens").then(() => window.KarvyTokens.pollMeter())
      .catch((e) => console.warn("[panel] tokens meter unavailable", e));
    // T4:👀 demo 入口原由 demo_panel.js 载入时自绑 —— 懒加载后第一击由这里接:载脚本 + 开面板;
    // 脚本载入后它自绑的 click 接手,这里看到全局已在就让位(避免一次点击双开)。
    const demoBtn = document.getElementById("demo-open");
    if (demoBtn) demoBtn.addEventListener("click", () => {
      if (window.KarvyDemoPanel) return;   // 已载:demo_panel 自己的监听器负责
      _openLazyPanel("demo", () => window.KarvyDemoPanel.open());
    });
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
    setupNavFold();   // docs/90 刀2:三组可折叠(默认引擎室收起,localStorage 记选择)
    _wireSkillLifelineEntries();   // 技能库每张技能卡挂「🧬 生命线」入口(观察面板渲染补挂)
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
    fetchPendingResume();      // #54 逃生门:重启后中断的流程 → 顶部横幅让人续跑/丢弃
    // 无 Key → 强制引导录入模型(进系统就判)。T4:models 脚本懒加载,boot ensure 后再判 gate
    // (异步注入不卡首绘;载入失败会人话报错 —— gate 是安全门,不许静默跳过)
    _openLazyPanel("models", () => window.KarvyModelsPanel.checkSetupGate({ pollSnapshot }));
    // docs/46 S4:新手引导 —— 重看入口 + 空态行动链接 + placeholder 轮换 + 首启 tour
    const tourBtn = document.getElementById("tour-replay");
    if (tourBtn) tourBtn.addEventListener("click", () => startTour(true));
    // 「第一个 10 分钟」旅程重看入口(可重入:重置成 fresh 再拉状态)
    const journeyBtn = document.getElementById("journey-replay");
    if (journeyBtn) journeyBtn.addEventListener("click", async () => {
      _journeyAwait = 0;
      // 重看必须把采集器状态清回起点:后端 stage=fresh 会重置 intake.done,但前端 _intakeIdx
      // 仍停在上轮末尾(=qs.length),否则 _renderIntake 短路 → 拿旧答案静默重播种、4 问不再露面。
      _intakeIdx = 0;
      Object.keys(_intakeAnswers).forEach((k) => delete _intakeAnswers[k]);
      await _postJSON("/api/onboarding/journey", { stage: "fresh" });
      await _initJourney();
      // _initJourney 的"无变化不重渲"守卫看 stage(fresh→fresh 未变)会跳过重渲,但 intake.done
      // 刚从 true 翻回 false —— 必须强制重渲一次,否则采集器 4 问不再露面(BREAK #9 的第二半)。
      _renderJourneyBar();
      openChatModal();
    });
    _decorateStaticEmpties();
    _startPlaceholderRotation();
    // 首启:先看「第一个 10 分钟」旅程是否活跃 —— 活跃就让旅程当主角(tour 随时可从 💡 重看);
    // 不活跃(老用户/已完成/已跳过)才自动起 6 步 tour(localStorage 无 tour_done 时)。
    _initJourney().then((active) => {
      if (!active) setTimeout(() => startTour(false), 1600);
    }).catch(() => setTimeout(() => startTour(false), 1600));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
