"""entry — `karvyloop console` 子命令入口(M3+ 批 8.5-C-frontend)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

实做:抽 `_resolve_runtime`(走 `karvyloop/cli/_runtime.py`)→ 构造 FastAPI app
→ `uvicorn.run(...)` 起服务 → 0.5s 后(非 --no-browser)后台 `webbrowser.open`。

边界:CLAUDE.md 安全地基
- --host 默认 127.0.0.1
- 绑 0.0.0.0 时 **必须** stderr 警告
- 真实 API key 不进本模块(全走 config.yaml + _bootstrap_runtime)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


def _port_free(host: str, port: int) -> bool:
    """该端口现在能不能绑(探测;探测不了 → 当能绑,交给 uvicorn 决定)。

    **必须设 SO_REUSEADDR**:uvicorn 默认就是带 REUSEADDR 绑的,若这里不设,刚 `fuser -k`/重启时
    端口处于 TIME_WAIT 会被误判"占用"→ 无谓挪端口(实测 VM 重启后跑到了 8768)。设了之后:TIME_WAIT
    端口判为可绑(对,uvicorn 能绑),**活着的监听端口仍判占用**(SO_REUSEADDR 不让抢活监听)。
    """
    import os
    import socket
    test_host = "0.0.0.0" if host in ("", "::") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 只在 POSIX 设 SO_REUSEADDR(对齐 uvicorn/asyncio):Linux/Mac 上它=复用 TIME_WAIT(不抢活监听);
    # **Windows 上它=允许抢占同端口**(语义相反),设了会把"被占"误判成"空闲" → 千万别在 Windows 设。
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((test_host, port))
        return True
    except OSError:
        return False
    except Exception:
        return True   # IPv6-only host 之类探测不了 → 别挡
    finally:
        s.close()


def _next_free_port(host: str, port: int, *, limit: int = 20) -> int:
    """从 port+1 向上探 limit 个空闲端口;都占满 → 返回原 port(让 uvicorn 照旧报错,不静默吞)。"""
    for cand in range(port + 1, port + 1 + max(1, limit)):
        if _port_free(host, cand):
            return cand
    return port


def _probe_karvyloop_version(host: str, port: int, *, timeout: float = 0.6):
    """探测 host:port 上是不是 **KarvyLoop console**(GET /api/update_status 有 'current' 字段即是)。

    返回它的版本字符串;不是 KarvyLoop / 连不上 / 不是预期形状 → None。**关键**:用来区分
    "端口被外部进程占"(可安全挪端口)vs"被 KarvyLoop 自己占"(升级时旧版没退 / 已有实例 —— 这时
    **不能**静默挪到新端口,否则用户开 8766 看到旧版、新版偷偷在 8767,完全不知道)。
    """
    import json
    import urllib.request
    h = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with urllib.request.urlopen(f"http://{h}:{port}/api/update_status", timeout=timeout) as r:
            d = json.loads(r.read().decode())
        v = d.get("current")
        return str(v) if v else None
    except Exception:
        return None


def cmd_console(args: argparse.Namespace) -> int:
    """`karvyloop console` 入口(8.5-C-frontend 实做)。

    Args:
        args: argparse.Namespace 含 --config / --host / --port / --no-browser / --no-llm。

    Returns:
        exit code (0 成功,非 0 失败)。
    """
    host: str = args.host
    port: int = args.port
    config_path: Optional[Path] = Path(args.config) if args.config else None
    no_browser: bool = args.no_browser
    no_llm: bool = args.no_llm

    # === 9.4 双语:解析 UI locale(显式 --lang > env KARVYLOOP_LANG > config.yaml lang > en)===
    # i18n 是纯表现层 — 只决定用户看哪种语言,不碰任何业务逻辑。语言偏好持久在 config.yaml。
    from karvyloop.i18n import set_startup_locale, t
    from karvyloop.config_lang import read_lang
    set_startup_locale(explicit=getattr(args, "lang", None), config_lang=read_lang(config_path))

    # === CLAUDE.md 安全地基:绑 0.0.0.0 = LAN 暴露,stderr 警告 ===
    if host == "0.0.0.0":
        sys.stderr.write(t("console.lan_warning") + "\n")
        sys.stderr.flush()

    # === 9.5 P1:独立用户工作区(跟 KarvyLoop 源码隔离)===
    # 不再用 cwd(=源码树)当 workspace —— 那会让 agent 没写权限 + 读到 KarvyLoop 自己的
    # CLAUDE.md/CONTEXT 串身份。用户工作区默认 ~/karvyloop-work(env/config 可覆盖)。
    from karvyloop.config_workspace import resolve_workspace
    workspace_root = resolve_workspace(config_path)

    # === 共享 runtime 解析(Q5 借 — 与 cmd_chat 共用 _resolve_runtime)===
    from karvyloop.cli._runtime import resolve_runtime
    resolved = resolve_runtime(
        config_path=config_path,
        workspace_root=workspace_root,
    )

    # === 构造 FastAPI app(走 build_console_app,eager init app.state)===
    from karvyloop.console.app import build_console_app
    from karvyloop.domain import Address
    from karvyloop.karvy.observer import WorkbenchObserver

    workbench = WorkbenchObserver()
    user_address = Address(domain_id="dom-1", role="user", agent_id="ch")

    # --no-llm 模式:故意不注入 main_loop(让 console 跑只读视图 + chat_history)
    main_loop = None if no_llm else resolved.main_loop
    runtime_kwargs = {} if no_llm else resolved.runtime_kwargs

    # 构造 workbench_app(workbench 用 workbench_app.get_chat_history() 拉历史)
    from karvyloop.workbench.app import WorkbenchApp
    workbench_app = WorkbenchApp(
        workbench=workbench,
        user_address=user_address,
        main_loop=main_loop,
        runtime_kwargs=runtime_kwargs,
    )

    app = build_console_app(
        workbench=workbench,
        main_loop=main_loop,
        runtime_kwargs=runtime_kwargs,
        workbench_app=workbench_app,
    )
    # 9.4:存 config 路径,供 /api/lang 持久化语言偏好 + 模型管理(写回 config.yaml)。
    # 未显式 --config 但已加载默认配置(非 no_llm)→ 记下**默认路径**,
    # 否则 app.state.config_path 为空会让模型/语言管理误判"无配置"(VM 实测踩到)。
    if config_path:
        app.state.config_path = str(config_path)
    elif not no_llm:
        from pathlib import Path as _CfgP
        _default_cfg = _CfgP.home() / ".karvyloop" / "config.yaml"
        app.state.config_path = str(_default_cfg) if _default_cfg.exists() else ""
    else:
        app.state.config_path = ""
    # 无 Key 强制引导(/api/setup_status):记下是不是用户**显式** --no-llm
    # (显式只读模式不强制录模型;非 no_llm 但无可用 key → 网页强制引导)。
    app.state.no_llm = bool(no_llm)

    # === 9.3a:接线 token 账本(测量层,全局注册;每次 LLM 调用记一条)===
    try:
        from pathlib import Path as _PathTok
        from karvyloop.llm.token_ledger import TokenLedger, register_ledger
        token_ledger = TokenLedger(_PathTok.home() / ".karvyloop" / "tokens.db")
        register_ledger(token_ledger)
        app.state.token_ledger = token_ledger
    except Exception as e:
        logger.warning(f"[karvyloop console] token 账本接线失败(不影响启动): {e}")

    # === 9.4-B3a(D5):接线 PROPOSE 待决议表 ===
    # 小卡推建议前先登记(broadcast_proposal 内);用户 ACCEPT 凭 proposal_id 查回按 kind 兑现。
    # handlers 默认空(诚实:结构修好了 — 有 proposal_id + 有消费路径 + ACCEPT 回显 dispatch;
    # 各 kind 的真副作用 handler 随子系统成熟逐个 register,不为对称而假兑现)。
    try:
        from pathlib import Path as _PathProp
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry
        from karvyloop.console.proposal_handlers import build_proposal_handlers
        # P1-c:落盘 → 待决卡(含 DEFER 挂起的)跨重启存活,决策 loop 不因重启丢板。
        app.state.proposal_registry = PendingProposalRegistry(
            persist_path=_PathProp.home() / ".karvyloop" / "pending_proposals.json"
        )
        # 门2(D5 live):注册有真实目的地的 kind handler(crystallize_skill 采纳确认);
        # 其余 kind 靠 registry 默认诚实回执,接线随子系统成熟补(docs/30 §5.1)。
        app.state.proposal_handlers = build_proposal_handlers(app)
    except Exception as e:
        logger.warning(f"[karvyloop console] PROPOSE 待决议表接线失败(不影响启动): {e}")

    # === 9.0e:接线小卡 IntentAnalyst → console 推送桥 ===
    # 小卡跟着 console 一起起,每天后台看一次行为,够强的建议弹到 H2A 列。
    # --no-llm 时跳过(无 LLM analyzer 会静默,接了也不出建议,省开销)。
    pump_trace_index = None  # 9.1d:供对话编排器复用做 CV-4 衔接
    if not no_llm:
        try:
            from karvyloop.cli.intent_pump import build_proposal_pump
            from karvyloop.karvy.fastbrain.trace_poll import DAILY_POLL_INTERVAL_S

            bundle = build_proposal_pump(
                app,
                workbench=workbench,
                config_path=config_path,
                # 复用主 loop 已接好的 gateway(models.* 单一真理来源)→ 修主动建议永空转。
                gateway=runtime_kwargs.get("gateway"),
                model_ref=runtime_kwargs.get("model_ref", "") or "",
            )
            app.state.proposal_pump = bundle.pump
            app.state.proposal_close = bundle.close
            app.state.proposal_daily_interval_s = DAILY_POLL_INTERVAL_S
            pump_trace_index = bundle.trace_index
            # 9.3c(修 D1):MainLoop 把每次 drive 事件落进**共享**漏斗原文层
            # → 提炼器异步 原文→摘要→习惯(与 IntentAnalyst 同一 TraceIndex)
            if main_loop is not None and hasattr(main_loop, "set_trace_funnel"):
                main_loop.set_trace_funnel(bundle.trace_index)
            # §14.2 / docs/40 §3 慢侧 atom 质量裁判:复用已接好的 gateway,把 judge_quality(async)
            # 桥成同步注入 MainLoop;daily_poll 跑 ml.quality_review() → 读 Trace 里已确定性评、做对站住
            # 的 run,LLM 评质量补到样本(跑评分离:绝不在 drive 热路径,只在每日慢侧 tick)。
            _gw = runtime_kwargs.get("gateway")
            if (main_loop is not None and _gw is not None
                    and hasattr(main_loop, "set_atom_quality_judge")):
                _mref = runtime_kwargs.get("model_ref", "") or ""

                def _atom_quality_judge(intent, output_text, _gw=_gw, _mref=_mref):
                    import asyncio
                    from karvyloop.crystallize.atom_critic import judge_quality
                    try:
                        return asyncio.run(judge_quality(intent, output_text,
                                                         gateway=_gw, model_ref=_mref))
                    except Exception:
                        return (None, "")

                main_loop.set_atom_quality_judge(_atom_quality_judge)

                # docs/40 §6 丙 跨-run 经验蒸馏裁判(更慢一档):对比同子目标的满意/不满意执行 → 规律。
                if hasattr(main_loop, "set_lesson_judge"):
                    def _lesson_judge(material, _gw=_gw, _mref=_mref):
                        import asyncio
                        from karvyloop.crystallize.lessons import judge_lesson
                        try:
                            return asyncio.run(judge_lesson(material, gateway=_gw, model_ref=_mref))
                        except Exception:
                            return ""
                    main_loop.set_lesson_judge(_lesson_judge)
            sys.stderr.write(
                (t("console.karvy_wired_on") if bundle.has_llm
                 else t("console.karvy_wired_off")) + "\n"
            )
            sys.stderr.flush()
        except Exception as e:
            # 接线失败不该阻断 console 启动(降级为"无主动建议")
            logger.warning(f"[karvyloop console] 小卡意图分析接线失败(console 照常起): {e}")

    # === 9.1d:接线对话编排器(ConversationManager)===
    # 续上最近一段(CV-6),旧对话开新时摘要喂 Trace(CV-4,复用 pump 的 trace_index)。
    try:
        from pathlib import Path as _Path
        from karvyloop.cognition.conversation import ConversationManager, ConversationStore

        conv_store = ConversationStore(_Path.home() / ".karvyloop" / "conversations")
        # 9.2b/9.2c:domain_registry 供 /api/peers 列业务域 + /api/domain/create 建域 + CV-14 注入 value.md。
        # 0.1.0:进程内 registry(本会话建的域可用可对话);**域定义持久化 = P1**
        # (对话文件本身照常持久;重启后 registry 空 → 旧业务域对话 governance 优雅退化为空)。
        try:
            from karvyloop.domain.registry import BusinessDomainRegistry
            from karvyloop.domain.store import DomainStore
            domain_registry = BusinessDomainRegistry()
            # 9.2c-持久化:重启加载已建业务域(保留原 id → 旧对话仍对得上)
            domain_store = DomainStore(_Path.home() / ".karvyloop" / "domains.json")
            for d in domain_store.load_all():
                domain_registry.restore(d)
            app.state.domain_store = domain_store
        except Exception as e:
            logger.warning(f"[karvyloop console] domain_registry 构造失败(仅私聊): {e}")
            domain_registry = None
        app.state.domain_registry = domain_registry
        # 9.5 #3-P1:公共原子库 + 角色库(镜像 CRUD)→ 左导航管理面。持久在 ~/.karvyloop/。
        try:
            from karvyloop.atoms.registry import AtomRegistry, AtomStore
            from karvyloop.roles.registry import RoleRegistry
            atom_registry = AtomRegistry(store=AtomStore(_Path.home() / ".karvyloop" / "atoms.json"))
            # skills_dir 注入 → 角色引用技能时校验"用不拥有"(技能须已在库;扫盘兜底,无需索引)
            _ml = getattr(app.state, "main_loop", None)
            role_registry = RoleRegistry(
                _Path.home() / ".karvyloop" / "roles", atom_registry=atom_registry,
                skills_dir=_Path.home() / ".karvyloop" / "skills",
                skill_index=getattr(_ml, "skill_index", None),
            )
        except Exception as e:
            logger.warning(f"[karvyloop console] atom/role registry 构造失败: {e}")
            atom_registry = None
            role_registry = None
        app.state.atom_registry = atom_registry
        app.state.role_registry = role_registry
        # 9.5 P2/step2:任务看板登记 + 落盘(重启记得住;running 中断标 interrupted)
        from karvyloop.console.tasks import TaskRegistry, TaskStore
        app.state.task_registry = TaskRegistry(
            store=TaskStore(_Path.home() / ".karvyloop" / "tasks.json"),
        )
        # §0.7 fail-loud:start/finish → 自动 push task_status 给 WS clients(状态即事件,
        # 不靠前端 2s 轮询)。结构性保证:所有调 start/finish 的路径都推,含未来新增。
        # P3-b 跑评分离:同一接缝把任务终态(done/error)补进 Trace(评价唯一数据源)——
        # 此前任务结果只进 tasks.json,评价飞轮永远看不见任务级成败(两本账)。
        from karvyloop.console.task_events import make_task_change_sink
        app.state.task_registry.on_change = make_task_change_sink(
            app, getattr(getattr(app.state, "main_loop", None), "trace", None))
        # §11 MVP 复利信号:记 H2A 决策结果 → 算"提案接受率"趋势(越用越懂你的可测证据)
        from karvyloop.console.decision_stats import DecisionStats
        app.state.decision_stats = DecisionStats(
            path=_Path.home() / ".karvyloop" / "decision_stats.json",
        )
        # 最近拍板流水(只读回看)—— 拍完从待决列消失,但人能回看自己拍过什么。落盘。
        from karvyloop.console.decision_log import DecisionLog, RevocationStore
        app.state.decision_log = DecisionLog(
            path=_Path.home() / ".karvyloop" / "decision_log.json",
        )
        # fs_grants:工作区外访问授权台账(敏感路径硬地板;工具层/沙箱/能力链共用全局注册)
        from karvyloop.capability.fs_grants import FsGrantsStore, register_store as _reg_fs
        app.state.fs_grants = FsGrantsStore(_Path.home() / ".karvyloop" / "fs_grants.json")
        _reg_fs(app.state.fs_grants)
        # 口味命中率:押注/对账存储(前瞻预测,"越用越像你"的可证明刻度)。落盘。
        from karvyloop.crystallize.taste_eval import TastePredictionStore
        app.state.taste_predictions = TastePredictionStore(
            _Path.home() / ".karvyloop" / "taste_predictions.json")
        # 撤回抑制:你撤过的偏好,冷却窗口内别自动结晶回来(让"撤回"有牙)。落盘=跨重启算数。
        app.state.decision_revocations = RevocationStore(
            path=_Path.home() / ".karvyloop" / "decision_revoked.json",
        )
        # loop step4b 地基:个人知识库 = 活的、落盘的 Belief 长期库(重启不丢)。
        # 摄入编译(4b-1)/对话蒸馏(后续)写进它,drive 前从它召回注入上下文。
        from karvyloop.cognition.memory import MemoryManager
        from karvyloop.cognition.belief_store import BeliefStore
        app.state.memory = MemoryManager(
            store=BeliefStore(_Path.home() / ".karvyloop" / "beliefs.json"),
        )
        conv_mgr = ConversationManager(
            conv_store, trace_index=pump_trace_index, domain_registry=domain_registry,
        )
        conv_mgr.start()  # 默认续上最近一段私聊(CV-6,静默)
        app.state.conversation_manager = conv_mgr
        cur = conv_mgr.current()
        n = cur.turn_count if cur else 0
        sys.stderr.write(t("console.conv_ready", n=n) + "\n")
        sys.stderr.flush()
    except Exception as e:
        logger.warning(f"[karvyloop console] 对话编排器接线失败(console 照常起): {e}")

    # === 端口被占的处理(放在开浏览器/opening/uvicorn 之前,三者都用真实端口)===
    # 撞端口不挡用户,但**要区分**:外部进程占 → 安全挪到下一个空闲端口;KarvyLoop 自己占
    # (升级时旧版没退 / 已有实例)→ **绝不静默挪**(否则用户开 8766 看旧版、新版偷偷在别处),如实告知 + 退出。
    _req_port = port
    if not _port_free(host, port):
        _running = _probe_karvyloop_version(host, port)
        if _running is not None:
            from karvyloop import __version__ as _ver
            _bh = "localhost" if host in ("0.0.0.0", "::", "") else host
            _url = f"http://{_bh}:{port}/"
            if str(_running) == str(_ver):
                sys.stderr.write(t("console.already_running", url=_url, ver=_running) + "\n")
            else:   # 旧版还占着 → 升级未生效,提示先停旧版
                sys.stderr.write(t("console.old_running", url=_url, old=_running, new=_ver) + "\n")
            sys.stderr.flush()
            return 0
        # 占用者是外部进程 → 安全挪到下一个空闲端口
        port = _next_free_port(host, port)
        if port != _req_port:
            sys.stderr.write(t("console.port_fallback", orig=_req_port, port=port) + "\n")
            sys.stderr.flush()

    # === 记下"怎么重启自己"(一键升级用:升级 runner 装完后照这个把 console 拉起来)===
    # **必须走 sys.executable + `-m karvyloop`**,不能用 sys.argv[0]:若 console 是 `python -m` 启动的,
    # argv[0] 是 .py 路径、不可直接 exec → 重启失败=把用户装坏(独立对抗验收 D1)。解释器永远可执行。
    _relaunch = [sys.executable, "-m", "karvyloop", "console",
                 "--host", host, "--port", str(port), "--no-browser"]
    if no_llm:
        _relaunch.append("--no-llm")
    if config_path:
        _relaunch += ["--config", str(config_path)]
    if getattr(args, "lang", None):
        _relaunch += ["--lang", str(args.lang)]
    app.state.console_relaunch = {"argv": _relaunch, "host": host, "port": port}

    # === 访问令牌:本机 loopback 免密,跨设备必须带 token。每次启动**新生成**(重启即刷新)。===
    # 写进 ~/.karvyloop/console.runtime.json(0600),`karvyloop url` 可随时取当前带 token 的链接。
    from karvyloop.console import access as _access
    _token = _access.new_token()
    app.state.access_token = _token
    try:
        _access.write_runtime(_token, host, port)
    except Exception as e:
        logger.warning(f"[karvyloop console] 写 access token runtime 失败: {e}")
    _urls = _access.access_urls(host, port, _token)
    if _urls["remote"]:   # 绑了非 loopback → 打印跨设备带 token 链接(本机 localhost 仍免密)
        sys.stderr.write(t("console.remote_url", url=_urls["remote"]) + "\n")
        # 稍后再取链接:给能用的命令形式 —— `karvyloop` 在 PATH 就用它,否则用 `python -m karvyloop`(永远可用)。
        import shutil as _shutil
        _cli = "karvyloop" if _shutil.which("karvyloop") else f"{Path(sys.executable).name} -m karvyloop"
        sys.stderr.write(t("console.url_hint", cmd=_cli) + "\n")
        sys.stderr.flush()

    # === 自动开浏览器(非 --no-browser 时,后台 thread 0.5s 后 open)===
    if not no_browser:
        # 绑 0.0.0.0/::(LAN 可达)时浏览器**不能**导航到 0.0.0.0 → 开 localhost(同机可达)
        browser_host = "localhost" if host in ("0.0.0.0", "::", "") else host
        url = f"http://{browser_host}:{port}/"
        def _open_browser():
            import webbrowser
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.warning(f"自动开浏览器失败({url}):{e} — 手动访问即可")
        threading.Timer(0.5, _open_browser).start()
        sys.stderr.write(t("console.opening", url=url) + "\n")
        sys.stderr.flush()

    # === 起 uvicorn(阻塞)===
    try:
        import uvicorn
    except ImportError as e:
        sys.stderr.write(t("console.uvicorn_missing", error=e) + "\n")
        return 1

    logger.info(
        f"[karvyloop console] starting uvicorn on {host}:{port} "
        f"(main_loop={'injected' if main_loop else 'none'})"
    )
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("[karvyloop console] interrupted — shutting down")
    except OSError as e:
        # E.g. port already in use
        sys.stderr.write(t("console.bind_failed", error=e) + "\n")
        return 1
    return 0


def build_console_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """`karvyloop console` 子命令 parser。

    help 文案走 i18n(默认 en);解析 `--help` 时尚未消费 `--lang`,故 help 文案
    用**当前生效 locale**(env KARVYLOOP_LANG / 默认 en),足够覆盖"装好默认英文"。
    """
    from karvyloop.i18n import t
    p_console = sub.add_parser("console", help=t("cli.help.console"))
    p_console.add_argument("--config", type=str, default=None,
                          help=t("cli.help.console.config"))
    p_console.add_argument("--host", type=str, default="127.0.0.1",
                          help=t("cli.help.console.host"))
    p_console.add_argument("--port", type=int, default=8766,
                          help=t("cli.help.console.port"))
    p_console.add_argument("--no-browser", action="store_true",
                          help=t("cli.help.console.no_browser"))
    p_console.add_argument("--no-llm", action="store_true",
                          help=t("cli.help.console.no_llm"))
    p_console.add_argument("--lang", type=str, default=None,
                          help=t("cli.help.lang"))
    return p_console


__all__ = ["cmd_console", "build_console_parser"]
