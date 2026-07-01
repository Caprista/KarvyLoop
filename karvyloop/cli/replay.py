"""karvyloop replay <task_id> — 读 trace 重放(M3+ 批 6)。

设计:plans/snoopy-singing-sunbeam.md §批 6。

职责:
- 从 `~/.karvyloop/trace.sqlite` 读指定 task_id 的所有 entries
- 按 NDJSON 印到 stdout(每行一个事件,JSON 序列化)
- 找不到时返非零退出码 + stderr 提示 + 列出已有 task_id(用户能 `replay <那个>`)

借(Q5):
- SqliteTraceStore(cognition/sqlite_trace.py)协议
- 标准 json 序列化

边界:
- 仅支持按 task_id 查;不接时间窗 / kind 过滤(M1 v1 简化)
- 不解密 / 不脱敏(trace payload 本来就不存 intent 原文 — 见 main_loop.py:221 不存 agent)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


def _default_trace_path() -> Path:
    return Path.home() / ".karvyloop" / "trace.sqlite"


def cmd_replay(
    task_id: str,
    *,
    trace_path: Optional[Path] = None,
    argv: Optional[Sequence[str]] = None,
) -> int:
    """karvyloop replay <task_id> 入口。

    Args:
        task_id: 要重放的 drive 任务 ID(uuid4 hex[:16])。
        trace_path: 自定义 trace.sqlite 路径(测试用)。
        argv: 给 main() 的 argv(测试用)。

    Returns:
        0=打印完;1=找不到该 task_id;2=trace.sqlite 不存在。
    """
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
        # NDJSON 输出
        for e in entries:
            sys.stdout.write(json.dumps({
                "task_id": e.task_id,
                "seq": e.seq,
                "kind": e.kind,
                "payload": e.payload,
                "ts": e.ts,
                "agent": e.agent,
                "source": e.source,
            }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        store.close()
        return 0
    except Exception:
        store.close()
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    """子入口(给 python -m karvyloop.cli.replay 用)。"""
    p = argparse.ArgumentParser(prog="karvyloop replay")
    p.add_argument("task_id", help="要重放的 drive 任务 ID")
    p.add_argument("--trace-path", type=str, default=None,
                   help="自定义 trace.sqlite 路径(默认 ~/.karvyloop/trace.sqlite)")
    args = p.parse_args(list(argv) if argv is not None else None)
    return cmd_replay(
        task_id=args.task_id,
        trace_path=Path(args.trace_path) if args.trace_path else None,
    )


if __name__ == "__main__":
    sys.exit(main())