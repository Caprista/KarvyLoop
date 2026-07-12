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
    p_doctor.add_argument("--online", action="store_true", help=t("cli.help.doctor.online"))
    sub.add_parser("status", help=t("cli.help.status"))

    # replay(M3+ 批 6 — Trace 重放子命令;可观测性③:--run 按 run_id 过滤)
    p_replay = sub.add_parser("replay", help=t("cli.help.replay"))
    p_replay.add_argument("task_id", nargs="?", default="", help=t("cli.help.replay.task_id"))
    p_replay.add_argument("--run", dest="run_id", type=str, default="", help=t("cli.help.replay.run"))
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

    # relay-serve / relay-pair(docs/43 第二级:Karvy 信使 relay,「信使不拆信」)。
    # 文案暂英文硬编码(照 export 先例;i18n 表本任务不动)。
    p_rserve = sub.add_parser(
        "relay-serve",
        help="run the Karvy messenger relay — stateless, diskless, blind-forwarding rendezvous "
             "(it only ever sees end-to-end ciphertext)")
    p_rserve.add_argument("--host", type=str, default="0.0.0.0",
                          help="bind address (default 0.0.0.0 — a relay is meant to be reachable; "
                               "it holds no keys and sees only ciphertext)")
    p_rserve.add_argument("--port", type=int, default=8767, help="port (default 8767)")
    p_rpair = sub.add_parser(
        "relay-pair",
        help="print pairing info for the messenger relay: room id, console key fingerprint, "
             "one-time pairing code (v1 text pairing; QR/browser pairing is P2)")
    p_rpair.add_argument("--relay-url", type=str, default=None,
                         help="relay address to print in the pairing info (e.g. wss://relay.example)")
    p_rpair.add_argument("--scope", type=str, default="full", choices=["full", "read"],
                         help="access scope for the paired device: full (your own device, default) "
                              "or read (share to others: view-only, GET/HEAD/OPTIONS)")
    p_rpair.add_argument("--dir", type=str, default=None, help=argparse.SUPPRESS)  # state dir override(测试注入)
    # relay-unpair:列已配对设备 / 撤销一个(撤销 = 绝对把控权;撤销后该设备免不了码重连)。
    p_runpair = sub.add_parser(
        "relay-unpair",
        help="list paired devices, or revoke one by fingerprint/pubkey — a revoked device can no "
             "longer reconnect without a fresh pairing code (you keep absolute control)")
    p_runpair.add_argument("target", nargs="?", default=None,
                           help="fingerprint or pubkey-hex to revoke; omit to just list paired devices")
    p_runpair.add_argument("--dir", type=str, default=None, help=argparse.SUPPRESS)  # state dir override(测试注入)
    # devices:同主人设备 mesh 花名册(docs/74 第一刀)—— 我有哪些设备、各自什么能力、在线态。
    p_devices = sub.add_parser(
        "devices",
        help="list your same-owner device mesh — what devices you have, their capabilities, "
             "and presence (register this device on run)")
    p_devices.add_argument("--label", type=str, default=None,
                           help="name for THIS device (e.g. \"home Linux\"); reused if omitted")
    p_devices.add_argument("--remove", type=str, default=None, metavar="TARGET",
                           help="remove a device from your mesh by fingerprint prefix or label. "
                                "If removal narrows your capability boundary (that device is the "
                                "only one providing something), you get a risk warning and must "
                                "re-confirm with --yes")
    p_devices.add_argument("--yes", action="store_true",
                           help="confirm a removal that narrows your capability boundary")
    p_devices.add_argument("--dir", type=str, default=None, help=argparse.SUPPRESS)  # state dir override(测试注入)
    # mesh-sync:跟我的另一台设备同步一次认知/任务(经 relay 交换 MeshLog delta,docs/74)。
    p_msync = sub.add_parser(
        "mesh-sync",
        help="sync cognition/tasks once with one of your own devices over the relay "
             "(exchange shared-log deltas — what you learned there shows up here)")
    p_msync.add_argument("--relay", type=str, required=True, help="relay url (e.g. wss://relay.example)")
    p_msync.add_argument("--peer-room", type=str, required=True, help="the peer device's room id (from its relay-pair)")
    p_msync.add_argument("--fingerprint", type=str, required=True, help="the peer device's key fingerprint (verified, anti-MITM)")
    p_msync.add_argument("--code", type=str, default=None, help="peer's one-time pairing code (first time only)")
    p_msync.add_argument("--dir", type=str, default=None, help=argparse.SUPPRESS)  # state dir override(测试注入)

    # remote:接入端 —— 从另一台机器跨网访问你家的 console(连 relay /join,E2E 握手,发一个请求)。
    p_remote = sub.add_parser(
        "remote",
        help="access your home console from ANOTHER machine over the network — connects to the relay, "
             "does the E2E handshake (verifies fingerprint), sends one request and prints the response")
    p_remote.add_argument("--relay", type=str, required=True, help="relay url (e.g. wss://relay.example)")
    p_remote.add_argument("--room", type=str, required=True, help="room id from `relay-pair`")
    p_remote.add_argument("--fingerprint", type=str, required=True,
                          help="console key fingerprint from `relay-pair` (verified — mismatch = abort, anti-MITM)")
    p_remote.add_argument("--code", type=str, default=None,
                          help="one-time pairing code from `relay-pair` (first time only; paired devices omit)")
    p_remote.add_argument("--request", type=str, required=True,
                          help='request to send, like "GET /api/status"')
    p_remote.add_argument("--dir", type=str, default=None, help=argparse.SUPPRESS)  # state dir override(测试注入)

    # 管理面(名词-动词,gh 风格):role / domain / memory / skill / schedule / token。
    # 覆盖既有后端(RoleRegistry / BusinessDomainRegistry / MemoryManager / SkillIndex /
    # SchedulerStore / TokenLedger),每条 read 支持 --json;create/mutate 走 --yes(H2A CLI 形态)。
    _build_manage_parsers(sub)

    return p


def _build_manage_parsers(sub) -> None:
    """把管理面名词-动词子命令挂上（抽出来保持 _build_parser 清爽,同 build_console_parser 委托风格)。"""
    from karvyloop.i18n import t

    def _add_config(p):
        p.add_argument("--config", type=str, default=None)
        return p

    def _add_json(p):
        p.add_argument("--json", action="store_true", help=t("cli.help.json"))
        return p

    def _add_yes(p):
        p.add_argument("--yes", action="store_true", help=t("cli.help.yes"))
        return p

    # role
    p_role = sub.add_parser("role", help=t("cli.help.role"))
    role_sub = p_role.add_subparsers(dest="subcmd", required=True)
    _add_json(_add_config(role_sub.add_parser("list", help=t("cli.help.role.list"))))
    p_role_show = _add_json(_add_config(role_sub.add_parser("show", help=t("cli.help.role.show"))))
    p_role_show.add_argument("id", type=str, help=t("cli.help.role.id"))
    p_role_create = _add_yes(_add_json(_add_config(
        role_sub.add_parser("create", help=t("cli.help.role.create")))))
    p_role_create.add_argument("--id", type=str, required=True, help=t("cli.help.role.create.id"))
    p_role_create.add_argument("--identity", type=str, default="", help=t("cli.help.role.create.identity"))
    p_role_create.add_argument("--soul", type=str, default="", help=t("cli.help.role.create.soul"))
    p_role_create.add_argument("--nickname", type=str, default="", help=t("cli.help.role.create.nickname"))
    p_role_create.add_argument("--model", type=str, default="", help=t("cli.help.role.create.model"))
    p_role_rm = _add_yes(_add_json(_add_config(
        role_sub.add_parser("rm", help=t("cli.help.role.rm")))))
    p_role_rm.add_argument("id", type=str, help=t("cli.help.role.id"))

    # domain
    p_domain = sub.add_parser("domain", help=t("cli.help.domain"))
    domain_sub = p_domain.add_subparsers(dest="subcmd", required=True)
    _add_json(_add_config(domain_sub.add_parser("list", help=t("cli.help.domain.list"))))
    p_domain_show = _add_json(_add_config(domain_sub.add_parser("show", help=t("cli.help.domain.show"))))
    p_domain_show.add_argument("id", type=str, help=t("cli.help.domain.id"))
    p_domain_create = _add_yes(_add_json(_add_config(
        domain_sub.add_parser("create", help=t("cli.help.domain.create")))))
    p_domain_create.add_argument("--name", type=str, required=True, help=t("cli.help.domain.create.name"))
    p_domain_create.add_argument("--parent", type=str, default="", help=t("cli.help.domain.create.parent"))
    p_domain_archive = _add_yes(_add_json(_add_config(
        domain_sub.add_parser("archive", help=t("cli.help.domain.archive")))))
    p_domain_archive.add_argument("id", type=str, help=t("cli.help.domain.id"))

    # memory
    p_memory = sub.add_parser("memory", help=t("cli.help.memory"))
    memory_sub = p_memory.add_subparsers(dest="subcmd", required=True)
    p_mem_recall = _add_json(_add_config(memory_sub.add_parser("recall", help=t("cli.help.memory.recall"))))
    p_mem_recall.add_argument("query", type=str, help=t("cli.help.memory.recall.query"))
    p_mem_recall.add_argument("--limit", type=int, default=8, help=t("cli.help.memory.limit"))
    p_mem_recall.add_argument("--scope", type=str, default="personal", help=t("cli.help.memory.scope"))
    p_mem_add = _add_json(_add_config(memory_sub.add_parser("add", help=t("cli.help.memory.add"))))
    p_mem_add.add_argument("belief", type=str, help=t("cli.help.memory.add.belief"))
    p_mem_add.add_argument("--scope", type=str, default="personal", help=t("cli.help.memory.scope"))
    p_mem_add.add_argument("--yes", action="store_true", help=t("cli.help.yes"))

    # skill
    p_skill = sub.add_parser("skill", help=t("cli.help.skill"))
    skill_sub = p_skill.add_subparsers(dest="subcmd", required=True)
    _add_json(_add_config(skill_sub.add_parser("list", help=t("cli.help.skill.list"))))
    p_skill_import = _add_yes(_add_json(_add_config(
        skill_sub.add_parser("import", help=t("cli.help.skill.import")))))
    p_skill_import.add_argument("source", type=str, help=t("cli.help.skill.import.source"))
    p_skill_import.add_argument("--overwrite", action="store_true", help=t("cli.help.skill.import.overwrite"))

    # schedule
    p_sched = sub.add_parser("schedule", help=t("cli.help.schedule"))
    sched_sub = p_sched.add_subparsers(dest="subcmd", required=True)
    _add_json(_add_config(sched_sub.add_parser("list", help=t("cli.help.schedule.list"))))
    p_sched_add = _add_yes(_add_json(_add_config(
        sched_sub.add_parser("add", help=t("cli.help.schedule.add")))))
    p_sched_add.add_argument("text", type=str, help=t("cli.help.schedule.add.text"))
    p_sched_rm = _add_yes(_add_json(_add_config(
        sched_sub.add_parser("rm", help=t("cli.help.schedule.rm")))))
    p_sched_rm.add_argument("id", type=str, help=t("cli.help.schedule.id"))
    p_sched_toggle = _add_yes(_add_json(_add_config(
        sched_sub.add_parser("toggle", help=t("cli.help.schedule.toggle")))))
    p_sched_toggle.add_argument("id", type=str, help=t("cli.help.schedule.id"))
    _toggle_grp = p_sched_toggle.add_mutually_exclusive_group(required=True)
    _toggle_grp.add_argument("--on", dest="toggle_on", action="store_true", help=t("cli.help.schedule.on"))
    _toggle_grp.add_argument("--off", dest="toggle_on", action="store_false", help=t("cli.help.schedule.off"))

    # token
    p_token = sub.add_parser("token", help=t("cli.help.token"))
    token_sub = p_token.add_subparsers(dest="subcmd", required=True)
    p_token_report = _add_json(_add_config(token_sub.add_parser("report", help=t("cli.help.token.report"))))
    p_token_report.add_argument("--by", type=str, default="source",
                                choices=["source", "model", "day"], help=t("cli.help.token.by"))


def _dispatch_manage(args) -> Optional[int]:
    """管理面命令分发。命中返回 exit code,未命中(非管理命令)返回 None(让 main 继续)。"""
    from . import manage as M
    cmd = args.cmd
    cfg = getattr(args, "config", None)
    js = getattr(args, "json", False)
    yes = getattr(args, "yes", False)
    if cmd == "role":
        if args.subcmd == "list":
            return M.cmd_role_list(config_path=cfg, json_output=js)
        if args.subcmd == "show":
            return M.cmd_role_show(args.id, config_path=cfg, json_output=js)
        if args.subcmd == "create":
            return M.cmd_role_create(args.id, config_path=cfg, identity=args.identity,
                                     soul=args.soul, nickname=args.nickname, model=args.model,
                                     yes=yes, json_output=js)
        if args.subcmd == "rm":
            return M.cmd_role_rm(args.id, config_path=cfg, yes=yes, json_output=js)
    if cmd == "domain":
        if args.subcmd == "list":
            return M.cmd_domain_list(config_path=cfg, json_output=js)
        if args.subcmd == "show":
            return M.cmd_domain_show(args.id, config_path=cfg, json_output=js)
        if args.subcmd == "create":
            return M.cmd_domain_create(args.name, config_path=cfg, parent=args.parent,
                                       yes=yes, json_output=js)
        if args.subcmd == "archive":
            return M.cmd_domain_archive(args.id, config_path=cfg, yes=yes, json_output=js)
    if cmd == "memory":
        if args.subcmd == "recall":
            return M.cmd_memory_recall(args.query, config_path=cfg, json_output=js,
                                       limit=args.limit, scope=args.scope)
        if args.subcmd == "add":
            return M.cmd_memory_add(args.belief, config_path=cfg, scope=args.scope,
                                    yes=args.yes, json_output=js)
    if cmd == "skill":
        if args.subcmd == "list":
            return M.cmd_skill_list(config_path=cfg, json_output=js)
        if args.subcmd == "import":
            return M.cmd_skill_import(args.source, config_path=cfg, overwrite=args.overwrite,
                                      yes=yes, json_output=js)
    if cmd == "schedule":
        if args.subcmd == "list":
            return M.cmd_schedule_list(config_path=cfg, json_output=js)
        if args.subcmd == "add":
            return M.cmd_schedule_add(args.text, config_path=cfg, yes=yes, json_output=js)
        if args.subcmd == "rm":
            return M.cmd_schedule_rm(args.id, config_path=cfg, yes=yes, json_output=js)
        if args.subcmd == "toggle":
            return M.cmd_schedule_toggle(args.id, on=args.toggle_on, config_path=cfg,
                                         yes=yes, json_output=js)
    if cmd == "token":
        if args.subcmd == "report":
            return M.cmd_token_report(config_path=cfg, json_output=js, by=args.by)
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    # 中文等非 UTF-8 Windows 控制台:输出被管道/重定向时默认 GBK 编码,✓ 等字符直接
    # UnicodeEncodeError 崩(实测)。errors 不动、只换编码;探不到 reconfigure 就算了。
    for _stream in (sys.stdout, sys.stderr):
        try:
            if getattr(_stream, "encoding", "utf-8").lower() not in ("utf-8", "utf8"):
                _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

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
        return cmd_doctor(fix=getattr(args, "fix", False), online=getattr(args, "online", False))

    if args.cmd == "status":
        from .doctor_cmd import cmd_status
        return cmd_status()

    if args.cmd == "replay":
        from pathlib import Path
        from .replay import cmd_replay
        return cmd_replay(
            task_id=args.task_id,
            run_id=getattr(args, "run_id", "") or "",
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

    if args.cmd == "relay-serve":
        from karvyloop.relay.server import cmd_relay_serve
        return cmd_relay_serve(host=args.host, port=args.port)

    if args.cmd == "relay-pair":
        from karvyloop.relay.pairing import cmd_relay_pair
        return cmd_relay_pair(relay_url=args.relay_url, state_dir=args.dir, scope=args.scope)

    if args.cmd == "relay-unpair":
        from karvyloop.relay.pairing import cmd_relay_unpair
        return cmd_relay_unpair(target=args.target, state_dir=args.dir)

    if args.cmd == "devices":
        if args.remove:
            from karvyloop.mesh.cli import cmd_devices_remove
            return cmd_devices_remove(args.remove, yes=args.yes, state_dir=args.dir)
        from karvyloop.mesh.cli import cmd_devices
        return cmd_devices(label=args.label, state_dir=args.dir)

    if args.cmd == "mesh-sync":
        from karvyloop.mesh.sync_client import cmd_mesh_sync
        return cmd_mesh_sync(relay_url=args.relay, peer_room=args.peer_room,
                             fingerprint=args.fingerprint, code=args.code, state_dir=args.dir)

    if args.cmd == "remote":
        from karvyloop.relay.remote import cmd_remote
        return cmd_remote(relay_url=args.relay, rid=args.room, fingerprint=args.fingerprint,
                          request=args.request, code=args.code, state_dir=args.dir)

    # 管理面(role/domain/memory/skill/schedule/token)—— 名词-动词,命中即返回。
    if args.cmd in ("role", "domain", "memory", "skill", "schedule", "token"):
        rc = _dispatch_manage(args)
        if rc is not None:
            return rc

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
