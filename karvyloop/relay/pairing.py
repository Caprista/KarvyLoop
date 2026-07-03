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
    def new_code(self) -> str:
        """生成一枚一次性码(XXXX-XXXX),TTL 15 分钟;顺手清理过期码。"""
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        state = self._load()
        codes = [c for c in state.get("codes", [])
                 if time.time() - float(c.get("ts", 0)) < CODE_TTL_S]
        codes.append({"code": code, "ts": time.time()})
        state["codes"] = codes
        self._save(state)
        return code

    def verify_and_consume(self, client_pub: bytes, mac: bytes) -> bool:
        """HELLO 验证门(交给 e2e.console_accept 当 verify_pair 回调)。

        - client_pub 已配对 → 直接过(免码重连)。
        - 否则逐枚未过期一次性码重算 HMAC 比对;命中 → **码即焚** + 记设备为已配对。
        """
        import hmac as _hmac
        state = self._load()
        pubhex = client_pub.hex()
        if pubhex in state.get("paired", []):
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
            state.setdefault("paired", []).append(pubhex)
            self._save(state)
            return True
        if len(live) != len(codes):                   # 只是清了过期码
            state["codes"] = live
            self._save(state)
        return False


def cmd_relay_pair(relay_url: Optional[str] = None,
                   state_dir: Optional[str] = None) -> int:
    """`karvyloop relay-pair`:打印 relay 地址 + 房间号 + 公钥指纹 + 一次性配对码。

    v1 = 文本配对(把这四样输进会说 relay E2E 协议的客户端);二维码/浏览器端 JS 解密 = P2。
    """
    store = PairingStore(state_dir)
    try:
        _, pub = store.identity()
    except e2e.RelayCryptoUnavailable as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    rid = store.rid()
    code = store.new_code()
    relay = relay_url or "wss://<your-relay-host>/   (self-host: karvyloop relay-serve)"
    print("KarvyLoop messenger relay — pairing info (v1: text pairing; QR/browser = P2)")
    print(f"  Relay:         {relay}")
    print(f"  Room:          {rid}")
    print(f"  Fingerprint:   {e2e.fingerprint(pub)}")
    print(f"  One-time code: {code}   (expires in {CODE_TTL_S // 60} min, single use)")
    print("  Start console with:  karvyloop console --relay <relay-url>")
    return 0


__all__ = ["PairingStore", "cmd_relay_pair", "CODE_TTL_S"]
