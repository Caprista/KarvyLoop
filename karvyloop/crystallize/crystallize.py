"""crystallize — 结晶引擎（crystallize/crystallize.py）。

规格:docs/modules/crystallize.md §3 promote.py + §4 关 1 / 关 2 + §4 阈值是旋钮非真理
- 两道关顺序不能反:
    关 1(资格):has_verify_gate(sig) AND success_count >= 1
    关 2(价值):score >= PROMOTE_SCORE AND success_rate >= MIN_SUCCESS_RATE
                AND (generalized OR high_freq)
- 7天半衰期公式同驱动 promote 与 evict
- v1 简版:不实现 4 轮访谈异步生成(M1 v1 接受手工提供 name/manifest/body,
  后续 P1 接 AskUserQuestion);保证判定逻辑本身正确
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from karvyloop.schemas import Skill, UsageStats

from .signature import compute_signature
from .store import UsageStore
from .verify import VerifyStore


# ---- 阈值(§4 旋钮非真理)----
PROMOTE_SCORE = 3.0
MIN_SUCCESS_RATE = 0.8
HALFLIFE_DAYS = 7.0
MIN_RECENCY_FACTOR = 0.1
EVICT_SCORE = 0.5
STALE_DAYS = 30.0
HIGH_FREQ = 5  # usage_count >= HIGH_FREQ 也算价值够(无需参数泛化)
GENERALIZED_DISTINCT = 2  # 至少 2 种不同参数变体才判为可泛化


@dataclass(frozen=True)
class CrystallizeThresholds:
    """结晶灵敏度旋钮(9.4:从硬编码常量改为可配置;config.yaml `crystallize.*`)。

    docs §4"阈值是旋钮非真理":这几个值决定"技能库长多快/多严",真实用户测试期要
    能不改代码就调。默认 = 原硬编码值(向后兼容)。
      - min_usage_count:用够几次算高频价值(原 HIGH_FREQ=5)
      - min_success_rate:成功率下限(原 MIN_SUCCESS_RATE=0.8)
      - usage_debounce_sec:同 sig 去抖窗口,防单次爆发灌计数(原 USAGE_DEBOUNCE_SEC=60)
      - promote_score / generalized_distinct:价值分门槛 / 泛化所需变体数
    """
    min_usage_count: int = HIGH_FREQ
    min_success_rate: float = MIN_SUCCESS_RATE
    usage_debounce_sec: float = 60.0
    promote_score: float = PROMOTE_SCORE
    generalized_distinct: int = GENERALIZED_DISTINCT
    # 9.4:token-overlap 累积聚类门槛(0=关=精确签名;>0=同任务不同说法按 intent 重叠归并)。
    # 真机标定 0.2:6 种"平方"说法塌缩成 1 簇,而"爬虫/邮件"(只共享 python)不误并。
    # 这是 M1 测试期 #1 要调的旋钮(偏"结晶宽松"→调低更易长技能库,但过低会误并不同任务)。
    cluster_overlap_threshold: float = 0.2
    # docs/44 断⑭:关 2 并入满意度(Trace 派生,含 checker 差评/LLM 质量)——"被打差评的不晋升"。
    # 保守标定(floor 必须 < 0.5):样本 overall 的量纲是 —— 已核验成功 ≥0.6;成功但未核验
    # (无验证门,诚实打 5 折)= 0.5;失败/checker FAIL = 0.0。spec 明说"无独立验据仍可结晶,
    # 只是标 verified:false" → 纯"未核验成功"历史(0.5)**不许**被这道关拦,故 floor 取 0.45:
    # 只有**确凿差评**(0 分样本,即失败或独立验收 FAIL)占近期 ~1/4 以上权重才压到线下。
    # 样本 <min 不判(没证据不拦 —— 拦是要有据的,同一套诚实哲学)。
    satisfaction_floor: float = 0.45
    satisfaction_min_samples: int = 3


DEFAULT_THRESHOLDS = CrystallizeThresholds()


# ---- 判定结果 ----

class DecisionKind(str, Enum):
    NOT_ELIGIBLE = "not_eligible"   # 关 1 没过
    NOT_YET = "not_yet"             # 关 1 过了,关 2 还不够
    READY = "ready"                 # 两关都过


@dataclass
class PromoteDecision:
    kind: DecisionKind
    reason: str = ""
    score: float = 0.0
    success_rate: float = 0.0
    generalized: bool = False
    high_freq: bool = False

    @classmethod
    def NotEligible(cls, reason: str) -> "PromoteDecision":
        return cls(kind=DecisionKind.NOT_ELIGIBLE, reason=reason)

    @classmethod
    def NotYet(cls, reason: str) -> "PromoteDecision":
        return cls(kind=DecisionKind.NOT_YET, reason=reason)

    @classmethod
    def Ready(cls, reason: str = "", **kw) -> "PromoteDecision":
        return cls(kind=DecisionKind.READY, reason=reason, **kw)


# ---- 7天半衰期评分(同驱动 promote 与 evict)----

def usage_score(stats: UsageStats, *, now: float, halflife_days: float = HALFLIFE_DAYS) -> float:
    """7天半衰期:usage_count × 0.5^(days/7),保底 0.1(不是截底到 0)。

    days 上限不限;长时间不用 → score 趋近 usage_count × 0,但 MIN_RECENCY 兜住 0.1。
    注意:不是把整个 score 兜住 0.1,而是 recency 因子兜住 0.1,再 × usage_count。
    当 usage_count==0 时,score 自然是 0(规范里的 0.1 兜底只对 recency)。
    """
    if stats.usage_count <= 0:
        return 0.0
    if not stats.last_used_at:
        return 0.0
    days = max(0.0, (now - stats.last_used_at) / 86400.0)
    recency = max(0.5 ** (days / halflife_days), MIN_RECENCY_FACTOR)
    return stats.usage_count * recency


def success_rate(stats: UsageStats) -> float:
    """成功率 = success_count / max(1, usage_count)。"""
    denom = max(1, stats.usage_count)
    return stats.success_count / denom


def _is_generalized(param_variants: list[dict], *, distinct: int = GENERALIZED_DISTINCT) -> bool:
    """同 schema 至少 `distinct` 种取值 → 判为可泛化(参数化)。

    v1 保守:key 集合一致 + 值组合有 ≥ distinct 种不同。
    整体 pv 转成 sorted tuple 做哈希;完全相同的 pv 算 1 种。
    """
    if len(param_variants) < distinct:
        return False
    # 先检查 schema 形状(所有 pv 的 key 集合一致)
    key_set = frozenset(param_variants[0].keys())
    for pv in param_variants[1:]:
        if not isinstance(pv, dict):
            return False
        if frozenset(pv.keys()) != key_set:
            return False
    # 值组合去重计数
    value_signatures = set()
    for pv in param_variants:
        sig = tuple(sorted(pv.items(), key=lambda kv: kv[0]))
        value_signatures.add(sig)
    return len(value_signatures) >= distinct


def _is_high_freq(stats: UsageStats) -> bool:
    return stats.usage_count >= HIGH_FREQ


# ---- 关 1 + 关 2 判定 ----

def maybe_promote(
    sig: str,
    store: UsageStore,
    verify: VerifyStore,
    *,
    now: Optional[float] = None,
    thresholds: "CrystallizeThresholds" = DEFAULT_THRESHOLDS,
    satisfaction: Optional[object] = None,
) -> PromoteDecision:
    """两关判定。顺序不能反(spec §4 硬约束)。

    返回 PromoteDecision —— 调用方按 kind 分支(READY → 触发访谈 / 写入;
    NOT_YET → 等下次;NOT_ELIGIBLE → 不该结晶)。

    `thresholds`:结晶灵敏度旋钮(9.4 可配置;默认 = 原硬编码值,向后兼容)。
    `satisfaction`:SatisfactionStore(docs/44 断⑭)。给了才启用满意度关(None=旧行为,
    0 回归)——闸门条件从"跑成了 N 次"升级为"跑成且验过且**没被打差评** N 次":
    Trace 派生的满意度(含 checker FAIL 差评/LLM 低质量评)持续低于 floor → 不晋升。
    """
    now = now if now is not None else time.time()
    stats = store.get(sig)
    if stats is None:
        return PromoteDecision.NotEligible("no usage stats yet")

    # 关 1(资格):可验证 + 至少成功 1 次
    if not verify.has_gate(sig):
        return PromoteDecision.NotEligible("no verify gate (关1)")
    if stats.success_count < 1:
        return PromoteDecision.NotEligible("never succeeded (关1)")

    # 关 2(价值):用够 + 泛化 + 成功率(均走可配置 thresholds)
    score = usage_score(stats, now=now)
    sr = success_rate(stats)
    generalized = _is_generalized(stats.param_variants, distinct=thresholds.generalized_distinct)
    high_freq = stats.usage_count >= thresholds.min_usage_count
    if score < thresholds.promote_score:
        return PromoteDecision.NotYet(f"score {score:.2f} < {thresholds.promote_score}")
    if sr < thresholds.min_success_rate:
        return PromoteDecision.NotYet(f"success_rate {sr:.2f} < {thresholds.min_success_rate}")
    if not (generalized or high_freq):
        return PromoteDecision.NotYet("not generalized and not high_freq")
    # 关 2(信用,docs/44 断⑭):满意度(新近度加权,抗滞后)不许持续差评。保守:
    # 样本不足 min_samples 不判;评不出(异常)不拦 —— 只有**确凿**差评才挡晋升。
    if satisfaction is not None:
        floor = getattr(thresholds, "satisfaction_floor", 0.45)
        min_n = getattr(thresholds, "satisfaction_min_samples", 3)
        try:
            samples = satisfaction.samples(sig)
            if len(samples) >= min_n:
                sat = satisfaction.mean_overall_recent(sig)
                if sat is not None and sat < floor:
                    return PromoteDecision.NotYet(
                        f"satisfaction {sat:.2f} < {floor}(近期被打差评,不晋升)")
        except Exception:
            pass  # 满意度评不出 → 不拦(信号缺失≠差评)
    return PromoteDecision.Ready(
        reason="two gates passed",
        score=score, success_rate=sr,
        generalized=generalized, high_freq=high_freq,
    )


# ---- 结晶产物写入 SKILL.md ----

_FRONT_DATE = re.compile(r"^date:\s*(.+)$", re.MULTILINE)


def build_skill_md(
    name: str,
    description: str,
    body: str,
    *,
    signature: str,
    verify_proof: dict,
    trace_refs: list[str],
    when_to_use: str = "",
    scope: str = "user",
    arguments: Optional[list[dict]] = None,
    result_reuse: str = "dynamic",
    verified: Optional[bool] = None,
    created_ts: Optional[float] = None,
) -> str:
    """构造 SKILL.md 文本。

    - frontmatter 必含 name/signature/description/when_to_use/verify_proof/trace_refs(scope+arguments 可选)
    - signature: 用于 sig↔name 反查(SkillIndex 从磁盘重建)
    - verify_proof + trace_refs 满足 AC5(结晶产物是合法 SKILL.md,含 verify_proof + trace_refs)
    - verified(docs/44 断⑭):结晶时有无**独立验据**(checker verdict 回流,非执行器自报)。
      False 也照样结晶 —— 只是诚实标 `verified: false`,recall 排序吃这个标;None=不写(兼容
      非结晶路径的手工调用,行为同旧)。
    - created_ts(P1.5 灵魂缺口③"周五纪念物"):结晶落盘时刻,写成 `crystallized_ts:`(Unix ts)。
      **加性**:None=不写(老技能/手工调用无此行 → API 如实返 null,不伪造出生记录)。
    """
    args = arguments or []
    fm_lines = [
        "---",
        f"name: {name}",
        f"signature: {signature}",
        f"description: {description}",
    ]
    if when_to_use:
        fm_lines.append(f"when_to_use: {when_to_use}")
    fm_lines.append(f"scope: {scope}")
    fm_lines.append(f"result_reuse: {result_reuse or 'dynamic'}")   # #2 §13:dynamic=命中重跑/stable=可回放
    if verified is not None:
        fm_lines.append(f"verified: {'true' if verified else 'false'}")
    if created_ts is not None:
        fm_lines.append(f"crystallized_ts: {float(created_ts):.3f}")
    if args:
        fm_lines.append("arguments:")
        for a in args:
            fm_lines.append(f"  - name: {a.get('name','')}")
            if a.get("type"):
                fm_lines.append(f"    type: {a['type']}")
            if a.get("description"):
                fm_lines.append(f"    description: {a['description']}")
            if a.get("required"):
                fm_lines.append("    required: true")
    fm_lines.append("verify_proof:")
    fm_lines.append(f"  passed_at: {verify_proof.get('passed_at', 0)}")
    fm_lines.append(f"  verifier: {verify_proof.get('verifier', 'manual')}")
    if verify_proof.get("note"):
        fm_lines.append(f"  note: {verify_proof['note']}")
    fm_lines.append("trace_refs:")
    if trace_refs:
        for r in trace_refs:
            fm_lines.append(f"  - {r}")
    else:
        fm_lines.append("  []")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body.rstrip())
    fm_lines.append("")
    return "\n".join(fm_lines)


_FRONT_VERIFIED = re.compile(r"^verified:\s*(true|false)\s*$", re.MULTILINE)


def mark_skill_verified(skill_md_path: Path) -> bool:
    """独立验收 PASS 回流后,把已落盘 SKILL.md 的 `verified: false` 翻成 true(幂等)。

    docs/44 断⑭:无独立验据时诚实结晶成 `verified: false`;之后 checker 真验过了,
    这个标要跟着事实走(否则"诚实标"变成"永久污点")。只动 frontmatter 里的那一行,
    正文一个字不碰;没有 verified 行(旧技能)→ 不补写(不伪造出生记录),返回 False。
    """
    p = Path(skill_md_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False
    # 只在 frontmatter 块(第一对 --- ... ---)内替换,防正文里恰好有同形行被误改
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    head, tail = text[: end + 4], text[end + 4:]
    m = _FRONT_VERIFIED.search(head)
    if m is None:
        return False
    if m.group(1) == "true":
        return True   # 已是 true,幂等
    new_head = head[: m.start()] + "verified: true" + head[m.end():]
    try:
        p.write_text(new_head + tail, encoding="utf-8")
    except OSError:
        return False
    return True


_FRONT_CRYSTALLIZED_TS = re.compile(r"^crystallized_ts:\s*([0-9][0-9.]*)\s*$", re.MULTILINE)


def read_crystallized_ts(skill_md_text: str) -> Optional[float]:
    """从 SKILL.md 文本读结晶时刻(P1.5 缺口③;/api/skills 暴露用)。

    只在第一对 `--- ... ---` frontmatter 块里找(防正文同形行误读,同 mark_skill_verified
    的纪律);无此行(老技能)/ 坏值 → None(诚实空,不伪造出生记录)。
    """
    text = skill_md_text or ""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    head = text[: end + 4] if end >= 0 else text
    m = _FRONT_CRYSTALLIZED_TS.search(head)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def write_skill_md(skill_dir: Path, skill_md_text: str) -> Path:
    """写到 `<skill_dir>/SKILL.md`。不存在则创建目录。

    `skill_dir` 约定是 `<skills_root>/<skill_name>/`(与 registry.load_skills_dir
    布局一致);传 skills_root 时不会自动加 skill_name 子目录,需 caller 拼好。
    """
    skill_dir = Path(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(skill_md_text, encoding="utf-8")
    return p


# ---- 命名可读性(S):hash 串 → kebab 人类可读名 ----

_KEBAB_RE = re.compile(r"[^a-z0-9]+")
_HASH_NAME_RE = re.compile(r"^skill_[0-9a-f]{6,}$")   # 自动结晶的兜底名(skill_<sig 前 8 位>)


def _kebabize(text: str, *, max_words: int = 5) -> str:
    """把一段自由文本压成 kebab-case 短名(纯确定性,无 LLM):小写、非字母数字→连字符、去空段。"""
    s = _KEBAB_RE.sub("-", (text or "").strip().lower()).strip("-")
    if not s:
        return ""
    words = [w for w in s.split("-") if w][:max_words]
    return "-".join(words)


def is_hash_skill_name(name: str) -> bool:
    """判断一个技能名是否是不可读的 `skill_<hash>` 兜底串(可读名生成只针对这类,老可读名不动)。"""
    return bool(_HASH_NAME_RE.match((name or "").strip()))


def readable_skill_name(
    hint: str,
    sig: str,
    *,
    namer: Optional[object] = None,
    taken: Optional[set] = None,
) -> str:
    """给一个**将要结晶**的技能起人类可读名(kebab-case)。

    - `namer`(可选,同 result_classifier 那次 LLM 一个套路的注入闭包 `(hint)->str`):有就用它出短名;
      异常/空/不合法 → 回退确定性 `_kebabize(hint)`;两者都空 → 回退 `skill_<sig 前 8 位>`(永不裸奔)。
    - 结果只保留 ASCII kebab(纯中文意图 kebab 化后可能为空 → 回退 hash 名),与 skill_index name/
      COMPOSITION `skill:` 引用正则(\\w\\-)一致,进匹配 token + 面板可读。
    - `taken`:已占用名集合(同名加 -2/-3 后缀避冲突,不覆盖已有技能)。**加性**:老技能不改。
    """
    fallback = f"skill_{sig[:8]}"
    cand = ""
    if namer is not None:
        try:
            raw = namer(hint)
            cand = _kebabize(str(raw or ""))
        except Exception:
            cand = ""
    if not cand:
        cand = _kebabize(hint)
    if not cand:
        return fallback
    # 与目录/引用约定对齐:kebab 名不能撞已占用名(加数字后缀,保加性)
    taken = taken or set()
    if cand not in taken:
        return cand
    for i in range(2, 100):
        alt = f"{cand}-{i}"
        if alt not in taken:
            return alt
    return fallback


def crystallize(
    sig: str,
    *,
    name: str,
    description: str,
    body: str,
    when_to_use: str,
    arguments: Optional[list[dict]],
    store: UsageStore,
    verify: VerifyStore,
    skills_dir: Path,
    scope: str = "user",
    now: Optional[float] = None,
    thresholds: "CrystallizeThresholds" = DEFAULT_THRESHOLDS,
    result_reuse: str = "dynamic",   # #2 §13:dynamic=命中重跑/stable=可回放(默认 dynamic,宁重跑不投毒)
    satisfaction: Optional[object] = None,   # docs/44 断⑭:与调用方 maybe_promote 同一份(否则闸门半接)
) -> Skill:
    """M1 v1 简化版 crystallize(同步,参数显式传入):

    1. 调 maybe_promote(两关)
    2. 关过 → 写 SKILL.md
    3. 返回 Skill(内存态)

    异步 4 轮访谈(AskUserQuestion)是 P1 范畴;M1 v1 接受调用方已经访谈完毕
    把结果作为参数传入。这样保证:判定逻辑可测;访谈流程可以独立迭代。

    `now`:用于评分的时间戳(默认 wall clock)。测试需传可控时间。
    `thresholds`:**必须与调用方 maybe_promote 用的同一套**——否则 drive 判 ready、
    这里用默认阈值重判 not-ready 就会抛(VM 实测:配 promote_score=1.0 时 drive 说 ready、
    本函数用默认 3.0 重判 score 2.00<3.0 抛 ValueError → 配置旋钮只被半接)。
    """
    decision = maybe_promote(sig, store, verify, now=now, thresholds=thresholds,
                             satisfaction=satisfaction)
    if decision.kind is not DecisionKind.READY:
        raise ValueError(f"sig {sig} not ready: {decision.reason}")
    proof = verify.latest_proof(sig)
    if proof is None:
        # 关 1 既然过了,这里不该为 None;防御
        raise ValueError(f"sig {sig}: verify gate passed but no proof found")
    verify_proof = {
        "passed_at": proof.at,
        "verifier": "auto" if proof.note != "manual" else "manual",
        "note": proof.note,
    }
    trace_refs = [proof.trace_ref] if proof.trace_ref else []
    # docs/44 断⑭:自报与独立验据分开 —— 结晶时如实标"有没有独立验收 PASS 过"。
    # duck-type 防御:老的/第三方 VerifyStore 没这方法 → None(frontmatter 不写,行为同旧)。
    _has_ind = getattr(verify, "has_independent", None)
    verified_flag: Optional[bool] = bool(_has_ind(sig)) if callable(_has_ind) else None
    # P1.5 缺口③:结晶落盘时刻(wall clock,同 Skill.created_at 口径)——frontmatter 与
    # 内存态用**同一个**戳,不各 time.time() 各的。
    created = time.time()
    skill_md = build_skill_md(
        name=name,
        description=description,
        body=body,
        signature=sig,
        verify_proof=verify_proof,
        trace_refs=trace_refs,
        when_to_use=when_to_use,
        scope=scope,
        arguments=arguments,
        result_reuse=result_reuse,
        verified=verified_flag,
        created_ts=created,
    )
    path = write_skill_md(skills_dir / name, skill_md)
    stats = store.get(sig) or UsageStats()
    # SKILL.md frontmatter 写 "user"/"domain";Skill.scope 是 Literal["personal","domain"]。
    # M1 保守:把 "user" 译作 "personal"(域外的都是 personal),保 schema 一致。
    skill_scope = "personal" if scope == "user" else "domain"
    return Skill(
        name=name,
        manifest={"name": name, "description": description,
                  "when_to_use": when_to_use, "scope": scope,
                  "arguments": arguments or [], "result_reuse": result_reuse,
                  "verify_proof": verify_proof, "trace_refs": trace_refs,
                  "verified": verified_flag,
                  "path": str(path)},
        body=body,
        from_candidate=sig,
        usage=stats,
        verify_proof=verify_proof,
        scope=skill_scope,
        created_at=created,
        evolved_at=created,
    )


__all__ = [
    # 阈值常量
    "PROMOTE_SCORE", "MIN_SUCCESS_RATE", "HALFLIFE_DAYS",
    "MIN_RECENCY_FACTOR", "EVICT_SCORE", "STALE_DAYS",
    "HIGH_FREQ", "GENERALIZED_DISTINCT",
    # 判定
    "DecisionKind", "PromoteDecision", "maybe_promote",
    "usage_score", "success_rate",
    # 结晶
    "build_skill_md", "write_skill_md", "crystallize", "mark_skill_verified",
    "read_crystallized_ts",
    # 命名可读性(S):hash 串 → kebab 可读名
    "readable_skill_name", "is_hash_skill_name",
]
