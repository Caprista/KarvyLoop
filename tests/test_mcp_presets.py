"""MCP 渠道预设(#42 优化:拧开就有水 / docs/96 刀1:生活应用策展)—— 目录有效性 /
真实消费形状 / apply 端点 / 前端接线。

关键不变量:
- build_server_config 产出的形状必须是 read_mcp_server_configs **真实消费**的形状
  (stdio `{name, command, args, env}` / remote `{name, url, transport, token}`),
  不发明新形状 → 用真读取函数验证。
- 密钥只落 config.yaml,API 响应**绝不回显**(fixture key 带 FAKE/DO-NOT-LEAK 字样)。
- 诚实(docs/96 刀1):有 manager(lifespan)→ apply 返回真结果;无 manager(本文件的
  TestClient 不跑 lifespan)→ 必须退回 requires_restart=True,不假装已生效。
  真热加载路径在 tests/test_mcp_hotload.py(真 stdio 桩 server)。
- disabled 占位预设(Gmail/GCal/Slack 需 OAuth)fail-closed,文案诚实。
"""
from __future__ import annotations

import re

import pytest
import yaml

from karvyloop.console.mcp_presets import (
    PRESETS, apply_preset, build_server_config, configured_names, list_presets,
)

FAKE_TOKEN = "ghp_FAKE-DO-NOT-LEAK-0123456789abcdef"

# 目录三形态(docs/96 刀1):stdio(command)/ remote(url)/ disabled 占位(都没有)
# computer_use(docs/99 刀1):stdio(npx 拉起上游 computer-use MCP server)。
STDIO_IDS = {"filesystem", "fetch", "memory", "time", "sqlite", "computer_use"}
REMOTE_IDS = {"notion", "github"}
DISABLED_IDS = {"gmail", "gcalendar", "slack"}


# ---------- 目录有效性 ----------

class TestCatalog:
    def test_ids_unique(self):
        ids = [p["id"] for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_wellknown_presets_present(self):
        ids = {p["id"] for p in PRESETS}
        assert (STDIO_IDS | REMOTE_IDS | DISABLED_IDS) <= ids

    def test_required_fields(self):
        for p in PRESETS:
            for field in ("id", "name", "description", "command", "args_template",
                          "env_template", "params", "needs_secret", "secret_hint", "risk_note",
                          # docs/96 刀1 扩展字段(向后兼容加,全预设齐)
                          "icon", "category", "credential_url", "outbound_tools",
                          "disabled", "disabled_reason", "url"):
                assert field in p, f"{p.get('id')} 缺字段 {field}"
            assert isinstance(p["needs_secret"], bool)
            assert isinstance(p["disabled"], bool)
            assert isinstance(p["outbound_tools"], list)
            assert p["category"] in ("app", "channel")
            if p["id"] in STDIO_IDS:
                assert p["command"] in ("npx", "uvx")   # 只收"一条命令就能跑"的
                assert not p["url"]
            elif p["id"] in REMOTE_IDS:
                assert p["url"].startswith("https://")  # remote 一律 https
                assert not p["command"]
            elif p["id"] in DISABLED_IDS:
                assert p["disabled"] is True
                assert p["disabled_reason"].strip()     # 占位必须有诚实文案
                assert "OAuth" in p["disabled_reason"]
                assert not p["command"] and not p["url"]

    def test_needs_secret_coverage(self):
        """needs_secret=True ⟺ 有 secret 参数 + 有 secret_hint(前端要能提示去哪拿 key)。"""
        for p in PRESETS:
            has_secret_param = any(prm.get("secret") for prm in p["params"])
            assert p["needs_secret"] == has_secret_param, p["id"]
            if p["needs_secret"]:
                assert p["secret_hint"].strip(), p["id"]
        for pid in ("github", "notion"):
            p = next(x for x in PRESETS if x["id"] == pid)
            assert p["needs_secret"] is True
            assert p["credential_url"].startswith("https://")   # 凭证怎么拿的指路链接

    def test_outbound_tools_marked_for_write_capable_apps(self):
        """刀0 消费:发送/写出类工具名显式标注(server 侧原始名,不带 mcp_ 前缀)。"""
        notion = next(p for p in PRESETS if p["id"] == "notion")
        assert "notion-create-pages" in notion["outbound_tools"]
        gh = next(p for p in PRESETS if p["id"] == "github")
        assert {"create_issue", "create_pull_request"} <= set(gh["outbound_tools"])
        for p in PRESETS:
            for t in p["outbound_tools"]:
                assert not t.startswith("mcp_"), f"{p['id']}: outbound_tools 应是 server 侧原始名"

    def test_placeholders_resolve(self):
        """模板里的每个 {placeholder} 都必须有对应声明的参数(否则永远填不上)。"""
        for p in PRESETS:
            declared = {prm["key"] for prm in p["params"]}
            blobs = list(p["args_template"]) + list(p["env_template"].values())
            for blob in blobs:
                for ph in re.findall(r"\{(\w+)\}", blob):
                    assert ph in declared, f"{p['id']} 模板占位符 {{{ph}}} 没有声明参数"
            if p["id"] in REMOTE_IDS:   # remote 预设的凭证走 token 参数(→ Bearer)
                assert "token" in declared, p["id"]

    def test_list_presets_resolves_workspace_default(self, tmp_path):
        ws = str(tmp_path / "work")
        cat = {p["id"]: p for p in list_presets(ws)}
        folder = cat["filesystem"]["params"][0]
        assert folder["default_resolved"] == ws          # 默认=工作区,不是家目录
        db = cat["sqlite"]["params"][0]
        assert db["default_resolved"].startswith(ws)


# ---------- build_server_config:真实消费形状 ----------

class TestBuildServerConfig:
    def test_shape_is_what_reader_consumes(self, tmp_path):
        """终极验证:写进 config.yaml → 用**真读取函数** read_mcp_server_configs 读回来。"""
        from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
        entry = build_server_config("filesystem", {}, workspace=str(tmp_path))
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump({"mcp": {"servers": [entry]}}, allow_unicode=True),
                            encoding="utf-8")
        got = read_mcp_server_configs(str(cfg_path))
        assert len(got) == 1
        assert got[0].name == "filesystem"
        assert got[0].command == entry["command"]
        assert got[0].args == entry["args"]

    def test_filesystem_defaults_to_workspace_not_home(self, tmp_path):
        from pathlib import Path
        ws = str(tmp_path / "myws")
        entry = build_server_config("filesystem", {}, workspace=ws)
        assert entry["args"][-1] == ws
        assert entry["args"][-1] != str(Path.home())

    def test_filesystem_explicit_folder_wins(self, tmp_path):
        folder = str(tmp_path / "docs")
        entry = build_server_config("filesystem", {"folder": folder}, workspace=str(tmp_path))
        assert entry["args"][-1] == folder

    def test_github_is_remote_with_bearer_token(self, tmp_path):
        """docs/96 刀1:github 预设升级为官方 remote server —— `{name,url,transport,token}`,
        读回后 token → Authorization: Bearer(真消费函数验证)。"""
        from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
        entry = build_server_config("github", {"token": FAKE_TOKEN})
        assert entry == {"name": "github", "url": "https://api.githubcopilot.com/mcp/",
                         "transport": "http", "token": FAKE_TOKEN}
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump({"mcp": {"servers": [entry]}}, allow_unicode=True),
                            encoding="utf-8")
        (got,) = read_mcp_server_configs(str(cfg_path))
        assert got.transport_kind == "http"
        assert got.headers == {"Authorization": f"Bearer {FAKE_TOKEN}"}

    def test_notion_is_remote_hosted(self):
        entry = build_server_config("notion", {"token": "ntn_FAKE-DO-NOT-LEAK"})
        assert entry == {"name": "notion", "url": "https://mcp.notion.com/mcp",
                         "transport": "http", "token": "ntn_FAKE-DO-NOT-LEAK"}

    def test_github_without_token_refused(self):
        with pytest.raises(ValueError) as ei:
            build_server_config("github", {})
        assert "token" in str(ei.value)
        assert FAKE_TOKEN not in str(ei.value)   # 错误信息只含参数名,绝不含密钥值

    def test_disabled_placeholder_fails_closed_with_honest_reason(self):
        """Gmail/GCal/Slack 占位(需 OAuth):build/apply 都 fail-closed,别让人贴 token 撞墙。"""
        for pid in ("gmail", "gcalendar", "slack"):
            with pytest.raises(ValueError) as ei:
                build_server_config(pid, {"token": FAKE_TOKEN})
            assert "OAuth" in str(ei.value)
            assert FAKE_TOKEN not in str(ei.value)

    def test_no_env_key_when_empty(self):
        entry = build_server_config("fetch", {})
        assert "env" not in entry                # 空 env 不写(保持 config.yaml 干净)

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            build_server_config("nope", {})


# ---------- apply 端点(TestClient,tmp config)----------

@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("lang: en\n", encoding="utf-8")   # 已有键要保留
    app.state.config_path = str(cfg)
    return TestClient(app), cfg


class TestApplyEndpoint:
    def test_apply_writes_config_and_never_echoes_secret(self, client):
        c, cfg = client
        r = c.post("/api/mcp/preset/apply",
                   json={"preset_id": "github", "params": {"token": FAKE_TOKEN}})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # 本 fixture 不跑 lifespan → 无 mcp_manager → 如实退回"要重启"(不假装热加载了);
        # 真热加载路径(装上即用)在 tests/test_mcp_hotload.py 用真桩 server 验。
        assert body["requires_restart"] is True
        assert FAKE_TOKEN not in r.text              # 响应绝不回显密钥
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        servers = data["mcp"]["servers"]
        assert len(servers) == 1 and servers[0]["name"] == "github"
        # docs/96 刀1:github 预设已升级为官方 remote server(token → Bearer,读回时展开)
        assert servers[0]["url"] == "https://api.githubcopilot.com/mcp/"
        assert servers[0]["token"] == FAKE_TOKEN
        assert data["lang"] == "en"                  # 其余配置键原样保留

    def test_apply_disabled_placeholder_fails_closed(self, client):
        """Gmail 占位卡(需 OAuth)从端点 apply → fail-closed + 诚实 reason,config 不落条目。"""
        c, cfg = client
        body = c.post("/api/mcp/preset/apply",
                      json={"preset_id": "gmail", "params": {}}).json()
        assert body["ok"] is False and "OAuth" in body["reason"]
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        assert not (data.get("mcp") or {}).get("servers")

    def test_apply_upsert_no_duplicates(self, client):
        c, cfg = client
        for _ in range(2):
            assert c.post("/api/mcp/preset/apply",
                          json={"preset_id": "fetch", "params": {}}).json()["ok"]
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert [s["name"] for s in data["mcp"]["servers"]] == ["fetch"]

    def test_apply_unknown_preset_fails_closed(self, client):
        c, _ = client
        body = c.post("/api/mcp/preset/apply", json={"preset_id": "nope", "params": {}}).json()
        assert body["ok"] is False and "nope" in body["reason"]

    def test_apply_missing_required_param_fails(self, client):
        c, cfg = client
        body = c.post("/api/mcp/preset/apply", json={"preset_id": "github", "params": {}}).json()
        assert body["ok"] is False and "token" in body["reason"]
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        assert not (data.get("mcp") or {}).get("servers")   # 失败不落半个条目

    def test_presets_marked_configured(self, client):
        c, _ = client
        c.post("/api/mcp/preset/apply", json={"preset_id": "memory", "params": {}})
        r = c.get("/api/mcp/presets").json()
        assert r["requires_restart"] is True
        by_id = {p["id"]: p for p in r["presets"]}
        assert by_id["memory"]["configured"] is True
        assert by_id["fetch"]["configured"] is False
        assert FAKE_TOKEN not in str(r)              # 目录响应里没有任何密钥

    def test_configured_names_reads_existing(self, client):
        c, cfg = client
        c.post("/api/mcp/preset/apply", json={"preset_id": "time", "params": {}})
        assert configured_names(str(cfg)) == {"time"}
        assert configured_names("") == set()

    def test_apply_preset_no_config_path(self):
        ok, reason = apply_preset("fetch", {}, "")
        assert ok is False and reason


# ---------- docs/99 安全体验:机器控制知情授权端点(控制台把 CLI 的 request_consent 搬进产品)----

class TestComputerConsentApi:
    def _clear(self):
        from karvyloop.capability.computer_gate import clear_computer_gate
        clear_computer_gate()

    def test_status_returns_notice_and_default_off(self, client):
        self._clear()
        c, _ = client
        r = c.get("/api/computer/consent").json()
        assert r["enabled"] is False              # 默认关(顺序铁律)
        assert "屏" in r["notice"]                # 知情文案点明"看你的屏"

    def test_enable_without_csrf_rejected_stays_off(self, client):
        from karvyloop.capability.computer_gate import is_computer_control_enabled
        self._clear()
        c, _ = client
        d = c.post("/api/computer/consent", json={"enable": True}).json()
        assert d["ok"] is False and ("授权标记" in d["reason"] or "CSRF" in d["reason"])
        assert is_computer_control_enabled() is False     # 没开

    def test_enable_untrusted_origin_rejected(self, client):
        from karvyloop.capability.computer_gate import is_computer_control_enabled
        self._clear()
        c, _ = client   # TestClient host="testclient"(非 IP)→ 不可信来源
        d = c.post("/api/computer/consent", json={"enable": True},
                   headers={"x-karvyloop-upgrade": "1"}).json()
        assert d["ok"] is False and "局域网" in d["reason"]
        assert is_computer_control_enabled() is False

    def test_enable_disable_with_csrf_and_trusted_origin(self, client, monkeypatch):
        from karvyloop.capability.computer_gate import (
            clear_computer_gate, is_computer_control_enabled)
        import karvyloop.console.routes_ops as ro
        clear_computer_gate()
        monkeypatch.setattr(ro, "_is_trusted_upgrade_origin", lambda h: True)
        c, _ = client
        d = c.post("/api/computer/consent", json={"enable": True},
                   headers={"x-karvyloop-upgrade": "1"}).json()
        assert d["ok"] is True and d["enabled"] is True
        assert is_computer_control_enabled() is True
        d2 = c.post("/api/computer/consent", json={"enable": False},
                    headers={"x-karvyloop-upgrade": "1"}).json()
        assert d2["ok"] is True and d2["enabled"] is False
        assert is_computer_control_enabled() is False
        clear_computer_gate()


# ---------- 前端接线(编译源即契约)----------

class TestFrontendWiring:
    def _read(self, rel):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        return (root / rel).read_text(encoding="utf-8")

    def test_skills_panel_calls_preset_api(self):
        src = self._read("karvyloop/console/frontend/src/skills_panel.ts")
        assert "/api/mcp/presets" in src
        assert "/api/mcp/preset/apply" in src
        assert "mcpp.restart_note" in src            # 无热加载时诚实的"要重启"提示仍在(fallback)

    def test_computer_consent_toggle_wired(self):
        """安全体验:机器控制知情授权开关真接上 —— 调 /api/computer/consent、带 CSRF 头、用 cuc.* 文案。"""
        src = self._read("karvyloop/console/frontend/src/skills_panel.ts")
        assert "/api/computer/consent" in src
        assert "x-karvyloop-upgrade" in src          # 高信任动作带 CSRF 头
        assert "cuc.title" in src and "cuc.enable" in src
        i18n = self._read("karvyloop/console/frontend/src/i18n.ts")
        for key in ("cuc.title", "cuc.notice", "cuc.enable", "cuc.disable", "cuc.on", "cuc.off"):
            assert i18n.count(f'"{key}"') == 2, f"{key} 应在 en+zh 两表各一次"

    def test_skills_panel_hotload_wiring(self):
        """docs/96 刀1:手动重连端点 + 装上即用/连失败真结果话术 + disabled 占位卡 +
        凭证指路链接 + 状态灯,前端真接上。"""
        src = self._read("karvyloop/console/frontend/src/skills_panel.ts")
        assert "/api/mcp/reconnect" in src           # 手动"重连 server"按钮
        for needle in ("mcpp.live_note", "mcpp.conn_failed", "mcpp.st_connected",
                       "mcpp.st_failed", "mcpp.st_oauth", "mcpp.apps_title",
                       "mcp-card-disabled", "credential_url", "disabled_reason"):
            assert needle in src, f"skills_panel.ts 缺 {needle}"

    def test_unlock_panel_apps_chips(self):
        src = self._read("karvyloop/console/frontend/src/unlock_panel.ts")
        assert "/api/mcp/presets" in src             # 解锁面板 MCP 卡带应用状态小灯
        assert "unlock.mcp.apps_label" in src

    def test_i18n_keys_in_both_tables(self):
        src = self._read("karvyloop/console/frontend/src/i18n.ts")
        for key in ("mcpp.title", "mcpp.connect", "mcpp.connected", "mcpp.needs_secret",
                    "mcpp.restart_note", "mcpp.param_default_ph",
                    # docs/96 刀1 新增
                    "mcpp.apps_title", "mcpp.apps_hint", "mcpp.live_note", "mcpp.conn_failed",
                    "mcpp.reconnect", "mcpp.reconnecting", "mcpp.reconnect_done",
                    "mcpp.reconnect_partial", "mcpp.st_connected", "mcpp.st_not_connected",
                    "mcpp.st_failed", "mcpp.st_oauth", "mcpp.st_saved",
                    "mcpp.get_credential", "mcpp.perm_label", "unlock.mcp.apps_label"):
            assert src.count(f'"{key}"') == 2, f"{key} 应在 en+zh 两表各出现一次"
