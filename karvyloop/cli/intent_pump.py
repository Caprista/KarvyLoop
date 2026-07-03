"""intent_pump — 把小卡的 IntentAnalyst 接到 console 推送桥(M3+ 拍 9.0e 接线)。

设计:docs/20 §3.3.5 + docs/25 + plans/snoopy-singing-sunbeam.md。

**本拍 9.0e 职责**(9.0 真端到端最后一块):
- CLI/entry 是"知道两边的协调者"(docs/20 §3.10 私有桥接架构):
  - 它**可以** import 小卡私有 `karvyloop.karvy.atoms.IntentAnalyst`(深路径)
  - 它**可以** import 公共 `karvyloop.karvy.fastbrain.*`(快脑机制)
  - 它**可以** import `karvyloop.console.ProposalPump`(推送桥)
  - 它把这些拼起来,谁也不直接依赖谁(FB-5/FB-7 锁仍成立)
- 组装链:TraceIndex + HabitStore + BehaviorPatternAnalyzer(ProviderLlmClient) + IntentAnalyst → ProposalPump

**优雅退化**(CLAUDE.md §少脚手架 + 安全地基):
- config 不存在 / provider 构造失败 → llm_client=None → analyzer 静默(IntentAnalyst 仍可起,只不出 Proposal)
- 这让 `karvyloop console --no-llm` / 无配置场景下 console 照常起,只是"小卡暂不主动建议"

**灵魂铁律**:
- K7:IntentAnalyst 不参与 A2A(只读 trace/habit)— 本模块不破坏
- K5:本模块**不**碰 decision_to_envelope(只装 pump;决策走 routes/ws)
- 小卡私有:本模块是 OS bootstrap 协调者,不是"别的 agent 想用 intent 分析"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# 默认磁盘路径(同 ~/.karvyloop/ 根,跨设备拷整目录)
DEFAULT_TRACE_DB = Path.home() / ".karvyloop" / "trace_buffer.db"
DEFAULT_HABIT_DB = Path.home() / ".karvyloop" / "habits.db"


@dataclass
class PumpBundle:
    """组装结果:pump + 关闭钩子 + 是否接了 LLM(供日志/诊断)。"""
    pump: Any                       # console.ProposalPump
    close: Callable[[], None]       # 关闭 TraceIndex + HabitStore
    has_llm: bool                   # 是否接上 LLM(False = analyzer 静默)
    trace_index: Any
    habit_store: Any


def _try_build_llm_client(
    config_path: Optional[Path],
    *,
    gateway: Optional[Any] = None,
    model_ref: str = "",
) -> Optional[Any]:
    """尽力构造 LlmClientProtocol;任何失败返 None(优雅退化)。

    **优先复用已接好的 gateway**(`models.*` 单一真理来源)—— 修掉旧 `karvyloop.llm.config`
    认 `llm.*` schema、跟真实 config 对不上导致主动建议永空转的病(对齐"核真实数据形态")。
    没传 gateway 时回退旧 ProviderLlmClient 路径(向后兼容 --no-llm 之外的旧接线)。
    """
    if gateway is not None:
        try:
            from karvyloop.karvy.fastbrain.trace_habit import GatewayLlmClient
            return GatewayLlmClient(gateway, default_model_ref=model_ref)
        except Exception as e:
            logger.warning(
                f"[intent_pump] gateway LLM client 构造失败,退回旧 loader: {e}"
            )
    try:
        from karvyloop.llm.config import load_config
        from karvyloop.llm.provider import create_provider
        from karvyloop.karvy.fastbrain.trace_habit import ProviderLlmClient

        cfg = load_config(config_path) if config_path else load_config()
        provider = create_provider(cfg.default, cfg)
        return ProviderLlmClient(provider)
    except Exception as e:
        logger.warning(
            f"[intent_pump] LLM client 构造失败,小卡建议将静默(可正常起 console): {e}"
        )
        return None


def build_proposal_pump(
    app: Any,
    *,
    workbench: Any,
    config_path: Optional[Path] = None,
    trace_db: Optional[Path] = None,
    habit_db: Optional[Path] = None,
    global_config: Optional[dict] = None,
    strength_threshold: float = 0.7,
    gateway: Optional[Any] = None,
    model_ref: str = "",
) -> PumpBundle:
    """组装 IntentAnalyst + ProposalPump,接到 console app。

    Args:
        app: FastAPI app(ProposalPump 推 app.state.ws_clients)。
        workbench: WorkbenchObserver(IntentAnalyst 构造需要,K1 observer)。
        config_path: config.yaml 路径(None → 默认 ~/.karvyloop/config.yaml)。
        trace_db / habit_db: sqlite 路径(None → 默认 ~/.karvyloop/)。
        global_config: model_ref 解析的全局配置(per-agent 覆盖 → 默认)。
        strength_threshold: 念头强度阈值(超过才推 PROPOSE)。

    Returns:
        PumpBundle(pump + close + has_llm)。
    """
    from karvyloop.karvy.atoms import IntentAnalyst
    from karvyloop.karvy.fastbrain.trace_habit import (
        BehaviorPatternAnalyzer,
        resolve_model_ref,
    )
    from karvyloop.karvy.fastbrain.trace_index import TraceIndex
    from karvyloop.karvy.fastbrain.trace_habit import HabitStore
    from karvyloop.console import ProposalPump

    trace_index = TraceIndex(trace_db or DEFAULT_TRACE_DB)
    habit_store = HabitStore(habit_db or DEFAULT_HABIT_DB)

    llm_client = _try_build_llm_client(config_path, gateway=gateway, model_ref=model_ref)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm_client)

    # 主 loop 的默认模型(runtime model_ref)要进解析链的"全局默认"位:否则 resolve_model_ref
    # 落到硬编码 anthropic 兜底 —— 用户配的是 minimax/deepseek 时,analyst 打错 provider
    # → LLM 调用静默失败 → predict 永远沉默(2026-07-03 真跑确诊的第 3 死因)。
    if global_config is None and model_ref:
        global_config = {"default_model": model_ref}

    def _resolver(agent: str) -> Any:
        return resolve_model_ref(agent, global_config)

    analyst = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=analyzer,
        model_ref_resolver=_resolver,
        strength_threshold=strength_threshold,
    )

    # 修"predict 永远空"的真根因:drive 落的是 trace **原文**层,analyst 读**摘要**层,
    # 而 raw→summary 提炼器(trace_poll.distill_raw_to_summary)此前在生产路径无人调用
    # → 摘要层永远空 → analyst 永远沉默。这里把提炼器注入 pump,boot/daily 先提炼再分析。
    from karvyloop.karvy.fastbrain.trace_poll import distill_raw_to_summary

    def _distill():
        return distill_raw_to_summary(trace_index)

    pump = ProposalPump(app, analyst, distill=_distill)

    closed = {"done": False}

    def _close() -> None:
        if closed["done"]:
            return
        closed["done"] = True
        try:
            trace_index.close()
        except Exception:
            pass
        try:
            habit_store.close()
        except Exception:
            pass

    logger.info(
        f"[intent_pump] 装好:trace={trace_db or DEFAULT_TRACE_DB} "
        f"habit={habit_db or DEFAULT_HABIT_DB} llm={'on' if llm_client else 'off(静默)'}"
    )
    return PumpBundle(
        pump=pump,
        close=_close,
        has_llm=llm_client is not None,
        trace_index=trace_index,
        habit_store=habit_store,
    )


__all__ = [
    "DEFAULT_HABIT_DB",
    "DEFAULT_TRACE_DB",
    "PumpBundle",
    "build_proposal_pump",
]
