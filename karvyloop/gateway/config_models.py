"""config_models — 全局模型注册表的增删改查(读写 ~/.karvyloop/config.yaml)。

Hardy 反复强调:**模型是全局配置**。此前只有只读 `/api/models`,没有管理入口。
这里提供 list / upsert / delete / set_default —— 直接读写 config.yaml(密钥住**仓库外**,安全)。

密钥安全(地基级):
- 列表时**遮罩** —— `${ENV}` 引用原样回显(不是秘密),字面量只露尾 4 位(`****1234`)。
- 保存时密钥**留空 = 保留原值**(前端回传遮罩串 `****` 也视为留空,不覆盖)。
- 全模块**绝不 log / print 密钥**;只回 masked。

config.yaml 结构(与 gateway/registry.from_config 对齐):
  models.providers.<name>.{base_url, api_key, auth_header, messages_path, models:[...]}
  agents.defaults.model = 默认 chat 模型引用;embedding.model = 默认 embedding 引用
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# 有效 API 方言(与 schemas/model.py ModelApi 对齐;前端下拉用)
VALID_APIS = (
    "anthropic-messages", "openai-completions", "openai-responses",
    "google-generative-ai", "ollama", "bedrock-converse",
)
VALID_ROLES = ("chat", "embedding")
# 推理强度档(碎碎念⑩;与 gateway/reasoning.py REASONING_LEVELS 同源语义)
VALID_REASONING = ("fast", "balanced", "deep")


def _default_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


def _load(cfg_path=None) -> dict:
    p = Path(cfg_path) if cfg_path else _default_path()
    if not p.exists():
        return {}
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _save(cfg: dict, cfg_path=None) -> None:
    p = Path(cfg_path) if cfg_path else _default_path()
    import yaml
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _mask_key(k) -> str:
    """遮罩密钥:空→'';`${ENV}` 原样(非秘密);字面量只露尾 4。"""
    if not k:
        return ""
    s = str(k)
    if s.startswith("${"):
        return s
    return ("****" + s[-4:]) if len(s) > 4 else "****"


def _providers(cfg: dict) -> dict:
    return (cfg.get("models") or {}).get("providers") or {}


def _default_chat(cfg: dict) -> str:
    return ((cfg.get("agents") or {}).get("defaults") or {}).get("model", "") or ""


def _default_embedding(cfg: dict) -> str:
    return (cfg.get("embedding") or {}).get("model", "") or ""


def _default_reasoning(cfg: dict) -> str:
    return ((cfg.get("agents") or {}).get("defaults") or {}).get("reasoning", "") or ""


def list_models(cfg_path=None) -> dict:
    """全局模型清单(密钥遮罩)+ 默认标记 + provider 列表。"""
    cfg = _load(cfg_path)
    provs = _providers(cfg)
    dc, de = _default_chat(cfg), _default_embedding(cfg)
    out = []
    for pname, p in provs.items():
        for m in (p.get("models") or []):
            mid = m.get("id", "")
            out.append({
                "id": mid, "name": m.get("name", mid), "provider": pname,
                "api": m.get("api", ""), "role": m.get("role", "chat"),
                "context_window": m.get("context_window"), "max_tokens": m.get("max_tokens"),
                "reasoning": bool(m.get("reasoning", False)),
                "reasoning_styles": dict(m.get("reasoning_styles") or {}),
                "base_url": p.get("base_url", ""),
                "auth_header": p.get("auth_header", "x-api-key"),
                "messages_path": p.get("messages_path", ""),
                "api_key_masked": _mask_key(p.get("api_key")),
                "has_key": bool(p.get("api_key")),
                "is_default_chat": mid == dc,
                "is_default_embedding": mid == de,
            })
    return {"models": out, "default_chat": dc, "default_embedding": de,
            "default_reasoning": _default_reasoning(cfg),
            "valid_reasoning": list(VALID_REASONING),
            "providers": list(provs.keys()), "valid_apis": list(VALID_APIS)}


def upsert_model(spec: dict, cfg_path=None) -> tuple[bool, str]:
    """新增/编辑一个模型(及其 provider)。model id 全局唯一,移动 provider 会迁移。"""
    cfg = _load(cfg_path)
    cfg.setdefault("models", {}).setdefault("providers", {})
    provs = cfg["models"]["providers"]
    pname = str(spec.get("provider", "")).strip()
    mid = str(spec.get("model_id", "")).strip()
    if not pname or not mid:
        return False, "provider 和 model id 必填"
    if "/" not in mid:
        return False, "model id 须为 <provider>/<model-id> 形态(全局唯一引用键)"
    api = str(spec.get("api", "")).strip()
    if api not in VALID_APIS:
        return False, f"api 须是 {VALID_APIS} 之一"
    role = str(spec.get("role", "chat")).strip() or "chat"
    if role not in VALID_ROLES:
        return False, "role 须是 chat 或 embedding"
    p = provs.setdefault(pname, {"base_url": "", "models": []})
    if spec.get("base_url"):
        p["base_url"] = str(spec["base_url"]).strip()
    if spec.get("auth_header"):
        p["auth_header"] = spec["auth_header"]
    if spec.get("messages_path"):
        p["messages_path"] = str(spec["messages_path"]).strip()
    # 额外静态请求头(如 Kimi For Coding 的 UA 放行门)—— 配置驱动接入奇怪端点。
    # 只接受 str→str 的 dict;绝不让它带鉴权头(密钥唯一来源是 api_key)。
    eh = spec.get("extra_headers")
    if isinstance(eh, dict):
        clean = {str(k): str(v) for k, v in eh.items()
                 if str(k).lower() not in ("authorization", "x-api-key")}
        if clean:
            p["extra_headers"] = clean
        else:
            p.pop("extra_headers", None)
    # 密钥:留空 / 回传遮罩串 → 保留原值;否则写入
    nk = str(spec.get("api_key", "") or "").strip()
    if nk and not nk.startswith("****"):
        p["api_key"] = nk
    p.setdefault("models", [])
    md = {
        "id": mid, "name": str(spec.get("model_name") or mid), "api": api, "role": role,
        "context_window": int(spec.get("context_window") or 200000),
        "max_tokens": int(spec.get("max_tokens") or 8192),
        "reasoning": bool(spec.get("reasoning", False)),
    }
    # 推理强度落参表(可选,碎碎念⑩):{档: {原样注入请求体的参数}}。只收合法档位、值须是 dict;
    # 空/不合法 → 不写(缺省走 gateway/reasoning.py 内置映射)。
    rs = spec.get("reasoning_styles")
    if isinstance(rs, dict):
        clean_rs = {k: dict(v) for k, v in rs.items()
                    if k in VALID_REASONING and isinstance(v, dict)}
        if clean_rs:
            md["reasoning_styles"] = clean_rs
    # id 全局唯一:先从所有 provider 删同 id(支持改 provider),再加到目标 provider
    for pp in provs.values():
        pp["models"] = [x for x in (pp.get("models") or []) if x.get("id") != mid]
    p["models"].append(md)
    _save(cfg, cfg_path)
    return True, ""


def delete_model(mid: str, cfg_path=None) -> tuple[bool, str]:
    """删模型。守护:默认 chat/embedding 不可删(先换默认)。"""
    cfg = _load(cfg_path)
    if mid in (_default_chat(cfg), _default_embedding(cfg)):
        return False, "这是默认模型,不能删(先把默认换成别的)"
    provs = _providers(cfg)
    found = False
    for p in provs.values():
        before = len(p.get("models") or [])
        p["models"] = [x for x in (p.get("models") or []) if x.get("id") != mid]
        if len(p["models"]) < before:
            found = True
    if not found:
        return False, f"模型 {mid} 不存在"
    _save(cfg, cfg_path)
    return True, ""


def set_default(role: str, mid: str, cfg_path=None) -> tuple[bool, str]:
    """设默认 chat / embedding 模型。"""
    if role not in VALID_ROLES:
        return False, "role 须是 chat 或 embedding"
    cfg = _load(cfg_path)
    provs = _providers(cfg)
    if not any(x.get("id") == mid for p in provs.values() for x in (p.get("models") or [])):
        return False, f"模型 {mid} 不存在"
    if role == "chat":
        cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = mid
    else:
        cfg.setdefault("embedding", {})["model"] = mid
    _save(cfg, cfg_path)
    return True, ""


def set_default_reasoning(level: str, cfg_path=None) -> tuple[bool, str]:
    """设全局推理强度档 `agents.defaults.reasoning`(碎碎念⑩)。空串 = 删掉(不设档,零注入)。

    UI 接线待办:models 面板加"推理强度"三档切换调这里(本件后端先落语义,不动前端)。
    """
    lvl = str(level or "").strip()
    if lvl and lvl not in VALID_REASONING:
        return False, f"reasoning 须是 {VALID_REASONING} 之一(或空 = 不设)"
    cfg = _load(cfg_path)
    defaults = cfg.setdefault("agents", {}).setdefault("defaults", {})
    if lvl:
        defaults["reasoning"] = lvl
    else:
        defaults.pop("reasoning", None)
    _save(cfg, cfg_path)
    return True, ""


__all__ = ["list_models", "upsert_model", "delete_model", "set_default",
           "set_default_reasoning", "VALID_APIS", "VALID_REASONING"]
