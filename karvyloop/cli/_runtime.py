"""_runtime — 抽 cmd_chat 和 cmd_console 共享的 runtime 解析(M3+ 批 8.5-C-frontend)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

为什么抽:cmd_chat 和 cmd_console 都需要 (config + token + gateway + sandbox + workspace + model),
重复 2 份 = 漂移风险。借 Q5:不重写,抽 1 层薄胶水。

借:Q5 自造≠闭门造车 — `_bootstrap_runtime` 已在 `karvyloop.cli.run` 写好,本模块
   只 import + 调,**不**重写 runtime bootstrap 逻辑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResolvedRuntime:
    """共享 runtime 解析结果。"""
    config_path: Path
    main_loop: Optional[object]   # MainLoop | None
    runtime_kwargs: dict          # 慢脑工厂 kwargs(token/sandbox/gateway/workspace_root/model_ref)
    skills_dir: Path


def resolve_runtime(
    config_path: Optional[Path] = None,
    workspace_root: Optional[str] = None,
) -> ResolvedRuntime:
    """解析 runtime — cmd_chat 和 cmd_console 共用。

    Args:
        config_path: 显式 config.yaml 路径(None 时走 ~/.karvyloop/config.yaml 默认)。
        workspace_root: 显式 workspace 根(None 时走 cwd)。

    Returns:
        ResolvedRuntime — 含 main_loop / runtime_kwargs / skills_dir / config_path。

    失败模式:
    - config 不存在 → main_loop=None, runtime_kwargs={}(修 silent-fail 路径)
    - build_main_loop 异常 → 同上 + logger.warning
    - _bootstrap_runtime 异常 → runtime_kwargs={} 但 main_loop 仍可构造(走 fallback)
    """
    cfg_path = Path(config_path) if config_path else Path.home() / ".karvyloop" / "config.yaml"
    skills_dir = Path.home() / ".karvyloop" / "skills"

    main_loop = None
    runtime_kwargs: dict = {}

    if not cfg_path.exists():
        # silent-fail 路径(批 8.5-A 修:不再静默)
        return ResolvedRuntime(
            config_path=cfg_path,
            main_loop=None,
            runtime_kwargs={},
            skills_dir=skills_dir,
        )

    try:
        from karvyloop.cli.run_loop import build_main_loop
        main_loop = build_main_loop(config_path=cfg_path, skills_dir=skills_dir)
    except Exception as e:
        logger.warning(f"MainLoop 构造失败(runtime 仍可起): {e}")
        return ResolvedRuntime(
            config_path=cfg_path,
            main_loop=None,
            runtime_kwargs={},
            skills_dir=skills_dir,
        )

    try:
        from karvyloop.cli.run import _bootstrap_runtime
        boot = _bootstrap_runtime(
            config_path=cfg_path,
            workspace_root=workspace_root or str(Path.cwd()),
            model_ref=None,
        )
        if boot is not None:
            _cfg, token, gateway, sb, ws, default_model = boot
            runtime_kwargs = {
                "token": token, "sandbox": sb, "gateway": gateway,
                "workspace_root": ws, "model_ref": default_model,
            }
            # §13.3:gateway 到位后给 MainLoop 接上**语义可缓存性判定器** —— 让真正稳定的任务能回放
            # 省 token;判不出/无 gateway 时 MainLoop 默认 dynamic(宁重跑不投毒)。
            try:
                from karvyloop.crystallize.result_classifier import (
                    make_result_classifier, make_skill_namer)
                clf = make_result_classifier(gateway, default_model or "")
                if clf is not None and main_loop is not None:
                    main_loop._result_classifier = clf
                # 命名可读性(S):结晶落盘时用同一 gateway 顺手起 kebab 可读名;无 gateway → 确定性兜底。
                namer = make_skill_namer(gateway, default_model or "")
                if namer is not None and main_loop is not None:
                    main_loop.set_skill_namer(namer)
            except Exception as e:
                logger.warning(f"result_classifier/skill_namer 接线失败(默认兜底,不影响): {e}")
    except Exception as e:
        logger.warning(f"runtime_kwargs 注入失败(不影响启动): {e}")

    return ResolvedRuntime(
        config_path=cfg_path,
        main_loop=main_loop,
        runtime_kwargs=runtime_kwargs,
        skills_dir=skills_dir,
    )


__all__ = ["ResolvedRuntime", "resolve_runtime"]
