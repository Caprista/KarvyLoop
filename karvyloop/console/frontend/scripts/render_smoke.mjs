/* render_smoke.mjs — 真路径验证迁移后的 render bundle(dev-report #4 slice 1)。
 * 在真 DOM(jsdom)里加载构建产物 static/render.js,跑 markdown 渲染 + XSS 消毒 + 事件分派,
 * 断言行为与迁移前一致。这是渲染层的"真走一遍",补 Python 静态测试看不到的运行时。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
// navigator 是 node 全局只读 getter(且渲染路径不用,仅复制按钮点击时用)→ 不设

// 真库 highlight.js 由 index.html 的 <script> 提供 window.hljs;冒烟里注入一个记录桩,
// 既避免拖 121KB 库进测试,又能断言渲染层**真调**了 highlightElement(只加 hljs class,
// 桩绝不注入脚本 → 和真库一样安全)。挂在 IIFE 之前,渲染时 _hljs() 就取得到。
const _hljsCalls = [];
dom.window.hljs = {
  highlightElement: (elArg) => {
    _hljsCalls.push((elArg.tagName || "") + "." + (elArg.className || ""));
    elArg.classList.add("hljs");
  },
};

const here = dirname(fileURLToPath(import.meta.url));
const code = readFileSync(resolve(here, "../../static/render.js"), "utf8");
(0, eval)(code); // 运行 IIFE → 设 window.KarvyRender

const R = dom.window.KarvyRender;
assert.ok(R && typeof R.renderMarkdown === "function", "window.KarvyRender 契约缺失");

// 1) markdown 渲染 + DOMPurify 消毒(XSS 不出)
const html = R.renderMarkdown("# Title\n\nhello **world** <script>alert(1)</script>");
assert.ok(html.includes("<h1>") && html.includes("Title"), "markdown 没渲染出 <h1>");
assert.ok(html.includes("<strong>"), "markdown 没渲染出 **加粗**");
assert.ok(!html.toLowerCase().includes("<script"), "XSS:<script> 未被 DOMPurify 消毒掉");

// 2) 事件分派:有工具 → 过程折叠 + 最终答案;tool_call → 折叠卡
const c = dom.window.document.createElement("div");
R.renderEvents(c, [
  { type: "tool_call", name: "read_file", input: { path: "/etc/hosts" } },
  { type: "tool_result", output: "127.0.0.1" },
  { type: "text", text: "done **ok**" },
]);
assert.ok(c.querySelector(".process-fold"), "有工具调用应折叠'过程'");
assert.ok(c.querySelector(".tool-card"), "tool_call 应渲染折叠卡");
assert.ok(c.querySelector(".final-answer"), "最后一段 text 应作为最终答案");

// 3) 纯对话(无工具)→ 直接渲染,不折叠
const c2 = dom.window.document.createElement("div");
R.renderEvents(c2, [{ type: "text", text: "just chat" }]);
assert.ok(!c2.querySelector(".process-fold"), "纯对话不该有过程折叠");
assert.ok(c2.querySelector(".chat-md"), "纯对话应直接渲染 markdown");

// 4) 代码块语法高亮:围栏代码 → <pre><code>,渲染层在**消毒后**真调 highlightElement;
//    并把代码块包进带复制按钮的 code-wrap;代码里的 <script> 仍被 DOMPurify 剥掉(XSS 不出)。
const c3 = dom.window.document.createElement("div");
R.appendMarkdown(c3, "```python\nprint('hi')\n<script>alert(1)</script>\n```");
assert.ok(c3.querySelector("pre code"), "围栏代码应渲染成 <pre><code>");
assert.ok(_hljsCalls.some((s) => s.startsWith("CODE")), "代码块应真调 hljs.highlightElement");
assert.ok(c3.querySelector("pre code.hljs"), "高亮后代码块应带 hljs class");
assert.ok(c3.querySelector(".code-wrap") && c3.querySelector(".copy-btn"), "代码块应包进带复制按钮的 code-wrap");
assert.ok(!c3.innerHTML.toLowerCase().includes("<script"), "代码块内 <script> 仍须被 DOMPurify 剥掉");

// 5) thinking(推理块)→ 默认折叠的 <details.thinking-card>(与正文视觉分离);
//    thinking 文本走同一消毒管线,<script> 不出。
const c4 = dom.window.document.createElement("div");
R.renderEvent(c4, { type: "thinking", text: "let me reason <script>alert(2)</script>" });
const det = c4.querySelector("details.thinking-card");
assert.ok(det, "thinking 事件应渲染成 details.thinking-card");
assert.ok(det.querySelector("summary"), "thinking 卡应有 summary(可点开)");
assert.ok(!det.hasAttribute("open"), "thinking 卡默认应折叠(无 open 属性)");
assert.ok(!c4.innerHTML.toLowerCase().includes("<script"), "thinking 文本内 <script> 仍须被剥掉");

console.log("✓ render smoke OK — markdown + DOMPurify 消毒 + 事件分派 + 代码高亮 + thinking 折叠 行为正确");
