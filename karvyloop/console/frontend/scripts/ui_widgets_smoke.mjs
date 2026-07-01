/* ui_widgets_smoke.mjs — 验证共享部件:transferList(点击左右移动)+ pagedList(搜索+分页)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("ui_widgets.js");

const W = dom.window.KarvyWidgets;
assert.ok(W && typeof W.transferList === "function" && typeof W.pagedList === "function", "KarvyWidgets 契约缺失");

// transferList:初选 [b];点左边的 a → 移到右已选;点右边的 b → 移回左
const tl = W.transferList({ items: [{ id: "a", label: "Atom A" }, { id: "b", label: "Atom B" }, { id: "c", label: "Atom C" }], selected: ["b"] });
assert.deepEqual(tl.getSelected(), ["b"], "初始已选应是 [b]");
const leftItems = () => [...tl.el.querySelectorAll(".xfer-pane:first-child .xfer-item")];
const rightItems = () => [...tl.el.querySelectorAll(".xfer-pane:last-child .xfer-item")];
// 左边(可选)应有 a、c;点 a 移过去
const aRow = leftItems().find((r) => r.textContent.includes("Atom A"));
assert.ok(aRow, "左侧应有可选 Atom A");
aRow.click();
assert.ok(tl.getSelected().includes("a") && tl.getSelected().includes("b"), "点 a 后已选应含 a、b");
// 点右边的 b 移回左
const bRow = rightItems().find((r) => r.textContent.includes("Atom B"));
assert.ok(bRow, "右侧已选应有 Atom B");
bRow.click();
assert.ok(!tl.getSelected().includes("b"), "点右侧 b 后应移回左(已选去掉 b)");

// pagedList:10 项、pageSize 4 → 3 页;搜索 "role7" → 只剩 1 项
const items = Array.from({ length: 10 }, (_, i) => ({ id: "role" + i }));
const pl = W.pagedList({ items, pageSize: 4, searchOf: (it) => it.id, renderItem: (it) => { const d = dom.window.document.createElement("div"); d.className = "pi"; d.textContent = it.id; return d; } });
assert.equal(pl.querySelectorAll(".pi").length, 4, "首页应显示 4 项");
assert.ok(pl.querySelector(".paged-info").textContent.includes("1 / 3"), "应是第 1/3 页");
pl.querySelectorAll(".paged-btn")[1].click();   // 下一页
assert.ok(pl.querySelector(".paged-info").textContent.includes("2 / 3"), "点下一页应到 2/3");
const search = pl.querySelector(".paged-search");
search.value = "role7"; search.dispatchEvent(new dom.window.Event("input"));
assert.equal(pl.querySelectorAll(".pi").length, 1, "搜索 role7 应只剩 1 项");

console.log("✓ ui widgets smoke OK — transferList 左右移动 + pagedList 分页/搜索");
