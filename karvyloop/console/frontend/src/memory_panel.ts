/* memory_panel.ts — 🧠 个人知识库 / 认知面板(从 app.js 抽出,大尾巴 slice)。
 * loop step4b 摄入面:沉淀工作流(喂料→分析→跟小卡交流→你拍板 persist/reject)+ 认知图谱(SVG 网状视图)
 * + 已知 beliefs 列表。整簇自洽,只用 dom/modal/i18n 全局 + window.KarvyRender(渲染总结/对话)+ SVG。
 * 暴露 window.KarvyMemoryPanel.open()。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  mgmtBody: () => HTMLElement | null;
  formMsg: () => HTMLElement;
  setMsg: (msgEl: HTMLElement, ok: boolean, text: string) => void;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
interface Widgets {
  pagedList: <T>(opts: { items: T[]; pageSize?: number; searchOf: (it: T) => string; renderItem: (it: T) => HTMLElement; searchPh?: string; emptyText?: string }) => HTMLElement;
}

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const _KW = (window as unknown as { KarvyWidgets: Widgets }).KarvyWidgets;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);
const _md = (target: HTMLElement, text: string): void => {
  const r = (window as unknown as { KarvyRender?: { appendMarkdown: (e: HTMLElement, m: string) => void } }).KarvyRender;
  if (r) r.appendMarkdown(target, text); else target.textContent = text;
};

function _memKind(k: string): string {
  const m = t("mem.kind_" + (k || "fact"));
  return m.indexOf("mem.kind_") === 0 ? (k || "") : m;  // 未知 kind → 原值
}
function _memSrc(s: string): string {
  const m = t("mem.src_" + (s || "ingest"));
  return m.indexOf("mem.src_") === 0 ? (s || "") : m;
}
// 真实来源(Hardy:别给用户看 fed/ingest 这种内部代号):优先 source_ref —— URL→可点链接、
// 粘贴文本→"粘贴文本";没有 ref 才回退到友好的来源类别(你分享的资料/对话沉淀/手动录入)。
// Q2 出处回链:对话蒸馏的条目带 conversation_id → 文案仍是友好的"对话沉淀",但可点 —— 点回
// 产生它的那次对话(跳转统一在 app.js:面板只发全局事件,老数据无 id → 回退纯文本,不崩不骗)。
function _origin(source: string, sourceRef: string, conversationId?: string): { text: string; href: string; conv: string } {
  const ref = (sourceRef || "").trim();
  if (/^https?:\/\//.test(ref)) {
    let short = ref.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    if (short.length > 46) short = short.slice(0, 44) + "…";
    return { text: short, href: ref, conv: "" };
  }
  if (ref.indexOf("text:") === 0) return { text: t("mem.src_pasted"), href: "", conv: "" };
  const conv = source === "conversation" ? (conversationId || "").trim() : "";
  return { text: _memSrc(source), href: "", conv };
}
function _originNode(source: string, sourceRef: string, conversationId?: string): HTMLElement {
  const o = _origin(source, sourceRef, conversationId);
  if (o.href) return el("a", { class: "mc-src-link", href: o.href, target: "_blank", text: o.text, title: o.href });
  if (o.conv) {
    // 复用 app.js 的会话跳转(openConvById 按 id 定位真 peer):发 karvy:open-conversation 事件,
    // app.js 收口(关面板 → 跳会话;定位不到旧会话 → 聊天流里友好提示)。
    return el("a", { class: "mc-src-link mc-src-conv", href: "#", text: o.text, title: t("mem.src_conv_title"),
      onclick: (e: Event) => {
        e.preventDefault();
        window.dispatchEvent(new (window as unknown as { CustomEvent: typeof CustomEvent }).CustomEvent(
          "karvy:open-conversation", { detail: { conversation_id: o.conv } }));
      } });
  }
  return el("span", { class: "mc-src", text: o.text });
}

// ch4 pillar 3:认知图谱**网状视图**(mesh),仿 Obsidian graph view。Hardy:别排成一个圆、别堆成一坨、
// 标题别只截前几个字。做法:① 力导向布局(FR + 向心引力 corral 散点 + 碰撞去重叠)② viewBox 自适应到节点包围盒
// (不留大片空白)③ 标题默认只显示 hub、悬停某点高亮它+邻居并显其标题、其余变暗(稠密图靠悬停聚焦而非全标)。
const _NS = "http://www.w3.org/2000/svg";
const _nodeLabel = (n: any): string => ((n.title || "").trim() || (n.content || "").slice(0, 12));
const _raf = (fn: () => void): void => { typeof requestAnimationFrame === "function" ? requestAnimationFrame(fn) : setTimeout(fn, 0); };

// 展示层稀疏化(Hardy):后端为召回而生成的边很密(词面重叠,平均每点 ~12 条)→ 画出来就是一坨圆盘。
// Obsidian 之所以铺成枝杈是因为它稀疏(每点 2~3 条手写链接)。所以**只画"真链接"**:语义边(LLM 标签重叠)
// 无条件保留 + 每个点再留最强的 top-K 条(按共享 token 数)。弱词面边留在后端做召回,但不画。
// —— 密度是圆的主因;稀疏 → 同样的力布局自然长成枝杈。不动召回,只动展示。
function _sparsifyForDisplay(nodes: any[], edges: any[]): any[] {
  // 每点保留最强 K 条。K=2(而非 3):边越密 → 环越多 → 连线交叉越多。实测本库 K=3→35 处交叉、
  // K=2→仅 6 处(只少画 6 条边),更接近 Obsidian 的树状清爽。交叉不是随机——每条都是真关联,只是少画弱的第 3 条。
  const K = 2;
  const strength = (e: any): number => (e.via ? e.via.length : 1) + (e.semantic ? 100 : 0);
  const per: number[][] = nodes.map(() => []);
  edges.forEach((e: any, idx: number) => { per[e.source].push(idx); per[e.target].push(idx); });
  const keep = new Set<number>();
  edges.forEach((e: any, idx: number) => { if (e.semantic) keep.add(idx); });          // 语义边全留
  per.forEach((list) => {                                                                // 每点最强 top-K
    list.sort((a, b) => strength(edges[b]) - strength(edges[a]));
    for (let n = 0; n < Math.min(K, list.length); n++) keep.add(list[n]);
  });
  return edges.filter((_: any, idx: number) => keep.has(idx));
}
// 展示图:剪边 + 用**画出的**边重算 degree(节点大小/亮度/LOD 都按显示密度,而非全量密度)。不改召回。
function _displayGraph(nodes: any[], edges: any[]): { nodes: any[]; edges: any[] } {
  const pruned = _sparsifyForDisplay(nodes, edges);
  const deg = nodes.map(() => 0);
  pruned.forEach((e: any) => { deg[e.source]++; deg[e.target]++; });
  const nodes2 = nodes.map((n: any, i: number) => ({ ...n, degree: deg[i] }));
  return { nodes: nodes2, edges: pruned };
}

// 单个连通分量的局部力导向(velocity Verlet:限程斥力 + 线性弹簧到 L + 向心到本分量质心 + 摩擦 + 退火),
// 写进 pos(局部坐标、质心归零)。限程斥力让链状拓扑抻成枝而非圆。
function _simComponent(members: number[], edges: any[], pos: any[]): void {
  const M = members.length;
  if (M === 1) { pos[members[0]] = { x: 0, y: 0 }; return; }
  const set = new Set(members);
  const sub = edges.filter((e: any) => set.has(e.source) && set.has(e.target));
  // 边的**目标长度随关联强度变**(Hardy:关联越强越近):强度=共享概念数 + 语义边加成;越强 → 目标长度越短。
  const edgeLen = (e: any): number => { const s = (e.via ? e.via.length : 1) + (e.semantic ? 2 : 0); return Math.max(26, 72 - 9 * s); };
  const GA = Math.PI * (3 - Math.sqrt(5));
  members.forEach((i, k) => { const a = k * GA, r = Math.sqrt(k + 0.5) * 22; pos[i] = { x: r * Math.cos(a), y: r * Math.sin(a), vx: 0, vy: 0 }; });
  const REP = 900, LINK = 0.25, CENTER = 0.02, DECAY = 0.7, RMAX = 260;
  const ITER = M > 120 ? 300 : 450;
  let alpha = 1;
  for (let it = 0; it < ITER; it++) {
    for (let a = 0; a < M; a++) for (let b = a + 1; b < M; b++) {
      const i = members[a], j = members[b];
      const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y, d2 = dx * dx + dy * dy, d = Math.sqrt(d2) || 0.01;
      if (d > RMAX) continue;
      const f = REP * alpha / d2, ux = dx / d, uy = dy / d;
      pos[i].vx += ux * f; pos[i].vy += uy * f; pos[j].vx -= ux * f; pos[j].vy -= uy * f;
    }
    for (const e of sub) {
      const dx = pos[e.target].x - pos[e.source].x, dy = pos[e.target].y - pos[e.source].y, d = Math.hypot(dx, dy) || 0.01;
      const f = LINK * alpha * (d - edgeLen(e)), ux = dx / d, uy = dy / d;   // 强关联 → 短目标 → 拉得更近
      pos[e.source].vx += ux * f; pos[e.source].vy += uy * f; pos[e.target].vx -= ux * f; pos[e.target].vy -= uy * f;
    }
    for (const i of members) { pos[i].vx += -pos[i].x * CENTER * alpha; pos[i].vy += -pos[i].y * CENTER * alpha; }
    for (const i of members) { pos[i].vx *= DECAY; pos[i].vy *= DECAY; pos[i].x += pos[i].vx; pos[i].y += pos[i].vy; }
    alpha *= 0.992;
  }
  let cx = 0, cy = 0; for (const i of members) { cx += pos[i].x; cy += pos[i].y; } cx /= M; cy /= M;
  for (const i of members) { pos[i].x -= cx; pos[i].y -= cy; }   // 质心归零
}

// 力导向布局:**按连通分量分别布局再打包**(Hardy:别把游离碎片甩得满屏乱线)。每个分量内部力导向成型,
// 再按大小打包(大的居中、小的螺旋铺在周围、互不重叠)→ 整图读起来是一坨有组织的星系,而不是稠密角落 + 满地碎渣。
function _forceLayout(nodes: any[], edges: any[]): { pos: { x: number; y: number }[]; rad: (i: number) => number } {
  const N = nodes.length;
  const rad = (i: number): number => 2.5 + Math.min(6, (nodes[i]?.degree || 0) * 0.6);   // 小星辰,连接多略大
  if (!N) return { pos: [], rad };
  // 1) 连通分量(union-find)
  const parent = nodes.map((_: any, i: number) => i);
  const find = (x: number): number => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  for (const e of edges) parent[find(e.source)] = find(e.target);
  const comps = new Map<number, number[]>();
  for (let i = 0; i < N; i++) { const r = find(i); if (!comps.has(r)) comps.set(r, []); comps.get(r)!.push(i); }
  const pos: any[] = nodes.map(() => ({ x: 0, y: 0 }));
  // 2) 多节点分量各自局部布局;**孤立点(无任何关联)另收进一个整齐网格块**(否则满屏散点像随机噪声)。
  const info: { members: number[]; r: number }[] = [];
  const singles: number[] = [];
  for (const members of comps.values()) {
    if (members.length === 1) { singles.push(members[0]); continue; }
    _simComponent(members, edges, pos);
    let R = 0; for (const i of members) R = Math.max(R, Math.hypot(pos[i].x, pos[i].y) + rad(i));
    info.push({ members, r: R + 8 });
  }
  if (singles.length) {   // 孤立点排成紧凑网格(读作"这些是暂无关联的笔记",而非散落的噪点)
    const cols = Math.ceil(Math.sqrt(singles.length)), gap = 26;
    singles.forEach((idx, k) => { pos[idx] = { x: (k % cols) * gap, y: Math.floor(k / cols) * gap }; });
    let cx = 0, cy = 0; for (const i of singles) { cx += pos[i].x; cy += pos[i].y; } cx /= singles.length; cy /= singles.length;
    let R = 0; for (const i of singles) { pos[i].x -= cx; pos[i].y -= cy; R = Math.max(R, Math.hypot(pos[i].x, pos[i].y) + rad(i)); }
    info.push({ members: singles, r: R + 8 });
  }
  // 3) 打包:大分量居中,其余按黄金角螺旋找不重叠的位置铺开
  info.sort((a, b) => b.r - a.r);
  const GA = Math.PI * (3 - Math.sqrt(5));
  const placed: { x: number; y: number; r: number }[] = [];
  for (const ci of info) {
    let px = 0, py = 0;
    if (placed.length) {
      for (let tt = 1; tt < 4000; tt++) {
        const a = tt * GA, rr = Math.sqrt(tt) * (ci.r * 0.5 + 16);
        px = rr * Math.cos(a); py = rr * Math.sin(a);
        let ok = true;
        for (const p of placed) if (Math.hypot(px - p.x, py - p.y) < p.r + ci.r + 12) { ok = false; break; }
        if (ok) break;
      }
    }
    placed.push({ x: px, y: py, r: ci.r });
    for (const i of ci.members) { pos[i].x += px; pos[i].y += py; }
  }
  const out = pos.map((q) => ({ x: q.x, y: q.y }));
  for (let pass = 0; pass < 40; pass++) for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {   // 全局碰撞去重叠(安全)
    const dx = out[i].x - out[j].x, dy = out[i].y - out[j].y, d = Math.hypot(dx, dy) || 0.01, min = rad(i) + rad(j) + 10;
    if (d < min) { const push = (min - d) / 2, ux = dx / d, uy = dy / d;
      out[i].x += ux * push; out[i].y += uy * push; out[j].x -= ux * push; out[j].y -= uy * push; }
  }
  return { pos: out, rad };
}

// 即时气泡(取代 SVG <title> 的浏览器慢速原生 tooltip):鼠标进节点立刻出,跟随光标,离开即隐。单例挂 body。
let _tipEl: HTMLDivElement | null = null;
function _showTip(x: number, y: number, title: string, body: string): void {
  if (!_tipEl) { _tipEl = document.createElement("div"); _tipEl.className = "mem-tip"; document.body.appendChild(_tipEl); }
  const tp = _tipEl; tp.innerHTML = "";
  const h = document.createElement("div"); h.className = "mem-tip-title"; h.textContent = title; tp.appendChild(h);
  if (body) { const b = document.createElement("div"); b.className = "mem-tip-body"; b.textContent = body; tp.appendChild(b); }
  tp.style.display = "block";
  const w = tp.offsetWidth || 220, hh = tp.offsetHeight || 60;
  tp.style.left = Math.max(6, Math.min(x + 14, (window.innerWidth || 1024) - w - 8)) + "px";
  tp.style.top = Math.max(6, Math.min(y + 14, (window.innerHeight || 768) - hh - 8)) + "px";
}
function _hideTip(): void { if (_tipEl) _tipEl.style.display = "none"; }

// 建 SVG(边+节点+标题),仿 Obsidian/地图:归一化填满固定 viewBox;缩放走 **viewBox**(不是 CSS transform),
// 所以字号/点径都**反向按当前缩放折算 → 屏幕恒定大小**(像地图 POI 文字,不随缩放变大);标签按缩放**分层显示**
// (放得越大、露出的标签越多,LOD)。悬停出气泡+聚焦邻域,单击固定选中。
// 返回 { svg, highlight(q), fit(), zoomAt(cx,cy,f), panBy(dx,dy) }。
function _graphSvg(nodes: any[], edges: any[], layout: { pos: { x: number; y: number }[]; rad: (i: number) => number }, big: boolean,
  onSelect?: (i: number | null) => void):
  { svg: SVGElement; highlight: (q: string) => void; fit: () => void; zoomAt: (cx: number, cy: number, f: number) => void;
    panBy: (dx: number, dy: number) => void; select: (i: number | null, opts?: { center?: boolean }) => void; neighbors: (i: number) => number[] } {
  const { pos } = layout;
  const N = nodes.length;
  const nbr: Set<number>[] = nodes.map(() => new Set<number>());   // 邻接表(聚焦/LOD 用)
  for (const e of edges) { nbr[e.source].add(e.target); nbr[e.target].add(e.source); }
  const maxDeg = Math.max(1, ...nodes.map((_: any, i: number) => nbr[i].size));
  const rankByDeg = nodes.map((_: any, i: number) => i).sort((a: number, b: number) => nbr[b].size - nbr[a].size);
  const rank = new Array<number>(N); rankByDeg.forEach((idx, r) => (rank[idx] = r));   // 度数排名(LOD 揭示顺序)
  // 归一化坐标铺满固定 viewBox(1000×640,含留白)。固定 viewBox → 缩放只改 viewBox,字/点按缩放折算成屏幕恒定尺寸。
  const VW = 1000, VH = 640, pad = 90;
  const xs = pos.map((p) => p.x), ys = pos.map((p) => p.y);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const gw = maxx - minx || 1, gh = maxy - miny || 1;
  const s0 = Math.min((VW - 2 * pad) / gw, (VH - 2 * pad) / gh);
  const ox = (VW - gw * s0) / 2 - minx * s0, oy = (VH - gh * s0) / 2 - miny * s0;
  const P = pos.map((p) => ({ x: p.x * s0 + ox, y: p.y * s0 + oy }));
  const svg = document.createElementNS(_NS, "svg") as SVGElement;
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("class", "mem-graph" + (big ? " big" : ""));
  const edgeEls: SVGElement[] = [];
  for (const e of edges) {
    const l = document.createElementNS(_NS, "line");
    l.setAttribute("x1", String(P[e.source].x)); l.setAttribute("y1", String(P[e.source].y));
    l.setAttribute("x2", String(P[e.target].x)); l.setAttribute("y2", String(P[e.target].y));
    l.setAttribute("class", "mem-edge" + (e.semantic ? " semantic" : ""));
    const tt = document.createElementNS(_NS, "title"); tt.textContent = (e.via || []).join(" · ");
    l.appendChild(tt); svg.appendChild(l); (l as any)._e = e; edgeEls.push(l as SVGElement);
  }
  const nodeEls: SVGElement[] = [], labelEls: SVGElement[] = [], hitEls: SVGElement[] = [];
  const baseR = (i: number): number => 2 + Math.min(5, nbr[i].size * 0.5);   // 目标**屏幕**半径(小星辰,连接多略大)
  for (let i = 0; i < N; i++) {
    const c = document.createElementNS(_NS, "circle");
    c.setAttribute("cx", String(P[i].x)); c.setAttribute("cy", String(P[i].y));
    c.setAttribute("class", "mem-node " + (nodes[i].kind === "preference" ? "pref" : "fact"));
    c.setAttribute("fill-opacity", (0.42 + 0.58 * (nbr[i].size / maxDeg)).toFixed(2));   // 连得越多越亮
    svg.appendChild(c); nodeEls.push(c as SVGElement);
    const tx = document.createElementNS(_NS, "text");
    tx.setAttribute("x", String(P[i].x)); tx.setAttribute("class", "mem-label"); tx.setAttribute("text-anchor", "middle");
    tx.textContent = _nodeLabel(nodes[i]);
    svg.appendChild(tx); labelEls.push(tx as SVGElement);
    // 透明**命中圈**(比可见点大得多)→ 小点也好悬停/点中,不用把光标怼在 2px 上
    const hc = document.createElementNS(_NS, "circle");
    hc.setAttribute("cx", String(P[i].x)); hc.setAttribute("cy", String(P[i].y));
    hc.setAttribute("class", "mem-hit");
    hitEls.push(hc as SVGElement);
  }
  hitEls.forEach((h) => svg.appendChild(h));   // 命中圈置顶(捕获鼠标,盖过边/标签)
  // 视口状态(viewBox)。缩放=改 vbw/vbh;字/点按 scale=屏幕px/用户单位 折算成恒定屏幕尺寸;标签按缩放分层揭示。
  let vbx = 0, vby = 0, vbw = VW, vbh = VH, curScale = 1;
  const TARGET_FONT = 12.5;   // 正常字号(屏幕 px,不随缩放变大)
  const refresh = (): void => {
    svg.setAttribute("viewBox", `${vbx} ${vby} ${vbw} ${vbh}`);
    let scale = 0;
    const ctm = (svg as any).getScreenCTM ? (svg as any).getScreenCTM() : null;
    if (ctm && ctm.a) scale = Math.hypot(ctm.a, ctm.b);
    else if ((svg as any).clientWidth) scale = (svg as any).clientWidth / vbw;
    if (!scale) scale = 1;
    curScale = scale;
    const fontU = TARGET_FONT / scale;                      // 屏幕恒定字号 → 折算成用户单位
    const zoom = VW / vbw;                                  // 缩放层级(1=全览,越大越放大)
    const K = Math.min(N, Math.max(3, Math.round(4 * Math.pow(zoom, 1.35))));   // 露出的标签数随缩放增长(地图式 LOD)
    const HIT = 13;   // 命中圈**屏幕**半径(≥13px → 直径 26px,小点也易中)
    for (let i = 0; i < N; i++) {
      const rU = baseR(i) / scale;                          // 屏幕恒定点径
      nodeEls[i].setAttribute("r", String(rU));
      hitEls[i].setAttribute("r", String(Math.max(baseR(i), HIT) / scale));   // 命中圈屏幕恒定、够大
      labelEls[i].setAttribute("font-size", fontU.toFixed(2));
      labelEls[i].setAttribute("y", String(P[i].y - rU - 3 / scale));
      labelEls[i].classList.toggle("lod", rank[i] < K);     // 该缩放层级该不该露出
    }
  };
  // 聚焦:某点 → 该点+邻居高亮(强制显标题)、相连边点亮、其余全暗;null → 复位到 LOD。悬停=预览,单击=固定。
  let selected: number | null = null;
  const applyFocus = (i: number | null): void => {
    if (i === null) {
      nodeEls.forEach((c) => c.classList.remove("dim", "focus", "adj", "selected"));
      labelEls.forEach((tx) => tx.classList.remove("dim", "lbl-on"));
      edgeEls.forEach((l) => l.classList.remove("dim", "lit"));
      return;
    }
    const near = nbr[i];
    nodeEls.forEach((c, j) => {
      c.classList.toggle("focus", j === i);
      c.classList.toggle("selected", selected === i && j === i);
      c.classList.toggle("adj", near.has(j));
      c.classList.toggle("dim", j !== i && !near.has(j));
    });
    labelEls.forEach((tx, j) => {
      tx.classList.toggle("lbl-on", j === i || near.has(j));    // 聚焦点+邻居:强制显(盖过 LOD)
      tx.classList.toggle("dim", j !== i && !near.has(j));      // 其余标签:压掉
    });
    edgeEls.forEach((l) => {
      const e = (l as any)._e, on = e.source === i || e.target === i;
      l.classList.toggle("lit", on); l.classList.toggle("dim", !on);
    });
  };
  hitEls.forEach((hc, i) => {   // 事件挂在大命中圈上(不是 2px 的可见点)
    hc.addEventListener("mouseenter", (ev: MouseEvent) => {
      _showTip(ev.clientX, ev.clientY, _nodeLabel(nodes[i]), nodes[i].content || "");
      if (selected === null) applyFocus(i);
    });
    hc.addEventListener("mousemove", (ev: MouseEvent) => _showTip(ev.clientX, ev.clientY, _nodeLabel(nodes[i]), nodes[i].content || ""));
    hc.addEventListener("mouseleave", () => { _hideTip(); if (selected === null) applyFocus(null); else applyFocus(selected); });
    hc.addEventListener("click", (ev: MouseEvent) => {
      ev.stopPropagation();
      select(selected === i ? null : i);
    });
  });
  svg.addEventListener("click", () => { if (selected !== null) select(null); });   // 点空白 → 取消选中
  const highlight = (q: string): void => {
    const query = (q || "").trim().toLowerCase();
    const hit = new Set<number>();
    if (query) nodes.forEach((n, i) => {
      if ((_nodeLabel(n) + " " + (n.content || "")).toLowerCase().includes(query)) hit.add(i);
    });
    const on = query.length > 0;
    nodeEls.forEach((c, i) => c.classList.toggle("dim", on && !hit.has(i)));
    labelEls.forEach((tx, i) => { tx.classList.toggle("dim", on && !hit.has(i)); tx.classList.toggle("lbl-on", on && hit.has(i)); });
    edgeEls.forEach((l) => {
      const e = (l as any)._e;
      l.classList.toggle("dim", on && !(hit.has(e.source) || hit.has(e.target)));
    });
  };
  // 屏幕坐标 → 用户坐标(经 viewBox 逆变换),缩放锚定光标用
  const toUser = (cx: number, cy: number): { x: number; y: number } => {
    const ctm = (svg as any).getScreenCTM ? (svg as any).getScreenCTM() : null;
    if (ctm && (svg as any).createSVGPoint) {
      const pt = (svg as any).createSVGPoint(); pt.x = cx; pt.y = cy;
      const u = pt.matrixTransform(ctm.inverse()); return { x: u.x, y: u.y };
    }
    return { x: vbx + vbw / 2, y: vby + vbh / 2 };   // 回退:图心(jsdom 无 CTM)
  };
  const fit = (): void => { vbx = 0; vby = 0; vbw = VW; vbh = VH; refresh(); };
  const zoomAt = (cx: number, cy: number, f: number): void => {
    const u = toUser(cx, cy);
    const nw = Math.max(VW / 9, Math.min(VW * 1.15, vbw / f)), nh = nw * (vbh / vbw);
    vbx = u.x - (u.x - vbx) * (nw / vbw); vby = u.y - (u.y - vby) * (nh / vbh);
    vbw = nw; vbh = nh; refresh();
  };
  const panBy = (dx: number, dy: number): void => { vbx -= dx / curScale; vby -= dy / curScale; refresh(); };
  const centerOn = (i: number): void => { vbx = P[i].x - vbw / 2; vby = P[i].y - vbh / 2; refresh(); };   // 把某点移到视口中心
  const neighbors = (i: number): number[] => [...nbr[i]];
  // 选中(编程式,供详情卡的关联节点点击切换焦点用):设选中态 + 聚焦 +(可选)居中 + 回调
  const select = (i: number | null, opts?: { center?: boolean }): void => {
    selected = i; applyFocus(i);
    if (i !== null && opts && opts.center) centerOn(i);
    if (onSelect) onSelect(i);
  };
  fit();
  return { svg, highlight, fit, zoomAt, panBy, select, neighbors };
}

async function renderMemoryGraph(container: HTMLElement): Promise<void> {
  container.innerHTML = "";
  const g = await _getJSON("/api/memory/graph");
  const nodes = (g && g.nodes) || [];
  const edges = (g && g.edges) || [];
  if (!nodes.length) { container.appendChild(el("div", { class: "mgmt-empty", text: t("mem.empty") })); return; }
  const disp = _displayGraph(nodes, edges);   // 只画真链接(语义 + 每点 top-K),弱词面边不画
  const layout = _forceLayout(disp.nodes, disp.edges);
  const built = _graphSvg(disp.nodes, disp.edges, layout, false);
  // 悬停蒙版 + 中间放大按钮:鼠标移到图上 → 浮出蒙版,点中间的 ⊕ 进大图(取代下面那条弱鸡「看大图」文字链)
  const wrap = el("div", { class: "mem-graph-wrap" }, built.svg,
    el("div", { class: "mem-graph-hover", onclick: () => _openGraphFullscreen(nodes, edges) },
      el("button", { class: "mem-graph-plus", text: "+" })));
  container.appendChild(wrap);
  _raf(() => built.fit());   // 插入 DOM 后按真实尺寸重算屏幕恒定字号/点径 + LOD
}

// 点开看大图:全屏 overlay + 滚轮缩放(缩到光标)/拖动 + 搜索高亮 + 悬停聚焦 + **选中出详情卡**(标题/完整信息/
// 来源/关联节点,关联节点可点击切换焦点)。
function _openGraphFullscreen(nodes: any[], edges: any[]): void {
  const overlay = el("div", { class: "mem-graph-overlay" });
  const disp = _displayGraph(nodes, edges);   // 大图同样只画真链接
  const layout = _forceLayout(disp.nodes, disp.edges);
  // 详情卡:选中某点 → 右侧浮出(标题+完整内容+来源+关联节点);关联节点点击 → 切换焦点并居中。
  const detail = el("div", { class: "mem-detail hidden" });
  const renderDetail = (i: number | null): void => {
    detail.innerHTML = "";
    if (i === null) { detail.classList.add("hidden"); return; }
    detail.classList.remove("hidden");
    const n = disp.nodes[i];
    detail.appendChild(el("button", { class: "mem-detail-close", text: "✕", onclick: () => built.select(null) }));
    detail.appendChild(el("div", { class: "mem-detail-title", text: _nodeLabel(n) }));
    // 元信息:类型 + 来源(URL → 链接;否则本地化的来源类别)
    const meta = el("div", { class: "mem-detail-meta" });
    meta.appendChild(el("span", { class: "mem-detail-kind", text: _memKind(n.kind) }));
    meta.appendChild(el("span", { text: " · " + t("mem.detail_source") + ": " }));
    const o = _origin(n.source, n.source_ref);
    meta.appendChild(o.href
      ? el("a", { class: "mem-detail-src-link", href: o.href, target: "_blank", text: o.text, title: o.href })
      : el("span", { text: o.text }));
    detail.appendChild(meta);
    const body = el("div", { class: "mem-detail-body" }); _md(body, n.content || ""); detail.appendChild(body);
    // 关联知识点:可点击 → 焦点转移 + 居中
    const nb = built.neighbors(i);
    detail.appendChild(el("div", { class: "mem-detail-rel-label", text: t("mem.detail_related", { n: nb.length }) }));
    const rels = el("div", { class: "mem-detail-rels" });
    if (nb.length) nb.forEach((j) => rels.appendChild(
      el("button", { class: "mem-rel", text: _nodeLabel(disp.nodes[j]), onclick: () => built.select(j, { center: true }) })));
    else rels.appendChild(el("div", { class: "mem-detail-norel", text: t("mem.detail_no_rel") }));
    detail.appendChild(rels);
  };
  const built = _graphSvg(disp.nodes, disp.edges, layout, true, renderDetail);
  const stage = el("div", { class: "mem-graph-stage" }, built.svg);
  const search = el("input", { class: "mem-graph-search", type: "text", placeholder: t("mem.graph_search"),
    oninput: (e: Event) => built.highlight((e.target as HTMLInputElement).value) }) as HTMLInputElement;
  const bar = el("div", { class: "mem-graph-bar" },
    el("span", { class: "mem-graph-title", text: t("mem.graph") + " · " + t("mem.graph_count", { n: nodes.length }) }),
    el("span", { class: "mem-graph-hint", text: t("mem.graph_hint") }),
    search,
    el("button", { class: "mem-graph-close", text: "✕", onclick: () => overlay.remove() }));
  // 滚轮缩放(锚定光标,走 viewBox → 字/点屏幕恒定、标签随缩放分层露出)+ 拖动平移
  let dragging = false, lx = 0, ly = 0;
  stage.addEventListener("wheel", (e: WheelEvent) => {
    e.preventDefault();
    built.zoomAt(e.clientX, e.clientY, e.deltaY > 0 ? 0.85 : 1.18);
  }, { passive: false });
  stage.addEventListener("mousedown", (e: MouseEvent) => { dragging = true; lx = e.clientX; ly = e.clientY; });
  window.addEventListener("mousemove", (e: MouseEvent) => {
    if (!dragging) return;
    const dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
    built.panBy(dx, dy);
  });
  window.addEventListener("mouseup", () => { dragging = false; });
  overlay.appendChild(bar); overlay.appendChild(stage); overlay.appendChild(detail);
  document.body.appendChild(overlay);
  _raf(() => built.fit());   // 按舞台真实尺寸初始化屏幕恒定尺寸
  setTimeout(() => search.focus(), 30);
}

// ch4 #2:沉淀工作流 —— 没待办 → 喂料;有待办 → 接着聊那一条(下次打开继续)。
async function _reloadDistill(wrap: HTMLElement): Promise<void> {
  const data = await _getJSON("/api/memory/distill");
  const pending = data && data.pending;
  if (pending) _renderDistillPending(wrap, pending);
  else _renderDistillFeed(wrap);
}

function _renderDistillFeed(wrap: HTMLElement): void {
  wrap.innerHTML = "";
  wrap.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.feed_label") }));
  wrap.appendChild(el("div", { class: "mgmt-hint", text: t("distill.feed_hint") }));
  const ta = el("textarea", { placeholder: t("distill.feed_ph") }) as HTMLTextAreaElement;
  const msg = _formMsg();
  const submit = el("button", { class: "mgmt-submit", text: t("distill.feed_btn"),
    onclick: async () => {
      const material = ta.value.trim();
      if (!material) return;
      submit.disabled = true; _setMsg(msg, true, t("distill.analyzing"));
      const res = await _postJSON("/api/memory/feed", { material });
      submit.disabled = false;
      if (res.ok || (res.data && res.data.pending)) { await _reloadDistill(wrap); }
      else { _setMsg(msg, false, (res.data && res.data.reason) || res.status); }
    } }) as HTMLButtonElement;
  wrap.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() }, ta, submit, msg));
}

function _renderDistillPending(wrap: HTMLElement, p: any): void {
  wrap.innerHTML = "";
  wrap.appendChild(el("div", { class: "mgmt-section-title", text: t("distill.pending_title") }));
  // Bug1:这份资料喂过 → 警示 + 说明"沉淀会替换旧版(不重复)"
  if ((p.already_fed || 0) > 0) {
    wrap.appendChild(el("div", { class: "distill-dup", text: t("distill.already_fed", { n: p.already_fed }) }));
  }
  if (p.source_url) wrap.appendChild(el("a", { class: "distill-src", href: p.source_url, target: "_blank", text: p.source_url }));
  // 小卡的结构化总结(知识自生长框架)
  const sum = el("div", { class: "distill-summary" });
  _md(sum, p.summary || "");
  wrap.appendChild(sum);
  // 沉淀前的交流记录
  const tr = el("div", { class: "distill-chat" });
  for (const x of (p.transcript || [])) {
    const line = el("div", { class: "distill-line " + (x.who === "you" ? "you" : "karvy") });
    line.appendChild(el("span", { class: "distill-who", text: x.who === "you" ? t("chat.you") : t("chat.karvy") }));
    const bd = el("div", { class: "distill-bd" });
    _md(bd, x.text || "");
    line.appendChild(bd);
    tr.appendChild(line);
  }
  wrap.appendChild(tr);
  // 交流输入(沉淀前跟小卡讨论这条料)
  const cin = el("input", { type: "text", class: "distill-chat-in", placeholder: t("distill.chat_ph") }) as HTMLInputElement;
  const cmsg = _formMsg();
  const send = el("button", { class: "mgmt-submit", text: t("distill.chat_send"),
    onclick: async () => {
      const m = cin.value.trim();
      if (!m) return;
      send.disabled = true; _setMsg(cmsg, true, "…");
      const res = await _postJSON("/api/memory/distill/chat", { message: m });
      send.disabled = false;
      if (res.ok) { cin.value = ""; await _reloadDistill(wrap); }
      else _setMsg(cmsg, false, (res.data && res.data.reason) || res.status);
    } }) as HTMLButtonElement;
  wrap.appendChild(el("form", { class: "mgmt-form", onsubmit: (e: Event) => e.preventDefault() }, cin, send, cmsg));
  // 你拍板:沉淀 / 不沉淀(结束这条才能开下一条)
  const decideMsg = _formMsg();
  const bar = el("div", { class: "distill-decide" });
  bar.appendChild(el("button", { class: "distill-yes", text: t("distill.persist"),
    onClick: () => _decideDistill(decideMsg, "persist") }));
  bar.appendChild(el("button", { class: "distill-no", text: t("distill.reject"),
    onClick: () => _decideDistill(decideMsg, "reject") }));
  wrap.appendChild(bar);
  wrap.appendChild(decideMsg);
}

async function _decideDistill(msg: HTMLElement, decision: string): Promise<void> {
  _setMsg(msg, true, t("distill.deciding"));
  const res = await _postJSON("/api/memory/distill/decide", { decision: decision });
  if (!res.ok) { _setMsg(msg, false, (res.data && res.data.reason) || res.status); return; }
  await renderMemoryPanel();   // 结束这条 → 回喂料态 + 刷新"已知"列表
}

// Bug2:整理相似知识(H2A)——一次 LLM 出合并建议,逐簇你拍板合并(离摄入热路径,用户点才跑)。
async function _runConsolidate(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.consolidate_btn") }));
  const backRow = el("div", { class: "mgmt-row" },
    el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderMemoryPanel() }));
  const status = el("div", { class: "mgmt-hint", text: t("mem.consolidating") });
  body.appendChild(status); body.appendChild(backRow);
  const r = await _postJSON("/api/memory/consolidate/suggest", {});
  status.remove();
  const clusters = (r.ok && r.data && r.data.clusters) || [];
  if (!clusters.length) { body.insertBefore(el("div", { class: "mgmt-empty", text: t("mem.consolidate_none") }), backRow); return; }
  const list = el("div", { class: "mgmt-list" });
  body.insertBefore(list, backRow);
  for (const c of clusters) {
    const card = el("div", { class: "mgmt-card consolidate-card" });
    // 合并去向:标题 + 正文
    card.appendChild(el("div", { class: "mc-main" },
      el("div", { class: "mc-name", text: t("mem.consolidate_into", { n: (c.member_contents || []).length }) }),
      el("div", { class: "consolidate-target" },
        (c.merged_title ? el("span", { class: "mc-tag", text: c.merged_title }) : null),
        el("span", { text: " " + c.merged_content }))));
    // 被并的成员(小字列出,让你看清合的是哪几条)
    const mem = el("div", { class: "consolidate-members" });
    (c.member_contents || []).forEach((m: string, i: number) => {
      const tt = (c.member_titles || [])[i] || "";
      mem.appendChild(el("div", { class: "consolidate-member", text: "・ " + (tt ? tt + " — " : "") + m }));
    });
    if (c.reason) mem.appendChild(el("div", { class: "mgmt-hint", text: c.reason }));
    card.appendChild(mem);
    const doBtn = el("button", { class: "dpref-confirm", text: t("mem.consolidate_do"),
      onclick: async () => {
        (doBtn as HTMLButtonElement).disabled = true;
        const ar = await _postJSON("/api/memory/consolidate/apply",
          { member_contents: c.member_contents, merged_content: c.merged_content, merged_title: c.merged_title || "" });
        if (ar.ok && ar.data && ar.data.ok) card.replaceWith(el("div", { class: "mgmt-hint",
          text: t("mem.consolidate_done", { n: ar.data.removed }) }));
        else (doBtn as HTMLButtonElement).disabled = false;
      } });
    card.appendChild(el("div", { class: "dpref-actions" }, doBtn));
    list.appendChild(card);
  }
}

// ============================================================================
// docs/66 §F(Hardy 三次收敛):认知聊天**整个住在知识库模块里**。
// 「聊知识」区 = 待处理知识列表(每段没沉淀的会话一行)+ 知识馆员聊天 + ⚗️收敛 →
// 逐条确认(收/改/不要,没动的不沉)→ 只沉确认的 → 关会话(欠账清一笔)。主聊天零耦合。
// ============================================================================
let _kSession = "";   // 当前打开的知识会话 id("" = 下一句话新开一段)

function _kLine(log: HTMLElement, who: "you" | "karvy", text: string): void {
  const line = el("div", { class: "distill-line " + who });
  line.appendChild(el("span", { class: "distill-who", text: who === "you" ? t("chat.you") : t("knowledge.speaker") }));
  const bd = el("div", { class: "distill-bd" });
  _md(bd, text || "");
  line.appendChild(bd);
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

// 沉淀确认卡(面板内渲染):逐条 收/改/不要;没动的 = 未确认 = 不沉;depth≥4 带 ⚠。
function _renderSedimentCard(host: HTMLElement, card: any, onDone: () => void): void {
  const box = el("div", { class: "sediment-card" });
  box.appendChild(el("div", { class: "sediment-head", text: t("sediment.card_title") }));
  box.appendChild(el("div", { class: "sediment-note", text: t("sediment.card_note") }));
  const states: Record<string, { action: string; content?: string }> = {};
  const submit = el("button", { type: "button", class: "sediment-submit" }) as HTMLButtonElement;
  const updateSubmit = () => {
    const n = Object.values(states).filter((x) => x.action !== "drop").length;
    submit.textContent = n > 0 ? t("sediment.submit", { n }) : t("sediment.submit_zero");
  };
  for (const it of card.items || []) {
    const row = el("div", { class: "sediment-row depth-" + (it.depth || 1) });
    const content = el("span", { class: "sediment-content", text: it.content });
    const setState = (cls: string) => {
      row.classList.remove("is-keep", "is-edit", "is-drop");
      if (cls) row.classList.add("is-" + cls);
      updateSubmit();
    };
    const bKeep = el("button", { type: "button", class: "sediment-act keep", text: t("sediment.keep") });
    bKeep.addEventListener("click", () => {
      const editing = content.getAttribute("contenteditable") === "true";
      const txt = (content.textContent || "").trim();
      if (editing && txt && txt !== it.content) { states[it.id] = { action: "edit", content: txt }; setState("edit"); }
      else { states[it.id] = { action: "accept" }; setState("keep"); }
      content.setAttribute("contenteditable", "false");
    });
    const bEdit = el("button", { type: "button", class: "sediment-act edit", text: t("sediment.edit") });
    bEdit.addEventListener("click", () => { content.setAttribute("contenteditable", "true"); (content as HTMLElement).focus(); });
    const bDrop = el("button", { type: "button", class: "sediment-act drop", text: t("sediment.drop") });
    bDrop.addEventListener("click", () => { states[it.id] = { action: "drop" }; content.setAttribute("contenteditable", "false"); setState("drop"); });
    const acts = el("span", { class: "sediment-acts" }, bKeep, bEdit, bDrop);
    row.appendChild(el("span", { class: "sediment-chip", text: t("layer." + it.layer) }));
    row.appendChild(content); row.appendChild(acts);
    if (it.needs_attention) row.appendChild(el("div", { class: "sediment-warn", text: t("sediment.attention") }));
    box.appendChild(row);
  }
  const cancel = el("button", { type: "button", class: "sediment-cancel", text: t("sediment.cancel") });
  cancel.addEventListener("click", () => box.remove());
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    const res = await _postJSON("/api/knowledge/sediment", {
      conversation_id: card.conversation_ref, items: card.items, decisions: states });
    if (!res.ok || !(res.data && res.data.ok)) { submit.disabled = false; return; }
    box.remove();
    onDone();
  });
  updateSubmit();
  box.appendChild(el("div", { class: "sediment-foot" }, cancel, submit));
  host.appendChild(box);
  host.scrollTop = host.scrollHeight;
}

async function _renderKnowledgeArea(wrap: HTMLElement): Promise<void> {
  wrap.innerHTML = "";
  // 整窗 IM 布局(Hardy:"给你一个完整的窗口做聊天"):左栏=会话切换(➕新开一段 + 每段一行),
  // 右侧=聊天记录占满(**唯一滚动区**)+ 底部输入条(输入|发|⚗️收敛)。没有内外双滚动条。
  const debt = await _getJSON("/api/knowledge/debt");
  const sessions = (debt && debt.sessions) || [];
  const side = el("div", { class: "kchat-side" });
  side.appendChild(el("div", { class: "kchat-side-head", text: t("kchat.side_head", { n: sessions.length }),
    title: t("knowledge.entry_desc") }));
  const mkRow = (label: string, active: boolean, cls: string, onclick: () => void, xId?: string) => {
    const r = el("button", { class: "kchat-sess" + (active ? " active" : "") + cls });
    r.appendChild(el("span", { class: "kchat-sess-nm", text: label }));
    r.addEventListener("click", onclick);
    if (xId) {
      // Hardy:鼠标移上去右边出现 ✕ → 点 ✕ 弹确认(没沉淀的会丢)→ 确认才关,取消不动
      const x = el("span", { class: "kchat-sess-x", text: "✕", title: t("kchat.close_title") });
      x.addEventListener("click", async (e: Event) => {
        e.stopPropagation();   // 别顺带触发"切到这段"
        if (!window.confirm(t("kchat.close_confirm", { s: label.slice(0, 30) }))) return;
        const res = await _postJSON("/api/knowledge/discard", { session_id: xId });
        if (res.ok && res.data && res.data.ok) {
          if (_kSession === xId) _kSession = "";
          void _renderKnowledgeArea(wrap);
        }
      });
      r.appendChild(x);
    }
    side.appendChild(r);
  };
  mkRow(t("kchat.new"), !_kSession, " kchat-sess-new", () => { _kSession = ""; void _renderKnowledgeArea(wrap); });
  for (const s of sessions) {
    mkRow("📥 " + (s.snippet || t("conv.untitled")), s.id === _kSession, "",
          () => { _kSession = s.id; void _renderKnowledgeArea(wrap); }, s.id);
  }
  const main = el("div", { class: "kchat-main" });
  const log = el("div", { class: "kchat-log" });
  // 旧喂料流的**待审条目**(有才显示,浮在记录顶部随流滚动;喂料入口已由聊天替代——丢进来就是喂)
  try {
    const d = await _getJSON("/api/memory/distill");
    if (d && d.pending) {
      const pw = el("div", { class: "distill-area kchat-pending" });
      _renderDistillPending(pw, d.pending);
      log.appendChild(pw);
    }
  } catch { /* 无待审 → 纯聊天 */ }
  if (_kSession) {
    try {
      const sess = await _getJSON("/api/knowledge/session?id=" + encodeURIComponent(_kSession));
      for (const turn of (sess && sess.turns) || []) {
        if (turn.user_intent) _kLine(log, "you", turn.user_intent);
        if (turn.agent_response) _kLine(log, "karvy", turn.agent_response);
      }
    } catch { /* 读不到当新会话 */ }
  }
  // 正经聊天框(Hardy:"我跟你说是聊天框"):底部一行横排 [textarea|发|⚗️收敛],绝不竖叠。
  // 反馈纪律(Hardy:"他不理我!"):你的话立即上流 + 流内 typing 行;忙时再发**不静默吞**;
  // 失败在流里说原因(不是角落小字)。textarea:Enter 发,Shift+Enter 换行(粘长文没障碍)。
  const cin = el("textarea", { class: "kchat-in", rows: "1", placeholder: t("kchat.ph") }) as HTMLTextAreaElement;
  const send = el("button", { type: "button", class: "kchat-btn kchat-send", text: t("kchat.send") }) as HTMLButtonElement;
  const conv = el("button", { type: "button", class: "kchat-btn kchat-converge", text: t("kchat.converge"),
    title: t("btn.converge.title") }) as HTMLButtonElement;
  const msg = _formMsg();
  let _busy = false;
  const typingLine = (): HTMLElement => {
    const ln = el("div", { class: "distill-line karvy kchat-typing" });
    ln.appendChild(el("span", { class: "distill-who", text: t("knowledge.speaker") }));
    ln.appendChild(el("div", { class: "distill-bd", text: t("kchat.thinking") }));
    log.appendChild(ln); log.scrollTop = log.scrollHeight;
    return ln;
  };
  const doSend = async () => {
    const m = cin.value.trim();
    if (!m) return;
    if (_busy) { _setMsg(msg, false, t("kchat.busy")); return; }   // 忙时不吞:明说等一下,保住输入
    _busy = true; send.disabled = true;
    cin.value = "";
    _kLine(log, "you", m);
    const tl = typingLine();
    const res = await _postJSON("/api/knowledge/chat", { session_id: _kSession, message: m });
    tl.remove();
    _busy = false; send.disabled = false;
    if (res.ok && res.data && res.data.ok) {
      _kSession = res.data.session_id;
      _setMsg(msg, true, "");
      _kLine(log, "karvy", res.data.reply);
    } else {
      // 失败在流里说(像个人),不是角落小字
      _kLine(log, "karvy", "(" + t("kchat.failed", { reason: (res.data && res.data.reason) || String(res.status) }) + ")");
    }
  };
  send.addEventListener("click", doSend);
  cin.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void doSend(); }
  });
  conv.addEventListener("click", async () => {
    if (!_kSession) { _setMsg(msg, false, t("kchat.nothing_yet")); return; }
    if (_busy) { _setMsg(msg, false, t("kchat.busy")); return; }
    _busy = true; conv.disabled = true; send.disabled = true;
    const tl = typingLine();
    tl.querySelector(".distill-bd")!.textContent = t("kchat.converging");
    const res = await _postJSON("/api/knowledge/converge", { session_id: _kSession });
    tl.remove();
    _busy = false; conv.disabled = false; send.disabled = false;
    if (!res.ok || !(res.data && res.data.ok)) {
      _kLine(log, "karvy", "(" + t("kchat.failed", { reason: (res.data && res.data.reason) || String(res.status) }) + ")");
      return;
    }
    const card = res.data.card;
    if (!card || !card.n) { _kLine(log, "karvy", t("sediment.none")); return; }
    _renderSedimentCard(log, card, () => {
      _kSession = "";                       // 沉了就关这段 → 回到"新开一段"态,列表里那行消失
      void renderMemoryPanel();             // 整面板刷新(待处理列表+已知列表都更新)
    });
  });
  const bar = el("div", { class: "kchat-bar" }, cin, send, conv);
  main.appendChild(log);
  main.appendChild(bar);
  main.appendChild(msg);
  wrap.appendChild(side);
  wrap.appendChild(main);
}

// 双标签(Hardy:"知识库和知识沉淀做在 2 个标签页,免得聊天视图不纯粹")
let _memTab: "sediment" | "library" = "sediment";

async function renderMemoryPanel(): Promise<void> {
  const body = mgmtBody(); if (!body) return; body.innerHTML = "";
  const tabs = el("div", { class: "mem-tabs" });
  const mkTab = (key: "sediment" | "library", label: string) => {
    const b = el("button", { class: "mem-tab" + (_memTab === key ? " active" : ""), text: label });
    b.addEventListener("click", () => { if (_memTab !== key) { _memTab = key; void renderMemoryPanel(); } });
    tabs.appendChild(b);
  };
  mkTab("sediment", t("mem.tab_sediment"));
  mkTab("library", t("mem.tab_library"));
  body.appendChild(tabs);
  // 沉淀页 = 整窗 IM(body 停止滚动,唯一滚动区在聊天记录里 —— 不许内外双滚动条)
  body.classList.toggle("kchat-mode", _memTab === "sediment");
  if (_memTab === "sediment") {
    // 标签页①「聊知识 · 沉淀」:左栏会话切换 + 聊天记录 + 底部输入条(喂料入口已由聊天替代:
    // 丢进来就是喂;旧喂料流的待审条目浮在记录顶部,有才显示)
    const kWrap = el("div", { class: "kchat-area" });
    body.appendChild(kWrap);
    await _renderKnowledgeArea(kWrap);
    return;
  }
  // 标签页②「知识库」:图谱 + 已知列表(纯浏览,不混聊天)
  body.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.graph") }));
  const graphBox = el("div", { class: "mem-graph-box" });
  body.appendChild(graphBox);
  renderMemoryGraph(graphBox);
  // 已知(列表)
  const data = await _getJSON("/api/memory");
  const beliefs = (data && data.beliefs) || [];
  body.appendChild(el("div", { class: "mgmt-section-title" },
    el("span", { text: t("mem.known") + " (" + beliefs.length + ")" }),
    beliefs.length >= 2 ? el("button", { class: "mgmt-inline-link mem-consolidate-btn",
      text: t("mem.consolidate_btn"), onclick: () => _runConsolidate() }) : null));
  if (!beliefs.length) {
    body.appendChild(el("div", { class: "mgmt-empty", text: t("mem.empty") }));
  } else {
    // #5 知识多了要能搜/翻页(复用 pagedList);每条可删(知识库管理)
    body.appendChild(_KW.pagedList({
      items: beliefs, pageSize: 8, searchPh: t("mem.search"), emptyText: t("mem.empty"),
      searchOf: (b: any) => (b.title || "") + " " + (b.content || "") + " " + _memKind(b.kind),
      renderItem: (b: any) => {
        const title = (b.title || "").trim();
        return el("div", { class: "mgmt-card" },
          el("div", { class: "mc-main" },
            el("div", { class: "mc-name", text: title || b.content }),
            title ? el("div", { class: "mc-meta", text: b.content }) : null,
            el("div", { class: "mc-meta" },
              el("span", { class: "mc-tag", text: _memKind(b.kind) }),
              " · ", _originNode(b.source, b.source_ref, b.conversation_id))),
          el("button", { class: "mc-del", text: t("mgmt.delete"),
            onclick: async () => {
              if (!window.confirm(t("mem.del_confirm", { c: (title || b.content).slice(0, 40) }))) return;
              await _postJSON("/api/memory/remove", { content: b.content });
              await renderMemoryPanel();
            } }));
      },
    }));
  }
}

async function open(): Promise<void> {
  openMgmtModal(t("mgmt.memory_title")); await renderMemoryPanel();
}

const KarvyMemoryPanel = { open };
(window as unknown as { KarvyMemoryPanel: typeof KarvyMemoryPanel }).KarvyMemoryPanel = KarvyMemoryPanel;
export { KarvyMemoryPanel };
