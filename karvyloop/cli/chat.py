"""karvyloop chat 子命令(M3 批 3)。

3 模式:
  - 默认前台:跑 WorkbenchApp.run() 终端 UI
  - --headless:不起 UI,只验 App 可构造(给测试用)
  - --serve:textual-serve 远程(LAN/手机浏览器)

边界:CLAUDE.md 安全地基 --host 默认 127.0.0.1(不绑 LAN);想 LAN 需显式传 0.0.0.0。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


def cmd_chat(
    *,
    config_path: Optional[Path] = None,
    headless: bool = False,
    serve: bool = False,
    host: str = "127.0.0.1",
    port: int = 8765,
    argv: Optional[Sequence[str]] = None,
) -> int:
    """karvyloop chat 入口。

    Args:
        config_path: ~/.karvyloop/config.yaml 路径(默认 None,自动解析)。
        headless: True 时只构造 App 验 import,不进入主循环。
        serve: True 时启 textual-serve。
        host: serve host(默认 127.0.0.1)。
        port: serve port(默认 8765)。
        argv: 传给 App.run_test 的额外参数(测试用)。

    Returns:
        exit code (0 成功,非 0 失败)。
    """
    if serve:
        # serve 模式启 textual-serve,subprocess 跑 `python -m karvyloop.cli.chat`
        from karvyloop.workbench.serve import launch_serve
        command = f"{sys.executable} -m karvyloop.cli.chat"
        launch_serve(command=command, host=host, port=port, title="KarvyLoop Workbench")
        return 0

    from karvyloop.domain import Address
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.workbench.app import WorkbenchApp
    from karvyloop.workbench.main_loop_bridge import DriveOutcome, drive_in_tui  # noqa: F401  拍 5 引用
    from ._runtime import resolve_runtime  # 批 8.5-C-frontend: 抽共享(Q5 借 — 不重写)

    workbench = WorkbenchObserver()
    user_address = Address(domain_id="dom-1", role="user", agent_id="ch")

    # 批 8.5-C-frontend:抽 _resolve_runtime 共享给 cmd_console,避免重复 _bootstrap 逻辑
    resolved = resolve_runtime(
        config_path=config_path,
        workspace_root=str(Path.cwd()),
    )
    main_loop = resolved.main_loop
    runtime_kwargs = resolved.runtime_kwargs
    cfg_path = resolved.config_path

    if main_loop is None and not cfg_path.exists():
        # 批 8.5-A:不再静默 — TUI 启动时 stderr 显式警告,让用户立刻看到为何 TUI 提交不响应
        from karvyloop.i18n import t
        sys.stderr.write(t("cli.chat.readonly_warning", path=cfg_path) + "\n")
        sys.stderr.flush()

    # 拍 9.2d:对话编排器(默认续上私聊小卡;multi-turn + 持久 + CV-8/10)。
    # 接线失败不阻断 TUI(降级为无对话上下文)。
    conversation_manager = None
    try:
        from karvyloop.cognition.conversation import ConversationManager, ConversationStore
        conv_store = ConversationStore(Path.home() / ".karvyloop" / "conversations")
        conversation_manager = ConversationManager(conv_store)
        conversation_manager.start()  # 续上最近一段私聊(CV-6,静默)
    except Exception as e:
        logger.warning(f"对话编排器接线失败(TUI 照常起): {e}")

    app = WorkbenchApp(
        workbench=workbench,
        user_address=user_address,
        main_loop=main_loop,
        runtime_kwargs=runtime_kwargs,
        conversation_manager=conversation_manager,
    )

    if headless:
        # 验 import + 构造,不进入主循环
        logger.info("headless 模式:WorkbenchApp 构造成功")
        return 0

    # 默认:前台 TUI
    app.run()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """子入口(给 python -m karvyloop.cli.chat 用)。"""
    p = argparse.ArgumentParser(prog="karvyloop chat")
    p.add_argument("--config", type=str, default=None)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--host", type=str, default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(list(argv) if argv is not None else None)
    return cmd_chat(
        config_path=Path(args.config) if args.config else None,
        headless=args.headless,
        serve=args.serve,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    sys.exit(main())