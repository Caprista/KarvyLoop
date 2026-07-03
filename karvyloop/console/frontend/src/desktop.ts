/* desktop.ts — 桌面视图(第三视图,docs/51 P1 骨架)。
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
 */

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

  function t(key: string): string {
    const i18n = (window as unknown as { KarvyI18n?: I18n }).KarvyI18n;
    return i18n ? i18n.t(key) : key;
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

  // ---- ⚖便签的"常驻可瞟"保险(docs/51 §4.2;docs/46 铁律的桌面版)----
  function notifyH2A(): void {
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
    note.classList.remove("note-alert");
    void note.offsetWidth;                                   // 重触发动画
    note.classList.add("note-alert");
    setTimeout(() => note.classList.remove("note-alert"), 2800);
    const bubble = document.getElementById("karvy-bubble");  // 卡皮巴拉冒泡
    if (bubble) {
      const dots = bubble.querySelector(".karvy-bubble-dots");
      if (dots) dots.textContent = "⚖";
      bubble.classList.remove("hidden");
      setTimeout(() => bubble.classList.add("hidden"), 6000);
    }
  }

  // ---- 默认摆位:右侧一列便签(⚖最上,继承"⚖永远第一"),按实测高度依次堆 ----
  function computeNoteDefault(col: HTMLElement, prevBottom: number): Pos {
    const desk = deskEl();
    if (!desk) return { x: 12, y: prevBottom };
    const d = desk.getBoundingClientRect();
    const w = col.offsetWidth || 304;
    return { x: Math.max(12, d.width - w - 16), y: prevBottom };
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
    // 便签:存过用存的(clamp 进当前视口),没存过按默认右列依次堆
    let stackY = 12;
    noteEls().forEach((col) => {
      const k = noteKey(col);
      const saved = _store.notes[k];
      const pos = saved ? clampPos(col, saved.x, saved.y) : computeNoteDefault(col, stackY);
      applyPos(col, pos.x, pos.y);
      stackY = pos.y + (col.offsetHeight || 180) + 12;   // 只影响"没存过"的下一张默认位
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
  }

  function leave(): void {
    _entered = false;
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

  const KarvyDesktop = { enter, leave, notifyH2A, resetLayout };
  (window as unknown as { KarvyDesktop: typeof KarvyDesktop }).KarvyDesktop = KarvyDesktop;
})();

export {};
