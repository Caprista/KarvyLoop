"""test_ingest — loop step4b-1:摄入时编译器(个人知识库第一块)。

AC:
- AC1 parse_facts:JSON dict 数组 / 字符串数组 / ```json 围栏 / 按行兜底 / 空
- AC2 ingest_material:编译出的事实 → 逐条写进 MemoryManager,带 provenance(source=ingest,
  ts=now,kind)+ freshness=now + scope=personal
- AC3 空材料 → 不调模型、written=0
- AC4 空内容事实跳过;write 抛错的事实计入 skipped(不崩)
- AC5 compile_material 用受限调用(无工具)+ INGEST_SYSTEM,收集 TextDelta
"""
from __future__ import annotations

import pytest

from karvyloop.cognition import ingest as I


# ---- AC1 parse_facts ----
def test_parse_json_dicts():
    out = I.parse_facts('[{"content":"用户是 Hardy","kind":"fact"},{"content":"偏好英文默认","kind":"preference"}]')
    assert [f["content"] for f in out] == ["用户是 Hardy", "偏好英文默认"]
    assert out[1]["kind"] == "preference"


def test_parse_json_strings():
    out = I.parse_facts('["事实A","事实B"]')
    assert [f["content"] for f in out] == ["事实A", "事实B"]
    assert all(f["kind"] == "fact" for f in out)


def test_parse_fenced_json():
    out = I.parse_facts('```json\n[{"content":"X"}]\n```')
    # title 是新增的正交字段(短标题),比较时剥掉只看 content/kind
    assert [{k: v for k, v in f.items() if k != "title"} for f in out] == [{"content": "X", "kind": "fact"}]


def test_parse_fallback_lines():
    out = I.parse_facts("- 第一条\n* 第二条\n3. 第三条")
    assert [f["content"] for f in out] == ["第一条", "第二条", "第三条"]


def test_parse_empty():
    assert I.parse_facts("") == []
    assert I.parse_facts("[]") == []


# ---- #1 回归:malformed 输出绝不投毒长期库 ----
def test_parse_dict_wrapper_unwrapped():
    # 模型常返 {"facts":[...]} 而非裸数组 → 解包,别整坨当一条
    out = I.parse_facts('{"facts":[{"content":"A"},{"content":"B"}]}')
    assert [f["content"] for f in out] == ["A", "B"]


def test_parse_invalid_json_in_fence_returns_empty():
    # ```json 里是坏 JSON → 宁可空,绝不把 [{bad}] 整坨写进记忆
    assert I.parse_facts("```json\n[{bad json}]\n```") == []


def test_parse_prose_blob_returns_empty():
    # 模型不听话返 prose 段落(无 bullet)→ 不抽(否则一整段成一条垃圾"事实")
    assert I.parse_facts("用户可能喜欢猫,也可能喜欢狗,我不太确定。") == []


def test_parse_json_object_garbage_not_poisoned():
    # 像 JSON 开头但整体不可解析 → 返回 [](不走 prose 兜底投毒)
    assert I.parse_facts('{"oops": unterminated') == []


def test_parse_real_bullet_list_salvaged():
    # 显式 bullet 列表(非 JSON、非 prose)→ 救得回
    out = I.parse_facts("用户偏好:\n- 喜欢简洁\n- 默认英文")
    # "用户偏好:" 无 marker 不算;两条 bullet 抽出
    assert [f["content"] for f in out] == ["喜欢简洁", "默认英文"]


def test_parse_outer_fence_only_preserves_interior():
    # 只剥外层围栏:content 里合法的多行 JSON(含内部内容)不被删穿(NEW-1 回归)
    txt = '```json\n[\n  {"content": "用户喜欢 markdown"}\n]\n```'
    assert [{k: v for k, v in f.items() if k != "title"} for f in I.parse_facts(txt)] == [{"content": "用户喜欢 markdown", "kind": "fact"}]


def test_parse_drops_overlong_and_brace_lines():
    out = I.parse_facts("- " + "x" * 400 + "\n- 含{花括号}的行\n- 正常")
    assert [f["content"] for f in out] == ["正常"]


# ---- 桩 ----
class TextDelta:
    def __init__(self, t):
        self.text = t


class FakeGW:
    """老网关桩:complete 签名**不认** response_schema kwarg → 触发 ingest 侧优雅降级
    (捕 TypeError 剥掉重调)。用来锁"不支持约束解码时试点仍产出"。"""
    def __init__(self, reply):
        self.reply = reply
        self.seen_msgs = None
        self.seen_tools = None
        self.seen_system = None

    async def complete(self, messages, tools, model_ref, *, system=None):
        self.seen_msgs = messages
        self.seen_tools = tools
        self.seen_system = system
        for ch in self.reply:
            yield TextDelta(ch)


class FakeGWSchema:
    """新网关桩:complete 接 response_schema kwarg → 记下来供断言"schema 被透传"。"""
    def __init__(self, reply):
        self.reply = reply
        self.seen_schema = "unset"

    async def complete(self, messages, tools, model_ref, *, system=None, response_schema=None):
        self.seen_schema = response_schema
        for ch in self.reply:
            yield TextDelta(ch)


class FakeMem:
    def __init__(self, fail_on=None):
        self.written = []
        self.fail_on = fail_on or set()

    def write(self, belief, *, pinned=False):
        if belief.content in self.fail_on:
            raise ValueError("bad belief")
        self.written.append(belief)


# ---- AC2 ----
@pytest.mark.asyncio
async def test_ingest_writes_structured_beliefs():
    gw = FakeGW('[{"content":"用户住在杭州","kind":"fact"},{"content":"喜欢简洁回答","kind":"preference"}]')
    mem = FakeMem()
    res = await I.ingest_material("一些关于我的材料", gateway=gw, mem=mem,
                                  model_ref="m", agent_id="hardy", now=1000.0)
    assert res.written == 2 and len(mem.written) == 2
    b0 = mem.written[0]
    assert b0.content == "用户住在杭州" and b0.scope == "personal"
    assert b0.freshness_ts == 1000.0
    assert b0.provenance["source"] == "ingest" and b0.provenance["ts"] == 1000.0
    assert b0.provenance["agent"] == "hardy" and b0.provenance["kind"] == "fact"
    assert mem.written[1].provenance["kind"] == "preference"


# ---- AC3 空材料 ----
@pytest.mark.asyncio
async def test_ingest_empty_material_no_model_call():
    gw = FakeGW("[]")
    mem = FakeMem()
    res = await I.ingest_material("   ", gateway=gw, mem=mem, now=1.0)
    assert res.written == 0 and mem.written == []
    assert gw.seen_msgs is None          # 没调模型


# ---- AC4 跳过 ----
@pytest.mark.asyncio
async def test_ingest_skips_empty_and_failed():
    # 空 content 在 parse_facts 阶段就被剔掉(不进 ingest);写入失败的那条计 skipped
    gw = FakeGW('[{"content":"  "},{"content":"坏的"},{"content":"好的"}]')
    mem = FakeMem(fail_on={"坏的"})
    res = await I.ingest_material("material", gateway=gw, mem=mem, now=2.0)
    assert res.written == 1 and res.skipped == 1   # parse 丢空白条 → 只剩"坏的"(写失败)+"好的"
    assert [b.content for b in mem.written] == ["好的"]


# ---- #2 回归:now=0.0 不被静默吞 + 真 MemoryManager 验证不漂移 ----
@pytest.mark.asyncio
async def test_ingest_now_zero_writes_against_real_manager():
    from karvyloop.cognition.memory import MemoryManager
    gw = FakeGW('[{"content":"epoch-zero 事实"}]')
    mem = MemoryManager()
    res = await I.ingest_material("material", gateway=gw, mem=mem, now=0.0)  # epoch 0 合法
    assert res.written == 1 and res.skipped == 0          # 不再静默吞
    assert mem.index.all("personal")[0].content == "epoch-zero 事实"


@pytest.mark.asyncio
async def test_ingest_real_manager_catches_validation():
    # 用真 MemoryManager(不是 FakeMem)→ 抓 Belief 校验漂移
    from karvyloop.cognition.memory import MemoryManager
    gw = FakeGW('[{"content":"真事实","kind":"preference"}]')
    mem = MemoryManager()
    res = await I.ingest_material("m", gateway=gw, mem=mem, agent_id="hardy", now=5.0)
    assert res.written == 1
    b = mem.index.all("personal")[0]
    assert b.scope == "personal" and b.provenance["source"] == "ingest"
    assert b.provenance["kind"] == "preference" and b.freshness_ts == 5.0


# ---- AC5 受限调用 ----
@pytest.mark.asyncio
async def test_compile_uses_restricted_call_with_system():
    gw = FakeGW('["事实"]')
    facts = await I.compile_material("素材", gateway=gw, model_ref="m")
    assert [{k: v for k, v in f.items() if k != "title"} for f in facts] == [{"content": "事实", "kind": "fact"}]
    assert gw.seen_tools == []                              # 无工具(纯抽取)
    assert any(I.INGEST_SYSTEM in s for s in gw.seen_system.static)  # 喂了编译器 system
    assert gw.seen_msgs[0]["content"] == "素材"             # 材料进 user 消息


# ---- 约束解码底层试点:schema 透传 + 不支持时优雅降级 ----
@pytest.mark.asyncio
async def test_compile_threads_facts_schema_when_supported():
    """网关接 response_schema → 试点把 facts 的 json_schema 透传下去(约束解码底层),
    产出仍是合法结构(上层 parse_facts 二层兜底不动)。"""
    gw = FakeGWSchema('[{"content":"用户在杭州","kind":"fact"}]')
    facts = await I.compile_material("素材", gateway=gw, model_ref="m")
    # schema 被透传(非 None、是 facts 数组 schema)
    assert isinstance(gw.seen_schema, dict) and gw.seen_schema.get("type") == "array"
    assert gw.seen_schema["items"]["properties"]["content"]["type"] == "string"
    # 上层严校验仍产出合法结构
    assert [f["content"] for f in facts] == ["用户在杭州"]


@pytest.mark.asyncio
async def test_compile_degrades_when_gateway_lacks_schema_kwarg():
    """老网关/桩不认 response_schema kwarg → ingest 捕 TypeError 剥掉重调,试点仍产出(不崩)。"""
    gw = FakeGW('[{"content":"用户在上海","kind":"fact"}]')   # 无 response_schema kwarg
    facts = await I.compile_material("素材", gateway=gw, model_ref="m")
    assert [f["content"] for f in facts] == ["用户在上海"]     # 退回无约束路径仍产出


@pytest.mark.asyncio
async def test_ingest_material_still_works_with_schema_gateway():
    """端到端:带 schema 网关走完整 ingest_material,facts 写进真 MemoryManager。"""
    from karvyloop.cognition.memory import MemoryManager
    gw = FakeGWSchema('[{"content":"用户喜欢简洁","kind":"preference"}]')
    mem = MemoryManager()
    res = await I.ingest_material("材料", gateway=gw, mem=mem, agent_id="hardy", now=7.0)
    assert res.written == 1
    b = mem.index.all("personal")[0]
    assert b.content == "用户喜欢简洁" and b.provenance["kind"] == "preference"
