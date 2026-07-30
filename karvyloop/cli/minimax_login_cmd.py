"""minimax_login_cmd — `karvyloop minimax-login`:用 OAuth 设备码流接 MiniMax(不贴 key)。

Hardy 2026-07-30。跑起来 = 给你一个网址+配对码,你**任意浏览器**打开、登录、批准 → 这边轮询拿到
token → 自动写进 config.yaml 的 minimax provider(access token 当 Bearer key)→ 就能聊了。
设备码流不要回调,**你的 VM(headless)天生行**:你在自己笔记本批准,VM 上轮询即可。

client_id:MiniMax OAuth 客户端 ID。KarvyLoop 要 consent 屏显示自己名字,得注册自己的(真实世界
一步,待办)。现在先用 --client-id 显式传(试通流程)。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# MiniMax anthropic-messages 兼容端点(端点是 MiniMax 的公开信息)
_API_BASE = {"cn": "https://api.minimaxi.com", "global": "https://api.minimax.io"}
_DEFAULT_MODEL = "MiniMax-M3"


def _write_minimax_provider(token, *, region: str, model: str,
                            config_path: Optional[Path]) -> None:
    """把 OAuth token 写成可用的 minimax provider(auth_header=Authorization→网关自动 Bearer),
    并把默认 chat 模型切到它。走 config_models._save(0600 落盘)。"""
    from karvyloop.gateway import config_models as cm
    cfg = cm._load(config_path)
    base = token.resource_url or _API_BASE.get(region, _API_BASE["cn"])
    model_ref = f"minimax/{model}"
    providers = cfg.setdefault("models", {}).setdefault("providers", {})
    providers["minimax"] = {
        "base_url": base,
        "auth": "api-key",
        "auth_header": "Authorization",      # Bearer 系:网关据此发 Authorization: Bearer <key>
        "api_key": token.access,             # OAuth access token 当 key(过期后需重登/刷新,待办)
        "models": [{
            "id": model_ref, "name": "MiniMax", "api": "anthropic-messages",
            "context_window": 200000, "max_tokens": 8192,
        }],
    }
    cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = model_ref
    cm._save(cfg, config_path)


def cmd_minimax_login(*, region: str = "cn", client_id: str = "", model: str = "",
                      config_path: Optional[Path] = None, stdout=None) -> int:
    from karvyloop.llm.minimax_oauth import MiniMaxOAuthError, login_device_flow
    out = stdout or sys.stdout
    if not client_id:
        out.write("需要 --client-id <MiniMax OAuth 客户端 ID>。\n"
                  "(KarvyLoop 尚未注册自己的 MiniMax OAuth 客户端 —— 这是真实世界一步的待办;"
                  "先用你拿到的 client_id 试通流程。)\n")
        return 1
    model = model or _DEFAULT_MODEL

    def _prompt(url: str, code: str) -> None:
        out.write("\n=== 用 MiniMax 登录(在任意浏览器完成,手机/笔记本都行)===\n")
        out.write(f"  1. 打开:{url}\n")
        out.write(f"  2. 登录 MiniMax,如提示输入配对码:{code}\n")
        out.write("  3. 点批准 —— 批准后这里会自动继续(在轮询等你)\n\n")
        out.flush()
        try:
            import webbrowser
            webbrowser.open(url)   # 同机顺手开;headless 开不了就照上面手动
        except Exception:
            pass

    try:
        token = login_device_flow(region=region, client_id=client_id, on_prompt=_prompt)
    except MiniMaxOAuthError as e:
        out.write(f"MiniMax OAuth 登录失败:{e}\n")
        return 1
    except Exception as e:  # 网络等
        out.write(f"MiniMax OAuth 登录出错:{type(e).__name__}: {e}\n")
        return 1

    _write_minimax_provider(token, region=region, model=model, config_path=config_path)
    out.write(f"✅ MiniMax 已通过 OAuth 接好(默认模型 minimax/{model})—— 现在就能聊了。\n")
    return 0


__all__ = ["cmd_minimax_login"]
