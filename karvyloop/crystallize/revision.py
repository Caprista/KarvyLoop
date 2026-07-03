"""revision — Trace-conditioned 技能修订（crystallize/revision.py）。

闭环:某技能(sig)近几次重跑的**客观信号差**(满意度低/失败,全部从 Trace 派生的
SatisfactionStore 读)→ 触发 LLM 修订:输入 = SKILL.md 现方法 + 失败 run 的 Trace 摘要,
输出 = 修订后的 Steps。小改自动落 + SKILL.md 记 Changelog;大改出 H2A 卡(proposal),
人过目才动 —— 绝不静默换方法。

设计纪律(承袭 lessons.py / atom_critic.py):
- **跑评分离**:本模块只在慢侧 tick(daily/knowledge 节奏)被调,drive 热路径零改动。
- **只从 Trace 派生**:触发信号读 SatisfactionStore(它由 trace_eval 从 eval_fact 重建),
  失败摘要按 (task_id, trace_ref) 回 Trace 取;水位也回写 Trace(重启安全,不重复烧钱)。
- **问责链**:skill 是 atom 层产物,由客观信号自动裁;但留完整审计痕
  (Trace `skill_revision` 事件 + SKILL.md 内 `## Changelog`:日期 + 触发的 trace refs + 改了什么),
  修订幅度大(方法重写/删步骤过半)→ 升 H2A 卡,不静默。
- **存方法不存答案**:只改 `## Steps` 段;prompt 明令 + 每步过 sanitize(安全单行)。
- **宁空勿毒**:parse_revision 严格 JSON;解析失败/垃圾 → 返 ([], "") → **原文一字不动**。
- **匹配不上向量**:小改/大改分界用 token-overlap(cluster.intent_tokens,含 CJK bigram)。
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path
from typing import Callable, Optional

from .atom_critic import SatisfactionStore, sanitize_critique, _first_json_object
from .cluster import intent_tokens, overlap_score


# ---- 常量:Trace 事件 / proposal kind(routes/registry 这波别人在动 → kind 常量放本模块)----

# 修订尝试/落地回写 Trace 的事件类型(审计痕 + 水位;prune_raw 只丢原文类,本事件永久)
REVISION_KIND = "skill_revision"
# H2A 卡 kind(大改出卡)。接线方在 handlers 表挂 apply_revision_proposal 即可兑现。
KIND_REVISE_SKILL = "revise_skill"

# ---- 触发阈值(依据写清;改动需记数据依据,同 atom_critic 纪律)----

# 至少积累这么多已评样本才考虑修订:confidence_overall 的先验伪样本量 pseudo=4,
# 样本 < 4 时置信分被先验(0.6)主导,任何触发都是噪声。与 LESSON_MIN_SAMPLES 同地板。
REVISION_MIN_SAMPLES = 4
# 触发线:confidence_overall(贝叶斯收缩,先验 0.6=中性)< 0.55 —— 收缩意味着要真实攒出
# 坏信号才降得下来(n=4 时需近期加权均值 ≤0.5,即整体最多"成功但未核验"水平、通常含失败);
# 低于中性先验 0.05 = 客观在拖后腿,不是采样波动。
REVISION_BAD_THRESH = 0.55
# 坏样本判据:overall ≤ 0.5。0.5 是"成功但未核验"的上限(achievement=0.5 × (0.6+0.4×1.0));
# 已核验成功即使效率 0 也有 0.6 —— 所以 ≤0.5 ⇔ 没做对或没核验,是干净的"这次不行"信号。
REVISION_BAD_SAMPLE = 0.5
# 近窗内至少这么多坏样本才触发(防单次离群 + 置信分漂移合谋误触发 LLM)。
REVISION_MIN_BAD = 2
# 只看最近这么多样本(评的是**现方法**;更早的样本可能对应修订前的旧方法)。
REVISION_WINDOW = 8
# 水位:距上次修订尝试新增这么多已评样本才再试(给上次修订起效时间 + 防反复烧钱;
# 与 LESSON_NEW_SAMPLES 同节奏)。水位回写 Trace → 重启可重建。
REVISION_NEW_SAMPLES = 4
# 每轮慢侧 tick 最多修几个技能(成本尖峰封顶,同 LESSON_LIMIT 精神)。
REVISION_LIMIT = 3
# 进对比材料的失败 run 摘要条数上限。
_REVISION_BAD_RUNS = 3
# steps 数量上限(垃圾守卫:LLM 吐 200 步的"方法"不是方法)。
_MAX_STEPS = 20

# 大改分界:旧步骤的"保留率" < 0.5(过半旧步骤被删/被改写到 token-overlap 认不出)→ 大改。
# 一条旧步骤算"保留"= 存在某条新步骤与它 token-overlap ≥ 0.5(cluster.intent_tokens,
# 含 CJK bigram,无向量)。加注意事项/新增步骤/微调措辞 → 保留率高 → 小改自动落。
REVISION_MAJOR_RETAIN_THRESH = 0.5
_STEP_OVERLAP_THRESH = 0.5

# Changelog 段 header(审计痕;recall.split_body_guidance 会把它从重跑上下文剥掉)
CHANGELOG_HEADER = "## Changelog"


REVISION_SYSTEM = (
    "你是 role,在修订一个反复表现不佳的技能的**方法**(Steps)。\n"
    "输入是它现在的方法,和最近几次失败/低分执行的摘要。\n"
    "只修订**做法/步骤**:可以调整步骤顺序、改写步骤、加注意事项、删无效步骤。\n"
    "**绝不写入任何具体答案/结果/数据/日期**——技能存方法不存答案,答案会过期投毒。\n"
    "严格只输出一个 JSON 对象,不要别的文字:\n"
    '{"steps": ["1. 第一步", "2. 第二步", "..."], "note": "一句说明改了什么、为什么"}\n'
    '修不动/信息不足 → {"steps": [], "note": ""}。'
)


# ---- 解析(宁空勿毒)----

def parse_revision(text: str) -> tuple[list[str], str]:
    """严格解析 LLM 修订输出 → (steps, note)。任何不合法 → ([], "") = 原文不动。

    - 只认第一个配平 JSON 对象;steps 必须是 list[str];
    - 每步过 sanitize_critique(安全单行:防 `## `/`---`/fence 结构性投毒);
    - 消毒后为空的步骤丢弃;steps 超 _MAX_STEPS 或全空 → ([], "")。
    """
    blob = _first_json_object((text or "").strip())
    if not blob:
        return ([], "")
    try:
        obj = json.loads(blob)
    except Exception:
        return ([], "")
    if not isinstance(obj, dict):
        return ([], "")
    raw_steps = obj.get("steps", None)
    if not isinstance(raw_steps, list):
        return ([], "")
    steps: list[str] = []
    for s in raw_steps:
        if not isinstance(s, str):
            return ([], "")           # 混入非字符串 = 形状不对,整体拒(宁空勿毒)
        c = sanitize_critique(s)
        if c:
            steps.append(c)
    if not steps or len(steps) > _MAX_STEPS:
        return ([], "")
    return (steps, sanitize_critique(obj.get("note", "")))


async def judge_revision(material: str, *, gateway, model_ref: str = "") -> str:
    """修订的 LLM 调用(返**原文**,revise_underperforming 再 parse_revision)。

    材料过 clip_to_tokens(context engineering 预算);token_source 打 skill_revision 标。
    无 gateway / 失败 → ""(宁空勿毒,绝不拖垮)。
    """
    if gateway is None:
        return ""
    from karvyloop.context.budget import clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    mat, _ = clip_to_tokens(material or "", 1800)
    out = ""
    try:
        with token_source("skill_revision"):
            async for ev in gateway.complete(
                [{"role": "user", "content": mat}], [], ref,
                system=SystemPrompt(static=[REVISION_SYSTEM]),
            ):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception:
        return ""
    return out


# ---- SKILL.md 的 Steps 段读写 ----

_H2_RE = re.compile(r"^## ")


def extract_steps(skill_text: str) -> tuple[Optional[str], list[str]]:
    """从 SKILL.md 全文找 `## Steps` 开头的段 → (header 行原文, 步骤行列表)。

    结晶写的 header 是 `## Steps(上次证明可行的打法)`,手写的可能是裸 `## Steps` ——
    都按前缀匹配。没有 Steps 段 → (None, [])(调用方按大改处理:出卡由人裁,不自动动)。
    """
    lines = (skill_text or "").splitlines()
    header: Optional[str] = None
    steps: list[str] = []
    in_sec = False
    for line in lines:
        if _H2_RE.match(line):
            if in_sec:
                break
            if line.strip().startswith("## Steps"):
                header = line
                in_sec = True
            continue
        if in_sec and line.strip():
            steps.append(line.strip())
    return (header, steps)


def replace_steps(skill_text: str, new_steps: list[str]) -> str:
    """把 `## Steps` 段内容替换成 new_steps(header 原样保留;段以下一个 `## `/文末为界)。"""
    lines = (skill_text or "").splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    replaced = False
    while i < n:
        line = lines[i]
        if not replaced and _H2_RE.match(line) and line.strip().startswith("## Steps"):
            out.append(line)
            out.append("")
            out.extend(new_steps)
            i += 1
            while i < n and not _H2_RE.match(lines[i]):
                i += 1
            if i < n:
                out.append("")
            replaced = True
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if skill_text.endswith("\n") else "")


def _append_changelog(skill_path: Path, entry: str) -> None:
    """在 SKILL.md 的 `## Changelog` 段追加一条审计行(段不存在则文末新建)。"""
    from .improve import _insert_into_section
    text = skill_path.read_text(encoding="utf-8")
    text = _insert_into_section(text, CHANGELOG_HEADER, [entry])
    skill_path.write_text(text, encoding="utf-8")


def _fmt_date(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---- 小改/大改分界(token-overlap,无向量)----

def is_major_revision(old_steps: list[str], new_steps: list[str]) -> bool:
    """大改 = 方法重写/删步骤过半:旧步骤在新方法里的**保留率 < 0.5**。

    保留 = 某条新步骤与该旧步骤 token-overlap ≥ 0.5(cluster.intent_tokens,含 CJK
    bigram)。加注意/新增步骤/微调措辞 → 旧步骤仍认得出 → 小改;整段换打法/删过半 → 大改。
    没有旧步骤(手写技能无 Steps / 空段)→ 视为大改(没有"小改"的基准,交人过目)。
    """
    olds = [s for s in (old_steps or []) if s.strip()]
    news = [s for s in (new_steps or []) if s.strip()]
    if not olds:
        return True
    if not news:
        return True                     # 删光所有步骤 = 大改
    new_toks = [intent_tokens(s) for s in news]
    retained = 0
    for o in olds:
        ot = intent_tokens(o)
        if not ot:
            retained += 1               # 无 token 的旧行(纯符号)不参与裁决,算保留
            continue
        if any(overlap_score(ot, nt) >= _STEP_OVERLAP_THRESH for nt in new_toks):
            retained += 1
    return (retained / len(olds)) < REVISION_MAJOR_RETAIN_THRESH


# ---- 触发判定(客观信号,全部从 Trace 派生的 SatisfactionStore 读)----

def needs_revision(satisfaction: SatisfactionStore, sig: str) -> tuple[bool, str]:
    """该技能是否该修?返回 (是否, 依据字符串——进审计痕/卡 basis)。

    两道门同时过才触发(阈值依据见模块顶常量注释):
    ① confidence_overall(贝叶斯收缩+新近度)< REVISION_BAD_THRESH —— 整体客观在拖后腿;
    ② 近 REVISION_WINDOW 个样本里 ≥ REVISION_MIN_BAD 个坏样本(overall ≤ 0.5)——
      有具体的失败 run 可喂给修订(也防漂移误触发)。
    """
    samples = satisfaction.samples(sig)
    if len(samples) < REVISION_MIN_SAMPLES:
        return (False, f"samples={len(samples)}<{REVISION_MIN_SAMPLES}")
    conf = satisfaction.confidence_overall(sig)
    if conf is None:
        return (False, "no confidence")
    recent = samples[-REVISION_WINDOW:]
    n_bad = sum(1 for s in recent if s.overall <= REVISION_BAD_SAMPLE)
    basis = f"confidence={conf:.2f}(<{REVISION_BAD_THRESH}触发) bad={n_bad}/{len(recent)}(≥{REVISION_MIN_BAD}触发)"
    return (conf < REVISION_BAD_THRESH and n_bad >= REVISION_MIN_BAD, basis)


# ---- 水位 + 审计痕(全在 Trace,重启安全)----

def _revision_watermark(trace, sig: str) -> int:
    """上次修订尝试时的样本数(从 Trace 的 skill_revision 事件重建;无 → 0)。"""
    try:
        entries = trace.query(f"revision:{sig}", kind=REVISION_KIND)
    except Exception:
        return 0
    wm = 0
    for e in entries:
        p = getattr(e, "payload", None) or {}
        try:
            wm = max(wm, int(p.get("n_samples", 0) or 0))
        except (TypeError, ValueError):
            continue
    return wm


def _writeback_revision(trace, sig: str, *, skill_name: str, mode: str, n_samples: int,
                        trigger: str, trace_refs: list[str], note: str, clk) -> None:
    """修订尝试(无论落没落)回写 Trace:审计痕 + 水位。失败不拖垮。

    mode: "auto"(小改已落) | "proposed"(大改出卡) | "noop"(触发了但 LLM 修不动/解析失败)
          | "h2a_applied"(人 ACCEPT 后落地)。
    """
    try:
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(
            task_id=f"revision:{sig}", kind=REVISION_KIND,
            payload={"sig": sig, "skill_name": skill_name, "mode": mode,
                     "n_samples": int(n_samples), "trigger": trigger,
                     "trace_refs": list(trace_refs or []), "note": note},
            ts=clk(), source="revision",
        ))
    except Exception:
        pass


# ---- H2A 卡(大改)----

def build_revision_proposal(*, skill_name: str, sig: str, path: str,
                            old_steps: list[str], new_steps: list[str],
                            note: str, trigger: str, trace_refs: list[str], ts: float):
    """大改 → 组 revise_skill H2A 卡(复用 Proposal 机制;kind 常量在本模块,不动 registry)。

    payload 全字符串值 —— 走「改了再批」白名单时用户可就地改 new_steps。
    """
    from karvyloop.karvy.atoms import Proposal   # 局部 import:同 proposal_registry 惯例,避免层间硬耦合
    return Proposal(
        summary=f"技能「{skill_name}」近几次客观信号差,建议大幅修订方法(重写/删步骤过半,需你过目)",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.8,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_REVISE_SKILL,
        payload={
            "skill_name": skill_name,
            "sig": sig,
            "path": str(path),
            "old_steps": "\n".join(old_steps),
            "new_steps": "\n".join(new_steps),
            "note": note or "",
            "trigger": trigger,
            "trace_refs": ",".join(trace_refs or []),
        },
        basis=f"触发依据:{trigger};失败样本 traces: {', '.join(trace_refs or []) or '(原文已滚动)'}。"
              f"改动幅度过大(方法重写/删步骤过半),按问责链升 H2A,不静默换方法。",
        context_ref={"kind": "skill", "id": skill_name},
    )


def apply_revision_proposal(proposal, *, trace=None, clock=None) -> tuple[bool, str]:
    """revise_skill 卡的 ACCEPT handler:把卡里的 new_steps 落进 SKILL.md + 记 Changelog +
    Trace 审计。接线方只需 `handlers[KIND_REVISE_SKILL] = partial(apply_revision_proposal, trace=..., clock=...)`。

    「改了再批」生效:new_steps 以 ACCEPT 时 payload 为准(用户可能就地改过)。
    """
    clk = clock or _time.time
    p = dict(getattr(proposal, "payload", {}) or {})
    path = Path(p.get("path", "") or "")
    if not path.is_file():
        return (False, f"SKILL.md 不存在:{path}")
    new_steps = [sanitize_critique(s) for s in (p.get("new_steps", "") or "").splitlines()]
    new_steps = [s for s in new_steps if s]
    if not new_steps:
        return (False, "卡里没有可落的步骤(宁空勿毒:不清空方法)")
    text = path.read_text(encoding="utf-8")
    header, _old = extract_steps(text)
    if header is None:
        # 无 Steps 段(手写技能):文末新建一个,不猜别的段
        text = text.rstrip() + "\n\n## Steps\n\n" + "\n".join(new_steps) + "\n"
    else:
        text = replace_steps(text, new_steps)
    path.write_text(text, encoding="utf-8")
    refs = [r for r in (p.get("trace_refs", "") or "").split(",") if r]
    _append_changelog(path, _changelog_entry(clk(), "h2a", refs, p.get("note", "")))
    if trace is not None:
        _writeback_revision(trace, p.get("sig", ""), skill_name=p.get("skill_name", ""),
                            mode="h2a_applied", n_samples=0, trigger=p.get("trigger", ""),
                            trace_refs=refs, note=p.get("note", ""), clk=clk)
    return (True, f"已按卡修订 {p.get('skill_name', '')} 的方法({len(new_steps)} 步)")


def _changelog_entry(ts: float, mode: str, trace_refs: list[str], note: str) -> str:
    refs = ",".join((trace_refs or [])[:3]) or "(原文已滚动)"
    return f"- ({_fmt_date(ts)}) [revision:{mode}] traces: {refs} — {sanitize_critique(note) or '按客观信号修订 Steps'}"


# ---- 主入口(慢侧 tick 调;drive 热路径零改动)----

def revise_underperforming(trace, satisfaction: SatisfactionStore, *, judge,
                           skills_dir: Path, skill_index=None,
                           proposal_sink: Optional[Callable] = None,
                           limit: int = REVISION_LIMIT, clock=None) -> dict:
    """**慢侧**:对客观信号差的已结晶技能跑一轮 Trace-conditioned 修订。

    - `judge`:同步 callable `(material) -> raw_text`(持 gateway 的层注入 judge_revision
      的 async→sync 桥)。无 → {"revised":0,"proposed":0}(0 回归)。
    - `proposal_sink`:callable(Proposal) —— 大改卡的出口(接 PendingProposalRegistry.register)。
      无 sink 时大改**只记 Trace、不落盘**(绝不因没接线就静默自动落大改)。
    返回 {"revised": 小改自动落数, "proposed": 大改出卡数}。
    """
    out = {"revised": 0, "proposed": 0}
    if trace is None or satisfaction is None or judge is None:
        return out
    clk = clock or _time.time
    attempts = 0
    for sig in satisfaction.sigs():
        if attempts >= limit:
            break
        name = None
        if skill_index is not None:
            try:
                name = skill_index.name_for_sig(sig)
            except Exception:
                name = None
        if not name:
            continue                                   # 未结晶成技能的 sig 没有可修的 SKILL.md
        skill_path = Path(skills_dir) / name / "SKILL.md"
        if not skill_path.is_file():
            continue
        triggered, basis = needs_revision(satisfaction, sig)
        if not triggered:
            continue
        samples = satisfaction.samples(sig)
        # 水位:距上次修订尝试新增足够样本才再试(给上次修订起效时间;重启从 Trace 重建)
        if len(samples) - _revision_watermark(trace, sig) < REVISION_NEW_SAMPLES:
            continue
        # 失败 run 摘要(从 Trace 按 (task_id, trace_ref) 取;lessons 同一取数路径)
        from .lessons import _run_brief
        bad = [s for s in samples[-REVISION_WINDOW:]
               if s.overall <= REVISION_BAD_SAMPLE and s.trace_ref and s.task_id]
        bad = bad[-_REVISION_BAD_RUNS:]
        briefs = [b for b in (_run_brief(trace, s.task_id, s.trace_ref) for s in bad) if b]
        bad_refs = [s.trace_ref for s in bad]
        text = skill_path.read_text(encoding="utf-8")
        header, old_steps = extract_steps(text)
        method = (header + "\n" + "\n".join(old_steps)) if header else "(此技能没有 Steps 段)"
        if not briefs:
            # 失败原文被容量环剪了 → 推进水位别每轮空转(lessons M-2 同策),不烧 judge
            _writeback_revision(trace, sig, skill_name=name, mode="noop",
                                n_samples=len(samples), trigger=basis,
                                trace_refs=bad_refs, note="失败 run 原文不可得,跳过", clk=clk)
            continue
        material = (f"## 现方法(SKILL.md Steps)\n{method}\n\n## 最近失败/低分的执行\n"
                    + "\n\n".join(f"[差评 {i+1}]\n{b}" for i, b in enumerate(briefs)))
        attempts += 1
        try:
            raw = judge(material)
        except Exception:
            raw = ""
        new_steps, note = parse_revision(raw)
        if not new_steps:
            # 宁空勿毒:修不动/解析失败 → 原文一字不动;水位前移防下轮重烧同一批
            _writeback_revision(trace, sig, skill_name=name, mode="noop",
                                n_samples=len(samples), trigger=basis,
                                trace_refs=bad_refs, note="LLM 修不动/解析失败,原文不动", clk=clk)
            continue
        if is_major_revision(old_steps, new_steps):
            # 大改(方法重写/删步骤过半)→ H2A 卡,人过目;SKILL.md 不动
            prop = build_revision_proposal(
                skill_name=name, sig=sig, path=str(skill_path),
                old_steps=old_steps, new_steps=new_steps, note=note,
                trigger=basis, trace_refs=bad_refs, ts=clk())
            if proposal_sink is not None:
                try:
                    proposal_sink(prop)
                    out["proposed"] += 1
                except Exception:
                    pass
            _writeback_revision(trace, sig, skill_name=name, mode="proposed",
                                n_samples=len(samples), trigger=basis,
                                trace_refs=bad_refs, note=note, clk=clk)
            continue
        # 小改(调步骤/加注意)→ 自动落 + Changelog 审计痕
        skill_path.write_text(replace_steps(text, new_steps), encoding="utf-8")
        _append_changelog(skill_path, _changelog_entry(clk(), "auto", bad_refs, note))
        _writeback_revision(trace, sig, skill_name=name, mode="auto",
                            n_samples=len(samples), trigger=basis,
                            trace_refs=bad_refs, note=note, clk=clk)
        out["revised"] += 1
    return out


__all__ = [
    "REVISION_KIND", "KIND_REVISE_SKILL", "REVISION_SYSTEM", "CHANGELOG_HEADER",
    "REVISION_MIN_SAMPLES", "REVISION_BAD_THRESH", "REVISION_BAD_SAMPLE",
    "REVISION_MIN_BAD", "REVISION_WINDOW", "REVISION_NEW_SAMPLES", "REVISION_LIMIT",
    "REVISION_MAJOR_RETAIN_THRESH",
    "parse_revision", "judge_revision", "extract_steps", "replace_steps",
    "is_major_revision", "needs_revision", "revise_underperforming",
    "build_revision_proposal", "apply_revision_proposal",
]
