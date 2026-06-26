"""TraceIndex 双层 ring buffer 测试(M3+ 拍 9.0a)。

设计:docs/25 §5。

**覆盖矩阵**(CLAUDE.md Q2 CI shape test):
- AC1: append + list 顺序(新→旧)
- AC2: payload roundtrip(JSON 序列化保真)
- AC3: 字节计数(raw_bytes / summary_bytes)
- AC4: 满则覆 cycle — 原文层满了后继续 append,最旧的被踢
- AC5: 满则覆 cycle — 摘要层满了后继续 append,最旧的被踢
- AC6: 跨进程 persistence — close 后重开数据还在
- AC7: 容量 is_full 阈值(90% 触发)
- AC8: close 幂等
- AC9: capacity 常量暴露
- AC10: 双层独立(原文满不影响摘要,反之亦然)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from karvyloop.karvy.fastbrain.trace_index import (
    DEFAULT_RAW_CAPACITY_BYTES,
    DEFAULT_SUMMARY_CAPACITY_BYTES,
    FULL_RATIO,
    TraceIndex,
    TraceRecord,
)
from karvyloop.karvy.fastbrain.trace_poll import (
    DAILY_POLL_INTERVAL_S,
    boot_poll,
    daily_poll,
    install_pollers,
)


# ---- fixture ----


@pytest.fixture
def tmp_index(tmp_path: Path) -> TraceIndex:
    """小容量 TraceIndex(1KB 原文 / 4KB 摘要),便于跑满覆 cycle。"""
    db = tmp_path / "trace.db"
    return TraceIndex(
        db,
        raw_capacity=1024,  # 1KB
        summary_capacity=4096,  # 4KB
    )


@pytest.fixture
def small_payload() -> dict:
    """~ 100 字节 payload。"""
    return {"kind": "test", "msg": "x" * 80, "n": 42}


# ---- AC1: append + list 顺序 ----


def test_append_and_list_raw_returns_newest_first(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    """append 3 条 → list_raw 返新→旧(seq DESC)。"""
    tmp_index.append_raw({"i": 1, **small_payload})
    tmp_index.append_raw({"i": 2, **small_payload})
    tmp_index.append_raw({"i": 3, **small_payload})

    items = tmp_index.list_raw(limit=10)
    assert len(items) == 3
    assert [it.payload["i"] for it in items] == [3, 2, 1]
    # list 是新→旧,seq 严格递减
    assert items[0].seq > items[1].seq > items[2].seq


def test_append_and_list_summary_returns_newest_first(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    tmp_index.append_summary({"i": 1, **small_payload})
    tmp_index.append_summary({"i": 2, **small_payload})

    items = tmp_index.list_summary(limit=10)
    assert [it.payload["i"] for it in items] == [2, 1]


def test_list_limit_caps_results(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    for i in range(5):
        tmp_index.append_raw({"i": i, **small_payload})
    assert len(tmp_index.list_raw(limit=3)) == 3
    # limit=3 应返最近 3 条(新→旧)
    assert [it.payload["i"] for it in tmp_index.list_raw(limit=3)] == [4, 3, 2]


# ---- AC2: payload roundtrip ----


def test_payload_roundtrip_preserves_unicode_and_nested(
    tmp_index: TraceIndex,
) -> None:
    payload = {
        "kind": "intent",
        "text": "中文 emoji 🎉 nested",
        "tags": ["a", "b", "c"],
        "meta": {"user": "ch", "n": 7, "ok": True, "missing": None},
    }
    rec = tmp_index.append_raw(payload)
    assert rec.size_bytes > 0
    items = tmp_index.list_raw(limit=1)
    assert items[0].payload == payload


def test_payload_with_non_ascii_chars_byte_count_matches(
    tmp_index: TraceIndex,
) -> None:
    """size_bytes 应按 UTF-8 编码后字节数算(不是字符数)。"""
    payload = {"msg": "你好"}  # 2 chars = 6 bytes UTF-8
    rec = tmp_index.append_raw(payload)
    expected = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert rec.size_bytes == expected
    # 中文字符在 UTF-8 中每字 3 字节;`{"msg": "你好"}` = 9 + 6 + 2 = 17 字节
    assert rec.size_bytes == 17


# ---- AC3: 字节计数 ----


def test_raw_bytes_starts_at_zero(tmp_index: TraceIndex) -> None:
    assert tmp_index.raw_bytes() == 0
    assert tmp_index.summary_bytes() == 0


def test_raw_bytes_grows_with_append(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    r1 = tmp_index.append_raw(small_payload)
    assert tmp_index.raw_bytes() == r1.size_bytes
    r2 = tmp_index.append_raw(small_payload)
    assert tmp_index.raw_bytes() == r1.size_bytes + r2.size_bytes


def test_raw_and_summary_bytes_are_independent(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    """原文写不增摘要计数,反之亦然。"""
    tmp_index.append_raw(small_payload)
    assert tmp_index.summary_bytes() == 0
    tmp_index.append_summary(small_payload)
    assert tmp_index.raw_bytes() > 0
    assert tmp_index.summary_bytes() > 0
    # 写摘要不增原文计数(独立性:再写一条摘要,原文不变)
    raw_before = tmp_index.raw_bytes()
    tmp_index.append_summary(small_payload)
    assert tmp_index.raw_bytes() == raw_before  # 写摘要不增原文
    # 写原文不增摘要计数
    summary_before = tmp_index.summary_bytes()
    tmp_index.append_raw(small_payload)
    assert tmp_index.summary_bytes() == summary_before  # 写原文不增摘要


# ---- AC4: 满则覆 cycle(原文)----


def test_raw_evicts_oldest_when_full(tmp_path: Path) -> None:
    """原文层满了后继续 append,最旧的被踢(环回)。"""
    db = tmp_path / "trace.db"
    # 容量 = 1KB,故意 append 25 条 ~50B 的 payload,触发多次 evict
    idx = TraceIndex(db, raw_capacity=1024, summary_capacity=4096)
    try:
        for i in range(25):
            idx.append_raw({"i": i, "padding": "x" * 40})
        # 容量超 90% 阈值 → 触发过 evict
        assert idx.is_raw_full() or idx.raw_bytes() < 1024
        # 最旧的(0..早期)一定被踢了
        items = idx.list_raw(limit=100)
        payloads = [it.payload["i"] for it in items]
        # 最新的一定在
        assert 24 in payloads
        # 最早的(0)一定不在(因为 25 条全 50B+JSON 包裹 > 1KB)
        assert 0 not in payloads
        # seq 单调(没空洞,因为按 seq ASC 删)
        seqs = [it.seq for it in items]
        assert seqs == sorted(seqs, reverse=True)
    finally:
        idx.close()


def test_raw_total_bytes_stays_below_capacity_after_evict(
    tmp_path: Path,
) -> None:
    """满则覆后,total bytes 应 < 90% 容量(已 evict 过)。"""
    db = tmp_path / "trace.db"
    cap = 1024
    idx = TraceIndex(db, raw_capacity=cap, summary_capacity=4096)
    try:
        for i in range(50):
            idx.append_raw({"i": i, "padding": "x" * 100})  # 大 payload 强制 evict
        # evict 到 90% 阈值以下就停,所以 < 90% 容量
        assert idx.raw_bytes() < int(cap * FULL_RATIO) + 200  # 留余量(单条可能微超)
    finally:
        idx.close()


# ---- AC5: 满则覆 cycle(摘要)----


def test_summary_evicts_oldest_when_full(tmp_path: Path) -> None:
    """摘要层同理。"""
    db = tmp_path / "trace.db"
    idx = TraceIndex(db, raw_capacity=1024, summary_capacity=2048)
    try:
        for i in range(40):
            idx.append_summary({"i": i, "padding": "x" * 80})
        assert idx.summary_bytes() < int(2048 * FULL_RATIO) + 200
        items = idx.list_summary(limit=100)
        # 最旧的一定被踢
        assert 0 not in [it.payload["i"] for it in items]
    finally:
        idx.close()


# ---- AC6: 跨进程 persistence ----


def test_persistence_across_close_and_reopen(tmp_path: Path) -> None:
    """close 后重开,数据还在(模拟跨进程)。"""
    db = tmp_path / "trace.db"
    idx1 = TraceIndex(db, raw_capacity=1024, summary_capacity=4096)
    idx1.append_raw({"i": 1, "msg": "persisted raw"})
    idx1.append_summary({"i": 2, "msg": "persisted summary"})
    seq_raw = idx1.list_raw(limit=1)[0].seq
    seq_sum = idx1.list_summary(limit=1)[0].seq
    idx1.close()

    # 重开 — 新"进程"模拟
    idx2 = TraceIndex(db, raw_capacity=1024, summary_capacity=4096)
    try:
        raw_items = idx2.list_raw(limit=10)
        sum_items = idx2.list_summary(limit=10)
        assert len(raw_items) == 1
        assert raw_items[0].payload["msg"] == "persisted raw"
        assert raw_items[0].seq == seq_raw
        assert len(sum_items) == 1
        assert sum_items[0].payload["msg"] == "persisted summary"
        assert sum_items[0].seq == seq_sum
    finally:
        idx2.close()


def test_persistence_across_real_subprocess(tmp_path: Path) -> None:
    """真子进程读写同一 sqlite 文件 — 模拟跨进程边界(Win/Linux 都跑)。"""
    db = tmp_path / "trace.db"

    # 子进程:写 3 条原文 + 2 条摘要
    writer = textwrap.dedent(
        f"""
        from pathlib import Path
        from karvyloop.karvy.fastbrain.trace_index import TraceIndex
        idx = TraceIndex(Path({str(db)!r}), raw_capacity=1024, summary_capacity=4096)
        idx.append_raw({{"i": 1}})
        idx.append_raw({{"i": 2}})
        idx.append_raw({{"i": 3}})
        idx.append_summary({{"j": 10}})
        idx.append_summary({{"j": 20}})
        idx.close()
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", writer],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"writer failed: {result.stderr}"
    assert "ok" in result.stdout

    # 父进程:重开,验证
    idx = TraceIndex(db, raw_capacity=1024, summary_capacity=4096)
    try:
        raws = [it.payload["i"] for it in idx.list_raw(limit=10)]
        sums = [it.payload["j"] for it in idx.list_summary(limit=10)]
        assert raws == [3, 2, 1]
        assert sums == [20, 10]
    finally:
        idx.close()


# ---- AC7: is_full 阈值 ----


def test_is_raw_full_triggers_at_90_percent(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    cap = 1000
    idx = TraceIndex(db, raw_capacity=cap, summary_capacity=1000)
    try:
        assert not idx.is_raw_full()
        # 写到 90% 容量附近
        big = {"x": "y" * 800}  # 一次 ~ 850 字节
        idx.append_raw(big)
        # 850 / 1000 = 85% → 未满
        assert not idx.is_raw_full()
        # 再 append 一次 → 1700 → 触发 evict,留下 850B < 90% (900)
        idx.append_raw(big)
        # 850B < 900(90% 阈值)= not full
        assert not idx.is_raw_full()
    finally:
        idx.close()


def test_is_summary_full_independent_of_raw(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    idx = TraceIndex(db, raw_capacity=10000, summary_capacity=500)
    try:
        # 摘要写满,原文不写
        big = {"x": "y" * 400}  # ~430B
        idx.append_summary(big)
        # 430 / 500 = 86% → 未满
        assert not idx.is_summary_full()
        idx.append_summary(big)
        # 860 → evict,留 430 < 90% 阈值 450 → not full
        assert not idx.is_summary_full()
        # 原文层仍空
        assert idx.raw_bytes() == 0
    finally:
        idx.close()


# ---- AC8: close 幂等 ----


def test_close_is_idempotent(tmp_index: TraceIndex) -> None:
    tmp_index.close()
    tmp_index.close()  # 第二次不报错
    # 已 close 后再 append 报错
    with pytest.raises(RuntimeError, match="已 close"):
        tmp_index.append_raw({"x": 1})


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    with TraceIndex(db, raw_capacity=1024, summary_capacity=4096) as idx:
        idx.append_raw({"x": 1})
    # 退出 with 块后 close;再 append 报错
    with pytest.raises(RuntimeError, match="已 close"):
        idx.append_raw({"x": 2})


# ---- AC9: 公共常量 ----


def test_default_capacities_exposed() -> None:
    """默认 10MB / 50MB 必须从模块顶层 import 到(Q2 锁 — public surface 不变)。"""
    assert DEFAULT_RAW_CAPACITY_BYTES == 10 * 1024 * 1024
    assert DEFAULT_SUMMARY_CAPACITY_BYTES == 50 * 1024 * 1024
    assert 0 < FULL_RATIO < 1


def test_trace_record_is_frozen_dataclass() -> None:
    """TraceRecord 不可变(Q5 借 cognition/trace.TrackEntry 风格)。"""
    rec = TraceRecord(seq=1, ts=1.0, payload={"x": 1}, size_bytes=10)
    with pytest.raises(Exception):  # FrozenInstanceError
        rec.seq = 2  # type: ignore[misc]


# ---- AC10: 双层完全独立 ----


def test_layers_fully_independent(tmp_path: Path) -> None:
    """原文满 / 摘要空 / 互相不影响。"""
    db = tmp_path / "trace.db"
    idx = TraceIndex(db, raw_capacity=512, summary_capacity=512)
    try:
        # 把原文打满
        for _ in range(20):
            idx.append_raw({"x": "y" * 100})
        # 摘要一条没写
        assert idx.summary_bytes() == 0
        # 写摘要
        idx.append_summary({"a": "b" * 100})
        assert idx.summary_bytes() > 0
        # 原文的字节数没被摘要影响
        raw_bytes_before = idx.raw_bytes()
        idx.append_summary({"c": "d" * 100})
        # 原文字节数不变
        assert idx.raw_bytes() == raw_bytes_before
    finally:
        idx.close()


# ---- 触发器测试(trace_poll)----


def test_boot_poll_returns_status_dict(tmp_index: TraceIndex) -> None:
    """boot_poll 返 status dict 含 6 字段。"""
    status = boot_poll(tmp_index)
    assert isinstance(status, dict)
    assert set(status.keys()) == {
        "raw_bytes",
        "raw_pct",
        "summary_bytes",
        "summary_pct",
        "is_raw_full",
        "is_summary_full",
    }
    assert status["raw_bytes"] == 0
    assert status["summary_bytes"] == 0
    assert status["is_raw_full"] is False


def test_boot_poll_reflects_writes(tmp_index: TraceIndex, small_payload: dict) -> None:
    tmp_index.append_raw(small_payload)
    tmp_index.append_summary(small_payload)
    status = boot_poll(tmp_index)
    assert status["raw_bytes"] > 0
    assert status["summary_bytes"] > 0


def test_daily_poll_distills_raw_to_summary(tmp_index: TraceIndex) -> None:
    """9.3c(修 D1):daily_poll 现在做 原文→摘要 蒸馏(不再 no-op)。"""
    tmp_index.append_raw({"kind": "intent", "intent": "hi"})
    summary_before = tmp_index.summary_bytes()
    out = daily_poll(tmp_index)  # 原文→摘要
    assert out is not None and out["kind"] == "distilled_summary"
    # 摘要层增长(蒸馏写了一条)
    assert tmp_index.summary_bytes() > summary_before


def test_daily_poll_empty_raw_returns_none(tmp_index: TraceIndex) -> None:
    """无原文 → daily_poll 返 None,不写摘要。"""
    assert daily_poll(tmp_index) is None


def test_install_pollers_runs_boot_only_by_default(
    tmp_index: TraceIndex, small_payload: dict
) -> None:
    """9.0a: 默认 enable_daily=False,只跑 boot_poll,不挂 timer。"""
    tmp_index.append_raw(small_payload)
    timer = install_pollers(tmp_index)
    assert timer is None  # 默认不开 daily
    # 数据不动
    assert tmp_index.raw_bytes() > 0


def test_install_pollers_with_daily_returns_timer(
    tmp_index: TraceIndex,
) -> None:
    """9.0a: enable_daily=True 时挂 timer(测试时用 cancel 立刻停)。"""
    timer = install_pollers(tmp_index, enable_daily=True)
    try:
        assert timer is not None
        assert timer.is_alive()
        # 24h 间隔太长,测试不等到 fire,直接 cancel
    finally:
        if timer is not None:
            timer.cancel()


def test_daily_poll_interval_constant() -> None:
    """24h 常量(Q2 锁 public surface)。"""
    assert DAILY_POLL_INTERVAL_S == 24 * 60 * 60


# ---- 边界 / 反向 ----


def test_zero_capacity_rejected(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    with pytest.raises(ValueError, match="raw_capacity must > 0"):
        TraceIndex(db, raw_capacity=0, summary_capacity=1024)
    with pytest.raises(ValueError, match="summary_capacity must > 0"):
        TraceIndex(db, raw_capacity=1024, summary_capacity=0)


def test_empty_list_when_no_data(tmp_index: TraceIndex) -> None:
    assert tmp_index.list_raw(limit=10) == []
    assert tmp_index.list_summary(limit=10) == []


def test_parent_dir_created_automatically(tmp_path: Path) -> None:
    """TraceIndex 在嵌套不存在目录时自动 mkdir(便利 bootstrap)。"""
    nested = tmp_path / "a" / "b" / "c" / "trace.db"
    assert not nested.parent.exists()
    idx = TraceIndex(nested, raw_capacity=1024, summary_capacity=1024)
    try:
        idx.append_raw({"x": 1})
        assert nested.exists()
    finally:
        idx.close()


# ---- FB-5 不变量:fastbrain.trace_index 不依赖小卡私有 ----


def test_trace_index_does_not_depend_on_karvy_private() -> None:
    """FB-5 锁:trace_index 模块源码不 import karvy.atoms(注释里提 IntentAnalyst 是允许的)。"""
    import karvyloop.karvy.fastbrain.trace_index as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    # 检查 import 语句(不检查注释/docstring 里出现的 "atoms"/"IntentAnalyst" 字符串)
    import_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    import_blob = "\n".join(import_lines)
    assert "karvy.atoms" not in import_blob, (
        f"FB-5 violation — trace_index 禁 import karvy.atoms:\n{import_blob}"
    )
    assert "IntentAnalyst" not in import_blob


def test_trace_poll_does_not_depend_on_karvy_private() -> None:
    """FB-5 锁:trace_poll 同样。"""
    import karvyloop.karvy.fastbrain.trace_poll as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    import_blob = "\n".join(import_lines)
    assert "karvy.atoms" not in import_blob, (
        f"FB-5 violation — trace_poll 禁 import karvy.atoms:\n{import_blob}"
    )
    assert "IntentAnalyst" not in import_blob


# ---- 9.3c-2: hash-chain 篡改可检测(TR-5)+ scope(TR-3)----


def test_verify_chain_clean(tmp_index: TraceIndex) -> None:
    tmp_index.append_raw({"kind": "intent", "intent": "a"})
    tmp_index.append_raw({"kind": "intent", "intent": "b"})
    tmp_index.append_raw({"kind": "intent", "intent": "c"})
    ok, broken = tmp_index.verify_chain("raw")
    assert ok is True and broken == -1


def test_verify_chain_detects_tamper(tmp_index: TraceIndex) -> None:
    tmp_index.append_raw({"kind": "intent", "intent": "a"})
    tmp_index.append_raw({"kind": "intent", "intent": "b"})
    # 篡改第 1 条 payload(模拟 human 编辑了 trace 文件)
    tmp_index._conn.execute(
        "UPDATE trace_raw SET payload_json='{\"kind\":\"x\"}' WHERE seq=1"
    )
    tmp_index._conn.commit()
    ok, broken = tmp_index.verify_chain("raw")
    assert ok is False and broken == 1  # 检测到第 1 条被改


def test_verify_chain_detects_deletion(tmp_index: TraceIndex) -> None:
    for i in range(4):
        tmp_index.append_raw({"kind": "intent", "intent": f"i{i}"})
    # 删中间一条 → 链断(下一条 prev_hash 对不上)
    tmp_index._conn.execute("DELETE FROM trace_raw WHERE seq=2")
    tmp_index._conn.commit()
    ok, broken = tmp_index.verify_chain("raw")
    assert ok is False


def test_scope_filter(tmp_index: TraceIndex) -> None:
    tmp_index.append_raw({"kind": "intent", "intent": "全局1"})  # default global
    tmp_index.append_raw({"kind": "intent", "intent": "装修事"}, scope="dom-装修")
    tmp_index.append_raw({"kind": "intent", "intent": "全局2"})
    glob = [r.payload["intent"] for r in tmp_index.list_raw(scope="global")]
    biz = [r.payload["intent"] for r in tmp_index.list_raw(scope="dom-装修")]
    assert set(glob) == {"全局1", "全局2"}
    assert biz == ["装修事"]
    # 不传 scope → 全部
    assert len(tmp_index.list_raw()) == 3


def test_record_carries_scope_and_hash(tmp_index: TraceIndex) -> None:
    rec = tmp_index.append_raw({"k": 1}, scope="dom-x")
    assert rec.scope == "dom-x"
    assert rec.hash  # 非空 hash
