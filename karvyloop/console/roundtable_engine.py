"""console/roundtable_engine.py — 圆桌引擎(P2-e:拆 routes.py,领域引擎下沉,行为零变化)。

从 routes.py 纯搬移:编排意图解析(圆桌 vs 单点委派)/ 待办圆桌态(持久化)/ 名册与成员寻址 /
主持人 LLM 调用(对齐开场・对齐轮・目标收敛・控场收敛)/ 阶段1 讨论执行核心;
/api/roundtable/* HTTP 端点仍留在 routes.py。
"""
from __future__ import annotations

import logging
from typing import Any

from karvyloop.llm.token_ledger import token_source as _token_src

from .workflow_engine import _push_step

logger = logging.getLogger(__name__)


# 圆桌/多人协作信号:出现这些词 = 想让"几个人坐一起讨论"(圆桌),不是把活交给一个人(委派)。
_ROUNDTABLE_KW = (
    "圆桌", "round table", "roundtable", "开个会", "开会", "一起讨论", "一起分析",
    "一起聊", "大家讨论", "都来", "几个人", "多人", "讨论一下", "讨论下",
    "discuss together", "brainstorm", "panel",
)

# "全员/所有角色"——全局小卡该把**所有活跃域成员**都唤醒上桌(不靠 LLM 从 roster 里挑、它会塌成 1 个;
# 实测"让所有角色开圆桌"→ 模糊派发只唤醒 1 个,这是真规模缺口)。封顶 64(对齐大桌全员上限,防失控)。
_ALL_HANDS_KW = ("全员", "所有角色", "所有人", "全部角色", "全部人", "全体", "每个角色", "all roles", "everyone")
_ALL_HANDS_CAP = 64


def _resolve_roundtable_from_intent(app, intent: str):
    """私聊小卡 + 编排意图 → 解析出"在哪个群、拉哪些角色、议什么"。返回 dict 或 None。

    圆桌 vs 单点委派的区分:出现圆桌关键词 **或** 一句话里点到 ≥2 个角色 → 圆桌。
    群定位:句子里点到 "Karvy World/大群" 或匹配角色横跨 >1 个域 → l0 大群(跨域桌);
    否则匹配角色都在同一个域 → 那个域的群。议题 = 原 intent。
    """
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN

    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return None
    low = (intent or "").lower()
    has_kw = any(k in intent or k in low for k in _ROUNDTABLE_KW)
    all_hands = any(k in intent or k in low for k in _ALL_HANDS_KW)  # "全员/所有角色" → 唤醒所有成员
    # 跨域扫所有 active 域的成员:全员意图 → **全收**;否则只收名字出现在 intent 里的角色(去重)。
    matched: list[dict] = []
    seen = set()
    try:
        for domain in reg.list_all():
            if getattr(domain, "lifecycle", "active") != "active":
                continue
            for m in reg.resolve_members(domain.id):
                if m.role in ("user", "observer"):
                    continue
                name = m.agent_id if (m.role == "agent" and m.agent_id) else m.role
                hit = all_hands or (m.role and m.role in intent) or (m.agent_id and m.agent_id in intent)
                if not hit:
                    continue
                key = (domain.id, m.agent_id or m.role)
                if key in seen:
                    continue
                seen.add(key)
                matched.append({"domain_id": domain.id, "agent_id": m.agent_id or "",
                                "name": name, "domain_name": getattr(domain, "name", domain.id)})
    except Exception:
        return None
    if not matched:
        return None
    if all_hands and len(matched) > _ALL_HANDS_CAP:
        matched = matched[:_ALL_HANDS_CAP]      # 全员上桌封顶(防失控/截断一把 key)
    # 圆桌判定:有圆桌词 + ≥1 角色,或点到 ≥2 个**不同**角色,或全员意图。
    # (同一角色名跨多个域 ≠ 多人:"让分析师出周报" 命中两个域的"分析师"也只是单点委派,
    #  别误升圆桌 —— 真模型压测台逮到的 bug。)
    distinct_names = {m["name"] for m in matched}
    if not ((has_kw and matched) or len(distinct_names) >= 2 or (all_hands and matched)):
        return None
    # 群定位:显式点到大群,或角色跨域 → l0 大群;否则同域群。
    wants_world = any(k in low for k in ("karvy world", "karvyworld")) or ("大群" in intent)
    domains_hit = {m["domain_id"] for m in matched}
    if wants_world or len(domains_hit) > 1:
        group_domain_id = KARVY_WORLD_DOMAIN
        group_name = "Karvy World"
    else:
        only = matched[0]
        group_domain_id = only["domain_id"]
        group_name = only["domain_name"]
    return {
        "group_domain_id": group_domain_id,
        "group_name": group_name,
        "participants": [m["agent_id"] for m in matched],
        "participant_names": [m["name"] for m in matched],
        "topic": intent,
    }


def _roundtable_state(app) -> dict:
    """待办圆桌态(conv_id → {topic, participants, phase})。持久化(配了 config_path 时)→ 重启续
    "开始讨论"。测试无 config_path → 纯内存(不污染真实 home)。"""
    st = getattr(app.state, "roundtables", None)
    if st is None:
        cfgp = getattr(app.state, "config_path", "") or ""
        if cfgp:
            import json
            import pathlib
            path = pathlib.Path(cfgp).parent / "roundtables.json"
            app.state._roundtables_path = path
            try:
                st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                st = {}
            if not isinstance(st, dict):
                st = {}
        else:
            st = {}   # 无 config(测试)→ 纯内存
        app.state.roundtables = st
    return st


def _persist_roundtable_state(app) -> None:
    """待办圆桌态落盘(配了路径才落;原子写)。"""
    path = getattr(app.state, "_roundtables_path", None)
    if path is None:
        return
    try:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(app.state.roundtables, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[roundtable] 待办态持久化失败: {e}")


def _roundtable_pending(app, conv_id: str):
    """这条对话若是"待讨论圆桌"(阶段0未点开始)→ 返 {conversation_id, participants} 供前端亮横幅。"""
    st = _roundtable_state(app).get(conv_id or "")
    if st and st.get("phase") == "aligning":
        names = []
        dom_reg = getattr(app.state, "domain_registry", None)
        peer = None
        mgr = getattr(app.state, "conversation_manager", None)
        if mgr is not None:
            peer = mgr.current_peer()
        roster = _roundtable_roster(app, peer) if peer is not None else []
        by_id = {a.agent_id: a for a in roster}
        for aid in st.get("participants", []):
            a = by_id.get(aid)
            names.append(_member_display(app, a) if a is not None else aid)
        return {"conversation_id": conv_id, "participants": names}
    return None


def _member_display(app, addr) -> str:
    """轻量取一个成员的展示名(花名/职务),不构造人格 prompt(给名册列表用)。"""
    role_reg = getattr(app.state, "role_registry", None)
    rid = (addr.agent_id or addr.role) or ""
    if role_reg is not None and rid:
        try:
            rv = role_reg.get(rid)
            if rv is not None and hasattr(rv, "display_name"):
                return rv.display_name()
        except Exception:
            pass
    return rid or addr.role or "角色"


def _roundtable_roster(app, peer) -> list:
    """这个群场能拉谁上桌(返回 [Address]):
    - 业务域群 → 本域的 agent(排除 user);
    - karvy world 大群(l0)→ 你**所有**的 agent:跨所有活跃域的成员 **+ 独立角色**(不在任何域的,
      如导入的一批 agent)。否则那些角色哪个群都 @ 不到(Hardy 2026-06-30 报:大群 @ 匹配不到角色 ——
      他有几百个导入角色但零业务域,旧逻辑只聚合域成员 → 名册空)。
    """
    from karvyloop.karvy.capability import is_karvy_peer
    from karvyloop.domain import Address
    if peer is None:
        return []
    dom_reg = getattr(app.state, "domain_registry", None)
    out, seen = [], set()
    try:
        if is_karvy_peer(peer.domain_id):
            member_agent_ids = set()
            if dom_reg is not None:
                for d in dom_reg.list_active():
                    for a in dom_reg.resolve_members(d.id):
                        if a.role == "user":
                            continue
                        k = (a.domain_id, a.agent_id)
                        if k not in seen:
                            seen.add(k); out.append(a)
                        member_agent_ids.add(a.agent_id)
            # 独立角色(不归任何域)也能在大群 @ —— 用 agent_id 去重,别和已收的域成员重
            role_reg = getattr(app.state, "role_registry", None)
            if role_reg is not None:
                for rv in role_reg.list_all():
                    rid = getattr(rv, "id", "")
                    if rid and rid not in member_agent_ids:
                        out.append(Address(domain_id="", role="agent", agent_id=rid))
        elif dom_reg is not None:
            for a in dom_reg.resolve_members(peer.domain_id):
                if a.role != "user":
                    out.append(a)
    except Exception as e:
        logger.warning(f"[roundtable] 取名册失败: {e}")
    return out


# 结果文档里的少数派报告标头(英文常量;待主会话统一 i18n,建议 key:
# roundtable.result.dissent_header)。分歧留档从"prompt 里顺嘴提"升成**结构规则**:
# 收口时仍开放的 dissent 必然出现在产物里,不靠模型自觉。
RESULT_DOC_DISSENT_HEADER = "Key dissents (kept on the record)"


def _roundtable_result_doc(result: dict) -> str:
    """把圆桌产出拼成"结果文档":结论为主 + 少数派报告(关键分歧留档)+ 内部讨论附后。

    这份文档进 task_registry → 同步到工作台首页【流进来的料】卡;点卡看这份(结论+讨论),
    再"打开聊天"跳回群场追问小卡(Hardy:圆桌结果要回流首页,点击去聊天查看+追问)。
    """
    concl = (result.get("conclusion") or "").strip()
    parts = [concl or "(小卡未给出结论)"]
    dis = [str(d).strip() for d in (result.get("dissents") or []) if str(d).strip()]
    if dis:
        parts.append(f"\n\n---\n\n**{RESULT_DOC_DISSENT_HEADER}**:")
        for d in dis:
            parts.append(f"\n- ⚖️ {d}")
    tr = result.get("transcript") or []
    if tr:
        parts.append(f"\n\n---\n\n**内部讨论**({result.get('rounds', 0)} 轮):")
        for x in tr:
            parts.append(f"\n- **R{x.get('round')} · {x.get('speaker', '?')}**:{(x.get('text') or '').strip()}")
    return "".join(parts)


def _conclusion_risk(result: dict, *, shared_layer: bool) -> str:
    """圆桌结论落认知库前的风险分级(纯函数)。返回风险原因;"" = routine(照旧直写)。

    高风险(升 H2A 卡,人 ACCEPT 才落认知库;kind ∈ silence.HIGH_RISK_KINDS 绝不被
    "挣来的静音"自动兑现)—— 任一命中即高风险:
    - shared_layer:结论要进**共享/通用认知层**(跨域 world 圆桌,无 applies 过滤,
      影响未来**所有**决策的召回)→ 一律过人。
    - unresolved_dissent:收口时仍有未解决 dissent(少数派报告)→ 结论有争议,写进
      影响未来决策的认知前必须人拍。
    - no_consensus:没真收敛(轮数到顶硬停)→ 共识没达标的"结论"更要过人。
    routine(不弹卡 —— 挣得的静默,不是每个圆桌都打扰):**域内**圆桌 + 干净收敛 +
    无遗留分歧 → 照旧直写域私有认知(召回只在本域,爆炸半径有限)。
    """
    if shared_layer:
        return "shared_layer"
    if result.get("dissents"):
        return "unresolved_dissent"
    if not result.get("converged"):
        return "no_consensus"
    return ""


def _parse_moderation_json(text: str):
    """严格解析主持人**结构化裁决** JSON;任何不合格 → None(宁空勿毒 —— 调用方退回旧词法)。

    规矩(与 LLM 输出解析器纪律同口径):只剥最外层代码围栏;json.loads 严格;必须是 dict;
    consensus 必须是 [0,1] 数值(bool 不算);prose 不抽、不猜。open_dissents/recommendation
    的逐项类型清洗交给 karvy.roundtable.normalize_moderation(单一实现,不两处各写)。
    """
    import json
    import re
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)   # 只剥最外层 fence
    if m:
        t = m.group(1).strip()
    try:
        d = json.loads(t)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    c = d.get("consensus")
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return None
    if not (0.0 <= float(c) <= 1.0):
        return None
    return d


async def _host_moderate_call(gw, model_ref, topic, transcript, *, final):
    """小卡兼主持:每轮**结构化裁决**(consensus/open_dissents/recommendation)+ 收敛产出。

    非 final 轮输出严格 JSON:{"consensus": 0-1, "open_dissents": [...], "recommendation": "..."};
    解析失败 → 退回旧词法(CONVERGE 一词收敛,否则再一轮),绝不崩。收口逻辑(阈值/少数派
    报告/轮数硬顶)在 karvy.roundtable.run_roundtable_session,这里只产裁决数据。
    """
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    convo = "\n".join(f"[{x['speaker']}] {x['text']}" for x in transcript) or "(还没人发言)"
    if final:
        sysp = ("你是圆桌主持人小卡。把下面这场围绕主题的讨论**收敛成一份简洁结论**"
                "(给老板看、并写进认知库)。抓住共识与关键分歧,给可用的产出。只输出结论本身。")
    else:
        sysp = ("你是圆桌主持人小卡,管三件事:明确主题、防跑偏、防冷场。看这场讨论,"
                "只输出**严格 JSON**(不要围栏、不要任何其他文字):"
                '{"consensus": 0到1的数(讨论对主题的共识程度,1=完全一致), '
                '"open_dissents": ["谁:一句话概括仍未被回应的实质分歧", ...](没有则 []), '
                '"recommendation": "你此刻会给的一句话建议"}。'
                "注意:重复过的、已被回应的、纯情绪的抬杠**不算** open_dissents,"
                "只记有价值的实质反对。")
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        with _token_src("roundtable"):   # 主持人调用(P0-9:此前无标;成员发言另走 forge 标不动)
            async for ev in gw.complete([{"role": "user", "content": f"主题:{topic}\n\n讨论:\n{convo}"}],
                                        [], ref, system=SystemPrompt(static=[sysp])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception:
        out = ""
    if final:
        return {"text": out.strip()}
    parsed = _parse_moderation_json(out)
    if parsed is not None:
        return {"consensus": float(parsed["consensus"]),
                "open_dissents": parsed.get("open_dissents") or [],
                "recommendation": parsed.get("recommendation") or ""}
    # 解析失败 → 旧词法兜底("没到就再一轮",别崩;老模型回 CONVERGE 一词也仍然认)
    return {"action": "converge" if "CONVERGE" in out.upper() else "continue"}


async def _roundtable_clarify_opening(gw, model_ref, topic, member_names) -> str:
    """阶段0:小卡作主持,开讨论**前**先跟用户对齐目标(需求分析)——复述理解 + 2-3 个澄清问题。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡。用户刚发起一个圆桌。开讨论**之前**,你的第一件事是**跟用户"
            "对齐目标(需求分析)**:用一两句复述你对主题的理解,然后问 2-3 个最关键的澄清问题"
            "(要分析什么、想要的产出/目标是什么、范围或约束)。**别开始讨论、别替成员发言**,只对齐。"
            "亲切、简洁、像主持人开场。")
    usr = f"圆桌主题:{topic}\n准备上桌的成员:{'、'.join(member_names) or '(待定)'}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        with _token_src("roundtable"):   # P0-9
            async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                        system=SystemPrompt(static=[sysp])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 对齐开场失败: {e}")
    return out.strip() or (f"我们先对齐一下「{topic}」:你最想分析的核心是什么?期望的产出/目标是?"
                           "有没有范围或约束?对齐清楚我就开始组织讨论。")


async def _roundtable_goal_summary(gw, model_ref, topic, align_text) -> str:
    """阶段0→1:把"小卡↔用户"的对齐对话收敛成一句**目标**,喂给即将上桌的成员。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡。根据你和用户刚才对齐目标的对话,把这次圆桌**要解决的目标**"
            "收敛成一句清晰具体的话(给即将上桌的成员看,让他们围绕它讨论)。只输出这一句目标。")
    usr = f"主题:{topic}\n\n对齐对话:\n{align_text or '(用户未补充,按主题字面理解)'}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        with _token_src("roundtable"):   # P0-9
            async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                        system=SystemPrompt(static=[sysp])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 目标收敛失败: {e}")
    return out.strip() or topic


def _roundtable_members(app, peer, participants):
    """按勾选的成员从群名册取;空 → 全上桌。返回 [Address]。

    §2.6:寻址用 **(域, agent_id) 复合键**(`域::agent_id`)—— 同名角色跨域才能独立选中。
    兼容旧数据:也认裸 agent_id。
    """
    roster = _roundtable_roster(app, peer)
    if participants:
        chosen = set(participants)
        return [a for a in roster
                if f"{a.domain_id}::{a.agent_id}" in chosen or a.agent_id in chosen]
    return list(roster)


def _roundtable_external_guests(app, peer, participants):
    """M2(#71 §7.1):从勾选成员里挑出**外部公民客人席**(不占 role 决策席、只供稿)。

    - participants 里被原生名册认掉的是 role;剩下的 citizen_id 若在 citizen_registry 里 →
      外部客人。返回 [ExternalCitizen]。
    - 确定性域约束(#71 §2.6.5):scoped(T1)只能进它绑定的那个域;guest(T0)任意域当纯客人。
      进不了这个域(跨域的 scoped)一律拒(deny-by-default),不上桌。
    - 未接 external_runtime → 空(零回归:纯原生 role 圆桌)。
    """
    from karvyloop.karvy.external_collab import can_join_domain, find_external_target
    reg = getattr(app.state, "citizen_registry", None)
    if reg is None or not participants:
        return []
    domain_id = getattr(peer, "domain_id", "") or "" if peer is not None else ""
    # 已被原生名册认掉的裸/复合键,不再当外部目标(避免重名误判)。
    native = _roundtable_members(app, peer, participants)
    native_ids = {a.agent_id for a in native} | {f"{a.domain_id}::{a.agent_id}" for a in native}
    guests, seen = [], set()
    for p in participants:
        name = (p or "").split("::")[-1] if isinstance(p, str) else ""
        if not name or name in native_ids or p in native_ids or name in seen:
            continue
        citizen = find_external_target(reg, domain_id, name)
        if citizen is None:
            continue
        # 域约束:scoped 跨域 → 不上桌(deny-by-default);guest 任意域当纯客人。
        if not can_join_domain(citizen, domain_id):
            logger.info(f"[roundtable] 外部公民「{name}」不能进域「{domain_id}」的圆桌(scoped 跨域),跳过")
            continue
        seen.add(name)
        guests.append(citizen)
    return guests


def _roundtable_external_roster(app, peer) -> list[dict]:
    """圆桌客人席入口(#71 §7.1 + docs/73 §4):能进这个场的**外部公民**列成名册项,让用户能勾选
    上桌当客人供稿(产出恒 untrusted、走 external_adopt 采纳门、不占决策席)。

    后端供稿链本就全通,此前只差这个入口 → 圆桌外部协作在生产里几乎触发不到(自闭环审计逮到的
    built-not-wired 缺口)。key 复用前端同一套 `域::citizen_id`:`_roundtable_external_guests` 提
    末段 citizen_id 解析成客人;`_roundtable_members`(纯原生 roster)不会误收。返回 [member dict]。
    """
    cit_reg = getattr(app.state, "citizen_registry", None)
    if cit_reg is None or peer is None:
        return []
    from karvyloop.karvy.external_collab import can_join_domain
    dom_id = getattr(peer, "domain_id", "") or ""
    out: list[dict] = []
    try:
        for c in cit_reg.list_active():
            cid = getattr(c, "citizen_id", "") or ""
            if not cid or not can_join_domain(c, dom_id):
                continue   # scoped(T1)跨域 / 无 id → deny-by-default 不列
            out.append({"agent_id": cid, "role": "external",
                        "domain_id": getattr(c, "domain_id", "") or "",
                        "domain_name": "", "display": cid, "is_external": True})
    except Exception:  # noqa: BLE001 — 取外部名册失败不拖垮原生名册
        return out
    return out


def _build_roundtable_room(app, peer, conversation_id, members, guests):
    """docs/73 §4:把这场圆桌收进一个一等 **Room**,用 opacity **属性**(而非"哪个 resolver
    返回的"这条约定)结构化钉死"谁的产出能进对话主线"。

    - 自家 role 成员 → RoomMember(kind=role) → normalize 恒 `internal` → enters_mainline()=True。
    - 外部客人 → RoomMember(kind=external, opacity=opaque) → **强制 opaque**(哪怕上游误标
      internal 也 deny-by-default 降级)→ enters_mainline()=False → **结构上**进不了 member_reply
      transcript。**A2A Contagion 防御从"约定"升成 Room 的不变量**(§4 防平行独白强制)。
    - scope:圆桌用默认 workspace(未 share);share 出去的 channel 单独 workspace 是 M3(§0.5)。
    """
    from karvyloop.collab.registry import Room
    from karvyloop.collab.room import (
        OPACITY_OPAQUE, PARTICIPANT_EXTERNAL, PARTICIPANT_ROLE, RoomMember,
    )
    domain_id = (getattr(peer, "domain_id", "") or "") if peer is not None else ""
    rm: list = []
    for a in (members or []):
        rm.append(RoomMember(participant_id=(a.agent_id or a.role or ""),
                             kind=PARTICIPANT_ROLE, domain_id=(a.domain_id or ""),
                             display_name=_member_display(app, a)))
    for g in (guests or []):
        cid = getattr(g, "citizen_id", "") or ""
        rm.append(RoomMember(participant_id=cid, kind=PARTICIPANT_EXTERNAL,
                             opacity=OPACITY_OPAQUE, domain_id=domain_id, display_name=cid))
    return Room(room_id=f"rt::{conversation_id or ''}", members=tuple(rm),
                origin_domain_id=domain_id, title=f"🎡 {conversation_id or ''}")


async def _roundtable_clarify_turn(gw, model_ref, topic, align_history, user_msg):
    """阶段0 对话式(Hardy:少按钮)—— 小卡看对齐对话 + 用户最新一句,判断够不够开始讨论了。

    返 (reply, ready)。ready=True → 目标已清楚(或用户说可以开始),小卡这就自己组织讨论;
    False → 还需澄清,继续问。小卡在末尾单独一行写 READY/ASK,这里解析后剥掉。
    """
    import re
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡,正在和用户**对齐圆桌目标**(还没开始讨论)。看已有对齐对话 + 用户最新一句,"
            "判断:目标是否**已清楚到可以组织成员讨论了**(或用户已明确表示可以开始)。"
            "够了 → 回一句简短的「好,我这就组织大家讨论」,并在**最后单独一行**只写 READY;"
            "还需澄清 → 继续问最关键的 1-2 点(必要时直接问「这样我可以开始了吗?」),最后单独一行只写 ASK。")
    usr = f"圆桌主题:{topic}\n\n对齐对话:\n{align_history or '(刚开始)'}\n\n用户最新一句:{user_msg}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        with _token_src("roundtable"):   # P0-9
            async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                        system=SystemPrompt(static=[sysp])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 对齐轮失败: {e}")
    text = out.strip()
    m = re.search(r"\n\s*(READY|ASK)\s*$", text)
    if m:
        ready = (m.group(1) == "READY")
        text = text[:m.start()].strip()
    else:
        ready = ("READY" in text) and ("ASK" not in text)
        text = re.sub(r"\b(READY|ASK)\b", "", text).strip()
    return (text or "（我再想想怎么帮你对齐）"), ready


def _external_supply_doc(external_supply: list) -> str:
    """把外部客人供稿拼成文档段(醒目标外部·untrusted·需采纳;不与原生讨论混脸)。"""
    if not external_supply:
        return ""
    parts = ["\n\n---\n\n**🔌 外部供稿**(不可信数据 · 需你拍板采纳才算数):"]
    for s in external_supply:
        cid = s.get("citizen_id", "?")
        if s.get("ok"):
            body = (s.get("text") or "").strip() or "(无产出)"
            parts.append(f"\n- **🔌 {cid}**(待采纳):{body}")
        else:
            reason = s.get("reason", "") or "失败"
            tag = "要权限/澄清(已升 H2A)" if s.get("input_required") else reason
            parts.append(f"\n- **🔌 {cid}** ✗:{tag}")
    return "".join(parts)


async def _run_external_guest_supply(app, *, guests, goal, topic, peer, task_id,
                                     conversation_id, should_cancel=None) -> list:
    """M2 客人供稿(#71 §7.1):外部公民各派一次活拿 untrusted 产出,每条升 external_adopt 采纳门。

    - 走 external_collab.drive_external_contribution(bridge 子进程,产出恒 untrusted、登记供稿账本)。
    - 成功产出 → 建 external_adopt 提案(H2A 采纳门)+ 广播到决策舱;input_required → 也升卡提醒。
    - **不写记忆、不进 record_turn 主线、不喂给 role**(不占决策席、不触发别人)。
    - 客人席失败不拖垮整桌(fail-loud 记 reason,继续下一个)。返回 [contribution dict]。
    """
    if not guests:
        return []
    import time as _t

    from karvyloop.karvy.external_collab import (
        build_external_adopt_proposal, drive_external_contribution,
    )
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    bridge_factory = getattr(app.state, "external_bridge_factory", None)
    if bridge_factory is None:
        # 无桥工厂(未接执行面)→ 诚实降级:客人上桌了但派不了活,标 fail-loud,不静默。
        from karvyloop.external_runtime import bridge_factory as _default_bf
        bridge_factory = _default_bf
    token_recorder = getattr(app.state, "external_token_recorder", None)
    reg = getattr(app.state, "citizen_registry", None)
    proposal_reg = getattr(app.state, "proposal_registry", None)
    domain_id = getattr(peer, "domain_id", "") or "" if peer is not None else ""
    task = (f"圆桌目标:{goal}\n(原始主题:{topic})\n\n"
            "请你围绕这个目标给出你的看法/产出(它会作为外部供稿交给用户拍板,自足描述你的结论)。")
    out: list = []
    for citizen in guests:
        if should_cancel is not None:
            try:
                if should_cancel():
                    break
            except Exception:
                pass
        cid = getattr(citizen, "citizen_id", "") or ""
        seed_id = f"rt-{conversation_id}-{cid}-{int(_t.time() * 1000)}"
        contrib = await drive_external_contribution(
            citizen, task, bridge_factory=bridge_factory, token_recorder=token_recorder,
            citizen_registry=reg, seed_id=seed_id, context_note="")
        out.append(contrib)
        # 实时推送(谁供了稿 / 谁失败;客人席也上时间线,🔌 标外部)
        await _push_step(app, task_id, cid, f"🔌 {cid}",
                         "done" if contrib.get("ok") else "failed",
                         "" if contrib.get("ok") else (contrib.get("reason") or ""))
        if not contrib.get("ok"):
            continue
        # 成功产出 → 升 external_adopt 采纳门(H2A 唯一升级门:采纳才穿来源边界)
        if proposal_reg is not None:
            try:
                prop = build_external_adopt_proposal(
                    citizen_id=cid, domain_id=domain_id, seed_id=seed_id,
                    output=contrib.get("text", ""),
                    context=f"圆桌「{topic[:40]}」的外部供稿", ts=_t.time(),
                    conversation_id=conversation_id)
                proposal_reg.register(prop)
                from karvyloop.console.proposals import broadcast_proposal
                await broadcast_proposal(app, prop)
            except Exception as e:  # noqa: BLE001 — 升卡失败不阻断供稿收集
                logger.warning(f"[roundtable] 外部供稿升采纳卡失败(cid={cid}): {e}")
    return out


# 轮数上限的硬顶(调用方/待办态可配 max_rounds,但夹在 [1, _MAX_ROUNDS_CAP] 内 ——
# 配置也不许把圆桌烧穿;顶值待 Trace 真数据标定)。
_MAX_ROUNDS_CAP = 8


def _effective_max_rounds(st: dict, override=None) -> int:
    """这场圆桌的轮数上限:调用方传参 > 圆桌待办态 st["max_rounds"](发起时按议题存)>
    默认 DEFAULT_MAX_ROUNDS(=3)。坏值一律回默认;夹 [1, _MAX_ROUNDS_CAP] 硬兜底。"""
    from karvyloop.karvy.roundtable import DEFAULT_MAX_ROUNDS
    raw = override if override is not None else (st or {}).get("max_rounds")
    try:
        n = int(raw) if raw is not None else DEFAULT_MAX_ROUNDS
    except (TypeError, ValueError):
        n = DEFAULT_MAX_ROUNDS
    return max(1, min(n, _MAX_ROUNDS_CAP))


async def _execute_roundtable_discussion(app, conversation_id: str,
                                         max_rounds: int | None = None) -> dict[str, Any]:
    """圆桌阶段1 执行核心(被 /discuss 和 对话式自动开始 复用):goal→成员群聊→收敛→产出→记录。

    max_rounds:轮数上限覆盖(None → 待办态里存的 → 默认 3;见 _effective_max_rounds)。"""
    from .routes import _model_for_role, _persona_for_role_addr, _rk_model, drive_in_tui
    mgr = getattr(app.state, "conversation_manager", None)
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    st = _roundtable_state(app).get(conversation_id)
    if not st:
        return {"ok": False, "reason": "没有待讨论的圆桌"}
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "请在圆桌窗里开始"}
    mgr.resume(peer, conversation_id)   # 确保结果追加进这条圆桌对话
    members = _roundtable_members(app, peer, st["participants"])
    # M2(#71 §7.1):外部公民作为**客人供稿席**上桌(不占 role 决策席、只供稿、产出恒 untrusted)。
    guests = _roundtable_external_guests(app, peer, st["participants"])
    if not members and not guests:
        return {"ok": False, "reason": "圆桌成员不在了(域里角色变动?)"}
    # docs/73 §4 防平行独白 · A2A Contagion 结构强制:把成员+客人收进一个一等 Room,
    # 用 opacity **属性**结构化钉死决策席(member_reply 主线只放 enters_mainline() 的 internal)。
    # members 本就 role/internal、guests 本就 external/opaque → 正常路径 safe==members(零回归);
    # 但万一有外部混进决策席(未来重构/重名),Room 强制 opaque → 此处 fail-loud 剔除,绝不让它
    # 进 record_turn 触发别的 role(A2A 防御从"两个 resolver 各管一半"的约定升成 Room 的不变量)。
    room = _build_roundtable_room(app, peer, conversation_id, members, guests)
    _mainline_ids = {m.participant_id for m in room.internal_members()}
    _safe = [a for a in members if (a.agent_id or a.role or "") in _mainline_ids]
    if len(_safe) != len(members):
        logger.error("[roundtable] 结构守卫:非-internal 成员混进决策席,已按 Room opacity 剔除"
                     f"(A2A 防御;{len(members)}→{len(_safe)})")
        members = _safe
    if not members and not guests:
        return {"ok": False, "reason": "圆桌成员不在了(域里角色变动?)"}
    governance = mgr.governance_text() or ""
    ws = rk.get("workspace_root", "/")
    model_ref = rk.get("model_ref", "")
    topic = st["topic"]

    # 把"小卡↔你"的对齐对话收敛成一句 goal,喂给成员
    ctx = mgr.context_view() or ()
    align = "\n".join(
        ((f"你:{tn.user_intent}" if tn.user_intent else "")
         + (f"\n小卡:{tn.agent_response}" if tn.agent_response else "")).strip()
        for tn in ctx
    ).strip()
    with _token_src("roundtable_host"):     # 目标收敛归到 roundtable_host 源(原记成 unknown)
        goal = await _roundtable_goal_summary(gw, model_ref, topic, align)
    # Step 0(a):你的决策标准在**圆桌**里也生效(成员发言按你的标准对齐;fresh 只跳执行记忆,
    # 不跳你的标准 —— governance 显式传仍生效)。query=goal → 按相关性召回。
    from karvyloop.console.decision_wire import assemble_governance
    governance = assemble_governance(app, intent=goal, domain=(peer.domain_id or ""), base=governance)

    async def member_reply(addr, _topic, transcript):
        dom = dom_reg.get(addr.domain_id)
        persona, speaker = _persona_for_role_addr(app, addr, dom, ws)
        convo = "\n".join(f"[{x['speaker']}] {x['text']}" for x in transcript)
        intent = (f"圆桌目标:{goal}\n(原始主题:{topic})\n\n"
                  + (f"已有讨论:\n{convo}\n\n" if convo else "")
                  + "请你围绕**目标**,从你的职务/视角给出看法(简洁、有观点,别复述别人)。")
        outcome = await drive_in_tui(intent, main_loop, governance=governance,
                                     persona=persona, scope="domain", fresh=True,
                                     **_rk_model(rk, _model_for_role(app, addr.agent_id)))
        err = getattr(outcome, "error", "")
        # §0.7 P2:圆桌成员发言/缺席即时推送(实时看谁说了、谁没回应)
        await _push_step(app, task_id, addr.agent_id, speaker,
                         "failed" if err else "done", err)
        if err:
            return None
        return {"speaker": speaker, "text": (outcome.text or "").strip()}

    async def host_moderate(_topic, transcript, *, final):
        with _token_src("roundtable_host"):    # 主持控场也归 roundtable_host(原 unknown)
            return await _host_moderate_call(gw, model_ref, goal, transcript, final=final)

    task_reg = getattr(app.state, "task_registry", None)
    task_id = (task_reg.start(who="🎡 圆桌", domain_id=peer.domain_id, role="group",
                              intent=f"🎡 {topic[:120]}") if task_reg is not None else None)
    from karvyloop.karvy.roundtable import run_roundtable_session

    from .workflow_engine import _clear_task_cancelled, _is_task_cancelled  # noqa: F401
    # §0.7 逃生门:人点"中止" → 按 task_id 记旗 → 每轮开始前查它 → 圆桌不再烧下一轮 token。
    def _should_cancel() -> bool:
        return _is_task_cancelled(app, task_id or "")
    # 50+ 大桌:全员上桌(封顶 64,防真·失控),但**并发只 6 路**——别 50 路同时打一把 key 截断。
    _seats = min(len(members), 64)
    _rounds_cap = _effective_max_rounds(st, max_rounds)   # 可配轮数(默认 3;硬夹防烧穿)
    if members:
        try:
            result = await run_roundtable_session(goal, members, member_reply=member_reply,
                                                  host_moderate=host_moderate,
                                                  max_rounds=_rounds_cap,
                                                  max_seats=_seats, concurrency=6,
                                                  should_cancel=_should_cancel)
        except Exception as e:
            if task_reg is not None and task_id is not None:
                task_reg.finish(task_id, error=str(e))
            logger.exception(f"[roundtable] 讨论异常: {e}")
            return {"ok": False, "reason": f"圆桌讨论失败: {e}"}
    else:
        # 只有外部客人、无原生 role → 无 role 讨论主线(外部不占决策席);只做客人供稿。
        result = {"topic": topic, "transcript": [], "rounds": 0,
                  "converged": False, "conclusion": "", "cancelled": False,
                  "consensus": None, "dissents": []}
    # M2 客人供稿席(#71 §7.1):外部公民**不进 role 讨论主线**(不占决策席、不被 record_turn),
    # 单独派活(走 bridge)拿 untrusted 产出,每条升 external_adopt 采纳门(H2A 才穿来源边界)。
    # 铁律:外部产出**不直接触发别的 agent**——它不喂进 role 的 member_reply transcript(A2A
    # Contagion 防御:要接力必经小卡编排 + H2A)。
    external_supply = await _run_external_guest_supply(
        app, guests=guests, goal=goal, topic=topic, peer=peer, task_id=task_id,
        conversation_id=conversation_id, should_cancel=_should_cancel)
    result["external_supply"] = external_supply
    result["topic"] = topic
    result["goal"] = goal
    result_doc = _roundtable_result_doc(result)
    if external_supply:
        result_doc += _external_supply_doc(external_supply)
    # 中止旗用完即清(下次同 task_id 复用不误判);中止的圆桌在文档里如实标一句。
    _clear_task_cancelled(app, task_id or "")
    if result.get("cancelled"):
        result_doc = "🛑 (已中止)\n\n" + result_doc
    if task_reg is not None and task_id is not None:
        task_reg.finish(task_id, result=result_doc)
        task_reg.set_conversation(task_id, conversation_id)   # 卡 → 跳回这条圆桌

    # 讨论产出追加进圆桌对话(带结构化负载 → 群聊串渲染)
    try:
        mgr.record_turn("🎡 圆桌讨论", result_doc, brain="slow", task_id=task_id or "",
                        data={"roundtable": {
                            "topic": topic, "goal": goal,
                            "transcript": result.get("transcript", []),
                            "conclusion": result.get("conclusion", ""),
                            "rounds": result.get("rounds", 0),
                            "converged": result.get("converged", False),
                            # 结构化收口产物(前端渲染共识度 + 少数派报告;加性字段)
                            "consensus": result.get("consensus"),
                            "dissents": result.get("dissents", []),
                        }})
    except Exception as e:
        logger.warning(f"[roundtable] 追加讨论记录失败: {e}")

    # 产出 → 认知库(B:高风险结论过 H2A,routine 照流 —— 对齐招牌"你拍板")
    mem = getattr(app.state, "memory", None)
    if result.get("conclusion") and mem is not None:
        try:
            import time as _t
            from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
            from karvyloop.schemas.cognition import Belief
            # §2.6 认知两层:域群圆桌 → 域专属(私有)认知(applies.domain,只在本域召回);
            # l0 大群(跨域)圆桌 → 通用/共享层(无 applies)。
            _dom = getattr(peer, "domain_id", "") if peer is not None else ""
            applies = ({"domain": _dom, "role": "group"}
                       if _dom and _dom != KARVY_WORLD_DOMAIN else {})
            risk = _conclusion_risk(result, shared_layer=(not applies))
            if risk:
                # 高风险(共享层 / 带遗留分歧 / 未达共识收口)→ **不直写**认知库:
                # 升 H2A 卡(kind ∈ HIGH_RISK_KINDS,绝不被静音自动兑现),ACCEPT 才落库。
                from karvyloop.console.proposals import broadcast_proposal
                from karvyloop.karvy.proposal_registry import proposal_for_roundtable_conclusion
                prop = proposal_for_roundtable_conclusion(
                    topic=topic, conclusion=result["conclusion"], domain_id=_dom,
                    applies=applies, dissents=result.get("dissents") or [],
                    consensus=result.get("consensus"), rounds=result.get("rounds", 0),
                    converged=bool(result.get("converged")), risk=risk,
                    conversation_id=conversation_id, ts=_t.time())
                await broadcast_proposal(app, prop)
            else:
                # routine(域内 + 干净收敛 + 无遗留分歧)→ 照旧直写域私有认知(不打扰)。
                mem.write(Belief(
                    content=f"圆桌「{topic[:40]}」结论:{result['conclusion'][:600]}",
                    provenance={"source": "roundtable", "kind": "fact",
                                "topic": topic[:80], "applies": applies},
                    freshness_ts=_t.time(), scope="personal"))
        except Exception as e:
            logger.warning(f"[roundtable] 结论写认知/升卡失败: {e}")

    st["phase"] = "done"
    _persist_roundtable_state(app)
    return {"ok": True, "conversation_id": conversation_id, **result}
