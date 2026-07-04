(function() {
  "use strict";
  const SPRITE_URL = "/static/assets/karvy-capybara.png";
  const WIDTH = 441;
  const HEIGHT = 512;
  function shade(hex, f) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#777777";
    const n = parseInt(m[1], 16);
    const r = Math.max(0, Math.min(255, Math.round((n >> 16 & 255) * f)));
    const g = Math.max(0, Math.min(255, Math.round((n >> 8 & 255) * f)));
    const b = Math.max(0, Math.min(255, Math.round((n & 255) * f)));
    return "#" + (1 << 24 | r << 16 | g << 8 | b).toString(16).slice(1);
  }
  function normalizeAccent(accent) {
    const a = accent || "";
    if (!/^#?[0-9a-f]{6}$/i.test(a)) return KARVY_ACCENT;
    return a[0] === "#" ? a : "#" + a;
  }
  function buildPalette(accent) {
    const A = normalizeAccent(accent);
    return { A, a: shade(A, 0.72) };
  }
  const KARVY_ACCENT = "#8fc7e8";
  const ROLE_ACCENTS = [
    "#e07a5f",
    "#8e7cc3",
    "#6a994e",
    "#d4a373",
    "#457b9d",
    "#bc6c25",
    "#c76b8e",
    "#5f9ea0"
  ];
  function colorForRole(roleId) {
    if (!roleId || roleId === "karvy") return KARVY_ACCENT;
    let h = 0;
    for (let i = 0; i < roleId.length; i++) h = (h << 5) - h + roleId.charCodeAt(i) | 0;
    return ROLE_ACCENTS[Math.abs(h) % ROLE_ACCENTS.length];
  }
  const FRAMES = {};
  const STATES = ["idle", "working", "carry", "sleep", "happy"];
  function validateFrames() {
    return [];
  }
  const OVERLAY_PARTS = ["badge", "keys", "card", "zzz"];
  function createPet(opts) {
    const mount = opts.canvas;
    const accent = normalizeAccent(opts.accent);
    const root = document.createElement("span");
    root.className = "karvy-sprite";
    if (mount.id) root.id = mount.id;
    root.style.setProperty("--pet-accent", accent);
    root.style.setProperty("--pet-accent-dim", shade(accent, 0.72));
    const img = document.createElement("img");
    img.className = "karvy-sprite-img";
    img.src = SPRITE_URL;
    img.alt = "";
    img.setAttribute("aria-hidden", "true");
    img.draggable = false;
    root.appendChild(img);
    OVERLAY_PARTS.forEach((part) => {
      const el = document.createElement("span");
      el.className = "karvy-sprite-" + part;
      el.setAttribute("aria-hidden", "true");
      root.appendChild(el);
    });
    if (mount.parentNode) mount.parentNode.replaceChild(root, mount);
    let state = "idle";
    let destroyed = false;
    function render() {
      root.setAttribute("data-state", state);
    }
    function setState(s) {
      if (destroyed) return false;
      if (STATES.indexOf(s) < 0) return false;
      if (s === state) return true;
      state = s;
      render();
      return true;
    }
    render();
    return {
      setState,
      state: () => state,
      render,
      // destroy 只封状态机(和旧引擎"只停表不拆 DOM"同语义);DOM 的去留归调用方管
      destroy: () => {
        destroyed = true;
      }
    };
  }
  const KarvyPixelPet = {
    createPet,
    validateFrames,
    buildPalette,
    colorForRole,
    STATES,
    FRAMES,
    WIDTH,
    HEIGHT,
    KARVY_ACCENT,
    SPRITE_URL
  };
  if (typeof window !== "undefined") {
    window.KarvyPixelPet = KarvyPixelPet;
  }
  (function() {
    const LS_KEY = "karvyloop_desk.v1";
    const BASE_Z = 220;
    const HANDLE_MIN_W = 48;
    const HANDLE_MIN_H = 32;
    const KEY_STEP = 8;
    let _zTop = BASE_Z;
    let _entered = false;
    let _wired = false;
    let _suppressClick = null;
    let _store = { notes: {}, windows: {} };
    function t(key, vars) {
      const i18n = window.KarvyI18n;
      return i18n ? i18n.t(key, vars) : key;
    }
    function deskView() {
      return document.body.classList.contains("desk-view");
    }
    function loadStore() {
      const out = { notes: {}, windows: {} };
      try {
        const raw = localStorage.getItem(LS_KEY);
        if (!raw) return out;
        const j = JSON.parse(raw);
        const num = (v) => typeof v === "number" && isFinite(v);
        if (j && typeof j === "object") {
          const notes = j.notes || {};
          Object.keys(notes).forEach((k) => {
            const p = notes[k];
            if (p && num(p.x) && num(p.y)) out.notes[k] = { x: p.x, y: p.y };
          });
          const w = j.windows || {};
          if (w.chat && num(w.chat.x) && num(w.chat.y)) out.windows.chat = { x: w.chat.x, y: w.chat.y, min: !!w.chat.min };
          if (w.mgmt && num(w.mgmt.x) && num(w.mgmt.y)) out.windows.mgmt = { x: w.mgmt.x, y: w.mgmt.y };
        }
      } catch {
      }
      return out;
    }
    function saveStore() {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(_store));
      } catch {
      }
    }
    function deskEl() {
      return document.querySelector(".cockpit");
    }
    function noteEls() {
      return Array.from(document.querySelectorAll(".cockpit-grid .cockpit-col"));
    }
    function noteKey(col) {
      return Array.from(col.classList).find((c) => c.indexOf("col-") === 0) || "col";
    }
    function chatOverlay() {
      return document.getElementById("chat-modal");
    }
    function chatPanel() {
      return document.querySelector("#chat-modal .chat-panel");
    }
    function mgmtOverlay() {
      return document.getElementById("mgmt-modal");
    }
    function mgmtPanel() {
      return document.querySelector("#mgmt-modal .modal");
    }
    function getPos(el) {
      const x = parseFloat(el.dataset.deskX || "0");
      const y = parseFloat(el.dataset.deskY || "0");
      return { x: isFinite(x) ? x : 0, y: isFinite(y) ? y : 0 };
    }
    function applyPos(el, x, y) {
      el.dataset.deskX = String(Math.round(x));
      el.dataset.deskY = String(Math.round(y));
      el.style.transform = "translate3d(" + Math.round(x) + "px," + Math.round(y) + "px,0) rotate(var(--desk-tilt, 0deg))";
    }
    function clampPos(el, x, y) {
      const desk = deskEl();
      const par = el.offsetParent;
      if (!desk || !par) return { x, y };
      const d = desk.getBoundingClientRect();
      const p = par.getBoundingClientRect();
      if (!(d.width > 0) || !(d.height > 0)) return { x, y };
      const w = el.offsetWidth || HANDLE_MIN_W;
      const minX = d.left - p.left - Math.max(0, w - HANDLE_MIN_W);
      const maxX = Math.max(minX, d.right - p.left - HANDLE_MIN_W);
      const minY = d.top - p.top;
      const maxY = Math.max(minY, d.bottom - p.top - HANDLE_MIN_H);
      return { x: Math.min(Math.max(x, minX), maxX), y: Math.min(Math.max(y, minY), maxY) };
    }
    function persistPos(kind, pos) {
      if (kind === "chat") {
        _store.windows.chat = { x: pos.x, y: pos.y, min: !!(_store.windows.chat && _store.windows.chat.min) };
      } else if (kind === "mgmt") {
        _store.windows.mgmt = { x: pos.x, y: pos.y };
      } else {
        _store.notes[kind] = { x: pos.x, y: pos.y };
      }
      saveStore();
    }
    function zTarget(el) {
      if (el.classList.contains("chat-panel")) return chatOverlay() || el;
      if (el.classList.contains("modal")) return mgmtOverlay() || el;
      return el;
    }
    function focusEl(el) {
      _zTop += 1;
      zTarget(el).style.zIndex = String(_zTop);
      document.querySelectorAll(".desk-focused").forEach((n) => n.classList.remove("desk-focused"));
      el.classList.add("desk-focused");
    }
    function makeDraggable(el, handle, kind) {
      let dragging = false, moved = false, raf = 0;
      let sx = 0, sy = 0, ox = 0, oy = 0, nx = 0, ny = 0;
      handle.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        const tgt = e.target;
        if (tgt && tgt.closest && tgt.closest("button, select, input, textarea, a, [contenteditable]")) return;
        dragging = true;
        moved = false;
        sx = e.clientX;
        sy = e.clientY;
        const p = getPos(el);
        ox = p.x;
        oy = p.y;
        nx = ox;
        ny = oy;
        try {
          if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId);
        } catch {
        }
        handle.classList.add("dragging");
      });
      handle.addEventListener("pointermove", (e) => {
        if (!dragging) return;
        const dx = e.clientX - sx, dy = e.clientY - sy;
        if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
        moved = true;
        const c = clampPos(el, ox + dx, oy + dy);
        nx = c.x;
        ny = c.y;
        if (!raf && typeof requestAnimationFrame === "function") {
          raf = requestAnimationFrame(() => {
            raf = 0;
            applyPos(el, nx, ny);
          });
        } else if (typeof requestAnimationFrame !== "function") {
          applyPos(el, nx, ny);
        }
      });
      const finish = (e) => {
        if (!dragging) return;
        dragging = false;
        try {
          if (handle.releasePointerCapture) handle.releasePointerCapture(e.pointerId);
        } catch {
        }
        handle.classList.remove("dragging");
        if (raf && typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(raf);
          raf = 0;
        }
        if (moved) {
          applyPos(el, nx, ny);
          persistPos(kind, { x: nx, y: ny });
          _suppressClick = el;
          setTimeout(() => {
            if (_suppressClick === el) _suppressClick = null;
          }, 0);
        }
      };
      handle.addEventListener("pointerup", finish);
      handle.addEventListener("pointercancel", finish);
      el.addEventListener("click", (e) => {
        if (_suppressClick === el) {
          e.stopPropagation();
          e.preventDefault();
          _suppressClick = null;
        }
      }, true);
    }
    function wireHandleKeys(el, handle, kind) {
      handle.addEventListener("keydown", (e) => {
        if (!deskView()) return;
        if (e.key === "Escape") {
          if (kind === "chat" || kind === "mgmt") {
            e.preventDefault();
            minimizeWin(kind);
          }
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
    function dockEl() {
      return document.getElementById("desk-dock");
    }
    function renderDock() {
      const dock = dockEl();
      if (!dock || dock.childElementCount > 0) return;
      document.querySelectorAll(".sidebar .nav-item[data-panel]").forEach((src) => {
        if (src.disabled) return;
        const b = document.createElement("button");
        b.className = "dock-item nav-item";
        b.setAttribute("data-panel", src.getAttribute("data-panel") || "");
        const ico = src.querySelector(".nav-ico");
        b.textContent = ico && ico.textContent || "▫";
        const lbl = src.querySelector("[data-i18n]:not(.nav-ico)");
        if (lbl) b.setAttribute("data-i18n-tip", lbl.getAttribute("data-i18n") || "");
        dock.appendChild(b);
      });
      const tok = document.createElement("button");
      tok.className = "dock-item dock-tokens";
      tok.textContent = "💰";
      tok.setAttribute("data-i18n-tip", "cockpit.token_title");
      tok.addEventListener("click", () => {
        const m = document.getElementById("token-meter");
        if (m) m.click();
      });
      dock.appendChild(tok);
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
        else if (ov) {
          minimizeWin("chat");
        }
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
      dock.addEventListener("click", (e) => {
        const tgt = e.target;
        const item = tgt && tgt.closest ? tgt.closest(".dock-item[data-panel]") : null;
        if (!item) return;
        dock.querySelectorAll(".dock-item.dock-active").forEach((n) => n.classList.remove("dock-active"));
        item.classList.add("dock-active");
        restoreWin("mgmt");
      });
    }
    function updateDockIndicators() {
      const chatBtn = document.getElementById("desk-dock-win-chat");
      const mgmtBtn = document.getElementById("desk-dock-win-mgmt");
      const cOv = chatOverlay(), mOv = mgmtOverlay();
      if (chatBtn && cOv) {
        const min = cOv.classList.contains("desk-min");
        chatBtn.classList.toggle("is-min", min);
        chatBtn.classList.add("is-open");
        chatBtn.title = min ? t("desk.restore") : t("desk.min");
      }
      if (mgmtBtn && mOv) {
        const closed = mOv.classList.contains("hidden");
        const min = mOv.classList.contains("desk-min");
        mgmtBtn.classList.toggle("is-off", closed);
        mgmtBtn.classList.toggle("is-open", !closed);
        mgmtBtn.classList.toggle("is-min", !closed && min);
        const ttl = document.getElementById("mgmt-title");
        mgmtBtn.title = (ttl && ttl.textContent || "") + " — " + (min ? t("desk.restore") : t("desk.min"));
        if (closed) {
          const dock = dockEl();
          if (dock) dock.querySelectorAll(".dock-item.dock-active").forEach((n) => n.classList.remove("dock-active"));
        }
      }
    }
    function minimizeWin(kind) {
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
    function restoreWin(kind) {
      const ov = kind === "chat" ? chatOverlay() : mgmtOverlay();
      if (!ov) return;
      const wasMin = ov.classList.contains("desk-min");
      ov.classList.remove("desk-min");
      if (kind === "chat") {
        const p = chatPanel();
        if (p) {
          const pos = getPos(p);
          _store.windows.chat = { x: pos.x, y: pos.y, min: false };
          saveStore();
        }
        if (p) focusEl(p);
        if (wasMin) {
          const input = document.getElementById("chat-input");
          if (input) setTimeout(() => input.focus(), 30);
        }
      } else {
        ensureMgmtPos();
        const p = mgmtPanel();
        if (p) focusEl(p);
      }
      updateDockIndicators();
    }
    function ensureMgmtPos() {
      const ov = mgmtOverlay(), p = mgmtPanel(), desk = deskEl();
      if (!ov || !p || !desk || ov.classList.contains("hidden")) return;
      const saved = _store.windows.mgmt;
      if (saved) {
        const c2 = clampPos(p, saved.x, saved.y);
        applyPos(p, c2.x, c2.y);
        return;
      }
      const d = desk.getBoundingClientRect();
      const o = p.offsetParent;
      const po = o ? o.getBoundingClientRect() : { left: 0, top: 0 };
      if (!(d.width > 0)) return;
      const x = d.left - po.left + Math.max(24, (d.width - p.offsetWidth) / 2);
      const y = d.top - po.top + 48;
      const c = clampPos(p, x, y);
      applyPos(p, c.x, c.y);
    }
    const SLEEP_AFTER_MS = 30 * 60 * 1e3;
    const NOTE_CAP = 3;
    const RESULT_CAP = 140;
    let _soulOn = false;
    let _mascot = null;
    let _mascotState = "idle";
    let _mascotBusy = false;
    const _stations = /* @__PURE__ */ new Map();
    const _signedNotes = [];
    const _workcards = /* @__PURE__ */ new Map();
    let _soulWs = null;
    let _soulWsTimer = 0;
    let _soulWsDelay = 2e3;
    function cockpitEl() {
      return deskEl();
    }
    function reducedMotion() {
      try {
        return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      } catch {
        return false;
      }
    }
    function ensureSoulDom() {
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
      const fab = document.getElementById("chat-open");
      if (fab && !document.getElementById("desk-karvy-pixel")) {
        const cv = document.createElement("canvas");
        cv.id = "desk-karvy-pixel";
        fab.appendChild(cv);
      }
    }
    function ensureMascot() {
      const cv = document.getElementById("desk-karvy-pixel");
      if (!cv) return;
      if (!_mascot) _mascot = createPet({ canvas: cv, accent: KARVY_ACCENT });
    }
    function setMascotReal(state) {
      _mascotState = state;
      if (_mascot && !_mascotBusy) _mascot.setState(state);
    }
    function mascotHappy() {
      if (!_mascot || _mascotBusy) return;
      _mascotBusy = true;
      _mascot.setState("happy");
      setTimeout(() => {
        _mascotBusy = false;
        if (_mascot) _mascot.setState(_mascotState);
      }, 2200);
    }
    function stationVisible(row) {
      return row.role_id !== "karvy" && (row.status === "busy" || !!row.last_activity_ts);
    }
    function petStateFor(row) {
      if (row.status === "busy") return "working";
      const ts = (row.last_activity_ts || 0) * 1e3;
      return ts && Date.now() - ts < SLEEP_AFTER_MS ? "idle" : "sleep";
    }
    function upsertStation(row) {
      if (!row || !row.role_id) return;
      if (row.role_id === "karvy") {
        setMascotReal(row.status === "busy" ? "working" : "idle");
        return;
      }
      const wrap = document.getElementById("desk-stations");
      const bar = document.getElementById("desk-presence");
      if (!wrap || !bar) return;
      if (!stationVisible(row)) {
        const gone = _stations.get(row.role_id);
        if (gone) {
          gone.pet.destroy();
          gone.el.remove();
          _stations.delete(row.role_id);
        }
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
        const pet = createPet({ canvas: cv, accent: colorForRole(row.role_id) });
        st = { el, pet };
        _stations.set(row.role_id, st);
        el.addEventListener("click", () => {
          const lt = el.dataset.taskId || "";
          if (lt) jumpToTask(lt, el.dataset.taskIntent || "");
          else restoreWin("chat");
        });
      }
      const nameEl = st.el.querySelector(".station-name");
      if (nameEl) nameEl.textContent = row.display || row.role_id;
      st.el.setAttribute("aria-label", row.display || row.role_id);
      const state = petStateFor(row);
      st.pet.setState(state);
      st.el.dataset.petState = state;
      st.el.classList.toggle("is-busy", row.status === "busy");
      st.el.dataset.taskId = row.last_task && row.last_task.id || "";
      st.el.dataset.taskIntent = row.last_task && row.last_task.intent || "";
      const tip = row.status === "busy" && row.last_task ? t("desk.presence_doing", { intent: row.last_task.intent }) : state === "sleep" ? t("desk.presence_rest") : t("desk.presence_idle");
      st.el.setAttribute("data-tip", tip);
      bar.classList.remove("hidden");
    }
    async function refreshPresence() {
      const bar = document.getElementById("desk-presence");
      if (typeof fetch !== "function") {
        if (bar) bar.classList.add("hidden");
        return;
      }
      try {
        const r = await fetch("/api/roles/presence");
        if (!r.ok) throw new Error(String(r.status));
        const data = await r.json();
        const rows = data && data.roles || [];
        const seen = /* @__PURE__ */ new Set();
        rows.forEach((row) => {
          seen.add(row.role_id);
          upsertStation(row);
        });
        _stations.forEach((st, rid) => {
          if (!seen.has(rid)) {
            st.pet.destroy();
            st.el.remove();
            _stations.delete(rid);
          }
        });
        if (bar) bar.classList.toggle("hidden", !_stations.size && !_workcards.size);
      } catch {
        if (bar && !_stations.size && !_workcards.size) bar.classList.add("hidden");
      }
    }
    function jumpToTask(_taskId, intent) {
      const probe = (intent || "").slice(0, 64);
      const cards = document.querySelectorAll("#busy-list .task-card, #task-board .task-card");
      for (let i = 0; i < cards.length; i++) {
        const it = cards[i].querySelector(".task-intent");
        if (it && probe && (it.textContent || "").indexOf(probe) === 0) {
          cards[i].click();
          return;
        }
      }
      const note = document.querySelector(".cockpit-grid .col-intel");
      if (note) {
        if (note.classList.contains("col-collapsed")) note.classList.remove("col-collapsed");
        focusEl(note);
        note.classList.remove("note-alert");
        void note.offsetWidth;
        note.classList.add("note-alert");
        setTimeout(() => note.classList.remove("note-alert"), 2600);
      }
    }
    function spawnSignedNote(tk) {
      const desk = cockpitEl();
      if (!desk || !deskView()) return;
      const note = document.createElement("div");
      note.className = "desk-signed-note";
      const tilt = (Math.random() * 4 - 2).toFixed(2);
      note.style.setProperty("--note-tilt", tilt + "deg");
      const who = document.createElement("div");
      who.className = "signed-note-who";
      who.textContent = "✍ " + (tk.who || "?");
      const when = document.createElement("span");
      when.className = "signed-note-time";
      try {
        when.textContent = new Date((tk.finished || Date.now() / 1e3) * 1e3).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch {
        when.textContent = "";
      }
      who.appendChild(when);
      const body = document.createElement("div");
      body.className = "signed-note-body";
      const text = (tk.result || tk.intent || "").trim();
      body.textContent = text.length > RESULT_CAP ? text.slice(0, RESULT_CAP) + "…" : text;
      note.appendChild(who);
      note.appendChild(body);
      note.setAttribute("data-tip", t("desk.note_open"));
      note.addEventListener("click", () => jumpToTask(tk.id || "", tk.intent || ""));
      const d = desk.getBoundingClientRect();
      const baseX = d.width > 0 ? Math.max(24, d.width * 0.32) : 120;
      const baseY = d.height > 0 ? Math.max(24, d.height * 0.18) : 80;
      note.style.left = Math.round(baseX + Math.random() * 40 + _signedNotes.length * 26) + "px";
      note.style.top = Math.round(baseY + _signedNotes.length * 64 + Math.random() * 18) + "px";
      note.style.zIndex = String(++_zTop);
      desk.appendChild(note);
      _signedNotes.push(note);
      while (_signedNotes.length > NOTE_CAP) {
        const old = _signedNotes.shift();
        if (old) {
          old.classList.add("is-fading");
          setTimeout(() => old.remove(), 450);
        }
      }
    }
    function ensureWorkcard(tk) {
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
      _workcards.set(id, { el, chips: /* @__PURE__ */ new Map() });
    }
    function workcardStep(st) {
      const wc = _workcards.get(st.task_id || "");
      if (!wc) return;
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
        wc.el.querySelector(".workcard-chips").appendChild(chip);
      }
      const failed = st.status === "failed";
      chip.classList.toggle("failed", failed);
      chip.classList.toggle("done", !failed);
      const mk = chip.querySelector(".chip-mark");
      if (mk) mk.textContent = failed ? "✗" : "✓";
    }
    function finishWorkcard(taskId, ok) {
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
      }, ok ? 6e3 : 9e3);
    }
    function soulHandle(msg) {
      if (!msg || !deskView()) return;
      const p = msg.payload || {};
      if (msg.type === "role_presence") {
        upsertStation(p);
      } else if (msg.type === "task_status") {
        const tk = p;
        if (tk.status === "running" && tk.role === "group") ensureWorkcard(tk);
        else if (tk.status === "done") {
          spawnSignedNote(tk);
          finishWorkcard(tk.id || "", true);
        } else if (tk.status === "error") finishWorkcard(tk.id || "", false);
      } else if (msg.type === "task_step") {
        workcardStep(p);
      } else if (msg.type === "h2a_envelope") {
        mascotHappy();
      }
    }
    function soulConnect() {
      if (typeof WebSocket !== "function") return;
      if (_soulWs && (_soulWs.readyState === 0 || _soulWs.readyState === 1)) return;
      try {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(proto + "//" + location.host + "/ws");
        _soulWs = ws;
        ws.onmessage = (ev) => {
          try {
            soulHandle(JSON.parse(String(ev.data)));
          } catch {
          }
        };
        ws.onopen = () => {
          _soulWsDelay = 2e3;
        };
        ws.onerror = () => {
        };
        ws.onclose = () => {
          _soulWs = null;
          if (_soulOn) {
            _soulWsTimer = window.setTimeout(soulConnect, _soulWsDelay);
            _soulWsDelay = Math.min(_soulWsDelay * 2, 3e4);
          }
        };
      } catch {
        _soulWs = null;
      }
    }
    async function seedWorkcards() {
      if (typeof fetch !== "function") return;
      try {
        const r = await fetch("/api/tasks");
        if (!r.ok) return;
        const data = await r.json();
        (data && data.tasks || []).forEach((tk) => {
          if (tk && tk.status === "running" && tk.role === "group") ensureWorkcard(tk);
        });
      } catch {
      }
    }
    const RECENT_KNOWLEDGE_CAP = 3;
    const RECENT_CONTENT_CAP = 120;
    async function refreshRecentKnowledge() {
      const desk = cockpitEl();
      if (!desk || typeof fetch !== "function") return;
      let items = [];
      try {
        const r = await fetch("/api/memory/recent?limit=" + RECENT_KNOWLEDGE_CAP);
        if (!r.ok) throw new Error(String(r.status));
        const data = await r.json();
        items = (data && data.items || []).slice(0, RECENT_KNOWLEDGE_CAP);
      } catch {
        items = [];
      }
      let box = document.getElementById("desk-recent-knowledge");
      if (!items.length) {
        if (box) box.remove();
        return;
      }
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
    async function refreshMemento() {
      const desk = cockpitEl();
      if (!desk || typeof fetch !== "function") return;
      let m = null;
      try {
        const r = await fetch("/api/desk/memento");
        if (!r.ok) throw new Error(String(r.status));
        m = await r.json();
      } catch {
        m = null;
      }
      const num = (k) => {
        const v = m ? m[k] : 0;
        return typeof v === "number" && isFinite(v) ? v : 0;
      };
      const tasks = num("tasks_done"), skills = num("skills_new"), decisions = num("decisions"), tokens = num("tokens_total");
      let tile = document.getElementById("desk-memento");
      if (!m || tasks + skills + decisions <= 0) {
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
      const wk = m.week_label && String(m.week_label) || "";
      const head = document.createElement("div");
      head.className = "desk-memento-head";
      head.textContent = "🏅 " + t("desk.memento_title") + (wk ? " · " + wk : "");
      tile.appendChild(head);
      const stats = document.createElement("div");
      stats.className = "desk-memento-stats";
      const chip = (icon, n, key) => {
        if (n <= 0) return;
        const c = document.createElement("span");
        c.className = "desk-memento-chip";
        c.textContent = icon + " " + t(key, { n });
        stats.appendChild(c);
      };
      chip("✅", tasks, "desk.memento_tasks");
      chip("🧬", skills, "desk.memento_skills");
      chip("⚖", decisions, "desk.memento_decisions");
      if (tokens > 0) {
        const c = document.createElement("span");
        c.className = "desk-memento-chip desk-memento-tokens";
        c.textContent = "🔢 " + t("desk.memento_tokens", { n: tokens > 1e3 ? (tokens / 1e3).toFixed(1) + "k" : String(tokens) });
        stats.appendChild(c);
      }
      tile.appendChild(stats);
    }
    function openMemoryPanel() {
      const nav = document.querySelector('.sidebar .nav-item[data-panel="memory"]') || document.querySelector('.dock-item[data-panel="memory"]');
      if (nav) nav.click();
    }
    function enterSoul() {
      _soulOn = true;
      ensureSoulDom();
      ensureMascot();
      void refreshPresence();
      void seedWorkcards();
      void refreshRecentKnowledge();
      void refreshMemento();
      soulConnect();
    }
    function leaveSoul() {
      _soulOn = false;
      if (_soulWsTimer) {
        clearTimeout(_soulWsTimer);
        _soulWsTimer = 0;
      }
      if (_soulWs) {
        try {
          _soulWs.close();
        } catch {
        }
        _soulWs = null;
      }
      if (_mascot) {
        _mascot.destroy();
        _mascot = null;
      }
      _mascotBusy = false;
      _mascotState = "idle";
      _stations.forEach((st) => {
        st.pet.destroy();
        st.el.remove();
      });
      _stations.clear();
      _workcards.forEach((wc) => wc.el.remove());
      _workcards.clear();
      _signedNotes.forEach((n) => n.remove());
      _signedNotes.length = 0;
      const cv = document.getElementById("desk-karvy-pixel");
      if (cv) cv.remove();
      const rk = document.getElementById("desk-recent-knowledge");
      if (rk) rk.remove();
      const mem = document.getElementById("desk-memento");
      if (mem) mem.remove();
      const bar = document.getElementById("desk-presence");
      if (bar) bar.classList.add("hidden");
      const box = document.getElementById("desk-workcards");
      if (box) box.classList.add("hidden");
      const actor = document.getElementById("desk-carry-actor");
      if (actor) actor.remove();
    }
    let _carrying = false;
    function playCarry(note, onArrive) {
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
      const pet = createPet({ canvas: acv, accent: KARVY_ACCENT });
      pet.setState("carry");
      actor.style.left = Math.round(from.left) + "px";
      actor.style.top = Math.round(from.top) + "px";
      document.body.appendChild(actor);
      cv.classList.add("is-away");
      const dx = Math.round(to.left + Math.max(0, to.width / 2) - from.left);
      const dy = Math.round(to.top + Math.max(0, to.height) - 40 - from.top);
      const cleanup = () => {
        pet.destroy();
        actor.remove();
        cv.classList.remove("is-away");
        _carrying = false;
        _mascotBusy = false;
        if (_mascot) _mascot.setState(_mascotState);
        onArrive();
      };
      void actor.offsetWidth;
      actor.classList.add("is-walking");
      actor.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
      let done = false;
      const finish = () => {
        if (!done) {
          done = true;
          cleanup();
        }
      };
      actor.addEventListener("transitionend", finish);
      setTimeout(finish, 2e3);
      return true;
    }
    function notifyH2A() {
      if (!deskView()) return;
      const note = document.querySelector(".cockpit-grid .col-decide");
      if (!note) return;
      if (note.classList.contains("col-collapsed")) {
        note.classList.remove("col-collapsed");
        try {
          localStorage.setItem("karvy.rail.col-decide", "0");
        } catch {
        }
      }
      focusEl(note);
      const pos = clampPos(note, getPos(note).x, getPos(note).y);
      applyPos(note, pos.x, pos.y);
      const flash = () => {
        note.classList.remove("note-alert");
        void note.offsetWidth;
        note.classList.add("note-alert");
        setTimeout(() => note.classList.remove("note-alert"), 2800);
        const bubble = document.getElementById("karvy-bubble");
        if (bubble) {
          const dots = bubble.querySelector(".karvy-bubble-dots");
          if (dots) dots.textContent = "⚖";
          bubble.classList.remove("hidden");
          setTimeout(() => bubble.classList.add("hidden"), 6e3);
        }
      };
      if (!playCarry(note, flash)) flash();
    }
    const KARVY_ZONE = { w: 220, h: 200 };
    function computeNoteDefault(col, idx, colBottoms) {
      const desk = deskEl();
      if (!desk) return { x: 12, y: 16 };
      const d = desk.getBoundingClientRect();
      const w = col.offsetWidth || 304;
      const h = col.offsetHeight || 180;
      const lane = Math.floor(idx / 2);
      const x = Math.max(12, d.width - (lane + 1) * (w + 24));
      const laneStart = lane === 0 ? 16 : 44;
      let y = colBottoms[lane] !== void 0 ? colBottoms[lane] : laneStart;
      if (lane === 0 && y + h > d.height - KARVY_ZONE.h && d.width - w - 24 < d.width - KARVY_ZONE.w) {
        y = Math.max(16, d.height - KARVY_ZONE.h - h - 12);
      }
      colBottoms[lane] = y + h + 14;
      return { x, y };
    }
    function wireAll() {
      if (_wired) return;
      _wired = true;
      noteEls().forEach((col) => {
        const head = col.querySelector(".col-head");
        if (!head) return;
        makeDraggable(col, head, noteKey(col));
        wireHandleKeys(col, head, noteKey(col));
      });
      const cp = chatPanel(), ch = document.querySelector("#chat-modal .chat-panel-head");
      if (cp && ch) {
        makeDraggable(cp, ch, "chat");
        wireHandleKeys(cp, ch, "chat");
      }
      const mp = mgmtPanel(), mh = document.querySelector("#mgmt-modal .modal-head");
      if (mp && mh) {
        makeDraggable(mp, mh, "mgmt");
        wireHandleKeys(mp, mh, "mgmt");
      }
    }
    function handles() {
      const out = [];
      noteEls().forEach((c) => {
        const h = c.querySelector(".col-head");
        if (h) out.push(h);
      });
      const ch = document.querySelector("#chat-modal .chat-panel-head");
      if (ch) out.push(ch);
      const mh = document.querySelector("#mgmt-modal .modal-head");
      if (mh) out.push(mh);
      return out;
    }
    function enter() {
      const desk = deskEl();
      if (!desk) return;
      renderDock();
      wireAll();
      _store = loadStore();
      _entered = true;
      _zTop = BASE_Z;
      const colBottoms = [];
      noteEls().forEach((col, idx) => {
        const k = noteKey(col);
        const saved = _store.notes[k];
        const pos = saved ? clampPos(col, saved.x, saved.y) : computeNoteDefault(col, idx, colBottoms);
        applyPos(col, pos.x, pos.y);
        col.style.zIndex = String(++_zTop);
      });
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
      const mOv = mgmtOverlay();
      if (mOv) mOv.style.zIndex = String(++_zTop);
      ensureMgmtPos();
      handles().forEach((h) => h.setAttribute("tabindex", "0"));
      const cx = document.getElementById("chat-modal-close");
      if (cx) {
        cx.setAttribute("title", t("desk.min"));
        cx.setAttribute("aria-label", t("desk.min"));
      }
      updateDockIndicators();
      enterSoul();
    }
    function leave() {
      _entered = false;
      leaveSoul();
      noteEls().forEach((col) => {
        col.style.transform = "";
        col.style.zIndex = "";
        col.classList.remove("note-alert", "desk-focused");
      });
      const cp = chatPanel();
      if (cp) {
        cp.style.transform = "";
        cp.classList.remove("desk-focused");
      }
      const mp = mgmtPanel();
      if (mp) {
        mp.style.transform = "";
        mp.classList.remove("desk-focused");
      }
      const cOv = chatOverlay();
      if (cOv) {
        cOv.classList.remove("desk-min");
        cOv.style.zIndex = "";
      }
      const mOv = mgmtOverlay();
      if (mOv) {
        mOv.classList.remove("desk-min");
        mOv.style.zIndex = "";
      }
      handles().forEach((h) => h.removeAttribute("tabindex"));
      const cx = document.getElementById("chat-modal-close");
      if (cx) {
        cx.setAttribute("title", "");
        cx.setAttribute("aria-label", "close");
      }
    }
    function resetLayout() {
      try {
        if (!window.confirm(t("desk.reset_confirm"))) return;
      } catch {
      }
      try {
        localStorage.removeItem(LS_KEY);
      } catch {
      }
      _store = { notes: {}, windows: {} };
      if (deskView()) enter();
    }
    document.addEventListener("click", (e) => {
      if (!deskView()) return;
      const tgt = e.target;
      if (!tgt || !tgt.closest) return;
      if (tgt.closest("#chat-modal-close")) {
        e.preventDefault();
        e.stopPropagation();
        minimizeWin("chat");
        return;
      }
      if (tgt.closest("#chat-open")) restoreWin("chat");
      if (tgt.closest("#mgmt-min")) {
        e.preventDefault();
        e.stopPropagation();
        minimizeWin("mgmt");
      }
    }, true);
    document.addEventListener("pointerdown", (e) => {
      if (!deskView()) return;
      const tgt = e.target;
      if (!tgt || !tgt.closest) return;
      const box = tgt.closest(".cockpit-grid .cockpit-col, #chat-modal .chat-panel, #mgmt-modal .modal");
      if (box) focusEl(box);
    }, true);
    function observeOverlays() {
      if (typeof MutationObserver !== "function") return;
      const watch = (el, onChange) => {
        if (!el) return;
        new MutationObserver(onChange).observe(el, { attributes: true, attributeFilter: ["class"] });
      };
      const mOv0 = mgmtOverlay();
      let mgmtWasHidden = !!(mOv0 && mOv0.classList.contains("hidden"));
      watch(mOv0, () => {
        const ov = mgmtOverlay();
        if (!ov) return;
        const hid = ov.classList.contains("hidden");
        const reopened = mgmtWasHidden && !hid;
        mgmtWasHidden = hid;
        if (!deskView()) return;
        if (reopened) {
          if (ov.classList.contains("desk-min")) ov.classList.remove("desk-min");
          ensureMgmtPos();
          const p = mgmtPanel();
          if (p) focusEl(p);
        }
        updateDockIndicators();
      });
      watch(chatOverlay(), () => {
        if (deskView()) updateDockIndicators();
      });
    }
    function injectMgmtMin() {
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
    let _rszT = 0;
    window.addEventListener("resize", () => {
      if (!_entered || !deskView()) return;
      if (_rszT) clearTimeout(_rszT);
      _rszT = window.setTimeout(() => {
        _rszT = 0;
        noteEls().forEach((col) => {
          const p = getPos(col);
          const c = clampPos(col, p.x, p.y);
          applyPos(col, c.x, c.y);
        });
        const cp = chatPanel();
        if (cp) {
          const p = getPos(cp);
          const c = clampPos(cp, p.x, p.y);
          applyPos(cp, c.x, c.y);
        }
        const mp = mgmtPanel();
        if (mp && mgmtOverlay() && !mgmtOverlay().classList.contains("hidden")) {
          const p = getPos(mp);
          const c = clampPos(mp, p.x, p.y);
          applyPos(mp, c.x, c.y);
        }
      }, 120);
    });
    renderDock();
    injectMgmtMin();
    observeOverlays();
    const KarvyDesktop = {
      enter,
      leave,
      notifyH2A,
      resetLayout,
      // P1.5 测试接缝(smoke/Playwright 喂真实事件形状,不开真 socket;生产路径 = soulConnect 的 onmessage)
      _soul: {
        handle: soulHandle,
        refreshPresence,
        refreshRecentKnowledge,
        refreshMemento,
        stationCount: () => _stations.size
      }
    };
    window.KarvyDesktop = KarvyDesktop;
  })();
})();
