/* i18n_smoke.mjs — 真路径验证迁移后的 i18n bundle(dev-report #4 slice 2)。
 * jsdom 里加载构建产物 static/i18n.js,断言 window.KarvyI18n 契约 + t() 取值 + 插值 + 运行时 parity。
 * 编译期 parity 已由 i18n.ts 的 _i18nParity 类型断言保证;这里补运行时行为。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
// 给真 origin 才有 localStorage(opaque origin 会抛);bare localStorage 解析到这里
Object.defineProperty(globalThis, "localStorage", { value: dom.window.localStorage, configurable: true });

const here = dirname(fileURLToPath(import.meta.url));
const code = readFileSync(resolve(here, "../../static/i18n.js"), "utf8");
(0, eval)(code); // 运行 → 设 window.KarvyI18n

const I = dom.window.KarvyI18n;
assert.ok(I && typeof I.t === "function", "window.KarvyI18n 契约缺失");
for (const fn of ["t", "getLang", "setLang", "applyStatic", "mountSwitcher"]) {
  assert.ok(typeof I[fn] === "function", `KarvyI18n.${fn} 缺失`);
}

// 1) 默认 en + 取值
assert.equal(I.getLang(), "en", "默认语言应为 en");
assert.equal(I.t("ui.title"), "KarvyLoop", "t('ui.title') 取值错");

// 2) 缺失 key → 回退 key 本身(永不空)
assert.equal(I.t("___no_such_key___"), "___no_such_key___", "缺失 key 应回退 key 本身");

// 3) {var} 插值
const strings = I._strings;
const sample = Object.keys(strings.en).find((k) => /\{\w+\}/.test(strings.en[k]));
if (sample) {
  const varName = strings.en[sample].match(/\{(\w+)\}/)[1];
  const out = I.t(sample, { [varName]: "XYZ" });
  assert.ok(out.includes("XYZ"), `t() 插值失败:${sample}`);
}

// 4) 运行时 parity(编译期已由类型断言保证,这里双保险)
const en = new Set(Object.keys(strings.en));
const zh = new Set(Object.keys(strings.zh));
const missZh = [...en].filter((k) => !zh.has(k));
const missEn = [...zh].filter((k) => !en.has(k));
assert.equal(missZh.length, 0, `zh 缺翻译: ${missZh.slice(0, 5)}`);
assert.equal(missEn.length, 0, `en 缺 key(zh 孤儿): ${missEn.slice(0, 5)}`);

console.log(`✓ i18n smoke OK — 契约 + 取值 + 插值 + 运行时 parity(${en.size} keys × en/zh)`);
