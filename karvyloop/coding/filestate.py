"""read-before-write 状态机（coding/filestate.py）。

规格：docs/modules/forge.md §2.3（HR-4，coding agent 最致命故障的防线）。
维护每文件快照(content_hash + mtime);Write/Edit 前置校验:
  ① 没读过 → 拒(errorCode READ_REQUIRED=2)
  ② mtime 自读取后变化 → 拒(CHANGED_SINCE_READ=3)
  ③ Edit 的 old_string 精确匹配且唯一(在 Edit 工具自身校验里做)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Optional


# 错误码(spec 隐含)
READ_REQUIRED = 2
CHANGED_SINCE_READ = 3


@dataclass(frozen=True)
class Snapshot:
    path: str
    content_hash: str
    mtime: float


class ReadBeforeWriteError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


class FileState:
    """单个会话/任务的 read-before-write 状态。"""

    def __init__(self):
        self._reads: dict[str, Snapshot] = {}

    def record_read(self, path: str, content: bytes) -> Snapshot:
        """Read 成功后调用:记录快照。"""
        norm = os.path.abspath(path)
        if not os.path.lexists(norm):
            # 读完发现文件没了(竞态)→ 当作 CHANGED 留着,下次 write 会拒
            snap = Snapshot(path=norm, content_hash="", mtime=0.0)
        else:
            mtime = os.path.getmtime(norm)
            h = hashlib.sha256(content).hexdigest()
            snap = Snapshot(path=norm, content_hash=h, mtime=mtime)
        self._reads[norm] = snap
        return snap

    def assert_writable(self, path: str) -> Snapshot:
        """Write/Edit 前调用:未读或已变 → 抛 ReadBeforeWriteError。

        规则:
        - 未读取过 + 文件**不存在** → 允许写(新文件,无内容可覆盖,读它没意义;9.5 修订)
        - 未读取过 + 文件**已存在** → READ_REQUIRED(防盲目覆盖已有内容,HR-4 真正目的)
        - 读取时文件不存在（mtime 哨兵 0.0）→ 允许写（创建文件）
        - 读取时存在,现在不存在 → CHANGED_SINCE_READ
        - 读取时存在,现在存在但 mtime 漂移 > 1ms → CHANGED_SINCE_READ
        """
        norm = os.path.abspath(path)
        snap = self._reads.get(norm)
        if snap is None:
            # 9.5 修订(用户:先读后写对新文件死板)——HR-4 的目的是防"盲目覆盖已有文件"。
            # 新文件(磁盘上不存在)没有内容可覆盖 → 直接允许创建,不必先读一个不存在的文件。
            if not os.path.lexists(norm):
                return Snapshot(path=norm, content_hash="", mtime=0.0)
            raise ReadBeforeWriteError(
                READ_REQUIRED,
                f"已存在的 {path} 未读取不可写(HR-4:先读后写,防盲目覆盖)",
            )
        # 哨兵:读取时文件不存在（仍允许写 = 创建文件）
        if snap.mtime == 0.0:
            return snap
        if not os.path.lexists(norm):
            raise ReadBeforeWriteError(
                CHANGED_SINCE_READ,
                f"{path} 自读取后已被删除",
            )
        cur_mtime = os.path.getmtime(norm)
        if cur_mtime > snap.mtime + 1e-3:  # 浮点容差
            raise ReadBeforeWriteError(
                CHANGED_SINCE_READ,
                f"{path} 自读取后被修改(读时 mtime={snap.mtime},当前={cur_mtime})",
            )
        return snap

    def get(self, path: str) -> Optional[Snapshot]:
        return self._reads.get(os.path.abspath(path))

    def clear(self) -> None:
        self._reads.clear()
