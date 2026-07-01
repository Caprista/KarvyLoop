/* dom.ts — 纯 DOM / fetch 叶子工具(从 app.js 抽出,dev-report #4 slice 3)。
 * 无状态、不闭包 app.js 内部 → 可干净抽。app.js 顶部把裸名 el/_getJSON/_postJSON 重绑到这里的
 * 全局(`var el = window.KarvyDom.el`),760+ 处调用点一行不改。暴露 window.KarvyDom 契约。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;

// el("tag", {class, text, onClick, ...attrs}, ...children):diff-patch 风格 DOM 构造(全前端在用)。
function el(tag: string, attrs?: Attrs | null, ...children: Child[]): HTMLElement {
  const e = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      const v = attrs[k];
      if (k === "class") e.className = String(v);
      else if (k === "text") e.textContent = String(v);
      else if (k.startsWith("on") && typeof v === "function") {
        e.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
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

// GET → JSON;任何失败 → null(调用方按 null 兜底)。
async function getJSON(url: string): Promise<unknown> {
  try { const r = await fetch(url); if (r.ok) return await r.json(); } catch { /* 网络/解析失败 → null */ }
  return null;
}

interface PostResult { ok: boolean; status: number; data: Record<string, unknown> }
// POST JSON;ok = HTTP ok 且 body.ok !== false(与后端 {ok:false} 约定一致)。
async function postJSON(url: string, payload: unknown): Promise<PostResult> {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let d: Record<string, unknown> = {};
  try { d = await r.json(); } catch { /* 非 JSON 响应 → {} */ }
  return { ok: r.ok && d.ok !== false, status: r.status, data: d };
}

const KarvyDom = { el, getJSON, postJSON };
(window as unknown as { KarvyDom: typeof KarvyDom }).KarvyDom = KarvyDom;
export { KarvyDom };
