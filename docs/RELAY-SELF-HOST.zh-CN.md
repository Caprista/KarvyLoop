# 自建你的 KarvyLoop relay(出门也能访问)

> 🌐 **语言**: [English](RELAY-SELF-HOST.md) · **中文(当前)**

KarvyLoop 跑在你自己的机器上。要**在任意网络**连回它(手机 4G、咖啡馆网络),你的手机和家里
机器——两台都躲在 NAT 后面——需要一个公网**碰头点**。这就是 *relay*:一台极小、无状态、
盲转发的服务器,永远只见密文。它是**你的**、在**你的**服务器上——relay 地址是配置、绝不硬编码,
所以你永远不会被绑在别人的基础设施上。

relay 从不持有你的钥匙、读不了你的数据(帧在你手机和家里 console 之间端到端加密,relay 只搬字节)。
流量极小——决策卡/聊天/记忆增量都是几 KB,不是视频。

## 你需要

- 一台小的公网 Linux 服务器(1 核 1G 绰绰有余,relay 是羽量级)。
- 一个指向它的域名(**A 记录** → 服务器公网 IP)。**手机/浏览器访问必须有域名**,因为浏览器
  只允许安全 WebSocket(`wss://`),它要 TLS 证书,证书要域名。(没域名?Python 客户端仍能走
  `ws://`,但手机浏览器不行。)

## 一条命令

在你的服务器上,用能 `sudo` 的用户:

```bash
curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/relay-setup.sh \
  | bash -s -- --domain relay.yourdomain.com
```

这个脚本:建 venv 装 `karvyloop[relay]`,把 relay 做成开机自启的 **systemd** 服务,再配
**nginx + Let's Encrypt**,让你的 relay 在 `wss://relay.yourdomain.com` 可达(证书自动续期)。

在服务器防火墙/云安全组放行 **TCP 80 和 443**(certbot 挑战 + `wss` 端点)。8767 端口**不必**
对公网开——nginx 在本机反代到它。

## 家里 console 指过去(设一次)

在 `~/.karvyloop/config.yaml` 加一行:

```yaml
relay: wss://relay.yourdomain.com
```

之后 `karvyloop console` 每次启动自动挂上你的 relay。(或每次传:
`karvyloop console --relay wss://relay.yourdomain.com`。)

## 配对手机

1. 手机**连着家里 Wi-Fi**,打开 console 的 `/m` 页。
2. 点 **🌐(出门也能用)**。就这样——这台手机拿到自己的钥匙(只存在手机上),以后在外面也连得上家。
3. 吊销一台手机(丢了,或清理):在电脑上打开 **🖥️ 我的设备** → **已授权的手机** → **吊销**。
   它的下一个请求就被拒。

配对邀请是一次性码(15 分钟过期、首用即焚)。你手机的钥匙**从不显示在 URL 或二维码里**——
截图给不了任何人访问权。

## 还没有域名?

裸跑——只有 Python 客户端,没有浏览器:

```bash
bash relay-setup.sh --port 8767        # 绑 0.0.0.0:8767,无 TLS
```

然后家里 `karvyloop console --relay ws://你的服务器IP:8767`,从另一台机器用
`karvyloop remote --relay ws://... --room ... --fingerprint ... --code ...` 连回来。
以后加了域名,重跑加 `--domain` 就解锁手机。

## 卸载

```bash
sudo systemctl disable --now karvy-relay
sudo rm /etc/systemd/system/karvy-relay.service /etc/nginx/sites-enabled/karvy-relay
```

## 威胁模型(为什么放公网也安全)

relay 结构上就读不了你的流量:它从不 import 加密模块,只盲转发二进制帧。恶意或被攻破的 relay
最多拒绝服务、看流量元数据——读不了、改不了、伪造不了 AEAD 封的帧里的东西。你的钥匙从不离开你的设备。
