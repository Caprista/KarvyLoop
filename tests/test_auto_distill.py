"""test_auto_distill — loop step4b:对话自动蒸馏(轮后自动把对话编译进知识库)。

复用 4b-1 摄入编译器(source=conversation);批量(攒够 N 轮才蒸,省 token)、watermark
防重复蒸。地基已让 Belief 库真活,这步不再是孤儿。

AC:
- AC1 format_turns:user/assistant 拼成材料;空轮跳过
- AC2 should_distill:阈值走冷启动 warmup(1→2→4→稳态 batch);稳态与旧固定 batch 一致
- AC3 distill_turns:复用 ingest_material,source=conversation,写进 mem
- AC4 distill_turns:空材料 → written=0,不调模型
- AC5 maybe_auto_distill:未攒够 → 不蒸;攒够 → 蒸新轮 + watermark 推进 + 不重复蒸
- AC6 maybe_auto_distill:无 memory / 无 gateway / 无对话 → 跳过,不崩
"""
from __future__ import annotations

import types

import pytest

from karvyloop.cognition import auto_distill as AD


class _Turn:
    def __init__(self, u, a):
        self.user_intent = u
        self.agent_response = a


# ---- AC1 ----
def test_format_turns():
    out = AD.format_turns([_Turn("你好", "你好呀"), _Turn("", "只有助手"), _Turn("只有用户", "")])
    assert "用户: 你好" in out and "小卡: 你好呀" in out
    assert "只有助手" in out and "只有用户" in out


def test_format_turns_empty():
    assert AD.format_turns([]) == ""
    assert AD.format_turns([_Turn("", "")]) == ""


# ---- AC2(冷启动 warmup:1→2→4→稳态)----
def test_warmup_batch_ladder():
    # 阈值阶梯:watermark 0→1轮 / 1→2轮 / 2,3→4轮 / 4+→稳态 batch
    assert AD.warmup_batch(0) == 1
    assert AD.warmup_batch(1) == 2
    assert AD.warmup_batch(2) == 4
    assert AD.warmup_batch(3) == 4
    assert AD.warmup_batch(4) == AD.DISTILL_BATCH
    assert AD.warmup_batch(100) == AD.DISTILL_BATCH


def test_warmup_batch_explicit_batch_semantics():
    # batch 只定稳态阈值;warmup 阶梯与 batch 取 min 封顶 —— 只会更早蒸,绝不比稳态晚
    assert AD.warmup_batch(0, batch=8) == 1
    assert AD.warmup_batch(1, batch=8) == 2
    assert AD.warmup_batch(3, batch=8) == 4
    assert AD.warmup_batch(4, batch=8) == 8     # 稳态跟 batch 走(指数 1→2→4→8)
    assert AD.warmup_batch(0, batch=2) == 1
    assert AD.warmup_batch(2, batch=2) == 2     # 阶梯 4 > batch 2 → 封顶,不拖慢
    assert AD.warmup_batch(5, batch=2) == 2


def test_should_distill_warmup_sequence():
    # 数列效果:第 1 轮蒸 → 第 3 轮蒸 → 第 7 轮蒸 → 以后每 4 轮(冷启动第一天就有记忆感)
    assert AD.should_distill(1, 0) is True      # 新对话第 1 轮就蒸
    assert AD.should_distill(2, 1) is False
    assert AD.should_distill(3, 1) is True      # 第 3 轮
    assert AD.should_distill(6, 3) is False
    assert AD.should_distill(7, 3) is True      # 第 7 轮
    assert AD.should_distill(10, 7) is False
    assert AD.should_distill(11, 7) is True     # 之后回到稳态:每 4 轮


def test_should_distill_steady_state_unchanged():
    # 稳态(watermark ≥ 4)与旧固定 batch 行为完全一致
    assert AD.should_distill(8, 4, batch=4) is True   # 又攒够一批
    assert AD.should_distill(7, 4, batch=4) is False
    assert AD.should_distill(12, 8, batch=4) is True
    # 旧版 should_distill(3, 0)=False 在 warmup 下改为 True —— 这正是冷启动修复本身
    assert AD.should_distill(3, 0, batch=4) is True


# ---- 桩 ----
class TextDelta:
    def __init__(self, t):
        self.text = t


class FakeGW:
    def __init__(self, reply):
        self.reply = reply
        self.called = 0

    async def complete(self, messages, tools, model_ref, *, system=None):
        self.called += 1
        yield TextDelta(self.reply)


class FakeMem:
    def __init__(self):
        self.written = []

    def write(self, belief, *, pinned=False):
        self.written.append(belief)


# ---- AC3/AC4 ----
@pytest.mark.asyncio
async def test_distill_turns_writes_conversation_beliefs():
    gw = FakeGW('[{"content":"用户喜欢简洁","kind":"preference"}]')
    mem = FakeMem()
    res = await AD.distill_turns([_Turn("少废话", "好的")], gateway=gw, mem=mem, now=1.0)
    assert res.written == 1
    b = mem.written[0]
    assert b.provenance["source"] == "conversation"   # 标 conversation 来源
    assert b.scope == "personal"


@pytest.mark.asyncio
async def test_distill_turns_empty_no_model_call():
    gw = FakeGW("[]")
    mem = FakeMem()
    res = await AD.distill_turns([_Turn("", "")], gateway=gw, mem=mem, now=1.0)
    assert res.written == 0 and gw.called == 0          # 空材料不调模型


# ---- AC5 watermark + 批量 ----
_L0 = "l0"  # KARVY_WORLD_DOMAIN(私聊)


def _conv(cid, turns, *, domain_id=_L0):
    return types.SimpleNamespace(
        id=cid, turns=turns,
        peer=types.SimpleNamespace(domain_id=domain_id, role="observer", agent_id="karvy"),
    )


def _app(mem, gw):
    st = types.SimpleNamespace(
        memory=mem,
        runtime_kwargs={"gateway": gw, "model_ref": "m"},
        distill_watermarks={},
    )
    return types.SimpleNamespace(state=st)


def _mgr(conv):
    return types.SimpleNamespace(current=lambda: conv)


@pytest.mark.asyncio
async def test_maybe_auto_distill_batches_and_watermarks():
    from karvyloop.console.routes import maybe_auto_distill
    # P1b:maybe_auto_distill 改走组合抽取(facts+decisions 一次调用)→ 桩按组合对象格式返
    gw = FakeGW('{"facts":[{"content":"事实","kind":"fact"}],"decisions":[]}')
    mem = FakeMem()
    conv = _conv("c1", [_Turn("u0", "a0")])
    app = _app(mem, gw)
    mgr = _mgr(conv)

    # 冷启动 warmup:新对话第 1 轮就蒸(立刻有"记得你"信号)
    res = await maybe_auto_distill(app, mgr)
    assert res is not None and res["written"] == 1
    assert app.state.distill_watermarks["c1"] == 1      # watermark 推进到 1

    # 第 2 轮:warmup 阈值 2,只攒 1 轮 → 不蒸
    conv.turns.append(_Turn("u1", "a1"))
    assert await maybe_auto_distill(app, mgr) is None

    # 第 3 轮 → 蒸;watermark 推进到 3
    conv.turns.append(_Turn("u2", "a2"))
    res = await maybe_auto_distill(app, mgr)
    assert res is not None and res["written"] == 1
    assert app.state.distill_watermarks["c1"] == 3

    # 再调(还是 3 轮)→ 不重复蒸
    assert await maybe_auto_distill(app, mgr) is None


@pytest.mark.asyncio
async def test_maybe_auto_distill_steady_state_unchanged():
    # 稳态(watermark ≥ 4):与旧固定 batch 行为一致,攒够 4 轮才蒸
    from karvyloop.console.routes import maybe_auto_distill
    gw = FakeGW('{"facts":[{"content":"事实","kind":"fact"}],"decisions":[]}')
    conv = _conv("c1", [_Turn(f"u{i}", f"a{i}") for i in range(7)])
    app = _app(FakeMem(), gw)
    app.state.distill_watermarks["c1"] = 4
    mgr = _mgr(conv)

    # 7 - 4 = 3 < 4 → 不蒸
    assert await maybe_auto_distill(app, mgr) is None
    assert gw.called == 0

    # 加到 8 轮 → 又攒够一批
    conv.turns.append(_Turn("u7", "a7"))
    res = await maybe_auto_distill(app, mgr)
    assert res is not None
    assert app.state.distill_watermarks["c1"] == 8


@pytest.mark.asyncio
async def test_domain_conversation_not_distilled_to_personal():
    # 业务域对话(domain_id != l0)→ 不蒸进个人库(personal/domain 隔离硬规则,#4)
    from karvyloop.console.routes import maybe_auto_distill
    gw = FakeGW('[{"content":"x"}]')
    conv = _conv("biz", [_Turn(f"u{i}", f"a{i}") for i in range(5)], domain_id="装修")
    app = _app(FakeMem(), gw)
    assert await maybe_auto_distill(app, _mgr(conv)) is None
    assert gw.called == 0


@pytest.mark.asyncio
async def test_inflight_guard_blocks_concurrent():
    # in-flight 闸:同一对话已在蒸 → 跳过(防并发重复蒸,#1)
    from karvyloop.console.routes import maybe_auto_distill
    gw = FakeGW('[{"content":"x"}]')
    conv = _conv("c1", [_Turn(f"u{i}", f"a{i}") for i in range(4)])
    app = _app(FakeMem(), gw)
    app.state._distill_inflight = {"c1"}                # 假装已在飞
    assert await maybe_auto_distill(app, _mgr(conv)) is None
    assert gw.called == 0


@pytest.mark.asyncio
async def test_true_concurrency_only_one_distills():
    # 真并发:两个 task 同时跑同一对话 → in-flight 闸 + await 前推进 → 只蒸一次(#1 核心保证)
    import asyncio as _aio
    gate = _aio.Event()

    class BlockingGW:
        called = 0

        async def complete(self, *a, **k):
            BlockingGW.called += 1
            await gate.wait()          # 卡住,模拟慢 LLM,制造真重叠窗口
            yield TextDelta('[{"content":"x"}]')

    from karvyloop.console.routes import maybe_auto_distill
    conv = _conv("c1", [_Turn(f"u{i}", f"a{i}") for i in range(4)])
    app = _app(FakeMem(), BlockingGW())
    mgr = _mgr(conv)
    t1 = _aio.create_task(maybe_auto_distill(app, mgr))
    t2 = _aio.create_task(maybe_auto_distill(app, mgr))
    await _aio.sleep(0)                # 让两个 task 都过临界区
    gate.set()                          # 放行
    await _aio.gather(t1, t2)
    assert BlockingGW.called == 1       # 只有一个真去蒸(另一个被 in-flight/水位闸掉)


@pytest.mark.asyncio
async def test_failure_advances_watermark_no_retry():
    # 蒸馏失败 → watermark 仍推进(该批跳过,不每轮重试 hammer,#2)
    from karvyloop.console.routes import maybe_auto_distill

    class BoomGW:
        called = 0

        async def complete(self, *a, **k):
            BoomGW.called += 1
            raise RuntimeError("gateway down")
            yield  # pragma: no cover

    conv = _conv("c1", [_Turn(f"u{i}", f"a{i}") for i in range(4)])
    app = _app(FakeMem(), BoomGW())
    res = await maybe_auto_distill(app, _mgr(conv))
    assert res is None
    assert app.state.distill_watermarks["c1"] == 4      # 推进了 → 下轮不重试该批
    assert "c1" not in app.state._distill_inflight        # in-flight 已清


@pytest.mark.asyncio
async def test_no_conversation_skips():
    from karvyloop.console.routes import maybe_auto_distill
    app = _app(FakeMem(), FakeGW("[]"))
    assert await maybe_auto_distill(app, _mgr(None)) is None      # 无当前对话
    assert await maybe_auto_distill(app, _mgr(_conv("c", []))) is None  # 空轮


# ---- AC6 跳过条件 ----
@pytest.mark.asyncio
async def test_maybe_auto_distill_no_memory():
    from karvyloop.console.routes import maybe_auto_distill
    app = types.SimpleNamespace(state=types.SimpleNamespace(memory=None))
    assert await maybe_auto_distill(app, _mgr(None)) is None


@pytest.mark.asyncio
async def test_maybe_auto_distill_no_gateway():
    from karvyloop.console.routes import maybe_auto_distill
    conv = _conv("c", [_Turn("u", "a")] * 5)
    st = types.SimpleNamespace(memory=FakeMem(), runtime_kwargs={}, distill_watermarks={})
    app = types.SimpleNamespace(state=st)
    assert await maybe_auto_distill(app, _mgr(conv)) is None


# ---- schedule_auto_distill(fire-and-forget)----
import asyncio


@pytest.mark.asyncio
async def test_schedule_creates_and_retains_task():
    from karvyloop.console.routes import schedule_auto_distill
    # 最小 app:maybe_auto_distill 会因 memory=None 快速返回 None
    app = types.SimpleNamespace(state=types.SimpleNamespace(memory=None))
    schedule_auto_distill(app, _mgr(None))
    assert len(app.state._distill_tasks) == 1            # 任务被创建并保引用(防 GC)
    await asyncio.sleep(0)                               # 让 task 跑完
    await asyncio.sleep(0)
    assert len(app.state._distill_tasks) == 0            # done_callback 清掉


def test_schedule_no_running_loop_graceful():
    # 同步上下文(无事件循环)→ 不崩,静默返回
    from karvyloop.console.routes import schedule_auto_distill
    app = types.SimpleNamespace(state=types.SimpleNamespace(memory=None))
    schedule_auto_distill(app, _mgr(None))               # 不抛
    assert not hasattr(app.state, "_distill_tasks") or len(app.state._distill_tasks) == 0
