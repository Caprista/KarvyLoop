"""test_capability_unlocks — 「能力解锁」清单(Hardy 2026-07-04:降级功能给引导和选择)。

病根:可选能力(MCP/附件解析/推送渠道/中继…)优雅降级做得越好,用户越不知道
"还有这回事、去哪配"。不变量:
① /api/capability/unlocks 确定性给全(id/status/install/detail),状态只有 on/off/missing_dep;
② 探测如实 —— 依赖缺 = missing_dep,依赖在没配 = off,配了 = on;
③ **绝不泄密**:config.yaml 里的 password/token 值绝不出现在响应里(detail 只有个数/包名);
④ 前端真接线(不是 backend self-hype):面板 + MCP 生态目录外链(官方 registry + 目录站)+
   i18n en/zh 齐 + 旅程收官 next-steps + 就近引导(files 缺依赖处)。
"""
from __future__ import annotations

import json
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.unlocks import list_unlocks  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402

EXPECTED_IDS = {"mcp", "files", "asr", "ocr", "webhook_channel", "email_channel", "relay", "web_verify"}
VALID_STATUS = {"on", "off", "missing_dep"}


def _by_id(items):
    return {u["id"]: u for u in items}


def test_unlocks_api_shape():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.get("/api/capability/unlocks")
    assert r.status_code == 200
    body = r.json()
    items = body.get("unlocks")
    assert isinstance(items, list)
    got = _by_id(items)
    assert set(got) == EXPECTED_IDS
    for u in items:
        assert u["status"] in VALID_STATUS, f"{u['id']} 状态非法:{u['status']}"
        assert "install" in u and "detail" in u


def test_all_deps_missing_reported_honestly():
    """依赖全缺 → 依赖型能力全 missing_dep + 每项都带安装命令;渠道型仍是 off(纯配置)。"""
    got = _by_id(list_unlocks("", has_dep=lambda m: False))
    for cid in ("mcp", "files", "asr", "relay", "web_verify"):
        assert got[cid]["status"] == "missing_dep", cid
        assert "pip install" in got[cid]["install"], cid
    assert got["files"]["detail"]["missing"] == ["pypdf", "python-docx", "openpyxl"]
    assert got["email_channel"]["status"] == "off"
    assert got["webhook_channel"]["status"] == "off"


def test_deps_present_but_nothing_configured():
    """依赖全在、零配置 → mcp 是 off(包在没 server),纯依赖型是 on,渠道型 off。"""
    got = _by_id(list_unlocks("", has_dep=lambda m: True))
    assert got["mcp"]["status"] == "off"
    assert got["mcp"]["detail"]["servers"] == 0
    for cid in ("files", "asr", "relay", "web_verify"):
        assert got[cid]["status"] == "on", cid
    assert got["email_channel"]["status"] == "off"
    assert got["webhook_channel"]["status"] == "off"


def test_configured_everything_on_and_no_secret_leak(tmp_path):
    """config.yaml 配齐 → 全 on;**种进去的密钥值绝不出现在清单里**(泄密红线)。"""
    secret_pw, secret_token = "SUPER-SECRET-PW-FAKE-DO-NOT-LEAK", "tok-FAKE-DO-NOT-LEAK"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp:\n"
        "  servers:\n"
        "    - {name: fetch, command: uvx, args: [mcp-server-fetch]}\n"
        f"    - {{name: notion, url: https://mcp.notion.example/mcp, transport: http, token: {secret_token}}}\n"
        "channels:\n"
        "  email:\n"
        "    enabled: true\n"
        f"    smtp: {{host: smtp.example.com, port: 465, user: me@example.com, password: {secret_pw}}}\n"
        "    to: me@example.com\n"
        "  webhook:\n"
        "    enabled: true\n"
        "    url: https://ntfy.sh/private-topic-FAKE\n"
        "    preset: ntfy\n",
        encoding="utf-8")
    items = list_unlocks(str(cfg), has_dep=lambda m: True)
    got = _by_id(items)
    assert got["mcp"]["status"] == "on"
    assert got["mcp"]["detail"]["servers"] == 2
    assert got["email_channel"]["status"] == "on"
    assert got["webhook_channel"]["status"] == "on"
    dump = json.dumps(items)
    assert secret_pw not in dump, "邮箱密码泄进解锁清单"
    assert secret_token not in dump, "MCP token 泄进解锁清单"
    assert "ntfy.sh/private-topic-FAKE" not in dump, "webhook URL(可能内嵌 token)泄进解锁清单"


def test_broken_config_degrades_to_off_not_crash(tmp_path):
    """坏 YAML → 探测降级为"未配置",绝不 raise(清单是引导面,不许因配置坏而消失)。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp: [unclosed", encoding="utf-8")
    got = _by_id(list_unlocks(str(cfg), has_dep=lambda m: True))
    assert got["mcp"]["status"] == "off"
    assert got["email_channel"]["status"] == "off"


def test_frontend_wired():
    """前端真接线(不是 backend self-hype):面板源/构建产物/i18n en+zh/入口三处。"""
    fe = ROOT / "karvyloop" / "console" / "frontend" / "src"
    static = ROOT / "karvyloop" / "console" / "static"
    src = (fe / "unlock_panel.ts").read_text(encoding="utf-8")
    assert "/api/capability/unlocks" in src
    # MCP 生态目录外链:官方 registry + 主流目录站(只进文案,渲染成 <a>)
    for link in ("https://registry.modelcontextprotocol.io/",
                 "https://www.pulsemcp.com/servers",
                 "https://glama.ai/mcp/servers"):
        assert link in src, f"MCP 目录链接缺失:{link}"
    built = (static / "unlock_panel.js").read_text(encoding="utf-8")
    assert "/api/capability/unlocks" in built, "构建产物没带解锁面板(没 npm run build?)"
    assert "registry.modelcontextprotocol.io" in built
    # index.html 装了面板脚本
    html = (static / "index.html").read_text(encoding="utf-8")
    assert "unlock_panel.js" in html
    # i18n en+zh 各一份(parity 由 i18n.ts 编译断言锁,这里验构建产物)
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    for k in ("unlock.name", "unlock.mcp.value", "unlock.status_missing_dep",
              "unlock.open_from_here", "journey.unlock_btn", "journey.unlock_moment"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"
    # 三个引导入口:技能面板卡 + 旅程收官 next-steps + files 缺依赖就近引导 + 诊断面
    assert "_renderUnlockCard" in (fe / "skills_panel.ts").read_text(encoding="utf-8")
    assert "openCoding" in (fe / "skills_panel.ts").read_text(encoding="utf-8")
    app_js = (static / "app.js").read_text(encoding="utf-8")
    assert "journey.unlock_btn" in app_js and "KarvyUnlockPanel" in app_js
    assert "KarvyUnlockPanel" in (fe / "files_panel.ts").read_text(encoding="utf-8")
    assert "KarvyUnlockPanel" in (fe / "diagnose_panel.ts").read_text(encoding="utf-8")
