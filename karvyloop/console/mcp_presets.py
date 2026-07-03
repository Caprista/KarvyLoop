"""mcp_presets — console 的「渠道预设」目录(#42 优化:拧开就有水)。

痛点:agent 够不着你的生活 —— 想接文件/网页/GitHub 得自己去 config.yaml 手写
`mcp.servers`(name/command/args/env),门槛劝退。这里把**知名、uvx/npx 一条命令就能跑**
的 MCP server 做成预设目录,console 里一键写进 config.yaml。

事实对齐(别发明形状):
- 消费方是 `karvyloop/coding/tools/mcp_tool.py:read_mcp_server_configs` —— 它读
  config.yaml 的 `mcp.servers: [{name, command, args, env}]`,build_server_config
  产出的就是这个形状(不多不少)。
- MCP server 只在 console 启动时连(console/app.py lifespan → connect_mcp_agent_tools
  → runtime_kwargs["mcp_tools"]),**没有热加载** → apply 后如实返回 requires_restart=True。
- 密钥(如 GitHub token)落 config.yaml —— 它本来就是密钥之家(仓外);本模块**绝不
  log/print 密钥**,API 响应绝不回显 params。

安全默认:
- filesystem 预设默认圈定 **KarvyLoop 工作区**(config_workspace.resolve_workspace),
  **不是家目录** —— 用户可自选文件夹,但默认不把整台机器递出去。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# 参数默认值里的哨兵:解析成用户工作区(resolve_workspace;绝不默认家目录)
_WS = "@workspace"

# 预设目录:知名 MCP server,uvx/npx 可直接跑,无需额外安装步骤。
# description / risk_note 双语一条(en · zh),前端原样展示。
PRESETS: list[dict[str, Any]] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
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
        "id": "github",
        "name": "GitHub",
        "description": "Search repos, read files, manage issues & PRs on GitHub. "
                       "· 在 GitHub 上搜仓库、读文件、管 issue 和 PR。",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-github"],
        "env_template": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{token}"},
        "params": [{"key": "token", "required": True, "secret": True}],
        "needs_secret": True,
        "secret_hint": "GitHub personal access token — github.com/settings/tokens · GitHub 个人访问令牌",
        "risk_note": "Acts on GitHub with your token's permissions — prefer a fine-grained token "
                     "with minimal scopes. · 用你令牌的权限操作 GitHub —— 建议用最小权限的 fine-grained token。",
    },
    {
        "id": "memory",
        "name": "Memory (knowledge graph)",
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
]


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
    """把预设 + 用户参数拼成 config.yaml `mcp.servers` 的**真实消费形状**:
    `{name, command, args, [env]}`(read_mcp_server_configs 吃的就是这个,不发明新形状)。

    占位符(如 {folder}/{token})从 params 取,缺了用默认(@workspace → workspace);
    仍为空 → ValueError(信息只含参数名,**绝不含密钥值**)。
    """
    p = _by_id(preset_id)
    if p is None:
        raise ValueError(f"unknown preset: {preset_id}")
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


def apply_preset(preset_id: str, params: dict[str, str], config_path: str) -> tuple[bool, str]:
    """把一个预设 upsert 进 config.yaml 的 `mcp.servers`(同名替换,不重复)。

    写法跟 gateway/config_models._save 同款(safe_load/safe_dump,保留其余键)。
    密钥只落盘,**不 log、不出现在返回值里**。返回 (ok, reason)。
    """
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


__all__ = ["PRESETS", "list_presets", "build_server_config", "configured_names", "apply_preset"]
