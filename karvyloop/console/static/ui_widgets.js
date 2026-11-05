var KarvyWidgetsBundle = (function(exports) {
  "use strict";
  const el = window.KarvyDom.el;
  function transferList(opts) {
    const items = opts.items || [];
    const byId = {};
    items.forEach((it) => {
      byId[it.id] = it;
    });
    let sel = (opts.selected || []).filter((id) => byId[id]);
    let q = "";
    const leftHead = el("div", { class: "xfer-head" });
    const rightHead = el("div", { class: "xfer-head" });
    const leftList = el("div", { class: "xfer-list" });
    const rightList = el("div", { class: "xfer-list" });
    const row = (it, selected) => el(
      "div",
      {
        class: "xfer-item" + (selected ? " on" : ""),
        title: it.id,
        onclick: () => {
          if (selected) sel = sel.filter((x) => x !== it.id);
          else sel.push(it.id);
          paint();
        }
      },
      el("span", { class: "xfer-mark", text: selected ? "−" : "+" }),
      el("span", { class: "xfer-label", text: it.label })
    );
    function paint() {
      const ss = new Set(sel);
      const avail = items.filter((it) => !ss.has(it.id) && (!q || (it.label + " " + it.id).toLowerCase().includes(q)));
      leftHead.textContent = (opts.titleLeft || "可选") + " (" + avail.length + ")";
      rightHead.textContent = (opts.titleRight || "已选") + " (" + sel.length + ")";
      leftList.innerHTML = "";
      rightList.innerHTML = "";
      if (!avail.length) leftList.appendChild(el("div", { class: "xfer-empty", text: "—" }));
      else avail.forEach((it) => leftList.appendChild(row(it, false)));
      if (!sel.length) rightList.appendChild(el("div", { class: "xfer-empty", text: "—" }));
      else sel.forEach((id) => rightList.appendChild(row(byId[id], true)));
    }
    const search = el("input", {
      class: "xfer-search",
      type: "text",
      placeholder: opts.searchPh || "搜索",
      oninput: (e) => {
        q = (e.target.value || "").toLowerCase();
        paint();
      }
    });
    paint();
    const wrap = el(
      "div",
      { class: "xfer" },
      el("div", { class: "xfer-pane" }, leftHead, search, leftList),
      el("div", { class: "xfer-arrow", text: "⇄" }),
      el("div", { class: "xfer-pane" }, rightHead, rightList)
    );
    return { el: wrap, getSelected: () => sel.slice() };
  }
  function pagedList(opts) {
    const pageSize = opts.pageSize || 8;
    let q = "", page = 0;
    const itemsBox = el("div", { class: "paged-items" });
    const nav = el("div", { class: "paged-nav" });
    const filtered = () => !q ? opts.items : opts.items.filter((it) => (opts.searchOf(it) || "").toLowerCase().includes(q));
    function paint() {
      const fs = filtered();
      const pages = Math.max(1, Math.ceil(fs.length / pageSize));
      if (page >= pages) page = pages - 1;
      if (page < 0) page = 0;
      itemsBox.innerHTML = "";
      if (!fs.length) itemsBox.appendChild(el("div", { class: "mgmt-empty", text: opts.emptyText || "—" }));
      else fs.slice(page * pageSize, page * pageSize + pageSize).forEach((it) => itemsBox.appendChild(opts.renderItem(it)));
      nav.innerHTML = "";
      if (pages > 1) {
        nav.appendChild(el("button", {
          class: "paged-btn",
          text: "‹",
          onclick: () => {
            if (page > 0) {
              page--;
              paint();
            }
          }
        }));
        nav.appendChild(el("span", { class: "paged-info", text: page + 1 + " / " + pages }));
        nav.appendChild(el("button", {
          class: "paged-btn",
          text: "›",
          onclick: () => {
            if (page < pages - 1) {
              page++;
              paint();
            }
          }
        }));
      }
    }
    const search = el("input", {
      class: "paged-search",
      type: "text",
      placeholder: opts.searchPh || "搜索",
      oninput: (e) => {
        q = (e.target.value || "").toLowerCase();
        page = 0;
        paint();
      }
    });
    paint();
    return el("div", { class: "paged" }, search, itemsBox, nav);
  }
  const KarvyWidgets = { transferList, pagedList };
  window.KarvyWidgets = KarvyWidgets;
  exports.KarvyWidgets = KarvyWidgets;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});

/* ==== 全局自绘下拉 ============================================================
 * 把所有原生 <select> 换成项目自绘面板(不用浏览器原生弹层),同时保留原有语义:
 *   - 原生 select 仍留在 DOM 里(1px 透明,不可点),value / name / FormData / form.elements 全部照旧
 *   - 选中后派发 input + change 事件(drawflow 的节点值同步监听 input)
 *   - 动态插入的 select(各管理面板 / 工作流画布)由 MutationObserver 自动接管
 * 结构:.kv-select > select.kv-select-native + button.kv-select-btn > span.kv-select-label + span.kv-select-arrow
 * 弹层:body 下唯一的 #kv-select-pop(position:fixed,不被 modal 的 overflow 裁剪)
 * ========================================================================== */
(function () {
  "use strict";
  if (window.__kvSelectInit) return;
  window.__kvSelectInit = true;

  var POP_MAX_H = 280;
  var seq = 0;
  var active = null;
  var pop = null;

  function make(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function isOpen(st) {
    return !!active && active === st;
  }

  function ensurePop() {
    if (pop && document.body.contains(pop)) return pop;
    pop = make("div", "kv-select-pop hidden");
    pop.id = "kv-select-pop";
    pop.setAttribute("role", "listbox");
    pop.addEventListener("click", function (e) {
      var b = e.target && e.target.closest ? e.target.closest(".kv-select-opt") : null;
      if (!b || !active || b.disabled) return;
      choose(Number(b.getAttribute("data-idx")));
    });
    // 点在面板留白(非选项)上时收起,避免挡住下面的控件
    pop.addEventListener("pointerdown", function (e) {
      if (e.target && e.target.closest && e.target.closest(".kv-select-opt")) return;
      close();
    });
    document.body.appendChild(pop);
    return pop;
  }

  function sync(st) {
    var sel = st.sel;
    var opt = sel.options[sel.selectedIndex];
    var text = opt ? opt.textContent : "";
    if (st.label.textContent !== text) st.label.textContent = text || "\u00a0";
    if (st.btn.disabled !== !!sel.disabled) st.btn.disabled = !!sel.disabled;
    var hide = !!sel.hidden || (sel.style && sel.style.display === "none");
    if (st.wrap.hidden !== hide) st.wrap.hidden = hide;
    if (hide && isOpen(st)) close();
  }

  function buildOptions(st) {
    pop.textContent = "";
    var opts = st.sel.options;
    for (var i = 0; i < opts.length; i++) {
      var o = opts[i];
      var b = make("button", "kv-select-opt", o.textContent);
      b.type = "button";
      b.id = st.uid + "-o" + i;
      b.setAttribute("role", "option");
      b.setAttribute("data-idx", String(i));
      if (o.disabled) {
        b.disabled = true;
        b.setAttribute("aria-disabled", "true");
      }
      if (i === st.sel.selectedIndex) b.setAttribute("aria-selected", "true");
      pop.appendChild(b);
    }
    st.idx = st.sel.selectedIndex;
    paintActive(st);
  }

  function paintActive(st) {
    if (!pop) return;
    var nodes = pop.children;
    for (var i = 0; i < nodes.length; i++) {
      if (i === st.idx) nodes[i].classList.add("is-active");
      else nodes[i].classList.remove("is-active");
    }
    var cur = nodes[st.idx];
    if (!cur) return;
    st.btn.setAttribute("aria-activedescendant", cur.id);
    var top = cur.offsetTop;
    var h = cur.offsetHeight;
    if (top < pop.scrollTop) pop.scrollTop = top - 4;
    else if (top + h > pop.scrollTop + pop.clientHeight) pop.scrollTop = top + h - pop.clientHeight + 4;
  }

  function move(st, delta) {
    var opts = st.sel.options;
    var n = opts.length;
    if (!n) return;
    var i = st.idx;
    for (var k = 0; k < n; k++) {
      i = (i + delta + n) % n;
      if (!opts[i].disabled) {
        st.idx = i;
        break;
      }
    }
    paintActive(st);
  }

  function position(st) {
    var r = st.btn.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var w = Math.min(Math.max(r.width, 150), vw - 16);
    var below = vh - r.bottom - 8;
    var above = r.top - 8;
    pop.style.width = Math.round(w) + "px";
    pop.style.maxHeight = Math.round(Math.max(96, Math.min(POP_MAX_H, Math.max(below, above)))) + "px";
    var h = pop.offsetHeight;
    var top = (h > below && above > below) ? r.top - 6 - h : r.bottom + 6;
    pop.style.left = Math.round(Math.max(8, Math.min(r.left, vw - w - 8))) + "px";
    pop.style.top = Math.round(Math.max(8, top)) + "px";
  }

  function open(st) {
    ensurePop();
    if (active && active !== st) close();
    if (!st.sel.options.length) return;
    active = st;
    st.btn.setAttribute("aria-expanded", "true");
    pop.setAttribute("aria-labelledby", st.btn.id);
    pop.classList.remove("hidden");
    buildOptions(st);
    position(st);
  }

  function close() {
    var st = active;
    active = null;
    if (pop) {
      pop.classList.add("hidden");
      pop.textContent = "";
    }
    if (!st) return;
    st.idx = -1;
    st.btn.setAttribute("aria-expanded", "false");
    st.btn.removeAttribute("aria-activedescendant");
  }

  function choose(idx) {
    var st = active;
    if (!st || !(idx >= 0)) return;
    var sel = st.sel;
    close();
    if (sel.selectedIndex !== idx) {
      sel.selectedIndex = idx;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    sync(st);
    try {
      st.btn.focus({ preventScroll: true });
    } catch (e) {
      st.btn.focus();
    }
  }

  function bind(st) {
    st.btn.addEventListener("click", function (e) {
      e.preventDefault();
      if (isOpen(st)) close();
      else open(st);
    });
    st.btn.addEventListener("keydown", function (e) {
      var k = e.key;
      if (!isOpen(st)) {
        if (k === "ArrowDown" || k === "ArrowUp" || k === "Enter" || k === " ") {
          e.preventDefault();
          open(st);
          if (k === "ArrowUp") move(st, -1);
        }
        return;
      }
      if (k === "ArrowDown") {
        e.preventDefault();
        move(st, 1);
      } else if (k === "ArrowUp") {
        e.preventDefault();
        move(st, -1);
      } else if (k === "Home") {
        e.preventDefault();
        st.idx = -1;
        move(st, 1);
      } else if (k === "End") {
        e.preventDefault();
        st.idx = 0;
        move(st, -1);
      } else if (k === "Enter" || k === " ") {
        e.preventDefault();
        if (st.idx >= 0) choose(st.idx);
      } else if (k === "Escape") {
        e.preventDefault();
        close();
      } else if (k === "Tab") {
        close();
      }
    });
    // 点 <label> 文字时浏览器会把 click 转给 label 内的 select:拦下原生弹层,改开自绘面板
    st.sel.addEventListener("click", function (e) {
      e.preventDefault();
      if (st.sel.disabled) return;
      if (isOpen(st)) close();
      else open(st);
    });
  }

  // 代码里 sel.value = x 直接赋值时同步按钮文案(不影响内部选中态 / FormData)
  function hookValue(st) {
    var sel = st.sel;
    var proto = window.HTMLSelectElement && window.HTMLSelectElement.prototype;
    if (!proto) return;
    try {
      var vd = Object.getOwnPropertyDescriptor(proto, "value");
      if (vd && vd.get && vd.set) {
        Object.defineProperty(sel, "value", {
          configurable: true,
          get: function () { return vd.get.call(sel); },
          set: function (v) { vd.set.call(sel, v); sync(st); }
        });
      }
      var sd = Object.getOwnPropertyDescriptor(proto, "selectedIndex");
      if (sd && sd.get && sd.set) {
        Object.defineProperty(sel, "selectedIndex", {
          configurable: true,
          get: function () { return sd.get.call(sel); },
          set: function (v) { sd.set.call(sel, v); sync(st); }
        });
      }
    } catch (e) { /* 老引擎不支持时退化为打开时刷新 */ }
  }

  function enhance(sel) {
    if (!sel || sel.tagName !== "SELECT") return null;
    if (sel.__kvSt) return sel.__kvSt;
    if (sel.multiple || sel.size > 1) return null;
    var parent = sel.parentNode;
    if (!parent) return null;

    var uid = "kvs" + ++seq;
    var wrap = make("div", "kv-select");
    var btn = make("button", "kv-select-btn");
    btn.type = "button";
    btn.id = uid + "-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    var label = make("span", "kv-select-label");
    btn.appendChild(label);
    btn.appendChild(make("span", "kv-select-arrow", "\u25be"));

    var st = { sel: sel, wrap: wrap, btn: btn, label: label, idx: -1, uid: uid, mo: null };
    sel.__kvSt = st;

    parent.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(btn);

    sel.classList.add("kv-select-native");
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");

    bind(st);
    hookValue(st);

    st.mo = new MutationObserver(function () { sync(st); });
    st.mo.observe(sel, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled", "hidden", "style", "class"]
    });

    sync(st);
    return st;
  }

  function scan(root) {
    if (!root) return;
    if (root.nodeType === 1) {
      if (root.tagName === "SELECT") enhance(root);
      if (root.querySelectorAll) {
        var inner = root.querySelectorAll("select");
        for (var i = 0; i < inner.length; i++) enhance(inner[i]);
      }
    } else if (root.nodeType === 11 && root.querySelectorAll) {
      var frag = root.querySelectorAll("select");
      for (var j = 0; j < frag.length; j++) enhance(frag[j]);
    }
  }

  var bodyMo = new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      if (r.type !== "childList") continue;
      var st = r.target.__kvSt;
      if (st) sync(st);
      for (var j = 0; j < r.addedNodes.length; j++) scan(r.addedNodes[j]);
    }
  });

  document.addEventListener("pointerdown", function (e) {
    if (!active) return;
    var t = e.target;
    if (pop && pop.contains(t)) return;
    if (active.btn.contains(t)) return;
    close();
  }, true);

  // 焦点可能落在隐藏的原生 select 上(点 label 转发),所以 Esc/Tab 在文档层兜底
  document.addEventListener("keydown", function (e) {
    if (!active) return;
    if (e.key !== "Escape" && e.key !== "Tab") return;
    var st = active;
    close();
    if (e.key === "Escape") {
      e.preventDefault();
      try {
        st.btn.focus({ preventScroll: true });
      } catch (err) {
        st.btn.focus();
      }
    }
  }, true);

  document.addEventListener("scroll", function (e) {
    if (!active) return;
    if (e.target && e.target.nodeType === 1 && pop && pop.contains(e.target)) return;
    close();
  }, true);

  window.addEventListener("resize", close);

  function ready() {
    scan(document.body);
    bodyMo.observe(document.body, { childList: true, subtree: true });
  }
  if (document.body) ready();
  else document.addEventListener("DOMContentLoaded", ready);
})();
