"""system_import — 多 agent 系统导入:SYSTEM_TRIAGE(一次 LLM 出 IR)+ 确定性翻译 + 确定性落地。

docs/84 #3:**LLM 读懂,确定性翻译,人拍板**。三段分离(承既有 migrate plan/apply 参照):
  ① `system_triage`:一次受限 LLM 调用把 bundle(agents[] + topology 原样)读成严格 JSON IR。
     宁空勿毒(坏 JSON → None,调用方降级"逐个当单 agent 导"+ 拓扑丢失如实报);
     封顶 agents≤24 / edges≤64 / teams≤8;悬空引用丢。**IR 只活在 plan 阶段,
     不持久化、不进本体论**(本体论还是 域/角色/原子/workflow 模板/圆桌那几样)。
  ② `translate_to_plan`:纯函数,IR → ImportPlan(dict)。映射表(docs/84 §翻译器):
     系统→业务域(使命→value.md/禁令→deontic/成员=member_query)/嵌套团队→子域/
     流水线+并行汇聚+单条件路由+失败策略→workflow 模板(when/inputs/on_fail)/
     群聊辩论→圆桌种子(seed_intents)/supervisor 静态分派→路由权上移(移位,报告)。
     **诚实降级 degradations[] 逐条 {element, why, fallback}**:动态路由/循环/
     agent→agent 汇报链(**绝不造 role→role 问责链**,宪法:问责只有 role→人/atom→role)/
     共享黑板/定时常驻。降级是报给人拍的,不是静默吞。
  ③ `apply_system_plan`:确定性落地(零 LLM),顺序 = 原子→角色(RoleRegistry.create 自动
     seed 尽责契约)→域+子域(create/create_child)→WorkflowStore 模板(provenance:import)
     →圆桌种子(H2A 提案卡,人拍了才真开桌)。失败回滚不留孤儿;同名活跃域拒。

plan 阶段零写盘;apply 只吃人审过的 plan(判型可改/模板可编)。
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Optional

from karvyloop import i18n
from karvyloop.adapter.bootstrap import AtomProposal, _strip_outer_fence
from karvyloop.adapter.source import SystemBundle

# ---- 封顶(docs/84:所有 LLM 控制、可能落盘/落库的集合都要封顶)----
MAX_IR_AGENTS = 24
MAX_IR_EDGES = 64
MAX_IR_TEAMS = 8
_MAX_ATOMS_PER_AGENT = 8
_MAX_SKILLS_PER_AGENT = 8
_MAX_SCHEDULES = 16
_MAX_RULES = 16          # forbid/oblige 各自条数
_MAX_STR = 64
_MAX_REVIEW_STEPS = 4    # 汇报链降级出的评审步上限(防边灌爆步骤表)

_ATOM_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
_ROLE_ID_SAFE_RE = re.compile(r"[^\w\-]", re.UNICODE)   # 非法字符 → "_"(对齐 RoleRegistry._ROLE_ID_RE)
_VALID_KINDS = ("task", "daemon")
_VALID_AGENT_KINDS = ("decision", "executor", "hybrid", "skill")
_VALID_EDGE_TYPES = ("sequence", "parallel", "condition", "handoff",
                     "report", "broadcast", "loop", "dynamic")
_VALID_ON_FAIL = ("skip", "retry", "abort")

SYSTEM_TRIAGE = """你是 KarvyLoop 的多 agent 系统读谱器。用户从外部框架(如 CrewAI/AutoGen/LangGraph 的导出)
拿来一个**多 agent 系统**:一组 agent + 它们之间的协作拓扑。你要把它读成一张**中间表示(IR)**,
后续由确定性代码翻译落地 —— 你只负责"读懂",不负责"落地"。

输入会给你:系统名(可能空)、每个 agent 的名字+人设原文+tools、topology 原文(源格式,可能是图/列表/文字)、
已存在的公共原子库(供复用判断)。

只输出**一个 JSON 对象**(不要解释、不要 markdown 围栏),schema:
{
  "system": {"name": "系统名(读不出就给个贴切的短名)", "mission": "这个系统整体是干什么的(1-3句)",
             "forbid": ["系统级禁令(若原文有)"], "oblige": ["系统级强制项(若原文有)"]},
  "pattern": "主导协作形态:pipeline / parallel / conditional / groupchat / supervisor / nested / mixed",
  "agents": [
    {"name": "**必须与输入 agent 名一字不差**",
     "kind": "decision / executor / hybrid / skill(判型,唯一判据见下)",
     "identity": "一句话人设:这个角色是谁、最擅长什么(从人设原文提炼,中文)",
     "soul": "2-4 条工作风格/原则,用 \\n 分隔",
     "title": "职务短名(如 研究员/评审员,≤10字)",
     "task": "它在流程里承担的那一步是什么(一句话;没有明确流程角色就留空)",
     "team": "所属 team 的 id(没有就空串)",
     "atoms": [{"id": "snake_case", "kind": "task 或 daemon", "purpose": "一句话",
                "tools": ["只能从 run_command/read_file/write_file/edit_file/web_search/web_fetch 里选"],
                "tags": ["2-5个英文小写语义标签"]}],
     "skills": ["识别出的内含技能名(SOP/流程剧本)"]}
  ],
  "teams": [{"id": "短id", "name": "团队名", "mission": "这个团队干什么(一句话)",
             "parent": "父 team 的 id(顶层就空串)", "members": ["agent 名"]}],
  "edges": [{"from": "agent 名", "to": "agent 名",
             "type": "sequence / parallel / condition / handoff / report / broadcast / loop / dynamic",
             "condition": "type=condition 时:触发词/条件描述(简短)", "on_fail": "skip / retry / abort(读得出才填)"}],
  "schedules": [{"agent": "agent 名", "when": "自然语言时间(如 每天早8点)"}],
  "blackboard": "若系统有共享黑板/共享可写状态,描述一句;没有就空串"
}

判型(kind,唯一判据=这个 agent **担不担用户的责**):
- "decision":有身份/立场,替人做取舍与判断(顾问/把关/评审)。atoms 可为 0。
- "executor":只有能力步骤,谁用都一样、无立场(转换器/爬虫/查询器)。
- "hybrid":既有真人设立场、又有具体可执行能力。**拿不准就填 hybrid**。
- "skill":本质是一段流程剧本/SOP(教"怎么做"而不是"谁")。

edge type 怎么选:顺序交接=sequence;可同时跑=parallel;"若X则走这条"且条件是**静态可判**的=condition;
主管把活分派给下属=handoff;**下属做完向某个 agent 汇报/受其评审=report**;群聊/辩论/广播=broadcast;
回环重做=loop;**运行时才知道走哪条**(由某个 agent 现场路由)=dynamic。

硬约束:
- agents[].name / teams[].members / edges 的 from,to **只能用输入里给的 agent 名**,一字不差,绝不编造。
- 原子的 tools 只能从那 6 个真实原语里选;外部系统集成(发邮件/连SaaS)→ 进 skills,不编工具名。
- 读不出的就留空/空数组,**不要编**。严格 JSON,无围栏、无注释、无尾随文本。"""


# ---- IR 数据类(只活在 plan 阶段;frozen,不持久化)----

@dataclasses.dataclass(frozen=True)
class IRAgent:
    name: str
    kind: str                    # decision / executor / hybrid / skill
    identity: str
    soul: str
    title: str
    task: str
    team: str
    atoms: tuple[AtomProposal, ...]
    skills: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class IRTeam:
    id: str
    name: str
    mission: str
    parent: str                  # 父 team id;顶层 = ""
    members: tuple[str, ...]     # agent 名


@dataclasses.dataclass(frozen=True)
class IREdge:
    src: str
    dst: str
    type: str                    # _VALID_EDGE_TYPES
    condition: str = ""
    on_fail: str = ""            # "" = 未读出


@dataclasses.dataclass(frozen=True)
class SystemIR:
    system_name: str
    mission: str
    forbid: tuple[str, ...]
    oblige: tuple[str, ...]
    pattern: str
    agents: tuple[IRAgent, ...]
    teams: tuple[IRTeam, ...]
    edges: tuple[IREdge, ...]
    schedules: tuple[dict, ...]  # {"agent","when"}
    blackboard: str = ""


def _parse_atom_list(raw: Any) -> tuple[AtomProposal, ...]:
    """单 agent 的原子提案:同 bootstrap.parse_decomposition 的纪律(id 尺/封顶/去重)。"""
    out: list[AtomProposal] = []
    seen: set[str] = set()
    for a in (raw or []):
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id", "")).strip()
        if not aid or len(aid) > _MAX_STR or not _ATOM_ID_RE.match(aid) or aid in seen:
            continue
        kind = str(a.get("kind", "task")).strip() or "task"
        if kind not in _VALID_KINDS:
            kind = "task"
        tools = tuple(t for t in (str(x).strip() for x in (a.get("tools") or []))
                      if t and len(t) <= _MAX_STR)[:8]
        seen_tag: set[str] = set()
        tags: list[str] = []
        for x in (a.get("tags") or []):
            tg = str(x).strip().lower()[:32]
            if tg and tg not in seen_tag:
                seen_tag.add(tg)
                tags.append(tg)
        out.append(AtomProposal(id=aid, kind=kind, purpose=str(a.get("purpose", "")).strip()[:400],
                                tools=tools, tags=tuple(tags[:8]), reuse_existing=False))
        seen.add(aid)
        if len(out) >= _MAX_ATOMS_PER_AGENT:
            break
    return tuple(out)


def _str_list(raw: Any, *, n: int, each: int = _MAX_STR) -> tuple[str, ...]:
    return tuple(s for s in (str(x).strip() for x in (raw or []) if isinstance(x, (str, int, float)))
                 if s and len(s) <= each)[:n]


def parse_system_ir(text: str, valid_agent_names: set[str]) -> Optional[SystemIR]:
    """宁空勿毒:严格 JSON 解 SYSTEM_TRIAGE 输出 → SystemIR;解不出 / 没有一个真 agent → None。

    引用级把关(同 task_insight 的 evidence_ref 纪律):agents/teams/edges/schedules 里的
    agent 名**必须在 bundle 里真实存在**,编造的整项丢;edges/teams 的悬空引用丢。
    """
    raw = _strip_outer_fence(text or "")
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None

    # agents:名字必须来自 bundle(悬空/编造丢);kind 白名单外归 hybrid;封顶
    agents: list[IRAgent] = []
    seen: set[str] = set()
    for a in (obj.get("agents") or []):
        if not isinstance(a, dict):
            continue
        name = str(a.get("name", "")).strip()
        if not name or name not in valid_agent_names or name in seen:
            continue
        kind = str(a.get("kind", "")).strip().lower()
        if kind not in _VALID_AGENT_KINDS:
            kind = "hybrid"
        agents.append(IRAgent(
            name=name, kind=kind,
            identity=str(a.get("identity", "")).strip()[:600],
            soul=str(a.get("soul", "")).strip()[:1200],
            title=str(a.get("title", "")).strip()[:_MAX_STR],
            task=str(a.get("task", "")).strip()[:200],
            team=str(a.get("team", "")).strip()[:_MAX_STR],
            atoms=_parse_atom_list(a.get("atoms")),
            skills=_str_list(a.get("skills"), n=_MAX_SKILLS_PER_AGENT),
        ))
        seen.add(name)
        if len(agents) >= MAX_IR_AGENTS:
            break
    if not agents:
        return None
    agent_names = {a.name for a in agents}

    # teams:成员过滤到真 agent;空成员团队丢;parent 悬空清空;封顶
    teams: list[IRTeam] = []
    seen_t: set[str] = set()
    for t in (obj.get("teams") or []):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "")).strip()[:_MAX_STR]
        if not tid or tid in seen_t:
            continue
        members = tuple(m for m in _str_list(t.get("members"), n=MAX_IR_AGENTS)
                        if m in agent_names)
        if not members:
            continue
        teams.append(IRTeam(id=tid, name=str(t.get("name", "")).strip()[:_MAX_STR] or tid,
                            mission=str(t.get("mission", "")).strip()[:400],
                            parent=str(t.get("parent", "")).strip()[:_MAX_STR],
                            members=members))
        seen_t.add(tid)
        if len(teams) >= MAX_IR_TEAMS:
            break
    team_ids = {t.id for t in teams}
    # parent 悬空 / 自指 → 清空(顶层);成环 → 断环(把环里第一个的 parent 清空)
    teams = [dataclasses.replace(t, parent="" if (t.parent not in team_ids or t.parent == t.id)
                                 else t.parent) for t in teams]
    by_id = {t.id: t for t in teams}
    for t in list(teams):
        walked, cur = set(), t.id
        while cur:
            if cur in walked:                      # 成环:断在 t 上
                idx = teams.index(by_id[t.id])
                teams[idx] = dataclasses.replace(teams[idx], parent="")
                by_id[t.id] = teams[idx]
                break
            walked.add(cur)
            cur = by_id[cur].parent if cur in by_id else ""

    # edges:两端必须是真 agent,自指丢,type 白名单外归 sequence;去重;封顶
    edges: list[IREdge] = []
    seen_e: set[tuple] = set()
    for e in (obj.get("edges") or []):
        if not isinstance(e, dict):
            continue
        src = str(e.get("from", "")).strip()
        dst = str(e.get("to", "")).strip()
        if src not in agent_names or dst not in agent_names or src == dst:
            continue
        etype = str(e.get("type", "")).strip().lower()
        if etype not in _VALID_EDGE_TYPES:
            etype = "sequence"
        key = (src, dst, etype)
        if key in seen_e:
            continue
        seen_e.add(key)
        on_fail = str(e.get("on_fail", "")).strip().lower()
        edges.append(IREdge(src=src, dst=dst, type=etype,
                            condition=str(e.get("condition", "")).strip()[:200],
                            on_fail=on_fail if on_fail in _VALID_ON_FAIL else ""))
        if len(edges) >= MAX_IR_EDGES:
            break

    schedules = []
    for s in (obj.get("schedules") or []):
        if isinstance(s, dict) and str(s.get("agent", "")).strip() in agent_names:
            schedules.append({"agent": str(s["agent"]).strip(),
                              "when": str(s.get("when", "")).strip()[:120]})
        if len(schedules) >= _MAX_SCHEDULES:
            break

    sysobj = obj.get("system") if isinstance(obj.get("system"), dict) else {}
    pattern = str(obj.get("pattern", "")).strip().lower()
    if pattern not in ("pipeline", "parallel", "conditional", "groupchat",
                       "supervisor", "nested", "mixed"):
        pattern = "unknown"
    return SystemIR(
        system_name=str(sysobj.get("name", "")).strip()[:_MAX_STR],
        mission=str(sysobj.get("mission", "")).strip()[:1200],
        forbid=_str_list(sysobj.get("forbid"), n=_MAX_RULES, each=200),
        oblige=_str_list(sysobj.get("oblige"), n=_MAX_RULES, each=200),
        pattern=pattern,
        agents=tuple(agents), teams=tuple(teams), edges=tuple(edges),
        schedules=tuple(schedules),
        blackboard=str(obj.get("blackboard", "")).strip()[:400],
    )


def _format_bundle(bundle: SystemBundle, existing_atom_ids: list[str]) -> str:
    """喂给 TRIAGE 的料:系统名 + 每个 agent(名/人设/tools)+ topology 原样 + 已有原子库。"""
    parts = [f"系统名:{bundle.name or '(未命名)'}", f"agent 共 {len(bundle.agents)} 个:"]
    for m in bundle.agents:
        tool_names = []
        for t in m.tools:
            tool_names.append(str(t.get("name", "") or t.get("type", "")).strip()
                              if isinstance(t, dict) else str(t).strip())
        tool_names = [n for n in tool_names if n]
        parts.append(f"--- agent 名:{m.agent_name}\n人设原文:\n{m.system_prompt[:2000]}\n"
                     f"tools:{', '.join(tool_names) or '(无)'}")
    try:
        topo_txt = json.dumps(bundle.topology, ensure_ascii=False) if bundle.topology is not None else "(无)"
    except (TypeError, ValueError):
        topo_txt = str(bundle.topology)
    parts.append(f"topology(源格式原样):\n{topo_txt[:6000]}")
    parts.append(f"已存在的公共原子库(可复用):{', '.join(existing_atom_ids) or '(空)'}")
    return "\n\n".join(parts)


async def system_triage(bundle: SystemBundle, *, existing_atom_ids: list[str],
                        gateway: Any, model_ref: str = "") -> Optional[SystemIR]:
    """跑一次受限 LLM 读谱(无工具)→ SystemIR。gateway.complete 自动入 token 账本。

    重试一次再放弃(同 bootstrap_decompose:并发/网络偶发截断 JSON,多半重发就好);
    仍 None → 调用方降级"逐个当单 agent 导 + 拓扑丢失如实报"。gateway=None → None。
    """
    if gateway is None:
        return None
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    material = _format_bundle(bundle, existing_atom_ids)
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)
    valid_names = {m.agent_name for m in bundle.agents}
    for _attempt in range(2):
        out = ""
        async for ev in gateway.complete(
            [{"role": "user", "content": material}], [], ref,
            system=SystemPrompt(static=[SYSTEM_TRIAGE]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
        ir = parse_system_ir(out, valid_names)
        if ir is not None:
            return ir
    return None


# ---- ② 确定性翻译器(纯函数:IR → ImportPlan + degradations)----

def _role_id_for(name: str, taken: set[str]) -> str:
    rid = _ROLE_ID_SAFE_RE.sub("_", (name or "").strip()) or "imported_role"
    base, n = rid, 2
    while rid in taken:
        rid = f"{base}_{n}"
        n += 1
    taken.add(rid)
    return rid


def _degrade(element: str, why_key: str, fallback_key: str, **vars) -> dict:
    return {"element": element, "why": i18n.t(why_key, **vars),
            "fallback": i18n.t(fallback_key, **vars)}


def translate_to_plan(ir: SystemIR, *, bundle_name: str = "") -> tuple[dict, list[dict]]:
    """纯函数:SystemIR → (ImportPlan dict, degradations)。零 IO、零 LLM、零写盘。

    plan 形状(前端拍板面 + /apply 的输入契约,人可改判 kind / 可编模板步骤):
      {mode:"system", domain:{name,value_md,deontic{forbid,oblige}},
       subdomains:[{team_id,name,mission,parent_team_id,members[role_id]}](父先子后),
       roles:[{role_id,name,agent_kind,identity,soul,title,team,
               atoms:[{id,kind,purpose,tools,tags}],skills[]}],
       workflows:[{name,goal,steps:[{id,role_key,task,depends_on,inputs?,when?,on_fail?,max_retries?}]}],
       seed_intents:[{topic,participants[role_id]}],
       relocations:[{element,moved_to}], skills_recognized[], notes[]}
    """
    degradations: list[dict] = []
    notes: list[str] = []
    relocations: list[dict] = []

    taken: set[str] = set()
    role_id_of: dict[str, str] = {}
    roles: list[dict] = []
    skills_recognized: list[str] = []
    for a in ir.agents:
        rid = _role_id_for(a.name, taken)
        role_id_of[a.name] = rid
        roles.append({
            "role_id": rid, "name": a.name, "agent_kind": a.kind,
            "identity": a.identity, "soul": a.soul, "title": a.title, "team": a.team,
            "atoms": [{"id": p.id, "kind": p.kind, "purpose": p.purpose,
                       "tools": list(p.tools), "tags": list(p.tags)} for p in a.atoms],
            "skills": list(a.skills),
        })
        if a.kind == "skill":
            notes.append(i18n.t("system_import.note.skill_agent", name=a.name))
            skills_recognized.extend(s for s in a.skills if s not in skills_recognized)

    # 汇报链(report 边):绝不造 role→role 问责链 —— 职务写进 IDENTITY + 评审步 + 问责重接到你
    report_edges = [e for e in ir.edges if e.type == "report"]
    reviewers: dict[str, list[str]] = {}
    for e in report_edges:
        reviewers.setdefault(e.dst, []).append(e.src)
        degradations.append(_degrade(f"report_chain:{e.src}→{e.dst}",
                                     "system_import.degrade.report_chain.why",
                                     "system_import.degrade.report_chain.fallback",
                                     src=e.src, dst=e.dst))
    for r in roles:
        reps = reviewers.get(r["name"])
        if reps and r["agent_kind"] in ("decision", "hybrid"):
            r["identity"] = (r["identity"] + ("\n" if r["identity"] else "") +
                             i18n.t("system_import.identity.report_note",
                                    reporters="、".join(reps))).strip()

    # supervisor 静态分派:路由权上移(移位,不降级 —— 报告给人)
    if ir.pattern == "supervisor" or any(e.type == "handoff" for e in ir.edges):
        relocations.append({"element": "supervisor_dispatch",
                            "moved_to": i18n.t("system_import.relocate.supervisor")})

    # 动态路由 / 循环 / 黑板 / 定时:诚实降级
    workflow_edges: list[IREdge] = []
    for e in ir.edges:
        if e.type == "loop":
            degradations.append(_degrade(f"loop:{e.src}→{e.dst}",
                                         "system_import.degrade.loop.why",
                                         "system_import.degrade.loop.fallback"))
            continue                                   # 丢边(workflow 无循环 = 诚实 P1)
        if e.type == "dynamic":
            degradations.append(_degrade(f"dynamic_route:{e.src}→{e.dst}",
                                         "system_import.degrade.dynamic_route.why",
                                         "system_import.degrade.dynamic_route.fallback"))
            workflow_edges.append(dataclasses.replace(e, type="sequence", condition=""))
            continue                                   # 静态化成顺序依赖(降级已报)
        if e.type in ("sequence", "parallel", "condition", "handoff"):
            workflow_edges.append(e)
    if ir.blackboard:
        degradations.append(_degrade("blackboard",
                                     "system_import.degrade.blackboard.why",
                                     "system_import.degrade.blackboard.fallback"))
    for s in ir.schedules:
        degradations.append(_degrade(f"schedule:{s['agent']}",
                                     "system_import.degrade.schedule.why",
                                     "system_import.degrade.schedule.fallback",
                                     agent=s["agent"], when=s.get("when", "")))

    # 纯执行体/技能体不是步骤执行者(不建 role)→ 折边桥过它们,如实注记
    steppable = {a.name for a in ir.agents if a.kind in ("decision", "hybrid")}
    folded = sorted({e.src for e in workflow_edges if e.src not in steppable} |
                    {e.dst for e in workflow_edges if e.dst not in steppable})
    for name in folded:
        notes.append(i18n.t("system_import.note.executor_folded", name=name))
    remaining = list(folded)
    changed = True
    while changed and remaining:
        changed = False
        for mid in list(remaining):
            ins = [e for e in workflow_edges if e.dst == mid]
            outs = [e for e in workflow_edges if e.src == mid]
            bridged = [e for e in workflow_edges if mid not in (e.src, e.dst)]
            for ei in ins:
                for eo in outs:
                    if ei.src != eo.dst:      # 桥过去(端点可仍是待折节点,下一轮继续折)
                        bridged.append(IREdge(src=ei.src, dst=eo.dst, type=eo.type,
                                              condition=eo.condition, on_fail=eo.on_fail))
            if ins or outs:
                workflow_edges = bridged
                remaining.remove(mid)
                changed = True
    workflow_edges = [e for e in workflow_edges if e.src in steppable and e.dst in steppable]
    # 桥接可能引入重复边:去重保序
    _seen_we: set[tuple] = set()
    workflow_edges = [e for e in workflow_edges
                      if (k := (e.src, e.dst, e.type, e.condition, e.on_fail)) not in _seen_we
                      and not _seen_we.add(k)]

    # workflow 模板:参与边的角色各一步(拓扑序),条件→when / 失败策略→on_fail / 汇聚→inputs
    domain_name = (ir.system_name or bundle_name or "imported-system").strip()
    workflows: list[dict] = []
    participants = [a.name for a in ir.agents
                    if a.name in steppable and any(a.name in (e.src, e.dst) for e in workflow_edges)]
    if participants and workflow_edges:
        order: list[str] = []
        pend = {n: {e.src for e in workflow_edges if e.dst == n} for n in participants}
        while pend:
            ready = sorted([n for n, deps in pend.items() if not (deps & set(pend))],
                           key=participants.index)
            if not ready:                              # 残余环(loop 已剥,防御):按原序放行
                ready = sorted(pend, key=participants.index)
            for n in ready:
                order.append(n)
                pend.pop(n)
        step_id_of = {n: f"s{i + 1}" for i, n in enumerate(order)}
        agent_of = {a.name: a for a in ir.agents}
        steps = []
        for n in order:
            a = agent_of[n]
            deps = sorted({step_id_of[e.src] for e in workflow_edges if e.dst == n},
                          key=lambda s: int(s[1:]))
            step: dict[str, Any] = {
                "id": step_id_of[n], "role_key": role_id_of[n],
                "task": a.task or a.identity[:200] or i18n.t("system_import.task.step_fallback"),
                "depends_on": deps,
            }
            if len(deps) > 1:                          # 并行汇聚:这步真吃全部上游产出
                step["inputs"] = list(deps)
            cond_edges = [e for e in workflow_edges if e.dst == n and e.type == "condition"]
            if cond_edges:                             # 单条件路由(多条件只取第一条,IR 已封顶)
                ce = cond_edges[0]
                step["when"] = ({"step": step_id_of[ce.src], "contains": ce.condition[:64]}
                                if ce.condition else {"step": step_id_of[ce.src], "status": "done"})
            fails = [e.on_fail for e in workflow_edges if e.dst == n and e.on_fail]
            if fails:
                step["on_fail"] = fails[0]
                if fails[0] == "retry":
                    step["max_retries"] = 2
            steps.append(step)
        # 评审步(汇报链降级的建设性一半):评审者对被评审者产出加一步,封顶防灌爆
        n_review = 0
        for e in report_edges:
            if n_review >= _MAX_REVIEW_STEPS:
                break
            if e.src in step_id_of and e.dst in steppable and e.dst in role_id_of:
                steps.append({"id": f"s{len(steps) + 1}", "role_key": role_id_of[e.dst],
                              "task": i18n.t("system_import.task.review", target=e.src),
                              "depends_on": [step_id_of[e.src]]})
                n_review += 1
        if len(steps) >= 2:
            workflows.append({"name": i18n.t("system_import.workflow.name", domain=domain_name)[:40],
                              "goal": ir.mission or domain_name, "steps": steps})

    # 群聊辩论 → 圆桌种子(seed_intents;apply 时出 H2A 提案卡,人拍了才开桌)
    seed_intents: list[dict] = []
    if ir.pattern == "groupchat":
        parts_ = [role_id_of[a.name] for a in ir.agents if a.kind in ("decision", "hybrid")]
        if len(parts_) >= 2:
            seed_intents.append({"topic": ir.mission or i18n.t("system_import.seed.topic_fallback"),
                                 "participants": parts_})
    bcast = sorted({n for e in ir.edges if e.type == "broadcast" for n in (e.src, e.dst)
                    if n in steppable})
    if not seed_intents and len(bcast) >= 2:
        seed_intents.append({"topic": ir.mission or i18n.t("system_import.seed.topic_fallback"),
                             "participants": [role_id_of[n] for n in bcast]})

    # 嵌套团队 → 子域(父先子后,apply 顺序可直落;成员只挂会落成角色的)
    team_by_id = {t.id: t for t in ir.teams}

    def _depth(t: IRTeam) -> int:
        d, cur = 0, t.parent
        while cur and cur in team_by_id and d < MAX_IR_TEAMS:
            d += 1
            cur = team_by_id[cur].parent
        return d

    subdomains = [{"team_id": t.id, "name": t.name, "mission": t.mission,
                   "parent_team_id": t.parent,
                   "members": [role_id_of[m] for m in t.members if m in steppable]}
                  for t in sorted(ir.teams, key=_depth)]

    plan = {
        "mode": "system",
        "domain": {"name": domain_name, "value_md": ir.mission,
                   "deontic": {"forbid": list(ir.forbid), "oblige": list(ir.oblige)}},
        "subdomains": subdomains,
        "roles": roles,
        "workflows": workflows,
        "seed_intents": seed_intents,
        "relocations": relocations,
        "skills_recognized": skills_recognized,
        "notes": notes,
        # 降级清单也嵌进 plan:前端整个 plan 回传 apply 时,报告里能原样带上(单一真源)
        "degradations": degradations,
    }
    return plan, degradations


# ---- ③ 确定性落地(零 LLM;顺序 = 原子→角色→域+子域→模板→圆桌种子;失败回滚)----

class SystemApplyError(ValueError):
    """apply 前置校验/中途失败(已回滚)。endpoint 层转 422。"""


def apply_system_plan(plan: dict, *, atom_registry, role_registry, domain_registry,
                      domain_store=None, workflow_store=None, proposal_registry=None,
                      created_by_user: str = "ch", now: Optional[float] = None) -> dict:
    """把人审过的 ImportPlan 确定性落地。零 LLM;失败回滚不留孤儿(照 routes_roles 既有形状:
    删本次新建原子/角色、归档本次新建域;复用的既有资产不动)。

    落地顺序(docs/84):原子 → 角色(RoleRegistry.create 自动 seed 尽责契约)→ 域+子域
    → WorkflowStore 模板(provenance:import)→ 圆桌种子(H2A 提案,人拍了才开桌)。
    """
    import time as _time
    now = now if now is not None else _time.time()
    if atom_registry is None or role_registry is None or domain_registry is None:
        raise SystemApplyError(i18n.t("system_import.apply.missing_registry"))
    dom = plan.get("domain") or {}
    domain_name = str(dom.get("name", "")).strip()
    if not domain_name:
        raise SystemApplyError(i18n.t("system_import.apply.no_domain_name"))
    # 同名活跃域拒(零写盘前置检查,同 instantiate_template 口径)
    for d in domain_registry.list_active():
        if (getattr(d, "name", "") or "").strip().lower() == domain_name.lower():
            raise SystemApplyError(i18n.t("system_import.apply.same_name", name=domain_name))
    role_rows = [r for r in (plan.get("roles") or []) if isinstance(r, dict)]
    # 前置校验(全部过了才动盘,回滚只是纵深):role_id 字符集 / kind 白名单
    for r in role_rows:
        rid = str(r.get("role_id", "")).strip()
        if not rid or not re.match(r"^[\w\-]+$", rid, re.UNICODE):
            raise SystemApplyError(i18n.t("system_import.apply.bad_role_id", role_id=rid or "(空)"))
        if str(r.get("agent_kind", "")) not in _VALID_AGENT_KINDS:
            raise SystemApplyError(i18n.t("system_import.apply.bad_kind", role_id=rid))

    atoms_created: list[str] = []
    atoms_reused: list[str] = []
    roles_created: list[str] = []
    created_domains: list = []

    def _rollback() -> None:
        for rid in roles_created:
            try:
                role_registry.remove(rid)
            except Exception:  # noqa: BLE001
                pass
        for aid in atoms_created:
            try:
                atom_registry.remove(aid)
            except Exception:  # noqa: BLE001
                pass
        for d in created_domains:
            try:
                domain_registry.archive(d.id)
            except Exception:  # noqa: BLE001
                pass

    try:
        # ① 原子(decision/hybrid/executor 的都落;skill 型 agent 零写盘)
        for r in role_rows:
            if r["agent_kind"] == "skill":
                continue
            for ap in (r.get("atoms") or []):
                aid = str(ap.get("id", "")).strip()
                if not aid or not _ATOM_ID_RE.match(aid) or len(aid) > _MAX_STR:
                    continue
                if atom_registry.get(aid) is not None:
                    if aid not in atoms_reused:
                        atoms_reused.append(aid)
                    continue
                atom_registry.create(aid, str(ap.get("kind", "task")) or "task",
                                     str(ap.get("purpose", ""))[:400],
                                     tools=[str(t) for t in (ap.get("tools") or [])][:8],
                                     tags=[str(t) for t in (ap.get("tags") or [])][:8],
                                     origin=f"system-import:{domain_name}")
                atoms_created.append(aid)

        # ② 角色(decision/hybrid;executor 只落原子不建 role,skill 指路技能库)
        roles_reused: list[str] = []
        skipped_executor: list[str] = []
        skipped_skill: list[str] = []
        known = role_registry._known_skills() if hasattr(role_registry, "_known_skills") else None
        landed_role_ids: list[str] = []
        for r in role_rows:
            rid = str(r["role_id"]).strip()
            kind = r["agent_kind"]
            if kind == "executor":
                skipped_executor.append(rid)
                continue
            if kind == "skill":
                skipped_skill.append(rid)
                continue
            if role_registry.get(rid) is not None:
                roles_reused.append(rid)
                landed_role_ids.append(rid)
                continue
            atom_ids = [str(ap.get("id", "")).strip() for ap in (r.get("atoms") or [])]
            atom_ids = [a for a in atom_ids if a and atom_registry.get(a) is not None]
            bind_skills = [s for s in (r.get("skills") or [])
                           if known is not None and s in known]
            role_registry.create(rid, identity=str(r.get("identity", "")),
                                 soul=str(r.get("soul", "")),
                                 atom_ids=atom_ids, skill_ids=bind_skills,
                                 title=str(r.get("title", ""))[:64])
            roles_created.append(rid)
            landed_role_ids.append(rid)

        # ③ 域 + 子域(使命→value.md / 禁令→deontic / 成员=member_query;子域 create_child 继承)
        from karvyloop.domain.deontic import Deontic
        raw_value = str(dom.get("value_md", "")).strip()
        value_md = raw_value if (not raw_value or raw_value.startswith("# 价值观")) \
            else f"# 价值观\n\n{raw_value}"
        deo = dom.get("deontic") or {}
        member_query = " AND ".join([f"user:{created_by_user}"] +
                                    [f"agent:{rid}" for rid in landed_role_ids])
        root = domain_registry.create(
            name=domain_name, created_by=f"user:{created_by_user}",
            value_md_raw=value_md,
            deontic=Deontic(forbid=tuple(str(x) for x in (deo.get("forbid") or ())),
                            oblige=tuple(str(x) for x in (deo.get("oblige") or ()))),
            member_query=member_query)
        created_domains.append(root)
        team_domain: dict[str, Any] = {}
        sub_report: list[dict] = []
        for sd in (plan.get("subdomains") or []):
            if not isinstance(sd, dict) or not str(sd.get("name", "")).strip():
                continue
            members = [m for m in (sd.get("members") or []) if m in landed_role_ids]
            parent = team_domain.get(str(sd.get("parent_team_id", "")), root)
            child = domain_registry.create_child(
                parent_id=parent.id, name=str(sd["name"]).strip()[:64],
                created_by=f"user:{created_by_user}", deontic_override=Deontic(),
                member_query=" AND ".join([f"user:{created_by_user}"] +
                                          [f"agent:{m}" for m in members]))
            created_domains.append(child)
            team_domain[str(sd.get("team_id", ""))] = child
            sub_report.append({"id": child.id, "name": child.name, "parent_id": parent.id})

        # ④ workflow 模板(provenance:import;role_key 没落地的步骤丢 + 悬空引用清理,如实报)
        workflows_saved: list[dict] = []
        steps_dropped: list[str] = []
        if workflow_store is not None:
            for wf in (plan.get("workflows") or []):
                if not isinstance(wf, dict):
                    continue
                steps = []
                for s in (wf.get("steps") or []):
                    if not isinstance(s, dict) or not s.get("id"):
                        continue
                    if str(s.get("role_key", "")) not in landed_role_ids:
                        steps_dropped.append(str(s.get("id")))
                        continue
                    steps.append(dict(s))
                valid_ids = {s["id"] for s in steps}
                for s in steps:                       # 悬空 depends/inputs/when 清理(不静默剪枝语义:when 丢=恒跑)
                    s["depends_on"] = [d for d in (s.get("depends_on") or []) if d in valid_ids]
                    if isinstance(s.get("inputs"), list):
                        s["inputs"] = [d for d in s["inputs"] if d in valid_ids] or None
                        if s["inputs"] is None:
                            s.pop("inputs")
                    w = s.get("when")
                    if isinstance(w, dict) and w.get("step") not in valid_ids:
                        s.pop("when", None)
                if len(steps) >= 2:
                    tpl = workflow_store.save(
                        goal=str(wf.get("goal", ""))[:400],
                        role_keys=[s["role_key"] for s in steps],
                        steps=steps, name=str(wf.get("name", ""))[:40],
                        provenance="import")
                    workflows_saved.append({"id": tpl["id"], "name": tpl["name"],
                                            "steps": len(steps)})
                elif wf.get("steps"):
                    steps_dropped.extend(str(s.get("id", "")) for s in steps)

        # ⑤ 圆桌种子:H2A 提案卡(人拍了才真开桌;无 proposal_registry → 只报不落,fail-soft)
        roundtables_seeded = 0
        display_of = {r["role_id"]: (r.get("name") or r["role_id"]) for r in role_rows}
        for seed in (plan.get("seed_intents") or []):
            if not isinstance(seed, dict):
                continue
            parts_ = [p for p in (seed.get("participants") or []) if p in landed_role_ids]
            if len(parts_) < 2 or proposal_registry is None:
                continue
            try:
                from karvyloop.karvy.proposal_registry import proposal_for_roundtable
                proposal_registry.register(proposal_for_roundtable(
                    group_domain_id=root.id, group_name=domain_name,
                    participants=parts_,
                    participant_names=[display_of.get(p, p) for p in parts_],
                    topic=str(seed.get("topic", ""))[:400], ts=now))
                roundtables_seeded += 1
            except Exception:  # noqa: BLE001 — 种子卡失败不翻整个 apply(资产已落)
                pass
    except SystemApplyError:
        _rollback()
        raise
    except Exception as e:  # noqa: BLE001 — 中途任何失败:回滚,不留孤儿
        _rollback()
        raise SystemApplyError(i18n.t("system_import.apply.failed", error=str(e)[:300])) from e

    # 持久化(fail-soft:域已在内存,存盘失败只警告 —— 同 routes_domain 口径)
    if domain_store is not None:
        try:
            domain_store.save_all(domain_registry.list_all())
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "domain_id": root.id, "domain_name": domain_name,
        "subdomains": sub_report,
        "roles_created": roles_created, "roles_reused": roles_reused,
        "roles_skipped_executor": skipped_executor, "roles_skipped_skill": skipped_skill,
        "atoms_created": atoms_created, "atoms_reused": atoms_reused,
        "workflows_saved": workflows_saved, "steps_dropped": steps_dropped,
        "roundtables_seeded": roundtables_seeded,
        "skills_recognized": list(plan.get("skills_recognized") or []),
        "degradations": list(plan.get("degradations") or []),
        "notes": list(plan.get("notes") or []),
    }


__all__ = [
    "SYSTEM_TRIAGE", "SystemIR", "IRAgent", "IRTeam", "IREdge",
    "MAX_IR_AGENTS", "MAX_IR_EDGES", "MAX_IR_TEAMS",
    "parse_system_ir", "system_triage", "translate_to_plan",
    "apply_system_plan", "SystemApplyError",
]
