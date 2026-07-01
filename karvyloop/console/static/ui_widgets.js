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
