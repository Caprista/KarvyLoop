/* memory_panel_smoke.mjs — 验证抽出的知识库面板:契约 + open() + 喂料态/待办态切换 + 已知列表(jsdom)。 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <div id="mgmt-modal" class="hidden"><h2 id="mgmt-title"></h2><div id="mgmt-body"></div></div>
</body>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
dom.window.KarvyI18n = { t: (k) => k };

const here = dirname(fileURLToPath(import.meta.url));
const load = (f) => (0, eval)(readFileSync(resolve(here, "../../static/" + f), "utf8"));
load("dom.js");
load("modal.js");
load("ui_widgets.js");   // 已知列表用 pagedList(搜索+分页)
// 喂罐头(在 load 前覆盖 —— 模块加载时 const 捕获 _getJSON;用可变 flag 让同一函数切换返回)
let pendingMode = false, denseGraphMode = false;
dom.window.KarvyDom.getJSON = async (url) => {
  if (url === "/api/memory/distill") return pendingMode
    ? { pending: { summary: "一条料", transcript: [{ who: "you", text: "hi" }] } }
    : { pending: null };
  // 稠密图(9 边,1 语义):节点 0、4 都是度数 >K 的枢纽,弱边 0-4 在两端都非 top-3 → 应被剪掉;语义边 0-1 应留
  if (url === "/api/memory/graph" && denseGraphMode) return {
    nodes: [0, 1, 2, 3, 4, 5].map((i) => ({ id: i, title: "N" + i, content: "c" + i, kind: "knowledge" })),
    edges: [
      { source: 0, target: 1, via: ["a", "b", "c"], semantic: true }, { source: 0, target: 2, via: ["a", "b"] },
      { source: 0, target: 3, via: ["a", "b"] }, { source: 0, target: 4, via: ["a"] },
      { source: 2, target: 3, via: ["a", "b"] }, { source: 2, target: 4, via: ["a", "b"] },
      { source: 3, target: 4, via: ["a", "b"] }, { source: 4, target: 5, via: ["a", "b"] },
      { source: 2, target: 5, via: ["a", "b"] }],
  };
  if (url === "/api/memory/graph") return pendingMode ? { nodes: [], edges: [] } : {
    nodes: [
      { id: 0, title: "loop 工程", content: "loop engineering 让 agent 自运转发现工作", kind: "knowledge", degree: 2 },
      { id: 1, title: "复利引擎", content: "结晶是 loop 的复利引擎", kind: "knowledge", degree: 1 },
      { id: 2, title: "H2A 守人", content: "人是决策者+承担者", kind: "knowledge", degree: 1 }],
    edges: [{ source: 0, target: 1, via: ["loop"], semantic: true }, { source: 0, target: 2, via: ["agent"] }],
  };
  if (url === "/api/memory") return { beliefs: pendingMode ? [] : [
    { title: "偏好直接", content: "我偏好直接、不啰嗦的沟通", kind: "preference", source: "ingest", source_ref: "" },
    { title: "loop A", content: "loop 是自运转的", kind: "knowledge", source: "fed",
      source_ref: "https://addyosmani.com/blog/loop-engineering/" },
    { title: "loop B", content: "loop 无人参与", kind: "knowledge", source: "fed", source_ref: "text:abc123" }] };
  return null;
};
dom.window.KarvyDom.postJSON = async (url, payload) => {
  if (url === "/api/memory/consolidate/suggest") return { ok: true, status: 200, data: { ok: true, clusters: [
    { member_contents: ["loop 是自运转的", "loop 无人参与"], member_titles: ["loop A", "loop B"],
      merged_title: "loop 工程", merged_content: "loop 自运转、无人参与", reason: "同一件事" }] } };
  if (url === "/api/memory/consolidate/apply") return { ok: true, status: 200, data: { ok: true, removed: 2 } };
  return { ok: true, status: 200, data: { ok: true } };
};
load("memory_panel.js");

const M = dom.window.KarvyMemoryPanel;
assert.ok(M && typeof M.open === "function", "window.KarvyMemoryPanel.open 契约缺失");


// 双标签工具:各阶段按需切页(_memTab 模块态跨 open 持久,别假设停在哪页)
async function switchTab(key) {
  const tab = [...dom.window.document.querySelectorAll(".mem-tab")].find((b) => b.textContent === key);
  assert.ok(tab, "找不到标签 " + key);
  tab.click();
  await new Promise((r) => setTimeout(r, 30));
}

await M.open();
const modal = dom.window.document.getElementById("mgmt-modal");
const title = dom.window.document.getElementById("mgmt-title");
assert.equal(modal.classList.contains("hidden"), false, "open 应打开模态");
assert.equal(title.textContent, "mgmt.memory_title", "标题应是 mgmt.memory_title");
const body = dom.window.document.getElementById("mgmt-body");
// 双标签(Hardy):默认「聊知识·沉淀」页 = 馆员聊天框 + 喂料;「知识库」页 = 图谱 + 已知列表
assert.ok(body.querySelector(".mem-tabs"), "面板应有双标签栏");
assert.ok(body.querySelector(".kchat-area"), "沉淀页应有馆员聊天区");
assert.ok(body.querySelector(".kchat-bar .kchat-in"), "聊天框应是横排 bar 里的 textarea");
assert.ok(body.querySelector(".distill-area textarea"), "无待办应渲染喂料 textarea");
// 切到「知识库」标签页再验列表
await switchTab("mem.tab_library");
const libBody = dom.window.document.getElementById("mgmt-body");
// #5 已知列表:可搜索(pagedList)+ 标题作主行 + 每条可删
assert.ok(libBody.querySelector(".paged-search"), "已知列表应有搜索框(pagedList)");
assert.ok([...libBody.querySelectorAll(".mc-name")].some((n) => n.textContent === "偏好直接"), "已知列表主行应是标题");
assert.ok([...libBody.querySelectorAll(".mc-del")].length >= 1, "每条知识应有删除按钮");
// 来源显示真实出处(Hardy:别给用户看 fed/ingest 代号):有 URL 的 → 可点链接指向真实来源
const srcLink = libBody.querySelector(".mc-src-link");
assert.ok(srcLink && srcLink.getAttribute("href") === "https://addyosmani.com/blog/loop-engineering/",
  "带 source_ref 的知识应显示真实来源链接(不是 fed/ingest)");
// 搜 "loop" → 只剩 loop 相关的两条
const ksearch = body.querySelector(".paged-search");
ksearch.value = "loop"; ksearch.dispatchEvent(new dom.window.Event("input"));
assert.ok(![...body.querySelectorAll(".mc-name")].some((n) => n.textContent === "偏好直接"), "搜 loop 应过滤掉'偏好直接'");
// 认知图:真 mesh(SVG + 边 + 节点),标签用**标题**不是正文切片;有「看大图」按钮
const svg = body.querySelector("svg.mem-graph");
assert.ok(svg, "应渲染认知图 SVG");
assert.ok(svg.querySelectorAll("line.mem-edge").length >= 2, "应有边(mesh 关联,不是孤立圆点)");
assert.ok([...svg.querySelectorAll("text.mem-label")].some((tx) => tx.textContent === "loop 工程"), "节点标签应是标题『loop 工程』");
// 地图化质量门:① viewBox 是**固定** 0 0 1000 640(缩放走 viewBox,字/点屏幕恒定)② 节点绝不重叠
const vb = svg.getAttribute("viewBox").split(/\s+/).map(Number);
assert.deepEqual(vb, [0, 0, 1000, 640], `viewBox 应是固定 0 0 1000 640,实际 ${svg.getAttribute("viewBox")}`);
const circs = [...svg.querySelectorAll("circle.mem-node")].map((c) => ({
  x: +c.getAttribute("cx"), y: +c.getAttribute("cy"), r: +c.getAttribute("r") }));
for (let i = 0; i < circs.length; i++) for (let j = i + 1; j < circs.length; j++) {
  const d = Math.hypot(circs[i].x - circs[j].x, circs[i].y - circs[j].y);
  assert.ok(d >= circs[i].r + circs[j].r - 0.5, "节点不应重叠(碰撞去重叠失效)");
}
// 地图式 LOD:字号由脚本按缩放折算(设了 font-size 属性,不靠 CSS 固定值);标签有 .lod 揭示类
assert.ok([...svg.querySelectorAll("text.mem-label")].every((tx) => +tx.getAttribute("font-size") > 0),
  "标签字号应由脚本按缩放折算(font-size 属性 > 0)");
assert.ok([...svg.querySelectorAll("text.mem-label.lod")].length >= 1, "至少最高度数的标签应在当前缩放层级露出(.lod)");
// 亮度随连接数(星辰感):度数高的节点 fill-opacity 更大
const op0 = +nodeEls_op(svg, 0), op2 = +nodeEls_op(svg, 2);
function nodeEls_op(s, i) { return [...s.querySelectorAll("circle.mem-node")][i].getAttribute("fill-opacity"); }
assert.ok(op0 > op2, `度数高的点应更亮(node0 deg2 op=${op0} > node2 deg1 op=${op2})`);
// 悬停聚焦(Obsidian 招牌):事件挂在**大命中圈**(.mem-hit)上,可见点(.mem-node)只显示。命中圈数量对齐节点
const nodeEls = [...svg.querySelectorAll("circle.mem-node")];
const hitEls = [...svg.querySelectorAll("circle.mem-hit")];
assert.equal(hitEls.length, nodeEls.length, "每个节点应有一个命中圈");
assert.ok(+hitEls[1].getAttribute("r") > +nodeEls[1].getAttribute("r"), "命中圈半径应远大于可见点(小点也好点中)");
// 悬停命中圈 1(节点只连 0)→ 1=focus、0=adj、2=dim + 即时气泡
hitEls[1].dispatchEvent(new dom.window.MouseEvent("mouseenter", { clientX: 40, clientY: 40 }));
assert.ok(nodeEls[1].classList.contains("focus"), "悬停节点应 .focus");
assert.ok(nodeEls[0].classList.contains("adj"), "悬停节点的邻居应 .adj(高亮)");
assert.ok(nodeEls[2].classList.contains("dim"), "无关节点应 .dim(变暗)");
const tip = dom.window.document.querySelector(".mem-tip");
assert.ok(tip && tip.style.display === "block" && tip.querySelector(".mem-tip-title").textContent === "复利引擎",
  "悬停应立刻出气泡(标题=该点标题)");
hitEls[1].dispatchEvent(new dom.window.Event("mouseleave"));
assert.ok(!nodeEls[1].classList.contains("focus") && !nodeEls[2].classList.contains("dim"), "离开应复位聚焦");
assert.equal(dom.window.document.querySelector(".mem-tip").style.display, "none", "离开应隐藏气泡");
// 单击固定选中:点命中圈 0 → .selected + 邻居高亮;离开不复位(固定);再点它 → 取消
hitEls[0].dispatchEvent(new dom.window.MouseEvent("click", { clientX: 40, clientY: 40 }));
assert.ok(nodeEls[0].classList.contains("selected"), "单击应固定选中(.selected)");
assert.ok(nodeEls[1].classList.contains("adj") && nodeEls[2].classList.contains("adj"), "选中点的邻居应高亮");
hitEls[0].dispatchEvent(new dom.window.Event("mouseleave"));   // 固定后离开不该复位
assert.ok(nodeEls[0].classList.contains("selected"), "选中后鼠标离开仍保持(sticky)");
hitEls[0].dispatchEvent(new dom.window.MouseEvent("click", { clientX: 40, clientY: 40 }));
assert.ok(!nodeEls[0].classList.contains("selected") && !nodeEls[2].classList.contains("dim"), "再次点选中的点应取消焦点");
// 悬停蒙版 + 中间放大 + 按钮(取代文字链):点它 → 进大图
const plusBtn = body.querySelector(".mem-graph-wrap .mem-graph-plus");
assert.ok(plusBtn, "内嵌图应有悬停放大 + 按钮");
// 点看大图 → 全屏 overlay + 搜索高亮:搜 "H2A" → 命中节点 2 高亮、其余 dim
plusBtn.click();
const overlay = dom.window.document.querySelector(".mem-graph-overlay");
assert.ok(overlay, "看大图应打开全屏 overlay");
const gsearch = overlay.querySelector(".mem-graph-search");
gsearch.value = "H2A"; gsearch.dispatchEvent(new dom.window.Event("input"));
const bigNodes = [...overlay.querySelectorAll("circle.mem-node")];
assert.ok(bigNodes.some((c) => c.classList.contains("dim")) && bigNodes.some((c) => !c.classList.contains("dim")),
  "搜索应高亮命中节点、其余 dim");
// 选中节点 → 详情卡浮出(标题+完整内容+关联节点);点关联节点 → 切换焦点+更新卡
const oHits = [...overlay.querySelectorAll("circle.mem-hit")];
oHits[0].dispatchEvent(new dom.window.MouseEvent("click", { clientX: 50, clientY: 50 }));
const dcard = overlay.querySelector(".mem-detail");
assert.ok(dcard && !dcard.classList.contains("hidden"), "选中节点应浮出详情卡");
assert.equal(dcard.querySelector(".mem-detail-title").textContent, "loop 工程", "详情卡标题=选中点标题");
assert.ok(dcard.querySelector(".mem-detail-body").textContent.includes("loop engineering"), "详情卡应显示完整内容");
const relBtns = [...dcard.querySelectorAll(".mem-rel")];
assert.ok(relBtns.length === 2, `应列出 2 个关联知识点(实际 ${relBtns.length})`);
relBtns.find((b) => b.textContent === "复利引擎").dispatchEvent(new dom.window.MouseEvent("click", { clientX: 50, clientY: 50 }));
assert.equal(overlay.querySelector(".mem-detail-title").textContent, "复利引擎", "点关联节点应切换焦点+更新详情卡");
overlay.querySelector(".mem-detail-close").click();
assert.ok(overlay.querySelector(".mem-detail").classList.contains("hidden"), "✕ 应收起详情卡");
overlay.querySelector(".mem-graph-close").click();
assert.ok(!dom.window.document.querySelector(".mem-graph-overlay"), "✕ 应关闭大图");

// 展示层稀疏化(Hardy):稠密图(9 边,1 语义)→ 只画语义边 + 每点 top-3 → 画出的边 < 输入,且语义边保留、不孤立
denseGraphMode = true;
await M.open();
const dsvg = dom.window.document.getElementById("mgmt-body").querySelector("svg.mem-graph");
const drawn = dsvg.querySelectorAll("line.mem-edge").length;
assert.ok(drawn < 9, `稠密图应剪掉弱词面边(输入 9,画出 ${drawn})`);
assert.ok(dsvg.querySelectorAll("line.mem-edge.semantic").length >= 1, "语义边应无条件保留(画出)");
denseGraphMode = false;

// 待办态:有 pending → 渲染 persist/reject 拍板按钮(人在环)
pendingMode = true;
await M.open();
await switchTab("mem.tab_sediment");
const body2 = dom.window.document.getElementById("mgmt-body");
assert.ok(body2.querySelector(".distill-decide .distill-yes"), "有待办应渲染'沉淀'拍板按钮");
assert.ok(body2.querySelector(".distill-chat-in"), "有待办应能跟小卡继续交流");

// Bug2:整理相似知识 —— 点按钮 → 出合并建议(把 2 条并成 1)→ 点合并 → apply
pendingMode = false;   // 回到有 beliefs 的态(前面待办测试把它置 true 了)
await M.open();
await switchTab("mem.tab_library");
const kbody = dom.window.document.getElementById("mgmt-body");
const consolBtn = [...kbody.querySelectorAll("button")].find((b) => b.textContent === "mem.consolidate_btn");
assert.ok(consolBtn, "已知≥2 应有『整理相似知识』按钮");
consolBtn.click();
await new Promise((r) => setTimeout(r, 5));
const cbody = dom.window.document.getElementById("mgmt-body");
const card = cbody.querySelector(".consolidate-card");
assert.ok(card, "应渲染合并建议卡");
assert.ok(card.textContent.includes("loop 自运转、无人参与"), "应显示合并去向");
assert.ok(card.querySelectorAll(".consolidate-member").length === 2, "应列出被并的 2 条成员");
const mergeBtn = [...card.querySelectorAll("button")].find((b) => b.textContent === "mem.consolidate_do");
mergeBtn.click();
await new Promise((r) => setTimeout(r, 5));
assert.ok(!cbody.querySelector(".consolidate-card"), "点合并后该建议卡应被『已合并』替换");

console.log("✓ memory panel smoke OK — 标题 mesh 图 + 看大图搜索 + 🧹整理相似知识(建议→合并)");
