var KarvyMemoryPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const _KW = window.KarvyWidgets;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody;
  const _formMsg = _KM.formMsg, _setMsg = _KM.setMsg;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  const _md = (target, text) => {
    const r = window.KarvyRender;
    if (r) r.appendMarkdown(target, text);
    else target.textContent = text;
  };
  function _memKind(k) {
    const m = t("mem.kind_" + (k || "fact"));
    return m.indexOf("mem.kind_") === 0 ? k || "" : m;
  }
  function _memSrc(s) {
    const m = t("mem.src_" + (s || "ingest"));
    return m.indexOf("mem.src_") === 0 ? s || "" : m;
  }
  function _origin(source, sourceRef) {
    const ref = (sourceRef || "").trim();
    if (/^https?:\/\//.test(ref)) {
      let short = ref.replace(/^https?:\/\//, "").replace(/\/+$/, "");
      if (short.length > 46) short = short.slice(0, 44) + "…";
      return { text: short, href: ref };
    }
    if (ref.indexOf("text:") === 0) return { text: t("mem.src_pasted"), href: "" };
    return { text: _memSrc(source), href: "" };
  }
  function _originNode(source, sourceRef) {
    const o = _origin(source, sourceRef);
    return o.href ? el("a", { class: "mc-src-link", href: o.href, target: "_blank", text: o.text, title: o.href }) : el("span", { class: "mc-src", text: o.text });
  }
  const _NS = "http://www.w3.org/2000/svg";
  const _nodeLabel = (n) => (n.title || "").trim() || (n.content || "").slice(0, 12);
  const _raf = (fn) => {
    typeof requestAnimationFrame === "function" ? requestAnimationFrame(fn) : setTimeout(fn, 0);
  };
  function _sparsifyForDisplay(nodes, edges) {
    const K = 2;
    const strength = (e) => (e.via ? e.via.length : 1) + (e.semantic ? 100 : 0);
    const per = nodes.map(() => []);
    edges.forEach((e, idx) => {
      per[e.source].push(idx);
      per[e.target].push(idx);
    });
    const keep = /* @__PURE__ */ new Set();
    edges.forEach((e, idx) => {
      if (e.semantic) keep.add(idx);
    });
    per.forEach((list) => {
      list.sort((a, b) => strength(edges[b]) - strength(edges[a]));
      for (let n = 0; n < Math.min(K, list.length); n++) keep.add(list[n]);
    });
    return edges.filter((_, idx) => keep.has(idx));
  }
  function _displayGraph(nodes, edges) {
    const pruned = _sparsifyForDisplay(nodes, edges);
    const deg = nodes.map(() => 0);
    pruned.forEach((e) => {
      deg[e.source]++;
      deg[e.target]++;
    });
    const nodes2 = nodes.map((n, i) => ({ ...n, degree: deg[i] }));
    return { nodes: nodes2, edges: pruned };
  }
  function _simComponent(members, edges, pos) {
    const M = members.length;
    if (M === 1) {
      pos[members[0]] = { x: 0, y: 0 };
      return;
    }
    const set = new Set(members);
    const sub = edges.filter((e) => set.has(e.source) && set.has(e.target));
    const edgeLen = (e) => {
      const s = (e.via ? e.via.length : 1) + (e.semantic ? 2 : 0);
      return Math.max(26, 72 - 9 * s);
    };
    const GA = Math.PI * (3 - Math.sqrt(5));
    members.forEach((i, k) => {
      const a = k * GA, r = Math.sqrt(k + 0.5) * 22;
      pos[i] = { x: r * Math.cos(a), y: r * Math.sin(a), vx: 0, vy: 0 };
    });
    const REP = 900, LINK = 0.25, CENTER = 0.02, DECAY = 0.7, RMAX = 260;
    const ITER = M > 120 ? 300 : 450;
    let alpha = 1;
    for (let it = 0; it < ITER; it++) {
      for (let a = 0; a < M; a++) for (let b = a + 1; b < M; b++) {
        const i = members[a], j = members[b];
        const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y, d2 = dx * dx + dy * dy, d = Math.sqrt(d2) || 0.01;
        if (d > RMAX) continue;
        const f = REP * alpha / d2, ux = dx / d, uy = dy / d;
        pos[i].vx += ux * f;
        pos[i].vy += uy * f;
        pos[j].vx -= ux * f;
        pos[j].vy -= uy * f;
      }
      for (const e of sub) {
        const dx = pos[e.target].x - pos[e.source].x, dy = pos[e.target].y - pos[e.source].y, d = Math.hypot(dx, dy) || 0.01;
        const f = LINK * alpha * (d - edgeLen(e)), ux = dx / d, uy = dy / d;
        pos[e.source].vx += ux * f;
        pos[e.source].vy += uy * f;
        pos[e.target].vx -= ux * f;
        pos[e.target].vy -= uy * f;
      }
      for (const i of members) {
        pos[i].vx += -pos[i].x * CENTER * alpha;
        pos[i].vy += -pos[i].y * CENTER * alpha;
      }
      for (const i of members) {
        pos[i].vx *= DECAY;
        pos[i].vy *= DECAY;
        pos[i].x += pos[i].vx;
        pos[i].y += pos[i].vy;
      }
      alpha *= 0.992;
    }
    let cx = 0, cy = 0;
    for (const i of members) {
      cx += pos[i].x;
      cy += pos[i].y;
    }
    cx /= M;
    cy /= M;
    for (const i of members) {
      pos[i].x -= cx;
      pos[i].y -= cy;
    }
  }
  function _forceLayout(nodes, edges) {
    const N = nodes.length;
    const rad = (i) => {
      var _a;
      return 2.5 + Math.min(6, (((_a = nodes[i]) == null ? void 0 : _a.degree) || 0) * 0.6);
    };
    if (!N) return { pos: [], rad };
    const parent = nodes.map((_, i) => i);
    const find = (x) => {
      while (parent[x] !== x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
      }
      return x;
    };
    for (const e of edges) parent[find(e.source)] = find(e.target);
    const comps = /* @__PURE__ */ new Map();
    for (let i = 0; i < N; i++) {
      const r = find(i);
      if (!comps.has(r)) comps.set(r, []);
      comps.get(r).push(i);
    }
    const pos = nodes.map(() => ({ x: 0, y: 0 }));
    const info = [];
    const singles = [];
    for (const members of comps.values()) {
      if (members.length === 1) {
        singles.push(members[0]);
        continue;
      }
      _simComponent(members, edges, pos);
      let R = 0;
      for (const i of members) R = Math.max(R, Math.hypot(pos[i].x, pos[i].y) + rad(i));
      info.push({ members, r: R + 8 });
    }
    if (singles.length) {
      const cols = Math.ceil(Math.sqrt(singles.length)), gap = 26;
      singles.forEach((idx, k) => {
        pos[idx] = { x: k % cols * gap, y: Math.floor(k / cols) * gap };
      });
      let cx = 0, cy = 0;
      for (const i of singles) {
        cx += pos[i].x;
        cy += pos[i].y;
      }
      cx /= singles.length;
      cy /= singles.length;
      let R = 0;
      for (const i of singles) {
        pos[i].x -= cx;
        pos[i].y -= cy;
        R = Math.max(R, Math.hypot(pos[i].x, pos[i].y) + rad(i));
      }
      info.push({ members: singles, r: R + 8 });
    }
    info.sort((a, b) => b.r - a.r);
    const GA = Math.PI * (3 - Math.sqrt(5));
    const placed = [];
    for (const ci of info) {
      let px = 0, py = 0;
      if (placed.length) {
        for (let tt = 1; tt < 4e3; tt++) {
          const a = tt * GA, rr = Math.sqrt(tt) * (ci.r * 0.5 + 16);
          px = rr * Math.cos(a);
          py = rr * Math.sin(a);
          let ok = true;
          for (const p of placed) if (Math.hypot(px - p.x, py - p.y) < p.r + ci.r + 12) {
            ok = false;
            break;
          }
          if (ok) break;
        }
      }
      placed.push({ x: px, y: py, r: ci.r });
      for (const i of ci.members) {
        pos[i].x += px;
        pos[i].y += py;
      }
    }
    const out = pos.map((q) => ({ x: q.x, y: q.y }));
    for (let pass = 0; pass < 40; pass++) for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      const dx = out[i].x - out[j].x, dy = out[i].y - out[j].y, d = Math.hypot(dx, dy) || 0.01, min = rad(i) + rad(j) + 10;
      if (d < min) {
        const push = (min - d) / 2, ux = dx / d, uy = dy / d;
        out[i].x += ux * push;
        out[i].y += uy * push;
        out[j].x -= ux * push;
        out[j].y -= uy * push;
      }
    }
    return { pos: out, rad };
  }
  let _tipEl = null;
  function _showTip(x, y, title, body) {
    if (!_tipEl) {
      _tipEl = document.createElement("div");
      _tipEl.className = "mem-tip";
      document.body.appendChild(_tipEl);
    }
    const tp = _tipEl;
    tp.innerHTML = "";
    const h = document.createElement("div");
    h.className = "mem-tip-title";
    h.textContent = title;
    tp.appendChild(h);
    if (body) {
      const b = document.createElement("div");
      b.className = "mem-tip-body";
      b.textContent = body;
      tp.appendChild(b);
    }
    tp.style.display = "block";
    const w = tp.offsetWidth || 220, hh = tp.offsetHeight || 60;
    tp.style.left = Math.max(6, Math.min(x + 14, (window.innerWidth || 1024) - w - 8)) + "px";
    tp.style.top = Math.max(6, Math.min(y + 14, (window.innerHeight || 768) - hh - 8)) + "px";
  }
  function _hideTip() {
    if (_tipEl) _tipEl.style.display = "none";
  }
  function _graphSvg(nodes, edges, layout, big, onSelect) {
    const { pos } = layout;
    const N = nodes.length;
    const nbr = nodes.map(() => /* @__PURE__ */ new Set());
    for (const e of edges) {
      nbr[e.source].add(e.target);
      nbr[e.target].add(e.source);
    }
    const maxDeg = Math.max(1, ...nodes.map((_, i) => nbr[i].size));
    const rankByDeg = nodes.map((_, i) => i).sort((a, b) => nbr[b].size - nbr[a].size);
    const rank = new Array(N);
    rankByDeg.forEach((idx, r) => rank[idx] = r);
    const VW = 1e3, VH = 640, pad = 90;
    const xs = pos.map((p) => p.x), ys = pos.map((p) => p.y);
    const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
    const gw = maxx - minx || 1, gh = maxy - miny || 1;
    const s0 = Math.min((VW - 2 * pad) / gw, (VH - 2 * pad) / gh);
    const ox = (VW - gw * s0) / 2 - minx * s0, oy = (VH - gh * s0) / 2 - miny * s0;
    const P = pos.map((p) => ({ x: p.x * s0 + ox, y: p.y * s0 + oy }));
    const svg = document.createElementNS(_NS, "svg");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("class", "mem-graph" + (big ? " big" : ""));
    const edgeEls = [];
    for (const e of edges) {
      const l = document.createElementNS(_NS, "line");
      l.setAttribute("x1", String(P[e.source].x));
      l.setAttribute("y1", String(P[e.source].y));
      l.setAttribute("x2", String(P[e.target].x));
      l.setAttribute("y2", String(P[e.target].y));
      l.setAttribute("class", "mem-edge" + (e.semantic ? " semantic" : ""));
      const tt = document.createElementNS(_NS, "title");
      tt.textContent = (e.via || []).join(" · ");
      l.appendChild(tt);
      svg.appendChild(l);
      l._e = e;
      edgeEls.push(l);
    }
    const nodeEls = [], labelEls = [], hitEls = [];
    const baseR = (i) => 2 + Math.min(5, nbr[i].size * 0.5);
    for (let i = 0; i < N; i++) {
      const c = document.createElementNS(_NS, "circle");
      c.setAttribute("cx", String(P[i].x));
      c.setAttribute("cy", String(P[i].y));
      c.setAttribute("class", "mem-node " + (nodes[i].kind === "preference" ? "pref" : "fact"));
      c.setAttribute("fill-opacity", (0.42 + 0.58 * (nbr[i].size / maxDeg)).toFixed(2));
      svg.appendChild(c);
      nodeEls.push(c);
      const tx = document.createElementNS(_NS, "text");
      tx.setAttribute("x", String(P[i].x));
      tx.setAttribute("class", "mem-label");
      tx.setAttribute("text-anchor", "middle");
      tx.textContent = _nodeLabel(nodes[i]);
      svg.appendChild(tx);
      labelEls.push(tx);
      const hc = document.createElementNS(_NS, "circle");
      hc.setAttribute("cx", String(P[i].x));
      hc.setAttribute("cy", String(P[i].y));
      hc.setAttribute("class", "mem-hit");
      hitEls.push(hc);
    }
    hitEls.forEach((h) => svg.appendChild(h));
    let vbx = 0, vby = 0, vbw = VW, vbh = VH, curScale = 1;
    const TARGET_FONT = 12.5;
    const refresh = () => {
      svg.setAttribute("viewBox", `${vbx} ${vby} ${vbw} ${vbh}`);
      let scale = 0;
      const ctm = svg.getScreenCTM ? svg.getScreenCTM() : null;
      if (ctm && ctm.a) scale = Math.hypot(ctm.a, ctm.b);
      else if (svg.clientWidth) scale = svg.clientWidth / vbw;
      if (!scale) scale = 1;
      curScale = scale;
      const fontU = TARGET_FONT / scale;
      const zoom = VW / vbw;
      const K = Math.min(N, Math.max(3, Math.round(4 * Math.pow(zoom, 1.35))));
      const HIT = 13;
      for (let i = 0; i < N; i++) {
        const rU = baseR(i) / scale;
        nodeEls[i].setAttribute("r", String(rU));
        hitEls[i].setAttribute("r", String(Math.max(baseR(i), HIT) / scale));
        labelEls[i].setAttribute("font-size", fontU.toFixed(2));
        labelEls[i].setAttribute("y", String(P[i].y - rU - 3 / scale));
        labelEls[i].classList.toggle("lod", rank[i] < K);
      }
    };
    let selected = null;
    const applyFocus = (i) => {
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
        tx.classList.toggle("lbl-on", j === i || near.has(j));
        tx.classList.toggle("dim", j !== i && !near.has(j));
      });
      edgeEls.forEach((l) => {
        const e = l._e, on = e.source === i || e.target === i;
        l.classList.toggle("lit", on);
        l.classList.toggle("dim", !on);
      });
    };
    hitEls.forEach((hc, i) => {
      hc.addEventListener("mouseenter", (ev) => {
        _showTip(ev.clientX, ev.clientY, _nodeLabel(nodes[i]), nodes[i].content || "");
        if (selected === null) applyFocus(i);
      });
      hc.addEventListener("mousemove", (ev) => _showTip(ev.clientX, ev.clientY, _nodeLabel(nodes[i]), nodes[i].content || ""));
      hc.addEventListener("mouseleave", () => {
        _hideTip();
        if (selected === null) applyFocus(null);
        else applyFocus(selected);
      });
      hc.addEventListener("click", (ev) => {
        ev.stopPropagation();
        select(selected === i ? null : i);
      });
    });
    svg.addEventListener("click", () => {
      if (selected !== null) select(null);
    });
    const highlight = (q) => {
      const query = (q || "").trim().toLowerCase();
      const hit = /* @__PURE__ */ new Set();
      if (query) nodes.forEach((n, i) => {
        if ((_nodeLabel(n) + " " + (n.content || "")).toLowerCase().includes(query)) hit.add(i);
      });
      const on = query.length > 0;
      nodeEls.forEach((c, i) => c.classList.toggle("dim", on && !hit.has(i)));
      labelEls.forEach((tx, i) => {
        tx.classList.toggle("dim", on && !hit.has(i));
        tx.classList.toggle("lbl-on", on && hit.has(i));
      });
      edgeEls.forEach((l) => {
        const e = l._e;
        l.classList.toggle("dim", on && !(hit.has(e.source) || hit.has(e.target)));
      });
    };
    const toUser = (cx, cy) => {
      const ctm = svg.getScreenCTM ? svg.getScreenCTM() : null;
      if (ctm && svg.createSVGPoint) {
        const pt = svg.createSVGPoint();
        pt.x = cx;
        pt.y = cy;
        const u = pt.matrixTransform(ctm.inverse());
        return { x: u.x, y: u.y };
      }
      return { x: vbx + vbw / 2, y: vby + vbh / 2 };
    };
    const fit = () => {
      vbx = 0;
      vby = 0;
      vbw = VW;
      vbh = VH;
      refresh();
    };
    const zoomAt = (cx, cy, f) => {
      const u = toUser(cx, cy);
      const nw = Math.max(VW / 9, Math.min(VW * 1.15, vbw / f)), nh = nw * (vbh / vbw);
      vbx = u.x - (u.x - vbx) * (nw / vbw);
      vby = u.y - (u.y - vby) * (nh / vbh);
      vbw = nw;
      vbh = nh;
      refresh();
    };
    const panBy = (dx, dy) => {
      vbx -= dx / curScale;
      vby -= dy / curScale;
      refresh();
    };
    const centerOn = (i) => {
      vbx = P[i].x - vbw / 2;
      vby = P[i].y - vbh / 2;
      refresh();
    };
    const neighbors = (i) => [...nbr[i]];
    const select = (i, opts) => {
      selected = i;
      applyFocus(i);
      if (i !== null && opts && opts.center) centerOn(i);
      if (onSelect) onSelect(i);
    };
    fit();
    return { svg, highlight, fit, zoomAt, panBy, select, neighbors };
  }
  async function renderMemoryGraph(container) {
    container.innerHTML = "";
    const g = await _getJSON("/api/memory/graph");
    const nodes = g && g.nodes || [];
    const edges = g && g.edges || [];
    if (!nodes.length) {
      container.appendChild(el("div", { class: "mgmt-empty", text: t("mem.empty") }));
      return;
    }
    const disp = _displayGraph(nodes, edges);
    const layout = _forceLayout(disp.nodes, disp.edges);
    const built = _graphSvg(disp.nodes, disp.edges, layout, false);
    const wrap = el(
      "div",
      { class: "mem-graph-wrap" },
      built.svg,
      el(
        "div",
        { class: "mem-graph-hover", onclick: () => _openGraphFullscreen(nodes, edges) },
        el("button", { class: "mem-graph-plus", text: "+" })
      )
    );
    container.appendChild(wrap);
    _raf(() => built.fit());
  }
  function _openGraphFullscreen(nodes, edges) {
    const overlay = el("div", { class: "mem-graph-overlay" });
    const disp = _displayGraph(nodes, edges);
    const layout = _forceLayout(disp.nodes, disp.edges);
    const detail = el("div", { class: "mem-detail hidden" });
    const renderDetail = (i) => {
      detail.innerHTML = "";
      if (i === null) {
        detail.classList.add("hidden");
        return;
      }
      detail.classList.remove("hidden");
      const n = disp.nodes[i];
      detail.appendChild(el("button", { class: "mem-detail-close", text: "✕", onclick: () => built.select(null) }));
      detail.appendChild(el("div", { class: "mem-detail-title", text: _nodeLabel(n) }));
      const meta = el("div", { class: "mem-detail-meta" });
      meta.appendChild(el("span", { class: "mem-detail-kind", text: _memKind(n.kind) }));
      meta.appendChild(el("span", { text: " · " + t("mem.detail_source") + ": " }));
      const o = _origin(n.source, n.source_ref);
      meta.appendChild(o.href ? el("a", { class: "mem-detail-src-link", href: o.href, target: "_blank", text: o.text, title: o.href }) : el("span", { text: o.text }));
      detail.appendChild(meta);
      const body = el("div", { class: "mem-detail-body" });
      _md(body, n.content || "");
      detail.appendChild(body);
      const nb = built.neighbors(i);
      detail.appendChild(el("div", { class: "mem-detail-rel-label", text: t("mem.detail_related", { n: nb.length }) }));
      const rels = el("div", { class: "mem-detail-rels" });
      if (nb.length) nb.forEach((j) => rels.appendChild(
        el("button", { class: "mem-rel", text: _nodeLabel(disp.nodes[j]), onclick: () => built.select(j, { center: true }) })
      ));
      else rels.appendChild(el("div", { class: "mem-detail-norel", text: t("mem.detail_no_rel") }));
      detail.appendChild(rels);
    };
    const built = _graphSvg(disp.nodes, disp.edges, layout, true, renderDetail);
    const stage = el("div", { class: "mem-graph-stage" }, built.svg);
    const search = el("input", {
      class: "mem-graph-search",
      type: "text",
      placeholder: t("mem.graph_search"),
      oninput: (e) => built.highlight(e.target.value)
    });
    const bar = el(
      "div",
      { class: "mem-graph-bar" },
      el("span", { class: "mem-graph-title", text: t("mem.graph") + " · " + t("mem.graph_count", { n: nodes.length }) }),
      el("span", { class: "mem-graph-hint", text: t("mem.graph_hint") }),
      search,
      el("button", { class: "mem-graph-close", text: "✕", onclick: () => overlay.remove() })
    );
    let dragging = false, lx = 0, ly = 0;
    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      built.zoomAt(e.clientX, e.clientY, e.deltaY > 0 ? 0.85 : 1.18);
    }, { passive: false });
    stage.addEventListener("mousedown", (e) => {
      dragging = true;
      lx = e.clientX;
      ly = e.clientY;
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - lx, dy = e.clientY - ly;
      lx = e.clientX;
      ly = e.clientY;
      built.panBy(dx, dy);
    });
    window.addEventListener("mouseup", () => {
      dragging = false;
    });
    overlay.appendChild(bar);
    overlay.appendChild(stage);
    overlay.appendChild(detail);
    document.body.appendChild(overlay);
    _raf(() => built.fit());
    setTimeout(() => search.focus(), 30);
  }
  async function _reloadDistill(wrap) {
    const data = await _getJSON("/api/memory/distill");
    const pending = data && data.pending;
    if (pending) _renderDistillPending(wrap, pending);
    else _renderDistillFeed(wrap);
  }
  function _renderDistillFeed(wrap) {
    wrap.innerHTML = "";
    wrap.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.feed_label") }));
    wrap.appendChild(el("div", { class: "mgmt-hint", text: t("distill.feed_hint") }));
    const ta = el("textarea", { placeholder: t("distill.feed_ph") });
    const msg = _formMsg();
    const submit = el("button", {
      class: "mgmt-submit",
      text: t("distill.feed_btn"),
      onclick: async () => {
        const material = ta.value.trim();
        if (!material) return;
        submit.disabled = true;
        _setMsg(msg, true, t("distill.analyzing"));
        const res = await _postJSON("/api/memory/feed", { material });
        submit.disabled = false;
        if (res.ok || res.data && res.data.pending) {
          await _reloadDistill(wrap);
        } else {
          _setMsg(msg, false, res.data && res.data.reason || res.status);
        }
      }
    });
    wrap.appendChild(el("form", { class: "mgmt-form", onsubmit: (e) => e.preventDefault() }, ta, submit, msg));
  }
  function _renderDistillPending(wrap, p) {
    wrap.innerHTML = "";
    wrap.appendChild(el("div", { class: "mgmt-section-title", text: t("distill.pending_title") }));
    if ((p.already_fed || 0) > 0) {
      wrap.appendChild(el("div", { class: "distill-dup", text: t("distill.already_fed", { n: p.already_fed }) }));
    }
    if (p.source_url) wrap.appendChild(el("a", { class: "distill-src", href: p.source_url, target: "_blank", text: p.source_url }));
    const sum = el("div", { class: "distill-summary" });
    _md(sum, p.summary || "");
    wrap.appendChild(sum);
    const tr = el("div", { class: "distill-chat" });
    for (const x of p.transcript || []) {
      const line = el("div", { class: "distill-line " + (x.who === "you" ? "you" : "karvy") });
      line.appendChild(el("span", { class: "distill-who", text: x.who === "you" ? t("chat.you") : t("chat.karvy") }));
      const bd = el("div", { class: "distill-bd" });
      _md(bd, x.text || "");
      line.appendChild(bd);
      tr.appendChild(line);
    }
    wrap.appendChild(tr);
    const cin = el("input", { type: "text", class: "distill-chat-in", placeholder: t("distill.chat_ph") });
    const cmsg = _formMsg();
    const send = el("button", {
      class: "mgmt-submit",
      text: t("distill.chat_send"),
      onclick: async () => {
        const m = cin.value.trim();
        if (!m) return;
        send.disabled = true;
        _setMsg(cmsg, true, "…");
        const res = await _postJSON("/api/memory/distill/chat", { message: m });
        send.disabled = false;
        if (res.ok) {
          cin.value = "";
          await _reloadDistill(wrap);
        } else _setMsg(cmsg, false, res.data && res.data.reason || res.status);
      }
    });
    wrap.appendChild(el("form", { class: "mgmt-form", onsubmit: (e) => e.preventDefault() }, cin, send, cmsg));
    const decideMsg = _formMsg();
    const bar = el("div", { class: "distill-decide" });
    bar.appendChild(el("button", {
      class: "distill-yes",
      text: t("distill.persist"),
      onClick: () => _decideDistill(decideMsg, "persist")
    }));
    bar.appendChild(el("button", {
      class: "distill-no",
      text: t("distill.reject"),
      onClick: () => _decideDistill(decideMsg, "reject")
    }));
    wrap.appendChild(bar);
    wrap.appendChild(decideMsg);
  }
  async function _decideDistill(msg, decision) {
    _setMsg(msg, true, t("distill.deciding"));
    const res = await _postJSON("/api/memory/distill/decide", { decision });
    if (!res.ok) {
      _setMsg(msg, false, res.data && res.data.reason || res.status);
      return;
    }
    await renderMemoryPanel();
  }
  async function _runConsolidate() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.consolidate_btn") }));
    const backRow = el(
      "div",
      { class: "mgmt-row" },
      el("button", { class: "mgmt-inline-link", text: t("role.back"), onclick: () => renderMemoryPanel() })
    );
    const status = el("div", { class: "mgmt-hint", text: t("mem.consolidating") });
    body.appendChild(status);
    body.appendChild(backRow);
    const r = await _postJSON("/api/memory/consolidate/suggest", {});
    status.remove();
    const clusters = r.ok && r.data && r.data.clusters || [];
    if (!clusters.length) {
      body.insertBefore(el("div", { class: "mgmt-empty", text: t("mem.consolidate_none") }), backRow);
      return;
    }
    const list = el("div", { class: "mgmt-list" });
    body.insertBefore(list, backRow);
    for (const c of clusters) {
      const card = el("div", { class: "mgmt-card consolidate-card" });
      card.appendChild(el(
        "div",
        { class: "mc-main" },
        el("div", { class: "mc-name", text: t("mem.consolidate_into", { n: (c.member_contents || []).length }) }),
        el(
          "div",
          { class: "consolidate-target" },
          c.merged_title ? el("span", { class: "mc-tag", text: c.merged_title }) : null,
          el("span", { text: " " + c.merged_content })
        )
      ));
      const mem = el("div", { class: "consolidate-members" });
      (c.member_contents || []).forEach((m, i) => {
        const tt = (c.member_titles || [])[i] || "";
        mem.appendChild(el("div", { class: "consolidate-member", text: "・ " + (tt ? tt + " — " : "") + m }));
      });
      if (c.reason) mem.appendChild(el("div", { class: "mgmt-hint", text: c.reason }));
      card.appendChild(mem);
      const doBtn = el("button", {
        class: "dpref-confirm",
        text: t("mem.consolidate_do"),
        onclick: async () => {
          doBtn.disabled = true;
          const ar = await _postJSON(
            "/api/memory/consolidate/apply",
            { member_contents: c.member_contents, merged_content: c.merged_content, merged_title: c.merged_title || "" }
          );
          if (ar.ok && ar.data && ar.data.ok) card.replaceWith(el("div", {
            class: "mgmt-hint",
            text: t("mem.consolidate_done", { n: ar.data.removed })
          }));
          else doBtn.disabled = false;
        }
      });
      card.appendChild(el("div", { class: "dpref-actions" }, doBtn));
      list.appendChild(card);
    }
  }
  let _kSession = "";
  function _kLine(log, who, text) {
    const line = el("div", { class: "distill-line " + who });
    line.appendChild(el("span", { class: "distill-who", text: who === "you" ? t("chat.you") : t("knowledge.speaker") }));
    const bd = el("div", { class: "distill-bd" });
    _md(bd, text || "");
    line.appendChild(bd);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }
  function _renderSedimentCard(host, card, onDone) {
    const box = el("div", { class: "sediment-card" });
    box.appendChild(el("div", { class: "sediment-head", text: t("sediment.card_title") }));
    box.appendChild(el("div", { class: "sediment-note", text: t("sediment.card_note") }));
    const states = {};
    const submit = el("button", { type: "button", class: "sediment-submit" });
    const updateSubmit = () => {
      const n = Object.values(states).filter((x) => x.action !== "drop").length;
      submit.textContent = n > 0 ? t("sediment.submit", { n }) : t("sediment.submit_zero");
    };
    for (const it of card.items || []) {
      const row = el("div", { class: "sediment-row depth-" + (it.depth || 1) });
      const content = el("span", { class: "sediment-content", text: it.content });
      const setState = (cls) => {
        row.classList.remove("is-keep", "is-edit", "is-drop");
        if (cls) row.classList.add("is-" + cls);
        updateSubmit();
      };
      const bKeep = el("button", { type: "button", class: "sediment-act keep", text: t("sediment.keep") });
      bKeep.addEventListener("click", () => {
        const editing = content.getAttribute("contenteditable") === "true";
        const txt = (content.textContent || "").trim();
        if (editing && txt && txt !== it.content) {
          states[it.id] = { action: "edit", content: txt };
          setState("edit");
        } else {
          states[it.id] = { action: "accept" };
          setState("keep");
        }
        content.setAttribute("contenteditable", "false");
      });
      const bEdit = el("button", { type: "button", class: "sediment-act edit", text: t("sediment.edit") });
      bEdit.addEventListener("click", () => {
        content.setAttribute("contenteditable", "true");
        content.focus();
      });
      const bDrop = el("button", { type: "button", class: "sediment-act drop", text: t("sediment.drop") });
      bDrop.addEventListener("click", () => {
        states[it.id] = { action: "drop" };
        content.setAttribute("contenteditable", "false");
        setState("drop");
      });
      const acts = el("span", { class: "sediment-acts" }, bKeep, bEdit, bDrop);
      row.appendChild(el("span", { class: "sediment-chip", text: t("layer." + it.layer) }));
      row.appendChild(content);
      row.appendChild(acts);
      if (it.needs_attention) row.appendChild(el("div", { class: "sediment-warn", text: t("sediment.attention") }));
      box.appendChild(row);
    }
    const cancel = el("button", { type: "button", class: "sediment-cancel", text: t("sediment.cancel") });
    cancel.addEventListener("click", () => box.remove());
    submit.addEventListener("click", async () => {
      submit.disabled = true;
      const res = await _postJSON("/api/knowledge/sediment", {
        conversation_id: card.conversation_ref,
        items: card.items,
        decisions: states
      });
      if (!res.ok || !(res.data && res.data.ok)) {
        submit.disabled = false;
        return;
      }
      box.remove();
      onDone();
    });
    updateSubmit();
    box.appendChild(el("div", { class: "sediment-foot" }, cancel, submit));
    host.appendChild(box);
    host.scrollTop = host.scrollHeight;
  }
  async function _renderKnowledgeArea(wrap) {
    wrap.innerHTML = "";
    const debt = await _getJSON("/api/knowledge/debt");
    const sessions = debt && debt.sessions || [];
    const side = el("div", { class: "kchat-side" });
    side.appendChild(el("div", {
      class: "kchat-side-head",
      text: t("kchat.side_head", { n: sessions.length }),
      title: t("knowledge.entry_desc")
    }));
    const mkRow = (label, active, cls, onclick) => {
      const r = el("button", { class: "kchat-sess" + (active ? " active" : "") + cls, text: label });
      r.addEventListener("click", onclick);
      side.appendChild(r);
    };
    mkRow(t("kchat.new"), !_kSession, " kchat-sess-new", () => {
      _kSession = "";
      void _renderKnowledgeArea(wrap);
    });
    for (const s of sessions) {
      mkRow(
        "📥 " + (s.snippet || t("conv.untitled")),
        s.id === _kSession,
        "",
        () => {
          _kSession = s.id;
          void _renderKnowledgeArea(wrap);
        }
      );
    }
    const main = el("div", { class: "kchat-main" });
    const log = el("div", { class: "kchat-log" });
    try {
      const d = await _getJSON("/api/memory/distill");
      if (d && d.pending) {
        const pw = el("div", { class: "distill-area kchat-pending" });
        _renderDistillPending(pw, d.pending);
        log.appendChild(pw);
      }
    } catch {
    }
    if (_kSession) {
      try {
        const sess = await _getJSON("/api/knowledge/session?id=" + encodeURIComponent(_kSession));
        for (const turn of sess && sess.turns || []) {
          if (turn.user_intent) _kLine(log, "you", turn.user_intent);
          if (turn.agent_response) _kLine(log, "karvy", turn.agent_response);
        }
      } catch {
      }
    }
    const cin = el("textarea", { class: "kchat-in", rows: "1", placeholder: t("kchat.ph") });
    const send = el("button", { type: "button", class: "kchat-btn kchat-send", text: t("kchat.send") });
    const conv = el("button", {
      type: "button",
      class: "kchat-btn kchat-converge",
      text: t("kchat.converge"),
      title: t("btn.converge.title")
    });
    const msg = _formMsg();
    let _busy = false;
    const typingLine = () => {
      const ln = el("div", { class: "distill-line karvy kchat-typing" });
      ln.appendChild(el("span", { class: "distill-who", text: t("knowledge.speaker") }));
      ln.appendChild(el("div", { class: "distill-bd", text: t("kchat.thinking") }));
      log.appendChild(ln);
      log.scrollTop = log.scrollHeight;
      return ln;
    };
    const doSend = async () => {
      const m = cin.value.trim();
      if (!m) return;
      if (_busy) {
        _setMsg(msg, false, t("kchat.busy"));
        return;
      }
      _busy = true;
      send.disabled = true;
      cin.value = "";
      _kLine(log, "you", m);
      const tl = typingLine();
      const res = await _postJSON("/api/knowledge/chat", { session_id: _kSession, message: m });
      tl.remove();
      _busy = false;
      send.disabled = false;
      if (res.ok && res.data && res.data.ok) {
        _kSession = res.data.session_id;
        _setMsg(msg, true, "");
        _kLine(log, "karvy", res.data.reply);
      } else {
        _kLine(log, "karvy", "(" + t("kchat.failed", { reason: res.data && res.data.reason || String(res.status) }) + ")");
      }
    };
    send.addEventListener("click", doSend);
    cin.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void doSend();
      }
    });
    conv.addEventListener("click", async () => {
      if (!_kSession) {
        _setMsg(msg, false, t("kchat.nothing_yet"));
        return;
      }
      if (_busy) {
        _setMsg(msg, false, t("kchat.busy"));
        return;
      }
      _busy = true;
      conv.disabled = true;
      send.disabled = true;
      const tl = typingLine();
      tl.querySelector(".distill-bd").textContent = t("kchat.converging");
      const res = await _postJSON("/api/knowledge/converge", { session_id: _kSession });
      tl.remove();
      _busy = false;
      conv.disabled = false;
      send.disabled = false;
      if (!res.ok || !(res.data && res.data.ok)) {
        _kLine(log, "karvy", "(" + t("kchat.failed", { reason: res.data && res.data.reason || String(res.status) }) + ")");
        return;
      }
      const card = res.data.card;
      if (!card || !card.n) {
        _kLine(log, "karvy", t("sediment.none"));
        return;
      }
      _renderSedimentCard(log, card, () => {
        _kSession = "";
        void renderMemoryPanel();
      });
    });
    const bar = el("div", { class: "kchat-bar" }, cin, send, conv);
    main.appendChild(log);
    main.appendChild(bar);
    main.appendChild(msg);
    wrap.appendChild(side);
    wrap.appendChild(main);
  }
  let _memTab = "sediment";
  async function renderMemoryPanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    const tabs = el("div", { class: "mem-tabs" });
    const mkTab = (key, label) => {
      const b = el("button", { class: "mem-tab" + (_memTab === key ? " active" : ""), text: label });
      b.addEventListener("click", () => {
        if (_memTab !== key) {
          _memTab = key;
          void renderMemoryPanel();
        }
      });
      tabs.appendChild(b);
    };
    mkTab("sediment", t("mem.tab_sediment"));
    mkTab("library", t("mem.tab_library"));
    body.appendChild(tabs);
    body.classList.toggle("kchat-mode", _memTab === "sediment");
    if (_memTab === "sediment") {
      const kWrap = el("div", { class: "kchat-area" });
      body.appendChild(kWrap);
      await _renderKnowledgeArea(kWrap);
      return;
    }
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("mem.graph") }));
    const graphBox = el("div", { class: "mem-graph-box" });
    body.appendChild(graphBox);
    renderMemoryGraph(graphBox);
    const data = await _getJSON("/api/memory");
    const beliefs = data && data.beliefs || [];
    body.appendChild(el(
      "div",
      { class: "mgmt-section-title" },
      el("span", { text: t("mem.known") + " (" + beliefs.length + ")" }),
      beliefs.length >= 2 ? el("button", {
        class: "mgmt-inline-link mem-consolidate-btn",
        text: t("mem.consolidate_btn"),
        onclick: () => _runConsolidate()
      }) : null
    ));
    if (!beliefs.length) {
      body.appendChild(el("div", { class: "mgmt-empty", text: t("mem.empty") }));
    } else {
      body.appendChild(_KW.pagedList({
        items: beliefs,
        pageSize: 8,
        searchPh: t("mem.search"),
        emptyText: t("mem.empty"),
        searchOf: (b) => (b.title || "") + " " + (b.content || "") + " " + _memKind(b.kind),
        renderItem: (b) => {
          const title = (b.title || "").trim();
          return el(
            "div",
            { class: "mgmt-card" },
            el(
              "div",
              { class: "mc-main" },
              el("div", { class: "mc-name", text: title || b.content }),
              title ? el("div", { class: "mc-meta", text: b.content }) : null,
              el(
                "div",
                { class: "mc-meta" },
                el("span", { class: "mc-tag", text: _memKind(b.kind) }),
                " · ",
                _originNode(b.source, b.source_ref)
              )
            ),
            el("button", {
              class: "mc-del",
              text: t("mgmt.delete"),
              onclick: async () => {
                if (!window.confirm(t("mem.del_confirm", { c: (title || b.content).slice(0, 40) }))) return;
                await _postJSON("/api/memory/remove", { content: b.content });
                await renderMemoryPanel();
              }
            })
          );
        }
      }));
    }
  }
  async function open() {
    openMgmtModal(t("mgmt.memory_title"));
    await renderMemoryPanel();
  }
  const KarvyMemoryPanel = { open };
  window.KarvyMemoryPanel = KarvyMemoryPanel;
  exports.KarvyMemoryPanel = KarvyMemoryPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
