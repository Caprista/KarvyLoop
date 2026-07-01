"""serve — textual-serve 适配(M3 批 3c)。

边界:--serve 默认 127.0.0.1(不绑 0.0.0.0,LAN 默认关;CLAUDE.md 安全地基)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def launch_serve(
    *,
    command: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    title: str = "KarvyLoop Workbench",
    public_url: str | None = None,
    debug: bool = False,
) -> None:
    """启 textual-serve,把指定 command 当 subprocess 跑,WS 暴露给浏览器。

    Args:
        command: 启动 KarvyLoop Workbench 的 shell 命令(如 `python -m karvyloop.cli.chat`)。
        host: 默认 `127.0.0.1`(不绑 LAN);想 LAN 访问需显式传 `0.0.0.0`。
        port: 默认 8765。
        title: 浏览器 tab 标题。
        public_url: 反代后公开 URL(可选)。
        debug: True 启 Textual devtools。
    """
    try:
        from textual_serve.server import Server
    except ImportError as e:
        raise RuntimeError(
            "textual-serve 未安装;请 `pip install textual-serve>=0.5`"
        ) from e

    if host == "0.0.0.0":
        logger.warning(
            "serve 绑 0.0.0.0 = LAN 暴露;CLAUDE.md 安全地基:确认仅在受信网络开启"
        )

    server = Server(command=command, host=host, port=port, title=title, public_url=public_url)
    server.serve(debug=debug)