"""MCP 渠道预设(#42 优化:拧开就有水)—— 目录有效性 / 真实消费形状 / apply 端点 / 前端接线。

关键不变量:
- build_server_config 产出的形状必须是 read_mcp_server_configs **真实消费**的形状
  (config.yaml `mcp.servers: [{name, command, args, env}]`),不发明新形状 → 用真读取函数验证。
- 密钥只落 config.yaml,API 响应**绝不回显**(fixture key 带 FAKE/DO-NOT-LEAK 字样)。
- 诚实:MCP 只在启动时连 → apply 必须返回 requires_restart=True。
"""
from __future__ import annotations

import re

import pytest
import yaml

from karvyloop.console.mcp_presets import (
    PRESETS, apply_preset, build_server_config, configured_names, list_presets,
)

FAKE_TOKEN = "ghp_FAKE-DO-NOT-LEAK-0123456789abcdef"


# ---------- 目录有效性 ----------

class TestCatalog:
    def test_ids_unique(self):
        ids = [p["id"] for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_wellknown_presets_present(self):
        ids = {p["id"] for p in PRESETS}
        assert {"filesystem", "fetch", "github", "memory", "time", "sqlite"} <= ids

    def test_required_fields(self):
        for p in PRESETS:
            for field in ("id", "name", "description", "command", "args_template",
                          "env_template", "params", "needs_secret", "secret_hint", "risk_note"):
                assert field in p, f"{p.get('id')} 缺字段 {field}"
            assert p["command"] in ("npx", "uvx")   # 只收"一条命令就能跑"的
            assert isinstance(p["needs_secret"], bool)

    def test_needs_secret_coverage(self):
        """needs_secret=True ⟺ 有 secret 参数 + 有 secret_hint(前端要能提示去哪拿 key)。"""
        for p in PRESETS:
            has_secret_param = any(prm.get("secret") for prm in p["params"])
            assert p["needs_secret"] == has_secret_param, p["id"]
            if p["needs_secret"]:
                assert p["secret_hint"].strip(), p["id"]
        gh = next(p for p in PRESETS if p["id"] == "github")
        assert gh["needs_secret"] is True

    def test_placeholders_resolve(self):
        """模板里的每个 {placeholder} 都必须有对应声明的参数(否则永远填不上)。"""
        for p in PRESETS:
            declared = {prm["key"] for prm in p["params"]}
            blobs = list(p["args_template"]) + list(p["env_template"].values())
            for blob in blobs:
                for ph in re.findall(r"\{(\w+)\}", blob):
                    assert ph in declared, f"{p['id']} 模板占位符 {{{ph}}} 没有声明参数"

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

    def test_github_token_lands_in_env(self):
        entry = build_server_config("github", {"token": FAKE_TOKEN})
        assert entry["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": FAKE_TOKEN}

    def test_github_without_token_refused(self):
        with pytest.raises(ValueError) as ei:
            build_server_config("github", {})
        assert "token" in str(ei.value)
        assert FAKE_TOKEN not in str(ei.value)   # 错误信息只含参数名,绝不含密钥值

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
        assert body["requires_restart"] is True      # 诚实:启动时才连,无热加载
        assert FAKE_TOKEN not in r.text              # 响应绝不回显密钥
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        servers = data["mcp"]["servers"]
        assert len(servers) == 1 and servers[0]["name"] == "github"
        assert servers[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == FAKE_TOKEN
        assert data["lang"] == "en"                  # 其余配置键原样保留

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
        assert "mcpp.restart_note" in src            # 诚实的"要重启"提示真被用上

    def test_i18n_keys_in_both_tables(self):
        src = self._read("karvyloop/console/frontend/src/i18n.ts")
        for key in ("mcpp.title", "mcpp.connect", "mcpp.connected", "mcpp.needs_secret",
                    "mcpp.restart_note", "mcpp.param_default_ph"):
            assert src.count(f'"{key}"') == 2, f"{key} 应在 en+zh 两表各出现一次"
