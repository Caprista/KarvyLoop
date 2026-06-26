"""context-governance 验收测试 —— 逐条对应 docs/modules/context-governance.md §5。

7 条 AC:HR-3 断路器 / autocompact 阈值 / microcompact / boundary 稳定 +
cache_control / truncate_utf8 不破字符 / BlockingLimitError。
"""

from __future__ import annotations

import pytest

from karvyloop.context import (
    AUTOCOMPACT_BUFFER_TOKENS,
    CACHE_TYPE,
    COMPACTABLE,
    MAX_CONSECUTIVE_FAILURES,
    PLACEHOLDER,
    SENTINEL,
    BlockingLimitError,
    GovConfig,
    GovState,
    autocompact,
    autocompact_threshold,
    build_system_for_request,
    count_tokens_messages,
    find_sentinel_index,
    govern,
    is_sentinel,
    microcompact,
    microcompact_threshold,
    split_static_dynamic,
    truncate_str_utf8,
    truncate_utf8,
)


# ============ AC1:HR-3 断路器 —— 第 4 次不再调压缩 API ============
@pytest.mark.asyncio
async def test_ac1_breaker_opens_after_max_failures():
    state = GovState()
    cfg = GovConfig()
    # 构造 10 条 middle 消息
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    call_count = 0

    async def always_fail(mids):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("summarize failed")

    # 连续 3 次失败 → 第 4 次直接返回(不开 API)
    for i in range(MAX_CONSECUTIVE_FAILURES):
        out = await autocompact(messages, state, cfg, always_fail, context_window=10_000)
        assert out == messages  # 失败保持原样
    assert state.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert state.breaker_open is True

    # 第 4 次:不会调 summarize
    out2 = await autocompact(messages, state, cfg, always_fail, context_window=10_000)
    assert out2 == messages
    assert call_count == MAX_CONSECUTIVE_FAILURES  # 没增加


@pytest.mark.asyncio
async def test_ac1b_breaker_resets_on_success():
    state = GovState()
    cfg = GovConfig()
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    fail_then_succeed = [True, True, False]  # 失败两次,第三次成功
    idx = 0

    async def flaky(mids):
        nonlocal idx
        if fail_then_succeed[idx]:
            idx += 1
            raise RuntimeError("fail")
        idx += 1
        return "summary text"

    out1 = await autocompact(messages, state, cfg, flaky)
    assert state.consecutive_failures == 1
    out2 = await autocompact(messages, state, cfg, flaky)
    assert state.consecutive_failures == 2
    out3 = await autocompact(messages, state, cfg, flaky)
    # 成功 → 归零
    assert state.consecutive_failures == 0
    assert state.breaker_open is False
    # 摘要进了结果
    assert any("autocompact summary" in str(m.get("content", "")) for m in out3)


# ============ AC2:autocompact 触发阈值 = 窗口 - 13k ============
def test_ac2_autocompact_threshold():
    assert autocompact_threshold(200_000) == 200_000 - 13_000 == 187_000
    assert autocompact_threshold(100_000) == 87_000
    # 与 80% 区分:不应等于 int(200_000 * 0.8)
    assert autocompact_threshold(200_000) != 160_000


# ============ AC3:microcompact —— 超阈值时旧工具结果占位,最近 N 保留,id 配对不破 ============
def test_ac3_microcompact_keeps_recent_and_preserves_ids():
    # 构造:10 个 Read 工具结果 + 1 个 user
    messages = [
        {"role": "user", "content": "hi"},
    ]
    for i in range(10):
        messages.append({
            "role": "tool", "tool_use_id": f"id-{i}", "name": "Read",
            "content": f"old-output-{i}",
        })
    messages.append({"role": "user", "content": "now what?"})

    out = microcompact(messages, keep_recent=3)
    # 顺序不变
    assert out == messages
    # 全部 10 个 tool 结果里,前 7 个被占位,后 3 个保留
    tool_results = [m for m in out if m.get("role") == "tool" and m.get("name") == "Read"]
    assert len(tool_results) == 10
    assert tool_results[0]["content"] == PLACEHOLDER
    assert tool_results[6]["content"] == PLACEHOLDER
    assert tool_results[7]["content"] == "old-output-7"
    assert tool_results[9]["content"] == "old-output-9"
    # tool_use_id 配对不破
    for i, m in enumerate(tool_results):
        assert m["tool_use_id"] == f"id-{i}"
    # _meta 标记
    assert all(m.get("_meta", {}).get("microcompacted") for m in tool_results[:7])
    assert not any(m.get("_meta", {}).get("microcompacted") for m in tool_results[7:])


def test_ac3b_microcompact_keeps_all_when_below_threshold():
    messages = [
        {"role": "tool", "tool_use_id": "i-1", "name": "Read", "content": "x"},
        {"role": "tool", "tool_use_id": "i-2", "name": "Bash", "content": "y"},
    ]
    out = microcompact(messages, keep_recent=5)
    # 全保留
    assert all(m["content"] in ("x", "y") for m in out)


def test_ac3c_microcompact_skips_non_compactable_tools():
    """非 COMPACTABLE 集合的工具结果不动(TodoWrite 不在集合中 → 永不压)"""
    # 5 个 Read + 5 个 TodoWrite(穿插)
    messages = []
    for i in range(5):
        messages.append({"role": "tool", "tool_use_id": f"r-{i}", "name": "Read", "content": f"r{i}"})
        messages.append({"role": "tool", "tool_use_id": f"t-{i}", "name": "TodoWrite", "content": f"t{i}"})
    out = microcompact(messages, keep_recent=2)
    # TodoWrite 全部保留(非 compactable)
    tw = [m for m in out if m["name"] == "TodoWrite"]
    assert all(m["content"] in {f"t{i}" for i in range(5)} for m in tw)
    assert not any(m.get("_meta", {}).get("microcompacted") for m in tw)
    # Read:5 个,keep_recent=2 → 最近 2 个保留,前 3 个占位
    rw = [m for m in out if m["name"] == "Read"]
    assert rw[0]["content"] == PLACEHOLDER
    assert rw[1]["content"] == PLACEHOLDER
    assert rw[2]["content"] == PLACEHOLDER
    assert rw[3]["content"] == "r3"
    assert rw[4]["content"] == "r4"


# ============ AC4:boundary —— 静态段字节稳定 + 哨兵发送前被过滤 ============
def test_ac4_boundary_static_stable_and_sentinel_filtered():
    sections = [
        "static-line-1",
        "static-line-2",
        SENTINEL,
        "dynamic-line-1",
    ]
    static, dynamic = split_static_dynamic(sections)
    assert static == ["static-line-1", "static-line-2"]
    assert dynamic == ["dynamic-line-1"]
    # 哨兵不在结果里
    assert SENTINEL not in static
    assert SENTINEL not in dynamic
    # 改动态段不影响静态段
    sections2 = ["static-line-1", "static-line-2", SENTINEL, "dynamic-CHANGED"]
    static2, dynamic2 = split_static_dynamic(sections2)
    assert static2 == static

    # 哨兵过滤
    blocks = build_system_for_request(static, dynamic)
    block_texts = [b["text"] for b in blocks]
    assert SENTINEL not in block_texts
    assert "static-line-1" in block_texts
    assert "dynamic-line-1" in block_texts


def test_ac4b_boundary_sentinel_index():
    sections = ["a", "b", SENTINEL, "c"]
    assert find_sentinel_index(sections) == 2
    assert is_sentinel(SENTINEL) is True
    assert is_sentinel("a") is False


# ============ AC5:静态前缀末块带 cache_control: ephemeral ============
def test_ac5_static_last_block_has_cache_control():
    static = ["s1", "s2", "s3"]
    blocks = build_system_for_request(static, dynamic=["d1"])
    # 静态 3 块 + 动态 1 块
    assert len(blocks) == 4
    # 静态最后一块 = s3 → 带 cache_control
    assert blocks[2]["text"] == "s3"
    assert blocks[2].get("cache_control") == {"type": CACHE_TYPE}
    # 动态块不带 cache_control
    assert "cache_control" not in blocks[3]
    # 静态非最后块不带
    assert "cache_control" not in blocks[0]
    assert "cache_control" not in blocks[1]


def test_ac5b_no_static_no_cache():
    blocks = build_system_for_request(static=[], dynamic=["d1"])
    # 没有静态段 → 不打 cache_control
    assert all("cache_control" not in b for b in blocks)


# ============ AC6:truncate_utf8 永不切坏 UTF-8 ============
def test_ac6_truncate_utf8_preserves_char_boundary():
    # "你" = 3 bytes in UTF-8 (E4 BD A0)
    s = "你" * 100  # 300 bytes
    enc = s.encode("utf-8")
    # 切到 50 bytes: 一定在某个 "你" 中间
    cut, truncated = truncate_utf8(enc, 50)
    assert truncated is True
    # 切完还能 decode(可能 replace,但不破)
    decoded = cut.decode("utf-8", errors="replace")
    # 必须落在字符边界 → 长度一定是 3 的倍数
    assert len(cut) % 3 == 0
    # 字符数 × 3 = 字节数
    assert len(decoded.encode("utf-8")) == len(cut)

    # 切到恰好字符边界
    cut2, t2 = truncate_utf8(enc, 300)  # 恰好 == len(enc) → 不截
    assert t2 is False
    cut3, t3 = truncate_utf8(enc, 30)  # 远小于 → 必然截
    assert t3 is True
    # 切后是完整的 10 个 "你" = 30 字节(0..29,30..32 被切)
    assert cut2 == enc  # 边界情形
    assert len(cut3) == 30  # 10 个完整"你"
    assert cut3 == b"\xe4\xbd\xa0" * 10

    # 字符串版本
    scut, st = truncate_str_utf8(s, 50)
    assert st is True
    scut.encode("utf-8").decode("utf-8")  # 不破

    # 空 / 不超
    assert truncate_utf8(b"hi", 10) == (b"hi", False)
    assert truncate_utf8(b"", 10) == (b"", False)


def test_ac6b_truncate_various_multibyte():
    # 2 字节("中"=E4 B8 AD)+ 3 字节("你")+ 4 字节("𠀀"=F0 A0 80 80)
    s = "中你𠀀中你𠀀" * 50
    enc = s.encode("utf-8")
    for limit in [1, 2, 3, 4, 5, 7, 13, 100, 1000]:
        cut, _ = truncate_utf8(enc, limit)
        # 必须能 round-trip
        decoded = cut.decode("utf-8")
        # round-trip 等价
        assert decoded.encode("utf-8") == cut


# ============ AC7:关自动压缩 + 超限 → BlockingLimitError(不崩)============
@pytest.mark.asyncio
async def test_ac7_blocking_limit_when_disabled_and_over():
    cfg = GovConfig(autocompact_enabled=False)
    state = GovState()
    # 1 条消息,体量超限(单条 50K 字符,context_window=1000,限制 1000-3000=负 → 必然超)
    messages = [{"role": "user", "content": "x" * 50_000}]
    with pytest.raises(BlockingLimitError) as ex:
        await govern(messages, cfg, state, summarize=None, context_window=1000)
    assert ex.value.code == 7
    assert "auto-compact 已关" in str(ex.value)


@pytest.mark.asyncio
async def test_ac7b_blocking_limit_disabled_but_under():
    cfg = GovConfig(autocompact_enabled=False)
    state = GovState()
    messages = [{"role": "user", "content": "hi"}]
    # 不超 → 不抛
    out = await govern(messages, cfg, state, summarize=None, context_window=200_000)
    assert out == messages


# ============ 额外:govern 端到端(微缩)============
@pytest.mark.asyncio
async def test_extra_govern_pipeline_end_to_end():
    """小消息(<阈值)→ 不动;大消息(>microcompact 阈值)→ 占位;超 autocompact → 摘要。"""
    cfg = GovConfig(keep_recent_tool_results=2)
    state = GovState()
    # 5 个 Read 工具结果,总 5K 字符(<窗口)
    messages = []
    for i in range(5):
        messages.append({
            "role": "tool", "tool_use_id": f"id-{i}", "name": "Read",
            "content": "x" * 1000,
        })
    # 不超 microcompact 阈值 → 不动
    out = await govern(messages, cfg, state, summarize=None, context_window=200_000)
    assert out == messages

    # 触发 microcompact:用小窗口 + 大消息
    cfg2 = GovConfig(keep_recent_tool_results=2)
    # 50 个 Read,每个 5K 字符 = 250K 字符 ≈ 62.5K tokens
    # context_window=10K → microcompact_threshold=8.5K → 必然触发
    messages2 = []
    for i in range(50):
        messages2.append({
            "role": "tool", "tool_use_id": f"id-{i}", "name": "Read",
            "content": "x" * 5000,
        })
    out2 = await govern(messages2, cfg2, GovState(), summarize=None, context_window=10_000)
    # microcompact 触发 → 最近 2 个保留
    tool_results = [m for m in out2 if m["role"] == "tool"]
    kept = [m for m in tool_results if m["content"].startswith("xxxx")]
    assert len(kept) == 2  # keep_recent=2

    # autocompact 触发(给 summarize)
    async def sm(mids):
        return "summary of " + str(len(mids)) + " messages"
    cfg3 = GovConfig()
    # 5 条 user,每条 5K 字符 = 25K 字符 ≈ 6.25K tokens
    # context_window=10K → autocompact_threshold=10K-13K=负 → 用大窗口避歧义
    # 改用 context_window=1000(小窗口):autocompact_threshold = max(0, 1000-13K) = 0 → 必然触发
    messages3 = [{"role": "user", "content": "x" * 5000} for _ in range(5)]
    out3 = await govern(messages3, cfg3, GovState(), summarize=sm, context_window=1_000)
    # 应该有 summary 消息
    assert any("autocompact summary" in str(m.get("content", "")) for m in out3)


# ============ 额外:count_tokens_messages 粗估可用 ============
def test_extra_count_tokens_messages():
    msgs = [
        {"role": "system", "content": "x" * 100},
        {"role": "user", "content": "y" * 100},
    ]
    n = count_tokens_messages(msgs)
    # 100/4 + 4 + 100/4 + 4 = 58
    assert 50 <= n <= 70


# ============ AC8:v1.5 historical framing(参照 Hermes context_compressor.py)============
@pytest.mark.asyncio
async def test_ac8_autocompact_summary_has_historical_framing_prefix():
    """**M1.5** autocompact 生成的 summary 消息必须包 `HISTORICAL_FRAMING_PREFIX` 前缀。

    参照 Hermes `agent/context_compressor.py:43-69` 的 `SUMMARY_PREFIX`。
    4 条强约束(防 LLM 把摘要当 active instruction 继续执行):
    1. 这是 reference,不是 instruction
    2. 只回应最新 user 消息
    3. 反向信号(停/撤销/算了)终止 in-flight
    4. memory 永远胜过摘要

    失效模式:某天有人"简化"前缀 → LLM 看到摘要里"用户说做 X"就接着做,
    把本该终止的任务重新捡起 → 与结晶/断路器/边界 gate 形成死循环。
    """
    from karvyloop.context.autocompact import (
        HISTORICAL_FRAMING_PREFIX,
        autocompact,
    )
    from karvyloop.context.budget import GovConfig, GovState

    msgs = [
        {"role": "system", "content": "you are coder"},
        {"role": "user", "content": "task 1"},
        {"role": "assistant", "content": "ack 1"},
        {"role": "user", "content": "task 2"},
        {"role": "assistant", "content": "ack 2"},
        {"role": "user", "content": "latest msg — please do Y"},
    ]

    async def fake_summarize(middle):
        return "summary of middle turns"

    state = GovState()
    cfg = GovConfig()
    out = await autocompact(msgs, state, cfg, fake_summarize, keep_tail=2,
                            context_window=10_000)

    # 找 summary 消息(_meta.kind == "summary")
    summary_msgs = [m for m in out if m.get("_meta", {}).get("kind") == "summary"]
    assert len(summary_msgs) == 1, f"应正好 1 条 summary 消息;实际: {len(summary_msgs)}"
    sm = summary_msgs[0]
    content = sm["content"]
    # 1) 必含 HISTORICAL_FRAMING_PREFIX(整段)
    assert content.startswith(HISTORICAL_FRAMING_PREFIX), (
        f"summary 消息必须以 HISTORICAL_FRAMING_PREFIX 开头;实际起始: {content[:200]!r}"
    )
    # 2) 4 条关键约束各必现(中英 keyword 都可,确保跨 LLM 都 catch)
    must_contain = [
        "历史摘要",      # "historical summary"
        "仅作参考",      # "reference only"
        "不要",          # "do NOT" (负向)
        "最新 user",     # "latest user"
        "source of truth",
        "反向信号",      # "reverse signals"
        "memory",        # "memory 永远权威"中的关键词
    ]
    for k in must_contain:
        assert k in content, f"HISTORICAL_FRAMING_PREFIX 缺关键约束 {k!r};prefix: {HISTORICAL_FRAMING_PREFIX!r}"
    # 3) framed=True 标记(v1.5+ 用于给上层/审计可见)
    assert sm["_meta"].get("framed") is True, (
        f"summary 消息 _meta.framed 应为 True;实际: {sm['_meta']}"
    )


# ============ AC9:历史帧前缀是常量,且 export 出来(v1.5 公开 API)============
def test_ac9_historical_framing_prefix_is_exported():
    """**M1.5** HISTORICAL_FRAMING_PREFIX 公开导出,供 test / debug / 外部 audit 用。

    不 export 的话,审计/调试工具无法验证"v1.5 后所有 summary 都带前缀"——
    这是 v1.5 行为契约的一部分。
    """
    from karvyloop.context.autocompact import HISTORICAL_FRAMING_PREFIX
    import importlib
    # 注意:`from .autocompact import autocompact` 在 karvyloop/context/__init__.py:9
    # 把模块名 `autocompact` shadow 成了函数,所以 `import karvyloop.context.autocompact`
    # 拿到的是函数。必须用 importlib 强制按模块路径拿。
    autocompact_mod = importlib.import_module("karvyloop.context.autocompact")

    # 1) 模块顶层有 __all__ 包含
    assert "HISTORICAL_FRAMING_PREFIX" in autocompact_mod.__all__
    # 2) 直接 import 可用
    assert isinstance(HISTORICAL_FRAMING_PREFIX, str)
    assert len(HISTORICAL_FRAMING_PREFIX) > 100, (
        f"前缀太短可能没装下 4 条约束;实际长度: {len(HISTORICAL_FRAMING_PREFIX)}"
    )
