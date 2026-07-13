/* tunnel.ts — 浏览器版接入端(镜像 relay/remote.py 的 RemoteSession):
 * 连 relay /join?rid= → E2E 握手(KarvyE2E)→ id 多路复用的 tunnelFetch。
 *
 * 「随时随地」的浏览器那半:手机在任意网络,经公网 relay(盲转发,只见密文)把
 * /m 的每个 fetch 加密转给家里 console。协议帧内明文 = remote.py 同款 JSON:
 *   请求 {id, method, path, headers?, body_b64?} → 响应 {id, status, headers, body_b64, error}
 *
 * 身份持久化(localStorage,密钥**永不进 URL**——URL 会进浏览器历史/截图):
 *   karvy_remote_identity = {priv_hex, relay, room, fingerprint}
 * 首次配对用一次性码(来自 console 配对面板),之后免码重连(console 侧 paired 记住公钥;
 * 吊销 = console 面板一键,下次重连即拒)。
 * 依赖加载序:e2e.js 必须先于本文件(m.html 脚本序有测试锁)。
 */
interface E2EApi {
  T_DATA: number; T_ERR: number;
  ReplayError: new (...a: unknown[]) => Error;
  FrameError: new (...a: unknown[]) => Error;
  genKeypair: () => { priv: Uint8Array; pub: Uint8Array };
  pubFromPriv: (p: Uint8Array) => Uint8Array;
  fingerprintOf: (p: Uint8Array) => string;
  frameType: (f: Uint8Array) => number | null;
  parseErr: (f: Uint8Array) => string;
  buildHello: (priv: Uint8Array, code: string | null) => Uint8Array;
  clientComplete: (welcome: Uint8Array, priv: Uint8Array, fp: string) => {
    seal: (p: Uint8Array) => Uint8Array; open: (f: Uint8Array) => Uint8Array };
  bytesToHex: (b: Uint8Array) => string;
  hexToBytes: (h: string) => Uint8Array;
}
const E = (): E2EApi => (globalThis as unknown as { KarvyE2E: E2EApi }).KarvyE2E;

const IDENTITY_KEY = "karvy_remote_identity";

export interface RemoteIdentity { priv_hex: string; relay: string; room: string; fingerprint: string }

export function loadIdentity(): RemoteIdentity | null {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    return (d && d.priv_hex && d.relay && d.room && d.fingerprint) ? d : null;
  } catch (e) { return null; }
}

export function saveIdentity(id: RemoteIdentity): void {
  localStorage.setItem(IDENTITY_KEY, JSON.stringify(id));
}

export function clearIdentity(): void {
  try { localStorage.removeItem(IDENTITY_KEY); } catch (e) { /* noop */ }
}

function b64encode(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
}

function b64decode(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

type Pending = { resolve: (r: TunnelResponse) => void; reject: (e: Error) => void; timer: number };
export interface TunnelResponse { status: number; headers: Record<string, string>; body: Uint8Array; error: string }

export class Tunnel {
  private ws: WebSocket | null = null;
  private sess: ReturnType<E2EApi["clientComplete"]> | null = null;
  private nextId = 0;
  private pending = new Map<number, Pending>();
  private identity: RemoteIdentity;
  onstate: ((s: string) => void) | null = null;   // "connecting"|"open"|"closed"|"error:<code>"

  constructor(identity: RemoteIdentity) { this.identity = identity; }

  get connected(): boolean { return this.sess !== null && this.ws !== null && this.ws.readyState === 1; }

  /** 连 relay + 握手。code 只在首次配对给;之后免码(console 记住公钥)。 */
  connect(code?: string | null): Promise<void> {
    const url = this.identity.relay.replace(/\/+$/, "") + "/join?rid=" + encodeURIComponent(this.identity.room);
    this.onstate && this.onstate("connecting");
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      let settled = false;
      const fail = (e: Error) => { if (!settled) { settled = true; reject(e); } try { ws.close(); } catch (x) { /* */ } };
      ws.onerror = () => fail(new Error("relay unreachable"));
      ws.onopen = () => {
        try {
          ws.send(E().buildHello(E().hexToBytes(this.identity.priv_hex), code || null));
        } catch (e) { fail(e as Error); }
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (!(ev.data instanceof ArrayBuffer)) return;      // text 帧=relay 控制面,握手期忽略 ping
        const frame = new Uint8Array(ev.data);
        const ft = E().frameType(frame);
        if (this.sess === null) {                            // 握手阶段
          if (ft === E().T_ERR) { this.onstate && this.onstate("error:" + E().parseErr(frame)); return fail(new Error(E().parseErr(frame))); }
          try {
            this.sess = E().clientComplete(frame, E().hexToBytes(this.identity.priv_hex), this.identity.fingerprint);
          } catch (e) { return fail(e as Error); }
          this.ws = ws;
          ws.onclose = () => this._closed();
          this.onstate && this.onstate("open");
          settled = true;
          resolve();
          return;
        }
        this._dispatch(frame);                               // 会话阶段:按 id 派发
      };
      ws.onclose = () => fail(new Error("relay closed during handshake"));
    });
  }

  private _dispatch(frame: Uint8Array): void {
    if (E().frameType(frame) !== E().T_DATA || this.sess === null) return;
    let resp: { id?: number; status?: number; headers?: Record<string, string>; body_b64?: string; error?: string };
    try {
      resp = JSON.parse(new TextDecoder().decode(this.sess.open(frame)));
    } catch (e) { return; }                                  // 重放/坏帧:丢弃,绝不二次派发
    const p = this.pending.get(resp.id as number);
    if (!p) return;
    this.pending.delete(resp.id as number);
    clearTimeout(p.timer);
    p.resolve({
      status: Number(resp.status || 0),
      headers: resp.headers || {},
      error: String(resp.error || ""),
      body: resp.body_b64 ? b64decode(resp.body_b64) : new Uint8Array(0),
    });
  }

  private _closed(): void {
    this.sess = null;
    const err = new Error("relay connection lost");
    this.pending.forEach((p) => { clearTimeout(p.timer); p.reject(err); });
    this.pending.clear();
    this.onstate && this.onstate("closed");
  }

  /** 镜像 remote.py request():path 必须以 / 开头;返回 {status, headers, body, error}。 */
  request(method: string, path: string, opts?: { headers?: Record<string, string>; body?: Uint8Array; timeoutMs?: number }): Promise<TunnelResponse> {
    if (!path.startsWith("/") || path.includes("://")) {
      return Promise.reject(new Error("path must start with / and carry no scheme"));
    }
    if (!this.connected || this.sess === null || this.ws === null) {
      return Promise.reject(new Error("tunnel not connected"));
    }
    this.nextId += 1;
    const id = this.nextId;
    const req: Record<string, unknown> = { id, method: method.toUpperCase(), path };
    if (opts && opts.headers && Object.keys(opts.headers).length) req.headers = opts.headers;
    if (opts && opts.body && opts.body.length) req.body_b64 = b64encode(opts.body);
    return new Promise<TunnelResponse>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("tunnel request timeout"));
      }, (opts && opts.timeoutMs) || 30000);
      this.pending.set(id, { resolve, reject, timer });
      try {
        // JS 单线程:seal(seq++)与 send 天然原子,无需锁(Python 侧要 send_lock 是因为多 task)
        this.ws!.send(this.sess!.seal(new TextEncoder().encode(JSON.stringify(req))));
      } catch (e) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(e as Error);
      }
    });
  }

  /** fetch 形状适配:给 /m 的 KarvyFetch 用(Response-like:ok/status/json()/text())。 */
  async tunnelFetch(path: string, init?: { method?: string; headers?: Record<string, string>; body?: string }): Promise<{ ok: boolean; status: number; json: () => Promise<unknown>; text: () => Promise<string> }> {
    const r = await this.request(init && init.method ? init.method : "GET", path, {
      headers: init && init.headers,
      body: init && init.body ? new TextEncoder().encode(init.body) : undefined,
    });
    const text = new TextDecoder().decode(r.body);
    return {
      ok: r.status >= 200 && r.status < 300 && !r.error,
      status: r.status,
      json: () => Promise.resolve(JSON.parse(text)),
      text: () => Promise.resolve(text),
    };
  }

  close(): void {
    try { this.ws && this.ws.close(); } catch (e) { /* */ }
    this._closed();
  }
}

/** 首次配对:生成密钥对 → 用一次性码握手(consumes code,console 记住公钥)→ 存身份。 */
export async function pairAndSave(relay: string, room: string, fingerprint: string, code: string): Promise<Tunnel> {
  const kp = E().genKeypair();
  const identity: RemoteIdentity = { priv_hex: E().bytesToHex(kp.priv), relay, room, fingerprint };
  const t = new Tunnel(identity);
  await t.connect(code);            // 握手成功 = 码已消费、公钥已被 console 记为已配对
  saveIdentity(identity);
  return t;
}

const KarvyTunnel = { Tunnel, pairAndSave, loadIdentity, saveIdentity, clearIdentity };
(globalThis as unknown as { KarvyTunnel: typeof KarvyTunnel }).KarvyTunnel = KarvyTunnel;
export { KarvyTunnel };
