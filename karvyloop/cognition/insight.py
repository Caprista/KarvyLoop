"""cognition/insight — task_insight 非任务认知沉淀(docs/80 #5 → docs/82 落地)。

**缺口**:现有四条沉淀线全绑在"任务干得好不好/人怎么拍板/人说了什么/角色在域里学到什么",
漏的轴 = **执行本身顺带揭示的环境/世界事实**:环境事实(「这台机器 pip 要走镜像」)、
纠错经验(被"纯失败不沉"+"≥K 样本才蒸"双漏)、顺带观察(「客户邮件都周五发」——
auto_distill 只读对话轮从不读 Trace)。

**结构镜像 roles/experience.py**(同一套纪律,不另起炉灶):
- 门1(零 LLM 信号门)`find_insight_signals`:只认三种**确定性执行模式**——
  ① 纠错模式:同名工具 `ok=False → 之后 ok=True`(slice C 事实字段,确定性;
  老数据无 ok 字段回退旧推断"同名 ≥2 次且 input 变 + 最终成功")② terminal 非
  COMPLETED 且同任务后续 run 成功(replan 恢复)③ task_run error→done。
  平静日子零信号 → 零 LLM。
- 解析 `parse_insights` **宁空勿毒升到指称层**:严格 JSON 失败返 [];**evidence_ref
  必须核回本批真实 trace_ref,核不上整条丢**(编造证据 = 整条不要)。
- 门2(复现关)`build_insight_beliefs`:硬证据候选(失败→成功配对背书)首见即写
  provisional;软观察候选须跨 ≥2 run 词面背书(`overlap_score` 零 LLM 计数,
  复刻 decision_pref 确定性复现地板)才写。
- 载体 = Belief(复用认知库):`provenance.source == "task_insight"`、`provisional=True`
  (provenance_rank 排 auto 档,永掀不翻 user_explicit/decision_pref);env 类带
  `applies={"device"}`(环境事实是这台机器的,不冒充普适真理)。
- **无向量**(铁律):复现背书用 overlap_score 词面+CJK bigram。
- **防重叠**:prompt 硬禁任务评语(技能线)/决策规则(decision_pref)/一次性细节/
  开放问题;带 (domain,role) 的候选直接丢(role_experience 地盘)。
"""
from __future__ import annotations

import json
import logging
import platform
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from karvyloop.schemas.cognition import Belief

logger = logging.getLogger(__name__)

# Belief.provenance.source 标记(召回/展示/rank 据此筛出执行洞察,与其它沉淀线区分)
TASK_INSIGHT_SOURCE = "task_insight"
_KINDS = ("env", "correction", "observation")   # 环境事实 / 纠错经验 / 顺带观察

# 单条洞察封顶(挡病态长文;与 roles/experience 一致)
_MAX_INSIGHT_LEN = 300
# 单批 LLM 候选封顶(prompt 里也说了 ≤5;parse 再守一层,双关)
MAX_CANDIDATES = 5
# 复现关(门2)软观察地板:须在 ≥SOFT_MIN_RUNS 个不同 run 的材料里有词面背书
# (每个 run 至少 SOFT_MIN_OVERLAP 个词/bigram 命中才算背书 —— 单个撞词太弱,不算)
SOFT_MIN_RUNS = 2
SOFT_MIN_OVERLAP = 2

# 洞察消费的 Trace 事件面(捞料纪律:不读 eval_fact/satisfaction/lesson ——
# 任务质量轴归技能线,不双记账)
HARVEST_KINDS = ("atom_run", "error", "task_run")


# ---- 门1:零 LLM 确定性信号门 ----


@dataclass
class InsightSignal:
    """一个值得看一眼的执行片段(确定性模式命中;零 LLM 产出)。"""
    pattern: str                     # tool_retry | replan_recovery | task_recovery
    task_id: str                     # 命中的 Trace task
    trace_ref: str                   # 主证据 ref("task_id:seq",冷却/溯源键)
    refs: tuple = ()                 # 全部证据 ref(evidence_ref 核对面)
    material: str = ""               # 确定性拼好的材料(喂洞察编译器)
    hard: bool = True                # 硬证据 = 有失败→成功配对背书(门2 首见即写)
    ts: float = 0.0


def _entry_ref(e: Any) -> str:
    """TraceEntry 的统一证据 ref(三种事件同一口径,与 TraceStore.append 返回值同式)。"""
    return f"{getattr(e, 'task_id', '')}:{getattr(e, 'seq', 0)}"


def _short(v: Any, n: int) -> str:
    """紧凑截断(输入 dict → JSON;别的 → str)。材料是喂 LLM 的,短即是德。"""
    try:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        s = str(v)
    s = (s or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _freeze_input(v: Any) -> str:
    """工具 input 的稳定指纹("input 变了吗"判定;dict 键序不稳 → sort_keys)。"""
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(v)


def _entry_text(e: Any) -> str:
    """一条执行事件的确定性文本投影(软观察复现背书的比对面;零 LLM)。"""
    p = getattr(e, "payload", None) or {}
    k = getattr(e, "kind", "")
    if k == "atom_run":
        parts = [str(p.get("atom_id", ""))]
        for c in (p.get("tool_calls") or [])[:8]:
            if isinstance(c, dict):
                parts.append(f"{c.get('name', '')} {_short(c.get('input'), 120)}")
        parts.append(_short(p.get("output"), 300))
        parts.append(str(p.get("terminal", "")))
        return " ".join(x for x in parts if x)
    if k == "task_run":
        return " ".join(str(p.get(f, "") or "") for f in ("intent", "result", "who", "status"))
    if k == "error":
        return " ".join(str(p.get(f, "") or "") for f in ("error_type", "error", "stage"))
    return ""


def _tool_retry_signal(e: Any) -> Optional[InsightSignal]:
    """①纠错模式(每个 run 至多一条)。两档判定,按 tool_calls 有没有 ok 事实字段分流:

    - **确定性**(slice C 之后的新数据,tool_calls 条目带 ok/error_reason):同名工具
      `ok=False → 之后 ok=True` = 失败→成功配对**本身就是硬证据**,不再要求 input 变
      或整次 run 成功(工具级纠错已闭环,不是"纯失败";失败真因 error_reason 并进材料)。
    - **回退推断**(老 Trace 数据无 ok 字段,标注保留):同名 ≥2 次、input 变、
      整次 run 最终成功 —— 旧行为原样,加性兼容。
      **确定性档只在同名组内全部条目都带 ok 布尔字段时独占裁决**(ok 全 True 的
      "同名+input 变"是探索不是纠错,不落回推断——旧推断在全事实数据上是误报);
      部分有部分没有(混合/截断数据)→ 事实不全,回退推断档,宁可保守也别漏报太宽。
    """
    p = getattr(e, "payload", None) or {}
    by_name: dict[str, list[dict]] = {}
    for c in (p.get("tool_calls") or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "") or "").strip()
        if name:
            by_name.setdefault(name, []).append(c)
    ref = _entry_ref(e)
    for name, calls in by_name.items():
        # ---- 确定性档:同名组内**全部**条目带 ok 布尔字段 → 事实齐,只认事实 ----
        if all(isinstance(c.get("ok"), bool) for c in calls):
            failed: Optional[dict] = None
            for c in calls:   # tool_calls 列表序 = 时间序
                ok = c.get("ok")
                if ok is False:
                    failed = c
                elif ok is True and failed is not None:
                    reason = _short(str(failed.get("error_reason") or ""), 160)
                    shown = " → ".join(
                        _short(_freeze_input(cc.get("input")), 120) for cc in calls[:3])
                    material = (
                        f"[ref={ref}] 纠错模式(确定性):一次执行里工具「{name}」先失败"
                        f"(真因:{reason or '未记录'}),之后同名调用成功,共 {len(calls)} 次。"
                        f"参数序列:{shown}。输出摘要:{_short(p.get('output'), 200)}"
                    )
                    return InsightSignal(pattern="tool_retry",
                                         task_id=getattr(e, "task_id", ""),
                                         trace_ref=ref, refs=(ref,), material=material,
                                         hard=True, ts=float(getattr(e, "ts", 0.0) or 0.0))
            continue   # 事实齐但无 False→True 配对 → 该组确定没纠错,不落回推断
        # ---- 回退推断档(老数据/事实不全的混合数据):
        #      最终没成功 → 纠错没闭环,不算(纯失败归 role replan)----
        if not p.get("success"):
            continue
        inputs = [_freeze_input(c.get("input")) for c in calls]
        if len(inputs) >= 2 and len(set(inputs)) >= 2:
            shown = " → ".join(_short(i, 120) for i in inputs[:3])
            material = (
                f"[ref={ref}] 纠错模式:一次执行里工具「{name}」试了 {len(inputs)} 次、"
                f"参数在变,最终整次执行成功。参数序列:{shown}。"
                f"输出摘要:{_short(p.get('output'), 200)}"
            )
            return InsightSignal(pattern="tool_retry", task_id=getattr(e, "task_id", ""),
                                 trace_ref=ref, refs=(ref,), material=material,
                                 hard=True, ts=float(getattr(e, "ts", 0.0) or 0.0))
    return None


def find_insight_signals(entries: Iterable[Any]) -> list[InsightSignal]:
    """零 LLM 确定性信号门(门1):从 Trace 执行事件里筛"值得抽洞察"的片段。

    只认三种模式(全是硬证据 = 失败→成功配对):
    ① tool_retry:同名工具 ok=False → 之后 ok=True(确定性,slice C 事实字段;
       老数据无 ok 字段回退推断"同名 ≥2 次且 input 变 + run 最终成功");
    ② replan_recovery:terminal 非 COMPLETED 的 run 之后,同任务后续 run 成功
       (同任务的 kind="error" 事件真因顺带并进材料);
    ③ task_recovery:同一任务(registry_id)的 task_run error→done。
    平静日子(无命中)→ [](调用方零 LLM、零写入)。输入 duck-typed
    (kind/task_id/payload/ts/seq),坏事件跳过不炸。
    """
    by_task: dict[str, list[Any]] = {}
    for e in entries or []:
        if getattr(e, "kind", "") in HARVEST_KINDS:
            by_task.setdefault(str(getattr(e, "task_id", "")), []).append(e)
    out: list[InsightSignal] = []
    for tid, ents in by_task.items():
        ents.sort(key=lambda x: (float(getattr(x, "ts", 0.0) or 0.0), int(getattr(x, "seq", 0) or 0)))
        runs = [e for e in ents if e.kind == "atom_run"]
        errors = [e for e in ents if e.kind == "error"]
        # ① 纠错模式(每个 run 至多一条,不重复计)
        for e in runs:
            try:
                sig = _tool_retry_signal(e)
            except Exception:
                sig = None   # 坏 payload 不炸信号门
            if sig is not None:
                out.append(sig)
        # ② replan 恢复:失败 run(terminal 非 completed)→ 后续成功 run
        failed: Optional[Any] = None
        for e in runs:
            p = getattr(e, "payload", None) or {}
            term = str(p.get("terminal") or "").strip().lower()
            if term and term != "completed":
                failed = e   # 记住最近一次异常终止(空 terminal = 不可判,不冤枉)
                continue
            if failed is not None and p.get("success"):
                fp = getattr(failed, "payload", None) or {}
                fref, sref = _entry_ref(failed), _entry_ref(e)
                # 同任务、失败之前最近的 error 事件真因并进材料(error_type/stage)
                err_txt = ""
                for ee in reversed(errors):
                    if float(getattr(ee, "ts", 0.0) or 0.0) <= float(getattr(e, "ts", 0.0) or 0.0):
                        ep = getattr(ee, "payload", None) or {}
                        err_txt = (f"错误真因:{ep.get('error_type', '')}"
                                   f"({ep.get('stage', '')}) {_short(ep.get('error'), 160)}。")
                        break
                material = (
                    f"[ref={sref}] replan 恢复:任务 {tid} 一次执行以 terminal="
                    f"{fp.get('terminal', '')} 终止(atom={fp.get('atom_id', '')},"
                    f"证据 {fref}),{err_txt}后续重跑成功"
                    f"(atom={p.get('atom_id', '')})。成功输出摘要:{_short(p.get('output'), 200)}"
                )
                out.append(InsightSignal(pattern="replan_recovery", task_id=tid,
                                         trace_ref=sref, refs=(sref, fref),
                                         material=material, hard=True,
                                         ts=float(getattr(e, "ts", 0.0) or 0.0)))
                failed = None
        # ③ 任务级 error→done(task_run 事件按 registry_id 分组看序列)
        by_reg: dict[str, list[Any]] = {}
        for e in ents:
            if e.kind != "task_run":
                continue
            p = getattr(e, "payload", None) or {}
            by_reg.setdefault(str(p.get("registry_id") or tid), []).append(e)
        for _rid, seq in by_reg.items():
            err_e: Optional[Any] = None
            for e in seq:
                p = getattr(e, "payload", None) or {}
                st = str(p.get("status") or "")
                if st == "error":
                    err_e = e
                elif st == "done" and err_e is not None:
                    epay = getattr(err_e, "payload", None) or {}
                    eref, dref = _entry_ref(err_e), _entry_ref(e)
                    material = (
                        f"[ref={dref}] 任务先报错后完成:「{_short(p.get('intent'), 120)}」"
                        f"(执行者 {p.get('who', '')})先以 error 收场"
                        f"(证据 {eref},当时结果:{_short(epay.get('result'), 160)}),"
                        f"后来完成。完成结果:{_short(p.get('result'), 200)}"
                    )
                    out.append(InsightSignal(pattern="task_recovery", task_id=tid,
                                             trace_ref=dref, refs=(dref, eref),
                                             material=material, hard=True,
                                             ts=float(getattr(e, "ts", 0.0) or 0.0)))
                    err_e = None
    out.sort(key=lambda s: s.ts)
    return out


def collect_run_texts(entries: Iterable[Any]) -> list[tuple]:
    """执行池里每条 atom_run/task_run 的 (ref, 确定性文本) —— 软观察复现背书的比对面。
    error 事件不算独立 run(它是某次 run 的伴生真因,算它一票会虚增复现数)。"""
    out: list[tuple] = []
    for e in entries or []:
        if getattr(e, "kind", "") in ("atom_run", "task_run"):
            t = _entry_text(e)
            if t.strip():
                out.append((_entry_ref(e), t))
    return out


# ---- 洞察编译器 system(三类;硬禁另三轴)----

INSIGHT_SYSTEM = (
    "你是 KarvyLoop 的执行洞察编译器。下面是若干条从系统运行记录(Trace)里筛出来的执行片段"
    "(工具换参数重试后成功 / 失败后重跑恢复 / 任务先报错后完成),每条带 [ref=…] 编号。\n"
    "从中抽出**不是关于这次任务本身、将来仍然成立**的认知。只有三类:\n"
    "- env(环境事实):这台机器/这个环境的客观事实(如「这台机器 pip 装包要走镜像源」"
    "「VM 的 SFTP 坏的,传文件走 base64」)。\n"
    "- correction(纠错经验):这次纠错揭示的、将来同类操作该直接采用的做法。\n"
    "- observation(顺带观察):执行顺带暴露的世界规律(如「客户邮件都在周五发」)。\n"
    "**硬禁区,一条都不许出**:\n"
    "- 任务评语(这次任务干得好不好/质量/成败评价 —— 归技能线,不归你);\n"
    "- 决策规则/用户偏好(「用户喜欢…」「以后都应该先问…」—— 归决策偏好线);\n"
    "- 一次性细节(只对这一次任务成立的参数值/文件名/编号,泛化不了的别抽);\n"
    "- 开放问题/猜测(「可能」「也许」「有待确认」的不要,拿不准就不抽)。\n"
    "不要输出带具体业务域或角色归属的工作方法(那是角色经验的地盘)。\n"
    "每条必须带 evidence_ref = 它依据的那条片段的 ref(**原样照抄材料里的,不许编造**)。\n"
    "**严格保守,宁少勿滥**,最多 5 条;没有值得沉淀的就输出 []。\n"
    "每条短、自足、脱离上下文也读得懂(别用「它/这个」指代)。\n"
    "严格输出 JSON 数组,元素 "
    "{\"content\":\"<一条认知>\",\"kind\":\"env|correction|observation\",\"evidence_ref\":\"<ref>\"};"
    "没有可抽的输出 []。不要输出 JSON 以外的任何文字。"
)


def format_signals(signals: list[InsightSignal]) -> str:
    """把一批信号的确定性材料拼成编译器输入(每条自带 [ref=…],evidence_ref 从这儿抄)。"""
    return "\n".join(s.material for s in signals if (s.material or "").strip())


# ---- 解析(镜像 experience.parse_experiences;宁空勿毒升到指称层)----


def parse_insights(text: str, valid_refs: Iterable[str]) -> list[dict]:
    """解析洞察编译器输出 → [{"content","kind","evidence_ref"}]。**宁空勿毒**:
    只剥外层 fence → json.loads;像 JSON 却解析失败 → [](绝不 prose 兜底);
    **evidence_ref 核不回本批真实 ref → 整条丢**(编造证据的指称层投毒,一票否决);
    带 domain/role 归属的丢(role_experience 地盘);超长丢;封顶 MAX_CANDIDATES。
    """
    refs = {str(r) for r in (valid_refs or []) if str(r).strip()}
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return []
    try:
        data: Any = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []   # 解析失败一律拒(不 prose 兜底)
    if isinstance(data, dict):
        for key in ("insights", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data] if data.get("content") else []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if len(out) >= MAX_CANDIDATES:
            break
        if not isinstance(item, dict):
            continue
        c = (item.get("content") or "").strip()
        if not c or len(c) > _MAX_INSIGHT_LEN:
            continue
        ref = str(item.get("evidence_ref") or "").strip()
        if ref not in refs:
            continue   # 编造/漏 evidence → 整条丢(宁空勿毒升到指称层)
        if str(item.get("domain") or "").strip() or str(item.get("role") or "").strip():
            continue   # 带 (domain,role) 的工作方法 = role_experience 地盘,不双记账
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in _KINDS:
            kind = "observation"   # 不认识的类别按最保守的软观察走(门2 更严)
        out.append({"content": c, "kind": kind, "evidence_ref": ref})
    return out


# ---- 门2:复现关(硬证据首见即写;软观察须 ≥2 run 词面背书)----


def soft_backing_runs(content: str, run_texts: list[tuple], *,
                      min_overlap: int = SOFT_MIN_OVERLAP) -> int:
    """content 在多少个**不同 run** 的材料里有词面背书(overlap_score 零 LLM 计数,
    复刻 decision_pref 的确定性复现地板;**无向量**)。"""
    from karvyloop.context.relevance import overlap_score
    n = 0
    for _ref, text in run_texts or []:
        if overlap_score(content, text) >= max(1, min_overlap):
            n += 1
    return n


def passes_reproduction(cand: dict, *, signal: Optional[InsightSignal],
                        run_texts: list[tuple]) -> bool:
    """复现关判定:
    - **硬证据候选**(env/correction 且背书信号是失败→成功配对)→ 首见即写(provisional 兜底)。
    - **软观察候选**(kind=observation,或背书信号不硬)→ 须跨 ≥SOFT_MIN_RUNS 个 run
      词面背书才写(一次出现的"规律"不是规律)。
    """
    kind = cand.get("kind", "observation")
    hard = bool(signal is not None and signal.hard)
    if kind in ("env", "correction") and hard:
        return True
    return soft_backing_runs(cand.get("content", ""), run_texts) >= SOFT_MIN_RUNS


def _local_device() -> str:
    """env 类洞察的设备标(环境事实是这台机器的);拿不到主机名 → "local" 诚实兜底。"""
    try:
        return platform.node() or "local"
    except Exception:
        return "local"


def make_insight_belief(content: str, kind: str, *, trace_ref: str = "",
                        device: str = "", now: Optional[float] = None) -> Belief:
    """构造一条执行洞察 Belief(写入走 MemoryManager.write 唯一咽喉,这里只造形状)。

    - `provenance = {source, provisional: True, kind, trace_ref, ts}`(全字段;provisional
      让 provenance_rank 封顶 auto 档,永掀不翻人确认的)。
    - env 类附 `applies={"device"}`:环境事实按设备圈定,不冒充普适真理。
    - `scope="personal"`(执行洞察是你实例的通用层认知,无域归属)。
    """
    if now is None:
        now = time.time()
    k = kind if kind in _KINDS else "observation"
    prov: dict = {
        "source": TASK_INSIGHT_SOURCE, "provisional": True, "kind": k,
        "trace_ref": (trace_ref or "").strip(), "ts": now,
    }
    if k == "env":
        prov["applies"] = {"device": (device or "").strip() or _local_device()}
    return Belief(content=content.strip(), provenance=prov,
                  freshness_ts=now, scope="personal")


def is_task_insight(b: Any) -> bool:
    """是不是一条执行洞察(面板来源列/召回筛选/审计对齐用)。"""
    prov = getattr(b, "provenance", None) or {}
    return prov.get("source") == TASK_INSIGHT_SOURCE


def build_insight_beliefs(cands: list[dict], *, signals: list[InsightSignal],
                          run_texts: list[tuple], device: str = "",
                          now: Optional[float] = None,
                          max_writes: int = 3) -> list[Belief]:
    """候选 → 过复现关(门2)→ Belief 列表(封顶 max_writes;写入由调用方走 mem.write)。"""
    if now is None:
        now = time.time()
    by_ref: dict[str, InsightSignal] = {}
    for s in signals or []:
        for r in s.refs or (s.trace_ref,):
            by_ref.setdefault(str(r), s)
    out: list[Belief] = []
    for c in cands or []:
        if len(out) >= max(0, max_writes):
            break
        sig = by_ref.get(str(c.get("evidence_ref", "")))
        if not passes_reproduction(c, signal=sig, run_texts=run_texts):
            continue
        out.append(make_insight_belief(c["content"], c.get("kind", "observation"),
                                       trace_ref=str(c.get("evidence_ref", "")),
                                       device=device, now=now))
    return out


# ---- LLM 蒸馏(镜像 experience.distill_experience;单批一次调用)----


async def distill_insights(signals: list[InsightSignal], *, gateway: Any,
                           model_ref: str = "") -> list[dict]:
    """跑一次受限 LLM(无工具)→ 候选洞察 list(evidence_ref 已核回本批 refs)。

    调用方保证:signals 非空(信号门已过)、单 tick 只调本函数一次、token_source 已打标。
    """
    material = format_signals(signals)
    if not material.strip():
        return []
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref   # 解析不了用原值(测试桩 gateway 无 resolve_model 也能跑)
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)   # 基建天花板(防病态爆炸)
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[INSIGHT_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    valid = {r for s in signals for r in (s.refs or (s.trace_ref,))}
    return parse_insights(out, valid)


__all__ = [
    "TASK_INSIGHT_SOURCE", "INSIGHT_SYSTEM", "HARVEST_KINDS",
    "MAX_CANDIDATES", "SOFT_MIN_RUNS", "SOFT_MIN_OVERLAP",
    "InsightSignal", "find_insight_signals", "collect_run_texts", "format_signals",
    "parse_insights", "soft_backing_runs", "passes_reproduction",
    "make_insight_belief", "is_task_insight", "build_insight_beliefs",
    "distill_insights",
]
