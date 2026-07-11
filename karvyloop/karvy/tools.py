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


# ---- 4. 建角色:create_role(物化一个 agent 目录进角色库)----

def make_create_role_tool(*, role_registry: Any):
    """把 RoleRegistry.create 包成"从对话里建一个角色"工具(WORKSPACE_WRITE)。

    小卡跟用户聊清楚要一个什么角色(它是谁 identity、性格原则 soul、可选花名/职务/模型),
    直接落一个合法 agent 目录进公共角色库(7 文件 + COMPOSITION.yaml,COMMITMENT 自动 seed 尽责契约)。
    语义镜像 `POST /roles/create`(routes_roles):同一个 RoleRegistry.create,单一审计面。

    诚实边界:role_id 非法(空/含空格路径符)或已存在 → RoleRegistry.create 抛,工具捕获转成
    ok=False + reason(不炸、不盖旧角色)。原子先不挑(atom_ids=[]),角色随后可从原子库加/自造。
    policy 表下限 WORKSPACE_WRITE(写角色注册表,做事中写)。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        inp = inp or {}
        role_id = str(inp.get("role_id") or "").strip()
        if not role_id:
            return {"ok": False, "reason": "需要 role_id(角色名,如「设计师」;只能含字母/数字/下划线/连字符)"}
        if role_registry is None:
            return {"ok": False, "reason": "role_registry 未接,建不了角色"}
        identity = str(inp.get("identity") or "").strip()
        soul = str(inp.get("soul") or "").strip()
        nickname = str(inp.get("nickname") or "").strip()
        title = str(inp.get("title") or "").strip()
        model = str(inp.get("model") or "").strip()
        try:
            view = role_registry.create(
                role_id, identity=identity, soul=soul,
                nickname=nickname, title=title, model=model, atom_ids=[])
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常(含 DuplicateRoleError/非法名)
            return {"ok": False, "reason": f"建角色失败:{type(e).__name__}: {e}"}
        return {"ok": True, "id": view.id, "identity": view.identity,
                "nickname": view.nickname, "title": view.title, "model": view.model,
                "display": view.display_name()}

    return build_tool(
        name="create_role",
        description=(
            "从对话里给用户建一个新角色(落进他的角色库,之后能入职业务域、被 @ 协作)。"
            "先跟用户聊清楚要个什么角色再建:role_id=角色名(如「设计师」,只能含字母/数字/下划线/连字符,"
            "不能重名);identity=它是谁/负责什么(一句话人设);soul=性格原则/工作风格(可选);"
            "可选 nickname=花名、title=职务、model=指定模型。**别擅自建,确认清楚了再调**。"),
        input_schema={
            "type": "object",
            "properties": {
                "role_id": {"type": "string",
                            "description": "角色名/唯一 id(字母/数字/下划线/连字符,支持中文,不能重名)"},
                "identity": {"type": "string", "description": "它是谁、负责什么(一句话人设)"},
                "soul": {"type": "string", "description": "性格原则/工作风格(可选)"},
                "nickname": {"type": "string", "description": "花名(可选,进某域时的人名)"},
                "title": {"type": "string", "description": "职务(可选,如「产品经理」)"},
                "model": {"type": "string", "description": "指定模型(可选,不填=层叠默认)"},
            },
            "required": ["role_id", "identity"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    )


# ---- 5. 建业务域:create_domain(开一个业务域,可选子域)----

def make_create_domain_tool(*, domain_registry: Any, domain_store: Any = None,
                            created_by_user: str = "user"):
    """把 BusinessDomainRegistry.create / create_child 包成"从对话里开个业务域"工具(WORKSPACE_WRITE)。

    小卡跟用户聊清楚要开个什么业务域(名字、价值观 value.md、可选强护栏 forbid/oblige、可选父域),
    直接落一个业务域进注册表(有 domain_store → 存盘,与 `POST /domain/create` 同持久语义)。
    传了 parent_id → 走 create_child(继承父域 value.md + deontic,只能加不能删,D5)。

    诚实边界:同名 active 域已存在 → 拒(防注册表被同名灌满);父域不存在/已归档 → registry 抛,
    工具捕获转 ok=False + reason(不炸)。member_query 只含建域用户(角色以后再入职,同 REST 空角色语义)。
    policy 表下限 WORKSPACE_WRITE(写业务域注册表,做事中写)。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        inp = inp or {}
        name = str(inp.get("name") or "").strip()
        if not name:
            return {"ok": False, "reason": "需要 name(业务域名字,如「我的理财所」)"}
        if domain_registry is None:
            return {"ok": False, "reason": "domain_registry 未接,开不了业务域"}
        # 查重:已有同名 active 域 → 拒(镜像 REST 建域查重,防注册表被同名灌满)
        try:
            _nm = name.lower()
            _dup = next((d for d in domain_registry.list_active()
                         if (getattr(d, "name", "") or "").strip().lower() == _nm), None)
            if _dup is not None:
                return {"ok": False, "reason": f"已有同名业务域「{name}」(换个名字,或先归档旧的那个)"}
        except Exception:
            pass  # 查重失败不挡建域(降级)
        # value.md:空=空灵魂(合法);非空补「# 价值观」头(镜像 REST 建域)
        raw_value = str(inp.get("value_md") or "").strip()
        if not raw_value:
            value_md = ""
        elif raw_value.startswith("# 价值观"):
            value_md = raw_value
        else:
            value_md = f"# 价值观\n\n{raw_value}"
        # 强护栏 deontic:forbid/oblige 列表(可选;确定性可拦的那部分由 deontic_gate 硬闸兜)
        from karvyloop.domain.deontic import Deontic

        def _as_list(v: Any) -> tuple[str, ...]:
            if not v:
                return ()
            if isinstance(v, str):
                v = [v]
            return tuple(str(x).strip() for x in v if str(x).strip())
        deontic = Deontic(forbid=_as_list(inp.get("forbid")), oblige=_as_list(inp.get("oblige")))
        created_by = f"user:{created_by_user or 'user'}"
        member_query = f"user:{created_by_user or 'user'}"
        parent_id = str(inp.get("parent_id") or "").strip()
        try:
            if parent_id:
                domain = domain_registry.create_child(
                    parent_id=parent_id, name=name, created_by=created_by,
                    deontic_override=deontic, member_query=member_query)
            else:
                domain = domain_registry.create(
                    name=name, created_by=created_by, value_md_raw=value_md,
                    deontic=deontic, member_query=member_query)
        except Exception as e:  # noqa: BLE001 —— 工具永不穿透异常(父域不存在/已归档/value.md 非法)
            return {"ok": False, "reason": f"开业务域失败:{type(e).__name__}: {e}"}
        # 存盘(域是用户数据,默认持久;与 REST 同语义)。落盘失败如实回 warning,不假装存上了。
        persisted = True
        persist_warn = ""
        if domain_store is not None:
            try:
                domain_store.save_all(domain_registry.list_all())
            except Exception as e:  # noqa: BLE001
                persisted = False
                persist_warn = f"{type(e).__name__}: {e}"
        out = {"ok": True, "id": domain.id, "name": domain.name,
               "parent_id": domain.parent_id or "", "persisted": persisted}
        if not persisted:
            out["warning"] = f"业务域已建但没落盘(重启可能会丢):{persist_warn}"
        return out

    return build_tool(
        name="create_domain",
        description=(
            "从对话里给用户开一个业务域(把一群角色组织起来做一摊事的场,如「理财所」「我的自媒体工作室」)。"
            "先跟用户聊清楚再开:name=业务域名字(不能与现有域重名);value_md=这个域的价值观/做事原则(可选);"
            "forbid=这个域里绝不能做的事(可选,列表,如「未经确认直接下单」);oblige=必须做到的事(可选);"
            "parent_id=在某个已有域下开子域(可选,子域继承父域的价值观和护栏)。**别擅自开,确认清楚了再调**。"),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "业务域名字(不能重名),如「我的理财所」"},
                "value_md": {"type": "string", "description": "价值观/做事原则(可选,自然语言)"},
                "forbid": {"type": "array", "items": {"type": "string"},
                           "description": "这个域里绝不能做的事(可选)"},
                "oblige": {"type": "array", "items": {"type": "string"},
                           "description": "这个域里必须做到的事(可选)"},
                "parent_id": {"type": "string",
                              "description": "父域 id(可选;传了=在它下面开子域,继承其价值观+护栏)"},
            },
            "required": ["name"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    )


# ---- 6. 跨 runtime 协作:把活派给一个外部 runtime 公民(external_agent 等三件)----
#
# 设计(docs/71+72):让小卡把"别人家的 agent 运行时"当频道公民拉进来、@ 它派活、拿真实回复。
# 外部执行体 = opaque、归属外部主人、输出恒 untrusted、只供稿不占决策席、H2A 采纳才升记忆。
# 中性词纪律:代码/注释走 external_runtime/bridge/公民,不点参照工程名。
#
# 五步接线(与 make_create_schedule_tool 同族):
#   步1 工厂(本文件)→ 步2 注入(main_loop_bridge.drive_in_tui,gated on karvy_self)→
#   步3 下限表(capability/policy.py:external_agent=FULL / attach_external_agent=WORKSPACE_WRITE /
#        list_external_agents=READ_ONLY)→ 步4 deontic_gate(list 进只读豁免)→
#   步5 tool_catalog(三名进 BUILTIN_TOOL_NAMES 防 unresolved 误判)。


def _build_task_assign(citizen, task: str):
    """把"派活给外部公民"构造成 A2A TASK_ASSIGN 信封(from_=user, by=(karvy,);A1/A3)。

    外部执行体**永不作为 envelope 的 from_ 主体**(它无签名身份)——from_ 是发起人(user),
    by=(karvy,) 中间人,origin=external:<id> 作为 payload 数据字段标来源(#71 §3.3)。
    返回 (envelope, task_id)。
    """
    import time as _t

    from karvyloop.a2a import Envelope, EnvelopeType, TaskPayload, sign_envelope
    from karvyloop.domain import Address
    from karvyloop.external_runtime import citizen_address

    domain_id = getattr(citizen, "domain_id", "") or ""
    task_id = f"ext-{citizen.citizen_id}-{int(_t.time() * 1000)}"
    frm = Address(domain_id=domain_id, role="user", agent_id="ch")
    karvy = Address(domain_id=domain_id, role="observer", agent_id="karvy")
    to = citizen_address(domain_id, citizen.citizen_id)
    payload = TaskPayload(task_id=task_id, description=task,
                          context={"origin": f"external:{citizen.citizen_id}",
                                   "provenance": "untrusted"})
    env = Envelope(type=EnvelopeType.TASK_ASSIGN.value, from_=frm, by=(karvy,),
                   to=to, payload=payload, ts=str(_t.time()))
    env = dataclasses_replace_signature(env, sign_envelope(env))
    return env, task_id


def dataclasses_replace_signature(env, signature):
    """Envelope 是 frozen dataclass —— 用 dataclasses.replace 挂签名(构造后签)。"""
    import dataclasses as _dc
    return _dc.replace(env, signature=signature)


def make_external_agent_tool(*, citizen_registry, bridge_factory, a2a_router=None,
                             token_recorder=None):
    """把'派活给一个外部 runtime 公民'包成小卡可调用的 Tool(FULL 下限——起子进程=process_spawn+network)。

    - citizen_registry: ExternalCitizenRegistry(解析 citizen_id → ExternalCitizen)。
    - bridge_factory: (DriveRecipe) -> Bridge(§3.1;起子进程、fail-loud、密钥过滤)。
    - a2a_router: 可选 EnvelopeRouter(派活走信封 + 审计链;须用 citizen-aware resolver 构造,
      否则 to=Address(域, external, id) 解析不到 → REJECT_NO_TARGET)。为空 = 跳过 route(仍可跑)。
    - token_recorder: 可选 (source, usage_dict)->None:把外部 usage 记进独立 ext: 账本(§6)。

    安全:输出恒 untrusted(§4.1),落数据通道不进对话主线;工具永不穿透异常。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token, sandbox) -> Any:
        inp = inp or {}
        citizen_id = str(inp.get("citizen_id") or "").strip()
        task = str(inp.get("task") or "").strip()
        if not (citizen_id and task):
            return {"ok": False, "reason": "需要 citizen_id(哪个外部同事)+ task(让它做什么)"}
        if citizen_registry is None:
            return {"ok": False, "reason": "external_runtime 未接,派不了活"}
        citizen = citizen_registry.resolve(citizen_id)
        if citizen is None:
            return {"ok": False, "reason": f"没有叫「{citizen_id}」的外部同事(先接入)"}
        from karvyloop.external_runtime import STATUS_ACTIVE
        if citizen.status != STATUS_ACTIVE:
            return {"ok": False, "reason": f"「{citizen_id}」当前不可达({citizen.status}),检查它的 runtime"}
        recipe = citizen.recipe()
        if recipe is None:
            return {"ok": False, "reason": f"「{citizen_id}」没有可用配方(runtime_kind={citizen.runtime_kind})"}
        # use-time hash 复验(防 rug-pull):**每次派活前**复验目标二进制/配方指纹是否还对得上
        # attach 时 pin 的值;漂移(有人换了目标 runtime)→ fail-loud 返 needs_reattach,绝不静默
        # 跑一个被换过的 runtime(#71 §4 MCP rug-pull 实战教训:import 时审一次不够)。
        from karvyloop.external_runtime import verify_manifest_hash
        hv = verify_manifest_hash(recipe, citizen.manifest_hash)
        if not hv.ok:
            return {"ok": False, "needs_reattach": True, "citizen": citizen_id,
                    "reason": f"「{citizen_id}」指纹复验不过(疑似被换过),没派活 —— 重新接入:{hv.reason}"}
        # 派活构造成 A2A 信封走 router(前置接线:router 须用 citizen-aware resolver,否则 REJECT_NO_TARGET)
        task_id = ""
        if a2a_router is not None:
            try:
                env, task_id = _build_task_assign(citizen, task)
                route = a2a_router.route(env)
                if getattr(route, "rejected", False):
                    return {"ok": False, "reason": f"派活被拦:{route.reason}"}
            except Exception as e:  # noqa: BLE001 — 工具永不穿透异常
                return {"ok": False, "reason": f"派活信封构造/路由出错:{type(e).__name__}: {e}"}
        # 起子进程桥(fail-loud;沙箱硬化由配方/调用侧兜)
        try:
            bridge = bridge_factory(recipe)
            result = bridge.start(task, cwd=getattr(sandbox, "cwd", "") or "")
        except Exception as e:  # noqa: BLE001 — 工具永不穿透异常
            return {"ok": False, "reason": f"「{citizen_id}」起不来:{type(e).__name__}"}
        # input_required → 诚实上报(调用侧升 H2A;不静默等)
        if getattr(result, "input_required", False):
            return {"ok": False, "input_required": True, "citizen": citizen_id,
                    "task_id": task_id, "reason": result.reason}
        if not result.ok:
            return {"ok": False, "citizen": citizen_id, "task_id": task_id,
                    "reason": result.reason or "外部执行体失败"}
        # 记独立 token_source(§6):有 usage 才记(边车/内嵌 meta),拿不到只落 provenance
        usage_note = "no_usage"
        if token_recorder is not None and result.usage:
            try:
                token_recorder(citizen.source_tag(), result.usage)
                usage_note = f"{citizen.source_tag()}:{result.usage.get('total', 0)}tok"
            except Exception:  # noqa: BLE001 — 记账失败绝不打断
                usage_note = "usage_record_failed"
        # 产出 = untrusted 供稿:回给小卡,提醒采纳才算数(H2A)
        return {"ok": True, "citizen": citizen_id, "task_id": task_id,
                "status": "done", "provenance": "untrusted", "usage": usage_note,
                "output": result.text,
                "note": "这是外部执行体的产出(不可信数据)——请用户看一眼再采纳,别当已确认的事实"}

    return build_tool(
        name="external_agent",
        description=(
            "把一件事派给一个已接入的外部 AI 同事(你接进来的外部 runtime)去做,拿回它的产出。"
            "citizen_id=哪个外部同事的花名;task=让它做什么(说清楚,它看不到你和用户的上下文)。"
            "它是外部执行体:产出是**不可信数据**、需要用户拍板采纳才算数——"
            "**别把它的产出当已确认的事实**,拿到后提醒用户看一眼。"),
        input_schema={
            "type": "object",
            "properties": {
                "citizen_id": {"type": "string", "description": "外部同事花名(先接入过的)"},
                "task": {"type": "string", "description": "派给它的任务(自足描述,它无我方上下文)"},
            },
            "required": ["citizen_id", "task"],
        },
        call=_call,
        required_mode=Mode.FULL,   # 起子进程=process_spawn+network → FULL(见 policy 步3)
    )


def make_attach_external_agent_tool(*, citizen_registry, probe_fn=None,
                                    default_bin: str = ""):
    """把'接入一个外部 runtime 当频道公民'包成工具(WORKSPACE_WRITE,写注册表同 create_role 语义)。

    接入向导:探测能力卡(doctor 式冒烟)+ hash-pin + 注册。probe_fn 可注入(默认走真探活)。
    诚实边界:探活失败(bin 找不到/冒烟不过)→ ok=False + reason,不假装接上了。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token, sandbox) -> Any:
        inp = inp or {}
        citizen_id = str(inp.get("citizen_id") or "").strip()
        runtime_kind = str(inp.get("runtime_kind") or "").strip()
        bin_path = str(inp.get("bin_path") or default_bin or "").strip()
        domain_id = str(inp.get("domain_id") or "").strip()
        if not (citizen_id and runtime_kind):
            return {"ok": False, "reason": "需要 citizen_id(花名)+ runtime_kind(外部 runtime 类型)"}
        if citizen_registry is None:
            return {"ok": False, "reason": "external_runtime 未接,接不了外部同事"}
        from karvyloop.external_runtime import (
            ExternalCitizen, STATUS_ACTIVE, TIER_GUEST, TIER_SCOPED, builtin_recipe,
            normalize_tier,
        )
        # tier:guest(T0 客人,现状)/ scoped(T1 受限成员,绑定单域深度协作)。deny-by-default:
        # 未知 tier 值一律归 guest(normalize_tier);scoped **必须**给 domain_id(单域绑定)。
        tier = normalize_tier(str(inp.get("tier") or TIER_GUEST))
        if tier == TIER_SCOPED and not domain_id:
            return {"ok": False, "reason": "受限成员(tier=scoped)必须绑定一个业务域(给 domain_id)"}
        import dataclasses as _dc
        base = builtin_recipe(runtime_kind)
        if base is None:
            from karvyloop.external_runtime import builtin_kinds
            return {"ok": False, "reason": f"未知 runtime_kind「{runtime_kind}」(支持:{list(builtin_kinds())})"}
        recipe = _dc.replace(base, bin_path=bin_path or base.bin_path)
        _probe = probe_fn
        if _probe is None:
            from karvyloop.external_runtime import probe as _probe
        try:
            pr = _probe(recipe)
        except Exception as e:  # noqa: BLE001 — 工具永不穿透异常
            return {"ok": False, "reason": f"探活出错:{type(e).__name__}: {e}"}
        if not pr.ok:
            return {"ok": False, "reason": f"「{citizen_id}」接入失败(探活不过):{pr.reason}"}
        citizen = ExternalCitizen(
            citizen_id=citizen_id, runtime_kind=runtime_kind, bin_path=bin_path,
            domain_id=domain_id, capability_card=pr.capability_card,
            token_source=f"ext:{citizen_id}", manifest_hash=pr.manifest_hash,
            created_by="user", status=STATUS_ACTIVE, tier=tier)
        persisted = citizen_registry.add(citizen)
        out = {"ok": True, "citizen": citizen_id, "runtime_kind": runtime_kind,
               "status": STATUS_ACTIVE, "tier": tier, "domain_id": domain_id,
               "version": pr.version, "persisted": persisted}
        if not persisted:
            out["warning"] = f"已接入但没落盘(重启可能丢):{citizen_registry.persist_error}"
        return out

    return build_tool(
        name="attach_external_agent",
        description=(
            "接入一个外部 AI runtime,注册成常驻频道同事(之后能被 @ 派活)。先跟用户聊清楚再接:"
            "citizen_id=给它起个花名(如「cc」);runtime_kind=它是哪类外部 runtime;"
            "bin_path=它在用户机器上的可执行路径(可选,有默认);domain_id=挂到哪个业务域(可选);"
            "tier=成员等级:「guest」=客人(默认,派个活拿产出、无域深度)/「scoped」=受限成员"
            "(绑定单个业务域深度协作,只读该域公共料、写的都可撤,绝不碰域私有认知)——选 scoped 必须给 domain_id。"
            "系统会先探活(冒烟)确认它真能跑,跑不起来会如实告诉你接入失败,别假装接上了。"),
        input_schema={
            "type": "object",
            "properties": {
                "citizen_id": {"type": "string", "description": "外部同事花名(唯一)"},
                "runtime_kind": {"type": "string", "description": "外部 runtime 类型"},
                "bin_path": {"type": "string", "description": "可执行路径(可选,有默认)"},
                "domain_id": {"type": "string", "description": "挂载到哪个业务域(可选;tier=scoped 时必填)"},
                "tier": {"type": "string", "enum": ["guest", "scoped"],
                         "description": "成员等级:guest=客人(默认)/ scoped=受限成员(绑定单域深度协作)"},
            },
            "required": ["citizen_id", "runtime_kind"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,   # 写公民注册表 → 同 create_role
    )


def make_list_external_agents_tool(*, citizen_registry):
    """把'列出已接入的外部同事'包成工具(READ_ONLY,只读注册表,同 recall_memory)。"""
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token, sandbox) -> Any:
        if citizen_registry is None:
            return {"ok": False, "reason": "external_runtime 未接"}
        try:
            citizens = citizen_registry.list_all()
        except Exception as e:  # noqa: BLE001 — 工具永不穿透异常
            return {"ok": False, "reason": f"列举出错:{type(e).__name__}"}
        return {"ok": True, "count": len(citizens), "agents": [
            {"citizen_id": c.citizen_id, "runtime_kind": c.runtime_kind,
             "status": c.status, "domain_id": c.domain_id,
             "version": (c.capability_card or {}).get("version", "")}
            for c in citizens]}

    return build_tool(
        name="list_external_agents",   # 复数!与 policy 键 / catalog 逐字对齐(R1:防命名漂移落回 FULL)
        description=(
            "列出用户已接入的所有外部 AI 同事(花名/类型/是否可达)。用户问「我接了哪些外部 agent」时用。"
            "只读,不改动任何东西。"),
        input_schema={"type": "object", "properties": {}, "required": []},
        call=_call,
        required_mode=Mode.READ_ONLY,
    )


def make_revoke_external_agent_tool(*, citizen_registry):
    """把 'scoped 优雅撤销一个外部成员' 包成工具(WORKSPACE_WRITE,写注册表同 attach)。

    撤一个成员 = detach(domain, citizen_id):**不 kill 整个域**——已被 H2A 采纳的产出=已是
    用户数据不级联删,未采纳的供稿清理,撤销可追溯(返回它 seed 过哪些认知:哪些保留/哪些清)。
    诚实边界:没有此成员 → ok=False + reason(不假装撤了)。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token, sandbox) -> Any:
        inp = inp or {}
        citizen_id = str(inp.get("citizen_id") or "").strip()
        domain_id = str(inp.get("domain_id") or "").strip()
        if not citizen_id:
            return {"ok": False, "reason": "需要 citizen_id(要撤销哪个外部成员)"}
        if citizen_registry is None:
            return {"ok": False, "reason": "external_runtime 未接,撤不了外部成员"}
        try:
            ok = citizen_registry.detach(domain_id, citizen_id)
        except Exception as e:  # noqa: BLE001 — 工具永不穿透异常
            return {"ok": False, "reason": f"撤销出错:{type(e).__name__}: {e}"}
        if not ok:
            return {"ok": False, "reason": f"没有叫「{citizen_id}」的外部成员(在域「{domain_id or '(无域)'}」)"}
        trace = getattr(citizen_registry, "last_detach_trace", {}) or {}
        return {"ok": True, "citizen": citizen_id, "domain_id": domain_id,
                "kept_adopted": trace.get("kept_adopted", []),      # 已采纳:保留(用户数据)
                "cleared_unadopted": trace.get("cleared_unadopted", []),  # 未采纳:已清
                "note": "成员已撤销;它被采纳过的产出已是你的数据,原地保留;未采纳的供稿已清理"}

    return build_tool(
        name="revoke_external_agent",
        description=(
            "撤销一个已接入的外部 AI 成员(用户说「把 X 请出去 / 不用 X 了 / 撤了 X」时用)。"
            "citizen_id=要撤谁;domain_id=它挂在哪个域(不填=无域挂载)。优雅撤销:它被你采纳过的"
            "产出已是你的数据、原地保留;没采纳的供稿会清掉。不会因撤一个人就动整个域。"),
        input_schema={
            "type": "object",
            "properties": {
                "citizen_id": {"type": "string", "description": "要撤销的外部成员花名"},
                "domain_id": {"type": "string", "description": "它挂载的业务域(不填=无域)"},
            },
            "required": ["citizen_id"],
        },
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    )


__all__ = [
    "make_create_schedule_tool",
    "make_remember_fact_tool",
    "make_recall_memory_tool",
    "make_create_role_tool",
    "make_create_domain_tool",
    "make_external_agent_tool",
    "make_attach_external_agent_tool",
    "make_list_external_agents_tool",
    "make_revoke_external_agent_tool",
]
