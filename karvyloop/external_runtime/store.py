"""external_runtime/store — 外部公民注册表持久化(用户数据默认持久)。

**形态**:`~/.karvyloop/external_citizens.json` 一个数组,每项一个公民 dict;atomic 写
(同 DomainStore 的 .tmp → os.replace 语义)。

**key 纪律**:公民记录只存"从哪读 key"的元信息 —— 真 key **绝不落这个文件**
(key 在本地 config/env,由 bridge 组进程时从那读,且不进子进程 env)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class ExternalCitizenStore:
    """外部公民定义的磁盘存储(JSON 数组,atomic 写)。ExternalCitizenRegistry 消费。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[dict]:
        """读回所有公民 dict。文件不存在/坏 → 返空(不阻塞启动)。"""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    def save_all(self, records) -> None:
        """整存(atomic:.tmp → replace)。records = list[dict]。"""
        payload = json.dumps(list(records), ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = ["ExternalCitizenStore"]
