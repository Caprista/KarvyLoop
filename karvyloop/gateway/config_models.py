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

# 有效 API 方言(与 schemas/model.py ModelApi 对齐 —— schema 兼容层,旧配置能读)
VALID_APIS = (
    "anthropic-messages", "openai-completions", "openai-responses",
    "google-generative-ai", "ollama", "bedrock-converse",
)
# **真实现**的方言(与 gateway/providers/default_adapters 非 stub 集合对齐)。
# CFG-04 教训:前端下拉列了 6 个、其中 4 个是 stub → 用户自定义模型选中 stub,
# 配置"成功保存",聊天时才 NotImplementedError 炸脸。下拉只给这两个;chat 模型落 stub 直接拒。
IMPLEMENTED_APIS = ("anthropic-messages", "openai-completions")
VALID_ROLES = ("chat", "embedding")
# 推理强度档(碎碎念⑩;与 gateway/reasoning.py REASONING_LEVELS 同源语义)
VALID_REASONING = ("fast", "balanced", "deep")
# 输入模态(与 schemas/model.py InputModality 对齐;D/内测 U-06 配置约定)
VALID_MODALITIES = ("text", "image", "audio", "video")


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
    # 写前备份(Hardy 实损:一次误保存把能用的 provider 配置盖没了,重启才暴露且无从恢复)。
    # 单代 .bak 够用:任何一次写坏,上一版永远拿得回;绝不 log 内容(里面是密钥)。
    if p.exists():
        try:
            import shutil
            shutil.copy2(p, p.with_suffix(".yaml.bak"))
        except OSError:
            pass   # 备份失败不挡保存(只是少一层保险)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _mask_key(k) -> str:
    """遮罩密钥:空→'';`${ENV}` 原样(非秘密);字面量只露尾 4。"""
    if not k:
        return ""
    s = str(k)
    if s.startswith("${"):
        return s
    return ("****" + s[-4:]) if len(s) > 4 else "****"


def _env_ref_unset(k) -> bool:
    """`${VAR}` 引用了未设(或设成空)的环境变量 → True(展开后就是空串)。非引用/已设 → False。

    审计 #87 §3-SUSPECTED②:config 写 `${OPENAI_API_KEY}` 但 env 没 export → registry 展开成
    空串,而 _mask_key 原样回显 `${VAR}` + has_key=True → 面板显示"已配置",聊天时才 401。
    这里对面板诚实标注"env 未设",别让"配了个没设的引用"冒充"已配好"。绝不 log/回显 env 值。
    """
    import os
    import re
    s = str(k or "")
    refs = re.findall(r"\$\{([^}]+)\}", s)
    if not refs:
        return False
    return any(not os.environ.get(r, "").strip() for r in refs)


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
                # `${VAR}` 引用但 env 未设 → 面板标"env 未设",别把它当"已配置"骗过用户(SUSPECTED②)。
                "env_unset": _env_ref_unset(p.get("api_key")),
                "is_default_chat": mid == dc,
                "is_default_embedding": mid == de,
            })
    # valid_apis 喂前端下拉:**只列真实现的方言**(CFG-04:列 stub = 引用户造一个必炸的配置)。
    # all_apis 保留完整 schema 集合(诊断/兼容读旧配置用,不进下拉)。
    return {"models": out, "default_chat": dc, "default_embedding": de,
            "default_reasoning": _default_reasoning(cfg),
            "valid_reasoning": list(VALID_REASONING),
            "providers": list(provs.keys()), "valid_apis": list(IMPLEMENTED_APIS),
            "all_apis": list(VALID_APIS)}


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
    # CFG-04 写入即拦:chat 模型落在未实现方言上 = 存一个每次聊天必炸的配置,当场拒 + 人话指路。
    # 只拦 chat:默认配置自带 embedding 模型(api: ollama)且 embed 无生产调用者,不误伤旧配置编辑。
    if role == "chat" and api not in IMPLEMENTED_APIS:
        from karvyloop.i18n import t
        return False, t("models.api_unimplemented_choice", api=api)
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
    # 密钥:留空 / 回传遮罩串 → 保留原值;否则写入。
    # 但"保留原值"只对**已有密钥的编辑**成立 —— 云端 provider 首配时留空 = 写出一个
    # 永远跑不通的空壳还占住配置(Hardy 实拍:不填 key 也"保存成功",重启后全站锁死)。
    nk = str(spec.get("api_key", "") or "").strip()
    if nk and not nk.startswith("****"):
        p["api_key"] = nk
    else:
        has_existing = bool(str(p.get("api_key") or "").strip())
        base = str(p.get("base_url") or "").lower()
        is_local = (pname in ("ollama", "llamacpp", "lmstudio", "vllm-local")
                    or api == "ollama" or "127.0.0.1" in base or "localhost" in base)
        if not has_existing and not is_local:
            return False, "云端模型必须先填 API Key(留空只在编辑已有配置时表示保留原值)"
    p.setdefault("models", [])
    # 编辑既有模型:先抓旧条目,承载 reasoning / reasoning_styles 的"未提供 = 保留"语义
    # (审计 #87 §3-①③,与 extra_headers 同模具:请求不带该字段 → 不静默重置/整段丢)。
    existing_md = next(
        (x for pp in provs.values() for x in (pp.get("models") or [])
         if x.get("id") == mid), None)
    # 深想标志:None(请求未承载)= 保留旧值(新模型则 False);显式 bool = 覆写。
    # 此前无条件 `bool(spec.get("reasoning", False))` 把任意字段的编辑保存都重置成 False。
    r = spec.get("reasoning")
    reasoning_val = (bool(r) if r is not None
                     else bool((existing_md or {}).get("reasoning", False)))
    md = {
        "id": mid, "name": str(spec.get("model_name") or mid), "api": api, "role": role,
        "context_window": int(spec.get("context_window") or 200000),
        "max_tokens": int(spec.get("max_tokens") or 8192),
        "reasoning": reasoning_val,
    }
    # 推理强度落参表(可选,碎碎念⑩):{档: {原样注入请求体的参数}}。
    # None(未承载)= 保留旧表(编辑其它字段不整段丢);显式 dict = 覆写(只收合法档位、值须是 dict;
    # 全不合法/空 dict = 清空)。缺省走 gateway/reasoning.py 内置映射。
    rs = spec.get("reasoning_styles")
    if rs is None:
        if existing_md and existing_md.get("reasoning_styles"):
            md["reasoning_styles"] = {k: dict(v) for k, v in existing_md["reasoning_styles"].items()
                                      if isinstance(v, dict)}
    elif isinstance(rs, dict):
        clean_rs = {k: dict(v) for k, v in rs.items()
                    if k in VALID_REASONING and isinstance(v, dict)}
        if clean_rs:
            md["reasoning_styles"] = clean_rs
    # 输入模态(可选,D/内测 U-06):`input_modalities: [text, image]`,配了就认;
    # **没配 = 不写字段 = 未声明(None)**→ 执行器保持旧行为(带图照拼,存量视觉模型零回退);
    # 显式声明且不含 image 才降级图块。None(请求未承载)= 保留旧值(与 reasoning_styles 同模具:
    # 编辑别的字段不把手写的声明静默丢掉);显式 list = 覆写(只收合法模态、去重保序;
    # 全不合法/空 = 清掉字段回到未声明)。不做模型名→能力猜表。
    im = spec.get("input_modalities")
    if im is None:
        if existing_md and existing_md.get("input_modalities"):
            md["input_modalities"] = [str(x) for x in existing_md["input_modalities"]]
    elif isinstance(im, list):
        clean_im: list = []
        for x in im:
            x = str(x).strip()
            if x in VALID_MODALITIES and x not in clean_im:
                clean_im.append(x)
        if clean_im:
            md["input_modalities"] = clean_im
    # id 全局唯一:先从所有 provider 删同 id(支持改 provider),再加到目标 provider
    for pp in provs.values():
        pp["models"] = [x for x in (pp.get("models") or []) if x.get("id") != mid]
    p["models"].append(md)
    _save(cfg, cfg_path)
    return True, ""


def delete_model(mid: str, cfg_path=None) -> tuple[bool, str]:
    """删模型。守护:默认 chat/embedding 不可删(先换默认)。

    CFG-06(内测建议):删掉某 provider 的**最后一个**模型时,把该 provider 块整块清掉
    (base_url/api_key/auth_header 等"相关配置数据"不残留在 config.yaml 里)。
    只清**本次删空**的 provider —— 用户手写的其它配置项(别的 provider/顶层键)一概不动;
    写盘走既有 _save(整树回写已加载键 + 写前留一代 .bak)。
    """
    cfg = _load(cfg_path)
    if mid in (_default_chat(cfg), _default_embedding(cfg)):
        return False, "这是默认模型,不能删(先把默认换成别的)"
    provs = _providers(cfg)
    found = False
    emptied: list[str] = []
    for pname, p in provs.items():
        before = len(p.get("models") or [])
        p["models"] = [x for x in (p.get("models") or []) if x.get("id") != mid]
        if len(p["models"]) < before:
            found = True
            if not p["models"]:
                emptied.append(pname)   # 本次删空 → provider 块(含 key)一起清
    if not found:
        return False, f"模型 {mid} 不存在"
    for pname in emptied:
        provs.pop(pname, None)
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
           "set_default_reasoning", "VALID_APIS", "IMPLEMENTED_APIS", "VALID_REASONING"]
