// e2e_interop_check.mjs — 跨实现互操作检查:JS 端(static/e2e.js)对 Python 生成的
// 字节级向量必须:① hello/pair_mac/指纹逐字节复现 ② 打开 Python console 发的帧
// ③ seal 出与 Python client 完全相同的字节(协议全确定) ④ 重放/坏帧/错指纹全拒。
// 任何一字节漂移 = 互操作断 = 立即红。用法:node e2e_interop_check.mjs <vectors.json>
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const vecPath = process.argv[2] || join(here, "e2e_vectors.json");
const V = JSON.parse(readFileSync(vecPath, "utf-8"));

// 载入真构建产物(globalThis.KarvyE2E)
await import("file://" + join(here, "..", "..", "static", "e2e.js"));
const E = globalThis.KarvyE2E;
if (!E) { console.error("FAIL: KarvyE2E global missing"); process.exit(1); }

const b64d = (s) => new Uint8Array(Buffer.from(s, "base64"));
const b64e = (u) => Buffer.from(u).toString("base64");
let failures = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { console.log("  ok  ", name); }
  else { console.error("  FAIL", name, extra); failures++; }
};

const clientPriv = b64d(V.client_priv);

// ① 确定性复现:公钥 / pair_mac / hello / 指纹
ok("client_pub matches", b64e(E.pubFromPriv(clientPriv)) === V.client_pub);
ok("pair_mac matches", b64e(E.pairMac(V.pair_code, b64d(V.client_pub))) === V.pair_mac);
ok("hello frame byte-equal", b64e(E.buildHello(clientPriv, V.pair_code)) === V.hello);
ok("console fingerprint matches", E.fingerprintOf(b64d(V.console_pub)) === V.console_fingerprint);

// ② 握手:用 Python 的 WELCOME 完成 client 侧 → 双向会话
const sess = E.clientComplete(b64d(V.welcome), clientPriv, V.console_fingerprint);

// ③ seal 逐字节复现 Python client 的 c2s 帧;open 打开 Python console 的 s2c 帧
V.c2s_plain.forEach((p, i) => {
  ok(`c2s seal[${i}] byte-equal to Python`, b64e(sess.seal(b64d(p))) === V.c2s_frames[i]);
});
V.s2c_frames.forEach((f, i) => {
  ok(`s2c open[${i}] returns Python plaintext`, b64e(sess.open(b64d(f))) === V.s2c_plain[i]);
});

// ④ 安全性质:重放拒 / 篡改拒 / 错指纹拒 / 无码 HELLO 的 MAC 为零
try { sess.open(b64d(V.s2c_frames[0])); ok("replay rejected", false, "(replay accepted!)"); }
catch (e) { ok("replay rejected", e instanceof E.ReplayError); }
const tampered = b64d(V.s2c_frames[1]); tampered[tampered.length - 1] ^= 0x01;
// 注意:上面 open 已推进 seqIn 到 2,新造一个会话来验篡改(否则先撞重放闸)
const sess2 = E.clientComplete(b64d(V.welcome), clientPriv, V.console_fingerprint);
try { sess2.open(tampered); ok("tamper rejected", false, "(tampered frame accepted!)"); }
catch (e) { ok("tamper rejected", e instanceof E.FrameError); }
try {
  E.clientComplete(b64d(V.welcome), clientPriv, "dead-beef-dead-beef");
  ok("wrong fingerprint rejected", false, "(MITM fingerprint accepted!)");
} catch (e) { ok("wrong fingerprint rejected", e instanceof E.FingerprintMismatch); }
const helloNoCode = E.buildHello(clientPriv, null);
ok("no-code HELLO has zero MAC",
   b64e(helloNoCode.subarray(36)) === b64e(new Uint8Array(32)));

console.log(failures === 0 ? `\nINTEROP PASS (all checks)` : `\nINTEROP FAIL: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
