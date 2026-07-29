"""mcp_presets — console 的「渠道预设」目录(#42 优化:拧开就有水)。

痛点:agent 够不着你的生活 —— 想接文件/网页/GitHub 得自己去 config.yaml 手写
`mcp.servers`(name/command/args/env),门槛劝退。这里把**知名、uvx/npx 一条命令就能跑**
的 MCP server 做成预设目录,console 里一键写进 config.yaml。

事实对齐(别发明形状):
- 消费方是 `karvyloop/coding/tools/mcp_tool.py:read_mcp_server_configs` —— 它读
  config.yaml 的 `mcp.servers: [{name, command, args, env}]`(stdio)或
  `[{name, url, transport: http, token}]`(remote),build_server_config 按预设形状
  产出其中之一(不多不少)。
- **热加载(docs/96 刀1)**:连接生命周期已抽进 `console/mcp_manager.McpConnectionManager`
  (启动 = 第一次 reconnect;apply/add/手动重连端点复用同一函数)→ apply 成功后**装上即用**,
  不再要求重启;manager 不在(如 console 未跑完整 lifespan)时端点如实退回 requires_restart。
- 密钥(如 GitHub token)落 config.yaml —— 它本来就是密钥之家(仓外);本模块**绝不
  log/print 密钥**,API 响应绝不回显 params。

安全默认:
- filesystem 预设默认圈定 **KarvyLoop 工作区**(config_workspace.resolve_workspace),
  **不是家目录** —— 用户可自选文件夹,但默认不把整台机器递出去。

预设字段(docs/96 刀1 扩展,向后兼容加):
- icon:emoji,前端卡片用。
- category:"app"(生活/工作应用 ——「接上你的应用」区)| "channel"(通用渠道/开发向)。
- credential_url:凭证怎么拿的指路链接(空 = 不需要凭证)。
- outbound_tools:该 server **发送/写出类**工具名(server 侧原始名,不带我们的 `mcp_<srv>_`
  前缀)—— 显式标注供刀0(deontic 外发闸门)消费;本模块只提供数据,不做任何判定。
- disabled + disabled_reason:占位预设(如需 OAuth 的 Gmail)—— 卡片可见但不可接入,
  文案诚实;apply 会 fail-closed。
- url:remote(streamable HTTP)预设 —— 有 url 走 remote 形状,无 url 走 command/args stdio。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# 参数默认值里的哨兵:解析成用户工作区(resolve_workspace;绝不默认家目录)
_WS = "@workspace"

# 所有预设共有的扩展字段默认值(docs/96 刀1;缺省即"无",_with_defaults 统一补齐,
# 前端/测试可以放心按全字段读)。
_PRESET_DEFAULTS: dict[str, Any] = {
    "icon": "🔌",
    "category": "channel",
    "credential_url": "",
    "outbound_tools": [],
    "disabled": False,
    "disabled_reason": "",
    "command": "",
    "args_template": [],
    "env_template": {},
    "url": "",
    "params": [],
    "needs_secret": False,
    "secret_hint": "",
}


def _with_defaults(p: dict[str, Any]) -> dict[str, Any]:
    return {**_PRESET_DEFAULTS, **p}


# 预设目录:知名 MCP server。stdio 的 uvx/npx 一条命令能跑;remote 的贴 token 即通。
# description / risk_note / disabled_reason 双语一条(en · zh),前端原样展示。
# 「接上你的应用」(category=app)排前面 —— 生活应用策展第一批(docs/96 刀1)。
PRESETS: list[dict[str, Any]] = [_with_defaults(p) for p in [
    {
        "id": "notion",
        "name": "Notion",
        "icon": "📝",
        "category": "app",
        "description": "Search, read and write your Notion pages & databases — notes, docs, tasks. "
                       "· 搜索、读写你的 Notion 页面和数据库 —— 笔记、文档、任务。",
        "url": "https://mcp.notion.com/mcp",
        "params": [{"key": "token", "required": True, "secret": True}],
        "needs_secret": True,
        "secret_hint": "Notion internal integration token (ntn_…) — create one and share the pages "
                       "you want it to see. · Notion 内部集成令牌(ntn_…),建好后把要用的页面分享给它。",
        "credential_url": "https://www.notion.so/profile/integrations",
        # server 侧原始工具名(notion- 前缀是 Notion hosted MCP 自带的),写出/发送类。
        # 来源:developers.notion.com/docs/mcp-supported-tools(2026-07 核对)。
        "outbound_tools": ["notion-create-pages", "notion-update-page", "notion-move-pages",
                           "notion-duplicate-page", "notion-create-database",
                           "notion-update-data-source", "notion-create-view",
                           "notion-update-view", "notion-create-comment"],
        "risk_note": "Reads and writes only the pages you share with the integration — share "
                     "narrowly. · 只能读写你分享给该集成的页面 —— 按需分享,别全给。",
    },
    {
        "id": "github",
        "name": "GitHub",
        "icon": "🐙",
        "category": "app",
        "description": "Search repos, read code, manage issues & PRs — GitHub's official hosted MCP "
                       "server (upgraded from the old community stdio server). "
                       "· 搜仓库、读代码、管 issue 和 PR —— GitHub 官方托管 MCP server"
                       "(已从旧的社区版 stdio server 升级)。",
        "url": "https://api.githubcopilot.com/mcp/",
        "params": [{"key": "token", "required": True, "secret": True}],
        "needs_secret": True,
        "secret_hint": "GitHub personal access token — prefer fine-grained with minimal scopes. "
                       "· GitHub 个人访问令牌 —— 建议最小权限的 fine-grained token。",
        "credential_url": "https://github.com/settings/tokens",
        # server 侧原始工具名,发送/写出类(github/github-mcp-server 文档,2026-07 核对)。
        "outbound_tools": ["create_issue", "update_issue", "add_issue_comment",
                           "create_pull_request", "merge_pull_request", "update_pull_request",
                           "create_or_update_file", "push_files", "create_branch",
                           "create_repository", "fork_repository"],
        "risk_note": "Acts on GitHub with your token's permissions — prefer a fine-grained token "
                     "with minimal scopes. · 用你令牌的权限操作 GitHub —— 建议用最小权限的 fine-grained token。",
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "icon": "📧",
        "category": "app",
        "description": "Read and send email from your Gmail. · 读你的 Gmail、代你发邮件。",
        "disabled": True,
        "disabled_reason": "Needs Google OAuth sign-in — coming in a later release. There is no "
                           "token you can paste today, honestly. · 需要 Google OAuth 授权 —— "
                           "下个版本解锁;现在没有可贴的令牌,如实说。",
        "risk_note": "Would read your mailbox and send mail as you. · 会读你的邮箱、以你的身份发信。",
    },
    {
        "id": "gcalendar",
        "name": "Google Calendar",
        "icon": "📅",
        "category": "app",
        "description": "See your schedule and create events. · 看你的日程、代你建日历事件。",
        "disabled": True,
        "disabled_reason": "Needs Google OAuth sign-in — coming in a later release. There is no "
                           "token you can paste today, honestly. · 需要 Google OAuth 授权 —— "
                           "下个版本解锁;现在没有可贴的令牌,如实说。",
        "risk_note": "Would read and modify your calendar. · 会读并修改你的日历。",
    },
    {
        "id": "slack",
        "name": "Slack",
        "icon": "💬",
        "category": "app",
        "description": "Read channels and post messages in your Slack workspace. "
                       "· 读你 Slack 工作区的频道、代你发消息。",
        "disabled": True,
        "disabled_reason": "Needs Slack OAuth sign-in — coming in a later release. There is no "
                           "token you can paste today, honestly. · 需要 Slack OAuth 授权 —— "
                           "下个版本解锁;现在没有可贴的令牌,如实说。",
        "risk_note": "Would read channel history and post as you. · 会读频道历史、以你的身份发言。",
        # docs/96 刀0 对抗验收 LEAK③：Slack 发送类工具策展（占位卡也先备好 —— 刀2 OAuth 解锁
        # 那天接入即受「永不直发」闸，不等补名单）。名单 = 官方已归档 server
        # （@modelcontextprotocol/server-slack：slack_post_message/slack_reply_to_thread）
        # + korotovsky/slack-mcp-server（conversations_add_message）
        # + Slack Web API chat.* 直用名（post/postEphemeral/scheduleMessage/meMessage）。
        # 只列**发送**类；读类（get_channel_history/list_channels）绝不进策展。
        "outbound_tools": ["slack_post_message", "slack_reply_to_thread",
                           "conversations_add_message", "chat_postMessage",
                           "chat_postEphemeral", "chat_scheduleMessage", "chat_meMessage"],
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "icon": "📁",
        "description": "Let roles read & write files in one folder you pick — the classic first channel. "
                       "· 让角色在你指定的一个文件夹里读写文件 —— 最经典的第一路渠道。",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
        "env_template": {},
        "params": [{"key": "folder", "required": False, "secret": False, "default": _WS}],
        "needs_secret": False,
        "secret_hint": "",
        "risk_note": "File access is scoped to the chosen folder only. Defaults to your KarvyLoop "
                     "workspace — NOT your home folder. · 文件访问只限所选文件夹;默认 KarvyLoop 工作区,不是家目录。",
    },
    {
        "id": "fetch",
        "name": "Web Fetch",
        "icon": "🌐",
        "description": "Fetch a web page and convert it to markdown for the model to read. "
                       "· 抓一个网页并转成 markdown 给模型读。",
        "command": "uvx",
        "args_template": ["mcp-server-fetch"],
        "env_template": {},
        "params": [],
        "needs_secret": False,
        "secret_hint": "",
        "risk_note": "Can reach arbitrary URLs on the internet. · 能访问互联网上的任意网址。",
    },
    {
        "id": "memory",
        "name": "Memory (knowledge graph)",
        "icon": "🧠",
        "description": "A local knowledge-graph scratch memory the model can read & write across calls. "
                       "· 本地知识图谱便签记忆,模型跨调用可读写。",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-memory"],
        "env_template": {},
        "params": [],
        "needs_secret": False,
        "secret_hint": "",
        "risk_note": "Data stays local to this machine. · 数据只留在本机。",
    },
    {
        "id": "time",
        "name": "Time",
        "icon": "⏰",
        "description": "Current time and timezone conversions. · 当前时间与时区换算。",
        "command": "uvx",
        "args_template": ["mcp-server-time"],
        "env_template": {},
        "params": [],
        "needs_secret": False,
        "secret_hint": "",
        "risk_note": "Read-only. · 只读。",
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "icon": "🗄️",
        "description": "Query and update a local SQLite database. · 查询/更新一个本地 SQLite 数据库。",
        "command": "uvx",
        "args_template": ["mcp-server-sqlite", "--db-path", "{db_path}"],
        "env_template": {},
        "params": [{"key": "db_path", "required": False, "secret": False,
                    "default": _WS + "/karvyloop.sqlite"}],
        "needs_secret": False,
        "secret_hint": "",
        "risk_note": "Can modify the chosen database file. Defaults to a new file inside your "
                     "workspace. · 会修改所选数据库文件;默认在工作区里新建一个。",
    },
]]


def _by_id(preset_id: str) -> Optional[dict[str, Any]]:
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def _resolve_default(default: str, workspace: Optional[str]) -> str:
    """把参数默认值里的 `@workspace` 哨兵解析成真实工作区路径(没有工作区 → 空串)。"""
    if not default:
        return ""
    if default == _WS:
        return workspace or ""
    if default.startswith(_WS + "/"):
        return os.path.join(workspace, default[len(_WS) + 1:]) if workspace else ""
    return default


def list_presets(workspace: Optional[str] = None) -> list[dict[str, Any]]:
    """预设目录(公开视图)。给了 workspace 就把参数默认值解析出来(default_resolved),
    让前端能如实展示"默认圈到哪个文件夹"。目录里没有任何密钥,可安全整体返回。"""
    out: list[dict[str, Any]] = []
    for p in PRESETS:
        params = []
        for prm in p["params"]:
            q = dict(prm)
            q["default_resolved"] = _resolve_default(str(prm.get("default", "") or ""), workspace)
            params.append(q)
        out.append({**p, "params": params})
    return out


def build_server_config(preset_id: str, params: Optional[dict[str, str]] = None, *,
                        workspace: Optional[str] = None) -> dict[str, Any]:
    """把预设 + 用户参数拼成 config.yaml `mcp.servers` 的**真实消费形状**
    (read_mcp_server_configs 吃的形状,不发明):
    - stdio 预设(有 command)→ `{name, command, args, [env]}`;
    - remote 预设(有 url,docs/96 刀1)→ `{name, url, transport: http, [token]}`
      (复用 build_remote_server_config 的校验:https、token 不走明文 http)。

    disabled 预设(如需 OAuth 的 Gmail 占位)→ ValueError(fail-closed,文案诚实)。
    占位符(如 {folder}/{token})从 params 取,缺了用默认(@workspace → workspace);
    仍为空 → ValueError(信息只含参数名,**绝不含密钥值**)。
    """
    p = _by_id(preset_id)
    if p is None:
        raise ValueError(f"unknown preset: {preset_id}")
    if p.get("disabled"):
        raise ValueError(p.get("disabled_reason")
                         or f"preset '{preset_id}' is not available yet")
    supplied = dict(params or {})
    values: dict[str, str] = {}
    for prm in p["params"]:
        key = str(prm["key"])
        v = str(supplied.get(key, "") or "").strip()
        if not v:
            v = _resolve_default(str(prm.get("default", "") or ""), workspace)
        if not v:
            raise ValueError(f"preset '{preset_id}' missing parameter: {key}")
        values[key] = v

    # remote(streamable HTTP)预设:走 build_remote_server_config(校验/形状统一收口)
    # (docs/96 刀0:预设的 outbound_tools **不落 config entry** —— 接入时 mcp_manager 按
    # server 名回查 PRESETS 登记进 outbound_gate,老 config 也吃得到策展名单。)
    if p.get("url"):
        return build_remote_server_config(
            p["url"], name=p["id"], token=values.get("token", ""),
            auth=str(p.get("auth", "") or ""), scopes=p.get("scopes") or [])

    def _subst(s: str) -> str:
        out = s
        for k, v in values.items():
            out = out.replace("{" + k + "}", v)
        return out

    entry: dict[str, Any] = {
        "name": p["id"],
        "command": p["command"],
        "args": [_subst(a) for a in p["args_template"]],
    }
    env = {str(k): _subst(str(v)) for k, v in (p.get("env_template") or {}).items()}
    if env:
        entry["env"] = env
    return entry


def configured_names(config_path: str) -> set[str]:
    """config.yaml 里已配置的 MCP server 名集合(只读名字,不碰 env/密钥)。"""
    try:
        if not config_path:
            return set()
        pth = Path(config_path)
        if not pth.exists():
            return set()
        import yaml
        cfg = yaml.safe_load(pth.read_text(encoding="utf-8")) or {}
        return {str((s or {}).get("name", "")).strip()
                for s in ((cfg.get("mcp") or {}).get("servers") or [])
                if (s or {}).get("name")}
    except Exception:
        return set()


def _upsert_server(entry: dict[str, Any], config_path: str) -> tuple[bool, str]:
    """把一个 server 条目 upsert 进 config.yaml 的 `mcp.servers`(同名替换,不重复)。

    写法跟 gateway/config_models._save 同款(safe_load/safe_dump,保留其余键)。
    密钥只落盘,**不 log、不出现在返回值里**。返回 (ok, reason)。
    """
    import yaml
    pth = Path(config_path)
    cfg: dict[str, Any] = {}
    if pth.exists():
        try:
            cfg = yaml.safe_load(pth.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return False, f"config.yaml unreadable: {type(e).__name__}"
    mcp = cfg.get("mcp") or {}
    servers = [s for s in (mcp.get("servers") or [])
               if str((s or {}).get("name", "")).strip() != entry["name"]]
    servers.append(entry)
    mcp["servers"] = servers
    cfg["mcp"] = mcp
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True, ""


def apply_preset(preset_id: str, params: dict[str, str], config_path: str) -> tuple[bool, str]:
    """把一个预设 upsert 进 config.yaml 的 `mcp.servers`。返回 (ok, reason)。"""
    if not config_path:
        return False, "no config path"
    try:
        from karvyloop.config_workspace import resolve_workspace
        ws: Optional[str] = resolve_workspace(config_path, ensure=False)
    except Exception:
        ws = None
    try:
        entry = build_server_config(preset_id, params, workspace=ws)
    except ValueError as e:
        return False, str(e)
    return _upsert_server(entry, config_path)


# ---- remote MCP server(streamable HTTP):贴个 URL + 可选 token 就能加 ----------

_NAME_OK = "abcdefghijklmnopqrstuvwxyz0123456789_-"
# host 前缀里没信息量的 label(推导默认名时剥掉):mcp.notion.com → notion
_BORING_LABELS = ("mcp", "api", "www", "server", "remote")


def _derive_name(url: str) -> str:
    """从 URL 推一个默认 server 名(用户没起名时):取 host、剥无信息 label、拿第一段。"""
    import urllib.parse as _up
    host = (_up.urlsplit(url).hostname or "").lower()
    labels = [l for l in host.split(".") if l]
    while len(labels) > 1 and labels[0] in _BORING_LABELS:
        labels = labels[1:]
    return _sanitize_name(labels[0] if labels else "")


def _sanitize_name(name: str) -> str:
    s = "".join(ch if ch in _NAME_OK else "-" for ch in str(name or "").strip().lower())
    return s.strip("-_")[:64]


def build_remote_server_config(url: str, *, name: str = "",
                               token: str = "", auth: str = "",
                               scopes: Optional[list] = None) -> dict[str, Any]:
    """贴 URL + 可选 bearer token / OAuth → config.yaml `mcp.servers` 的 remote 形状
    `{name, url, transport: "http", [token] | [auth: oauth, scopes]}`
    (read_mcp_server_configs 真实消费;token 落盘后转成 Authorization: Bearer header,
    auth=oauth 走 docs/96 刀2 的 OAuth 客户端,token 落 0600 文件不进 config)。

    校验(错误信息只含参数名/URL 的 host,**绝不含 token 值**):
    - url 必须 http(s)://…;OAuth 端点必须 https(授权码/token 不许裸奔明文);
    - **token 不许走明文 http**(凭证裸奔),localhost 回环除外(本地调试);
    - name 允许 [a-z0-9_-],没给就从 host 推导(mcp.notion.com → notion)。
    """
    import urllib.parse as _up
    u = str(url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    parts = _up.urlsplit(u)
    if not parts.hostname:
        raise ValueError("url has no host")
    tok = str(token or "").strip()
    is_loopback = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if tok and parts.scheme == "http" and not is_loopback:
        raise ValueError("refusing to send a token over plain http — use https")
    is_oauth = str(auth or "").strip().lower() == "oauth"
    if is_oauth and parts.scheme != "https" and not is_loopback:
        raise ValueError("refusing to run OAuth over plain http — use https")
    nm = _sanitize_name(name) or _derive_name(u)
    if not nm:
        raise ValueError("could not derive a server name — pass one explicitly")
    entry: dict[str, Any] = {"name": nm, "url": u, "transport": "http"}
    if is_oauth:
        entry["auth"] = "oauth"
        sc = [str(x).strip() for x in (scopes or []) if str(x).strip()]
        if sc:
            entry["scopes"] = sc
    elif tok:
        entry["token"] = tok
    return entry


def add_remote_server(url: str, name: str, token: str, config_path: str) -> tuple[bool, str, str]:
    """贴 URL 加 remote MCP server:校验 → upsert config.yaml。返回 (ok, reason, name)。
    token 只落 config.yaml(密钥之家,仓外);**不 log、不进返回值**。"""
    if not config_path:
        return False, "no config path", ""
    try:
        entry = build_remote_server_config(url, name=name, token=token)
    except ValueError as e:
        return False, str(e), ""
    ok, reason = _upsert_server(entry, config_path)
    return ok, reason, (entry["name"] if ok else "")


def configured_remote_servers(config_path: str) -> list[dict[str, Any]]:
    """config.yaml 里已配置的 remote(http)server —— 只回 name + 去 query 的 url +
    有没有配凭证(bool),**绝不回 token/headers 的值**(展示用)。"""
    out: list[dict[str, Any]] = []
    try:
        if not config_path:
            return []
        pth = Path(config_path)
        if not pth.exists():
            return []
        import yaml
        cfg = yaml.safe_load(pth.read_text(encoding="utf-8")) or {}
        for s in ((cfg.get("mcp") or {}).get("servers") or []):
            if not isinstance(s, dict):
                continue
            url = str(s.get("url", "") or "").strip()
            name = str(s.get("name", "") or "").strip()
            if not url or not name:
                continue
            out.append({"name": name,
                        "url": url.split("?", 1)[0].split("#", 1)[0],
                        "has_token": bool(s.get("token") or s.get("headers"))})
    except Exception:
        return []
    return out


__all__ = ["PRESETS", "list_presets", "build_server_config", "configured_names",
           "apply_preset", "build_remote_server_config", "add_remote_server",
           "configured_remote_servers"]
