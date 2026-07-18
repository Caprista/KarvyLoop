"""pursuit_triage — 聊天里的跨天持久目标判型(docs/88 §7 第二刀:小卡自动判型 create)。

**定位**:用户在私聊小卡时说出一个**跨多天的持久目标**("帮我把 X 一直推进到 Y 为止"
"这周之内把测试全修绿")→ 识别 → LLM 派生确定性完成判据(verify_gate)→ 升
KIND_PURSUIT_COMMIT 承诺卡。**人 ACCEPT 才算承诺成立**(H2A 铁律:识别成功也绝不自动
committed);REJECT → 判型建的记录清掉,不留垃圾。

**两级识别(宁漏勿滥 —— 漏了用户还能走 API/面板,滥了是打扰)**:
1. `looks_like_pursuit`:**确定性粗筛,零 token** —— 必须同时有"持续/期限"信号 +
   "指令式目标"信号,且不是问句。单次任务/闲聊/含糊感慨绝不过筛。
2. 粗筛过了才烧**一次** LLM 判型(`derive_pursuit`,走网关咽喉 + token_source 打标):
   判"真是跨天目标吗" + 派生 gate(第一刀只 test_pass / file_exists)。

**宁空勿毒**:严格 JSON(只剥外层围栏);解析失败 / 字段不对 / gate 类型不在白名单 /
test_pass 命令拆不出可执行 argv → 放弃判型返 None,正常聊天路径继续 —— **绝不带半坏
数据创建**。创建走第一刀唯一 create 路径(routes_pursuit 的 helper),不另造第二套。

接线:console/routes.maybe_route_to_role(与圆桌/委派识别同一模式:早返回由调用方
record_turn,防 ctx 串台的血教训)。
"""
from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 判型建的记录在承诺卡 payload 里带这个来源标:REJECT 时按它清记录(显式 API/面板建的
# 不受影响 —— 那是用户亲手建的,保留"可稍后手动承诺"的第一刀语义)。
ORIGIN_KARVY_TRIAGE = "karvy_triage"

_ALLOWED_GATE_TYPES = ("test_pass", "file_exists")
_MAX_STATEMENT = 2000
_MAX_TITLE = 80
_MAX_TRIGGERS = 3
_TOKEN_SOURCE = "pursuit_triage"

# ---- 粗筛信号(确定性,零 token)----
# A:持续/期限信号 —— 这句话里有"跨着时间一直做/到某期限做完"的形状。
_PERSIST_KWS: tuple[str, ...] = (
    "一直", "直到", "为止", "推进到", "持续推进", "跨天", "跨几天",
    "这周之内", "这周内", "本周内", "这个月内", "本月内", "月底前", "周末前",
    "这几天", "接下来几天", "几天内", "长期目标",
    "until", "by the end of", "by friday", "keep pushing", "over the next few days",
)
# B:指令式目标信号 —— 这句话是"要把一件事做到某个状态"的委托,不是感慨/评论。
_GOAL_KWS: tuple[str, ...] = (
    "帮我", "把", "推进", "修好", "修绿", "修复", "搞定", "完成", "做到", "做完",
    "达到", "实现", "跑通", "写完", "全绿", "全部通过",
    "fix", "finish", "get ", "make ", "land ", "ship ",
)
# 否决:问句/征询不是委托(问"能不能/怎么做"≠"去做")。宁漏勿滥。
_QUESTION_KWS: tuple[str, ...] = ("吗", "怎么", "如何", "为什么", "什么", "why ", "how ", "?", "?")
_MIN_LEN = 8


def looks_like_pursuit(intent: str) -> bool:
    """像不像"跨多天持久目标"(确定性粗筛,零 token)。

    必须**同时**满足:①持续/期限信号 ②指令式目标信号 ③不是问句 ④不太短。
    保守是设计:过筛只是"值得烧一次 LLM 判型",真伪由 LLM 定;没过筛 = 正常聊天,
    用户永远还有 API / 面板的显式入口。
    """
    s = (intent or "").strip()
    if len(s) < _MIN_LEN:
        return False
    low = s.lower()
    if any(k in s or k in low for k in _QUESTION_KWS):
        return False
    if not any(k in s or k in low for k in _PERSIST_KWS):
        return False
    return any(k in s or k in low for k in _GOAL_KWS)


# ---- LLM 判型(粗筛过了才走到这;一次调用)----

TRIAGE_SYSTEM = """你是个人 Agent 运行时里的"跨天目标判型器"。用户对助手说了一句话,粗筛认为它
**可能**是一个"要跨多天持续推进、直到某个完成状态"的持久目标(Pursuit)。你来终判,并派生
**确定性完成判据**(gate:机器每次推进后用它判断"算完了吗",绝不再问模型)。

只输出**一个 JSON 对象**(不要解释、不要 markdown 围栏):
{
  "is_pursuit": true/false,
  "title": "短标题(≤30字)",
  "statement": "目标一句话(可直接当承诺陈述)",
  "gate": {"type": "test_pass", "cmd": "运行的命令,退出码 0 = 完成"}
       或 {"type": "file_exists", "path": "产出文件路径,存在 = 完成"},
  "revision_triggers": []
}

判定规则(宁漏勿滥):
- **is_pursuit=true 的门槛很高**:必须是"跨多天、需要机器反复推进、有明确完成状态"的目标
  (如"把某目录测试全修绿""持续重构直到某命令通过""把某份产出写出来")。
- 以下一律 is_pursuit=false:一次就能做完的任务、闲聊/感慨/提问、目标含糊没有可判定的完成状态
  ("变得更好""多关注竞品")、纯定期例行(那是定时任务不是目标)。
- gate 必须**确定性可判**:
  - 目标是"测试过/命令通过" → test_pass,cmd 填**具体可运行**的一条命令(如
    "python -m pytest tests/foo -x -q");用户没给出足够信息拼出具体命令 → 不要编,判 false。
  - 目标是"产出某文件" → file_exists,path 填具体路径(可相对工作区根目录)。
  - 两种都派生不出 → is_pursuit=false(没有确定性判据的目标第一刀不收)。
- revision_triggers 最多 3 条,没有就 []。
- 严格 JSON,无围栏无尾随文本。"""


@dataclasses.dataclass(frozen=True)
class PursuitDraft:
    """判型产物:一个可直接走第一刀 create 路径的结构化 Pursuit 草案。"""
    statement: str
    gate: dict
    title: str = ""
    revision_triggers: tuple = ()


def parse_pursuit_draft(text: str, *, intent: str = "") -> Optional[PursuitDraft]:
    """宁空勿毒:严格 JSON 解 LLM 判型输出 → PursuitDraft;任何不对 → None(放弃判型)。

    - 只剥**外层** fence;不是以 { 开头的一律不收(prose 不抽)。
    - is_pursuit 必须是字面 true;gate 类型必须在 {test_pass, file_exists}。
    - test_pass 的 cmd 必须过 split_test_pass_cmd(和 gate 求值同一口径)—— 拆不出可执行
      argv → 放弃(绝不让"永红 gate"进库)。
    """
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
    if not isinstance(obj, dict) or obj.get("is_pursuit") is not True:
        return None
    gate_in = obj.get("gate")
    if not isinstance(gate_in, dict):
        return None
    gtype = str(gate_in.get("type") or "").strip()
    if gtype not in _ALLOWED_GATE_TYPES:
        return None
    gate: dict
    if gtype == "test_pass":
        cmd = str(gate_in.get("cmd") or "").strip()
        if not cmd:
            return None
        from karvyloop.cognition.pursuit import split_test_pass_cmd
        try:
            argv = split_test_pass_cmd(cmd)
        except ValueError:
            argv = []
        if not argv or not str(argv[0]).strip():
            return None   # 拆不出可执行命令 → 放弃(create 侧同口径校验兜底)
        gate = {"type": "test_pass", "cmd": cmd}
        cwd = str(gate_in.get("cwd") or "").strip()
        if cwd:
            gate["cwd"] = cwd
    else:
        path = str(gate_in.get("path") or "").strip()
        if not path:
            return None
        gate = {"type": "file_exists", "path": path}
    statement = str(obj.get("statement") or "").strip()[:_MAX_STATEMENT] or (intent or "").strip()[:_MAX_STATEMENT]
    if not statement:
        return None
    title = str(obj.get("title") or "").strip()[:_MAX_TITLE]
    trigs = tuple(str(t).strip() for t in (obj.get("revision_triggers") or [])
                  if str(t).strip())[:_MAX_TRIGGERS]
    return PursuitDraft(statement=statement, gate=gate, title=title, revision_triggers=trigs)


async def derive_pursuit(intent: str, *, gateway: Any, model_ref: str = "",
                         workspace_root: str = "") -> Optional[PursuitDraft]:
    """一次受限 LLM 判型 → PursuitDraft;无 gateway / 解析失败 → None(正常聊天继续)。

    走网关咽喉(gateway.complete 自动入 token 账),本函数内打 token_source 标
    (记账只在网关,别绕)。
    """
    if gateway is None or not (intent or "").strip():
        return None
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    material = f"用户说:{intent}\n\n工作区根目录:{workspace_root or '(未知)'}"
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)
    out = ""
    with token_source(_TOKEN_SOURCE):
        async for ev in gateway.complete(
            [{"role": "user", "content": material}], [], ref,
            system=SystemPrompt(static=[TRIAGE_SYSTEM]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    return parse_pursuit_draft(out, intent=intent)


_DUP_STMT_RATIO = 0.7   # statement 词面重合达此值视为同一目标(常数待 Trace 真数据标定)


def _dup_of_active(store: Any, draft: "PursuitDraft") -> Optional[Any]:
    """判型草案是否与某条活跃追求同款。两重确定性判定(项目一贯,不上向量):
    ① verify_gate 完全相等(同一条测试命令/文件路径 = 铁定同款,措辞再不同也算);
    ② statement 词面重合率 ≥ _DUP_STMT_RATIO(latin token + CJK bigram,按较短句归一,
       双向取大 —— 防"同目标短句 vs 长句"漏判)。任何异常按不重复处理(宁建勿吞真需求)。"""
    from karvyloop.context.relevance import _cjk_bigrams, _latin_tokens, overlap_score

    def _ntok(s: str) -> int:
        return len(_latin_tokens((s or "").lower())) + len(_cjk_bigrams(s or ""))

    try:
        for rec in store.active():
            gate = getattr(rec.pursuit, "verify_gate", None)
            if gate and dict(draft.gate) == dict(gate):
                return rec
            a, b = draft.statement or "", getattr(rec.pursuit, "statement", "") or ""
            m = min(_ntok(a), _ntok(b))
            if m and max(overlap_score(a, b), overlap_score(b, a)) / m >= _DUP_STMT_RATIO:
                return rec
    except Exception:  # noqa: BLE001
        return None
    return None


async def maybe_pursuit_triage(app: Any, intent: str) -> Optional[dict]:
    """私聊小卡的一句话 → 跨天目标判型 → 建 Pursuit + 升承诺卡;不命中/判不出 → None。

    调用方(maybe_route_to_role)拿到非 None 早返回时,由**既有调用方代码** record_turn
    这一轮(与圆桌提案同一模式 —— 早返回不记 = ctx 串台的血教训)。

    顺序有讲究:粗筛(零成本)→ store 在不在(--no-llm 不判型)→ gateway 在不在 →
    才烧一次 LLM。任何一环不满足都安静返 None,drive 主流程零感知。
    """
    if not looks_like_pursuit(intent):
        return None
    store = getattr(getattr(app, "state", None), "pursuit_store", None)
    if store is None:
        return None   # pursuit 未接线 → 不判型(诚实降级,不装)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return None
    try:
        draft = await derive_pursuit(intent, gateway=gw, model_ref=rk.get("model_ref", ""),
                                     workspace_root=rk.get("workspace_root", ""))
    except Exception as e:  # noqa: BLE001 — 判型任何异常都降级正常聊天,不让 drive 崩
        logger.warning(f"[pursuit_triage] 判型失败,降级正常聊天: {e}")
        return None
    if draft is None:
        return None

    # 去重闸(对抗验收 P2):同句/近似句重复触发 → 不建第二条(多卡打扰 + committed 后
    # 同 gate 每 tick 双跑)。放在判型后:LLM 归一化过的 statement 才可比;重复本身稀有,
    # 多烧的这一次判型换精度值得。gate 相等 + 词面 overlap 两重确定性判定,不上向量。
    dup = _dup_of_active(store, draft)
    if dup is not None:
        from karvyloop import i18n
        stmt = (getattr(dup.pursuit, "statement", "") or "")[:80]
        return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False,
                "crystallized": False, "skill_name": "", "routed": True,
                "text": i18n.t("pursuit.triage.duplicate", statement=stmt)}

    # 复用第一刀唯一 create 路径(创建 + 升承诺卡;绝不另造第二套)。
    from karvyloop.console.routes_pursuit import create_pursuit_with_commit_card
    try:
        res = await create_pursuit_with_commit_card(
            app, statement=draft.statement, verify_gate=dict(draft.gate),
            title=draft.title, revision_triggers=list(draft.revision_triggers),
            origin=ORIGIN_KARVY_TRIAGE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[pursuit_triage] create 失败,降级正常聊天: {e}")
        return None
    if not res.get("ok"):
        return None   # gate 校验没过等 → 放弃判型(宁空勿毒),正常聊天继续
    if not res.get("commit_proposal_id"):
        # 建了记录但承诺卡没升起来 → 聊天路径不能留"没人拍板"的暗记录(H2A 铁律)。
        # 回滚删除,放弃判型(显式 API 建的才有"稍后手动承诺"语义)。
        pid = str(res.get("pursuit_id") or "")
        if pid:
            try:
                store.remove(pid)
            except Exception:
                pass
        return None

    from karvyloop import i18n
    from karvyloop.console.pursuit_tick import PURSUIT_MAX_ADVANCES
    text = i18n.t("pursuit.triage.card_text",
                  statement=draft.statement[:120],
                  gate=res.get("gate_desc", ""),
                  max_rounds=PURSUIT_MAX_ADVANCES)
    return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": True, "text": text}


__all__ = ["looks_like_pursuit", "parse_pursuit_draft", "derive_pursuit",
           "maybe_pursuit_triage", "PursuitDraft", "TRIAGE_SYSTEM", "ORIGIN_KARVY_TRIAGE"]
