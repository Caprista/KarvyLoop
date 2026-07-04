"""unlocks — 「能力解锁」清单(console):不配置就降级的可选能力,一张表看全。

痛点(Hardy 2026-07-04):可选能力(MCP 工具 / 附件解析 / 推送渠道 / 多设备中继…)
不配置就**静默降级** —— 优雅降级做得越好,用户越不知道"还有这回事、去哪配"。
"你不要以为用户配了会更好用,很多情况下用户真的不知道怎么配置,或者你不引导,
他们就真的不知道有这个配置。" 这里给每项能力一个**确定性探测**(零 LLM、绝不 raise):
已就绪(on)/ 未配置(off)/ 缺依赖(missing_dep),前端拿去渲染成解锁清单
(价值一句话 + 怎么做 + 生态链接;文案全在前端 i18n en+zh,本模块只给可判定事实)。

业界模式对齐(2026-07 雷达):Claude Desktop 的 connectors 目录、SaaS setup-checklist /
集成市场 —— 每行一个明确动作,不做瀑布式向导;空态/降级处就近给"去解锁"的路。

诚实红线:
- detail 只带非机密事实(server 个数 / 缺哪个包名),**绝不读、绝不回显任何密钥值**
  (config.yaml 是密钥之家 —— 这里只判断"块在不在/enabled 没有",复用 config_channels
  的解析器,机密字段 repr=False 纪律不变)。
- 状态语义如实:redis A2A transport 目前没接 console 运行时(库级 tier),**不列**
  ——列了用户配完也没变化,等于假解锁。语音输入是浏览器能力,由前端就地探测。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable, Optional

STATUS_ON = "on"                    # 已就绪/已配置
STATUS_OFF = "off"                  # 依赖齐但未配置(去配就通)
STATUS_MISSING_DEP = "missing_dep"  # 缺可选依赖(pip install 一条命令)

# files extra 的三件套:(pip 包名, import 模块名) —— python-docx 的模块名是 docx。
_FILES_DEPS: tuple[tuple[str, str], ...] = (
    ("pypdf", "pypdf"), ("python-docx", "docx"), ("openpyxl", "openpyxl"))


def _default_has_dep(module: str) -> bool:
    """依赖探测:find_spec 不 import(不触发副作用/慢启动);任何异常按"没装"。"""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _load_cfg(config_path: str) -> dict[str, Any]:
    """只读 config.yaml 成 dict;缺失/坏 YAML → {}(探测降级为"未配置",不崩)。"""
    try:
        if not config_path:
            return {}
        p = Path(config_path)
        if not p.exists():
            return {}
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def list_unlocks(config_path: str = "", *,
                 has_dep: Optional[Callable[[str], bool]] = None) -> list[dict[str, Any]]:
    """能力解锁清单(确定性,零 LLM)。每项:
    {"id", "status"(on/off/missing_dep), "install"(pip 命令或 ""), "detail"(非机密事实)}。

    ``has_dep`` 可注入(测试用);默认 importlib find_spec。顺序 = 前端展示序(价值降序)。
    """
    dep = has_dep or _default_has_dep
    cfg = _load_cfg(config_path)
    out: list[dict[str, Any]] = []

    # ① MCP 工具 —— 楔入整个 MCP 生态(成千上万现成 server)。
    #    on = mcp 包在 + config.yaml 至少配了 1 个 server;off = 包在没配;missing_dep = 包缺。
    try:
        from karvyloop.console.mcp_presets import configured_names
        n_servers = len(configured_names(config_path)) if config_path else 0
    except Exception:
        n_servers = 0
    mcp_status = (STATUS_MISSING_DEP if not dep("mcp")
                  else (STATUS_ON if n_servers else STATUS_OFF))
    out.append({"id": "mcp", "status": mcp_status,
                "install": 'pip install "karvyloop[mcp]"',
                "detail": {"servers": n_servers}})

    # ② 附件解析(PDF/Word/Excel)—— 三件套齐才算就绪;detail 报缺谁(pip 包名,非机密)。
    missing = [pkg for pkg, mod in _FILES_DEPS if not dep(mod)]
    out.append({"id": "files",
                "status": STATUS_ON if not missing else STATUS_MISSING_DEP,
                "install": 'pip install "karvyloop[files]"',
                "detail": {"missing": missing}})

    # ③ 推送渠道(webhook:ntfy/Bark/Slack)—— 纯配置,零额外依赖。
    #    复用 config_channels 解析器判定(enabled + url 合法才算通),绝不回显 url/headers。
    try:
        from karvyloop.config_channels import webhook_channel_config_from_dict
        webhook_on = webhook_channel_config_from_dict(cfg) is not None
    except Exception:
        webhook_on = False
    out.append({"id": "webhook_channel",
                "status": STATUS_ON if webhook_on else STATUS_OFF,
                "install": "", "detail": {}})

    # ④ 邮件渠道 —— 同上,纯配置;detail 带 inbox 分诊是否也开了(bool,非机密)。
    try:
        from karvyloop.config_channels import (
            email_channel_config_from_dict, inbox_pipe_config_from_dict)
        email_on = email_channel_config_from_dict(cfg) is not None
        inbox_on = inbox_pipe_config_from_dict(cfg) is not None
    except Exception:
        email_on, inbox_on = False, False
    out.append({"id": "email_channel",
                "status": STATUS_ON if email_on else STATUS_OFF,
                "install": "", "detail": {"inbox": inbox_on}})

    # ⑤ 多设备中继 E2E 加密 —— cryptography 在 = 就绪(启用还需 `console --relay`,前端文案说)。
    out.append({"id": "relay",
                "status": STATUS_ON if dep("cryptography") else STATUS_MISSING_DEP,
                "install": 'pip install "karvyloop[relay]"',
                "detail": {}})

    # ⑥ 网页产物真浏览器验收 —— playwright 在 = 就绪(chromium 下载步骤在前端文案里,
    #    是否已下载没有廉价可靠的探测,不假装知道)。
    out.append({"id": "web_verify",
                "status": STATUS_ON if dep("playwright") else STATUS_MISSING_DEP,
                "install": 'pip install "karvyloop[web]" && playwright install chromium',
                "detail": {}})

    return out


__all__ = ["list_unlocks", "STATUS_ON", "STATUS_OFF", "STATUS_MISSING_DEP"]
