/* e2e.ts — relay E2E 协议的浏览器实现(与 karvyloop/relay/e2e.py **逐字节兼容**,v1 冻结)。
 *
 * 「信使不拆信」的信封,浏览器这半:X25519 握手 + ChaCha20-Poly1305,nonce 计数防重放。
 * 密码学原语走 noble 系列(MIT,纯 JS,经审计;WebCrypto 没有 ChaCha20-Poly1305,且 noble
 * 全同步——不给握手/收发路径引 async 泥潭)。esbuild 打进 static/e2e.js(global KarvyE2E)。
 *
 * 帧格式(与 Python 端同一份 v1 冻结,relay 盲转发):
 *   header = "KL" + ver(0x01) + type(1B)
 *   0x01 HELLO   : header + client_pub(32) + pair_mac(32)
 *   0x02 WELCOME : header + console_pub(32) + confirm(16)
 *   0x10 DATA    : header + seq(8B BE) + AEAD 密文(AAD=header+seq)
 *   0x7f ERR     : header + utf8 错误码
 * 派生:ss=X25519 → HKDF-SHA256(salt="KLRELAY-v1", info="KLRELAY|"+name+"|"+cpub+spub)
 * nonce = 4B 方向常量("C2S\0"/"S2C\0") + 8B seq;收侧只认严格递增 seq。
 *
 * 纪律(CLAUDE.md 安全地基):绝不 log 密钥/明文;异常消息不带密钥材料。
 * 互操作正确性由 tests/test_relay_e2e_interop.py 的跨实现字节级向量锁死(Python 生成,
 * 本实现必须逐字节复现)——改这里任何一个字节都会被逮。
 */
import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { x25519 } from "@noble/curves/ed25519.js";
import { hkdf } from "@noble/hashes/hkdf.js";
import { hmac } from "@noble/hashes/hmac.js";
import { sha256 } from "@noble/hashes/sha2.js";

// === 帧常量(v1 冻结)===
const MAGIC = new Uint8Array([0x4b, 0x4c]);          // "KL"
const VERSION = 1;
export const T_HELLO = 0x01;
export const T_WELCOME = 0x02;
export const T_DATA = 0x10;
export const T_ERR = 0x7f;
const HEADER_LEN = 4;
const SEQ_LEN = 8;
const CONFIRM_LEN = 16;
const SALT = utf8("KLRELAY-v1");
const NONCE_C2S = new Uint8Array([0x43, 0x32, 0x53, 0x00]);   // "C2S\0"
const NONCE_S2C = new Uint8Array([0x53, 0x32, 0x43, 0x00]);   // "S2C\0"(S=0x53 2=0x32 C=0x43;此处曾手打错 0x53 被互操作向量当场逮住)
export const MAX_FRAME_BYTES = 1024 * 1024;          // 与 karvyloop.relay.MAX_FRAME_BYTES 对齐

export class FrameError extends Error {}
export class HandshakeError extends Error {}
export class FingerprintMismatch extends HandshakeError {}
export class ReplayError extends Error {}

function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

function eqBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];   // 常数时间比较
  return d === 0;
}

function header(ftype: number): Uint8Array {
  return new Uint8Array([MAGIC[0], MAGIC[1], VERSION, ftype]);
}

export function frameType(frame: Uint8Array): number | null {
  if (frame.length < HEADER_LEN || frame[0] !== MAGIC[0] || frame[1] !== MAGIC[1]
      || frame[2] !== VERSION) return null;
  return frame[3];
}

export function parseErr(frame: Uint8Array): string {
  return new TextDecoder().decode(frame.subarray(HEADER_LEN));
}

// === 密钥/指纹 ===

export function genKeypair(): { priv: Uint8Array; pub: Uint8Array } {
  const priv = crypto.getRandomValues(new Uint8Array(32));   // x25519 clamp 由 noble 内部做
  return { priv, pub: x25519.getPublicKey(priv) };
}

export function pubFromPriv(priv: Uint8Array): Uint8Array {
  return x25519.getPublicKey(priv);
}

export function fingerprintOf(pub: Uint8Array): string {
  const hex = bytesToHex(sha256(pub)).slice(0, 16);
  return [hex.slice(0, 4), hex.slice(4, 8), hex.slice(8, 12), hex.slice(12, 16)].join("-");
}

export function pairMac(code: string, clientPub: Uint8Array): Uint8Array {
  return hmac(sha256, utf8(code.trim().toUpperCase()), concat(utf8("KL-PAIR|"), clientPub));
}

function deriveKeys(shared: Uint8Array, clientPub: Uint8Array, consolePub: Uint8Array) {
  const derive = (name: string) =>
    hkdf(sha256, shared, SALT,
         concat(utf8("KLRELAY|" + name + "|"), clientPub, consolePub), 32);
  return { c2s: derive("c2s"), s2c: derive("s2c"), confirm: derive("confirm") };
}

// === 会话(DATA 双向 AEAD)===

export class Session {
  private sendKey: Uint8Array;
  private recvKey: Uint8Array;
  private sendDir: Uint8Array;
  private recvDir: Uint8Array;
  private seqOut = 0;
  private seqIn = 0;

  constructor(sendKey: Uint8Array, recvKey: Uint8Array,
              sendDir: Uint8Array, recvDir: Uint8Array) {
    this.sendKey = sendKey; this.recvKey = recvKey;
    this.sendDir = sendDir; this.recvDir = recvDir;
  }

  seal(plaintext: Uint8Array): Uint8Array {
    this.seqOut += 1;
    const seq = seqBytes(this.seqOut);
    const hdr = concat(header(T_DATA), seq);
    const nonce = concat(this.sendDir, seq);
    const ct = chacha20poly1305(this.sendKey, nonce, hdr).encrypt(plaintext);
    const frame = concat(hdr, ct);
    if (frame.length > MAX_FRAME_BYTES) throw new FrameError("frame too large");
    return frame;
  }

  open(frame: Uint8Array): Uint8Array {
    if (frameType(frame) !== T_DATA || frame.length < HEADER_LEN + SEQ_LEN + 16) {
      throw new FrameError("not a DATA frame");
    }
    const hdr = frame.subarray(0, HEADER_LEN + SEQ_LEN);
    const seqB = frame.subarray(HEADER_LEN, HEADER_LEN + SEQ_LEN);
    const seq = seqFromBytes(seqB);
    if (seq <= this.seqIn) throw new ReplayError("replayed/old seq");
    const nonce = concat(this.recvDir, seqB);
    let pt: Uint8Array;
    try {
      pt = chacha20poly1305(this.recvKey, nonce, hdr).decrypt(frame.subarray(HEADER_LEN + SEQ_LEN));
    } catch (e) {
      throw new FrameError("AEAD verification failed");
    }
    this.seqIn = seq;               // 只有验过 AEAD 才推进窗口
    return pt;
  }
}

function seqBytes(n: number): Uint8Array {
  // seq 是会话内计数,Number 安全上限 2^53 绰绰有余;高 32 位用除法出(JS 位运算只有 32 位)。
  const out = new Uint8Array(8);
  const hi = Math.floor(n / 0x1_0000_0000);
  const lo = n >>> 0;
  out[0] = (hi >>> 24) & 0xff; out[1] = (hi >>> 16) & 0xff;
  out[2] = (hi >>> 8) & 0xff; out[3] = hi & 0xff;
  out[4] = (lo >>> 24) & 0xff; out[5] = (lo >>> 16) & 0xff;
  out[6] = (lo >>> 8) & 0xff; out[7] = lo & 0xff;
  return out;
}

function seqFromBytes(b: Uint8Array): number {
  let hi = 0, lo = 0;
  for (let i = 0; i < 4; i++) hi = hi * 256 + b[i];
  for (let i = 4; i < 8; i++) lo = lo * 256 + b[i];
  return hi * 0x1_0000_0000 + lo;
}

// === 握手(client 侧;console 侧在 Python)===

export function buildHello(clientPriv: Uint8Array, pairingCode: string | null): Uint8Array {
  const clientPub = pubFromPriv(clientPriv);
  const mac = pairingCode ? pairMac(pairingCode, clientPub) : new Uint8Array(32);
  return concat(header(T_HELLO), clientPub, mac);
}

export function clientComplete(welcomeFrame: Uint8Array, clientPriv: Uint8Array,
                               expectedFingerprint: string): Session {
  if (frameType(welcomeFrame) !== T_WELCOME
      || welcomeFrame.length !== HEADER_LEN + 32 + CONFIRM_LEN) {
    throw new HandshakeError("malformed WELCOME");
  }
  const consolePub = welcomeFrame.subarray(HEADER_LEN, HEADER_LEN + 32);
  const confirm = welcomeFrame.subarray(HEADER_LEN + 32);
  const exp = (expectedFingerprint || "").trim().toLowerCase().replace(/-/g, "");
  const got = fingerprintOf(consolePub).replace(/-/g, "");
  if (!exp || !eqBytes(utf8(exp), utf8(got))) {
    throw new FingerprintMismatch("console key fingerprint mismatch — refusing (possible MITM)");
  }
  const clientPub = pubFromPriv(clientPriv);
  const shared = x25519.getSharedSecret(clientPriv, consolePub);
  const keys = deriveKeys(shared, clientPub, consolePub);
  const want = hmac(sha256, keys.confirm,
                    concat(utf8("KL-CONFIRM|"), clientPub, consolePub)).subarray(0, CONFIRM_LEN);
  if (!eqBytes(want, confirm)) {
    throw new HandshakeError("WELCOME confirm MAC failed — peer does not hold the private key");
  }
  return new Session(keys.c2s, keys.s2c, NONCE_C2S, NONCE_S2C);
}

// === hex 工具(配对信息/密钥持久化用;密钥永不进 URL)===

export function bytesToHex(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, "0");
  return s;
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase();
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

const KarvyE2E = {
  T_HELLO, T_WELCOME, T_DATA, T_ERR, MAX_FRAME_BYTES,
  FrameError, HandshakeError, FingerprintMismatch, ReplayError,
  genKeypair, pubFromPriv, fingerprintOf, pairMac,
  frameType, parseErr, Session, buildHello, clientComplete,
  bytesToHex, hexToBytes,
};
(globalThis as unknown as { KarvyE2E: typeof KarvyE2E }).KarvyE2E = KarvyE2E;   // 浏览器+node 双跑(互操作测试在 node)
export { KarvyE2E };
