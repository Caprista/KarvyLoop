"""UsageStore — 使用统计的存储抽象（crystallize/store.py）。

M1 v1:纯内存 dict 实现,接口先稳定下来;sqlite 持久化后置(不挡楔子路径)。

约束(§4 保守可逆):
  - 归档不是删除:store 保留 archived 字段,evict 只翻标记位
  - 用 copy/update,不动外部传入的 UsageStats 对象
  - 并发安全:加锁;M1 v1 用 threading.Lock(observe/evict/recall 都在
    后台 review loop 同一线程,锁只是 fail-closed 兜底)
"""

from __future__ import annotations

import threading
import time
from typing import Iterator, Optional

from karvyloop.schemas import UsageStats


# 60s 去抖常量(spec 引用 USAGE_DEBOUNCE_MS=60_000)
USAGE_DEBOUNCE_SEC = 60.0


class UsageStore:
    """抽象接口。M1 v1 仅给内存实现;sqlite 后置。"""

    def get_or_create(self, sig: str) -> UsageStats:
        raise NotImplementedError

    def get(self, sig: str) -> Optional[UsageStats]:
        raise NotImplementedError

    def put(self, sig: str, stats: UsageStats) -> None:
        raise NotImplementedError

    def archive(self, sig: str) -> None:
        raise NotImplementedError

    def restore(self, sig: str) -> None:
        raise NotImplementedError

    def is_archived(self, sig: str) -> bool:
        raise NotImplementedError

    def all(self) -> Iterator[tuple[str, UsageStats]]:
        raise NotImplementedError

    def recall_count_inc(self, sig: str) -> None:
        raise NotImplementedError


class InMemoryUsageStore(UsageStore):
    """纯内存实现。"""

    def __init__(self, *, clock=time.time) -> None:
        self._data: dict[str, UsageStats] = {}
        self._archived: set[str] = set()
        self._lock = threading.Lock()
        self._clock = clock

    def get_or_create(self, sig: str) -> UsageStats:
        with self._lock:
            if sig not in self._data:
                self._data[sig] = UsageStats()
            return self._data[sig]

    def get(self, sig: str) -> Optional[UsageStats]:
        with self._lock:
            return self._data.get(sig)

    def put(self, sig: str, stats: UsageStats) -> None:
        with self._lock:
            self._data[sig] = stats

    def archive(self, sig: str) -> None:
        with self._lock:
            if sig in self._data:
                self._archived.add(sig)

    def restore(self, sig: str) -> None:
        """evict 可逆的关键:从归档集合里取出来,UsageStats 保留。"""
        with self._lock:
            self._archived.discard(sig)

    def is_archived(self, sig: str) -> bool:
        with self._lock:
            return sig in self._archived

    def all(self) -> Iterator[tuple[str, UsageStats]]:
        with self._lock:
            # 返回副本,避免迭代时数据变动
            return iter(list(self._data.items()))

    def recall_count_inc(self, sig: str) -> None:
        with self._lock:
            cur = self._data.get(sig)
            if cur is None:
                return
            # 拍 9:recall_count 是真"用进"信号(快脑命中 = 技能被复用 = 真有用);
            # evict 的 usage_score 应优先用 recall_count,fallback 到 usage_count。
            # Schema 已加 recall_count 字段(拍 9 修死代码)。
            new_stats = cur.model_copy(update={"recall_count": cur.recall_count + 1})
            self._data[sig] = new_stats


__all__ = [
    "UsageStore",
    "InMemoryUsageStore",
    "USAGE_DEBOUNCE_SEC",
]
