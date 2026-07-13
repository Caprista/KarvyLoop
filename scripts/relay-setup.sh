#!/usr/bin/env bash
# relay-setup.sh — 一条命令在你自己的公网 Linux 服务器上架起 KarvyLoop relay。
#
# relay = 无状态盲转发碰头点(「信使不拆信」,只见密文)。它让你的手机在**任意网络**
# 连回家里的 console —— 因为两台都躲在 NAT 后,需要一个公网碰头点。这是**你自己的** relay,
# 不绑任何人的服务器(开源:relay 地址是配置不是代码)。
#
# 用法(在你的服务器上,以能 sudo 的用户跑):
#   curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/relay-setup.sh | bash -s -- --domain relay.example.com
#   # 或先下载再跑:  bash relay-setup.sh --domain relay.example.com
#   # 无域名先裸跑(仅 Python 客户端可用,浏览器要域名+证书): bash relay-setup.sh --port 8767
#
# 做的事:① venv 装 karvyloop[relay] ② systemd 常驻 relay(开机自启)
#          ③ 有 --domain 则 nginx + Let's Encrypt 出 wss(浏览器要);无则裸开端口。
# 幂等:重复跑安全。要卸载见文末。
set -euo pipefail

DOMAIN=""; PORT="8767"; EMAIL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --email) EMAIL="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

say() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }

say "① Python venv + karvyloop[relay]"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip git
[ -d "$HOME/kl-venv" ] || python3 -m venv "$HOME/kl-venv"
"$HOME/kl-venv/bin/pip" install --quiet --upgrade pip
# karvyloop 未上 PyPI → 从 GitHub 装(relay extra 带 cryptography)
"$HOME/kl-venv/bin/pip" install "karvyloop[relay] @ git+https://github.com/Caprista/KarvyLoop.git"

say "② systemd 常驻 relay(本机 127.0.0.1:${PORT};开机自启)"
sudo tee /etc/systemd/system/karvy-relay.service >/dev/null <<EOF
[Unit]
Description=KarvyLoop stateless relay (blind-forwarding rendezvous)
After=network-online.target
Wants=network-online.target
[Service]
User=$USER
# 有域名 → 绑 localhost(nginx 反代 TLS);无域名 → 绑 0.0.0.0 裸开
ExecStart=$HOME/kl-venv/bin/karvyloop relay-serve --host $([ -n "$DOMAIN" ] && echo 127.0.0.1 || echo 0.0.0.0) --port ${PORT}
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now karvy-relay
sleep 2
systemctl is-active --quiet karvy-relay && say "relay 已运行 ✓" || { echo "relay 起不来,看 journalctl -u karvy-relay"; exit 1; }

if [ -z "$DOMAIN" ]; then
  IP=$(curl -fsS https://api.ipify.org 2>/dev/null || echo "<你的公网IP>")
  cat <<EOF

裸跑完成(无 TLS)。防火墙放行 TCP ${PORT}。
  • Python 客户端可用:   karvyloop console --relay ws://${IP}:${PORT}
  • 浏览器远程访问需要 wss(证书)→ 重跑本脚本加 --domain your.domain 才行。
EOF
  exit 0
fi

say "③ nginx + Let's Encrypt(wss://${DOMAIN};浏览器远程访问必需)"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx
sudo tee /etc/nginx/sites-available/karvy-relay >/dev/null <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    location ~ ^/(attach|join)\$ {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location / { default_type text/plain; return 200 'KarvyLoop relay — up.\n'; }
}
EOF
sudo ln -sf /etc/nginx/sites-available/karvy-relay /etc/nginx/sites-enabled/karvy-relay
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
CERTBOT_EMAIL_ARG=$([ -n "$EMAIL" ] && echo "--email $EMAIL" || echo "--register-unsafely-without-email")
sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos $CERTBOT_EMAIL_ARG --redirect

cat <<EOF

✅ 完成。你的 relay:  wss://${DOMAIN}
放行防火墙 TCP 80 + 443(certbot 与 wss 用;${PORT} 不必对公网开)。
家里 console 指过来(设一次,永久生效):
  在 ~/.karvyloop/config.yaml 加一行:   relay: wss://${DOMAIN}
  或每次:  karvyloop console --relay wss://${DOMAIN}
然后在手机 /m 页点 🌐「出门也能用」即完成配对。

卸载:  sudo systemctl disable --now karvy-relay && sudo rm /etc/systemd/system/karvy-relay.service /etc/nginx/sites-enabled/karvy-relay
EOF
