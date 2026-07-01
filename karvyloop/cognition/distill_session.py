"""distill_session.py — 认知库沉淀工作流的"待办态"(一次一条,持久化,重启不丢)。

Hardy 工作流:喂料 → 抓取/分析 → 用"知识自生长/LLM Wiki"框架结构化总结给你看 →
你交流确认 → 沉淀 / 拒绝。**不结束不能开下一条**。本模块只管**单条待办**的 JSON 持久化
(重启后"下次打开继续聊"),不碰 LLM / 抓取 / 编译。

phase:
  - "awaiting"  : 已分析、给你看了结构化总结,等你交流/拍板沉淀(默认"待沟通"态)
(沉淀或拒绝后 → close() 删除文件,才能开下一条)
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional


class DistillSessionStore:
    """单条待办沉淀的持久化(原子写)。current() 没有就返 None。"""

    def __init__(self, path, *, clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def current(self) -> Optional[dict]:
        if not self._path.exists():
            return None
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) and d.get("id") else None
        except Exception:
            return None

    def open(self, *, material: str, fetched: str, summary: str,
             source_url: str = "", source_ref: str = "", already_fed: int = 0) -> dict:
        """开一条待办(调用方须先确认 current() 为 None —— 一次一条)。
        source_ref=来源指纹(URL/材料 hash);already_fed=该来源之前已沉淀几条(>0 → 沉淀会 supersede 换新)。"""
        s = {
            "id": uuid.uuid4().hex[:12],
            "material": material, "fetched": fetched, "summary": summary,
            "source_url": source_url, "source_ref": source_ref, "already_fed": int(already_fed),
            "phase": "awaiting",
            "transcript": [],            # 你↔小卡 围绕这条的交流(沉淀前的沟通)
            "created_ts": self._clock(),
        }
        self._save(s)
        return s

    def append_turn(self, *, who: str, text: str) -> Optional[dict]:
        s = self.current()
        if s is None:
            return None
        s.setdefault("transcript", []).append({"who": who, "text": text})
        self._save(s)
        return s

    def update_summary(self, summary: str) -> Optional[dict]:
        s = self.current()
        if s is None:
            return None
        s["summary"] = summary
        self._save(s)
        return s

    def close(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass

    def _save(self, s: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)


__all__ = ["DistillSessionStore"]
