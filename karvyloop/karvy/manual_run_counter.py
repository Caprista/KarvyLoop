"""manual_run_counter — 手动运行计数器(docs/90 刀3c「时机能力提示」的确定性地基）。

**只数用户手动发起、且成功完成的运行**,键 = `intent_fingerprint`(经 crystallize
`_intent_cluster` 归一的零 LLM 确定性指纹,换个说法的同一类事落同一键)。攒到第 N 次
(默认 2)→ 上层递一张温和的「要不要每周自动跑」建议卡(schedule_suggest)。

设计铁律(骚扰=产品伤害,重点守):
- **只认手动成功运行**:调用方只在"用户直接聊天(kind=drive)成功完成"那刻 bump ——
  pursuit tick / schedule 触发 / proposal 重跑 / 系统发起的运行**永不进这里**(它们在别的
  完成点,不调 bump)。失败不 bump(失败有 run_task 重试卡管)。
- **提过一次永不再提**:某指纹出过建议卡 → `already_suggested=True` 持久化(接受/拒绝/忽略
  都算提过)→ 这条永不再触发。
- **旁路、宁空勿毒**:计数在任务成功**之后**旁路做,任何异常都吞掉返回 None —— 计数丢了
  大不了少提一次,**绝不阻塞/拖慢 drive 热路径,绝不因它崩任务主路径**。坏文件当空。

落盘:`~/.karvyloop/manual_run_counts.json`(与 schedules.json/tasks.json 同家;path=None
= 纯内存,测试不污染真实 home)。零 LLM、零网络。
"""
from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Optional

from karvyloop.karvy.ambient import intent_fingerprint

logger = logging.getLogger(__name__)

# 计数表容量上限(有界,防指纹表随会话无限膨胀)。超了按 last_ts 砍最老的。
_MAX_ENTRIES = 2000
# 卡上展示的 intent 摘要截断(人话摘要,不喧宾夺主)。
_INTENT_SAMPLE_MAX = 160
# 寒暄门(对抗自评揪出的头号误触面):「谢谢」「ok」这类短语不是"一件事",发两次也不该
# 冒"要自动跑『谢谢』?"——太蠢。太短的 intent 不计数(宁可不提):CJK 少于 4 个字、
# 或纯拉丁少于 3 个词,都当寒暄跳过。确定性,零 LLM。
_MIN_CJK_CHARS = 4
_MIN_LATIN_WORDS = 3


def _too_short_to_be_a_task(text: str) -> bool:
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    if cjk:
        return cjk < _MIN_CJK_CHARS
    return len(text.split()) < _MIN_LATIN_WORDS


class ManualRunCounter:
    """手动运行计数器(intent 指纹 → {count, already_suggested, last_ts, intent})。"""

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self._path = pathlib.Path(path) if path else None
        # fingerprint -> {"count": int, "already_suggested": bool, "last_ts": float, "intent": str}
        self._entries: dict[str, dict] = {}
        self._load()

    # ---- 归一键(复用 ambient/crystallize 的确定性指纹,不另造)----
    @staticmethod
    def fingerprint(intent: str) -> str:
        return intent_fingerprint(intent or "")

    # ---- 持久化(fail-safe:坏文件当空,落盘失败不阻断)----
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[manual_run_counter] 计数文件损坏,当空(少提一次不致命)")
            return
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return
        for fp, e in entries.items():
            if not isinstance(e, dict):
                continue
            try:
                self._entries[str(fp)] = {
                    "count": int(e.get("count") or 0),
                    "already_suggested": bool(e.get("already_suggested")),
                    "last_ts": float(e.get("last_ts") or 0.0),
                    "intent": str(e.get("intent") or "")[:_INTENT_SAMPLE_MAX],
                }
            except (TypeError, ValueError):
                continue

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps({"entries": self._entries}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:  # 落盘失败不阻断(活动记录,丢一次不致命)
            logger.warning("[manual_run_counter] 计数落盘失败(不阻断): %s", e)

    def _prune(self) -> None:
        """有界:超上限按 last_ts 砍最老的(计数是"最近这阵子重复了没",老的不必留)。"""
        if len(self._entries) <= _MAX_ENTRIES:
            return
        keep = sorted(self._entries.items(), key=lambda kv: kv[1].get("last_ts", 0.0),
                      reverse=True)[:_MAX_ENTRIES]
        self._entries = dict(keep)

    # ---- 计数(只在"用户手动成功运行"那刻调)----
    def bump(self, intent: str, *, now: Optional[float] = None) -> Optional[dict]:
        """给这条 intent 的指纹 +1,返回该指纹当前 entry 的**副本**(含 fingerprint)。

        任何异常都吞掉返回 None(旁路纪律:计数坏了不许拖垮任务主路径)。"""
        try:
            text = (intent or "").strip()
            if not text:
                return None
            if _too_short_to_be_a_task(text):   # 寒暄门:短语不是"一件事",不计数
                return None
            fp = intent_fingerprint(text)
            ts = time.time() if now is None else float(now)
            e = self._entries.get(fp)
            if e is None:
                e = {"count": 0, "already_suggested": False, "last_ts": 0.0, "intent": ""}
                self._entries[fp] = e
            e["count"] = int(e.get("count") or 0) + 1
            e["last_ts"] = ts
            e["intent"] = text[:_INTENT_SAMPLE_MAX]   # 存最近一次原话(卡上人话摘要)
            self._prune()
            self._save()
            out = dict(e)
            out["fingerprint"] = fp
            return out
        except Exception as ex:  # noqa: BLE001 —— 旁路绝不外溢
            logger.debug("[manual_run_counter] bump 失败(忽略): %s", ex)
            return None

    def mark_suggested(self, fingerprint: str, *, now: Optional[float] = None) -> None:
        """标记这条指纹已出过建议卡 → 永不再提(接受/拒绝/忽略都算提过)。"""
        try:
            fp = (fingerprint or "").strip()
            if not fp:
                return
            e = self._entries.get(fp)
            if e is None:
                e = {"count": 0, "already_suggested": False, "last_ts": 0.0, "intent": ""}
                self._entries[fp] = e
            e["already_suggested"] = True
            e["last_ts"] = time.time() if now is None else float(now)
            self._save()
        except Exception as ex:  # noqa: BLE001
            logger.debug("[manual_run_counter] mark_suggested 失败(忽略): %s", ex)

    def get(self, fingerprint: str) -> Optional[dict]:
        e = self._entries.get((fingerprint or "").strip())
        return dict(e) if e else None

    def already_suggested(self, fingerprint: str) -> bool:
        e = self._entries.get((fingerprint or "").strip())
        return bool(e and e.get("already_suggested"))


__all__ = ["ManualRunCounter"]
