/* render.ts — 模型输出渲染层(原 static/render.js 的 TS 迁移,dev-report #4 slice 1)。
 * 把模型的结构化事件流(text/tool_call/tool_result/terminal)按类型渲染:
 *   text        → markdown(markdown-it)→ DOMPurify 消毒 → HTML
 *   tool_call   → 折叠卡(图标 + 工具名 + 输入摘要;<details> 原生折叠)
 *   tool_result → 输出面板(折叠 + 截断标记)
 *   terminal    → status 行
 * 借业界渲染模式(MIT);clean-room(只取原则)。
 *
 * 安全:模型文本半可信 —— markdown-it 关 html(不吃裸 HTML)+ DOMPurify 兜底消毒,绝不裸 innerHTML。
 * 迁移说明:markdown-it / DOMPurify 现由 npm 打包进本 bundle(带类型);highlight.js(window.hljs)
 * 与 i18n(window.KarvyI18n)仍是全局——尚未迁移,桥接使用。暴露 window.KarvyRender 契约不变。
 */
import markdownit from "markdown-it";
import DOMPurify from "dompurify";

// ---- render-event 形状(原来是 duck-typed any;迁移顺手补类型,捕获字段拼写错)----
interface RenderEvent {
  type: "text" | "thinking" | "tool_call" | "tool_result" | "terminal";
  text?: string;
  name?: string;
  input?: unknown;
  output?: string;
  is_error?: boolean;
  truncated?: boolean;
  ok?: boolean;
  reason?: string;
}

interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
interface Hljs { highlightElement: (el: Element) => void }

function _i18n(): I18n | undefined {
  return (window as unknown as { KarvyI18n?: I18n }).KarvyI18n;
}
function _hljs(): Hljs | undefined {
  return (window as unknown as { hljs?: Hljs }).hljs;
}

const md = markdownit({ html: false, linkify: true, breaks: false });

function _sanitize(html: string): string {
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel"] });
}

// 渲染 markdown → 消毒后的 HTML 字符串(库已打包,不再返回 null;保留签名兼容调用方)
function renderMarkdown(text: string): string {
  return _sanitize(md.render(text || ""));
}

// 代码高亮:在**已消毒的 DOM** 上跑 highlight.js(只加 hljs 样式 span,不注入脚本 → 安全)。
function _highlight(div: HTMLElement): void {
  const hl = _hljs();
  if (!hl || typeof hl.highlightElement !== "function") return;
  const blocks = div.querySelectorAll("pre code");
  for (let i = 0; i < blocks.length; i++) {
    try { hl.highlightElement(blocks[i]); } catch { /* 单块失败不影响其余 */ }
  }
}

// 把文本以 markdown 渲染进容器;失败安全回退裸文本节点(永不裸 innerHTML 未消毒内容)
function appendMarkdown(container: HTMLElement, text: string, cls?: string): HTMLElement {
  const html = renderMarkdown(text);
  const div = document.createElement("div");
  div.className = cls || "md";
  div.innerHTML = html;                 // 已 DOMPurify 消毒
  _highlight(div);                      // 代码块语法高亮(消毒后再跑,安全)
  const pres = div.querySelectorAll("pre");
  for (let i = 0; i < pres.length; i++) _wrapWithCopy(pres[i] as HTMLElement);
  container.appendChild(div);
  return div;
}

const _ICONS: Record<string, string> = {
  read_file: "📖", list_dir: "📂", search_code: "🔎", glob: "🔎", grep: "🔎",
  write_file: "✏️", edit_file: "✏️", run_command: "$", bash: "$",
  web_search: "🌐", network: "🌐",
};
function toolIcon(name: string): string { return _ICONS[name] || "🔧"; }

// 工具输入摘要:挑常见键(path/file/command/...)的值,否则截断的 JSON
function _inputSummary(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const obj = input as Record<string, unknown>;
  const keys = ["path", "file", "file_path", "command", "cmd", "pattern", "query", "url"];
  for (const k of keys) {
    if (obj[k] != null) return String(obj[k]);
  }
  try { return JSON.stringify(input); } catch { return ""; }
}
function _truncate(s: string, n: number): string { s = s || ""; return s.length > n ? s.slice(0, n) + "…" : s; }

function _el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag); if (cls) e.className = cls; return e;
}

// 复制按钮(代码/指令框右上角)。LAN(非 https/localhost)下 navigator.clipboard 可能不可用 →
// execCommand 兜底,保证局域网真机也能复制。
function _fallbackCopy(txt: string): void {
  try {
    const ta = document.createElement("textarea");
    ta.value = txt; ta.style.position = "fixed"; ta.style.left = "-9999px";
    document.body.appendChild(ta); ta.focus(); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta);
  } catch { /* 复制失败不致命 */ }
}
function _copyBtn(getText: () => string): HTMLButtonElement {
  const T = _i18n();
  const label = () => (T ? T.t("render.copy") : "Copy");
  const btn = _el("button", "copy-btn") as HTMLButtonElement;
  btn.type = "button"; btn.textContent = label();
  btn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const txt = getText() || "";
    const done = () => {
      btn.classList.add("copied");
      btn.textContent = T ? T.t("render.copied") : "Copied";
      setTimeout(() => { btn.classList.remove("copied"); btn.textContent = label(); }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done, () => { _fallbackCopy(txt); done(); });
    } else { _fallbackCopy(txt); done(); }
  });
  return btn;
}
// 把一个 <pre> 包进带复制按钮的容器(代码块 / 工具输入 / 工具输出都用)
function _wrapWithCopy(pre: HTMLElement): void {
  if (!pre || !pre.parentNode) return;
  const parent = pre.parentNode as HTMLElement;
  if (parent.classList && parent.classList.contains("code-wrap")) return;
  const wrap = _el("div", "code-wrap");
  parent.insertBefore(wrap, pre);
  wrap.appendChild(pre);
  wrap.appendChild(_copyBtn(() => pre.innerText || pre.textContent || ""));
}

// 渲染单条 render-event 进容器
function renderEvent(container: HTMLElement, ev: RenderEvent): void {
  if (!ev || !ev.type) return;
  if (ev.type === "text") {
    if ((ev.text || "").trim()) appendMarkdown(container, ev.text || "", "md chat-md");
  } else if (ev.type === "thinking") {
    // 推理过程 → 默认折叠(不污染答案;想看再展开)
    if ((ev.text || "").trim()) {
      const T0 = _i18n();
      const det0 = _el("details", "thinking-card");
      const sum0 = _el("summary", "thinking-head");
      sum0.textContent = "💭 " + (T0 ? T0.t("render.thinking") : "思考过程");
      det0.appendChild(sum0);
      const body0 = _el("div", "thinking-body");
      appendMarkdown(body0, ev.text || "");
      det0.appendChild(body0);
      container.appendChild(det0);
    }
  } else if (ev.type === "tool_call") {
    const card = _el("details", "tool-card");
    const sum = _el("summary", "tool-card-head");
    sum.textContent = toolIcon(ev.name || "") + " " + (ev.name || "tool") + "  " + _truncate(_inputSummary(ev.input), 80);
    card.appendChild(sum);
    const body = _el("pre", "tool-card-body");
    try { body.textContent = JSON.stringify(ev.input, null, 2); } catch { body.textContent = String(ev.input); }
    card.appendChild(body);
    _wrapWithCopy(body);   // 指令/输入框加复制按钮
    container.appendChild(card);
  } else if (ev.type === "tool_result") {
    const det = _el("details", "tool-result" + (ev.is_error ? " tool-result-error" : ""));
    const rs = _el("summary", "tool-result-head");
    const T = _i18n();
    const lbl = T ? T.t("render.result") : "result";
    const tr = ev.truncated ? " (" + (T ? T.t("render.truncated") : "truncated") + ")" : "";
    rs.textContent = (ev.is_error ? "⚠ " : "↳ ") + lbl + tr;
    det.appendChild(rs);
    const out = _el("pre", "tool-result-body");
    out.textContent = ev.output || "";
    det.appendChild(out);
    _wrapWithCopy(out);   // 输出框加复制按钮
    container.appendChild(det);
  } else if (ev.type === "terminal") {
    const st = _el("div", "terminal-status" + (ev.ok ? "" : " terminal-error"));
    st.textContent = (ev.ok ? "✓ " : "✗ ") + (ev.reason || "");
    container.appendChild(st);
  }
}

// 有工具调用时 → "过程"默认折叠,只突出最后的"结果"(用户:别给我一堆过程)。
// 纯对话(无工具)→ 照常直接渲染。
function renderEvents(container: HTMLElement, events: RenderEvent[]): void {
  events = events || [];
  const hasTools = events.some((e) => e.type === "tool_call" || e.type === "tool_result");
  if (!hasTools) { events.forEach((ev) => renderEvent(container, ev)); return; }
  // 最后一段非空 text = 最终结果;其余(工具卡 + 中间文字 + terminal)= 过程
  let lastTextIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "text" && (events[i].text || "").trim()) { lastTextIdx = i; break; }
  }
  const processEvents: RenderEvent[] = [];
  let finalText: RenderEvent | null = null;
  events.forEach((ev, idx) => {
    if (idx === lastTextIdx) finalText = ev; else processEvents.push(ev);
  });
  if (processEvents.length) {
    const steps = processEvents.filter((e) => e.type === "tool_call").length;
    const fold = _el("details", "process-fold");
    const head = _el("summary", "process-fold-head");
    const T = _i18n();
    head.textContent = T ? T.t("render.process", { n: steps }) : ("过程(" + steps + " 步)");
    fold.appendChild(head);
    const inner = _el("div", "process-fold-body");
    processEvents.forEach((ev) => renderEvent(inner, ev));
    fold.appendChild(inner);
    container.appendChild(fold);
  }
  if (finalText) appendMarkdown(container, (finalText as RenderEvent).text || "", "md chat-md final-answer");
}

const KarvyRender = {
  renderMarkdown,
  appendMarkdown,
  renderEvents,
  renderEvent,
  toolIcon,
};

// 全局契约(与旧 render.js 完全一致)——未迁的 app.js 照常 window.KarvyRender.*
(window as unknown as { KarvyRender: typeof KarvyRender }).KarvyRender = KarvyRender;

export { KarvyRender };
