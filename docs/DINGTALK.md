# DingTalk Bot Setup Guide

[中文完整版](DINGTALK.zh-CN.md)

Connect one KarvyLoop role (agent) to a DingTalk group: members @ the bot, and the role answers in its own persona. No public endpoint needed — the channel uses DingTalk's official **Stream mode** (an outbound WebSocket from your machine), so it works from behind NAT with zero port forwarding.

## Steps in brief

1. **DingTalk Open Platform** (https://open-dev.dingtalk.com/) → create an **internal enterprise app** → add the **Robot** capability → set message receiving to **Stream mode** → **publish a version** (publishing is the step everyone forgets) → add the bot to a group.
2. Copy the app's **ClientID / ClientSecret** (AppKey/AppSecret under Credentials).
3. On the machine running KarvyLoop: `pip install "karvyloop[dingtalk]"`.
4. Edit `~/.karvyloop/config.yaml`:

```yaml
channels:
  dingtalk:
    enabled: true
    client_id: "ding..."
    client_secret: "..."
    role: "资料管家"            # the role (agent) that answers in the group
    allow_senders: ["your-staffId"]   # empty = nobody can drive it (fail-closed)
```

5. Restart the console. Look for `[dingtalk] 通道已起` in the startup log.
6. In the group, @ the bot. It replies as the bound role, remembers context, and anyone not on `allow_senders` gets a single polite refusal.

**Finding your staffId**: send one @ mention with a placeholder in `allow_senders`; the console log prints the full staffId in the `[dingtalk] 白名单外 sender 被拒` line — paste it in and restart.

**Safety**: never share `client_secret` (chat screenshots included) and never commit it. The agent's high-risk actions still require your approval — the DingTalk channel changes nothing about the H2A gates.

Full checklist + troubleshooting table: [DINGTALK.zh-CN.md](DINGTALK.zh-CN.md) (Chinese).
