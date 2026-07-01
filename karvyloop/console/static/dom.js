var KarvyDomBundle = (function(exports) {
  "use strict";
  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (k === "class") e.className = String(v);
        else if (k === "text") e.textContent = String(v);
        else if (k.startsWith("on") && typeof v === "function") {
          e.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (v != null) {
          e.setAttribute(k, String(v));
        }
      }
    }
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  async function getJSON(url) {
    try {
      const r = await fetch(url);
      if (r.ok) return await r.json();
    } catch {
    }
    return null;
  }
  async function postJSON(url, payload) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    let d = {};
    try {
      d = await r.json();
    } catch {
    }
    return { ok: r.ok && d.ok !== false, status: r.status, data: d };
  }
  const KarvyDom = { el, getJSON, postJSON };
  window.KarvyDom = KarvyDom;
  exports.KarvyDom = KarvyDom;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
