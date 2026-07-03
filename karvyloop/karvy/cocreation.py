"""cocreation — 【共创模式】状态机 v1(docs/47,Hardy 2026-07-02 碎碎念③)。

**定位**:建 agent 场景的 plan mode —— 意图命中后小卡递口("要不要一起把它建出来?"),
进入一个**有状态、零副作用、随时可跳车**的共建会话;产物全部落**既有结构**
(域 value.md/deontic + role IDENTITY/soul + COMMITMENT 统一 seed),定稿走 H2A 确认卡。

**状态机(v1 只做 S1/S2/S5;S3 试跑 / S4 复盘留位)**:

    OFFERED ──用户应"一起深挖"──> S1_CLARIFY ──问够/跳车──> S2_DRAFT ──"就这样吧"──> S5(出 H2A 卡,清态)
       │                             │(贴模板 → 直接短路到模板确认,S2 形态)
       └── 拒绝/换话题 → 清态          └── 任一状态"就这样吧" → 用当前草案立即出卡(缺项 default seed)

**硬纪律(全部 harness 强制,不靠人格自觉)**:
- 澄清**每轮最多 2 问、每问带候选、最多 3 轮**(代码截断 + 轮数计数,prompt 只管问法质量);
- 草案卡 = **唯一事实源**:S2 后只接受 **diff 式字段修改**(每次 ≤3 个字段,改过即锁),
  **绝不整卡重生成**(业界对话式 builder 的"修 A 坏 B"死循环反例,docs/47 §五);payload **全字符串** →
  H2A 卡上天然可用既有「改了再批」(apply_payload_edits 白名单)逐字段终改;
- **S1/S2 零副作用**:不写任何 registry/磁盘,草案只活在会话态;第一次落地写发生在
  S5 ACCEPT 的 handler 里(复用 instantiate_template / RoleRegistry.create 路径,
  COMMITMENT 尽责契约由 create 统一 seed —— 共创是"引导自建"入口的对话皮,不是第四入口);
- **跳车语义**:任何时刻"就这样吧/直接建/别问了" → 当前最佳草案立即出卡,缺项走 default;
  "退出/算了/不建了" → 清态回普通聊天。跳车不是失败,是对"不想被访谈的用户"的尊重。
- **宁空勿毒**:所有 LLM 输出严格 JSON;解析失败 → 确定性兜底(澄清用固定四维模板问句,
  草案用确定性最小草案),绝不把散文垃圾写进草案/卡。
- **会话粘性(修"第二轮换说法就掉线"脆点)**:会话态挂在 (peer, conversation id) 上,
  激活期间整轮进状态机,**不再依赖逐轮关键词命中**;清态才回普通路径。

**接入点**:console/ws.py `_handle_intent_ws`(粘性门 + 递口);REST routes.api_intent 同款
接线留主线(routes.py 本轮不动)。token 记账:内部 LLM 调用统一打 `token_source("cocreation")`。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---- 状态常量 ----
STATE_OFFERED = "offered"        # 已递口,等用户应"一起深挖"还是"自己看模板"
STATE_CLARIFY = "s1_clarify"     # S1 目标澄清(四维采集)
STATE_DRAFT = "s2_draft"         # S2 草案卡(唯一事实源,diff 式修改)
# S3 试跑一单 / S4 复盘调整:本轮不做,状态机留位(docs/47 §3.2)
STATE_TRIAL = "s3_trial"         # 留位,未实现
STATE_REVIEW = "s4_review"       # 留位,未实现

# ---- harness 硬上限(docs/47 §3.2 S1 纪律)----
MAX_CLARIFY_ROUNDS = 3           # 最多 3 轮澄清,超过必须出草案
MAX_QUESTIONS_PER_ROUND = 2      # 每轮最多 2 问
MAX_CANDIDATES_PER_QUESTION = 3  # 每问最多 3 个候选答案
MAX_EDIT_FIELDS_PER_TURN = 3     # S2 一次 diff 最多动 3 个字段(结构上排除"整卡重生成")
MAX_DRAFT_ROLES = 3

# H2A 卡 kind(共创定稿;handler 见 make_cocreate_finalize_handler)
KIND_COCREATE_FINALIZE = "cocreate_finalize"

_TOKEN_SOURCE = "cocreation"

# ---- 确定性短语门(跳车/退出/应邀;零 token)----
_BAIL_KWS = ("就这样吧", "就这样", "直接建", "别问了", "就按这个", "开吧", "建吧",
             "就它了", "可以了", "没问题", "确认", "go ahead", "just build it", "lgtm")
_EXIT_KWS = ("退出", "算了", "不建了", "取消", "先不建", "不弄了", "quit", "cancel", "nevermind")
_ACCEPT_OFFER_KWS = ("共创", "一起", "深挖", "好啊", "好呀", "好的", "可以",
                     "走起", "来吧", "开始吧", "yes", "ok", "sure", "let's")
_DECLINE_OFFER_KWS = ("自己看", "先看看", "模板", "不用", "不要", "no", "myself")
_IMPATIENT_KWS = ("你看着办", "随便", "都行", "你定", "up to you", "whatever")

# S1 四维(目标物/节奏/口味/边界)确定性兜底问句(LLM 失败/无 gateway 时用,仍守纪律)
_FALLBACK_QUESTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("这个 agent 最核心要盯什么 / 产出什么?",
     ("每周一份行业动态摘要", "帮我整理和归档某类材料", "持续跟踪一个主题并提醒我")),
    ("多久要一次?",
     ("每天早上", "每周一", "用的时候叫它")),
    ("产出偏什么口味?",
     ("两页以内、要点式", "详细严谨、带来源", "轻松口语化")),
    ("有什么不许碰的、或上限?",
     ("不花钱、不对外发任何东西", "只读不改我的文件", "没什么限制")),
)


def _hit(text: str, kws: tuple[str, ...]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k in text or k in low for k in kws)


# ---- 会话态(粘性锚,docs/47 §3.4)----

@dataclasses.dataclass
class CocreationSession:
    """一条共创会话的全部状态(只活在内存会话态 —— S1/S2 零副作用不落盘)。"""
    conv_key: str
    state: str
    intent: str                              # 原始建 agent 意图
    rounds: int = 0                          # 已问澄清轮数(harness 硬停用)
    notes: list = dataclasses.field(default_factory=list)      # [(问, 答)] 采集记录
    pending_questions: list = dataclasses.field(default_factory=list)  # 上轮问出的问题文本
    fallback_idx: int = 0                    # 确定性兜底问句游标(四维依次问)
    draft: dict = dataclasses.field(default_factory=dict)      # 草案卡(扁平全字符串字段)
    locked: set = dataclasses.field(default_factory=set)       # 字段锁(用户敲定过的字段)
    template_id: str = ""                    # 模板短路(非空 = S5 走 instantiate)


def get_store(app: Any) -> dict:
    """会话态表(进程级,挂 app.state;key = peer+conversation id)。"""
    store = getattr(app.state, "cocreation_sessions", None)
    if store is None:
        store = {}
        app.state.cocreation_sessions = store
    return store


def _conv_key(mgr: Any) -> str:
    """粘性锚:当前 (peer, conversation)。只在私聊小卡(l0,非群)生效。"""
    if mgr is None:
        return ""
    try:
        peer = mgr.current_peer()
        conv = mgr.current()
    except Exception:
        return ""
    if peer is None or conv is None:
        return ""
    from karvyloop.karvy.capability import is_karvy_peer
    if not is_karvy_peer(getattr(peer, "domain_id", "")):
        return ""
    if getattr(peer, "role", "") == "group":
        return ""
    return f"{peer.domain_id}|{getattr(peer, 'role', '')}|{getattr(peer, 'agent_id', '')}|{conv.id}"


def is_cocreation_active(app: Any, mgr: Any) -> bool:
    """当前对话是否在共创态(粘性期间整轮进状态机,不看关键词)。"""
    key = _conv_key(mgr)
    return bool(key) and key in get_store(app)


# ---- LLM 管道(严格 JSON + token_source 打标;宁空勿毒)----

async def _llm_json(gateway: Any, model_ref: str, system: str, material: str) -> Optional[dict]:
    """一次受限 LLM 调用 → dict;任何失败 → None(调用方走确定性兜底)。"""
    if gateway is None:
        return None
    try:
        from karvyloop.gateway import ResolveScope
        from karvyloop.gateway.system import SystemPrompt
        from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
        from karvyloop.llm.token_ledger import token_source
        try:
            ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
        except Exception:
            ref = model_ref
        material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)
        out = ""
        with token_source(_TOKEN_SOURCE):
            async for ev in gateway.complete(
                [{"role": "user", "content": material}], [], ref,
                system=SystemPrompt(static=[system]),
            ):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
        return _parse_json_obj(out)
    except Exception as e:  # noqa: BLE001 — 共创任何 LLM 失败都降级,不拖垮对话
        logger.warning("[cocreation] LLM 调用失败(降级确定性兜底): %s", e)
        return None


def _parse_json_obj(text: str) -> Optional[dict]:
    """严格 JSON(只剥外层围栏;prose 不抽;解析失败 → None)。与 fuzzy_dispatch 同款纪律。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl != -1 else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    raw = raw.strip()
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


# ---- S1:澄清问句(采集协议;prompt 管问法质量,harness 管数量/停机)----

_CLARIFY_SYSTEM = """你是 KarvyLoop 全局助手"小卡"的共创澄清器。用户想建一个 agent/角色/团队,
你按四个维度收集需求:①目标物(盯什么/产出什么)②节奏(多久要一次/一次性)③口味(输出长短/严谨度/语言)④边界(不许碰什么/花钱上限)。

只输出**一个 JSON 对象**(无围栏无解释):
{"enough": true|false, "questions": [{"q": "问题", "candidates": ["候选答案1", "候选答案2"]}]}

硬纪律:
- questions **最多 2 个**;只问已有信息推不出来的、最缺的维度;
- 每问必须带 1-3 个候选答案(用户能直接点选/照抄的短句,别让他写作文);
- 已有信息够开一张草案 → enough=true 且 questions=[];
- 一次问一件事,别问卷化;严格 JSON。"""


def _clip_questions(qs: Any) -> list[dict]:
    """harness 截断:≤2 问、每问 ≤3 候选、纯字符串。"""
    out: list[dict] = []
    for q in (qs if isinstance(qs, list) else [])[:MAX_QUESTIONS_PER_ROUND]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("q", "")).strip()
        if not text:
            continue
        cands = [str(c).strip() for c in (q.get("candidates") or []) if str(c).strip()]
        out.append({"q": text[:200], "candidates": cands[:MAX_CANDIDATES_PER_QUESTION]})
    return out


def _fallback_round(sess: CocreationSession) -> list[dict]:
    """确定性兜底:按四维顺序取下 2 个没问过的(无 LLM 也守"每轮≤2 问带候选")。"""
    out: list[dict] = []
    while sess.fallback_idx < len(_FALLBACK_QUESTIONS) and len(out) < MAX_QUESTIONS_PER_ROUND:
        q, cands = _FALLBACK_QUESTIONS[sess.fallback_idx]
        out.append({"q": q, "candidates": list(cands)})
        sess.fallback_idx += 1
    return out


async def _next_questions(sess: CocreationSession, gateway: Any, model_ref: str) -> list[dict]:
    """下一轮澄清问句(LLM 优先,失败走确定性兜底);[] = 信息已够,该出草案。"""
    if sess.rounds >= MAX_CLARIFY_ROUNDS:
        return []  # 硬停:绝不第 4 轮还在问
    material = f"用户想建的 agent:{sess.intent}\n\n已采集的问答:\n" + (
        "\n".join(f"问:{q}\n答:{a}" for q, a in sess.notes) or "(还没问过)")
    obj = await _llm_json(gateway, model_ref, _CLARIFY_SYSTEM, material)
    if obj is not None:
        if obj.get("enough") is True:
            return []
        qs = _clip_questions(obj.get("questions"))
        if qs:
            return qs
        return []  # LLM 说不 enough 又给不出问题 → 别僵住,出草案
    return _fallback_round(sess)


def _render_questions(qs: list[dict], round_no: int) -> str:
    lines = [f"好,我们一起把它建出来 🦫(第 {round_no}/{MAX_CLARIFY_ROUNDS} 轮,想跳过随时说「就这样吧」)"
             if round_no == 1 else
             f"再确认一下(第 {round_no}/{MAX_CLARIFY_ROUNDS} 轮,说「就这样吧」我就直接出草案):"]
    for i, q in enumerate(qs, 1):
        lines.append(f"{i}. {q['q']}")
        if q.get("candidates"):
            lines.append("   " + " / ".join(f"「{c}」" for c in q["candidates"]))
    return "\n".join(lines)


# ---- S2:草案卡(唯一事实源;全字符串扁平字段 = 天然对接「改了再批」白名单)----

_DRAFT_SYSTEM = """你是 KarvyLoop 全局助手"小卡"的共创起草器。根据用户需求和澄清问答,起草一张建 agent 的结构化草案。

只输出**一个 JSON 对象**(无围栏无解释):
{"domain_name": "业务域名(短,像个小公司名)",
 "values": ["价值观原则(最多3条,每条一句)"],
 "forbid": ["硬禁止(最多2条)"], "oblige": ["硬要求(最多2条)"],
 "roles": [{"role_id": "ascii-kebab-id", "nickname": "中文花名", "title": "职务",
            "identity": "我是…,负责…(一句话身份)", "soul": "一两句性情/做事风格"}],
 "pursuit": "第一单任务,一句话(从用户目标直接生成)"}

硬纪律:values ≤3 条;forbid/oblige 各 ≤2;roles 1-3 个;role_id 只用小写字母/数字/连字符;
信息不够的字段给合理假设(草案会标注"我先假设…");宁少而准,别堆长文;严格 JSON。"""

_ROLE_ID_SAFE = re.compile(r"[^a-z0-9\-]+")


def _safe_role_id(raw: str, fallback: str) -> str:
    rid = _ROLE_ID_SAFE.sub("-", str(raw or "").strip().lower()).strip("-")
    return rid or fallback


def _flatten_draft(obj: dict, sess: CocreationSession) -> dict:
    """LLM 草案 JSON → 扁平全字符串草案卡(过一遍 harness 上限 + role_id 消毒)。"""
    suffix = hashlib.sha1(sess.intent.encode("utf-8")).hexdigest()[:6]
    values = [str(v).strip() for v in (obj.get("values") or []) if str(v).strip()][:3]
    forbid = [str(v).strip() for v in (obj.get("forbid") or []) if str(v).strip()][:2]
    oblige = [str(v).strip() for v in (obj.get("oblige") or []) if str(v).strip()][:2]
    draft: dict = {
        "domain_name": str(obj.get("domain_name", "")).strip()[:40] or _fallback_domain_name(sess.intent),
        "value_md": ("# 价值观\n" + "\n".join(f"- {v}" for v in values)) if values else "",
        "deontic_forbid": "\n".join(forbid),
        "deontic_oblige": "\n".join(oblige),
        "pursuit": str(obj.get("pursuit", "")).strip()[:200] or sess.intent[:200],
    }
    roles = [r for r in (obj.get("roles") or []) if isinstance(r, dict)][:MAX_DRAFT_ROLES]
    if not roles:
        roles = [{}]
    for i, r in enumerate(roles, 1):
        draft[f"role{i}_id"] = _safe_role_id(r.get("role_id", ""), f"cocreate-{suffix}-{i}")
        draft[f"role{i}_nickname"] = str(r.get("nickname", "")).strip()[:20]
        draft[f"role{i}_title"] = str(r.get("title", "")).strip()[:20]
        draft[f"role{i}_identity"] = (str(r.get("identity", "")).strip()
                                      or f"我是这个域的助手,负责:{sess.intent}")[:300]
        draft[f"role{i}_soul"] = str(r.get("soul", "")).strip()[:300]
    return draft


def _fallback_domain_name(intent: str) -> str:
    clean = re.sub(r"\s+", "", intent or "")[:10]
    return (clean + "小队") if clean else "我的新团队"


def _fallback_draft(sess: CocreationSession) -> dict:
    """确定性最小草案(LLM 失败/无 gateway;缺项交给三入口统一 default seed):
    域名从意图截取、单角色、身份=意图本身;value.md/deontic 留空(域层合法为空,
    COMMITMENT 尽责契约由 RoleRegistry.create 统一 seed,不在这里编)。"""
    return _flatten_draft({}, sess)


async def _make_draft(sess: CocreationSession, gateway: Any, model_ref: str) -> dict:
    material = f"用户想建的 agent:{sess.intent}\n\n澄清问答:\n" + (
        "\n".join(f"问:{q}\n答:{a}" for q, a in sess.notes) or "(用户跳过了澄清,合理假设并标注)")
    obj = await _llm_json(gateway, model_ref, _DRAFT_SYSTEM, material)
    if obj is None:
        return _fallback_draft(sess)
    try:
        return _flatten_draft(obj, sess)
    except Exception:  # 结构烂 → 宁空勿毒,确定性兜底
        return _fallback_draft(sess)


def render_draft_card(draft: dict, *, locked: Optional[set] = None) -> str:
    """草案卡的对话呈现(唯一事实源;改哪条说哪条 —— 只做 diff,绝不整卡重写)。"""
    locked = locked or set()
    if draft.get("template_id"):
        from karvyloop.domain.templates import get_template
        t = get_template(draft["template_id"]) or {}
        roles = "、".join(f"{r['nickname']}({r['title']})" for r in t.get("roles", []))
        return (f"你要的和现成模板{t.get('emoji', '')}「{t.get('name', draft['template_id'])}」很贴 —— "
                f"里面有:{roles}。\n直接开这个最快;回「就这样吧」我就出确认卡(你拍板才真开)。")
    def _mark(k: str) -> str:
        return "🔒" if k in locked else ""
    lines = ["【草案卡】(唯一事实源 —— 改哪条说哪条,我只做逐字段修改,绝不整卡重写)"]
    lines.append(f"· 域名{_mark('domain_name')}:{draft.get('domain_name', '')}")
    vm = draft.get("value_md", "")
    vals = [ln[2:] for ln in vm.splitlines() if ln.startswith("- ")] if vm else []
    lines.append(f"· 价值观{_mark('value_md')}:{';'.join(vals) if vals else '(暂无,可后补)'}")
    fb, ob = draft.get("deontic_forbid", ""), draft.get("deontic_oblige", "")
    lines.append(f"· 硬规矩{_mark('deontic_forbid')}:禁止 {fb or '(无)'} / 必须 {ob or '(无)'}")
    for i in range(1, MAX_DRAFT_ROLES + 1):
        if not draft.get(f"role{i}_id"):
            continue
        nick = draft.get(f"role{i}_nickname") or draft.get(f"role{i}_id")
        title = draft.get(f"role{i}_title", "")
        disp = f"{nick}({title})" if title else nick
        lines.append(f"· 角色{i}{_mark(f'role{i}_identity')}:{disp} — {draft.get(f'role{i}_identity', '')}"
                     + (f" / 性情:{draft.get(f'role{i}_soul')}" if draft.get(f"role{i}_soul") else ""))
    lines.append(f"· 第一单:{draft.get('pursuit', '')}")
    lines.append("")
    lines.append("对味就回「就这样吧」→ 我出确认卡(🤝 H2A 你拍板后才真建,卡上每个字段还能改了再批);"
                 "要改就直说改哪条;不想要了说「算了」。")
    return "\n".join(lines)


# ---- S2 diff 式修改(字段白名单 + 锁 + 每次 ≤3 字段;结构上排除整卡重生成)----

_EDIT_SYSTEM = """用户要修改一张建 agent 的草案卡。你会拿到卡上全部字段的当前值和用户的修改要求。

只输出**一个 JSON 对象**(无围栏无解释):{"set": {"字段名": "新值", ...}}

硬纪律:
- "set" 里**只许出现给你的字段名**;用户这句没提到的字段**绝不动**;
- 标了「已锁定」的字段,除非用户这次明确点名要改它,否则不要动;
- 最多改 3 个字段;改不出来/看不懂 → {"set": {}};严格 JSON。"""


def apply_draft_edits(draft: dict, locked: set, edits: dict) -> list[str]:
    """把 diff 应用到草案(白名单:只覆盖已有字段;每次 ≤3 个;改过即锁)。返回改过的字段名。"""
    changed: list[str] = []
    for k, v in (edits or {}).items():
        if len(changed) >= MAX_EDIT_FIELDS_PER_TURN:
            break
        if k not in draft or not isinstance(v, str) or not v.strip():
            continue
        if k == "value_md":
            v = v.strip()
            if v and not v.startswith("# 价值观"):
                v = "# 价值观\n" + "\n".join(
                    ln if ln.strip().startswith("-") else f"- {ln.strip()}"
                    for ln in v.splitlines() if ln.strip())
        draft[k] = v.strip()[:2000]
        locked.add(k)
        changed.append(k)
    return changed


async def _edit_draft(sess: CocreationSession, user_text: str,
                      gateway: Any, model_ref: str) -> str:
    fields = "\n".join(
        f"- {k}{'(已锁定:用户敲定过)' if k in sess.locked else ''}: {v}"
        for k, v in sess.draft.items())
    material = f"草案卡字段:\n{fields}\n\n用户的修改要求:{user_text}"
    obj = await _llm_json(gateway, model_ref, _EDIT_SYSTEM, material)
    if obj is None or not isinstance(obj.get("set"), dict):
        return ("这条我没解析成对某个字段的修改(宁可不动,不猜着改)。"
                "可以指着说,比如「性情改成:先说风险」「域名叫XX」;"
                "或者回「就这样吧」出确认卡,在卡上逐字段改了再批。")
    changed = apply_draft_edits(sess.draft, sess.locked, obj["set"])
    if not changed:
        return "没动任何字段(要么没对上卡上的字段,要么该字段你已敲定)。再指明确一点?"
    return "改好了(这些字段已锁定🔒,我不会再动):" + "、".join(changed) + "\n\n" + \
        render_draft_card(sess.draft, locked=sess.locked)


# ---- 模板短路(S1 入口:目标贴近现成模板 → 直接推荐,共创对模板党是加速器)----

def _match_template(intent: str) -> str:
    """确定性模板匹配(全名命中;无向量无 LLM)。返回 template_id 或 ''。"""
    try:
        from karvyloop.domain.templates import TEMPLATES
        for t in TEMPLATES:
            if t["name"] in (intent or ""):
                return t["id"]
    except Exception:
        pass
    return ""


# ---- S5:定稿 → H2A 确认卡(ACCEPT 才真建;payload 全字符串 = 卡上可改了再批)----

def proposal_for_cocreate_finalize(*, draft: dict, ts: float, strength: float = 0.85):
    """共创定稿卡。payload = 草案卡全字符串字段(H2A「改了再批」apply_payload_edits
    只认已有 str 键 —— 这就是天然的字段级终改 + 白名单)。"""
    from karvyloop.karvy.atoms import Proposal
    payload = {k: str(v) for k, v in (draft or {}).items()}
    if payload.get("template_id"):
        from karvyloop.domain.templates import get_template
        t = get_template(payload["template_id"]) or {}
        name = t.get("name", payload["template_id"])
        summary = f"共创定稿:一键开出模板域「{name}」"
        basis = (f"共创会话里你选定了现成模板「{name}」。ACCEPT = 走既有 instantiate 路径"
                 f"真开出该域和配好灵魂的角色(幂等:同名活跃域已存在会被拒并如实说)。")
    else:
        name = payload.get("domain_name", "")
        n_roles = sum(1 for i in range(1, MAX_DRAFT_ROLES + 1) if payload.get(f"role{i}_id"))
        summary = f"共创定稿:建业务域「{name}」+ {n_roles} 个角色"
        basis = ("这是共创会话的最终草案(S1/S2 期间没写过任何东西 —— 零副作用)。"
                 "ACCEPT 才真建:角色走 RoleRegistry.create(尽责契约 COMMITMENT 统一 seed,"
                 "与系统默认/导入同一份),域落 value.md + deontic 真护栏。"
                 "卡上任何字段不对,可直接改了再批。")
    stable = "|".join(f"{k}={payload.get(k, '')}" for k in sorted(payload))
    pid = KIND_COCREATE_FINALIZE + "-0-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=summary,
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_COCREATE_FINALIZE,
        payload=payload,
        proposal_id=pid,
        basis=basis,
    )


def validate_draft(draft: dict) -> list[str]:
    """草案卡出卡前校验(对齐真实 registry 的约束,别把注定被拒的卡递给人)。返回问题列表。"""
    problems: list[str] = []
    if draft.get("template_id"):
        from karvyloop.domain.templates import get_template
        if get_template(draft["template_id"]) is None:
            problems.append(f"未知模板:{draft['template_id']}")
        return problems
    if not (draft.get("domain_name") or "").strip():
        problems.append("缺域名")
    vm = (draft.get("value_md") or "").strip()
    if vm and not vm.startswith("# 价值观"):
        problems.append("value_md 非空时必须以「# 价值观」开头(域规范)")
    from karvyloop.roles.registry import _ROLE_ID_RE
    n = 0
    for i in range(1, MAX_DRAFT_ROLES + 1):
        rid = (draft.get(f"role{i}_id") or "").strip()
        if not rid:
            continue
        n += 1
        if not _ROLE_ID_RE.match(rid):
            problems.append(f"role{i}_id「{rid}」不合法(只能含字母/数字/下划线/连字符)")
    if n == 0:
        problems.append("草案里没有任何角色")
    for k, v in draft.items():
        if not isinstance(v, str):
            problems.append(f"字段 {k} 不是字符串(卡上改了再批只认字符串字段)")
    return problems


def finalize_custom_draft(payload: dict, *, domain_registry: Any, role_registry: Any,
                          domain_store: Any = None, created_by: str = "user:console") -> dict:
    """自建路径落地(镜像 instantiate_template 的流程与幂等语义):
    建角色(已存在则复用;COMMITMENT 由 RoleRegistry.create 统一 seed)→ 建域
    (value.md + deontic 真护栏 + 成员)→ 持久化。同名活跃域 → 拒(明确说)。"""
    if domain_registry is None or role_registry is None:
        return {"ok": False, "reason": "未接 role/domain registry"}
    name = (payload.get("domain_name") or "").strip()
    problems = validate_draft(dict(payload))
    if problems:
        return {"ok": False, "reason": "草案不合法:" + ";".join(problems)}
    for d in domain_registry.list_active():
        if getattr(d, "name", "") == name:
            return {"ok": False, "reason": f"已有同名业务域「{name}」(不重复开;可先归档旧的)"}

    roles: list[dict] = []
    for i in range(1, MAX_DRAFT_ROLES + 1):
        rid = (payload.get(f"role{i}_id") or "").strip()
        if not rid:
            continue
        roles.append({
            "role_id": rid,
            "nickname": (payload.get(f"role{i}_nickname") or "").strip(),
            "title": (payload.get(f"role{i}_title") or "").strip(),
            "identity": (payload.get(f"role{i}_identity") or "").strip()
                        or f"我是「{rid}」,在「{name}」里干活。",   # 缺项 default seed
            "soul": (payload.get(f"role{i}_soul") or "").strip(),
        })

    created, reused = [], []
    for r in roles:
        try:
            existing = None
            try:
                existing = role_registry.get(r["role_id"])
            except Exception:
                existing = None
            if existing is not None:
                reused.append(r["role_id"])
                continue
            # COMMITMENT(尽责下属契约)由 create 统一 seed —— 三入口同一份,共创不是第四入口
            role_registry.create(r["role_id"], identity=r["identity"], soul=r["soul"],
                                 nickname=r["nickname"], title=r["title"])
            created.append(r["role_id"])
        except Exception as e:
            logger.warning("[cocreation] 建角色 %s 失败: %s", r["role_id"], e)
            return {"ok": False, "reason": f"建角色 {r['role_id']} 失败:{e}"}

    from karvyloop.domain.deontic import Deontic
    forbid = tuple(x.strip() for x in (payload.get("deontic_forbid") or "").splitlines() if x.strip())
    oblige = tuple(x.strip() for x in (payload.get("deontic_oblige") or "").splitlines() if x.strip())
    member_query = " AND ".join([created_by] + [f"agent:{r['role_id']}" for r in roles])
    try:
        domain = domain_registry.create(
            name=name, created_by=created_by,
            value_md_raw=(payload.get("value_md") or ""),
            deontic=Deontic(forbid=forbid, oblige=oblige),
            member_query=member_query)
    except Exception as e:
        return {"ok": False, "reason": f"建域失败:{e}"}
    if domain_store is not None:
        try:
            domain_store.save_all(domain_registry.list_active())
        except Exception as e:
            logger.warning("[cocreation] 域持久化失败(域已在内存): %s", e)
    return {"ok": True, "domain_id": domain.id, "domain_name": name,
            "roles_created": created, "roles_reused": reused,
            "pursuit": (payload.get("pursuit") or "").strip(), "reason": ""}


def make_cocreate_finalize_handler(app: Any):
    """ACCEPT 兑现 handler(注入 proposal_handlers 表,kind=cocreate_finalize)。

    payload 带 template_id → 走既有 instantiate_template(模板短路);
    否则 finalize_custom_draft(RoleRegistry.create + DomainRegistry.create)。
    """
    def _handler(proposal) -> tuple[bool, str]:
        payload = dict(getattr(proposal, "payload", {}) or {})
        st = getattr(app, "state", None)
        dom_reg = getattr(st, "domain_registry", None)
        role_reg = getattr(st, "role_registry", None)
        dom_store = getattr(st, "domain_store", None)
        tid = (payload.get("template_id") or "").strip()
        if tid:
            from karvyloop.domain.templates import instantiate_template
            res = instantiate_template(tid, domain_registry=dom_reg, role_registry=role_reg,
                                       domain_store=dom_store)
        else:
            res = finalize_custom_draft(payload, domain_registry=dom_reg, role_registry=role_reg,
                                        domain_store=dom_store)
        if not res.get("ok"):
            return False, res.get("reason", "落地失败")
        detail = (f"已开出业务域「{res['domain_name']}」"
                  f"(新建角色:{','.join(res.get('roles_created', [])) or '无'};"
                  f"复用:{','.join(res.get('roles_reused', [])) or '无'})。")
        if res.get("pursuit"):
            detail += f" 第一单可以直接丢给它:「{res['pursuit']}」。"
        return True, detail
    return _handler


# ---- 状态机主入口(ws/routes 的两条接缝)----

async def _issue_finalize_card(app: Any, sess: CocreationSession) -> str:
    """S5:出 H2A 确认卡 + 清态(卡是唯一落地口;S1/S2 零副作用到此为止)。"""
    problems = validate_draft(sess.draft)
    if problems:
        # 卡会被 registry 拒的草案不递给人 —— 说清缺什么,留在 S2 修
        sess.state = STATE_DRAFT
        return "草案还差一点,出不了确认卡:" + ";".join(problems) + "\n直接说怎么改,或「算了」退出。"
    registry = getattr(app.state, "proposal_registry", None)
    if registry is None:
        return "这台 console 没接提案登记表(proposal_registry),定稿卡出不来 —— 先跟主线说一声。"
    proposal = proposal_for_cocreate_finalize(draft=sess.draft, ts=time.time())
    registry.register(proposal)
    try:
        from karvyloop.console.proposals import broadcast_proposal
        await broadcast_proposal(app, proposal)
    except Exception:
        pass
    get_store(app).pop(sess.conv_key, None)   # 定稿 = 共创会话收口(清粘性态)
    name = sess.draft.get("domain_name") or sess.draft.get("template_id", "")
    return (f"好,定稿了 ✅ 确认卡已发到 🤝 H2A:「{name}」。"
            "你 ACCEPT 才真建(卡上每个字段还能改了再批);REJECT 就当没提过。")


async def _enter_clarify_or_shortcut(app: Any, sess: CocreationSession,
                                     gateway: Any, model_ref: str) -> str:
    """S0→S1 入口:贴现成模板 → 直接短路到模板确认;否则开始四维澄清。"""
    tid = _match_template(sess.intent)
    if tid:
        sess.template_id = tid
        sess.draft = {"template_id": tid}
        sess.state = STATE_DRAFT
        return render_draft_card(sess.draft)
    sess.state = STATE_CLARIFY
    qs = await _next_questions(sess, gateway, model_ref)
    if not qs:  # 一问都不用问(意图已经够具体)→ 直接草案
        return await _to_draft(sess, gateway, model_ref)
    sess.rounds += 1
    sess.pending_questions = [q["q"] for q in qs]
    return _render_questions(qs, sess.rounds)


async def _to_draft(sess: CocreationSession, gateway: Any, model_ref: str) -> str:
    sess.draft = await _make_draft(sess, gateway, model_ref)
    sess.state = STATE_DRAFT
    prefix = ""
    if sess.notes:
        picked = ";".join(a for _, a in sess.notes if a)[:200]
        prefix = f"所以你要的是:{sess.intent}({picked})—— 按这个我起了张草案:\n\n"
    else:
        prefix = "我先按合理假设起了张草案(不对的直接改):\n\n"
    return prefix + render_draft_card(sess.draft, locked=sess.locked)


async def cocreation_take_turn(app: Any, mgr: Any, intent: str, *,
                               gateway: Any = None, model_ref: str = "") -> Optional[str]:
    """会话粘性门(docs/47 ④):当前对话在共创态 → 整轮进状态机,返回小卡的回复文本;
    未激活 / 应退出回普通聊天 → None(调用方走正常 drive)。**每轮都必须被 record_turn**
    (调用方负责,防 ctx 串台)。"""
    key = _conv_key(mgr)
    if not key:
        return None
    store = get_store(app)
    sess: Optional[CocreationSession] = store.get(key)
    if sess is None:
        return None

    text = (intent or "").strip()

    # 退出口(任何状态):清态,回普通聊天
    if _hit(text, _EXIT_KWS):
        store.pop(key, None)
        return "好,先不建了 🦫 想起来随时说一声,我们接着聊别的。"

    if sess.state == STATE_OFFERED:
        if _hit(text, _DECLINE_OFFER_KWS) and not _hit(text, _ACCEPT_OFFER_KWS):
            store.pop(key, None)
            return None          # 用户要自己看/换话题 → 摘态,走正常 drive
        if _hit(text, _BAIL_KWS):
            # 递口后直接"就这样吧" = 跳车:贴模板 → 模板卡;否则按现有信息成草案 → 直接出卡
            tid = _match_template(sess.intent)
            if tid:
                sess.template_id, sess.draft, sess.state = tid, {"template_id": tid}, STATE_DRAFT
            else:
                await _to_draft(sess, gateway, model_ref)
            return await _issue_finalize_card(app, sess)
        if _hit(text, _ACCEPT_OFFER_KWS):
            return await _enter_clarify_or_shortcut(app, sess, gateway, model_ref)
        store.pop(key, None)     # 既没应邀也没拒绝 = 换话题 → 摘态不纠缠
        return None

    if sess.state == STATE_CLARIFY:
        # 记录这轮回答(哪怕是"你看着办")—— 采集产物喂草案
        if sess.pending_questions:
            sess.notes.append(("; ".join(sess.pending_questions), text))
            sess.pending_questions = []
        if _hit(text, _BAIL_KWS):
            # 跳车:当前信息直接成草案并出卡(缺项 default seed)
            await _to_draft(sess, gateway, model_ref)
            return await _issue_finalize_card(app, sess)
        if _hit(text, _IMPATIENT_KWS):
            return await _to_draft(sess, gateway, model_ref)   # 不耐烦 → 立即出草案(留人审)
        if sess.rounds >= MAX_CLARIFY_ROUNDS:
            return await _to_draft(sess, gateway, model_ref)   # 硬停:3 轮到顶
        qs = await _next_questions(sess, gateway, model_ref)
        if not qs:
            return await _to_draft(sess, gateway, model_ref)
        sess.rounds += 1
        sess.pending_questions = [q["q"] for q in qs]
        return _render_questions(qs, sess.rounds)

    if sess.state == STATE_DRAFT:
        if _hit(text, _BAIL_KWS):
            return await _issue_finalize_card(app, sess)
        return await _edit_draft(sess, text, gateway, model_ref)

    # 未知状态(S3/S4 留位):防御性清态
    store.pop(key, None)
    return None


async def maybe_offer_cocreation(app: Any, mgr: Any, intent: str, *,
                                 gateway: Any = None, model_ref: str = "") -> str:
    """共创递口(docs/47 §3.1):建 agent 意图命中(L0 关键词门 / L1 LLM build 分类)且
    当前无共创会话 → 挂 OFFERED 态并返回递口文案(调用方贴在本轮回复末尾);
    否则返 ''。**递口本身零副作用**(只写会话态)。"""
    key = _conv_key(mgr)
    if not key:
        return ""
    store = get_store(app)
    if key in store:
        return ""
    hit = False
    try:
        from karvyloop.karvy.self_knowledge import wants_build_guidance
        hit = wants_build_guidance(intent)          # L0:关键词门(零 token)
    except Exception:
        hit = False
    if not hit and gateway is not None:
        try:
            from karvyloop.karvy.fuzzy_dispatch import (
                classify_build_intent, looks_like_capability_wish)
            if looks_like_capability_wish(intent):  # L0.5 意愿词启发,才烧 L1 分类
                from karvyloop.llm.token_ledger import token_source
                with token_source(_TOKEN_SOURCE):
                    hit = await classify_build_intent(intent, gateway=gateway,
                                                      model_ref=model_ref)
        except Exception:
            hit = False
    if not hit:
        return ""
    store[key] = CocreationSession(conv_key=key, state=STATE_OFFERED, intent=(intent or "").strip())
    return ("——\n这事我可以陪你一起深挖着建:【一起共创】(我问你几个小问题,起草案,你拍板才真建)"
            "还是【你先自己看看模板】?回「共创」开始,回「就这样吧」我直接按现有信息建。")


__all__ = [
    "KIND_COCREATE_FINALIZE",
    "MAX_CLARIFY_ROUNDS", "MAX_QUESTIONS_PER_ROUND", "MAX_EDIT_FIELDS_PER_TURN",
    "STATE_OFFERED", "STATE_CLARIFY", "STATE_DRAFT", "STATE_TRIAL", "STATE_REVIEW",
    "CocreationSession", "get_store", "is_cocreation_active",
    "cocreation_take_turn", "maybe_offer_cocreation",
    "render_draft_card", "apply_draft_edits", "validate_draft",
    "proposal_for_cocreate_finalize", "finalize_custom_draft",
    "make_cocreate_finalize_handler",
]
