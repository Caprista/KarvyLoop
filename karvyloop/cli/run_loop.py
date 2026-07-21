"""karvyloop run ↔ MainLoop 接线层(M3+ 批 4a + 批 6 持久化)。

设计:plans/snoopy-singing-sunbeam.md §批 4 + §批 6。

职责(薄胶水,Q5 自造):
- build_main_loop:读 config.yaml → 决定 skills_dir → 构造 sqlite store/verify/trace
  → bootstrap 索引
- run_intent_via_loop:构造 slow_brain → ml.drive → 渲染 + stderr 北极星指标

借(已有):
- MainLoop / forge_slow_brain_factory / Renderer
- SqliteUsageStore / SqliteVerifyStore / SqliteTraceStore(批 6 新增,sqlite3 stdlib)
- recall / observe / maybe_promote / crystallize 全在 MainLoop.drive 内部

R3 关键:run_intent_via_loop 必须在**同步上下文**调(不嵌套 asyncio.run);
从 async 上下文调时,用户应用 `await asyncio.to_thread(run_intent_via_loop, ...)`。

M3+ 批 6 新增:
- 默认 store/verify/trace 走 Sqlite 后端,落盘到 `~/.karvyloop/{usage,verify,trace}.sqlite`
- 主循环退出前显式 close 释放文件锁
- 接受 `_usage_store_path` / `_verify_store_path` / `_trace_store_path` 注入(测试用)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from karvyloop.cognition import SqliteTraceStore
from karvyloop.crystallize import (
    SqliteUsageStore,
    SqliteVerifyStore,
)

from karvyloop.runtime.main_loop import Brain, MainLoop, forge_slow_brain_factory  # P2-f:核心循环已搬 runtime/
from .render import Renderer


def _read_skills_dir_from_config(config_path: Optional[Path]) -> Optional[Path]:
    """从 ~/.karvyloop/config.yaml 读 crystallize.skills_dir 字段(没填返 None)。"""
    if config_path is None or not config_path.exists():
        return None
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    cry = cfg.get("crystallize") or {}
    raw = cry.get("skills_dir")
    if not raw:
        return None
    return Path(str(raw)).expanduser()


def _read_thresholds_from_config(config_path: Optional[Path]):
    """从 config.yaml `crystallize.{min_usage_count, min_success_rate, usage_debounce_sec,
    promote_score, generalized_distinct, cluster_overlap_threshold, satisfaction_floor,
    satisfaction_min_samples}` 读结晶旋钮。缺字段 → 用默认值。

    覆盖 CrystallizeThresholds **全部**字段(内部审计半接线修):满意度关(docs/44 断⑭)
    的地板/样本门原来没从 config 读 → 用户改 `crystallize.satisfaction_floor` 静默无效。"""
    from karvyloop.crystallize.crystallize import CrystallizeThresholds, DEFAULT_THRESHOLDS
    if config_path is None or not config_path.exists():
        return DEFAULT_THRESHOLDS
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return DEFAULT_THRESHOLDS
    cry = cfg.get("crystallize") or {}
    d = DEFAULT_THRESHOLDS
    try:
        return CrystallizeThresholds(
            min_usage_count=int(cry.get("min_usage_count", d.min_usage_count)),
            min_success_rate=float(cry.get("min_success_rate", d.min_success_rate)),
            usage_debounce_sec=float(cry.get("usage_debounce_sec", d.usage_debounce_sec)),
            promote_score=float(cry.get("promote_score", d.promote_score)),
            generalized_distinct=int(cry.get("generalized_distinct", d.generalized_distinct)),
            cluster_overlap_threshold=float(cry.get("cluster_overlap_threshold", d.cluster_overlap_threshold)),
            satisfaction_floor=float(cry.get("satisfaction_floor", d.satisfaction_floor)),
            satisfaction_min_samples=int(cry.get("satisfaction_min_samples", d.satisfaction_min_samples)),
        )
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLDS  # 配置值非法 → 退默认,不崩


def build_main_loop(
    *,
    config_path: Optional[Path] = None,
    skills_dir: Optional[Path] = None,
    usage_store_path: Optional[Path] = None,
    verify_store_path: Optional[Path] = None,
    trace_store_path: Optional[Path] = None,
) -> MainLoop:
    """构造 + bootstrap MainLoop。

    路径优先级:
      - skills_dir:显式 > config.yaml `crystallize.skills_dir` > `~/.karvyloop/skills/`
      - usage_store_path:显式 > `~/.karvyloop/usage.sqlite`
      - verify_store_path:显式 > `~/.karvyloop/verify.sqlite`
      - trace_store_path:显式 > `~/.karvyloop/trace.sqlite`

    显式传 None 走 sqlite in-memory(:memory:);测试用。
    """
    resolved_skills = (
        Path(skills_dir).expanduser()
        if skills_dir
        else _read_skills_dir_from_config(config_path)
        or (Path.home() / ".karvyloop" / "skills")
    )
    resolved_usage = (
        Path(usage_store_path).expanduser()
        if usage_store_path is not None
        else (Path.home() / ".karvyloop" / "usage.sqlite")
    )
    resolved_verify = (
        Path(verify_store_path).expanduser()
        if verify_store_path is not None
        else (Path.home() / ".karvyloop" / "verify.sqlite")
    )
    resolved_trace = (
        Path(trace_store_path).expanduser()
        if trace_store_path is not None
        else (Path.home() / ".karvyloop" / "trace.sqlite")
    )

    store = SqliteUsageStore(resolved_usage)
    verify = SqliteVerifyStore(resolved_verify)
    trace = SqliteTraceStore(resolved_trace)

    ml = MainLoop(
        skills_dir=resolved_skills,
        store=store,
        verify=verify,
        trace=trace,
        thresholds=_read_thresholds_from_config(config_path),  # 9.4:结晶旋钮可配置
    )
    ml.bootstrap()
    return ml


def close_main_loop_stores(ml: MainLoop) -> None:
    """显式关闭 MainLoop 持有的 sqlite store(M3+ 批 6:cmd_run 退出前调)。

    拍 6 决策:不在 `MainLoop.__del__` 自动 close —— 长跑进程(chat/TUI)持有同一
    实例,频繁开关 sqlite 会触发 WAL 重建。exit 由 caller 决定。
    """
    for attr in ("store", "verify", "trace"):
        obj = getattr(ml, attr, None)
        close = getattr(obj, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _emit_stats_line(stats: Any, *, task_id: str = "") -> None:
    """北极星指标 stderr 单行(M1 验收门用)。M3+ 批 6:加 task_id 方便调试。"""
    if stats.drive_calls == 0:
        return
    rate = stats.fast_brain_hit_rate
    sys.stderr.write(
        f"[karvyloop] fast_brain_hit_rate={rate:.2f} "
        f"crystallizations={stats.crystallizations} "
        f"slow_brain_runs={stats.slow_brain_runs} "
        f"drive_calls={stats.drive_calls} "
        f"task_id={task_id}\n"
    )
    sys.stderr.flush()


def _terminal_note(terminal_value: str) -> str:
    """DriveResult.terminal(Terminal.value 字符串)→ 非正常终止的诚实提示;COMPLETED/空 → ""。

    复用 runtime.main_loop._annotate_terminal 的文案(工厂在 r.text 末尾追加的就是它)——
    流式路径正文已实时打过,这里只把那句 ⚠ 提示补上,不整段重打。
    """
    if not terminal_value:
        return ""
    from karvyloop.atoms.terminal import Terminal
    from karvyloop.runtime.main_loop import _annotate_terminal
    try:
        term = Terminal(terminal_value)
    except ValueError:
        return ""
    return _annotate_terminal("", term)


def run_intent_via_loop(
    intent: str,
    ml: MainLoop,
    *,
    token: Any,
    sandbox: Any,
    gateway: Any,
    workspace_root: str,
    model_ref: str = "",
    renderer: Optional[Renderer] = None,
) -> int:
    """跑一次主循环 + 渲染 + stats。

    R3 关键:必须在**同步上下文**调(不嵌套 asyncio.run)。
    forge_slow_brain_factory 内部用 asyncio.run 同步化 forge —— 若调用方
    已经在 asyncio loop 里,直接调会爆。async 调用方应用 asyncio.to_thread 包。
    """
    # I(内测 U-05):默认路径此前不接 renderer → 慢脑跑几分钟终端全程无声,结束才吐全文。
    # 修:renderer 透进 forge 工厂(与 --json 路径的 emitter 同源事件流,不另造格式)——
    # 工具调用一行(⚙ name)/文本流式/终止标记(✓ run …)全部实时可见。
    rdr = renderer or Renderer()
    _chars0, _tools0 = rdr.stats.text_chars, rdr.stats.tool_calls
    # 即时阶段提示(stderr):drive 起步到首个流事件之间也不哑(检索技能→调模型)。
    from karvyloop.i18n import t as _t
    sys.stderr.write(_t("cli.run.progress_start") + "\n")
    sys.stderr.flush()
    slow_brain = forge_slow_brain_factory(
        token=token, sandbox=sandbox, gateway=gateway,
        workspace_root=workspace_root, model_ref=model_ref,
        renderer=rdr,
    )
    r = ml.drive(intent, slow_brain=slow_brain)

    # 渲染
    if r.brain == Brain.FAST and r.skill_name:
        # 已有 Renderer.fast_brain_note(skill_name, saved_tokens) — 省 token 估算 P1
        rdr.fast_brain_note(r.skill_name, saved_tokens=0)
    else:
        streamed = (rdr.stats.text_chars > _chars0) or (rdr.stats.tool_calls > _tools0)
        if streamed:
            # 正文/工具行已实时流过 —— 别整段重打;只补非正常终止的诚实提示
            # (工厂把它追加在 r.text 末尾,但流式事件里没有)。
            note = _terminal_note(getattr(r, "terminal", "") or "")
            if note:
                rdr.render_text(note)
        elif r.text:
            rdr.render_text(r.text)

    # 北极星指标(M3+ 批 6:加 task_id 方便调试)
    _emit_stats_line(ml.stats, task_id=r.task_id)

    # 退出码:终止语义已上冒到 DriveResult.terminal(docs/02 §15,还清原 P1 债)。
    #   - 基础能力失效(infra-dead:模型/网络调不通)→ 明确非零(3),和"任务失败"(1)区分,
    #     好让脚本/上层据此 fail-loud 而非把它当任务问题反复重试。
    #   - 其余:有 text 视为成功(0),无 text 视为失败(1)。
    from karvyloop.atoms.terminal import is_infra_dead
    if is_infra_dead(r.terminal):
        return 3
    return 0 if r.text else 1


__all__ = ["build_main_loop", "run_intent_via_loop", "close_main_loop_stores"]
