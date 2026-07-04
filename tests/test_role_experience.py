"""test_role_experience — 角色经验沉淀(role 越用越懂它那个域,docs/54 模块1 Top2)。

不变量:
① 保守门:任务成功+过独立验收+有域 → 沉淀 role-scoped Belief(真 cognition 写入,LLM stub)
② 保守门拒:纯失败无纠正 / 无域(l0) / 无信号 → 零 LLM 零写入
③ 用户纠正反馈 = 最富信号:即便任务未"成功"也沉淀
④ role-scoped Belief 形状:source=role_experience,applies={domain,role},scope=domain
⑤ 同(域,角色)drive → 召回到自己的经验;query 相关性排序(无向量)
⑥ **跨域不漏**:A 域角色经验不进 B 域召回((域,角色)隔离)
⑦ **跨角色不串**:同域别的角色召不到本角色经验
⑧ 矛盾经验走 supersede(复用刚接线的冲突消解):旧经验被打 invalid_at
⑨ 可见(source 标签)+ 可删(remove_by_content)
⑩ 不碰角色七文件:沉淀只写 Belief,角色 registry/soul 一字不改
⑪ 解析宁空勿毒:垃圾/prose → []
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402
from karvyloop.roles import experience as E  # noqa: E402


# ---- 桩:LLM 层 stub(经验编译器 + supersede 审查器分路由),memory 走真写入 ----

class TextDelta:
    def __init__(self, t):
        self.text = t


class ScriptedGW:
    def __init__(self, *, exp_reply="[]", supersede_reply='{"pairs":[]}'):
        self.exp_reply = exp_reply
        self.supersede_reply = supersede_reply
        self.calls = []

    def resolve_model(self, scope):
        return "stub-model"

    async def complete(self, messages, tools, model_ref, *, system=None):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        material = messages[0]["content"] if messages else ""
        if "一致性审查" in sys_text:
            self.calls.append(("supersede", material))
            yield TextDelta(self.supersede_reply)
        else:
            self.calls.append(("experience", material))
            yield TextDelta(self.exp_reply)


def _run(coro):
    return asyncio.run(coro)


def _sig(**kw):
    base = dict(role="审计师", domain="finance", requirement="核对Q3报表",
                result="逐条核对了来源", success=True, verified=True, ts=time.time())
    base.update(kw)
    return E.TaskOutcomeSignal(**base)


# ============ ① 保守门放行:成功+验收+有域 → 真写入 ============

def test_sediment_on_verified_success():
    mem = MemoryManager()
    gw = ScriptedGW(exp_reply='[{"content":"finance报表先核对来源再引用","kind":"method"}]')
    written = _run(E.sediment_experience(_sig(), mem=mem, gateway=gw, model_ref="m"))
    assert len(written) == 1
    b = written[0]
    assert E.is_role_experience(b)
    # 真进了 cognition 库
    assert any(x.content == b.content for x in mem.index.all("domain"))


# ============ ② 保守门拒:纯失败 / 无域 / 无信号 → 零 LLM 零写入 ============

def test_gate_rejects_plain_failure():
    mem = MemoryManager()
    gw = ScriptedGW(exp_reply='[{"content":"x","kind":"method"}]')
    written = _run(E.sediment_experience(
        _sig(success=False, verified=False, correction=""), mem=mem, gateway=gw))
    assert written == []
    assert gw.calls == []           # 没跑 LLM
    assert list(mem.index.all("domain")) == []


def test_gate_rejects_no_domain():
    mem = MemoryManager()
    gw = ScriptedGW(exp_reply='[{"content":"x","kind":"method"}]')
    for dom in ("", "l0"):
        written = _run(E.sediment_experience(_sig(domain=dom), mem=mem, gateway=gw))
        assert written == []
    assert gw.calls == []


def test_gate_rejects_success_but_unverified():
    # 成功但没过独立验收(inconclusive/无 checker)→ 不沉(非平凡方法要验证过才可信)
    assert E.should_distill(_sig(success=True, verified=False, correction="")) is False


# ============ ③ 用户纠正 = 最富信号:即便未成功也沉 ============

def test_correction_signal_sediments_even_on_failure():
    mem = MemoryManager()
    gw = ScriptedGW(exp_reply='[{"content":"finance分页从0开始别从1","kind":"pitfall"}]')
    written = _run(E.sediment_experience(
        _sig(success=False, verified=False, correction="你分页参数错了,从0开始"),
        mem=mem, gateway=gw))
    assert len(written) == 1
    assert written[0].provenance["kind"] == "pitfall"


# ============ ④ role-scoped Belief 形状 ============

def test_belief_shape():
    b = E.make_experience_belief("方法X", "method", domain="finance", role="审计师")
    assert b.provenance["source"] == "role_experience"
    assert b.provenance["applies"] == {"domain": "finance", "role": "审计师"}
    assert b.provenance["agent"] == "审计师"
    assert b.scope == "domain"


# ============ ⑤ 同(域,角色)召回到自己的经验 ============

def test_recall_same_domain_role():
    mem = MemoryManager()
    mem.write(E.make_experience_belief("finance报表先核对来源", "method",
                                       domain="finance", role="审计师"))
    blk = E.collect_role_experiences(mem, domain="finance", role="审计师", query="报表核对")
    assert "先核对来源" in blk
    assert "审计师" in blk   # 块头标了角色


# ============ ⑥ 跨域不漏(核心隔离)============

def test_cross_domain_isolation():
    mem = MemoryManager()
    mem.write(E.make_experience_belief("finance机密方法", "method",
                                       domain="finance", role="审计师"))
    # 同角色名、不同域 → 召不到(A 域经验不进 B 域)
    blk_other = E.collect_role_experiences(mem, domain="sales", role="审计师", query="机密方法")
    assert "finance机密方法" not in blk_other
    # 本域召得到
    blk_own = E.collect_role_experiences(mem, domain="finance", role="审计师", query="机密方法")
    assert "finance机密方法" in blk_own


# ============ ⑦ 跨角色不串(同域别的角色)============

def test_cross_role_isolation_same_domain():
    mem = MemoryManager()
    mem.write(E.make_experience_belief("审计师私有经验", "method",
                                       domain="finance", role="审计师"))
    blk_other_role = E.collect_role_experiences(mem, domain="finance", role="出纳", query="私有经验")
    assert "审计师私有经验" not in blk_other_role


# ============ ⑧ 矛盾经验走 supersede(复用冲突消解)============

def test_conflicting_experience_supersede():
    mem = MemoryManager()
    # 库里已有旧经验
    old = E.make_experience_belief("finance分页从1开始", "pitfall",
                                   domain="finance", role="审计师")
    mem.write(old)
    # 新任务沉出矛盾经验;supersede 审查器判 update → 旧条被打 invalid
    gw = ScriptedGW(exp_reply='[{"content":"finance分页从0开始","kind":"pitfall"}]',
                    supersede_reply='{"pairs":[{"new":0,"old":0,"relation":"update"}]}')
    written = _run(E.sediment_experience(
        _sig(correction="分页从0开始"), mem=mem, gateway=gw))
    assert len(written) == 1
    # 旧条被失效不删(仍在库,invalid_at 置)
    olds = [b for b in mem.index.all("domain") if b.content == "finance分页从1开始"]
    assert len(olds) == 1
    assert olds[0].invalid_at is not None
    # 召回默认过滤失效条 → 只召到新经验
    blk = E.collect_role_experiences(mem, domain="finance", role="审计师", query="分页")
    assert "从0开始" in blk and "从1开始" not in blk


# ============ ⑨ 可见 + 可删(走既有通用路径)============

def test_visible_source_label_and_removable():
    mem = MemoryManager()
    b = E.make_experience_belief("可删经验", "method", domain="finance", role="审计师")
    mem.write(b)
    # 可见:recent 带 source 标签
    recents = mem.recent(limit=10, scope="domain", domain="finance")
    hit = [x for x in recents if x.content == "可删经验"]
    assert hit and hit[0].provenance["source"] == "role_experience"
    # 可删:remove_by_content(用户数据主权)
    n = mem.remove_by_content({"可删经验"})
    assert n == 1
    assert not any(x.content == "可删经验" for x in mem.index.all("domain"))


def test_purge_domain_cascades_role_experience():
    # 删域时角色的域私有经验随域清(applies.domain 匹配)
    mem = MemoryManager()
    mem.write(E.make_experience_belief("finance经验", "method", domain="finance", role="审计师"))
    mem.write(E.make_experience_belief("sales经验", "method", domain="sales", role="销售"))
    n = mem.purge_domain("finance")
    assert n == 1
    remaining = [b.content for b in mem.index.all("domain")]
    assert "finance经验" not in remaining and "sales经验" in remaining


# ============ ⑩ 不碰角色七文件 ============

def test_does_not_touch_role_files():
    from karvyloop.roles.registry import RoleRegistry
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        reg = RoleRegistry(pathlib.Path(td))
        view = reg.create("auditor", identity="严谨的财务审计", nickname="审计师")
        rid = view.id
        before_soul = reg.get(rid).identity
        mem = MemoryManager()
        gw = ScriptedGW(exp_reply='[{"content":"经验X","kind":"method"}]')
        _run(E.sediment_experience(
            E.TaskOutcomeSignal(role=rid, domain="finance", requirement="r",
                                success=True, verified=True), mem=mem, gateway=gw))
        # 角色 identity 一字未改(经验是 belief 增量,不覆盖人设)
        assert reg.get(rid).identity == before_soul
        # 经验去了 cognition 库,不是角色文件
        assert any(E.is_role_experience(b) for b in mem.index.all("domain"))


# ============ ⑪ 解析宁空勿毒 ============

def test_parse_refuses_garbage():
    assert E.parse_experiences("") == []
    assert E.parse_experiences("这是一段散文,不是JSON,别抽成经验") == []
    assert E.parse_experiences('[{"content":"坏了') == []           # 像 JSON 但截断 → []
    assert E.parse_experiences('{"oops": true}') == []              # 无 content → []
    ok = E.parse_experiences('[{"content":"好经验","kind":"method"}]')
    assert ok == [{"content": "好经验", "kind": "method"}]
    # 未知 kind → 归 method
    assert E.parse_experiences('[{"content":"c","kind":"weird"}]')[0]["kind"] == "method"


def test_empty_distill_writes_nothing():
    # LLM 判无可沉(返 [])→ 门放行也不写
    mem = MemoryManager()
    gw = ScriptedGW(exp_reply="[]")
    written = _run(E.sediment_experience(_sig(), mem=mem, gateway=gw))
    assert written == []
    assert list(mem.index.all("domain")) == []
