# Self-hosting your KarvyLoop relay (away-from-home access)

> 🌐 **Language**: **English (current)** · [中文](RELAY-SELF-HOST.zh-CN.md)

KarvyLoop runs on your own machine. To reach it **from anywhere** (your phone on
cellular, a café network), your phone and your home machine — both behind NATs —
need a public **rendezvous point**. That's a *relay*: a tiny, stateless,
blind-forwarding server that only ever sees ciphertext. It is **yours**, on **your**
server — the relay address is configuration, never hardcoded, so you are never
locked to anyone else's infrastructure.

The relay never holds your keys and cannot read your data (frames are end-to-end
encrypted between your phone and your home console; the relay just forwards bytes).
Traffic is tiny — decision cards / chat / memory deltas are a few KB, not video.

## What you need

- A small public Linux server (1 vCPU / 1 GB is plenty — the relay is featherweight).
- A domain name pointing at it (an **A record** → your server's public IP). A domain
  is **required for phone/browser access**, because browsers only allow secure
  WebSockets (`wss://`), which need a TLS certificate, which needs a domain. (No
  domain? You can still use the Python client over `ws://`, but not the phone browser.)

## One command

On your server, as a user with `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/relay-setup.sh \
  | bash -s -- --domain relay.yourdomain.com
```

That script: creates a venv and installs `karvyloop[relay]`, runs the relay as a
boot-persistent **systemd** service, then sets up **nginx + Let's Encrypt** so your
relay is reachable at `wss://relay.yourdomain.com` (auto-renewing certificate).

Open your server firewall / cloud security group to **TCP 80 and 443** (certbot's
challenge + the `wss` endpoint). Port 8767 does **not** need to be public — nginx
proxies to it on localhost.

## Point your home console at it (set once)

Add one line to `~/.karvyloop/config.yaml`:

```yaml
relay: wss://relay.yourdomain.com
```

Now `karvyloop console` attaches to your relay automatically on every start.
(Or pass it per-run: `karvyloop console --relay wss://relay.yourdomain.com`.)

## Pair your phone

1. On your phone, **while on your home Wi-Fi**, open the console's `/m` page.
2. Tap **🌐 (away-from-home)**. That's it — this phone gets its own key, stored only
   on the phone, and can now reach home from anywhere.
3. To revoke a phone (lost it, or just cleaning up): open **🖥️ My devices** on the
   computer → **Authorized phones** → **Revoke**. Its very next request is refused.

The pairing invite is a one-time code (15-minute expiry, single use). Your phone's
key is never shown in a URL or QR — a screenshot can't grant anyone access.

## No domain yet?

Run it bare — Python client only, no browser:

```bash
bash relay-setup.sh --port 8767        # binds 0.0.0.0:8767, no TLS
```

Then `karvyloop console --relay ws://YOUR_SERVER_IP:8767` at home, and reach it with
`karvyloop remote --relay ws://... --room ... --fingerprint ... --code ...` from
another machine. Add a domain later and re-run with `--domain` to unlock the phone.

## Uninstall

```bash
sudo systemctl disable --now karvy-relay
sudo rm /etc/systemd/system/karvy-relay.service /etc/nginx/sites-enabled/karvy-relay
```

## Threat model (why this is safe on a public box)

The relay is structurally incapable of reading your traffic: it never imports the
crypto module, only blind-forwards binary frames. A malicious or compromised relay
can at most deny service or see traffic metadata — it cannot read, alter, or forge
what's inside the AEAD-sealed frames. Your keys never leave your devices.
