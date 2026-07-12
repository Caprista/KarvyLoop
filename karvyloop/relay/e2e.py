"""relay/e2e.py — 端到端加密(X25519 握手 + ChaCha20-Poly1305,nonce 计数防重放)。

「信使不拆信」的信封本体:relay 只见本模块产出的二进制帧,永远拿不到钥匙。

依赖 ``cryptography``(可选 extra ``[relay]``)——**import 本模块不炸**,真用到加密
时缺依赖才诚实报 ``pip install karvyloop[relay]``(基础安装零负担)。

帧格式(binary,v1 冻结;relay 对这些帧一律盲转发、不解析):
    header = b"KL" + ver(0x01) + type(1B)                      —— 4 字节
    0x01 HELLO   (client→console): header + client_pub(32) + pair_mac(32)
    0x02 WELCOME (console→client): header + console_pub(32) + confirm(16)
    0x10 DATA    (双向)          : header + seq(8B big-endian) + AEAD 密文
    0x7f ERR     (console→client): header + utf8 错误码(明文,不含任何秘密)

密钥:ss = X25519(己方私钥, 对方公钥) → HKDF-SHA256(salt=KLRELAY-v1)
派三把 32B 子钥:c2s(client→console)/ s2c(console→client)/ confirm(握手确认 MAC)。
nonce = 4B 方向常量 + 8B seq;**收侧只认严格递增的 seq**(重放/回退直接拒 = ReplayError)。
AEAD 的 AAD 绑整个帧头(header+seq),改头/换向都会解密失败。

配对(与 pairing.py 配合):
- HELLO 里的 pair_mac = HMAC-SHA256(配对码, "KL-PAIR|"+client_pub)——新设备必须持有
  `karvyloop relay-pair` 打出的一次性码;已配对过的 client_pub 免码。
- WELCOME 里的 confirm = HMAC(k_confirm, "KL-CONFIRM|"+client_pub+console_pub)[:16]
  ——证明对端真持有指纹对应的 console 静态私钥(不是 relay 在中间掉包)。
- client 端**必须**校验 fingerprint(console_pub) == 配对时抄下的指纹,错了 = 中间人,拒。

纪律(CLAUDE.md 安全地基):本模块绝不 log 任何密钥/明文;异常信息不带密钥材料。
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
from typing import Callable, Optional, Tuple

from karvyloop.relay import MAX_FRAME_BYTES

# === 帧常量(v1 冻结)===
MAGIC = b"KL"
VERSION = 1
T_HELLO = 0x01
T_WELCOME = 0x02
T_DATA = 0x10
T_ERR = 0x7F
HEADER_LEN = 4          # MAGIC(2) + ver(1) + type(1)
_KEY_LEN = 32
_CONFIRM_LEN = 16
_SEQ_LEN = 8
_SALT = b"KLRELAY-v1"
_NONCE_C2S = b"C2S\x00"   # client → console 方向常量(nonce 前 4 字节)
_NONCE_S2C = b"S2C\x00"   # console → client


class RelayCryptoUnavailable(RuntimeError):
    """缺 cryptography — 诚实报安装口令,不静默降级成明文。"""


class FrameError(ValueError):
    """帧格式坏 / AEAD 校验失败(内容被改或钥匙不对)。"""


class HandshakeError(ValueError):
    """握手失败(基类)。"""


class FingerprintMismatch(HandshakeError):
    """console 公钥指纹 ≠ 配对时抄下的指纹 —— 可能是中间人,必须拒。"""


class PairingRejected(HandshakeError):
    """配对码不对 / 设备未配对 —— console 拒绝这个 client。"""


class ReplayError(ValueError):
    """seq 不是严格递增 —— 重放/回退帧,拒收。"""


def _crypto():
    """惰性 import cryptography;缺了报安装口令(唯一的诚实降级 = 明确报错)。"""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
            X25519PublicKey,
        )
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as e:  # pragma: no cover - 环境相关
        raise RelayCryptoUnavailable(
            "relay E2E 加密需要 cryptography — 请 `pip install karvyloop[relay]` / "
            "relay E2E encryption needs the optional dependency: pip install karvyloop[relay]"
        ) from e
    return X25519PrivateKey, X25519PublicKey, ChaCha20Poly1305, HKDF, hashes


# === 密钥/指纹 ===

def gen_keypair() -> Tuple[bytes, bytes]:
    """生成 X25519 密钥对 → (priv_raw32, pub_raw32)。"""
    X25519PrivateKey, _, _, _, _ = _crypto()
    from cryptography.hazmat.primitives import serialization
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv_raw, pub_raw


def pub_from_priv(priv_raw: bytes) -> bytes:
    X25519PrivateKey, _, _, _, _ = _crypto()
    from cryptography.hazmat.primitives import serialization
    return (X25519PrivateKey.from_private_bytes(priv_raw)
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def fingerprint(pub_raw: bytes) -> str:
    """公钥指纹:sha256 前 16 hex,4-4-4-4 分组(配对页/`relay-pair` 打给人抄的)。"""
    h = hashlib.sha256(pub_raw).hexdigest()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def pair_mac(code: str, client_pub: bytes) -> bytes:
    """一次性配对码 MAC(纯 stdlib hmac,配对码是短期共享秘密)。"""
    return _hmac.new(code.strip().upper().encode("utf-8"),
                     b"KL-PAIR|" + client_pub, hashlib.sha256).digest()


def _derive_keys(shared: bytes, client_pub: bytes, console_pub: bytes):
    """HKDF-SHA256 从 X25519 共享秘密派 (k_c2s, k_s2c, k_confirm);info 绑双方公钥。"""
    _, _, _, HKDF, hashes = _crypto()
    out = {}
    for name in (b"c2s", b"s2c", b"confirm"):
        out[name] = HKDF(
            algorithm=hashes.SHA256(), length=_KEY_LEN, salt=_SALT,
            info=b"KLRELAY|" + name + b"|" + client_pub + console_pub,
        ).derive(shared)
    return out[b"c2s"], out[b"s2c"], out[b"confirm"]


# === 帧编解码 ===

def _header(ftype: int) -> bytes:
    return MAGIC + bytes([VERSION, ftype])


def frame_type(frame: bytes) -> Optional[int]:
    """帧类型;不是我们的帧(魔数/版本不对/太短)→ None(收侧丢弃,不炸)。"""
    if len(frame) < HEADER_LEN or frame[:2] != MAGIC or frame[2] != VERSION:
        return None
    return frame[3]


def err_frame(code: str) -> bytes:
    """明文错误帧(console→client 握手前唯一能说的话;只含错误码,零秘密)。"""
    return _header(T_ERR) + code.encode("utf-8")


def parse_err(frame: bytes) -> str:
    return frame[HEADER_LEN:].decode("utf-8", errors="replace")


# === 会话(握手完成后的 DATA 双向加密)===

class Session:
    """一次配对握手换出来的双向 AEAD 通道;send/open 各自维护严格递增 seq。

    线程模型:设计为单事件循环内使用;`seal` 是同步纯计算(无 await 点),
    并发 task 里调用要外挂 asyncio.Lock 保证「seal 与 send 原子」(见 client.py)。
    """

    def __init__(self, send_key: bytes, recv_key: bytes,
                 send_dir: bytes, recv_dir: bytes) -> None:
        _, _, ChaCha20Poly1305, _, _ = _crypto()
        self._send = ChaCha20Poly1305(send_key)
        self._recv = ChaCha20Poly1305(recv_key)
        self._send_dir = send_dir
        self._recv_dir = recv_dir
        self._seq_out = 0       # 下一发 = _seq_out+1
        self._seq_in = 0        # 已收最大 seq;新帧必须 > 它
        # 对端身份(console 侧 = 连进来的设备公钥;client 侧留空)。授权层据此 per-request
        # 查 scope + 撤销状态(§9.6 slice 2)。握手后由 console_accept 填。
        self.peer_pub: bytes = b""

    def seal(self, plaintext: bytes) -> bytes:
        """明文 → DATA 帧。超过帧上限直接拒(发送端自己守约,relay 也会拒)。"""
        self._seq_out += 1
        seq = self._seq_out.to_bytes(_SEQ_LEN, "big")
        header = _header(T_DATA) + seq
        nonce = self._send_dir + seq
        ct = self._send.encrypt(nonce, plaintext, header)
        frame = header + ct
        if len(frame) > MAX_FRAME_BYTES:
            raise FrameError(f"frame too large ({len(frame)} > {MAX_FRAME_BYTES})")
        return frame

    def open(self, frame: bytes) -> bytes:
        """DATA 帧 → 明文;seq 不严格递增 → ReplayError;AEAD 不过 → FrameError。"""
        if frame_type(frame) != T_DATA or len(frame) < HEADER_LEN + _SEQ_LEN + 16:
            raise FrameError("not a DATA frame")
        header = frame[:HEADER_LEN + _SEQ_LEN]
        seq_b = frame[HEADER_LEN:HEADER_LEN + _SEQ_LEN]
        seq = int.from_bytes(seq_b, "big")
        if seq <= self._seq_in:
            raise ReplayError(f"replayed/old seq {seq} (last {self._seq_in})")
        nonce = self._recv_dir + seq_b
        try:
            pt = self._recv.decrypt(nonce, frame[HEADER_LEN + _SEQ_LEN:], header)
        except Exception as e:   # InvalidTag —— 内容被改/钥匙不对;不带密钥材料
            raise FrameError("AEAD verification failed") from e
        self._seq_in = seq       # 只有验过 AEAD 才推进窗口(坏帧不许烧掉合法 seq)
        return pt


# === 握手 ===

def build_hello(client_priv: bytes, pairing_code: Optional[str]) -> bytes:
    """client 侧第 1 步:HELLO = client_pub + pair_mac(无码则 MAC 置零,已配对设备免码)。"""
    client_pub = pub_from_priv(client_priv)
    mac = pair_mac(pairing_code, client_pub) if pairing_code else b"\x00" * 32
    return _header(T_HELLO) + client_pub + mac


def console_accept(hello_frame: bytes, console_priv: bytes, console_pub: bytes,
                   verify_pair: Callable[[bytes, bytes], bool],
                   ) -> Tuple[bytes, "Session"]:
    """console 侧:验 HELLO(配对码/已配对指纹)→ (WELCOME 帧, 本端 Session)。

    verify_pair(client_pub, mac) 由 pairing.PairingStore.verify_and_consume 提供:
    一次性码首用即焚 + client_pub 记为已配对设备。
    """
    if frame_type(hello_frame) != T_HELLO or len(hello_frame) != HEADER_LEN + 64:
        raise HandshakeError("malformed HELLO")
    client_pub = hello_frame[HEADER_LEN:HEADER_LEN + 32]
    mac = hello_frame[HEADER_LEN + 32:HEADER_LEN + 64]
    if not verify_pair(client_pub, mac):
        raise PairingRejected("pairing code invalid/expired and device not paired")
    X25519PrivateKey, X25519PublicKey, _, _, _ = _crypto()
    shared = (X25519PrivateKey.from_private_bytes(console_priv)
              .exchange(X25519PublicKey.from_public_bytes(client_pub)))
    k_c2s, k_s2c, k_confirm = _derive_keys(shared, client_pub, console_pub)
    confirm = _hmac.new(k_confirm, b"KL-CONFIRM|" + client_pub + console_pub,
                        hashlib.sha256).digest()[:_CONFIRM_LEN]
    welcome = _header(T_WELCOME) + console_pub + confirm
    session = Session(send_key=k_s2c, recv_key=k_c2s,
                      send_dir=_NONCE_S2C, recv_dir=_NONCE_C2S)
    session.peer_pub = client_pub          # 授权层 per-request 查 scope/撤销用(§9.6 slice 2)
    return welcome, session


def client_complete(welcome_frame: bytes, client_priv: bytes,
                    expected_fingerprint: str) -> "Session":
    """client 侧第 2 步:验 WELCOME 指纹 + confirm MAC → 本端 Session。

    指纹不匹配 = 可能中间人(恶意 relay 掉包公钥)→ FingerprintMismatch,必须放弃。
    """
    if frame_type(welcome_frame) != T_WELCOME or \
            len(welcome_frame) != HEADER_LEN + 32 + _CONFIRM_LEN:
        raise HandshakeError("malformed WELCOME")
    console_pub = welcome_frame[HEADER_LEN:HEADER_LEN + 32]
    confirm = welcome_frame[HEADER_LEN + 32:]
    exp = (expected_fingerprint or "").strip().lower().replace("-", "")
    got = fingerprint(console_pub).replace("-", "")
    if not exp or not _hmac.compare_digest(exp, got):
        raise FingerprintMismatch("console key fingerprint mismatch — refusing (possible MITM)")
    client_pub = pub_from_priv(client_priv)
    X25519PrivateKey, X25519PublicKey, _, _, _ = _crypto()
    shared = (X25519PrivateKey.from_private_bytes(client_priv)
              .exchange(X25519PublicKey.from_public_bytes(console_pub)))
    k_c2s, k_s2c, k_confirm = _derive_keys(shared, client_pub, console_pub)
    want = _hmac.new(k_confirm, b"KL-CONFIRM|" + client_pub + console_pub,
                     hashlib.sha256).digest()[:_CONFIRM_LEN]
    if not _hmac.compare_digest(want, confirm):
        raise HandshakeError("WELCOME confirm MAC failed — peer does not hold the private key")
    return Session(send_key=k_c2s, recv_key=k_s2c,
                   send_dir=_NONCE_C2S, recv_dir=_NONCE_S2C)


__all__ = [
    "MAGIC", "VERSION", "T_HELLO", "T_WELCOME", "T_DATA", "T_ERR", "HEADER_LEN",
    "RelayCryptoUnavailable", "FrameError", "HandshakeError",
    "FingerprintMismatch", "PairingRejected", "ReplayError",
    "gen_keypair", "pub_from_priv", "fingerprint", "pair_mac",
    "frame_type", "err_frame", "parse_err",
    "Session", "build_hello", "console_accept", "client_complete",
]
