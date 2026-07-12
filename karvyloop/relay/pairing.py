"""relay/pairing.py — console 静态密钥 / 房间号 / 一次性配对码(``karvyloop relay-pair``)。

状态全在 **console 端** ``~/.karvyloop/``(relay 本体永远无状态无盘):
- ``relay_key``   —— console 静态 X25519 私钥(raw 32B,POSIX 上 0600)。**绝不出机、绝不 log。**
- ``relay.json``  —— {rid, codes[], paired[]}:
    - rid:房间号(rendezvous 用,稳定;泄露只导致 DoS——没有钥匙什么也解不开)。
    - codes:未消费的一次性配对码(**明文短期秘密**,TTL 15 分钟 + 首用即焚;与
      config.yaml 里的 API key 同一信任域/同级待遇,export 应排除)。存明文是因为
      验 HELLO 的 HMAC 需要码本身,哈希存法无法重算 MAC。
    - paired:已配对设备的 client 公钥(hex)——之后免码重连(公钥不是秘密)。

配对 v1 = `karvyloop relay-pair` 打印文本(relay 地址+房间号+指纹+一次性码);
浏览器端扫码/JS 解密属 P2(前端禁动),本模块只管 console 侧真理来源。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from karvyloop.relay import e2e

KEY_FILE = "relay_key"
STATE_FILE = "relay.json"
CODE_TTL_S = 15 * 60          # 一次性配对码有效期
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # 去易混字符(0O1IL)

# --- 访问 scope(§9.6 slice 2:授权层最粗一档,方法级)---
SCOPE_FULL = "full"          # 完整访问(自有设备:所有方法 + 主人 token)
SCOPE_READ = "read"          # 只读(分享给别人看:仅 GET/HEAD/OPTIONS,不能改)
_VALID_SCOPES = frozenset({SCOPE_FULL, SCOPE_READ})
# 只读 scope 放行的方法(其余=改动,只读设备一律拒)。
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def normalize_scope(scope: str) -> str:
    """归一 scope(deny-by-default):已知档原样;未知/空/篡改 → `read`(最不信任,别因笔误发全权)。"""
    s = (scope or "").strip().lower()
    return s if s in _VALID_SCOPES else SCOPE_READ


def _default_dir() -> Path:
    return Path.home() / ".karvyloop"


class PairingStore:
    """console 侧配对状态(密钥/房间号/一次性码/已配对设备)。base_dir 可注入(测试用 tmp)。"""

    def __init__(self, base_dir: "Optional[Path | str]" = None) -> None:
        self.dir = Path(base_dir) if base_dir else _default_dir()

    # --- 私有:状态文件 ---
    @property
    def key_path(self) -> Path:
        return self.dir / KEY_FILE

    @property
    def state_path(self) -> Path:
        return self.dir / STATE_FILE

    def _load(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        self._chmod600(tmp)
        os.replace(tmp, self.state_path)

    @staticmethod
    def _chmod600(p: Path) -> None:
        if os.name != "nt":
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass

    # --- 身份 ---
    def identity(self) -> Tuple[bytes, bytes]:
        """(priv_raw, pub_raw);首次调用生成并落盘(0600)。缺 cryptography → RelayCryptoUnavailable。"""
        if self.key_path.exists():
            priv = self.key_path.read_bytes()
            if len(priv) != 32:
                raise ValueError(f"corrupt relay key file: {self.key_path}")
            return priv, e2e.pub_from_priv(priv)
        priv, pub = e2e.gen_keypair()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(priv)
        self._chmod600(self.key_path)
        return priv, pub

    def fingerprint(self) -> str:
        return e2e.fingerprint(self.identity()[1])

    # --- 房间号 ---
    def rid(self) -> str:
        state = self._load()
        rid = state.get("rid")
        if not rid:
            rid = "r" + secrets.token_hex(11)     # 23 字符,[a-z0-9],server 校验通过
            state["rid"] = rid
            self._save(state)
        return str(rid)

    # --- 一次性配对码 ---
    def new_code(self, scope: str = "full") -> str:
        """生成一枚一次性码(XXXX-XXXX),TTL 15 分钟;顺手清理过期码。

        scope 绑在码上 → 用此码配对的设备继承该 scope(§9.6 slice 2):
        `full`=完整访问(自有设备默认);`read`=只读(GET/HEAD/OPTIONS,分享给别人看不能改)。
        未知 scope **deny-by-default 降到 read**(别因笔误就发全权)。
        """
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        state = self._load()
        codes = [c for c in state.get("codes", [])
                 if time.time() - float(c.get("ts", 0)) < CODE_TTL_S]
        codes.append({"code": code, "ts": time.time(), "scope": normalize_scope(scope)})
        state["codes"] = codes
        self._save(state)
        return code

    @staticmethod
    def _paired_pub(entry) -> str:
        """一条 paired 记录的公钥 hex —— 兼容旧格式(裸 hex 字符串)与新格式(dict)。"""
        return entry if isinstance(entry, str) else str((entry or {}).get("pub", ""))

    def _paired_pubs(self, state: dict) -> set:
        """已配对设备的公钥 hex 集合(去掉已撤销的)。撤销 = 从 paired 移除,不留 tombstone。"""
        return {self._paired_pub(e) for e in state.get("paired", []) if self._paired_pub(e)}

    def verify_and_consume(self, client_pub: bytes, mac: bytes) -> bool:
        """HELLO 验证门(交给 e2e.console_accept 当 verify_pair 回调)。

        - client_pub 已配对**且未撤销** → 直接过(免码重连)。撤销过的设备不在 paired 里 → 落到码验证。
        - 否则逐枚未过期一次性码重算 HMAC 比对;命中 → **码即焚** + 记设备为已配对(结构化记录)。
        """
        import hmac as _hmac
        state = self._load()
        pubhex = client_pub.hex()
        if pubhex in self._paired_pubs(state):
            return True
        now = time.time()
        codes = state.get("codes", [])
        live = [c for c in codes if now - float(c.get("ts", 0)) < CODE_TTL_S]
        matched = None
        for c in live:
            if _hmac.compare_digest(e2e.pair_mac(str(c.get("code", "")), client_pub), mac):
                matched = c
                break
        if matched is not None:
            live.remove(matched)                      # 一次性:首用即焚
            state["codes"] = live
            # 结构化配对记录(§9.6 授权层):记谁、继承码上的 scope、何时配对。
            # 码默认 scope=full(自有设备完整访问,零回归);分享码 scope=read 则设备只读。
            state.setdefault("paired", []).append(
                {"pub": pubhex, "label": "",
                 "scope": normalize_scope(matched.get("scope", "full")), "granted_at": now})
            self._save(state)
            return True
        if len(live) != len(codes):                   # 只是清了过期码
            state["codes"] = live
            self._save(state)
        return False

    def scope_for(self, pubkey_hex: str) -> Optional[str]:
        """一个 client 公钥的 scope:paired 且未撤销 → scope 字符串;未 paired/已撤销 → **None**。

        **per-request 调用 = 回源在线校验**(§9.6 slice 2/4):撤销后活连接的下一个请求
        就查不到 → None → 授权层拒。这是"撤销即断"落到活连接上的机制(不必断 WS)。
        兼容旧裸 hex 记录(视作 full)。
        """
        ph = (pubkey_hex or "").lower()
        if not ph:
            return None
        for e in self._load().get("paired", []):
            if self._paired_pub(e).lower() == ph:
                return normalize_scope(e.get("scope", SCOPE_FULL)) if isinstance(e, dict) else SCOPE_FULL
        return None

    # --- 已配对设备:列 + 撤销(§9.6 授权层:撤销 = 绝对把控权)---
    def list_paired(self) -> list:
        """列已配对设备(结构化记录 [{pub, fingerprint, label, scope, granted_at}])。

        兼容旧裸 hex 记录(视作 label="" scope="full")。fingerprint 现算,便于用户按指纹撤销。
        """
        state = self._load()
        out = []
        for e in state.get("paired", []):
            pubhex = self._paired_pub(e)
            if not pubhex:
                continue
            rec = {"pub": pubhex, "label": "", "scope": "full", "granted_at": 0.0}
            if isinstance(e, dict):
                rec.update({k: e[k] for k in ("label", "scope", "granted_at") if k in e})
            try:
                rec["fingerprint"] = e2e.fingerprint(bytes.fromhex(pubhex))
            except Exception:
                rec["fingerprint"] = ""
            out.append(rec)
        return out

    def revoke(self, ident: str) -> bool:
        """撤销一个已配对设备(按公钥 hex **或** 指纹匹配)→ 它再也免不了码重连(回源即断的地基)。

        撤销即从 paired 移除(不留 tombstone;要恢复得重新配对)。返回是否真撤了一个。
        **注**:本层堵的是"撤销后**重连**";已在的活连接由 relay/client 断连另行处理(关机即断窗口)。
        """
        ident = (ident or "").strip().lower()
        if not ident:
            return False
        state = self._load()
        paired = state.get("paired", [])
        kept = []
        removed = False
        for e in paired:
            pubhex = self._paired_pub(e).lower()
            if not pubhex:
                continue
            fpr = ""
            try:
                fpr = e2e.fingerprint(bytes.fromhex(pubhex)).lower()
            except Exception:
                pass
            if pubhex == ident or (fpr and fpr == ident):
                removed = True
                continue                              # 丢弃 = 撤销
            kept.append(e)
        if removed:
            state["paired"] = kept
            self._save(state)
        return removed


def cmd_relay_pair(relay_url: Optional[str] = None,
                   state_dir: Optional[str] = None, scope: str = "full") -> int:
    """`karvyloop relay-pair [--scope full|read]`:打印 relay 地址 + 房间号 + 指纹 + 一次性配对码。

    scope=full(默认)= 自有设备完整访问;scope=read = 分享给别人**只读**(GET/HEAD/OPTIONS,看不能改)。
    v1 = 文本配对(把这四样输进会说 relay E2E 协议的客户端);二维码/浏览器端 JS 解密 = P2。
    """
    store = PairingStore(state_dir)
    try:
        _, pub = store.identity()
    except e2e.RelayCryptoUnavailable as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    rid = store.rid()
    scope = normalize_scope(scope)
    code = store.new_code(scope)
    relay = relay_url or "wss://<your-relay-host>/   (self-host: karvyloop relay-serve)"
    scope_note = "full access (your own device)" if scope == SCOPE_FULL else "READ-ONLY (share to others: view, not modify)"
    print("KarvyLoop messenger relay — pairing info (v1: text pairing; QR/browser = P2)")
    print(f"  Relay:         {relay}")
    print(f"  Room:          {rid}")
    print(f"  Fingerprint:   {e2e.fingerprint(pub)}")
    print(f"  One-time code: {code}   (expires in {CODE_TTL_S // 60} min, single use)")
    print(f"  Scope:         {scope} — {scope_note}")
    print("  Start console with:  karvyloop console --relay <relay-url>")
    return 0


def cmd_relay_unpair(target: Optional[str] = None,
                     state_dir: Optional[str] = None) -> int:
    """`karvyloop relay-unpair [target]`:列已配对设备,或按指纹/公钥撤销一个。

    撤销 = 从 paired 移除 → 该设备再也免不了码重连(回源即断的地基;活连接的断连另行处理)。
    """
    store = PairingStore(state_dir)
    paired = store.list_paired()
    if not target:
        if not paired:
            print("No paired devices.")
            return 0
        print(f"Paired devices ({len(paired)}):")
        for r in paired:
            scope = r.get("scope", "full")
            label = (" · " + r["label"]) if r.get("label") else ""
            print(f"  {r.get('fingerprint', '?')}  [scope={scope}]{label}")
        print("\nRevoke one with:  karvyloop relay-unpair <fingerprint>")
        return 0
    if store.revoke(target):
        print(f"Revoked: {target} — it can no longer reconnect without a fresh pairing code.")
        return 0
    print(f"No paired device matched: {target}  (list with: karvyloop relay-unpair)")
    return 1


__all__ = ["PairingStore", "cmd_relay_pair", "cmd_relay_unpair", "CODE_TTL_S"]
