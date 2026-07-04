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


def resolve_config_state_path(config_path: Optional[Path], no_llm: bool) -> str:
    """app.state.config_path 的唯一决定逻辑(闭环审计断① CRITICAL 修)。

    - 显式 --config → 用它。
    - 显式 --no-llm → ""(只读模式,有意不接配置写入)。
    - 其余 → **恒设**默认 `~/.karvyloop/config.yaml`,**不管文件在不在**。
      病根:此前"默认 config 已存在才设路径",纯新机器上默认 config 还不存在 →
      config_path="" → `/api/model/save` 拒绝写 key → 强制引导永远保存失败
      (主推安装路径 `karvyloop console` 的第一环断)。下游 `config_models._load/_save`
      本就处理文件不存在(load 返 {},save mkdir+写),恒设路径零风险。
    """
    if config_path:
        return str(config_path)
    if no_llm:
        return ""
    _default_cfg = Path.home() / ".karvyloop" / "config.yaml"
    try:
        _default_cfg.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[karvyloop console] 创建 ~/.karvyloop 目录失败(保存配置时会再试): {e}")
    return str(_default_cfg)


def _spend_card_to_proposal(card: dict):
    """把 spend_budget.build_card 产的**纯 dict 提醒卡** → Proposal(broadcast_proposal 要 duck type)。

    card = {kind, summary, proposal_id, payload}。补齐 Proposal 必填字段(options 三选一 / strength
    由 ratio 折算 / ts)。**纯提醒**:kind=spend_budget_alert 无兑现 handler(登记在 ALL_KINDS),
    用户 ACCEPT/REJECT 都只关卡。basis 用 summary(决策卡"为什么"区不空)。"""
    import time as _t
    from karvyloop.karvy.atoms import Proposal
    payload = dict(card.get("payload") or {})
    ratio = payload.get("ratio")
    try:
        strength = float(ratio) if ratio is not None else 0.9
    except (TypeError, ValueError):
        strength = 0.9
    return Proposal(
        summary=str(card.get("summary") or "花费提醒"),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=max(0.0, min(1.0, strength)),
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=_t.time(),
        kind=str(card.get("kind") or "spend_budget_alert"),
        payload=payload,
        proposal_id=str(card.get("proposal_id") or ""),
        basis=str(card.get("summary") or ""),
    )


def _make_spend_card_emitter(app):
    """构造 spend_budget 的 emit_card 回调:sync(card dict)→ 异步 broadcast_proposal(线程安全)。

    gateway 咽喉 check 可能跑在**主事件循环**(后台自动 tick)或 drive 的 **worker 线程**
    (asyncio.to_thread)。镜像 ws.py P4 的 run_coroutine_threadsafe 桥法:
    - 当前线程有 running loop → 直接 create_task(顺手缓存该 loop 供 worker 线程复用)。
    - 无 running loop(worker 线程)→ 用缓存的主 loop run_coroutine_threadsafe 桥回。
    fire-and-forget、fail-soft:桥不通只吞掉(预算是软护栏,出卡失败绝不拖垮真调用;block
    仍靠 gateway 抛 SpendBudgetExceeded fail-loud,不依赖这张卡)。spend_budget_alert 走
    allow_silence=False(提醒卡本身不该被"挣来的静音"再接管一层)。"""
    import asyncio as _asyncio

    def _emit(card: dict) -> None:
        try:
            from karvyloop.console.proposals import broadcast_proposal
            prop = _spend_card_to_proposal(card)

            async def _go():
                try:
                    await broadcast_proposal(app, prop, allow_silence=False)
                except Exception:
                    logger.debug("[budget] 出卡 broadcast 失败(不影响判定)", exc_info=True)

            try:
                loop = _asyncio.get_running_loop()
                app.state._spend_card_loop = loop   # 缓存供 worker 线程回桥
                loop.create_task(_go())
            except RuntimeError:
                loop = getattr(app.state, "_spend_card_loop", None)
                if loop is not None:
                    _asyncio.run_coroutine_threadsafe(_go(), loop)
                # else:主 loop 尚未起过任何异步广播 → 本次静默(极早期,罕见);下次有 loop 即恢复
        except Exception:
            logger.debug("[budget] emit_card 桥异常(静默)", exc_info=True)

    return _emit


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
    # 共创模式(docs/47):语音/TUI 渠道(GlobalKarvy)也接同一套共创会话态(app 晚于 workbench_app 构造,回挂)。
    try:
        workbench_app.attach_console_app(app)
    except Exception:
        pass
    # 9.4:存 config 路径,供 /api/lang 持久化语言偏好 + 模型管理(写回 config.yaml)。
    # 断①修:非 no_llm 时**恒设**默认路径(不管文件在不在)—— 纯新机器的强制引导才能真保存 key。
    app.state.config_path = resolve_config_state_path(config_path, no_llm)
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

    # === docs/56 审计 HIGH:花费预算刹车(spend brake)接进 console 启动路径 ===
    # 病根:`wire_spend_budget` 只在 CLI `_bootstrap_runtime` 挂且 emit_card=None —— 用户真跑的
    # web console 里预算达阈值只落日志、**永不出卡**(承诺的"能自刹的信任"在发布线不可见)。
    # 修:在 console 启动(gateway/ledger 接线后)显式 wire,并把 emit_card 接到 broadcast_proposal
    # → 达 75/90/100% 阈值真出一张 H2A 提醒卡。gateway 咽喉(client.complete 调用前)真 check
    # (前台永不拦,只拦达 100% 的后台自动烧钱路径,fail-loud)。未配 budget → 无刹车(0 回归)。
    try:
        _budget_reg = None
        _gw_for_budget = (resolved.runtime_kwargs or {}).get("gateway") if not no_llm else None
        if _gw_for_budget is not None:
            _budget_reg = getattr(_gw_for_budget, "reg", None)   # ModelRegistry(取每模型 cost 算真钱)
        _budget_cfg_path = getattr(app.state, "config_path", "") or None
        _emit_spend_card = _make_spend_card_emitter(app)
        from karvyloop.llm.spend_budget import wire_spend_budget
        _budget = wire_spend_budget(
            registry=_budget_reg, config_path=_budget_cfg_path, emit_card=_emit_spend_card)
        app.state.spend_budget = _budget
        if _budget is not None:
            logger.info("[karvyloop console] 花费预算刹车已接线(gateway 咽喉生效 + 达阈值出卡)")
    except Exception as e:
        logger.warning(f"[karvyloop console] 花费预算刹车接线失败(不影响启动,降级为无刹车): {e}")

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

    # === 邮件决策闭环(docs/43 ⑤a):出门在外靠自己的邮箱拍板 ===
    # 未配置 channels.email → build 返 None,零负担;配置了 → app.py 的 tick 循环
    # 发 digest + 轮询回批。决策落地与 REST /api/h2a_decide 同一条路(handlers + 决策信号)。
    try:
        from karvyloop.channels.email_channel import build_email_channel
        _preg = getattr(app.state, "proposal_registry", None)

        def _email_decide(pid: str, decision: str, _app=app, _reg=_preg):
            from karvyloop.console.decision_wire import record_decision_signals
            record_decision_signals(_app, decision=decision, proposal_id=pid,
                                    reason="(via email)", domain="", role="")
            return _reg.decide(pid, decision,
                               handlers=getattr(_app.state, "proposal_handlers", None) or {})

        app.state.email_channel = (
            build_email_channel(registry=_preg, decide=_email_decide,
                                config_path=str(config_path) if config_path else None)
            if _preg is not None else None)
        if app.state.email_channel is not None:
            logger.info("[karvyloop console] 邮件决策通道已接线(digest+回批)")
    except Exception as e:
        app.state.email_channel = None
        logger.warning(f"[karvyloop console] 邮件通道接线失败(不影响启动): {e}")

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
            # fail-loud(predict 页签靠这条链活着):接上打 info,接不上打 warning,不再静默。
            if main_loop is not None and hasattr(main_loop, "set_trace_funnel"):
                main_loop.set_trace_funnel(bundle.trace_index)
                logger.info("[karvyloop console] trace 漏斗已接线:drive 事件 → 原文层(predict 数据源)")
            elif main_loop is not None:
                logger.warning(
                    "[karvyloop console] main_loop 没有 set_trace_funnel — 漏斗未接线,"
                    "predict(你可能想做)只剩确定性兜底"
                )
            else:
                logger.warning(
                    "[karvyloop console] 无 main_loop(config 缺失/构造失败)— drive 漏斗未接线,"
                    "predict(你可能想做)只剩确定性兜底"
                )
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

                # Trace-conditioned 技能修订裁判(crystallize.revision,慢侧):同 lesson judge
                # 桥法(async judge_revision → sync)。daily tick 跑 ml.revision_review():
                # 客观信号差的技能 → LLM 修 Steps;小改自动落,大改出 revise_skill H2A 卡。
                if hasattr(main_loop, "set_revision_judge"):
                    def _revision_judge(material, _gw=_gw, _mref=_mref):
                        import asyncio
                        from karvyloop.crystallize.revision import judge_revision
                        try:
                            return asyncio.run(judge_revision(material, gateway=_gw, model_ref=_mref))
                        except Exception:
                            return ""
                    main_loop.set_revision_judge(_revision_judge)
                # 大改 H2A 卡出口 → 待决议表(无 sink 时 revision 只记 Trace 不落盘,绝不静默自动落)
                _preg = getattr(app.state, "proposal_registry", None)
                if _preg is not None and hasattr(main_loop, "set_revision_proposal_sink"):
                    main_loop.set_revision_proposal_sink(_preg.register)
            sys.stderr.write(
                (t("console.karvy_wired_on") if bundle.has_llm
                 else t("console.karvy_wired_off")) + "\n"
            )
            sys.stderr.flush()
        except Exception as e:
            # 接线失败不该阻断 console 启动(降级为"无主动建议")。
            # fail-loud:这条链断 = predict 页签死;打 error+堆栈,别再让它安静地空着。
            logger.error(
                f"[karvyloop console] 小卡意图分析接线失败(console 照常起,但 predict 建议链断): {e}",
                exc_info=True,
            )

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

    # === docs/43 第二级:Karvy 信使 relay(--relay)——「信使不拆信」 ===
    # console 出站 WSS 长连 relay(家里发不了入站、发得了出站);解密后的请求打回本机
    # loopback(下面这台 uvicorn),复用全部既有中间件 + token 门(深度防御:loopback 免
    # token 也照带)。收尾照 email_channel_task 先例:uvicorn 退出后 stop() 收线程。
    relay_handle = None
    relay_url = getattr(args, "relay", None)
    if relay_url:
        try:
            from karvyloop.relay.client import start_relay_client_thread
            relay_handle = start_relay_client_thread(
                relay_url, console_host="127.0.0.1", console_port=port, token=_token)
            sys.stderr.write(
                f"[karvyloop console] messenger relay client: outbound to {relay_url} "
                "(end-to-end encrypted; the relay only ever sees ciphertext). "
                "Pair devices with `karvyloop relay-pair`.\n")
            sys.stderr.flush()
        except Exception as e:
            # 用户显式要了 --relay → 起不来就诚实失败(缺 cryptography 时报 pip install karvyloop[relay])
            sys.stderr.write(f"[karvyloop console] --relay failed to start: {e}\n")
            sys.stderr.flush()
            return 1

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
    finally:
        if relay_handle is not None:      # --relay 收尾:停信使客户端线程(照 email task cancel 先例)
            relay_handle.stop()
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
    # docs/43 第二级:Karvy 信使 relay(文案暂英文硬编码,照 export 先例;i18n 表本任务不动)
    p_console.add_argument("--relay", type=str, default=None, metavar="WSS_URL",
                          help="connect out to a Karvy messenger relay (end-to-end encrypted; "
                               "the relay only sees ciphertext). Pair devices with `karvyloop relay-pair`; "
                               "needs `pip install karvyloop[relay]`")
    return p_console


__all__ = ["cmd_console", "build_console_parser", "resolve_config_state_path"]
