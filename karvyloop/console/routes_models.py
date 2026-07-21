"""routes_models — /api/model/* + /api/providers/* 端点(全局模型配置增删改查 + onboarding 探测)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

Hardy:模型是全局配置,要有管理入口(增删改查 + 默认/推理档 + 试调校验 + 本地探测)。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


def _model_cfg_path(app):
    return getattr(app.state, "config_path", "") or None


def _reload_gateway_registry(app) -> tuple[bool, str]:
    """改完 config.yaml → 热替换内存里的 ModelRegistry(下次 LLM 调用即生效,不必重启)。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    cfgp = _model_cfg_path(app)
    if gw is None or not cfgp:
        return False, "无 gateway 或无 config 路径(--no-llm?)"
    try:
        from karvyloop.gateway.registry import ModelRegistry
        gw.reg = ModelRegistry.load(cfgp)
        _mark_config_seen(app)   # 自己刚读过盘 → 记 mtime,外改检测别把这次当"别处修改"
        return True, ""
    except Exception as e:
        _mark_config_seen(app)   # 坏配置也记——本次已 fail-loud 报因,watcher 别每轮重复轰
        # 配置已落盘,但新配置过不了校验(如默认模型被删)→ 不热替换,提示重启/修正
        return False, f"配置已保存,但热加载失败(检查默认模型/必填项;重启也会校验):{e}"


def _config_mtime(app) -> float:
    cfgp = _model_cfg_path(app)
    try:
        import os
        return os.stat(cfgp).st_mtime if cfgp else 0.0
    except OSError:
        return 0.0


def _mark_config_seen(app) -> None:
    app.state._config_mtime_seen = _config_mtime(app)


def check_config_external_change(app) -> bool:
    """配置外改检测(内测实拍病根:终端与 WebUI 双通道写同一份 config.yaml,console 只在
    自己保存时读盘 → 终端写坏的配置有"潜伏期",到重启/下次 UI 保存才发作,像"放一会就坏")。

    一次 stat(微秒级)比对 mtime:别处改了 → 立刻热加载 + 主动推一句(成功=已重新加载;
    失败=fail-loud 说清哪坏了)。潜伏期归零:盘一坏,下一次聊天/下一个维护 tick 就现形。
    挂两处:drive 入口(改完配置第一件事就是试聊)+ 维护 tick(不聊天的定时/追求路径兜底)。
    返回 True=检测到外改(无论热加载成败)。"""
    seen = getattr(app.state, "_config_mtime_seen", None)
    now_m = _config_mtime(app)
    if seen is None:              # 首次:只登记基线,不算外改
        app.state._config_mtime_seen = now_m
        return False
    if now_m == seen or now_m == 0.0:
        return False
    ok, msg = _reload_gateway_registry(app)   # 内部成败都会 _mark_config_seen(不重复轰)
    from karvyloop import i18n as _i18n
    from karvyloop.console.task_events import schedule_system_error
    if ok:
        schedule_system_error(app, "config_watch", _i18n.t("config.external_reloaded"))
    else:
        schedule_system_error(app, "config_watch",
                              _i18n.t("config.external_reload_failed", reason=msg))
    return True


@router.get("/model/config")
def api_model_config(request: Request) -> dict[str, Any]:
    """全局模型管理视图(密钥遮罩 + 默认标记 + provider 列表 + 合法 api 列表)。"""
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"models": [], "no_llm": True}
    from karvyloop.gateway.config_models import list_models
    try:
        return list_models(cfgp)
    except Exception as e:
        return {"models": [], "reason": f"读配置失败:{e}"}


class ModelSaveRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_name: str = Field(default="", max_length=128)
    api: str = Field(..., max_length=32)
    role: str = Field(default="chat", max_length=16)
    base_url: str = Field(default="", max_length=256)
    api_key: str = Field(default="", max_length=512)      # 留空/遮罩串=保留原值
    auth_header: str = Field(default="", max_length=32)
    messages_path: str = Field(default="", max_length=128)
    context_window: int = Field(default=200000, ge=0)
    max_tokens: int = Field(default=8192, ge=0)
    # 深想标志:None = 不承载 → 保留已有值(编辑任意字段不静默重置成 False);
    # 显式 True/False = 覆写。审计 #87 §3-①:此前 `bool=False` 默认 + upsert 无条件重建 →
    # 任何 reasoning:true 模型(如 Kimi For Coding 预设)一经控制台编辑即被打回 False,深想不注入。
    # 照 extra_headers 那次"有值才覆盖"的守卫;新模型无旧值时落 False。
    reasoning: Optional[bool] = None
    # 每模型推理落参表(见 config_models.upsert_model)。None = 不承载 → 保留已有值
    # (审计 #87 §3-③:此前完全没这字段 → upsert 重建 md 时整段丢失);显式 dict = 覆写(清洗)。
    reasoning_styles: Optional[dict[str, Any]] = None
    # 额外静态请求头(如 Kimi For Coding 的 User-Agent 放行门)。None = 不碰已有配置;
    # 传 dict = 覆写(config_models 会剥掉任何鉴权头 —— 密钥唯一来源仍是 api_key)。
    # 此前 preset 带 extra_headers 但这层没字段 → 引导保存时被静默丢掉(Kimi 跑不通的一环)。
    extra_headers: Optional[dict[str, str]] = None


def _restart_required(app, reloaded: bool) -> bool:
    """保存/改配置后,进程还到不到得了"能真聊"?(闭环审计断②诚实面)

    fresh 进程(无 config 启动)gateway/main_loop 都是 None:热加载没有对象可替换,
    且 pump/质量裁判/trace 漏斗/task sink 全在 entry 启动期按 main_loop 接线 ——
    在线重建=半活状态,诚实答案是"重启 console"。前端拿这个标志显示大字提示。
    """
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    return (not reloaded) or rk.get("gateway") is None \
        or getattr(app.state, "main_loop", None) is None


@router.post("/model/save")
def api_model_save(req: ModelSaveRequest, request: Request) -> dict[str, Any]:
    """新增/编辑全局模型(写 config.yaml + 热加载注册表)。密钥留空=保留原值。"""
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import upsert_model
    ok, reason = upsert_model(req.model_dump(), cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    # Kimi 三面孔诚实提示:sk-kimi- 前缀 = For Coding 的 key,粘在 moonshot 聊天端点必 401。
    # 不拦保存(key 归属只看前缀是推断不是断言),但当场把话说明白 —— 别等 validate 401 让用户猜。
    from karvyloop.gateway.presets import kimi_key_guidance
    hint = kimi_key_guidance(req.api_key, req.base_url)
    out: dict[str, Any] = {"ok": True, "reloaded": reloaded, "reload_note": rmsg,
                           # 断②:保存成功≠能聊。fresh 进程无 gateway/main_loop → 明确告知要重启,
                           # 前端(引导页)据此显示"密钥已保存,重启 console 后生效"的大字提示,不再静默。
                           "restart_required": _restart_required(request.app, reloaded)}
    if hint:
        out["hint"] = hint
    return out


@router.get("/providers/presets")
def api_providers_presets(request: Request) -> dict[str, Any]:
    """引导式 onboarding 的 provider 预设(选一个→预填技术字段,只需粘 key;含"去哪拿 key")。"""
    from karvyloop.gateway.presets import presets_public
    return {"presets": presets_public()}


def _scrub_secret(msg: str) -> str:
    """错误信息脱敏(CLAUDE.md:绝不外泄 key / Authorization)。保留 401/连不上等有用信号。"""
    import re
    s = str(msg or "")
    s = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", s)
    s = re.sub(r"(?i)(bearer|x-api-key|authorization)[:=\s]+\S+", r"\1 ***", s)
    s = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "***", s)   # 兜底:长 token 串一律打码
    return s[:200]


async def validate_default_model(app) -> dict[str, Any]:
    """对当前默认 chat 模型做一次最小真调用,确认 key/端点真能用(唯一一套校验,别造第二套)。

    复用方:① POST /api/model/validate(引导页"保存并验证");② GET /api/setup_status?live=1
    (CFG-05:console 重启后的启动 gate —— 配置在≠能用,与首配同级真验)。
    错误信息脱敏(不泄 key);失败时带 model + error_class(前端给"哪个模型/什么错"的诚实原因)。
    """
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        # fresh 进程(冷启动无 config)也要能**真验证**:从刚保存的 config 建临时 gateway
        # 打一发最小调用 —— 否则引导页"保存并验证"在最需要验证的场景(首配)反而不验,
        # 坏 key 要等用户重启+首聊才炸(Hardy 实拍拍死的不诚实面)。
        cfgp = _model_cfg_path(app)
        if not cfgp:
            return {"ok": False, "reason": "no_gateway"}
        try:
            from karvyloop.gateway import GatewayClient
            from karvyloop.gateway.registry import ModelRegistry
            gw = GatewayClient(ModelRegistry.load(cfgp))
        except Exception as e:
            msg = _scrub_secret(f"{type(e).__name__}: {e}")
            return {"ok": False, "reason": msg, "error_class": _classify_model_error(msg)}
    ref = ""
    try:
        ref = getattr(gw.reg, "default_chat", "") or ""
        if not ref:
            return {"ok": False, "reason": "no_default_model"}
        # CFG-04 验证阶段 fail-loud:默认模型的 api 是 stub(M0 未实现)→ 不打注定
        # NotImplementedError 的请求,当场给人话("此形态未实现,OpenAI 兼容请用 openai-completions")。
        # models 缺失(测试桩 reg)→ 跳过判定,不影响既有 mock 流。
        md = (getattr(gw.reg, "models", None) or {}).get(ref)
        api = getattr(md, "api", "") if md is not None else ""
        if api:
            from karvyloop.gateway.config_models import IMPLEMENTED_APIS
            if api not in IMPLEMENTED_APIS:
                from karvyloop.i18n import t
                return {"ok": False, "reason": t("models.api_unimplemented_choice", api=api),
                        "error_class": "unimplemented_api", "model": ref}
        got = False
        async for _ev in gw.complete([{"role": "user", "content": "ping"}], [], ref):
            got = True
            break   # 收到第一个事件 = 端点+key 通了,够了
        return {"ok": True, "model": ref} if got \
            else {"ok": False, "reason": "no_response", "model": ref}
    except Exception as e:
        msg = _scrub_secret(f"{type(e).__name__}: {e}")
        # #42 优化②:错误分类学 —— 别把裸异常甩给用户,告诉他是 key 坏了/地址错了/没网
        return {"ok": False, "reason": msg, "error_class": _classify_model_error(msg), "model": ref}


@router.post("/model/validate")
async def api_model_validate(request: Request) -> dict[str, Any]:
    """对当前默认 chat 模型做一次最小真调用,确认 key/端点真能用。

    zero-barrier:坏 key / 连不上 **当场抓**,而不是用户首次用才暴露。错误信息脱敏(不泄 key)。
    """
    return await validate_default_model(request.app)


def _classify_model_error(msg: str) -> str:
    """把模型试调异常粗分类(bad_key / unreachable / bad_url / unknown),前端映射成人话提示。"""
    m = (msg or "").lower()
    if any(x in m for x in ("401", "403", "unauthorized", "invalid api key", "authentication")):
        return "bad_key"
    if any(x in m for x in ("404", "not found", "unknown path")):
        return "bad_url"
    if any(x in m for x in ("connecterror", "connecttimeout", "connection refused", "getaddrinfo",
                            "nodename", "timed out", "timeout", "ssl", "network")):
        return "unreachable"
    return "unknown"


@router.get("/providers/detect_local")
async def api_detect_local_models(request: Request) -> dict[str, Any]:
    """#42 优化②:探测本机 Ollama(11434)→ 给「零 key 直用本地模型」路径。

    Ollama 提供 OpenAI 兼容端点(/v1);探测到就返回可用模型名,前端引导一键预填
    (base_url=http://127.0.0.1:11434/v1,api_key 任意占位)。探不到 → found=False,不报错。
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                names = [m.get("name", "") for m in (r.json().get("models") or [])][:20]
                return {"found": True, "models": [n for n in names if n]}
    except Exception:
        pass
    return {"found": False, "models": []}


class ModelDeleteRequest(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=128)


@router.post("/model/delete")
def api_model_delete(req: ModelDeleteRequest, request: Request) -> dict[str, Any]:
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import delete_model
    ok, reason = delete_model(req.model_id, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}


class ModelDefaultRequest(BaseModel):
    role: str = Field(..., max_length=16)      # chat | embedding
    model_id: str = Field(..., min_length=1, max_length=128)


@router.post("/model/set_default")
def api_model_set_default(req: ModelDefaultRequest, request: Request) -> dict[str, Any]:
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import set_default
    ok, reason = set_default(req.role, req.model_id, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}


class ModelReasoningRequest(BaseModel):
    level: str = Field(default="", max_length=16)   # fast | balanced | deep | ""(空=清除档位)


@router.post("/model/reasoning")
def api_model_set_reasoning(req: ModelReasoningRequest, request: Request) -> dict[str, Any]:
    """设全局推理强度档(Hardy ⑩ 的可见面)。写 `agents.defaults.reasoning` + 热加载注册表。

    当前档由 GET /model/config 顺势带出(list_models 已含 default_reasoning/valid_reasoning),
    不另开只读端点。空 level = 清除档位(零注入,走各模型缺省)。
    """
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import set_default_reasoning
    ok, reason = set_default_reasoning(req.level, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}
