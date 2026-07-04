"""routes_models — /api/model/* + /api/providers/* 端点(全局模型配置增删改查 + onboarding 探测)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

Hardy:模型是全局配置,要有管理入口(增删改查 + 默认/推理档 + 试调校验 + 本地探测)。
"""
from __future__ import annotations

from typing import Any

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
        return True, ""
    except Exception as e:
        # 配置已落盘,但新配置过不了校验(如默认模型被删)→ 不热替换,提示重启/修正
        return False, f"配置已保存,但热加载失败(检查默认模型/必填项;重启也会校验):{e}"


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
    reasoning: bool = False


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
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg,
            # 断②:保存成功≠能聊。fresh 进程无 gateway/main_loop → 明确告知要重启,
            # 前端(引导页)据此显示"密钥已保存,重启 console 后生效"的大字提示,不再静默。
            "restart_required": _restart_required(request.app, reloaded)}


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


@router.post("/model/validate")
async def api_model_validate(request: Request) -> dict[str, Any]:
    """对当前默认 chat 模型做一次最小真调用,确认 key/端点真能用。

    zero-barrier:坏 key / 连不上 **当场抓**,而不是用户首次用才暴露。错误信息脱敏(不泄 key)。
    """
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "no_gateway"}
    try:
        ref = getattr(gw.reg, "default_chat", "") or ""
        if not ref:
            return {"ok": False, "reason": "no_default_model"}
        got = False
        async for _ev in gw.complete([{"role": "user", "content": "ping"}], [], ref):
            got = True
            break   # 收到第一个事件 = 端点+key 通了,够了
        return {"ok": True, "model": ref} if got else {"ok": False, "reason": "no_response"}
    except Exception as e:
        msg = _scrub_secret(f"{type(e).__name__}: {e}")
        # #42 优化②:错误分类学 —— 别把裸异常甩给用户,告诉他是 key 坏了/地址错了/没网
        return {"ok": False, "reason": msg, "error_class": _classify_model_error(msg)}


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
