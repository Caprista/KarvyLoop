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
// id / tool_use_id 是**稳定配对锚点**:后端 render_events.py 给每个 tool_call 带 `id`、
// 每个 tool_result 带对应的 `tool_use_id`(源自模型的 tool_use_id,LLM 协议级稳定)。
// 归组不靠数组顺序(刷新/分页/chat_history 重建后顺序可乱)——靠 id↔tool_use_id 显式匹配。
interface RenderEvent {
  type: "text" | "thinking" | "tool_call" | "tool_result" | "terminal";
  id?: string;            // tool_call 的稳定 id(配对锚点)
  tool_use_id?: string;   // tool_result 指回它所属 tool_call 的 id
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

// T5(docs/83 弱机):highlight.js(~122KB)+ GitHub 主题 CSS 不再首屏常驻 —— 首次真的遇到
// 代码块才注入(同 driver/画布懒加载范式:载入以全局真出现为准、失败清缓存可重试)。
// 无代码块的会话零加载;载入前已渲染的块在 promise 回调里补高亮(块引用已捕获,不闪不丢)。
let _hlLoading: Promise<void> | null = null;
function _ensureHighlight(): Promise<void> {
  if (_hljs()) return Promise.resolve();
  if (_hlLoading) return _hlLoading;
  if (!document.getElementById("hljs-css")) {
    const l = document.createElement("link");
    l.id = "hljs-css";
    l.rel = "stylesheet";
    l.href = "/static/vendor/highlight-github.min.css";
    document.head.appendChild(l);
  }
  _hlLoading = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.id = "hljs-js";
    s.src = "/static/vendor/highlight.min.js";
    s.onload = () => (_hljs() ? resolve() : reject(new Error("hljs global missing")));
    s.onerror = () => { _hlLoading = null; s.remove(); reject(new Error("highlight.min.js load failed")); };
    document.head.appendChild(s);
  });
  return _hlLoading;
}

// 代码高亮:在**已消毒的 DOM** 上跑 highlight.js(只加 hljs 样式 span,不注入脚本 → 安全)。
function _highlight(div: HTMLElement): void {
  const blocks = div.querySelectorAll("pre code");
  if (!blocks.length) return;   // 无代码块 → highlight.js 一个字节不拉
  const run = (): void => {
    const hl = _hljs();
    if (!hl || typeof hl.highlightElement !== "function") return;
    for (let i = 0; i < blocks.length; i++) {
      try { hl.highlightElement(blocks[i]); } catch { /* 单块失败不影响其余 */ }
    }
  };
  if (_hljs()) { run(); return; }
  _ensureHighlight().then(run).catch(() => { /* 可选增强:加载失败静默降级(代码无色但仍可读) */ });
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

// ---- 编辑类工具的 diff 视图(edit_file:tool_call.input 已带 old_string/new_string,
//      前端本来就拿得到 → 不用执行器改就能渲增删行 diff;write_file 只有"改后"无"改前",
//      渲不成真 diff,退化成写入摘要,不硬造)----

// 最小 LCS 行 diff(通用文本算法,非"重造 markdown/消毒"那种深水区 → 自造不违"通用基建必借")。
// 返回 {op,text} 行序列:op ∈ "="(未变)/"-"(删)/"+"(增)。O(n*m) 表,行数封顶防大文件卡 UI。
type DiffLine = { op: "=" | "-" | "+"; text: string };
const _DIFF_MAX_LINES = 400;   // 每侧行数上限;超了不算 LCS(退化成整块删+整块增),防 O(n*m) 撑爆
function _lineDiff(before: string, after: string): DiffLine[] {
  const a = (before || "").split("\n");
  const b = (after || "").split("\n");
  // 超大文件:不跑 LCS(会卡),直接整块替换(仍是可读 diff,只是不对齐)
  if (a.length > _DIFF_MAX_LINES || b.length > _DIFF_MAX_LINES) {
    return [...a.map((t): DiffLine => ({ op: "-", text: t })),
            ...b.map((t): DiffLine => ({ op: "+", text: t }))];
  }
  const n = a.length, m = b.length;
  // dp[i][j] = LCS(a[i:], b[j:]) 长度
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ op: "=", text: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ op: "-", text: a[i] }); i++; }
    else { out.push({ op: "+", text: b[j] }); j++; }
  }
  while (i < n) { out.push({ op: "-", text: a[i] }); i++; }
  while (j < m) { out.push({ op: "+", text: b[j] }); j++; }
  return out;
}

// 从 tool_call.input 抽取可渲 diff 的编辑意图(before/after)。
// edit_file:old_string→new_string 是真 before/after(局部替换,渲这段就够);
// 其它工具(write_file 无"改前" / 非编辑工具)→ null(不渲 diff)。
function _editDiffSignal(name: string, input: unknown): { before: string; after: string } | null {
  if (name !== "edit_file" || !input || typeof input !== "object") return null;
  const obj = input as Record<string, unknown>;
  const before = obj.old_string, after = obj.new_string;
  if (typeof before !== "string" || typeof after !== "string") return null;
  if (before === after) return null;   // 无变化不渲 diff
  return { before, after };
}

// 渲染 diff 行进容器:每行走 textContent(**绝不 innerHTML** → 文件内容里的 <script>/HTML 不执行,
// XSS 天然不出),只靠 CSS class 着色(增绿删红)。等价于"消毒后再着色":textContent 即最强消毒。
function _renderDiff(container: HTMLElement, before: string, after: string): void {
  const lines = _lineDiff(before, after);
  const block = _el("pre", "tool-diff");
  for (const ln of lines) {
    const row = _el("div", "diff-line diff-" + (ln.op === "=" ? "ctx" : ln.op === "-" ? "del" : "add"));
    const gutter = _el("span", "diff-gutter");
    gutter.textContent = ln.op === "=" ? " " : ln.op;   // +/-/(空)
    const body = _el("span", "diff-text");
    body.textContent = ln.text;                          // 纯文本 → XSS 不出
    row.appendChild(gutter); row.appendChild(body);
    block.appendChild(row);
  }
  container.appendChild(block);
  _wrapWithCopy(block);   // 复制走的是 innerText(渲染后的可见文本),仍安全
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
    // 编辑类工具(edit_file)→ 渲增删行 diff(比整块 JSON 输入更好读);其余 → JSON 输入。
    const _sig = _editDiffSignal(ev.name || "", ev.input);
    if (_sig) {
      _renderDiff(card, _sig.before, _sig.after);
    } else {
      const body = _el("pre", "tool-card-body");
      try { body.textContent = JSON.stringify(ev.input, null, 2); } catch { body.textContent = String(ev.input); }
      card.appendChild(body);
      _wrapWithCopy(body);   // 指令/输入框加复制按钮
    }
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

// 过程区渲染:把每个 tool_call 与它的 tool_result 按**稳定锚点**(tool_call.id ↔ tool_result.tool_use_id)
// 配对成一个 .tool-group 单元 —— 归组不靠数组顺序(chat_history 重建 / 分页 / 流式补齐后顺序可扰动),
// 靠 id 显式匹配,call↔return 永远同组、可重建。缺 id 的(老数据 / 顺序保真足够的场景)退回按
// **紧邻的下一条 tool_result** 配对(与旧顺序语义一致,0 回归);配不上的孤儿 result 独立渲染。
function _renderProcessGrouped(inner: HTMLElement, processEvents: RenderEvent[]): void {
  // 先建 id → tool_result 索引(带 id 的走稳定配对)
  const resById: Record<string, RenderEvent> = {};
  for (const e of processEvents) {
    if (e.type === "tool_result" && e.tool_use_id) resById[e.tool_use_id] = e;
  }
  const consumed = new Set<RenderEvent>();   // 已被某 tool_call 组吸收的 result,不再独立渲染
  for (let i = 0; i < processEvents.length; i++) {
    const ev = processEvents[i];
    if (ev.type === "tool_result" && consumed.has(ev)) continue;   // 已归组,跳过
    if (ev.type === "tool_call") {
      // 配对:优先稳定 id;缺 id 退回"紧邻下一条未消费 result"(旧顺序语义)
      let res: RenderEvent | null = (ev.id && resById[ev.id]) || null;
      if (!res) {
        for (let j = i + 1; j < processEvents.length; j++) {
          const cand = processEvents[j];
          if (cand.type === "tool_call") break;   // 撞到下一个 call → 本 call 无紧邻 result
          if (cand.type === "tool_result" && !cand.tool_use_id && !consumed.has(cand)) { res = cand; break; }
        }
      }
      const group = _el("div", "tool-group");
      if (ev.id) group.setAttribute("data-tool-id", ev.id);   // 稳定锚点落 DOM(可重建/可测)
      renderEvent(group, ev);
      if (res) { renderEvent(group, res); consumed.add(res); }
      inner.appendChild(group);
    } else {
      renderEvent(inner, ev);   // text / thinking / terminal / 孤儿 result → 原样
    }
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
    _renderProcessGrouped(inner, processEvents);   // 稳定锚点配对 call↔return
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
