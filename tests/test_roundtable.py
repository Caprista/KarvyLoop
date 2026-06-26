"""test_roundtable — ch4 圆桌:多成员同场应答。

AC:
- AC1 每个成员各自应答 → 收集 N 份(保序)
- AC2 某成员抛错/空回 → 跳过,不拖垮整桌
- AC3 空 intent / 空成员 → []
- AC4 max_seats 封顶(超出的不上桌)
"""
from __future__ import annotations

import pytest

from karvyloop.karvy.roundtable import run_roundtable, run_roundtable_session


@pytest.mark.asyncio
async def test_each_member_answers():
    async def drive(m):
        return {"speaker": m, "text": f"{m} 的看法"}
    out = await run_roundtable("怎么选股?", ["设计师", "分析师", "风控"], drive_member=drive)
    assert [r["speaker"] for r in out] == ["设计师", "分析师", "风控"]   # 保序
    assert all("的看法" in r["text"] for r in out)


@pytest.mark.asyncio
async def test_failing_member_skipped():
    async def drive(m):
        if m == "坏的":
            raise RuntimeError("boom")
        if m == "空的":
            return {"speaker": m, "text": "  "}    # 空回 → 跳过
        return {"speaker": m, "text": "ok"}
    out = await run_roundtable("x", ["好的", "坏的", "空的", "好2"], drive_member=drive)
    assert [r["speaker"] for r in out] == ["好的", "好2"]   # 坏的/空的 被跳过,不拖垮整桌


@pytest.mark.asyncio
async def test_empty():
    async def drive(m):
        return {"speaker": m, "text": "x"}
    assert await run_roundtable("", ["a"], drive_member=drive) == []   # 空 intent
    assert await run_roundtable("q", [], drive_member=drive) == []     # 空成员


@pytest.mark.asyncio
async def test_max_seats_caps():
    called = []
    async def drive(m):
        called.append(m)
        return {"speaker": m, "text": "ok"}
    out = await run_roundtable("q", list(range(10)), drive_member=drive, max_seats=3)
    assert len(out) == 3 and len(called) == 3   # 只上 3 座


# ============ 小卡主持的圆桌(有界研究会)============
@pytest.mark.asyncio
async def test_session_converges_when_host_says_so():
    async def member_reply(m, topic, transcript):
        return {"speaker": m, "text": f"{m} 对「{topic}」的看法(已看到 {len(transcript)} 条)"}
    async def host(topic, transcript, *, final):
        if final:
            return {"text": "结论:综合大家的意见,选低估值+高股息"}
        return {"action": "converge" if len(transcript) >= 4 else "continue"}  # 第 2 轮收敛
    out = await run_roundtable_session("如何选股", ["分析师", "风控"],
                                       member_reply=member_reply, host_moderate=host, max_rounds=5)
    assert out["rounds"] == 2 and out["converged"] is True       # 第 2 轮收敛(没烧到 5)
    assert len(out["transcript"]) == 4                            # 2 轮 × 2 成员
    assert "结论" in out["conclusion"]                            # 小卡收敛出产出
    assert out["transcript"][0]["round"] == 1


@pytest.mark.asyncio
async def test_session_caps_at_max_rounds():
    async def member_reply(m, topic, transcript):
        return {"speaker": m, "text": "ok"}
    async def host(topic, transcript, *, final):
        return {"text": "勉强收个尾"} if final else {"action": "continue"}  # 永不主动收敛
    out = await run_roundtable_session("X", ["a"], member_reply=member_reply,
                                       host_moderate=host, max_rounds=3)
    assert out["rounds"] == 3 and out["converged"] is False       # 封顶 3 轮,不烧到底


@pytest.mark.asyncio
async def test_session_empty():
    async def m(x, tp, tr): return {"speaker": x, "text": "ok"}
    async def h(tp, tr, *, final): return {"text": "", "action": "converge"}
    assert (await run_roundtable_session("", ["a"], member_reply=m, host_moderate=h))["transcript"] == []
    assert (await run_roundtable_session("q", [], member_reply=m, host_moderate=h))["transcript"] == []
