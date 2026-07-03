"""karvyloop CLI 入口（cli/main.py）。

规格：docs/modules/workbench-cli.md §3 main.py。
- 子命令路由:init / run / (--version/--help)
- 无依赖极简:不引 argparse 之上的重型 CLI 库
- 返回 exit code
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence


from karvyloop import __version__ as VERSION   # 单一版本源


def _build_parser() -> argparse.ArgumentParser:
    # 9.4 双语:help 文案走 i18n(纯表现层)。help 在 parse 前就生成,故用
    # **当前生效 locale**(env KARVYLOOP_LANG / 默认 en);足够覆盖"装好默认英文"。
    from karvyloop.i18n import t
    p = argparse.ArgumentParser(
        prog="karvyloop",
        description=t("cli.desc"),
    )
    p.add_argument("--version", action="version", version=f"karvyloop {VERSION}")
    p.add_argument("--lang", type=str, default=None, help=t("cli.help.lang_global"))
    sub = p.add_subparsers(dest="cmd", required=False)

    # init
    p_init = sub.add_parser("init", help=t("cli.help.init"))
    p_init.add_argument("--config", type=str, default=None, help=t("cli.help.init.config"))
    p_init.add_argument("--force", action="store_true", help=t("cli.help.init.force"))
    p_init.add_argument("--no-wizard", action="store_true", help=t("cli.help.init.no_wizard"))

    # run
    p_run = sub.add_parser("run", help=t("cli.help.run"))
    p_run.add_argument("intent", type=str, help=t("cli.help.run.intent"))
    p_run.add_argument("--config", type=str, default=None)
    p_run.add_argument("--workspace", type=str, default=None, help=t("cli.help.run.workspace"))
    p_run.add_argument("--model", type=str, default=None, help=t("cli.help.run.model"))
    p_run.add_argument("--json", action="store_true", help=t("cli.help.run.json"))
    p_run.add_argument("--no-recall", action="store_true", help=t("cli.help.run.no_recall"))
    p_run.add_argument("--skills-dir", type=str, default=None, help=t("cli.help.run.skills_dir"))

    # chat(M3 批 3 — Textual TUI workbench)
    p_chat = sub.add_parser("chat", help=t("cli.help.chat"))
    p_chat.add_argument("--config", type=str, default=None)
    p_chat.add_argument("--headless", action="store_true", help=t("cli.help.chat.headless"))
    p_chat.add_argument("--serve", action="store_true", help=t("cli.help.chat.serve"))
    p_chat.add_argument("--host", type=str, default="127.0.0.1", help=t("cli.help.chat.host"))
    p_chat.add_argument("--port", type=int, default=8765, help=t("cli.help.chat.port"))

    # update(版本检测 — 只检测+提示,绝不自动升级)
    sub.add_parser("update", help=t("cli.help.update"))

    # verify-web(网页产物运行时验收 — 无头浏览器真加载抓控制台报错)
    p_vweb = sub.add_parser("verify-web", help=t("cli.help.verify_web"))
    p_vweb.add_argument("path", help=t("cli.help.verify_web.path"))
    p_vweb.add_argument("--entry", type=str, default="index.html", help=t("cli.help.verify_web.entry"))

    # doctor / status(确定性自检 — 零模型,无门槛"修"的 Layer 0)
    p_doctor = sub.add_parser("doctor", help=t("cli.help.doctor"))
    p_doctor.add_argument("--fix", action="store_true", help=t("cli.help.doctor.fix"))
    sub.add_parser("status", help=t("cli.help.status"))

    # replay(M3+ 批 6 — Trace 重放子命令)
    p_replay = sub.add_parser("replay", help=t("cli.help.replay"))
    p_replay.add_argument("task_id", help=t("cli.help.replay.task_id"))
    p_replay.add_argument("--trace-path", type=str, default=None, help=t("cli.help.replay.trace_path"))

    # console(M3+ 批 8.5-C — 本地 HTML 控制台,K3/K4 只读 + K5 工厂)
    # Q5 借:parser 在 console/entry.py 定义,这里委托注入 — 避免 2 份重复
    from karvyloop.console.entry import build_console_parser
    build_console_parser(sub)

    # url — 打印当前正在运行的 console 的访问链接(本机免密 + 跨设备带 token)
    sub.add_parser("url", help=t("cli.help.url"))

    # export — 你的实例是个文件夹:打包带走(排除 config.yaml 等秘密)。文案暂英文硬编码,
    from karvyloop.i18n import t as _t_exp
    p_export = sub.add_parser("export", help=_t_exp("cli.export.help"))
    p_export.add_argument(
        "--out", type=str, default=None,
        help="output archive path (.zip or .tar.gz; default: ./karvyloop-instance-<YYYYMMDD>.zip)")

    # import — export 的回程:一键迁移,把实例包解回 ~/.karvyloop(秘密永不落地)
    p_import = sub.add_parser("import", help=_t_exp("cli.import.help"))
    p_import.add_argument("archive", type=str, help=_t_exp("cli.import.help.archive"))
    p_import.add_argument("--force", action="store_true", help=_t_exp("cli.import.help.force"))
    p_import.add_argument("--dry-run", action="store_true", help=_t_exp("cli.import.help.dry_run"))

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    # 9.4 双语:显式 --lang > env KARVYLOOP_LANG > config.yaml lang > en(语言偏好持久)。
    from karvyloop.i18n import set_startup_locale
    from karvyloop.config_lang import read_lang
    set_startup_locale(explicit=getattr(args, "lang", None),
                       config_lang=read_lang(getattr(args, "config", None)))

    if args.cmd is None:
        parser.print_help(sys.stderr)
        return 0  # 无子命令也当作"显示帮助",exit 0

    if args.cmd == "init":
        from .init import cmd_init
        from pathlib import Path
        rc = cmd_init(
            path=Path(args.config) if args.config else None,
            interactive=True,
            force=args.force,
            no_wizard=args.no_wizard,
        )
        # 首装成功 + 交互式 → **直接打开控制台**(用户不必知道还要敲 `karvyloop console`;
        # 控制台起好会自动开浏览器)。非 TTY / --no-wizard(CI/脚本)不接管。
        if rc == 0 and not args.no_wizard and sys.stdin.isatty():
            from karvyloop.i18n import t as _t
            sys.stderr.write(_t("cli.init.launching_console") + "\n")
            sys.stderr.flush()
            console_argv = ["console"] + (["--config", args.config] if args.config else [])
            console_args = parser.parse_args(console_argv)
            from karvyloop.console.entry import cmd_console
            return cmd_console(console_args)
        return rc

    # 无 Key 强制引导(TUI 端,与网页一致):run/chat 前判断有没有可用模型;
    # 没有 → TTY 自动跑 init 向导,非 TTY 打清晰指引并退出。覆盖首次没配 + Key 被删。
    def _ensure_ready_or_setup() -> bool:
        from pathlib import Path
        from karvyloop.gateway.readiness import is_ready
        from karvyloop.cli.init import default_config_path
        from karvyloop.i18n import t as _t
        cfg = Path(args.config) if args.config else default_config_path()

        def _load_reg():
            try:
                if Path(cfg).exists():
                    from karvyloop.gateway.registry import ModelRegistry
                    return ModelRegistry.load(cfg)
            except Exception:
                return None
            return None

        ready, _why = is_ready(_load_reg())
        if ready:
            return True
        sys.stderr.write(_t("cli.no_key_setup") + "\n")   # 没可用模型/Key —— 先配置
        if sys.stdin.isatty():
            from .init import cmd_init
            cmd_init(path=Path(args.config) if args.config else None, interactive=True, force=False)
            ready, _why = is_ready(_load_reg())          # 向导后重判
            return ready
        return False   # 非 TTY:不强行进破损会话

    if args.cmd == "run":
        if not _ensure_ready_or_setup():
            return 1
        from pathlib import Path
        from .run import cmd_run
        return cmd_run(
            args.intent,
            config_path=Path(args.config) if args.config else None,
            workspace_root=args.workspace,
            model_ref=args.model,
            json_output=args.json,
            no_recall=args.no_recall,
            skills_dir=Path(args.skills_dir) if args.skills_dir else None,
        )

    if args.cmd == "chat":
        if not _ensure_ready_or_setup():
            return 1
        from pathlib import Path
        from .chat import cmd_chat
        return cmd_chat(
            config_path=Path(args.config) if args.config else None,
            headless=args.headless,
            serve=args.serve,
            host=args.host,
            port=args.port,
        )

    if args.cmd == "update":
        from .update_cmd import cmd_update
        return cmd_update()

    if args.cmd == "verify-web":
        from .web_verify_cmd import cmd_verify_web
        return cmd_verify_web(args.path, entry=args.entry)

    if args.cmd == "doctor":
        from .doctor_cmd import cmd_doctor
        return cmd_doctor(fix=getattr(args, "fix", False))

    if args.cmd == "status":
        from .doctor_cmd import cmd_status
        return cmd_status()

    if args.cmd == "replay":
        from pathlib import Path
        from .replay import cmd_replay
        return cmd_replay(
            task_id=args.task_id,
            trace_path=Path(args.trace_path) if args.trace_path else None,
        )

    if args.cmd == "console":
        from karvyloop.console.entry import cmd_console
        return cmd_console(args)

    if args.cmd == "url":
        return _cmd_url()

    if args.cmd == "export":
        from .export_cmd import cmd_export
        return cmd_export(out=args.out)

    if args.cmd == "import":
        from .import_cmd import cmd_import
        return cmd_import(args.archive, force=args.force, dry_run=args.dry_run)

    from karvyloop.i18n import t
    parser.error(t("cli.unknown_cmd", cmd=args.cmd))
    return 2


def _cmd_url() -> int:
    """打印当前运行中的 console 访问链接:本机免密 + 跨设备带 token(读 ~/.karvyloop/console.runtime.json)。"""
    from karvyloop.i18n import t
    from karvyloop.console.access import read_runtime, access_urls
    rt = read_runtime()
    if not rt:
        sys.stderr.write(t("cli.url.no_runtime") + "\n")
        return 1
    urls = access_urls(str(rt.get("host", "127.0.0.1")), int(rt.get("port", 8766)), str(rt.get("token", "")))
    lines = [t("cli.url.local", url=urls["local"])]
    if urls["remote"]:
        lines.append(t("cli.url.remote", url=urls["remote"]))
    else:
        lines.append(t("cli.url.remote_none"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
