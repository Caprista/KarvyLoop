"""Trace 三层漏斗 — 习惯层(M3+ 拍 9.0b)。

设计:docs/25-fastbrain-architecture.md §5 + 用户原话 2026-06-17。

**职责**(本拍 9.0b):
- 习惯层 = 永久保留的用户行为模式(冷数据,长期保留)
- 跨端共享(跟账号走)
- 由 LLM 慢脑(BehaviorPatternAnalyzer)从累积摘要凝出
- **model_ref 铺路** — 公共 LLM + per-agent 默认,9.0b 落地数据模型 + 解析器,9.0c IntentAnalyst 复用

**灵魂铁律**:
- FB-4:习惯层独立 — **不**依赖 原文/摘要
- FB-4:习惯可丢摘要(信息全在习惯里 — 用户原话"凝出后摘要本身可丢")
- FB-5:本模块**不**依赖小卡私有组件(不 import `karvy.atoms`)
- FB-7:本模块**不**写"意图分析"功能 — 那是 IntentAnalyst 职责

**借(Q5)**:
- sqlite3 stdlib + WAL(同 trace_index.py 模式)
- dataclass 风格(同 TraceRecord)

**自造**:
- HabitStore(永久 + 跨进程 + dedup 合并)
- ModelRef 解析链(per-agent 覆盖 → 全局默认 → 硬编码兜底)
- BehaviorPatternAnalyzer 骨架(9.0b 仅留接口,实做等 9.0c 一起)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence

from .trace_index import TraceRecord

logger = logging.getLogger(__name__)


# ---- 习惯数据模型 ----


@dataclass(frozen=True)
class Habit:
    """一条用户习惯(从一批 trace 摘要凝出)。

    fields:
        id: 主键
        pattern: 行为模式描述(LLM 生成的"用户习惯 X"模式)
        strength: 凝出强度 0-1(LLM 自评 + evidence_count 加权)
        evidence_count: 多少条 trace 摘要凝成(去重后)
        evidence_refs: trace_summary.seq 列表(可空 — 凝出后摘要可丢)
        first_seen: 首次凝出时间(秒,time.time)
        last_reinforced: 最近一次强化时间(同 trace 模式再次出现)
        model_ref: 凝出用的 model(per-agent model_ref 铺路,见 ModelRef)
    """
    id: int
    pattern: str
    strength: float
    evidence_count: int
    evidence_refs: tuple[int, ...]
    first_seen: float
    last_reinforced: float
    model_ref: str


# ---- model_ref 数据模型(9.0b 铺路)----


@dataclass(frozen=True)
class ModelRef:
    """模型引用(公共 LLM + per-agent default 铺路)。

    设计:用户原话"每个 agent 都可以各自关联全局配的某个模型"
    - `name`:模型名(全名,含 provider 前缀 / "anthropic/claude-sonnet-4-6")
    - `fallback`:兜底链(可选,失败时按顺序试)

    9.0b 落地:数据模型 + 解析器
    9.0c 落地:IntentAnalyst 调 LLM 时用 `resolve_model_ref(agent, cfg)`
    """
    name: str
    fallback: Optional["ModelRef"] = None


# ---- 公共 LLM 解析器(per-agent 覆盖 → 全局默认 → 硬编码兜底)----


# 硬编码兜底:0.1.0 默认模型(CLAUDE.md §19 个 vendor 中)
DEFAULT_FALLBACK_MODEL = "anthropic/claude-sonnet-4-6"


def resolve_model_ref(
    agent: str,
    global_config: Optional[Mapping[str, object]] = None,
) -> ModelRef:
    """解析 agent 的 model_ref 链。

    解析顺序:
        1. per-agent 覆盖(`global_config["model_refs"][agent]`)
        2. 全局默认(`global_config["default_model"]`)
        3. 硬编码兜底(`DEFAULT_FALLBACK_MODEL`)

    Args:
        agent: agent 标识(如 "intent_analyst" / "karvy" / "main_loop")
        global_config: 全局配置 dict(可选,None 时直接返兜底)

    Returns:
        ModelRef(可带 fallback 链)

    Examples:
        >>> resolve_model_ref("karvy", {"model_refs": {"karvy": "anthropic/claude-sonnet-4-6"}})
        ModelRef(name='anthropic/claude-sonnet-4-6', fallback=None)
        >>> resolve_model_ref("unknown", {"default_model": "deepseek/deepseek-chat"})
        ModelRef(name='deepseek/deepseek-chat', fallback=None)
        >>> resolve_model_ref("unknown", None)
        ModelRef(name='anthropic/claude-sonnet-4-6', fallback=None)
    """
    cfg = global_config or {}
    # 1. per-agent 覆盖
    model_refs = cfg.get("model_refs", {}) if isinstance(cfg, Mapping) else {}
    if isinstance(model_refs, Mapping) and agent in model_refs:
        name = model_refs[agent]
        if isinstance(name, str) and name:
            return ModelRef(name=name)
    # 2. 全局默认
    default = cfg.get("default_model", None) if isinstance(cfg, Mapping) else None
    if isinstance(default, str) and default:
        return ModelRef(name=default)
    # 3. 硬编码兜底
    return ModelRef(name=DEFAULT_FALLBACK_MODEL)


# ---- HabitStore(sqlite 持久化 + dedup 合并)----


_HABIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS habits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pattern TEXT NOT NULL UNIQUE,
  strength REAL NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  first_seen REAL NOT NULL,
  last_reinforced REAL NOT NULL,
  model_ref TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS habits_strength ON habits(strength DESC);
"""


def _open_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class HabitStore:
    """习惯层 — 永久保留,sqlite 持久化,跨进程安全。

    行为契约:
        - upsert(pattern, ...):同 pattern 合并 — evidence 累加 + last_reinforced 更新
        - list_habits():按 strength DESC + last_reinforced DESC 排序
        - 跨进程安全(WAL);线程安全(internal lock)
        - 无固定容量上限(习惯 = 永久;实际增长由业务控制,典型 10-100 条)
    """

    def __init__(self, path: Path, *, clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._conn = _open_sqlite(self._path)
        self._conn.executescript(_HABIT_SCHEMA_SQL)
        self._conn.commit()
        self._lock = threading.Lock()
        self._closed = False

    def upsert(
        self,
        pattern: str,
        *,
        strength: float = 1.0,
        evidence_refs: Sequence[int] = (),
        model_ref: str = "",
    ) -> Habit:
        """插或合并一条习惯。

        行为:
            - pattern 不存在 → 插入
            - pattern 存在 → evidence 合并(去重) + strength 取 max + last_reinforced 更新

        Returns:
            插/合并后的 Habit(含最终 id)
        """
        if not pattern:
            raise ValueError("pattern must be non-empty")
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"strength must be 0-1, got {strength}")
        now = self._clock()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, strength, evidence_count, evidence_refs_json, first_seen "
                "FROM habits WHERE pattern = ?",
                (pattern,),
            ).fetchone()
            if existing is None:
                cur = self._conn.execute(
                    "INSERT INTO habits (pattern, strength, evidence_count, "
                    "evidence_refs_json, first_seen, last_reinforced, model_ref) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        pattern,
                        strength,
                        len(evidence_refs),
                        json.dumps(sorted(set(evidence_refs)), ensure_ascii=False),
                        now,
                        now,
                        model_ref,
                    ),
                )
                self._conn.commit()
                return Habit(
                    id=int(cur.lastrowid),
                    pattern=pattern,
                    strength=strength,
                    evidence_count=len(evidence_refs),
                    evidence_refs=tuple(sorted(set(evidence_refs))),
                    first_seen=now,
                    last_reinforced=now,
                    model_ref=model_ref,
                )
            # merge
            hid, old_strength, old_count, old_refs_json, first_seen = existing
            old_refs = json.loads(old_refs_json) if old_refs_json else []
            merged_refs = sorted(set(old_refs) | set(evidence_refs))
            merged_count = len(merged_refs)
            new_strength = max(float(old_strength), strength)
            self._conn.execute(
                "UPDATE habits SET strength = ?, evidence_count = ?, "
                "evidence_refs_json = ?, last_reinforced = ?, model_ref = ? "
                "WHERE id = ?",
                (
                    new_strength,
                    merged_count,
                    json.dumps(merged_refs, ensure_ascii=False),
                    now,
                    model_ref or "",  # 留旧值如果新空
                    int(hid),
                ),
            )
            self._conn.commit()
            return Habit(
                id=int(hid),
                pattern=pattern,
                strength=new_strength,
                evidence_count=merged_count,
                evidence_refs=tuple(merged_refs),
                first_seen=float(first_seen),
                last_reinforced=now,
                model_ref=model_ref,
            )

    def list_habits(self, limit: int = 100) -> list[Habit]:
        """列所有习惯(按 strength DESC + last_reinforced DESC)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, pattern, strength, evidence_count, evidence_refs_json, "
                "first_seen, last_reinforced, model_ref "
                "FROM habits ORDER BY strength DESC, last_reinforced DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_habit(r) for r in rows]

    def get_habit(self, habit_id: int) -> Optional[Habit]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, pattern, strength, evidence_count, evidence_refs_json, "
                "first_seen, last_reinforced, model_ref "
                "FROM habits WHERE id = ?",
                (habit_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_habit(row)

    def count(self) -> int:
        """习惯总数。"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM habits").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "HabitStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _row_to_habit(row: tuple) -> Habit:
        hid, pattern, strength, count, refs_json, first, last, model_ref = row
        refs = json.loads(refs_json) if refs_json else []
        return Habit(
            id=int(hid),
            pattern=pattern,
            strength=float(strength),
            evidence_count=int(count),
            evidence_refs=tuple(int(r) for r in refs),
            first_seen=float(first),
            last_reinforced=float(last),
            model_ref=model_ref or "",
        )


# ---- 行为判断凝习惯(LLM 慢脑 — 0.1.0 骨架)----


class LlmClientProtocol(Protocol):
    """LLM 客户端 Protocol(供 BehaviorPatternAnalyzer 依赖倒置)。

    BehaviorPatternAnalyzer 只依赖这个**结构**,不耦合具体 LLM 实现;
    真 client 由 `ProviderLlmClient` 适配器包 `karvyloop.llm` 的 LLMProvider 而来。
    """

    def chat(
        self,
        model: str,
        messages: Sequence[dict],
        *,
        temperature: float = 0.3,
    ) -> str:
        """同步 chat,返 LLM 文本回复。"""
        ...


# BehaviorPatternAnalyzer prompt(行为判断凝习惯)
_BEHAVIOR_PROMPT = (
    "你是一个用户行为模式分析师。以下是一段时间内的用户活动 trace 摘要列表。\n"
    "请凝出 1-3 条**重复出现的、稳定的、跨场景**的用户行为模式(habit pattern),\n"
    "用一句话描述(中文,简洁,带主语'用户')。\n"
    "每条带 strength 评分(0-1,越高 = 越稳定/越多次出现/越跨场景)。\n"
    "**不要**凝时变信号(天气/时间/一次性任务),**不要**凝上下文强依赖的模式。\n"
    "**只**返 JSON 数组(不要 markdown 代码块,不要解释),每条 {{pattern: str, strength: float}}。\n"
    "若凝不出任何稳定模式,返空数组 []。\n"
    "摘要:\n{summary_list}\n"
)


def _strip_code_fences(text: str) -> str:
    """剥 markdown 代码块围栏(```json ... ``` / ``` ... ```)。

    LLM 常违反"不要 markdown"指令,这里做兜底净化。
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    # 去首行 ```... 和尾行 ```
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_array(text: str) -> str:
    """从 LLM 文本里抽出第一个 JSON 数组(裁掉前后散文)。

    返回 `[...]` 子串;找不到返原文(让 json.loads 自己报错)。
    """
    cleaned = _strip_code_fences(text)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _summary_to_line(rec: TraceRecord) -> str:
    """一条 TraceRecord → prompt 里的一行文本(seq + payload 关键字段)。"""
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    kind = payload.get("kind", "")
    # 取 payload 里除 kind 外的字段做摘要(截断防 prompt 爆)
    rest = {k: v for k, v in payload.items() if k != "kind"}
    rest_str = json.dumps(rest, ensure_ascii=False)
    if len(rest_str) > 300:
        rest_str = rest_str[:300] + "…"
    return f"#{rec.seq} [{kind}] {rest_str}"


class BehaviorPatternAnalyzer:
    """行为判断凝习惯(LLM 慢脑)。

    职责:
        - 拼 prompt(摘要列表 + 凝习惯指令)
        - 调 LLM(经 LlmClientProtocol 依赖倒置)
        - robust parse JSON 数组(剥代码块 + 抽数组 + 逐项校验)
        - 返 list[Habit](id=0 未持久化;evidence_refs = 所有摘要 seq;
          first_seen/last_reinforced=0.0 待 HabitStore.upsert 落库时赋值)

    优雅退化:
        - 无 LLM client → 返 []
        - 摘要空 → 返 []
        - LLM 返非法 JSON / 空数组 → 返 []
        - 单项缺 pattern / strength 越界 → 跳过该项(不整体失败)

    依赖倒置:`llm_client` 是 LlmClientProtocol(duck type);真 client 用
    `ProviderLlmClient` 适配 `karvyloop.llm` 的 LLMProvider。
    """

    def __init__(
        self,
        llm_client: Optional[LlmClientProtocol] = None,
        *,
        max_habits: int = 3,
    ) -> None:
        self._llm_client = llm_client
        self._max_habits = max_habits

    @property
    def has_llm(self) -> bool:
        return self._llm_client is not None

    def analyze(
        self,
        summaries: Sequence[TraceRecord],
        model_ref: ModelRef,
    ) -> list[Habit]:
        """从一批 trace 摘要凝出 Habit 列表。

        Args:
            summaries: 一批 TraceRecord(从 trace_index 摘要层读)
            model_ref: 凝出用的 model(per-agent model_ref 铺路)

        Returns:
            list[Habit] — 凝出的习惯;无 LLM client / 摘要空 / 凝不出 → []
        """
        if self._llm_client is None:
            return []
        if not summaries:
            return []

        # 1. 拼 prompt
        summary_list = "\n".join(_summary_to_line(s) for s in summaries)
        prompt = _BEHAVIOR_PROMPT.format(summary_list=summary_list)

        # 2. 调 LLM(失败 → 优雅返 [],不让慢脑异常打断主流程)
        #    拍 9.3a:标 token 来源=凝习惯(账本按 source 归属)
        from karvyloop.llm.token_ledger import token_source
        try:
            with token_source("凝习惯"):
                raw = self._llm_client.chat(
                    model_ref.name,
                    [{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
        except Exception as e:
            logger.debug(f"[BehaviorPatternAnalyzer] LLM chat 失败,返 []: {e}")
            return []

        # 3. robust parse
        return self._parse_habits(raw, summaries, model_ref)

    def _parse_habits(
        self,
        raw: str,
        summaries: Sequence[TraceRecord],
        model_ref: ModelRef,
    ) -> list[Habit]:
        """从 LLM 文本 parse 出 Habit 列表(逐项校验,坏项跳过)。"""
        try:
            arr = json.loads(_extract_json_array(raw or ""))
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[BehaviorPatternAnalyzer] JSON parse 失败,返 []: {e}")
            return []
        if not isinstance(arr, list):
            return []

        evidence_refs = tuple(int(s.seq) for s in summaries)
        evidence_count = len(evidence_refs)
        out: list[Habit] = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            pattern = item.get("pattern", "")
            strength = item.get("strength", None)
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                strength_f = float(strength)
            except (TypeError, ValueError):
                continue
            if not 0.0 <= strength_f <= 1.0:
                continue
            out.append(Habit(
                id=0,  # 未持久化(HabitStore.upsert 落库时赋 id)
                pattern=pattern.strip(),
                strength=strength_f,
                evidence_count=evidence_count,
                evidence_refs=evidence_refs,
                first_seen=0.0,  # 待 HabitStore.upsert 赋值
                last_reinforced=0.0,
                model_ref=model_ref.name,
            ))
            if len(out) >= self._max_habits:
                break
        return out


class ProviderLlmClient:
    """LlmClientProtocol 适配器 — 包 `karvyloop.llm` 的 LLMProvider(借通用基建)。

    设计(Q5 自造≠闭门造车):
    - **借** karvyloop.llm 的 LLMProvider / ChatRequest / Message / ChatResponse(不重写)
    - **自造**仅这层薄适配(把 `chat(model, messages, *, temperature)` → ChatRequest)

    用法(entry/CLI 接线层):
        from karvyloop.llm.provider import create_provider
        provider = create_provider("anthropic", llm_config)
        client = ProviderLlmClient(provider)
        analyzer = BehaviorPatternAnalyzer(llm_client=client)

    注:本类**懒导入** karvyloop.llm(__init__ 时才 import),保持 trace_habit 的
    HabitStore / ModelRef 部分**零 LLM 依赖**(纯数据层可独立 import)。
    """

    def __init__(self, provider: object, *, max_tokens: int = 1024) -> None:
        self._provider = provider
        self._max_tokens = max_tokens

    def chat(
        self,
        model: str,
        messages: Sequence[dict],
        *,
        temperature: float = 0.3,
    ) -> str:
        # 懒导入:只在真用 LLM 时才拉 karvyloop.llm
        from karvyloop.llm.provider import ChatRequest, Message

        req = ChatRequest(
            model=model,
            messages=[Message(role=m["role"], content=m["content"]) for m in messages],
            max_tokens=self._max_tokens,
        )
        resp = self._provider.chat(req)
        return getattr(resp, "content", "") or ""


def _run_coro_sync(coro: Any) -> Any:
    """把一个 coroutine 跑到完成并取回结果,**不论当前线程有没有 event loop**。

    - 无运行中的 loop(intent_pump 的 threading.Timer 后台线程 / boot 线程)→ asyncio.run。
    - 有运行中的 loop(万一在 async 上下文里调到)→ 丢到一个新线程里 asyncio.run,
      避免 "asyncio.run() cannot be called from a running event loop"。
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)   # 没有运行中的 loop —— 直接跑(后台线程的常态)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


class GatewayLlmClient:
    """LlmClientProtocol over **已接好的 gateway**(`models.*` 配置,单一真理来源)。

    病根(2026-06-20 起服务时抓到):intent_pump 走的是旧 `karvyloop.llm.config`(认 `llm.*`
    schema),而真实 config + 主运行时走 gateway 的 `models.providers`。schema 对不上 →
    主动建议(预判象限)永空转。修法 = 复用主 loop 已建好的 gateway,别再维护第二套配置栈
    (对齐"接基建前先核真实数据形态")。

    bridge:analyzer 的 `chat()` 是同步,gateway 的 `complete()` 是异步流式 → 收集 TextDelta
    跑到完成(`_run_coro_sync` 处理有/无 loop 两种线程上下文)。
    """

    def __init__(self, gateway: object, *, default_model_ref: str = "",
                 max_tokens: int = 1024) -> None:
        self._gw = gateway
        self._default_ref = default_model_ref
        self._max_tokens = max_tokens

    def chat(self, model: str, messages: Sequence[dict], *,
             temperature: float = 0.3) -> str:
        from karvyloop.gateway import ResolveScope
        ref = self._gw.resolve_model(
            ResolveScope(atom_model=(model or self._default_ref) or None)
        )
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]

        async def _run() -> str:
            out: list[str] = []
            async for ev in self._gw.complete(msgs, [], ref):
                if type(ev).__name__ == "TextDelta":
                    out.append(getattr(ev, "text", "") or "")
            return "".join(out)

        return _run_coro_sync(_run())


__all__ = [
    # data
    "BehaviorPatternAnalyzer",
    "DEFAULT_FALLBACK_MODEL",
    "GatewayLlmClient",
    "Habit",
    "HabitStore",
    "LlmClientProtocol",
    "ModelRef",
    "ProviderLlmClient",
    "resolve_model_ref",
]
