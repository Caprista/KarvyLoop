var KarvyTunnelBundle = (function(exports) {
  "use strict";
  const E = () => globalThis.KarvyE2E;
  const IDENTITY_KEY = "karvy_remote_identity";
  function loadIdentity() {
    try {
      const raw = localStorage.getItem(IDENTITY_KEY);
      if (!raw) return null;
      const d = JSON.parse(raw);
      return d && d.priv_hex && d.relay && d.room && d.fingerprint ? d : null;
    } catch (e) {
      return null;
    }
  }
  function saveIdentity(id) {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(id));
  }
  function clearIdentity() {
    try {
      localStorage.removeItem(IDENTITY_KEY);
    } catch (e) {
    }
  }
  function b64encode(b) {
    let s = "";
    for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s);
  }
  function b64decode(s) {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  class Tunnel {
    // "connecting"|"open"|"closed"|"error:<code>"
    constructor(identity) {
      this.ws = null;
      this.sess = null;
      this.nextId = 0;
      this.pending = /* @__PURE__ */ new Map();
      this.onstate = null;
      this.identity = identity;
    }
    get connected() {
      return this.sess !== null && this.ws !== null && this.ws.readyState === 1;
    }
    /** 连 relay + 握手。code 只在首次配对给;之后免码(console 记住公钥)。 */
    connect(code) {
      const url = this.identity.relay.replace(/\/+$/, "") + "/join?rid=" + encodeURIComponent(this.identity.room);
      this.onstate && this.onstate("connecting");
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(url);
        ws.binaryType = "arraybuffer";
        let settled = false;
        const fail = (e) => {
          if (!settled) {
            settled = true;
            reject(e);
          }
          try {
            ws.close();
          } catch (x) {
          }
        };
        ws.onerror = () => fail(new Error("relay unreachable"));
        ws.onopen = () => {
          try {
            ws.send(E().buildHello(E().hexToBytes(this.identity.priv_hex), code || null));
          } catch (e) {
            fail(e);
          }
        };
        ws.onmessage = (ev) => {
          if (!(ev.data instanceof ArrayBuffer)) return;
          const frame = new Uint8Array(ev.data);
          const ft = E().frameType(frame);
          if (this.sess === null) {
            if (ft === E().T_ERR) {
              this.onstate && this.onstate("error:" + E().parseErr(frame));
              return fail(new Error(E().parseErr(frame)));
            }
            try {
              this.sess = E().clientComplete(frame, E().hexToBytes(this.identity.priv_hex), this.identity.fingerprint);
            } catch (e) {
              return fail(e);
            }
            this.ws = ws;
            ws.onclose = () => this._closed();
            this.onstate && this.onstate("open");
            settled = true;
            resolve();
            return;
          }
          this._dispatch(frame);
        };
        ws.onclose = () => fail(new Error("relay closed during handshake"));
      });
    }
    _dispatch(frame) {
      if (E().frameType(frame) !== E().T_DATA || this.sess === null) return;
      let resp;
      try {
        resp = JSON.parse(new TextDecoder().decode(this.sess.open(frame)));
      } catch (e) {
        return;
      }
      const p = this.pending.get(resp.id);
      if (!p) return;
      this.pending.delete(resp.id);
      clearTimeout(p.timer);
      p.resolve({
        status: Number(resp.status || 0),
        headers: resp.headers || {},
        error: String(resp.error || ""),
        body: resp.body_b64 ? b64decode(resp.body_b64) : new Uint8Array(0)
      });
    }
    _closed() {
      this.sess = null;
      const err = new Error("relay connection lost");
      this.pending.forEach((p) => {
        clearTimeout(p.timer);
        p.reject(err);
      });
      this.pending.clear();
      this.onstate && this.onstate("closed");
    }
    /** 镜像 remote.py request():path 必须以 / 开头;返回 {status, headers, body, error}。 */
    request(method, path, opts) {
      if (!path.startsWith("/") || path.includes("://")) {
        return Promise.reject(new Error("path must start with / and carry no scheme"));
      }
      if (!this.connected || this.sess === null || this.ws === null) {
        return Promise.reject(new Error("tunnel not connected"));
      }
      this.nextId += 1;
      const id = this.nextId;
      const req = { id, method: method.toUpperCase(), path };
      if (opts && opts.headers && Object.keys(opts.headers).length) req.headers = opts.headers;
      if (opts && opts.body && opts.body.length) req.body_b64 = b64encode(opts.body);
      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          this.pending.delete(id);
          reject(new Error("tunnel request timeout"));
        }, opts && opts.timeoutMs || 3e4);
        this.pending.set(id, { resolve, reject, timer });
        try {
          this.ws.send(this.sess.seal(new TextEncoder().encode(JSON.stringify(req))));
        } catch (e) {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(e);
        }
      });
    }
    /** fetch 形状适配:给 /m 的 KarvyFetch 用(Response-like:ok/status/json()/text())。 */
    async tunnelFetch(path, init) {
      const r = await this.request(init && init.method ? init.method : "GET", path, {
        headers: init && init.headers,
        body: init && init.body ? new TextEncoder().encode(init.body) : void 0
      });
      const text = new TextDecoder().decode(r.body);
      return {
        ok: r.status >= 200 && r.status < 300 && !r.error,
        status: r.status,
        json: () => Promise.resolve(JSON.parse(text)),
        text: () => Promise.resolve(text)
      };
    }
    close() {
      try {
        this.ws && this.ws.close();
      } catch (e) {
      }
      this._closed();
    }
  }
  async function pairAndSave(relay, room, fingerprint, code) {
    const kp = E().genKeypair();
    const identity = { priv_hex: E().bytesToHex(kp.priv), relay, room, fingerprint };
    const t = new Tunnel(identity);
    await t.connect(code);
    saveIdentity(identity);
    return t;
  }
  const KarvyTunnel = { Tunnel, pairAndSave, loadIdentity, saveIdentity, clearIdentity };
  globalThis.KarvyTunnel = KarvyTunnel;
  exports.KarvyTunnel = KarvyTunnel;
  exports.Tunnel = Tunnel;
  exports.clearIdentity = clearIdentity;
  exports.loadIdentity = loadIdentity;
  exports.pairAndSave = pairAndSave;
  exports.saveIdentity = saveIdentity;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
