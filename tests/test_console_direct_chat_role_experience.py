"""test_console_direct_chat_role_experience — docs/56 审计 HIGH①/W1:直聊路径补角色经验沉淀。

病根:`_schedule_role_experience` 此前**唯一调用点**在委派 ACCEPT(proposal_handlers.py:290)——
用户直接跟业务角色私聊 / 群里 @ 它做完域内活儿,角色什么都学不到,"越用越懂域"只对走委派的用户
成立(飞轮半瘫)。修:ws.py / routes.py 的直聊 drive 成功收尾也触发沉淀。本文件锁两件事:

A. `_direct_chat_role_domain`(直聊归属解析器)对每种 peer 给对(域, 角色):
   - 业务域私聊 → (peer.domain_id, role/agent_id)
   - 私聊小卡(l0)/ 群协调场 → ("","")(内部保守门会拒 l0/无域,给空即可)
   - 群里 @ 一个业务角色 → 被 @ 角色的(域, 角色)
B. 触发点真写入(镜像 wired 代码调 `_schedule_role_experience`):真 MemoryManager 落一条
   role-scoped 经验 Belief;跨域跨角色隔离不破;l0 私聊小卡 → 零写入。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.ws import _direct_chat_role_domain  # noqa: E402
from karvyloop.console.proposal_handlers import _schedule_role_experience  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.roles import experience as EXP  # noqa: E402


# ---- 桩:LLM 层(经验编译器 stub / supersede 审查 stub 分路由),memory 走真写入(镜像 test_role_experience)
class TextDelta:   # 名字必须**恰好** "TextDelta":distill_experience 按 type(ev).__name__ 匹配
    def __init__(self, t):
        self.text = t


class _ScriptedGW:
    def __init__(self, exp_reply="[]"):
        self.exp_reply = exp_reply
        self.calls = []

    def resolve_model(self, scope):
        return "stub-model"

    async def complete(self, messages, tools, model_ref, *, system=None):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        self.calls.append(("supersede" if "一致性审查" in sys_text else "experience"))
        yield TextDelta('{"pairs":[]}' if "一致性审查" in sys_text else self.exp_reply)


class _Mgr:
    """极简 conversation manager:只需要 current_peer()。"""
    def __init__(self, peer):
        self._peer = peer

    def current_peer(self):
        return self._peer


def _app(mem, gw):
    st = types.SimpleNamespace(
        memory=mem,
        runtime_kwargs={"gateway": gw, "model_ref": "m"},
    )
    return types.SimpleNamespace(state=st)


# ================= A. 归属解析器 _direct_chat_role_domain =================

def test_resolver_business_peer():
    # 业务域私聊某角色 → (域, 角色)。agent 角色用 agent_id 当角色名。
    mgr = _Mgr(Address(domain_id="dom-fin", role="agent", agent_id="auditor"))
    dom, role = _direct_chat_role_domain(_app(None, None), mgr, mention="", mention_domain="")
    assert (dom, role) == ("dom-fin", "auditor")


def test_resolver_l0_karvy_returns_empty():
    # 私聊小卡(l0)→ ("","")(内部保守门会拒;这里不归属任何业务域)
    mgr = _Mgr(Address(domain_id="l0", role="agent", agent_id="karvy"))
    assert _direct_chat_role_domain(_app(None, None), mgr, mention="", mention_domain="") == ("", "")


def test_resolver_group_no_mention_returns_empty():
    # 群协调场(role=group)不 @ 人 → 小卡当协调者,不归属任何业务角色
    mgr = _Mgr(Address(domain_id="dom-team", role="group", agent_id=""))
    assert _direct_chat_role_domain(_app(None, None), mgr, mention="", mention_domain="") == ("", "")


def test_resolver_group_at_mention_resolves_to_role_domain(monkeypatch):
    # 群里 @ 一个业务角色 → 归属被 @ 角色的(域, 角色)
    import karvyloop.console.roundtable_engine as rte
    roster = [Address(domain_id="dom-fin", role="agent", agent_id="auditor"),
              Address(domain_id="dom-sales", role="agent", agent_id="closer")]
    monkeypatch.setattr(rte, "_roundtable_roster", lambda app, peer: roster)
    mgr = _Mgr(Address(domain_id="l0", role="group", agent_id=""))  # karvy world 大群
    dom, role = _direct_chat_role_domain(_app(None, None), mgr, mention="auditor", mention_domain="")
    assert (dom, role) == ("dom-fin", "auditor")


# ================= B. 触发点真写入(镜像 wired 直聊收尾) =================

def _fire_like_wired_code(app, mgr, *, intent, result_text, mention="", mention_domain=""):
    """完全按 ws.py/routes.py 直聊成功收尾里的两步调:解析归属 → 触发沉淀。
    (verified=True:直聊无独立 checker,干净完成即本路径最强成功信号 —— 见 wired 注释)"""
    dom, role = _direct_chat_role_domain(app, mgr, mention=mention, mention_domain=mention_domain)
    if dom and role:
        _schedule_role_experience(app, role=role, domain=dom, requirement=intent,
                                  result=result_text, success=True, verified=True)
    return dom, role


def test_direct_chat_success_sediments_role_scoped_experience():
    """直聊业务角色成功 → 真 cognition 库落一条 role-scoped 经验 Belief。"""
    mem = MemoryManager()
    gw = _ScriptedGW(exp_reply='[{"content":"finance报表先核对来源再引用","kind":"method"}]')
    app = _app(mem, gw)
    mgr = _Mgr(Address(domain_id="dom-fin", role="agent", agent_id="auditor"))

    dom, role = _fire_like_wired_code(app, mgr, intent="核对Q3报表", result_text="逐条核对了来源")
    assert (dom, role) == ("dom-fin", "auditor")
    # 触发点是 fire-and-forget:同步测试上下文里 _schedule 走 asyncio.run 直接跑完 → 已写入
    assert "experience" in gw.calls   # 真跑了经验编译 LLM(过了保守门)
    written = [b for b in mem.index.all("domain") if EXP.is_role_experience(b)]
    assert len(written) == 1
    b = written[0]
    assert b.provenance["applies"] == {"domain": "dom-fin", "role": "auditor"}
    assert b.scope == "domain"
    # 召回:同(域, 角色)拿得到自己的经验
    blk = EXP.collect_role_experiences(mem, domain="dom-fin", role="auditor", query="报表核对")
    assert "先核对来源" in blk


def test_direct_chat_cross_domain_and_role_isolation():
    """沉在 (dom-fin, auditor) 的经验:换域 / 换角色都召不到(隔离不破)。"""
    mem = MemoryManager()
    gw = _ScriptedGW(exp_reply='[{"content":"finance域机密方法","kind":"method"}]')
    app = _app(mem, gw)
    mgr = _Mgr(Address(domain_id="dom-fin", role="agent", agent_id="auditor"))
    _fire_like_wired_code(app, mgr, intent="做域内活", result_text="做法X")

    # 本(域, 角色)召得到
    assert "机密方法" in EXP.collect_role_experiences(
        mem, domain="dom-fin", role="auditor", query="机密方法")
    # 跨域:同角色名不同域 → 召不到
    assert "机密方法" not in EXP.collect_role_experiences(
        mem, domain="dom-sales", role="auditor", query="机密方法")
    # 跨角色:同域别的角色 → 召不到
    assert "机密方法" not in EXP.collect_role_experiences(
        mem, domain="dom-fin", role="closer", query="机密方法")


def test_direct_chat_with_karvy_l0_writes_nothing():
    """私聊小卡(l0)直聊成功 → 归属空 → 触发点不写(通用层不做角色经验,0 污染)。"""
    mem = MemoryManager()
    gw = _ScriptedGW(exp_reply='[{"content":"x","kind":"method"}]')
    app = _app(mem, gw)
    mgr = _Mgr(Address(domain_id="l0", role="agent", agent_id="karvy"))

    dom, role = _fire_like_wired_code(app, mgr, intent="随便聊聊", result_text="好的")
    assert (dom, role) == ("", "")
    assert gw.calls == []                              # 没跑 LLM
    assert list(mem.index.all("domain")) == []         # 零写入


def test_direct_chat_uses_synchronous_run_when_no_loop():
    """无 running loop(纯同步测试上下文)→ _schedule_role_experience 走 asyncio.run 同步跑完,
    仍真写入(证明触发点在同步收尾也不静默丢)。"""
    async def _outer():
        # 有 running loop 时:create_task fire-and-forget → 需等它跑完再断言
        mem = MemoryManager()
        gw = _ScriptedGW(exp_reply='[{"content":"async路径也沉","kind":"method"}]')
        app = _app(mem, gw)
        mgr = _Mgr(Address(domain_id="dom-fin", role="agent", agent_id="auditor"))
        _fire_like_wired_code(app, mgr, intent="i", result_text="r")
        # 排空 fire-and-forget 任务
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return mem

    mem = asyncio.run(_outer())
    written = [b for b in mem.index.all("domain") if EXP.is_role_experience(b)]
    assert len(written) == 1 and "async路径也沉" in written[0].content
