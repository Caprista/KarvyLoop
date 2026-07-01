/* dom_smoke.mjs — 真路径验证抽出的 dom 叶子工具(dev-report #4 slice 3)。
 * jsdom 里加载 static/dom.js,断言 el() 的 class/text/事件/子节点构造 + window.KarvyDom 契约。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const here = dirname(fileURLToPath(import.meta.url));
const code = readFileSync(resolve(here, "../../static/dom.js"), "utf8");
(0, eval)(code);

const D = dom.window.KarvyDom;
assert.ok(D && typeof D.el === "function", "window.KarvyDom.el 契约缺失");
assert.ok(typeof D.getJSON === "function" && typeof D.postJSON === "function", "getJSON/postJSON 缺失");

// el: class + text
const a = D.el("div", { class: "card", text: "hi" });
assert.equal(a.tagName, "DIV");
assert.equal(a.className, "card");
assert.equal(a.textContent, "hi");

// el: 任意属性 + 跳过 null
const b = D.el("a", { href: "/x", title: null });
assert.equal(b.getAttribute("href"), "/x");
assert.equal(b.hasAttribute("title"), false, "null 属性应跳过");

// el: 事件(onClick → click)
let clicked = 0;
const btn = D.el("button", { onClick: () => { clicked++; } });
btn.dispatchEvent(new dom.window.Event("click"));
assert.equal(clicked, 1, "onClick 没绑成 click");

// el: 子节点(字符串 → 文本节点;元素 → 直接挂;null → 跳过)
const wrap = D.el("div", null, "txt", D.el("span", { text: "child" }), null);
assert.equal(wrap.childNodes.length, 2, "字符串+元素 2 个子节点,null 跳过");
assert.equal(wrap.querySelector("span").textContent, "child");

console.log("✓ dom smoke OK — el(class/text/attr/event/children) + getJSON/postJSON 契约正确");
