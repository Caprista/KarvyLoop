/* desktop.ts — 桌面视图(对话之外唯一可切的第二形态,docs/51 P1 骨架 + docs/59 方案A)。
 *
 * body.desk-view 下,console 变成一张桌面:
 *   - dock(底部,兼任务栏):左段 = 左导航 12 入口的同构复用(dock 图标就是
 *     `.nav-item[data-panel]` 按钮,setupMgmtPanels 的绑定原样命中,零新增打开路径);
 *     右段 = 运行中/最小化窗口指示 + ↺重置布局。
 *   - 四象限 → 桌面便签:同一批 DOM(#h2a-list/#task-board… id 全在原处,轮询/WS
 *     渲染零改动),变绝对定位、可拖、可折叠(折叠复用既有 rail 逻辑与 localStorage key)。
 *   - 聊天 = 特殊窗:默认开、✕=最小化(收进 dock,点卡皮巴拉恢复),关不掉。
 *   - mgmt 模态 → 可拖单例窗(12 panel 仍写死 #mgmt-body,多窗是 P3)。
 *   - ⚖便签不许最小化;新决策卡到达 → 置顶+闪烁+卡皮巴拉冒泡(notifyH2A)。
 *
 * 拖拽自写 pointer events(业界桌面隐喻常见做法:标题栏拖拽、点击聚焦置顶、
 * taskbar 指示),零新依赖;位置持久化 localStorage("karvyloop_desk.v1")。
 * 对外契约:window.KarvyDesktop = { enter, leave, notifyH2A, resetLayout }。
 *
 * P1.5 灵魂(docs/53):卡皮巴拉工位区 + 小卡壁炉化/叼卡 + 署名便签 + 工作证摊桌。
 *   - 全部渲染只由真实事件驱动(task_status/task_step/role_presence/h2a_*),没有一帧假戏
 *     (§0 红线);数据源 = GET /api/roles/presence(契约冻结,调不通则工位栏优雅隐藏)
 *     + 本视图自开的一条**只读** WS(不动 app.js:desk 进场才连、离场即断、绝不发消息)。
 *   - 形象来自 ./pixelpet(官方 IP 原图 sprite + CSS 状态动画 + 状态机;手绘像素帧已废)。
 */

import * as PixelPet from "./pixelpet";

interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }

(function () {
  "use strict";

  const LS_KEY = "karvyloop_desk.v1";
  const BASE_Z = 220;          // 桌面元素 z 起点(> .modal-overlay 的 100;< dock 9500 / driver.js 10000+)
  const HANDLE_MIN_W = 48;     // 任何时候标题栏至少 48×32 可见(防拖丢)
  const HANDLE_MIN_H = 32;
  const KEY_STEP = 8;          // 键盘方向键步进(a11y)

  let _zTop = BASE_Z;
  let _entered = false;
  let _wired = false;          // 每元素只 makeDraggable 一次
  let _suppressClick: HTMLElement | null = null;   // 拖完的 click 不当"点头折叠"

  type Pos = { x: number; y: number };
  type Store = {
    notes: Record<string, Pos>;
    windows: { chat?: Pos & { min?: boolean }; mgmt?: Pos };
  };
  let _store: Store = { notes: {}, windows: {} };

  function t(key: string, vars?: Record<string, unknown>): string {
    const i18n = (window as unknown as { KarvyI18n?: I18n }).KarvyI18n;
    return i18n ? i18n.t(key, vars) : key;
  }
  function deskView(): boolean { return document.body.classList.contains("desk-view"); }

  // ---- localStorage(schema 带 .v1;读失败/脏数据一律回默认,try/catch 包死)----
  function loadStore(): Store {
    const out: Store = { notes: {}, windows: {} };
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return out;
      const j = JSON.parse(raw) as Store;
      const num = (v: unknown): v is number => typeof v === "number" && isFinite(v);
      if (j && typeof j === "object") {
        const notes = (j.notes || {}) as Record<string, Pos>;
        Object.keys(notes).forEach((k) => {
          const p = notes[k];
          if (p && num(p.x) && num(p.y)) out.notes[k] = { x: p.x, y: p.y };
        });
        const w = (j.windows || {}) as Store["windows"];
        if (w.chat && num(w.chat.x) && num(w.chat.y)) out.windows.chat = { x: w.chat.x, y: w.chat.y, min: !!w.chat.min };
        if (w.mgmt && num(w.mgmt.x) && num(w.mgmt.y)) out.windows.mgmt = { x: w.mgmt.x, y: w.mgmt.y };
      }
    } catch { /* 脏数据 → 默认布局 */ }
    return out;
  }
  function saveStore(): void {
    try { localStorage.setItem(LS_KEY, JSON.stringify(_store)); } catch { /* 无 localStorage */ }
  }

  // ---- 元素定位(全部走 transform,不触 layout;写入只在 pointerup/键盘落定)----
  function deskEl(): HTMLElement | null { return document.querySelector(".cockpit"); }
  function noteEls(): HTMLElement[] {
    return Array.from(document.querySelectorAll<HTMLElement>(".cockpit-grid .cockpit-col"));
  }
  function noteKey(col: HTMLElement): string {
    return Array.from(col.classList).find((c) => c.indexOf("col-") === 0) || "col";
  }
  function chatOverlay(): HTMLElement | null { return document.getElementById("chat-modal"); }
  function chatPanel(): HTMLElement | null { return document.querySelector("#chat-modal .chat-panel"); }
  function mgmtOverlay(): HTMLElement | null { return document.getElementById("mgmt-modal"); }
  function mgmtPanel(): HTMLElement | null { return document.querySelector("#mgmt-modal .modal"); }

  function getPos(el: HTMLElement): Pos {
    const x = parseFloat(el.dataset.deskX || "0");
    const y = parseFloat(el.dataset.deskY || "0");
    return { x: isFinite(x) ? x : 0, y: isFinite(y) ? y : 0 };
  }
  function applyPos(el: HTMLElement, x: number, y: number): void {
    el.dataset.deskX = String(Math.round(x));
    el.dataset.deskY = String(Math.round(y));
    // rotate 走 CSS 变量(便签轻微贴纸旋转),和拖拽 translate 合成一条 transform
    el.style.transform = "translate3d(" + Math.round(x) + "px," + Math.round(y) + "px,0) rotate(var(--desk-tilt, 0deg))";
  }
  // clamp 到桌面画布内:标题栏至少 48×32 可见;恢复时也走这里(防换分辨率丢窗)
  function clampPos(el: HTMLElement, x: number, y: number): Pos {
    const desk = deskEl();
    const par = el.offsetParent as HTMLElement | null;
    if (!desk || !par) return { x, y };
    const d = desk.getBoundingClientRect();
    const p = par.getBoundingClientRect();
    if (!(d.width > 0) || !(d.height > 0)) return { x, y };   // 无布局环境(测试/隐藏)不 clamp
    const w = el.offsetWidth || HANDLE_MIN_W;
    const minX = d.left - p.left - Math.max(0, w - HANDLE_MIN_W);
    const maxX = Math.max(minX, d.right - p.left - HANDLE_MIN_W);
    const minY = d.top - p.top;                                // 标题栏永不拖出画布上沿
    const maxY = Math.max(minY, d.bottom - p.top - HANDLE_MIN_H);
    return { x: Math.min(Math.max(x, minX), maxX), y: Math.min(Math.max(y, minY), maxY) };
  }

  function persistPos(kind: string, pos: Pos): void {
    if (kind === "chat") {
      _store.windows.chat = { x: pos.x, y: pos.y, min: !!(_store.windows.chat && _store.windows.chat.min) };
    } else if (kind === "mgmt") {
      _store.windows.mgmt = { x: pos.x, y: pos.y };
    } else {
      _store.notes[kind] = { x: pos.x, y: pos.y };
    }
    saveStore();
  }

  // ---- 聚焦置顶:便签与窗口同一个 z 空间(⚖闪烁需要便签能主动置顶)----
  function zTarget(el: HTMLElement): HTMLElement {
    // 聊天/mgmt 的 z 挂在各自 overlay 上(overlay 才是根 stacking context 里的定位者)
    if (el.classList.contains("chat-panel")) return chatOverlay() || el;
    if (el.classList.contains("modal")) return mgmtOverlay() || el;
    return el;
  }
  function focusEl(el: HTMLElement): void {
    _zTop += 1;
    zTarget(el).style.zIndex = String(_zTop);
    document.querySelectorAll(".desk-focused").forEach((n) => n.classList.remove("desk-focused"));
    el.classList.add("desk-focused");
  }

  // ---- 自写拖拽(docs/51 §4.4 规格):pointerdown→setPointerCapture→move rAF 节流→up 落盘 ----
  function makeDraggable(el: HTMLElement, handle: HTMLElement, kind: string): void {
    let dragging = false, moved = false, raf = 0;
    let sx = 0, sy = 0, ox = 0, oy = 0, nx = 0, ny = 0;

    handle.addEventListener("pointerdown", (e: PointerEvent) => {
      if (e.button !== 0) return;
      const tgt = e.target as HTMLElement | null;
      // 标题栏上的按钮/输入控件命中时不启动拖(✕/─/🔮 照常工作)
      if (tgt && tgt.closest && tgt.closest("button, select, input, textarea, a, [contenteditable]")) return;
      dragging = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      const p = getPos(el); ox = p.x; oy = p.y; nx = ox; ny = oy;
      try { if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId); } catch { /* jsdom 等无指针捕获 */ }
      handle.classList.add("dragging");
    });
    handle.addEventListener("pointermove", (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;   // 4px 死区:点头折叠不误伤
      moved = true;
      const c = clampPos(el, ox + dx, oy + dy);
      nx = c.x; ny = c.y;
      if (!raf && typeof requestAnimationFrame === "function") {
        raf = requestAnimationFrame(() => { raf = 0; applyPos(el, nx, ny); });
      } else if (typeof requestAnimationFrame !== "function") {
        applyPos(el, nx, ny);
      }
    });
    const finish = (e: PointerEvent) => {
      if (!dragging) return;
      dragging = false;
      try { if (handle.releasePointerCapture) handle.releasePointerCapture(e.pointerId); } catch { /* 同上 */ }
      handle.classList.remove("dragging");
      if (raf && typeof cancelAnimationFrame === "function") { cancelAnimationFrame(raf); raf = 0; }
      if (moved) {
        applyPos(el, nx, ny);
        persistPos(kind, { x: nx, y: ny });     // 写入节流:pointerup 才写,不在 move 里写
        _suppressClick = el;                     // 真拖过 → 吞掉随后的 click(别触发折叠)
        setTimeout(() => { if (_suppressClick === el) _suppressClick = null; }, 0);
      }
    };
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    el.addEventListener("click", (e) => {
      if (_suppressClick === el) { e.stopPropagation(); e.preventDefault(); _suppressClick = null; }
    }, true);
  }

  // 键盘等价操作(a11y):标题栏聚焦后方向键 8px 步进;Esc = 最小化(窗口;便签不适用)
  function wireHandleKeys(el: HTMLElement, handle: HTMLElement, kind: string): void {
    handle.addEventListener("keydown", (e: KeyboardEvent) => {
      if (!deskView()) return;
      if (e.key === "Escape") {
        if (kind === "chat" || kind === "mgmt") { e.preventDefault(); minimizeWin(kind); }
        return;
      }
      let dx = 0, dy = 0;
      if (e.key === "ArrowLeft") dx = -KEY_STEP;
      else if (e.key === "ArrowRight") dx = KEY_STEP;
      else if (e.key === "ArrowUp") dy = -KEY_STEP;
      else if (e.key === "ArrowDown") dy = KEY_STEP;
      else return;
      e.preventDefault();
      const p = getPos(el);
      const c = clampPos(el, p.x + dx, p.y + dy);
      applyPos(el, c.x, c.y);
      persistPos(kind, c);
      focusEl(el);
    });
  }

  // ---- dock(底部,兼任务栏)----
  function dockEl(): HTMLElement | null { return document.getElementById("desk-dock"); }

  function renderDock(): void {
    const dock = dockEl();
    if (!dock || dock.childElementCount > 0) return;   // 幂等
    // 左段:同构复用左导航入口 —— dock 图标**就是** `.nav-item[data-panel]` 按钮,
    // app.js setupMgmtPanels 的绑定原样命中(它按 selector 全量绑,零新增打开逻辑)。
    document.querySelectorAll<HTMLButtonElement>(".sidebar .nav-item[data-panel]").forEach((src) => {
      if (src.disabled) return;
      const b = document.createElement("button");
      b.className = "dock-item nav-item";
      b.setAttribute("data-panel", src.getAttribute("data-panel") || "");
      const ico = src.querySelector(".nav-ico");
      b.textContent = (ico && ico.textContent) || "▫";
      const lbl = src.querySelector("[data-i18n]:not(.nav-ico)");
      if (lbl) b.setAttribute("data-i18n-tip", lbl.getAttribute("data-i18n") || "");   // tooltip 复用 nav.* key
      dock.appendChild(b);
    });
    // 第 12 位:💰 token 表(代理点顶栏 #token-meter,同一条打开路径)
    const tok = document.createElement("button");
    tok.className = "dock-item dock-tokens";
    tok.textContent = "💰";
    tok.setAttribute("data-i18n-tip", "cockpit.token_title");
    tok.addEventListener("click", () => {
      const m = document.getElementById("token-meter");
      if (m) (m as HTMLElement).click();
    });
    dock.appendChild(tok);
    // 分隔线 + 右段:运行中/最小化窗口指示(P1 最多 chat + mgmt)+ ↺重置布局
    const sep = document.createElement("span");
    sep.className = "dock-sep";
    dock.appendChild(sep);
    const right = document.createElement("span");
    right.className = "dock-wins";
    right.setAttribute("data-i18n-title", "dock.running");
    const chatBtn = document.createElement("button");
    chatBtn.className = "dock-item dock-win";
    chatBtn.id = "desk-dock-win-chat";
    chatBtn.textContent = "💬";
    chatBtn.setAttribute("data-i18n-tip", "chat.title");
    chatBtn.addEventListener("click", () => {
      const ov = chatOverlay();
      if (ov && ov.classList.contains("desk-min")) restoreWin("chat");
      else if (ov) { minimizeWin("chat"); }               // taskbar 语义:开着再点 = 收起
    });
    right.appendChild(chatBtn);
    const mgmtBtn = document.createElement("button");
    mgmtBtn.className = "dock-item dock-win is-off";
    mgmtBtn.id = "desk-dock-win-mgmt";
    mgmtBtn.textContent = "🗂";
    mgmtBtn.addEventListener("click", () => {
      const ov = mgmtOverlay();
      if (ov && ov.classList.contains("desk-min")) restoreWin("mgmt");
      else if (ov && !ov.classList.contains("hidden")) minimizeWin("mgmt");
    });
    right.appendChild(mgmtBtn);
    // 🌗 日/夜壁纸换挡:auto(默认)→ day → night → off 循环;tip 实时显示当前档
    const wall = document.createElement("button");
    wall.className = "dock-item dock-wall";
    wall.id = "desk-wall-btn";
    wall.textContent = "🌗";
    wall.addEventListener("click", () => {
      setWallMode(WALL_MODES[(WALL_MODES.indexOf(wallMode()) + 1) % WALL_MODES.length]);
    });
    right.appendChild(wall);
    const reset = document.createElement("button");
    reset.className = "dock-item dock-reset";
    reset.textContent = "↺";
    reset.setAttribute("data-i18n-tip", "desk.reset");
    reset.addEventListener("click", () => resetLayout());
    right.appendChild(reset);
    dock.appendChild(right);
    // dock 里点了某个面板入口 → 若 mgmt 窗被最小化过,自动恢复 + 置顶 + active 指示
    dock.addEventListener("click", (e) => {
      const tgt = e.target as HTMLElement | null;
      const item = tgt && tgt.closest ? (tgt.closest(".dock-item[data-panel]") as HTMLElement | null) : null;
      if (!item) return;
      dock.querySelectorAll(".dock-item.dock-active").forEach((n) => n.classList.remove("dock-active"));
      item.classList.add("dock-active");
      restoreWin("mgmt");
    });
  }

  function updateDockIndicators(): void {
    const chatBtn = document.getElementById("desk-dock-win-chat");
    const mgmtBtn = document.getElementById("desk-dock-win-mgmt");
    const cOv = chatOverlay(), mOv = mgmtOverlay();
    if (chatBtn && cOv) {
      const min = cOv.classList.contains("desk-min");
      chatBtn.classList.toggle("is-min", min);
      chatBtn.classList.add("is-open");                  // 聊天永在(关不掉,只能最小化)
      chatBtn.title = min ? t("desk.restore") : t("desk.min");
    }
    if (mgmtBtn && mOv) {
      const closed = mOv.classList.contains("hidden");
      const min = mOv.classList.contains("desk-min");
      mgmtBtn.classList.toggle("is-off", closed);
      mgmtBtn.classList.toggle("is-open", !closed);
      mgmtBtn.classList.toggle("is-min", !closed && min);
      const ttl = document.getElementById("mgmt-title");
      mgmtBtn.title = ((ttl && ttl.textContent) || "") + " — " + (min ? t("desk.restore") : t("desk.min"));
      if (closed) {
        const dock = dockEl();
        if (dock) dock.querySelectorAll(".dock-item.dock-active").forEach((n) => n.classList.remove("dock-active"));
      }
    }
  }

  // ---- 最小化 / 恢复(窗口收进 dock;聊天窗额外可点卡皮巴拉恢复)----
  function minimizeWin(kind: string): void {
    const ov = kind === "chat" ? chatOverlay() : mgmtOverlay();
    if (!ov) return;
    ov.classList.add("desk-min");
    if (kind === "chat") {
      const p = chatPanel();
      const pos = p ? getPos(p) : { x: 18, y: 18 };
      _store.windows.chat = { x: pos.x, y: pos.y, min: true };
      saveStore();
    }
    updateDockIndicators();
  }
  function restoreWin(kind: string): void {
    const ov = kind === "chat" ? chatOverlay() : mgmtOverlay();
    if (!ov) return;
    const wasMin = ov.classList.contains("desk-min");
    ov.classList.remove("desk-min");
    if (kind === "chat") {
      const p = chatPanel();
      if (p) { const pos = getPos(p); _store.windows.chat = { x: pos.x, y: pos.y, min: false }; saveStore(); }
      if (p) focusEl(p);
      if (wasMin) {
        const input = document.getElementById("chat-input");
        if (input) setTimeout(() => (input as HTMLElement).focus(), 30);
      }
    } else {
      ensureMgmtPos();
      const p = mgmtPanel();
      if (p) focusEl(p);
    }
    updateDockIndicators();
  }

  // mgmt 窗第一次可见时才有尺寸 → 摆位延迟到打开那刻(存了就用存的,clamp 兜底)
  function ensureMgmtPos(): void {
    const ov = mgmtOverlay(), p = mgmtPanel(), desk = deskEl();
    if (!ov || !p || !desk || ov.classList.contains("hidden")) return;
    const saved = _store.windows.mgmt;
    if (saved) { const c = clampPos(p, saved.x, saved.y); applyPos(p, c.x, c.y); return; }
    const d = desk.getBoundingClientRect();
    const o = (p.offsetParent as HTMLElement | null);
    const po = o ? o.getBoundingClientRect() : { left: 0, top: 0 };
    if (!(d.width > 0)) return;
    const x = d.left - po.left + Math.max(24, (d.width - p.offsetWidth) / 2);
    const y = d.top - po.top + 48;
    const c = clampPos(p, x, y);
    applyPos(p, c.x, c.y);
  }

  // ============================================================================
  // P1.5 灵魂(docs/53):像素工位区 / 小卡壁炉化+叼卡 / 署名便签 / 工作证摊桌
  // 红线:所有状态渲染只由真实事件驱动 —— busy=真有任务在跑,idle=真空闲,
  // sleep=真的很久没活动;呼吸/眨眼是"在场"(pixelpet 内部),不是戏。
  // ============================================================================

  const SLEEP_AFTER_MS = 30 * 60 * 1000;   // 30 分钟无活动 → 趴下睡(真实状态,不是 flavor)
  const NOTE_CAP = 3;                       // 桌上署名便签上限(旧的淡出)
  const RESULT_CAP = 140;                   // 便签结果摘要截断

  type PresenceRow = {
    role_id: string; display: string; domain_id: string;
    status: string; running: number;
    last_activity_ts: number | null;
    last_task: { id: string; intent: string } | null;
  };

  let _soulOn = false;
  let _mascot: PixelPet.Pet | null = null;
  let _mascotState = "idle";                // 小卡的"真实态"(carry/happy 是短插播,完了回这个)
  let _mascotBusy = false;                  // carry/happy 播放中(播完恢复 _mascotState)
  const _stations = new Map<string, { el: HTMLElement; pet: PixelPet.Pet }>();
  const _signedNotes: HTMLElement[] = [];
  const _workcards = new Map<string, { el: HTMLElement; chips: Map<string, HTMLElement> }>();
  let _soulWs: WebSocket | null = null;
  let _soulWsTimer = 0;
  let _soulWsDelay = 2000;

  function cockpitEl(): HTMLElement | null { return deskEl(); }

  function reducedMotion(): boolean {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch { return false; }
  }

  // ---- DOM 骨架(幂等):工位栏(dock 上方左侧)+ 工作证条 + 小卡像素替身 ----
  function ensureSoulDom(): void {
    const desk = cockpitEl();
    if (!desk) return;
    if (!document.getElementById("desk-presence")) {
      const bar = document.createElement("div");
      bar.className = "desk-presence hidden";
      bar.id = "desk-presence";
      const cards = document.createElement("div");
      cards.className = "desk-workcards hidden";
      cards.id = "desk-workcards";
      const stations = document.createElement("div");
      stations.className = "desk-stations";
      stations.id = "desk-stations";
      bar.appendChild(cards);
      bar.appendChild(stations);
      desk.appendChild(bar);
    }
    // 小卡 sprite 替身:住进右下 .karvy-fab(fab 静态 PNG 在对话/看板视图保留;
    // desk 下 CSS 藏静态图、显 sprite)。canvas 只是挂载占位,createPet 原位换成 sprite 根。
    const fab = document.getElementById("chat-open");
    if (fab && !document.getElementById("desk-karvy-pixel")) {
      const cv = document.createElement("canvas");
      cv.id = "desk-karvy-pixel";
      fab.appendChild(cv);
    }
  }

  function ensureMascot(): void {
    const cv = document.getElementById("desk-karvy-pixel") as HTMLCanvasElement | null;
    if (!cv) return;
    if (!_mascot) _mascot = PixelPet.createPet({ canvas: cv, accent: PixelPet.KARVY_ACCENT });
  }

  function setMascotReal(state: string): void {
    _mascotState = state;
    if (_mascot && !_mascotBusy) _mascot.setState(state);
  }

  // 拍板闭环(h2a_envelope 真实事件)→ 短暂开心帧(耳朵动),完了回真实态
  function mascotHappy(): void {
    if (!_mascot || _mascotBusy) return;
    _mascotBusy = true;
    _mascot.setState("happy");
    setTimeout(() => {
      _mascotBusy = false;
      if (_mascot) _mascot.setState(_mascotState);
    }, 2200);
  }

  // ---- 工位区:GET /api/roles/presence(契约冻结)+ WS role_presence 增量 ----
  // API 没上线/调不通 → 工位栏优雅隐藏(别空壳);小卡行不占工位(它常驻右下,驱动替身状态)。
  function stationVisible(row: PresenceRow): boolean {
    return row.role_id !== "karvy" && (row.status === "busy" || !!row.last_activity_ts);
  }

  function petStateFor(row: PresenceRow): string {
    if (row.status === "busy") return "working";
    const ts = (row.last_activity_ts || 0) * 1000;
    return ts && Date.now() - ts < SLEEP_AFTER_MS ? "idle" : "sleep";
  }

  function upsertStation(row: PresenceRow): void {
    if (!row || !row.role_id) return;
    if (row.role_id === "karvy") {           // 小卡的 presence 驱动右下替身(真实状态,不是戏)
      setMascotReal(row.status === "busy" ? "working" : "idle");
      return;
    }
    const wrap = document.getElementById("desk-stations");
    const bar = document.getElementById("desk-presence");
    if (!wrap || !bar) return;
    if (!stationVisible(row)) {              // 没有活动记录的角色不摆空工位
      const gone = _stations.get(row.role_id);
      if (gone) { gone.pet.destroy(); gone.el.remove(); _stations.delete(row.role_id); }
      if (!_stations.size) bar.classList.add("hidden");
      return;
    }
    let st = _stations.get(row.role_id);
    if (!st) {
      const el = document.createElement("button");
      el.className = "desk-station";
      el.setAttribute("data-role-id", row.role_id);
      const cv = document.createElement("canvas");
      const light = document.createElement("span");
      light.className = "station-light";
      const name = document.createElement("span");
      name.className = "station-name";
      el.appendChild(cv);
      el.appendChild(light);
      el.appendChild(name);
      wrap.appendChild(el);
      const pet = PixelPet.createPet({ canvas: cv, accent: PixelPet.colorForRole(row.role_id) });
      st = { el, pet };
      _stations.set(row.role_id, st);
      el.addEventListener("click", () => {
        const lt = (el.dataset.taskId || "");
        if (lt) jumpToTask(lt, el.dataset.taskIntent || "");
        else restoreWin("chat");
      });
    }
    const nameEl = st.el.querySelector(".station-name");
    if (nameEl) nameEl.textContent = row.display || row.role_id;
    st.el.setAttribute("aria-label", row.display || row.role_id);
    const state = petStateFor(row);
    st.pet.setState(state);
    st.el.dataset.petState = state;                          // 可断言的真实状态(smoke/Playwright)
    st.el.classList.toggle("is-busy", row.status === "busy");
    st.el.dataset.taskId = (row.last_task && row.last_task.id) || "";
    st.el.dataset.taskIntent = (row.last_task && row.last_task.intent) || "";
    // hover 出"正在:<intent>"(busy);idle/sleep 老实说空闲/休息
    const tip = row.status === "busy" && row.last_task
      ? t("desk.presence_doing", { intent: row.last_task.intent })
      : state === "sleep" ? t("desk.presence_rest") : t("desk.presence_idle");
    st.el.setAttribute("data-tip", tip);
    bar.classList.remove("hidden");
  }

  async function refreshPresence(): Promise<void> {
    const bar = document.getElementById("desk-presence");
    if (typeof fetch !== "function") { if (bar) bar.classList.add("hidden"); return; }
    try {
      const r = await fetch("/api/roles/presence");
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      const rows: PresenceRow[] = (data && data.roles) || [];
      const seen = new Set<string>();
      rows.forEach((row) => { seen.add(row.role_id); upsertStation(row); });
      _stations.forEach((st, rid) => {       // 快照里没有的工位撤掉(角色删了)
        if (!seen.has(rid)) { st.pet.destroy(); st.el.remove(); _stations.delete(rid); }
      });
      if (bar) bar.classList.toggle("hidden", !_stations.size && !_workcards.size);
    } catch {
      // API 没上线/挂了 → 优雅隐藏,不空壳、不报错刷屏;WS 增量到了会再开
      if (bar && !_stations.size && !_workcards.size) bar.classList.add("hidden");
    }
  }

  // ---- 跳去该任务:复用 task_board 的跳聊天逻辑(点看板同一张卡 = 同一条 openTaskDetail 路径)----
  function jumpToTask(_taskId: string, intent: string): void {
    const probe = (intent || "").slice(0, 64);
    const cards = document.querySelectorAll<HTMLElement>("#busy-list .task-card, #task-board .task-card");
    for (let i = 0; i < cards.length; i++) {
      const it = cards[i].querySelector(".task-intent");
      if (it && probe && (it.textContent || "").indexOf(probe) === 0) { cards[i].click(); return; }
    }
    // 看板里找不到(被 cap 挤掉了)→ 退而把 📥/🔄 便签置顶闪一下(fail-soft,不装作跳成功)
    const note = document.querySelector<HTMLElement>(".cockpit-grid .col-intel");
    if (note) {
      if (note.classList.contains("col-collapsed")) note.classList.remove("col-collapsed");
      focusEl(note);
      note.classList.remove("note-alert");
      void note.offsetWidth;
      note.classList.add("note-alert");
      setTimeout(() => note.classList.remove("note-alert"), 2600);
    }
  }

  // ---- 署名便签(vignette ②):task_status done → 桌面浮出一张手写感小便签 ----
  function spawnSignedNote(tk: { id?: string; who?: string; intent?: string; result?: string; finished?: number }): void {
    const desk = cockpitEl();
    if (!desk || !deskView()) return;
    const note = document.createElement("div");
    note.className = "desk-signed-note";
    const tilt = (Math.random() * 4 - 2).toFixed(2);        // 手放上去的 ±2° 随机旋转
    note.style.setProperty("--note-tilt", tilt + "deg");
    const who = document.createElement("div");
    who.className = "signed-note-who";
    who.textContent = "✍ " + (tk.who || "?");
    const when = document.createElement("span");
    when.className = "signed-note-time";
    try {
      when.textContent = new Date((tk.finished || Date.now() / 1000) * 1000)
        .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch { when.textContent = ""; }
    who.appendChild(when);
    const body = document.createElement("div");
    body.className = "signed-note-body";
    const text = (tk.result || tk.intent || "").trim();
    body.textContent = text.length > RESULT_CAP ? text.slice(0, RESULT_CAP) + "…" : text;
    note.appendChild(who);
    note.appendChild(body);
    note.setAttribute("data-tip", t("desk.note_open"));
    note.addEventListener("click", () => jumpToTask(tk.id || "", tk.intent || ""));
    // 落点:桌面中带(避开右侧便签列/右下小卡),按张数往下错落 + 少许随机
    const d = desk.getBoundingClientRect();
    const baseX = d.width > 0 ? Math.max(24, d.width * 0.32) : 120;
    const baseY = d.height > 0 ? Math.max(24, d.height * 0.18) : 80;
    note.style.left = Math.round(baseX + Math.random() * 40 + _signedNotes.length * 26) + "px";
    note.style.top = Math.round(baseY + _signedNotes.length * 64 + Math.random() * 18) + "px";
    note.style.zIndex = String(++_zTop);
    desk.appendChild(note);
    _signedNotes.push(note);
    while (_signedNotes.length > NOTE_CAP) {                 // 3 张上限,最旧的淡出
      const old = _signedNotes.shift();
      if (old) { old.classList.add("is-fading"); setTimeout(() => old.remove(), 450); }
    }
  }

  // ---- 工作证摊桌(vignette ⑥ 最小版):workflow/圆桌 running → 参与者名字牌 ⏳/✓ ----
  function ensureWorkcard(tk: { id?: string; who?: string; intent?: string }): void {
    const id = tk.id || "";
    if (!id || _workcards.has(id)) return;
    const box = document.getElementById("desk-workcards");
    const bar = document.getElementById("desk-presence");
    if (!box || !bar) return;
    const el = document.createElement("div");
    el.className = "desk-workcard";
    el.setAttribute("data-task-id", id);
    const title = document.createElement("div");
    title.className = "workcard-title";
    title.textContent = (tk.who || "⚙") + " · " + ((tk.intent || "").slice(0, 42) || t("desk.workcard_wip"));
    const chips = document.createElement("div");
    chips.className = "workcard-chips";
    el.appendChild(title);
    el.appendChild(chips);
    el.addEventListener("click", () => jumpToTask(id, tk.intent || ""));
    box.appendChild(el);
    box.classList.remove("hidden");
    bar.classList.remove("hidden");
    _workcards.set(id, { el, chips: new Map() });
  }

  function workcardStep(st: { task_id?: string; display?: string; status?: string }): void {
    const wc = _workcards.get(st.task_id || "");
    if (!wc) return;                                         // 只跟画在桌上的群任务(最小版)
    const key = st.display || "?";
    let chip = wc.chips.get(key);
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "work-chip";
      const mark = document.createElement("span");
      mark.className = "chip-mark";
      const nm = document.createElement("span");
      nm.className = "chip-name";
      nm.textContent = key;
      chip.appendChild(mark);
      chip.appendChild(nm);
      wc.chips.set(key, chip);
      wc.el.querySelector(".workcard-chips")!.appendChild(chip);
    }
    const failed = st.status === "failed";
    chip.classList.toggle("failed", failed);
    chip.classList.toggle("done", !failed);
    const mk = chip.querySelector(".chip-mark");
    if (mk) mk.textContent = failed ? "✗" : "✓";
  }

  function finishWorkcard(taskId: string, ok: boolean): void {
    const wc = _workcards.get(taskId);
    if (!wc) return;
    wc.el.classList.add(ok ? "is-done" : "is-failed");
    setTimeout(() => {
      wc.el.remove();
      _workcards.delete(taskId);
      const box = document.getElementById("desk-workcards");
      if (box && !_workcards.size) box.classList.add("hidden");
      const bar = document.getElementById("desk-presence");
      if (bar && !_stations.size && !_workcards.size) bar.classList.add("hidden");
    }, ok ? 6000 : 9000);                                    // 全勾完/挂了停留一会儿再收
  }

  // ---- 灵魂事件消费(自开只读 WS 的 onmessage;也是 smoke/Playwright 的测试接缝)----
  function soulHandle(msg: { type?: string; payload?: unknown }): void {
    if (!msg || !deskView()) return;
    const p = (msg.payload || {}) as Record<string, unknown>;
    if (msg.type === "role_presence") {
      upsertStation(p as unknown as PresenceRow);
    } else if (msg.type === "task_status") {
      const tk = p as { id?: string; status?: string; role?: string; who?: string; intent?: string; result?: string; finished?: number };
      if (tk.status === "running" && tk.role === "group") ensureWorkcard(tk);
      else if (tk.status === "done") { spawnSignedNote(tk); finishWorkcard(tk.id || "", true); }
      else if (tk.status === "error") finishWorkcard(tk.id || "", false);
    } else if (msg.type === "task_step") {
      workcardStep(p as { task_id?: string; display?: string; status?: string });
    } else if (msg.type === "h2a_envelope") {
      mascotHappy();                                          // 拍板闭环 → 小卡短暂开心(真实事件)
    }
    // h2a_proposal 不在这处理:app.js 收到会调 notifyH2A(叼卡);双处理 = 播两遍
  }

  // 只读 WS:desk 进场才连、离场即断;绝不 send(所有写路径仍走 app.js 那条连接)
  function soulConnect(): void {
    if (typeof WebSocket !== "function") return;
    if (_soulWs && (_soulWs.readyState === 0 || _soulWs.readyState === 1)) return;
    try {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(proto + "//" + location.host + "/ws");
      _soulWs = ws;
      ws.onmessage = (ev: MessageEvent) => {
        try { soulHandle(JSON.parse(String(ev.data))); } catch { /* 非 JSON 心跳等,忽略 */ }
      };
      ws.onopen = () => { _soulWsDelay = 2000; };
      ws.onerror = () => { /* onclose 统一处理重连;这里只吞 error 事件 */ };
      ws.onclose = () => {
        _soulWs = null;
        if (_soulOn) {                                        // 还在桌面 → 退避重连
          _soulWsTimer = window.setTimeout(soulConnect, _soulWsDelay);
          _soulWsDelay = Math.min(_soulWsDelay * 2, 30000);
        }
      };
    } catch { _soulWs = null; }
  }

  // 进场种子:正在跑的 workflow/圆桌把工作证先摊上(步级勾随 WS 到)
  async function seedWorkcards(): Promise<void> {
    if (typeof fetch !== "function") return;
    try {
      const r = await fetch("/api/tasks");
      if (!r.ok) return;
      const data = await r.json();
      ((data && data.tasks) || []).forEach((tk: { id?: string; who?: string; intent?: string; status?: string; role?: string }) => {
        if (tk && tk.status === "running" && tk.role === "group") ensureWorkcard(tk);
      });
    } catch { /* 看板 API 不通 → 没有种子,不吵 */ }
  }

  // ---- vignette ④:最近沉淀的知识 → 桌角浮现只读小卡("它记得你且你看得见")----
  // 数据源 = GET /api/memory/recent(契约冻结);调不通/空 → 优雅隐藏,不空壳。纯只读,点=跳知识库。
  const RECENT_KNOWLEDGE_CAP = 3;         // 桌角最多浮 3 条(旧的不堆)
  const RECENT_CONTENT_CAP = 120;         // 每条摘要截断
  async function refreshRecentKnowledge(): Promise<void> {
    const desk = cockpitEl();
    if (!desk || typeof fetch !== "function") return;
    let items: Array<{ id?: string; content?: string; source?: string; domain?: string }> = [];
    try {
      const r = await fetch("/api/memory/recent?limit=" + RECENT_KNOWLEDGE_CAP);
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      items = ((data && data.items) || []).slice(0, RECENT_KNOWLEDGE_CAP);
    } catch { items = []; }        // API 没上线/挂了 → 优雅隐藏
    let box = document.getElementById("desk-recent-knowledge");
    if (!items.length) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement("div");
      box.className = "desk-recent-knowledge";
      box.id = "desk-recent-knowledge";
      desk.appendChild(box);
    }
    box.textContent = "";
    const head = document.createElement("div");
    head.className = "desk-recent-head";
    head.textContent = "🧠 " + t("desk.recent_knowledge");
    box.appendChild(head);
    items.forEach((it) => {
      const card = document.createElement("button");
      card.className = "desk-recent-item";
      const text = (it.content || "").trim();
      card.textContent = text.length > RECENT_CONTENT_CAP ? text.slice(0, RECENT_CONTENT_CAP) + "…" : text;
      card.setAttribute("data-tip", t("desk.recent_open"));
      card.addEventListener("click", () => openMemoryPanel());
      box.appendChild(card);
    });
  }

  // ---- vignette ⑤:周五纪念物 → 一枚"本周纪念物"小瓷砖(周报数字:跑了 N 个任务/结晶 M 个技能)----
  // 数据源 = GET /api/desk/memento(契约冻结,零 LLM 从 Trace/账本投影)。全 0 → 不出砖(没成绩不装)。
  async function refreshMemento(): Promise<void> {
    const desk = cockpitEl();
    if (!desk || typeof fetch !== "function") return;
    let m: Record<string, unknown> | null = null;
    try {
      const r = await fetch("/api/desk/memento");
      if (!r.ok) throw new Error(String(r.status));
      m = await r.json();
    } catch { m = null; }
    const num = (k: string): number => {
      const v = m ? (m as Record<string, unknown>)[k] : 0;
      return typeof v === "number" && isFinite(v) ? v : 0;
    };
    const tasks = num("tasks_done"), skills = num("skills_new"),
      decisions = num("decisions"), tokens = num("tokens_total");
    let tile = document.getElementById("desk-memento");
    if (!m || (tasks + skills + decisions <= 0)) {   // 本周没动静 → 不出纪念物(没成绩不装)
      if (tile) tile.remove();
      return;
    }
    if (!tile) {
      tile = document.createElement("div");
      tile.className = "desk-memento";
      tile.id = "desk-memento";
      desk.appendChild(tile);
    }
    tile.textContent = "";
    const wk = (m.week_label && String(m.week_label)) || "";
    const head = document.createElement("div");
    head.className = "desk-memento-head";
    head.textContent = "🏅 " + t("desk.memento_title") + (wk ? " · " + wk : "");
    tile.appendChild(head);
    const stats = document.createElement("div");
    stats.className = "desk-memento-stats";
    const chip = (icon: string, n: number, key: string): void => {
      if (n <= 0) return;
      const c = document.createElement("span");
      c.className = "desk-memento-chip";
      c.textContent = icon + " " + t(key, { n: n });
      stats.appendChild(c);
    };
    chip("✅", tasks, "desk.memento_tasks");
    chip("🧬", skills, "desk.memento_skills");
    chip("⚖", decisions, "desk.memento_decisions");
    if (tokens > 0) {
      const c = document.createElement("span");
      c.className = "desk-memento-chip desk-memento-tokens";
      c.textContent = "🔢 " + t("desk.memento_tokens", { n: tokens > 1000 ? (tokens / 1000).toFixed(1) + "k" : String(tokens) });
      stats.appendChild(c);
    }
    tile.appendChild(stats);
  }

  // 跳知识库:点桌角知识小卡 = 打开 Knowledge 管理面板(同一条 setupMgmtPanels 打开路径)。
  function openMemoryPanel(): void {
    const nav = document.querySelector<HTMLButtonElement>('.sidebar .nav-item[data-panel="memory"]')
      || document.querySelector<HTMLButtonElement>('.dock-item[data-panel="memory"]');
    if (nav) nav.click();
  }

  function enterSoul(): void {
    _soulOn = true;
    ensureSoulDom();
    ensureMascot();
    void refreshPresence();
    void seedWorkcards();
    void refreshRecentKnowledge();   // vignette ④:桌角最近沉淀的知识
    void refreshMemento();           // vignette ⑤:本周纪念物瓷砖
    soulConnect();
  }

  function leaveSoul(): void {
    _soulOn = false;
    if (_soulWsTimer) { clearTimeout(_soulWsTimer); _soulWsTimer = 0; }
    if (_soulWs) { try { _soulWs.close(); } catch { /* */ } _soulWs = null; }
    if (_mascot) { _mascot.destroy(); _mascot = null; }
    _mascotBusy = false;
    _mascotState = "idle";
    _stations.forEach((st) => { st.pet.destroy(); st.el.remove(); });
    _stations.clear();
    _workcards.forEach((wc) => wc.el.remove());
    _workcards.clear();
    _signedNotes.forEach((n) => n.remove());
    _signedNotes.length = 0;
    const cv = document.getElementById("desk-karvy-pixel");
    if (cv) cv.remove();                                      // 老视图零痕迹:像素替身只住 desk
    const rk = document.getElementById("desk-recent-knowledge");
    if (rk) rk.remove();                                      // vignette ④:桌角知识小卡随离场清
    const mem = document.getElementById("desk-memento");
    if (mem) mem.remove();                                    // vignette ⑤:纪念物瓷砖随离场清
    const bar = document.getElementById("desk-presence");
    if (bar) bar.classList.add("hidden");
    const box = document.getElementById("desk-workcards");
    if (box) box.classList.add("hidden");
    const actor = document.getElementById("desk-carry-actor");
    if (actor) actor.remove();
  }

  // ---- 叼卡动画(vignette ③):h2a_proposal 到达 → 小卡叼白卡从右下走向 ⚖ 便签 ----
  // 返回 true = 播了(到位后由调用方闪便签);false = 跳过(降级直接闪)。
  let _carrying = false;
  function playCarry(note: HTMLElement, onArrive: () => void): boolean {
    if (_carrying || reducedMotion() || !_mascot) return false;
    const fab = document.getElementById("chat-open");
    const cv = document.getElementById("desk-karvy-pixel");
    if (!fab || !cv) return false;
    const from = fab.getBoundingClientRect();
    const to = note.getBoundingClientRect();
    _carrying = true;
    _mascotBusy = true;
    const actor = document.createElement("div");
    actor.id = "desk-carry-actor";
    actor.className = "desk-carry";
    const acv = document.createElement("canvas");
    actor.appendChild(acv);
    const pet = PixelPet.createPet({ canvas: acv, accent: PixelPet.KARVY_ACCENT });
    pet.setState("carry");
    actor.style.left = Math.round(from.left) + "px";
    actor.style.top = Math.round(from.top) + "px";
    document.body.appendChild(actor);
    cv.classList.add("is-away");                             // 常驻位的小卡"起身走了"
    const dx = Math.round(to.left + Math.max(0, to.width / 2) - from.left);
    const dy = Math.round(to.top + Math.max(0, to.height) - 40 - from.top);
    const cleanup = () => {
      pet.destroy();
      actor.remove();
      cv.classList.remove("is-away");
      _carrying = false;
      _mascotBusy = false;
      if (_mascot) _mascot.setState(_mascotState);           // 回窝,回真实态
      onArrive();                                            // 到位 → 便签闪(既有动画)
    };
    // 强制 reflow 后再上 transform,transition 才生效;jsdom 无真布局 → 定时器兜底
    void actor.offsetWidth;
    actor.classList.add("is-walking");
    actor.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
    let done = false;
    const finish = () => { if (!done) { done = true; cleanup(); } };
    actor.addEventListener("transitionend", finish);
    setTimeout(finish, 2000);
    return true;
  }

  // ---- ⚖便签的"常驻可瞟"保险(docs/51 §4.2;docs/46 铁律的桌面版)----
  // 事件 vs 快照(Hardy 实拍拍到的开屏"飘上去"):叼卡/闪烁/冒泡是**新卡到来事件**的剧场,
  // 只回应真事件;页面加载/重连把存量 pending 卡回放进列表(replay)是**状态回放**,
  // 只保状态(展开/置顶/在视口内可瞟),一帧戏都不演。区分只做在这一处:
  // 调用方(app.js)标注来源 —— WS h2a_proposal / 手动求建议 = 事件;boot fetch = replay。
  function notifyH2A(opts?: { replay?: boolean }): void {
    if (!deskView()) return;
    const note = document.querySelector<HTMLElement>(".cockpit-grid .col-decide");
    if (!note) return;
    if (note.classList.contains("col-collapsed")) {          // 折叠态自动展开
      note.classList.remove("col-collapsed");
      try { localStorage.setItem("karvy.rail.col-decide", "0"); } catch { /* */ }
    }
    focusEl(note);                                           // 置顶(便签与窗口同一 z 空间)
    const pos = clampPos(note, getPos(note).x, getPos(note).y);
    applyPos(note, pos.x, pos.y);                            // 永远保证在视口内
    if (opts && opts.replay) return;                         // 快照回放:到此为止(无剧场)
    const flash = () => {
      note.classList.remove("note-alert");
      void note.offsetWidth;                                 // 重触发动画
      note.classList.add("note-alert");
      setTimeout(() => note.classList.remove("note-alert"), 2800);
      const bubble = document.getElementById("karvy-bubble");  // 卡皮巴拉冒泡
      if (bubble) {
        const dots = bubble.querySelector(".karvy-bubble-dots");
        if (dots) dots.textContent = "⚖";
        bubble.classList.remove("hidden");
        setTimeout(() => bubble.classList.add("hidden"), 6000);
      }
    };
    // P1.5:先叼卡走过去,到位再闪;播不了(reduced-motion/无替身)→ 直接闪(0 回归)
    if (!playCarry(note, flash)) flash();
  }

  // ---- 日/夜壁纸(Hardy 出图,1920×1079):按**客户端本地时间**自动切 ----
  // 6:00–18:59 = day,其余 night;四档设置存 localStorage:auto(默认)/day/night/off。
  // 判定时机 = 进桌面那刻 + 分钟级低频重判(跨过 6:00/19:00 边界自动换,不上高频 timer);
  // off = 摘光类名,纯色桌面回现状。类只挂在 body(desk-wall-day/-night),CSS 全部
  // body.desk-view 前缀 → 老视图零影响。
  const WALL_LS_KEY = "karvyloop_desk_wall.v1";
  const WALL_MODES = ["auto", "day", "night", "off"];
  const WALL_CHECK_MS = 60 * 1000;
  let _wallTimer = 0;
  function wallMode(): string {
    try {
      const v = localStorage.getItem(WALL_LS_KEY) || "auto";
      return WALL_MODES.indexOf(v) >= 0 ? v : "auto";      // 脏数据回默认
    } catch { return "auto"; }
  }
  function wallVariantFor(hour: number): string {
    return hour >= 6 && hour < 19 ? "day" : "night";
  }
  function applyWall(now?: Date): void {
    const mode = wallMode();
    let v = "";
    if (mode === "day" || mode === "night") v = mode;
    else if (mode === "auto") v = wallVariantFor((now || new Date()).getHours());
    document.body.classList.toggle("desk-wall-day", _entered && v === "day");
    document.body.classList.toggle("desk-wall-night", _entered && v === "night");
  }
  function setWallMode(mode: string): void {
    if (WALL_MODES.indexOf(mode) < 0) return;
    try { localStorage.setItem(WALL_LS_KEY, mode); } catch { /* 无 localStorage → 本次会话生效 */ }
    applyWall();
    updateWallTip();
  }
  function updateWallTip(): void {
    const b = document.getElementById("desk-wall-btn");
    if (!b) return;
    const tip = t("desk.wall_" + wallMode());
    b.setAttribute("data-tip", tip);
    b.setAttribute("title", tip);
    b.setAttribute("aria-label", tip);
  }
  function wallStart(): void {
    applyWall();
    updateWallTip();
    if (!_wallTimer) {
      _wallTimer = window.setInterval(() => { if (_entered) applyWall(); }, WALL_CHECK_MS);
    }
  }
  function wallStop(): void {
    // 与 wallStart 的 window.setInterval 严格同源(window.clearInterval):浏览器里二者等价,
    // jsdom smoke 里 bare clearInterval 是 Node 的表,清不掉 jsdom 的 interval → 进程永不退出
    if (_wallTimer) { window.clearInterval(_wallTimer); _wallTimer = 0; }
    document.body.classList.remove("desk-wall-day", "desk-wall-night");
  }

  // ---- 默认摆位:办公桌式铺开(Hardy 2026-07-03:堆一列=墙角,不是桌子)----
  // 便签铺成右侧**两列错落**:第 1 列(最右)⚖最上(继承"⚖永远第一")+ 🔄;
  // 第 2 列(中右,y 略降造错落感)📥 + 🔮。左半留给聊天主窗;
  // 右下 220×200 是卡皮巴拉的地盘,默认摆位永不侵入(超出往上收)。
  const KARVY_ZONE = { w: 220, h: 200 };
  const NOTE_MAX_H = 330;                                   // 便签 CSS max-height(desktop.css)
  function computeNoteDefault(col: HTMLElement, idx: number, colBottoms: number[]): Pos {
    const desk = deskEl();
    if (!desk) return { x: 12, y: 16 };
    const d = desk.getBoundingClientRect();
    const w = col.offsetWidth || 304;
    const h = col.offsetHeight || 180;
    const lane = Math.floor(idx / 2);                       // 0=最右列,1=中右列
    const x = Math.max(12, d.width - (lane + 1) * (w + 24));
    const laneStart = lane === 0 ? 16 : 44;                 // 第 2 列略降,错落有桌感
    const floor = colBottoms[lane] !== undefined ? colBottoms[lane] : laneStart;
    let y = floor;
    // 最右列避让卡皮巴拉地盘:便签底部会压进右下角 → 往上收(至少留出顶部 16px);
    // 但**绝不叠回同列上一张**(便签互相盖头比压吉祥物糟 —— z 设计本就内容在上,
    // 真挤不下时吉祥物让位,不是便签让位)。
    if (lane === 0 && y + h > d.height - KARVY_ZONE.h && d.width - w - 24 < d.width - KARVY_ZONE.w) {
      y = Math.max(Math.max(16, d.height - KARVY_ZONE.h - h - 12), floor);
    }
    // ⚖ 是主角且开机后才灌进存量待拍卡:按 CSS max-height 预留整槽,不按进场瞬时高度排
    // (Hardy 实拍:5 张 pending 卡让 ⚖ 长到全高,按瞬时高度排位会盖住下一张的头)。
    const slot = idx === 0 ? Math.max(h, NOTE_MAX_H) : h;
    colBottoms[lane] = y + slot + 14;
    return { x, y };
  }

  // ---- enter / leave(视图切换的唯一入口;幂等)----
  function wireAll(): void {
    if (_wired) return;
    _wired = true;
    noteEls().forEach((col) => {
      const head = col.querySelector<HTMLElement>(".col-head");
      if (!head) return;
      makeDraggable(col, head, noteKey(col));
      wireHandleKeys(col, head, noteKey(col));
    });
    const cp = chatPanel(), ch = document.querySelector<HTMLElement>("#chat-modal .chat-panel-head");
    if (cp && ch) { makeDraggable(cp, ch, "chat"); wireHandleKeys(cp, ch, "chat"); }
    const mp = mgmtPanel(), mh = document.querySelector<HTMLElement>("#mgmt-modal .modal-head");
    if (mp && mh) { makeDraggable(mp, mh, "mgmt"); wireHandleKeys(mp, mh, "mgmt"); }
  }

  function handles(): HTMLElement[] {
    const out: HTMLElement[] = [];
    noteEls().forEach((c) => { const h = c.querySelector<HTMLElement>(".col-head"); if (h) out.push(h); });
    const ch = document.querySelector<HTMLElement>("#chat-modal .chat-panel-head"); if (ch) out.push(ch);
    const mh = document.querySelector<HTMLElement>("#mgmt-modal .modal-head"); if (mh) out.push(mh);
    return out;
  }

  function enter(): void {
    const desk = deskEl();
    if (!desk) return;
    renderDock();
    wireAll();
    _store = loadStore();
    _entered = true;
    _zTop = BASE_Z;
    // 便签:存过用存的(clamp 进当前视口),没存过按"办公桌铺开"默认位(两列错落,避卡皮巴拉)
    const colBottoms: number[] = [];
    noteEls().forEach((col, idx) => {
      const k = noteKey(col);
      const saved = _store.notes[k];
      const pos = saved ? clampPos(col, saved.x, saved.y) : computeNoteDefault(col, idx, colBottoms);
      applyPos(col, pos.x, pos.y);
      col.style.zIndex = String(++_zTop);
    });
    // 聊天窗:默认开、默认左上主位;记住上次位置与最小化态
    const cw = _store.windows.chat;
    const cp = chatPanel();
    if (cp) {
      const pos = cw ? clampPos(cp, cw.x, cw.y) : { x: 18, y: 18 };
      applyPos(cp, pos.x, pos.y);
    }
    const cOv = chatOverlay();
    if (cOv) {
      cOv.classList.toggle("desk-min", !!(cw && cw.min));
      cOv.style.zIndex = String(++_zTop);
    }
    // mgmt 窗:开着才有尺寸,摆位交给 ensureMgmtPos(现在 + 每次打开)
    const mOv = mgmtOverlay();
    if (mOv) mOv.style.zIndex = String(++_zTop);
    ensureMgmtPos();
    // a11y:标题栏 tab 可达;聊天 ✕ 在桌面语义 = 最小化
    handles().forEach((h) => h.setAttribute("tabindex", "0"));
    const cx = document.getElementById("chat-modal-close");
    if (cx) { cx.setAttribute("title", t("desk.min")); cx.setAttribute("aria-label", t("desk.min")); }
    updateDockIndicators();
    wallStart();   // 日/夜壁纸:进场判定一次 + 分钟级低频重判
    enterSoul();   // P1.5 灵魂:工位区 + 像素小卡 + 只读 WS(desk 进场才活)
  }

  function leave(): void {
    _entered = false;
    wallStop();    // 摘壁纸类 + 停低频重判(老视图零痕迹)
    leaveSoul();   // P1.5 灵魂:断 WS、销毁像素形象、清便签/工作证(老视图零痕迹)
    // 清干净全部内联痕迹:两个老视图(对话/看板)像素级不动
    noteEls().forEach((col) => { col.style.transform = ""; col.style.zIndex = ""; col.classList.remove("note-alert", "desk-focused"); });
    const cp = chatPanel(); if (cp) { cp.style.transform = ""; cp.classList.remove("desk-focused"); }
    const mp = mgmtPanel(); if (mp) { mp.style.transform = ""; mp.classList.remove("desk-focused"); }
    const cOv = chatOverlay(); if (cOv) { cOv.classList.remove("desk-min"); cOv.style.zIndex = ""; }
    const mOv = mgmtOverlay(); if (mOv) { mOv.classList.remove("desk-min"); mOv.style.zIndex = ""; }
    handles().forEach((h) => h.removeAttribute("tabindex"));
    const cx = document.getElementById("chat-modal-close");
    if (cx) { cx.setAttribute("title", ""); cx.setAttribute("aria-label", "close"); }
  }

  // ---- ↺ 重置桌面布局(逃生门):清存档 → 回默认 ----
  function resetLayout(): void {
    try { if (!window.confirm(t("desk.reset_confirm"))) return; } catch { /* 无 confirm(测试)→ 直接重置 */ }
    try { localStorage.removeItem(LS_KEY); } catch { /* */ }
    _store = { notes: {}, windows: {} };
    if (deskView()) enter();   // 重进一遍 = 重算默认位(含清最小化态)
  }

  // ---- 全局接线(load 时一次;desk-view 外全部 no-op)----
  // 聊天 ✕:桌面语义 = 最小化(capture 拦在 app.js closeChatModal 之前);
  // 卡皮巴拉(#chat-open):恢复/聚焦聊天窗(不拦,app.js openChatModal 继续聚焦输入框)。
  document.addEventListener("click", (e) => {
    if (!deskView()) return;
    const tgt = e.target as HTMLElement | null;
    if (!tgt || !tgt.closest) return;
    if (tgt.closest("#chat-modal-close")) {
      e.preventDefault();
      e.stopPropagation();
      minimizeWin("chat");
      return;
    }
    if (tgt.closest("#chat-open")) restoreWin("chat");
    if (tgt.closest("#mgmt-min")) { e.preventDefault(); e.stopPropagation(); minimizeWin("mgmt"); }
  }, true);

  // 窗口/便签 pointerdown → 聚焦置顶(简单递增;重进视图时 enter() 重排归一防溢出)
  document.addEventListener("pointerdown", (e) => {
    if (!deskView()) return;
    const tgt = e.target as HTMLElement | null;
    if (!tgt || !tgt.closest) return;
    const box = tgt.closest(".cockpit-grid .cockpit-col, #chat-modal .chat-panel, #mgmt-modal .modal") as HTMLElement | null;
    if (box) focusEl(box);
  }, true);

  // mgmt 开/关(modal.ts 加减 .hidden)→ 同步 dock 指示 + 首开摆位(不动 modal.ts,观察即可)。
  // 铁律:观察者回调**绝不无条件改被观察元素的 class**——classList.remove() 即使 token
  // 不在也会重写 class 属性 → 再触发观察者 = 微任务死循环(主线程冻死);且无脑
  // remove("desk-min") 会把刚 minimize 的窗秒撤。只在 hidden 真的从有→无(重开面板)时动它。
  function observeOverlays(): void {
    if (typeof MutationObserver !== "function") return;
    const watch = (el: HTMLElement | null, onChange: () => void) => {
      if (!el) return;
      new MutationObserver(onChange).observe(el, { attributes: true, attributeFilter: ["class"] });
    };
    const mOv0 = mgmtOverlay();
    let mgmtWasHidden = !!(mOv0 && mOv0.classList.contains("hidden"));
    watch(mOv0, () => {
      const ov = mgmtOverlay();
      if (!ov) return;
      const hid = ov.classList.contains("hidden");
      const reopened = mgmtWasHidden && !hid;   // 关→开 的真跳变(dock/侧栏点开面板)
      mgmtWasHidden = hid;
      if (!deskView()) return;
      if (reopened) {
        if (ov.classList.contains("desk-min")) ov.classList.remove("desk-min");   // 重开 = 恢复可见
        ensureMgmtPos();
        const p = mgmtPanel(); if (p) focusEl(p);
      }
      updateDockIndicators();
    });
    watch(chatOverlay(), () => { if (deskView()) updateDockIndicators(); });
  }

  // mgmt 标题栏注入 ─ 最小化按钮(仅桌面视图显示,CSS 控;✕ 语义保持"关闭"不变)
  function injectMgmtMin(): void {
    const head = document.querySelector("#mgmt-modal .modal-head");
    const close = document.getElementById("mgmt-close");
    if (!head || !close || document.getElementById("mgmt-min")) return;
    const b = document.createElement("button");
    b.className = "modal-close desk-min-btn";
    b.id = "mgmt-min";
    b.textContent = "─";
    b.setAttribute("data-i18n-title", "desk.min");
    head.insertBefore(b, close);
  }

  // 换分辨率/拖窗口尺寸:全部 clamp 回画布(不丢窗)
  let _rszT = 0;
  window.addEventListener("resize", () => {
    if (!_entered || !deskView()) return;
    if (_rszT) clearTimeout(_rszT);
    _rszT = window.setTimeout(() => {
      _rszT = 0;
      noteEls().forEach((col) => { const p = getPos(col); const c = clampPos(col, p.x, p.y); applyPos(col, c.x, c.y); });
      const cp = chatPanel(); if (cp) { const p = getPos(cp); const c = clampPos(cp, p.x, p.y); applyPos(cp, c.x, c.y); }
      const mp = mgmtPanel();
      if (mp && mgmtOverlay() && !mgmtOverlay()!.classList.contains("hidden")) {
        const p = getPos(mp); const c = clampPos(mp, p.x, p.y); applyPos(mp, c.x, c.y);
      }
    }, 120);
  });

  // ---- load 时的一次性准备(脚本在 body 尾、app.js 之前:DOM 已就绪,boot 未跑)----
  renderDock();        // dock 按钮先于 setupMgmtPanels 存在 → 同一批绑定命中
  injectMgmtMin();
  observeOverlays();

  const KarvyDesktop = {
    enter, leave, notifyH2A, resetLayout,
    // P1.5 测试接缝(smoke/Playwright 喂真实事件形状,不开真 socket;生产路径 = soulConnect 的 onmessage)
    _soul: { handle: soulHandle, refreshPresence, refreshRecentKnowledge, refreshMemento,
             stationCount: () => _stations.size },
    // 日/夜壁纸测试接缝:apply 可注入 Date(mock 时间验 auto 档);生产路径 = wallStart 的分钟级重判
    _wall: { apply: applyWall, mode: wallMode, set: setWallMode, variantFor: wallVariantFor },
  };
  (window as unknown as { KarvyDesktop: typeof KarvyDesktop }).KarvyDesktop = KarvyDesktop;
})();

export {};
