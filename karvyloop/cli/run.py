"""karvyloop run:一句话→沙箱执行→流式返回（cli/run.py）。

规格：docs/modules/workbench-cli.md §3 run.py + #7 §2 垂直切片。
- 加载 config.yaml → ModelRegistry
- mint 最小权限 token
- 调 forge.generate_and_run(垂直切片)
- 流式渲染(可见即信任)
- M3+ 批 4(2026-06-17):接 MainLoop,默认走"recall → 慢脑(forge)→ observe →
  maybe_promote → 自动结晶";--no-recall / --json 走原直跳路径(M0 兼容)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

from karvyloop.capability import mint
from karvyloop.schemas import Capability
from karvyloop.coding.forge import generate_and_run
from karvyloop.gateway import GatewayClient
from karvyloop.gateway.registry import ModelRegistry
from karvyloop.sandbox import default_sandbox

from .render import Renderer


def _make_token(workspace_root: str) -> object:
    """M0 简版:工作区写 token(读 + 写 + exec)。"""
    return mint(
        task_id="cli",
        grants=[
            Capability(resource=f"fs:{workspace_root}", ops=["read", "write"]),
            Capability(resource=f"fs:{workspace_root}", ops=["exec"]),
        ],
        ttl_seconds=3600.0,
    )


def _bootstrap_runtime(
    *,
    config_path: Optional[Path],
    workspace_root: Optional[str],
    model_ref: Optional[str],
) -> Optional[tuple[Path, object, GatewayClient, object, str, str]]:
    """加载 config + gateway + sandbox + token,返回 (cfg_path, token, gateway, sb, ws, default_model)。

    抽出来给 cmd_run_async(直跳) 和 cmd_run_via_loop(主循环)共用,避免重复读 config。
    config 缺失时写 stderr + 返 None(让 caller 决定 return 1 还是 fallback)。
    """
    cfg_path = Path(config_path) if config_path else Path.home() / ".karvyloop" / "config.yaml"
    if not cfg_path.exists():
        from karvyloop.i18n import t
        sys.stderr.write(t("cli.run.config_missing", path=cfg_path) + "\n")
        return None
    reg = ModelRegistry.load(cfg_path)
    gateway = GatewayClient(reg)
    sb = default_sandbox()
    ws = workspace_root or str(Path.cwd())
    token = _make_token(ws)
    return cfg_path, token, gateway, sb, ws, (model_ref or reg.default_chat)


async def cmd_run_async(
    intent: str,
    *,
    config_path: Optional[Path] = None,
    workspace_root: Optional[str] = None,
    model_ref: Optional[str] = None,
    renderer: Optional[Renderer] = None,
    json_output: bool = False,
) -> int:
    """异步核心:跑一次垂直切片(M0 简版直跳 forge,M3+ 批 4 后被 cmd_run_via_loop 替代)。

    返回 0 成功 / 1 失败。
    """
    import io

    boot = _bootstrap_runtime(
        config_path=config_path,
        workspace_root=workspace_root,
        model_ref=model_ref,
    )
    if boot is None:
        return 1
    cfg_path, token, gateway, sb, ws, default_model = boot

    # renderer
    if json_output:
        # JSON 输出:把 NDJSON emitter 接到 stdout
        from karvyloop.coding.ndjson import NdjsonEmitter
        sink = io.StringIO()
        emitter = NdjsonEmitter(sink=sink, session_id="cli")
        result = await generate_and_run(
            intent, token, sb,
            gateway=gateway, emitter=emitter, workspace_root=ws,
            model_ref=default_model,
        )
        # 把 sink 内容写到 stdout(纯 JSON,无装饰)
        sys.stdout.write(sink.getvalue())
        sys.stdout.flush()
    else:
        r = renderer or Renderer()
        # 跑(没 emitter → forge 内部不打印 NDJSON)
        result = await generate_and_run(
            intent, token, sb,
            gateway=gateway, workspace_root=ws,
            model_ref=default_model,
            renderer=r,                       # ← 让 Forge 把事件透传给 Renderer
        )
        # 兜底:无 renderer 路径才走 result.text;有 renderer 路径上事件已实时打印
        if result.text and renderer is None:
            r.render_text(result.text)
        # 退出码:completed → 0,其他 → 1
        from karvyloop.atoms import Terminal
        return 0 if result.terminal == Terminal.COMPLETED else 1
    return 0 if result.terminal.value == "completed" else 1


def cmd_run_via_loop(
    intent: str,
    *,
    config_path: Optional[Path] = None,
    workspace_root: Optional[str] = None,
    model_ref: Optional[str] = None,
    renderer: Optional[Renderer] = None,
    skills_dir: Optional[Path] = None,
) -> int:
    """同步入口:走 MainLoop.drive(M3+ 批 4a)。

    R3 关键:此函数**不**进 asyncio.run;run_intent_via_loop 内部 forge_slow_brain_factory
    才用 asyncio.run —— 单一 asyncio 边界,避免嵌套爆。

    M3+ 批 6:跑完(或异常)后显式 close_main_loop_stores 释放 sqlite 连接。
    """
    from .run_loop import build_main_loop, close_main_loop_stores, run_intent_via_loop

    boot = _bootstrap_runtime(
        config_path=config_path,
        workspace_root=workspace_root,
        model_ref=model_ref,
    )
    if boot is None:
        return 1
    cfg_path, token, gateway, sb, ws, default_model = boot
    ml = build_main_loop(config_path=cfg_path, skills_dir=skills_dir)
    try:
        return run_intent_via_loop(
            intent, ml,
            token=token, sandbox=sb, gateway=gateway,
            workspace_root=ws, model_ref=default_model, renderer=renderer,
        )
    finally:
        close_main_loop_stores(ml)


def cmd_run(
    intent: str,
    *,
    config_path: Optional[Path] = None,
    workspace_root: Optional[str] = None,
    model_ref: Optional[str] = None,
    renderer: Optional[Renderer] = None,
    json_output: bool = False,
    no_recall: bool = False,
    skills_dir: Optional[Path] = None,
) -> int:
    """同步入口:早判 → 直跳(MainLoop)或直跳(forge)。

    M3+ 批 4 决策:
      - `--no-recall` 或 `--json` 走原 cmd_run_async(直跳 forge,不污染 UsageStore)
      - 否则走 cmd_run_via_loop(MainLoop,recall → 慢脑 → observe → 结晶)
    """
    try:
        if no_recall or json_output:
            return asyncio.run(cmd_run_async(
                intent,
                config_path=config_path,
                workspace_root=workspace_root,
                model_ref=model_ref,
                renderer=renderer,
                json_output=json_output,
            ))
        return cmd_run_via_loop(
            intent,
            config_path=config_path,
            workspace_root=workspace_root,
            model_ref=model_ref,
            renderer=renderer,
            skills_dir=skills_dir,
        )
    except KeyboardInterrupt:
        from karvyloop.i18n import t
        sys.stderr.write("\n" + t("cli.interrupted") + "\n")
        return 130  # POSIX 128+SIGINT


__all__ = ["cmd_run", "cmd_run_async", "cmd_run_via_loop"]
