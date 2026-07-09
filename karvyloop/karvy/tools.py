"""karvy/tools — 小卡(全局助手)对话里能直接调的能力工具工厂。

**病根**(审计 2026-07-08):小卡聊天 drive 只拿到最小工具集(读/写/编辑文件、run_command、
web_search/web_fetch、reconcile_receipt、create_atom,外加条件挂的 instantiate_domain_template)。
其余能力(定时任务、随聊记忆、角色、域)全是 REST-only —— 用户在聊天里说"每天早上提醒我复盘""记住
我讨厌开早会""你还记得我上次说的预算吗",小卡都够不着,只能引导用户去面板点。

这层按 `self_knowledge.make_instantiate_template_tool` **已验证的安全工厂 + 注入模式**,把三件能力
包成小卡可调用的 Tool(经 build_tool,HR-1;走 capability 护栏,policy 表配下限;在
`workbench.main_loop_bridge.drive_in_tui` 里按 `persona.karvy_self` + 相应 registry 存在才挂):

1. `make_create_schedule_tool` —— 定时任务(WORKSPACE_WRITE)。**设计:只有小卡能起定时任务**
   (scheduler.py:角色无调度工具,全系统唯一审计面),但小卡此前也没有工具 —— 创建只在控制台面板。
   这里补上:输入 = 自然语言排程描述(+ 可选委派角色),内部 NL→cron(schedule_parser)+ SchedulerStore.add,
   语义镜像 `POST /schedule/parse`→`/schedule/create`(先解析出 cron/intent,再创建)。
2. `make_remember_fact_tool` —— 随聊沉淀一条记忆(WORKSPACE_WRITE)。用户说"记住 X" → 写一条 Belief
   (MemoryManager.write,带 provenance/freshness,HR-7),来源标 `karvy_chat`。
3. `make_recall_memory_tool` —— 随聊回忆(READ_ONLY)。"你还记得关于 X 的事吗" → 走既有
   grep+overlap 召回(recall_block,**无向量**,house rule),把命中记忆块返给小卡。

诚实边界:cron 解析靠一次受限 LLM 调用,解析不出明确时间规律 → ok=False + reason(不瞎编时间);
记忆写入失败(落盘 fail-loud)如实回 persist_error;召回没命中 → 诚实返回空。工具永不穿透异常。
"""
from __future__ import annotations

from typing import Any


# ---- 1. 定时任务:create_schedule(只有小卡能起,scheduler.py 收口)----

def make_create_schedule_tool(*, scheduler_store: Any, schedule_parser: Any = None,
                              target_resolver: Any = None):
    """把 NL→cron 解析 + SchedulerStore.add 包成小卡可调用的 Tool。

    - `scheduler_store`:SchedulerStore 实例(REST 侧 `_scheduler_store(app)` 同一个,单一审计面)。
    - `schedule_parser`:make_schedule_parser 造的闭包 (description, now_str)->{cron,intent,...}|None;
      为空(--no-llm)→ 工具仍挂,但调用时诚实回"没接 LLM 解析不了"。
    - `target_resolver`:可选 (role_name)->(domain_id, role, agent_id, display);把委派角色名解析成
      定时任务的委派目标(REST 侧 `_resolve_schedule_target`)。为空 = 不解析,小卡自己到点跑。

    语义与 `POST /schedule/parse`+`/schedule/create` 一致:先解析(不懂时间就拒),再创建。
    policy 表下限 WORKSPACE_WRITE(写调度注册表,做事中写)。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        inp = inp or {}
        desc = str(inp.get("description") or "").strip()
        if not desc:
            return {"ok": False, "reason": "需要 description(用一句话说清什么时候做什么,如「每天早上8点提醒我复盘」)"}
        if schedule_parser is None:
            return {"ok": False, "reason": "没接 LLM,解析不了定时描述(--no-llm?)"}
        from karvyloop.karvy.schedule_parser import local_now_str
        try:
            parsed = schedule_parser(desc, local_now_str())
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常
            return {"ok": False, "reason": f"解析定时描述出错:{type(e).__name__}"}
        if not parsed:
            return {"ok": False, "reason": "没听懂明确的时间规律 —— 换种说法(如「每天/每周一/每小时 + 具体点数」)"}
        cron = str(parsed.get("cron") or "").strip()
        # intent:显式传了 action/intent 优先(小卡把"到点做什么"讲清),否则用解析出的 intent。
        intent = (str(inp.get("action") or "").strip()
                  or str(parsed.get("intent") or "").strip())
        if not (cron and intent):
            return {"ok": False, "reason": "解析结果缺 cron 或要做的事,没创建(可以说得更具体些)"}
        title = str(parsed.get("title") or "").strip()
        role_name = (str(inp.get("target_role") or "").strip()
                     or str(parsed.get("target_role") or "").strip())
        did = role = aid = ""
        if role_name and target_resolver is not None:
            try:
                did, role, aid, _disp = target_resolver(role_name)
            except Exception:
                did = role = aid = ""
        try:
            t = scheduler_store.add(cron, intent, title=title,
                                    target_domain=did, target_role=role, target_agent_id=aid)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"创建定时任务出错:{type(e).__name__}"}
        if t is None:
            return {"ok": False, "reason": "cron 非法或意图为空,没创建(定时描述再具体点)"}
        return {"ok": True, "id": t.id, "cron": t.cron, "intent": t.intent,
                "title": t.title, "target": (role or "") if not did else f"{did}/{role}"}

    return build_tool(
        name="create_schedule",
        description=(
            "创建一条定时任务(全系统只有你能起定时任务)。你把用户的排程需求用一句自然语言"
            "填进 description(如「每天早上8点把昨天进展汇总给我」「每周一9点提醒我交周报」),"
            "系统会把它解析成 cron 并创建。可选:action=到点具体要做的事(不填则用 description 里的意图);"
            "target_role=指定某个角色去做(不填=你自己到点跑)。**先跟用户确认时间和要做的事,再调用**;"
            "解析不出明确时间规律会被拒(如实转告用户换种说法,别瞎编时间)。"),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string",
                                "description": "自然语言排程需求(何时+做什么),如「每天早上8点提醒我复盘」"},
                "action": {"type": "string",
                           "description": "到点具体要做的事(可选;不填则用 description 里的意图)"},
                "target_role": {"type": "string",
                                "description": "委派给某角色去做(可选;不填=小卡自己到点跑)"},
            },
            "required": ["description"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    )


# ---- 2. 随聊记忆:remember_fact(写一条 Belief)----

def make_remember_fact_tool(*, memory: Any, agent_id: str = "user"):
    """把 MemoryManager.write 包成"记住这件事"工具(WORKSPACE_WRITE)。

    用户在对话里说"记住 X / 帮我记一下 Y" → 小卡把要记的这句话填进 content,写成一条 personal Belief
    (provenance 必带,HR-7:source=karvy_chat,ts=now)。落盘失败(fail-loud)如实回 persist_error。
    与 ingest/distill 同源(都产 Belief、都走 mem.write),区别只是触发口 = 小卡对话里即时记。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        content = str((inp or {}).get("content") or "").strip()
        if not content:
            return {"ok": False, "reason": "需要 content(要记住的那句话)"}
        if memory is None:
            return {"ok": False, "reason": "memory 未接(--no-llm?),记不了"}
        import time as _t
        from karvyloop.schemas import Belief
        now = _t.time()
        title = str((inp or {}).get("title") or "").strip()
        prov = {"source": "karvy_chat", "agent": agent_id, "ts": now,
                "trace_ref": "", "kind": "fact", "title": title[:64]}
        belief = Belief(content=content, provenance=prov, freshness_ts=now, scope="personal")
        try:
            persisted = memory.write(belief)
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常
            return {"ok": False, "reason": f"记忆写入出错:{type(e).__name__}: {e}"}
        # write() 返回**落盘**是否成功(断⑥):内存态已写但没持久化 → 诚实告知(重启会丢)。
        if not persisted:
            perr = getattr(memory, "persist_error", None)
            return {"ok": True, "persisted": False, "content": content,
                    "warning": f"已记进内存但没落盘(重启可能会丢):{perr or '未知'}"}
        return {"ok": True, "persisted": True, "content": content}

    return build_tool(
        name="remember_fact",
        description=(
            "把用户要你长期记住的一件事,沉淀进他的个人记忆库(下次对话/其它角色能召回)。"
            "用户说「记住…/帮我记一下…/别忘了…」时用:content 填要记住的那句话(用第三人称陈述,"
            "如「用户讨厌开早会」而不是「你讨厌开早会」);可选 title 给个短标题。"
            "只记用户明确要你记的事,别把闲聊都往里塞。"),
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的事实(第三人称陈述句)"},
                "title": {"type": "string", "description": "短标题(可选)"},
            },
            "required": ["content"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    )


# ---- 3. 随聊回忆:recall_memory(grep+overlap 召回,无向量)----

def make_recall_memory_tool(*, memory: Any):
    """把 MemoryManager.recall_block 包成"你还记得 X 吗"工具(READ_ONLY)。

    走既有 grep + token-overlap + 语义标签重叠召回路径(house rule:**不上向量**),
    把命中的记忆块返给小卡。只读、无副作用 → policy 表 READ_ONLY 下限 + 进 deontic_gate 只读豁免。
    没命中 → 诚实返回空(found=False),让小卡如实说"没记过这个"。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        query = str((inp or {}).get("query") or "").strip()
        if not query:
            return {"ok": False, "reason": "需要 query(要回忆关于什么)"}
        if memory is None:
            return {"ok": False, "reason": "memory 未接(--no-llm?),查不了"}
        try:
            limit = int((inp or {}).get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(limit, 20))
        try:
            block = memory.recall_block(query, scope="personal", limit=limit)
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常
            return {"ok": False, "reason": f"召回出错:{type(e).__name__}: {e}"}
        block = block or ""
        return {"ok": True, "found": bool(block.strip()), "memory": block}

    return build_tool(
        name="recall_memory",
        description=(
            "查用户的个人记忆库,回忆关于某事你之前记住过什么(用户问「你还记得…吗/我之前说过…」时用)。"
            "query 填要回忆的主题/关键词;返回命中的记忆片段(没命中就如实说没记过,别编)。"
            "只读,不会改动任何东西。"),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要回忆的主题/关键词"},
                "limit": {"type": "integer", "description": "最多召回几条(默认8,上限20)"},
            },
            "required": ["query"],
        },
        call=_call,
        required_mode=Mode.READ_ONLY,
    )


__all__ = [
    "make_create_schedule_tool",
    "make_remember_fact_tool",
    "make_recall_memory_tool",
]
