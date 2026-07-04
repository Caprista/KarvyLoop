/* pixelpet_smoke.mjs — 卡皮巴拉 sprite 引擎单元(docs/53 P1.5,v2 原图版)。
 * jsdom 里加载构建产物 static/desktop.js(pixelpet 打包在内,import 副作用挂
 * window.KarvyPixelPet)。断言:官方原图 sprite 挂载(占位 canvas 原位替换)/
 * accent 工牌换色 / 状态机只认真实状态(红线:引擎没有任何"闲逛/假装干活"的
 * 戏剧状态可进,且引擎自己零定时器,状态只能被 setState 翻)。
 * FRAMES/validateFrames 是兼容桩(手绘像素帧已废):恒空、恒合法。
 */
import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (!/Not implemented/.test(String(e && e.message))) console.error(e);
});
const dom = new JSDOM("<!doctype html><body></body>", { url: "http://localhost/", virtualConsole: vc });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.MutationObserver = dom.window.MutationObserver;

const here = dirname(fileURLToPath(import.meta.url));
const code = readFileSync(resolve(here, "../../static/desktop.js"), "utf8");
(0, eval)(code);

const P = dom.window.KarvyPixelPet;
assert.ok(P, "window.KarvyPixelPet 契约缺失");
for (const fn of ["createPet", "validateFrames", "buildPalette", "colorForRole"]) {
  assert.equal(typeof P[fn], "function", `KarvyPixelPet.${fn} 缺失`);
}

// ---- 1) 兼容桩:手绘帧已废 —— validateFrames 恒 [],FRAMES 恒空对象 ----
assert.deepEqual(P.validateFrames(), [], "validateFrames 兼容桩应恒返回 []");
assert.equal(typeof P.FRAMES, "object");
assert.deepEqual(Object.keys(P.FRAMES), [], "FRAMES 兼容桩应为空(帧数据已废,别再往里塞)");
assert.equal(typeof P.WIDTH, "number");
assert.equal(typeof P.HEIGHT, "number");
assert.ok(/karvy-capybara\.png$/.test(P.SPRITE_URL), "sprite 应指向官方原图 karvy-capybara.png");

// ---- 2) 状态机只认真实状态:idle/working/carry/sleep/happy,一个戏剧状态都没有 ----
// (红线锁:业界虚拟办公室产品的 idle 假戏被用户嘲讽过;这里连"闲逛/上厕所"的状态都不存在)
assert.deepEqual([...P.STATES].sort(), ["carry", "happy", "idle", "sleep", "working"].sort(),
  "STATES 必须只有 5 个真实驱动态");

// ---- 3) accent 换色(工牌):合法 accent 生效 + 暗部推导;非法输入回退品牌浅蓝 ----
const pal1 = P.buildPalette("#e07a5f");
assert.equal(pal1.A, "#e07a5f", "accent 未生效");
assert.notEqual(pal1.a, pal1.A, "accent 暗部应从主色推导(更深)");
assert.equal(P.buildPalette("garbage").A, P.KARVY_ACCENT, "非法 accent 应回退品牌浅蓝");

// ---- 4) 角色配色确定性:同 id 永远同色;小卡恒品牌浅蓝 ----
assert.equal(P.colorForRole("researcher"), P.colorForRole("researcher"), "同角色应恒同色");
assert.equal(P.colorForRole("karvy"), P.KARVY_ACCENT, "小卡应恒品牌浅蓝");
assert.equal(P.colorForRole(""), P.KARVY_ACCENT);

// ---- 5) 挂载:占位 canvas 原位替换成 sprite 根(id 保留),原图 + overlay 齐全 ----
const host = dom.window.document.createElement("div");
dom.window.document.body.appendChild(host);
const cv = dom.window.document.createElement("canvas");
cv.id = "pet-under-test";
host.appendChild(cv);
const pet = P.createPet({ canvas: cv, accent: "#e07a5f" });
const root = host.querySelector(".karvy-sprite");
assert.ok(root, "占位 canvas 应被 .karvy-sprite 根原位替换");
assert.ok(!host.querySelector("canvas"), "替换后不该残留占位 canvas");
assert.equal(root.id, "pet-under-test", "id 应随 sprite 根保留(#desk-karvy-pixel 等选择器契约)");
const img = root.querySelector("img.karvy-sprite-img");
assert.ok(img, "sprite 应内含官方原图 <img>");
assert.ok(img.src.indexOf("karvy-capybara.png") >= 0, "img 必须指向官方原图(不是手绘像素)");
assert.equal(img.getAttribute("aria-hidden"), "true", "装饰图应 aria-hidden(可达名称归宿主)");
for (const part of ["badge", "keys", "card", "zzz"]) {
  assert.ok(root.querySelector(".karvy-sprite-" + part), `overlay .karvy-sprite-${part} 缺失`);
}
assert.equal(root.style.getPropertyValue("--pet-accent"), "#e07a5f", "accent 应落到 --pet-accent(工牌换色)");

// ---- 6) createPet 状态机:data-state 只由 setState 翻;未知状态被拒;destroy 封机 ----
assert.equal(pet.state(), "idle", "初始应 idle");
assert.equal(root.getAttribute("data-state"), "idle", "初始 data-state 应 idle(CSS 动画钩子)");
assert.equal(pet.setState("working"), true);
assert.equal(pet.state(), "working");
assert.equal(root.getAttribute("data-state"), "working", "setState 应同步 data-state");
assert.equal(pet.setState("procrastinate"), false, "未知状态必须被拒");
assert.equal(pet.state(), "working", "被拒后状态不该变");
assert.equal(pet.setState("sleep"), true);
assert.equal(pet.state(), "sleep");
assert.equal(root.getAttribute("data-state"), "sleep");
// 引擎零定时器:等一拍,状态纹丝不动(呼吸等在场感全在 CSS,不在 JS)
await new Promise((r) => setTimeout(r, 60));
assert.equal(root.getAttribute("data-state"), "sleep", "引擎不许自己换状态(docs/53 红线)");
pet.destroy();
assert.equal(pet.setState("idle"), false, "destroy 后 setState 应拒绝");
assert.equal(root.getAttribute("data-state"), "sleep", "destroy 后 data-state 不许再动");

// ---- 7) 未挂载占位(无 parent)也不炸:状态机照常工作 ----
const loose = P.createPet({ canvas: dom.window.document.createElement("canvas") });
assert.equal(loose.setState("happy"), true, "未挂载 sprite 状态机也应工作");
loose.destroy();

console.log("✓ pixelpet smoke OK — 官方原图挂载(占位替换/id保留/overlay齐) / 5 真实状态(0 戏剧态·零定时器) / accent 工牌换色 / 确定性配色 / 兼容桩恒空");
