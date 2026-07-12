"""mesh/store — MeshLog 持久化(append-only JSONL,接真实的第一块地基,docs/74 §5.2/§6)。

共享日志要能**跨重启存活 + 可重载/可同步**,才谈得上"设备重连追上""认知在设备间共享"。
append-only → **JSONL**(一事件一行,追加高效,无需每次全量重写);防御式读(坏行跳过,不阻塞,
同 Trace append-only 纪律)。物化视图(记忆库/技能库/任务态)从加载的事件重建。

**去重在 MeshLog.merge**(按 event_id):同一事件被重复 append(重启+同步)不会二次生效。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List, Optional

from karvyloop.mesh.synclog import MeshEvent, MeshLog

STATE_FILE = "mesh_log.jsonl"


def _default_dir() -> Path:
    return Path.home() / ".karvyloop"


class MeshLogStore:
    """MeshLog 的 JSONL 持久化。base_dir 可注入(测试 tmp)。"""

    def __init__(self, base_dir: "Optional[Path | str]" = None) -> None:
        self.dir = Path(base_dir) if base_dir else _default_dir()

    @property
    def path(self) -> Path:
        return self.dir / STATE_FILE

    def append(self, events: Iterable[MeshEvent]) -> int:
        """把新事件追加进 JSONL(一行一个);返回写入条数。空 → 不碰盘。"""
        evs = list(events or [])
        if not evs:
            return 0
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for ev in evs:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        if os.name != "nt" and self.path.exists():
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        return len(evs)

    def load_events(self) -> List[MeshEvent]:
        """读全部持久化事件(坏行防御式跳过,不阻塞——append-only 不该被坏数据卡住)。"""
        out: List[MeshEvent] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except Exception:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(MeshEvent.from_dict(json.loads(line)))
            except Exception:
                continue                        # 坏行跳过(半写/损坏),不阻塞加载
        return out

    def open_log(self, device_id: str) -> MeshLog:
        """建一个 MeshLog(本设备 id)并把持久化事件 merge 进去(去重 + 推进 HLC 到已加载之后)。"""
        log = MeshLog(device_id)
        evs = self.load_events()
        # merge 的 wall 取已加载事件的最大墙钟(保证本地时钟不早于任何已存事件;无需外部 now)。
        wall = max((e.hlc.wall for e in evs), default=0)
        log.merge(evs, wall=wall)
        return log

    def persist_new(self, log: MeshLog) -> int:
        """把 log 里盘上还没有的事件补写进 JSONL(幂等:按 event_id 只写新的)。返回新写条数。"""
        have = {e.event_id for e in self.load_events()}
        new = [e for e in log.entries() if e.event_id not in have]
        return self.append(new)


__all__ = ["MeshLogStore", "STATE_FILE"]
