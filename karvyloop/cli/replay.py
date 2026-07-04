"""karvyloop replay <task_id> — 读 trace 重放(M3+ 批 6;可观测性③加 --run 过滤)。

设计:plans/snoopy-singing-sunbeam.md §批 6。

职责:
- 从 `~/.karvyloop/trace.sqlite` 读指定 task_id 的所有 entries
- 或(可观测性③)`--run <run_id>`:只输出该 run 的条目(run_id 由 drive 入口的
  run_scope 生成、随 contextvar 全链盖进 Trace,见 cognition/trace.py)
- 按 NDJSON 印到 stdout(每行一个事件,JSON 序列化);--run 时**stderr** 尾部
  加一行该 run 的摘要(条数/时长/token —— 全部从现有数据算,不新增记账;
  放 stderr 是为了 stdout 维持纯 NDJSON 可管道)
- 找不到时返非零退出码 + stderr 提示 + 列出已有 task_id(用户能 `replay <那个>`)

借(Q5):
- SqliteTraceStore(cognition/sqlite_trace.py)协议
- TokenLedger 只读查询(llm/token_ledger.py,--run 摘要的 token 数)
- 标准 json 序列化

边界:
- 不接时间窗 / kind 过滤(M1 v1 简化)
- 不解密 / 不脱敏(trace payload 本来就不存 intent 原文 — 见 main_loop.py 不存 agent)
- 老库(无 run_id 列)自动迁移;老记录 run_id="" 不属于任何 run(诚实,不猜)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


def _default_trace_path() -> Path:
    return Path.home() / ".karvyloop" / "trace.sqlite"


def _default_tokens_path() -> Path:
    return Path.home() / ".karvyloop" / "tokens.db"


def _emit_ndjson(entries) -> None:
    for e in entries:
        sys.stdout.write(json.dumps({
            "task_id": e.task_id,
            "seq": e.seq,
            "kind": e.kind,
            "payload": e.payload,
            "ts": e.ts,
            "agent": e.agent,
            "source": e.source,
            "run_id": getattr(e, "run_id", "") or "",
        }, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _run_summary_line(run_id: str, entries, tokens_path: Optional[Path]) -> str:
    """--run 摘要:条数/时长/token。全部从现有数据只读汇总(不新增记账);算不出的字段诚实省略。"""
    n = len(entries)
    ts_vals = [e.ts for e in entries if e.ts]
    dur = (max(ts_vals) - min(ts_vals)) if len(ts_vals) >= 2 else 0.0
    parts = [f"run={run_id}", f"entries={n}", f"duration={dur:.1f}s"]
    tpath = tokens_path if tokens_path is not None else _default_tokens_path()
    try:
        if tpath.exists():
            from karvyloop.llm.token_ledger import TokenLedger
            led = TokenLedger(tpath)
            try:
                tot = led.run_totals(run_id)
            finally:
                led.close()
            if tot["calls"]:   # 账本里没这个 run 的行 → 诚实省略,不谎报 tokens=0
                parts.append(f"tokens={tot['total']} (in={tot['input']} out={tot['output']} calls={tot['calls']})")
    except Exception:
        pass  # 摘要是增益,账本读不了不拦重放
    return "[karvyloop replay] " + " ".join(parts) + "\n"


def cmd_replay(
    task_id: str = "",
    *,
    run_id: str = "",
    trace_path: Optional[Path] = None,
    tokens_path: Optional[Path] = None,
    argv: Optional[Sequence[str]] = None,
) -> int:
    """karvyloop replay [task_id] [--run run_id] 入口。

    Args:
        task_id: 要重放的 drive 任务 ID(uuid4 hex[:16])。--run 给了时可省。
        run_id: 只输出该 run 的条目(可观测性③;与 task_id 同给 = 两个条件都要满足)。
        trace_path: 自定义 trace.sqlite 路径(测试用)。
        tokens_path: 自定义 tokens.db 路径(--run 摘要的 token 数;测试用)。
        argv: 给 main() 的 argv(测试用)。

    Returns:
        0=打印完;1=找不到条目;2=trace.sqlite 不存在;3=task_id 和 --run 都没给。
    """
    if not task_id and not run_id:
        sys.stderr.write("[karvyloop replay] 需要 task_id 或 --run <run_id>(至少一个)。\n")
        return 3
    path = Path(trace_path) if trace_path is not None else _default_trace_path()
    if not path.exists():
        sys.stderr.write(
            f"[karvyloop replay] trace store not found at {path}\n"
            f"提示:跑一次 karvyloop run \"...\" 后再 replay,或显式 --trace-path。\n"
        )
        return 2

    # 延迟 import:不在 init 时强制 import sqlite 路径(给 headless 测试留缝)
    from karvyloop.cognition import SqliteTraceStore

    store = SqliteTraceStore(path)
    try:
        if run_id:
            entries = store.query_run(run_id)
            if task_id:
                entries = [e for e in entries if e.task_id == task_id]
            if not entries:
                sys.stderr.write(
                    f"[karvyloop replay] no entries for run_id={run_id}"
                    + (f" (task_id={task_id})" if task_id else "") + "\n"
                )
                store.close()
                return 1
            _emit_ndjson(entries)
            sys.stderr.write(_run_summary_line(run_id, entries, tokens_path))
            store.close()
            return 0
        entries = store.query(task_id)
        if not entries:
            sys.stderr.write(
                f"[karvyloop replay] no entries for task_id={task_id}\n"
            )
            available = store.all_tasks()
            if available:
                sys.stderr.write(f"已有 task_id(最近 {min(len(available), 10)} 个):\n")
                for tid in available[-10:]:
                    sys.stderr.write(f"  - {tid}\n")
                sys.stderr.write("用法:karvyloop replay <task_id>\n")
            store.close()
            return 1
        _emit_ndjson(entries)
        store.close()
        return 0
    except Exception:
        store.close()
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    """子入口(给 python -m karvyloop.cli.replay 用)。"""
    p = argparse.ArgumentParser(prog="karvyloop replay")
    p.add_argument("task_id", nargs="?", default="", help="要重放的 drive 任务 ID(--run 给了可省)")
    p.add_argument("--run", dest="run_id", type=str, default="",
                   help="只输出该 run 的条目(run_id 见 Trace/token 账本的 run_id 字段)")
    p.add_argument("--trace-path", type=str, default=None,
                   help="自定义 trace.sqlite 路径(默认 ~/.karvyloop/trace.sqlite)")
    args = p.parse_args(list(argv) if argv is not None else None)
    return cmd_replay(
        task_id=args.task_id,
        run_id=args.run_id,
        trace_path=Path(args.trace_path) if args.trace_path else None,
    )


if __name__ == "__main__":
    sys.exit(main())
