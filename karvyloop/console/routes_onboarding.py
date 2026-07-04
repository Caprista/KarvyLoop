"""routes_onboarding — /api/onboarding/*(「第一个 10 分钟」新手旅程端点)。

三个端点,全部薄壳(状态机/样例数据在 karvyloop/onboarding.py):
- GET  /api/onboarding/journey  旅程状态 + 演示任务文案(en/zh 两份,前端按 UI 语言取)
- POST /api/onboarding/journey  推进/跳过/重看(stage 必须在合法集合内)
- GET  /api/onboarding/sample   随包样例 CSV(顺手 seed 一份进 workspace,文件面板可见)

诚实红线:sample 只是**输入数据**;演示任务真跑用户配置的模型,回执/曲线点全是真数据。
无模型(--no-llm / 没配 key)→ llm_ready:false,前端如实引导先配模型,不演假戏。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api")


def _has_runs(request: Request) -> bool:
    """实例是否已有任何 run(Trace 非空)—— 新用户检测的唯一判据(不猜)。"""
    ml = getattr(request.app.state, "main_loop", None)
    trace = getattr(ml, "trace", None) if ml is not None else None
    if trace is None:
        return False
    try:
        return bool(trace.all_tasks())
    except Exception:
        return False


def _llm_ready(request: Request) -> bool:
    """演示任务能不能真跑:有 main_loop 且 gateway 已接(没配 key = False,如实说)。"""
    ml = getattr(request.app.state, "main_loop", None)
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    return ml is not None and bool(rk.get("gateway"))


@router.get("/onboarding/journey")
def api_onboarding_journey(request: Request) -> dict[str, Any]:
    """旅程状态。stage ∈ fresh/step1/step2/done/skipped;无状态文件时:
    零 run → fresh(真新用户),有 run → done(老实例绝不突然弹新手旅程)。"""
    from karvyloop.onboarding import JOURNEY_TASKS, SAMPLE_NAME, read_stage
    return {
        "stage": read_stage(has_runs=_has_runs(request)),
        "llm_ready": _llm_ready(request),
        "sample_name": SAMPLE_NAME,
        "tasks": JOURNEY_TASKS,
    }


class JourneyStageRequest(BaseModel):
    stage: str = ""


@router.post("/onboarding/journey")
def api_onboarding_journey_set(req: JourneyStageRequest, request: Request) -> dict[str, Any]:
    """推进旅程阶段(step1/step2/done/skipped;fresh=「重看新手旅程」重置)。
    合法集合外一律拒(ok:false),绝不静默吞。"""
    from karvyloop.onboarding import write_stage
    ok = write_stage((req.stage or "").strip())
    return {"ok": ok, "stage": (req.stage or "").strip()} if ok else \
        {"ok": False, "reason": "bad_stage"}


@router.get("/onboarding/sample")
def api_onboarding_sample(request: Request) -> dict[str, Any]:
    """随包样例 CSV(虚构数据)。顺手把一份 seed 进 workspace 根(文件面板可见、
    「让TA分析」桥也够得着);seed 失败不挡返回(fail-soft,演示走文本附件路径)。"""
    from karvyloop.onboarding import load_sample
    name, text = load_sample()
    if not name:
        return {"ok": False, "reason": "sample_missing"}   # 打包丢了 → 如实报,不编数据
    # seed 进 workspace(已存在不覆盖:用户可能改过它)
    try:
        from pathlib import Path
        rk = getattr(request.app.state, "runtime_kwargs", None) or {}
        root = rk.get("workspace_root") or ""
        if root:
            target = Path(root) / name
            if not target.exists():
                target.write_text(text, encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, "name": name, "text": text}


__all__ = ["router"]
