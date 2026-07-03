"""karvyloop.relay — Karvy 信使 relay(docs/43 第二级:「信使不拆信」)。

出门在外用完整控制台的自有方案:家里发不了入站、发得了出站 →
console 出站 WSS 长连 relay;手机连同一 relay;两端之间只有 E2E 密文帧,
relay 无状态、无盘、只做配对(房间号 rendezvous)+ 盲转发。

模块:
- ``server``  — relay 本体(单文件 FastAPI,自部署一条命令;未来托管同一份代码)。
- ``e2e``     — 端到端加密(X25519 握手 + ChaCha20-Poly1305,nonce 计数防重放)。
- ``pairing`` — console 静态密钥 / 房间号 / 一次性配对码(``karvyloop relay-pair``)。
- ``client``  — console 侧客户端:解密后的 HTTP-over-frame 请求转发给本机 loopback
  console(带 token,深度防御),响应加密回传。

依赖:E2E 加密要 ``cryptography``(可选 extra ``[relay]``);缺了诚实报
``pip install karvyloop[relay]``,基础安装零负担。relay 服务本体只用 FastAPI(基础依赖)。
"""

# 全链路共用的帧大小上限(relay 拒收更大的帧;client 发送端也按它约束)。
MAX_FRAME_BYTES = 1024 * 1024  # 1 MiB

__all__ = ["MAX_FRAME_BYTES"]
