"""gen_e2e_vectors — 用 Python 端(真理来源 relay/e2e.py)生成跨实现字节级向量。

固定双方私钥 → hello/welcome/双向 DATA 帧全确定(HKDF/HMAC/ChaCha 无随机,nonce=方向+seq)。
JS 端(static/e2e.js)必须逐字节复现,否则互操作断。由 e2e_interop_check.mjs 消费,
tests/test_relay_e2e_interop.py 串起来跑(pytest → 本脚本 → node)。
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from karvyloop.relay import e2e  # noqa: E402
from karvyloop.relay.pairing import PairingStore  # noqa: E402


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def main(out_path: str) -> None:
    # 定死密钥(仅测试向量;x25519 clamp 由库内部处理,任意 32B 可当私钥)
    client_priv = bytes(range(1, 33))            # 0x01..0x20
    console_priv = bytes(range(101, 133))        # 0x65..0x84
    client_pub = e2e.pub_from_priv(client_priv)
    console_pub = e2e.pub_from_priv(console_priv)
    code = "TEST-CODE"

    hello = e2e.build_hello(client_priv, code)

    # console 侧握手(verify_pair 桩:直接验 MAC,同 pairing 逻辑)
    def verify(cpub: bytes, mac: bytes) -> bool:
        return e2e.pair_mac(code, cpub) == mac

    welcome, console_sess = e2e.console_accept(hello, console_priv, console_pub, verify)
    client_sess = e2e.client_complete(welcome, client_priv, e2e.fingerprint(console_pub))

    # 双向各 2 帧(seq 1,2;内容含 UTF-8 中文,逼出编码一致性)
    c2s_msgs = [b'{"id":1,"method":"GET","path":"/api/proposals/pending"}',
                "第二帧:中文内容 & bytes \x00\x01".encode("utf-8")]
    s2c_msgs = [b'{"id":1,"status":200,"body_b64":""}',
                "响应第二帧 🦫".encode("utf-8")]
    c2s_frames = [client_sess.seal(m) for m in c2s_msgs]      # client 发
    s2c_frames = [console_sess.seal(m) for m in s2c_msgs]     # console 发

    # console 侧能开 client 帧(Python 自洽;JS 端要复现 c2s_frames 的字节)
    for f in c2s_frames:
        console_sess.open(f)

    vectors = {
        "client_priv": b64(client_priv), "console_priv": b64(console_priv),
        "client_pub": b64(client_pub), "console_pub": b64(console_pub),
        "console_fingerprint": e2e.fingerprint(console_pub),
        "pair_code": code,
        "pair_mac": b64(e2e.pair_mac(code, client_pub)),
        "hello": b64(hello), "welcome": b64(welcome),
        "c2s_plain": [b64(m) for m in c2s_msgs],
        "c2s_frames": [b64(f) for f in c2s_frames],
        "s2c_plain": [b64(m) for m in s2c_msgs],
        "s2c_frames": [b64(f) for f in s2c_frames],
    }
    pathlib.Path(out_path).write_text(json.dumps(vectors), encoding="utf-8")
    print(f"vectors -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "e2e_vectors.json")
