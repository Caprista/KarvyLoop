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

console.log("✓ render smoke OK — markdown + DOMPurify 消毒 + 事件分派 行为正确");
