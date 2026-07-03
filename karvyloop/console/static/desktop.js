(function() {
  "use strict";
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
    function t(key) {
      const i18n = window.KarvyI18n;
      return i18n ? i18n.t(key) : key;
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
    }
    function computeNoteDefault(col, prevBottom) {
      const desk = deskEl();
      if (!desk) return { x: 12, y: prevBottom };
      const d = desk.getBoundingClientRect();
      const w = col.offsetWidth || 304;
      return { x: Math.max(12, d.width - w - 16), y: prevBottom };
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
      let stackY = 12;
      noteEls().forEach((col) => {
        const k = noteKey(col);
        const saved = _store.notes[k];
        const pos = saved ? clampPos(col, saved.x, saved.y) : computeNoteDefault(col, stackY);
        applyPos(col, pos.x, pos.y);
        stackY = pos.y + (col.offsetHeight || 180) + 12;
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
    }
    function leave() {
      _entered = false;
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
    const KarvyDesktop = { enter, leave, notifyH2A, resetLayout };
    window.KarvyDesktop = KarvyDesktop;
  })();
})();
