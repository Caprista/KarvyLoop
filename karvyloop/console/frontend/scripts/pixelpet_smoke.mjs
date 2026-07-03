/* pixelpet_smoke.mjs — 像素 sprite 引擎单元(docs/53 P1.5)。
 * jsdom 里加载构建产物 static/desktop.js(pixelpet 打包在内,import 副作用挂
 * window.KarvyPixelPet)。断言:帧数据合法 / palette 换色 / 状态机只认真实状态
 * (红线:引擎没有任何"闲逛/假装干活"的戏剧状态可进)。
 * jsdom canvas 无 2d context → createPet 走"不画只跑状态机"分支,正好测纯逻辑。
 */
import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

// jsdom 无 canvas 2d 是已知环境事实(引擎走"不画只跑状态机"分支),吞掉这条噪音
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

// ---- 1) 帧数据合法:全部帧 32×24、字符都在 palette 里 ----
assert.deepEqual(P.validateFrames(), [], "帧数据不合法(尺寸/字符越界)");
assert.equal(P.WIDTH, 32);
assert.equal(P.HEIGHT, 24);

// ---- 2) 状态机只认真实状态:idle/working/carry/sleep/happy,一个戏剧状态都没有 ----
// (红线锁:业界虚拟办公室产品的 idle 假戏被用户嘲讽过;这里连"闲逛/上厕所"的帧都不存在)
assert.deepEqual([...P.STATES].sort(), ["carry", "happy", "idle", "sleep", "working"].sort(),
  "STATES 必须只有 5 个真实驱动态");
assert.ok(P.FRAMES.idle.length >= 2, "idle 应有呼吸 2 帧");
assert.ok(P.FRAMES.working.length >= 2 && P.FRAMES.working.length <= 3, "working 应为打字 2-3 帧");
assert.ok(P.FRAMES.carry.length >= 2, "carry 应有叼卡走 2 帧");
assert.ok(P.FRAMES.sleep.length >= 1, "sleep 帧缺失");
assert.ok(P.FRAMES.blink && P.FRAMES.blink.length >= 1, "blink 插帧缺失(眨眼是'在场'允许项)");
// carry 帧必须真的叼着白卡(W 像素),sleep 帧必须有 Zzz(Z 像素)
assert.ok(P.FRAMES.carry[0].some((row) => row.indexOf("W") >= 0), "carry 帧没有白卡(W)");
assert.ok(P.FRAMES.sleep[0].some((row) => row.indexOf("Z") >= 0), "sleep 帧没有 Zzz(Z)");
// 围巾(accent A)在基础帧上,palette 换色才有意义
assert.ok(P.FRAMES.idle[0].some((row) => row.indexOf("A") >= 0), "idle 帧没有围巾(A)");

// ---- 3) palette 换色:同一形状,accent 换主色;非法输入回退品牌浅蓝 ----
const pal1 = P.buildPalette("#e07a5f");
assert.equal(pal1.A, "#e07a5f", "accent 未生效");
assert.notEqual(pal1.a, pal1.A, "accent 暗部应从主色推导(更深)");
const pal2 = P.buildPalette("#6a994e");
assert.equal(pal1.B, pal2.B, "身体主色不随 accent 变(同形状换围巾色)");
assert.equal(P.buildPalette("garbage").A, P.KARVY_ACCENT, "非法 accent 应回退品牌浅蓝");

// ---- 4) 角色配色确定性:同 id 永远同色;小卡恒品牌浅蓝 ----
assert.equal(P.colorForRole("researcher"), P.colorForRole("researcher"), "同角色应恒同色");
assert.equal(P.colorForRole("karvy"), P.KARVY_ACCENT, "小卡应恒品牌浅蓝");
assert.equal(P.colorForRole(""), P.KARVY_ACCENT);

// ---- 5) createPet 状态机:未知状态被拒(不瞎演),destroy 停表 ----
const cv = dom.window.document.createElement("canvas");
const pet = P.createPet({ canvas: cv, accent: "#e07a5f" });
assert.equal(pet.state(), "idle", "初始应 idle");
assert.equal(pet.setState("working"), true);
assert.equal(pet.state(), "working");
assert.equal(pet.setState("procrastinate"), false, "未知状态必须被拒");
assert.equal(pet.state(), "working", "被拒后状态不该变");
assert.equal(pet.setState("sleep"), true);
assert.equal(pet.state(), "sleep");
pet.destroy();
assert.equal(pet.setState("idle"), false, "destroy 后 setState 应拒绝");
assert.ok(cv.classList.contains("pixelpet-canvas"), "canvas 应挂 pixelpet-canvas 类(CSS 像素锐利)");
assert.equal(cv.width, 32);
assert.equal(cv.height, 24);

console.log("✓ pixelpet smoke OK — 帧合法 / 5 真实状态(0 戏剧态) / palette 换色 / 确定性配色 / 状态机拒瞎演");
