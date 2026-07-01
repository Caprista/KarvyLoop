/* ui_widgets.ts — 共享 UI 部件(Hardy:atom/role 多了以后 chip 气泡 + 同页列表交互差)。
 *  - transferList:穿梭框(左=可选带搜索,右=已选;点一下移过去/移回来)—— 替代标签气泡多选。
 *  - pagedList:搜索 + 分页的列表容器 —— 列表长了不用一直下拉。
 * 只用 dom 全局。暴露 window.KarvyWidgets.{ transferList, pagedList }。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom { el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement }
const el = (window as unknown as { KarvyDom: Dom }).KarvyDom.el;

interface XferItem { id: string; label: string }
interface XferOpts {
  items: XferItem[]; selected?: string[];
  titleLeft?: string; titleRight?: string; searchPh?: string;
}
// 穿梭框:左可选(搜)→ 点击移到右已选;右点击移回左。返回 { el, getSelected() }。
function transferList(opts: XferOpts): { el: HTMLElement; getSelected: () => string[] } {
  const items = opts.items || [];
  const byId: Record<string, XferItem> = {};
  items.forEach((it) => { byId[it.id] = it; });
  let sel = (opts.selected || []).filter((id) => byId[id]);   // 保留传入顺序,丢未知
  let q = "";
  const leftHead = el("div", { class: "xfer-head" });
  const rightHead = el("div", { class: "xfer-head" });
  const leftList = el("div", { class: "xfer-list" });
  const rightList = el("div", { class: "xfer-list" });

  const row = (it: XferItem, selected: boolean): HTMLElement =>
    el("div", { class: "xfer-item" + (selected ? " on" : ""), title: it.id,
      onclick: () => {
        if (selected) sel = sel.filter((x) => x !== it.id);
        else sel.push(it.id);
        paint();
      } },
      el("span", { class: "xfer-mark", text: selected ? "−" : "+" }),
      el("span", { class: "xfer-label", text: it.label }));

  function paint(): void {
    const ss = new Set(sel);
    const avail = items.filter((it) => !ss.has(it.id) &&
      (!q || (it.label + " " + it.id).toLowerCase().includes(q)));
    leftHead.textContent = (opts.titleLeft || "可选") + " (" + avail.length + ")";
    rightHead.textContent = (opts.titleRight || "已选") + " (" + sel.length + ")";
    leftList.innerHTML = ""; rightList.innerHTML = "";
    if (!avail.length) leftList.appendChild(el("div", { class: "xfer-empty", text: "—" }));
    else avail.forEach((it) => leftList.appendChild(row(it, false)));
    if (!sel.length) rightList.appendChild(el("div", { class: "xfer-empty", text: "—" }));
    else sel.forEach((id) => rightList.appendChild(row(byId[id], true)));
  }

  const search = el("input", { class: "xfer-search", type: "text", placeholder: opts.searchPh || "搜索",
    oninput: (e: Event) => { q = ((e.target as HTMLInputElement).value || "").toLowerCase(); paint(); } });
  paint();
  const wrap = el("div", { class: "xfer" },
    el("div", { class: "xfer-pane" }, leftHead, search, leftList),
    el("div", { class: "xfer-arrow", text: "⇄" }),
    el("div", { class: "xfer-pane" }, rightHead, rightList));
  return { el: wrap, getSelected: () => sel.slice() };
}

interface PagedOpts<T> {
  items: T[]; pageSize?: number;
  searchOf: (it: T) => string; renderItem: (it: T) => HTMLElement;
  searchPh?: string; emptyText?: string;
}
// 搜索 + 分页列表:长列表不用一直下拉。
function pagedList<T>(opts: PagedOpts<T>): HTMLElement {
  const pageSize = opts.pageSize || 8;
  let q = "", page = 0;
  const itemsBox = el("div", { class: "paged-items" });
  const nav = el("div", { class: "paged-nav" });

  const filtered = (): T[] => (!q ? opts.items
    : opts.items.filter((it) => (opts.searchOf(it) || "").toLowerCase().includes(q)));

  function paint(): void {
    const fs = filtered();
    const pages = Math.max(1, Math.ceil(fs.length / pageSize));
    if (page >= pages) page = pages - 1;
    if (page < 0) page = 0;
    itemsBox.innerHTML = "";
    if (!fs.length) itemsBox.appendChild(el("div", { class: "mgmt-empty", text: opts.emptyText || "—" }));
    else fs.slice(page * pageSize, page * pageSize + pageSize).forEach((it) => itemsBox.appendChild(opts.renderItem(it)));
    nav.innerHTML = "";
    if (pages > 1) {
      nav.appendChild(el("button", { class: "paged-btn", text: "‹",
        onclick: () => { if (page > 0) { page--; paint(); } } }));
      nav.appendChild(el("span", { class: "paged-info", text: (page + 1) + " / " + pages }));
      nav.appendChild(el("button", { class: "paged-btn", text: "›",
        onclick: () => { if (page < pages - 1) { page++; paint(); } } }));
    }
  }
  const search = el("input", { class: "paged-search", type: "text", placeholder: opts.searchPh || "搜索",
    oninput: (e: Event) => { q = ((e.target as HTMLInputElement).value || "").toLowerCase(); page = 0; paint(); } });
  paint();
  return el("div", { class: "paged" }, search, itemsBox, nav);
}

const KarvyWidgets = { transferList, pagedList };
(window as unknown as { KarvyWidgets: typeof KarvyWidgets }).KarvyWidgets = KarvyWidgets;
export { KarvyWidgets };
