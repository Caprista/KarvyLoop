"""roles/experience — 角色经验沉淀(role 越用越懂它那个域,docs/54 模块1 Top2)。

**病灶**(雷达实锤 docs/54-module-radar-b §模块1):角色是"石头人"——identity/soul/COMMITMENT
建后全靠人工编辑,**没有任何"角色用久了从反馈里长出经验"的机制**。decision_pref 是全局
用户级、不沉淀到 role。业界(Generative Agents 记忆流+反思 / Voyager 技能累积 / CoALA 记忆
分层)都让 agent 从经验演化;我们的角色是唯一不进化的实体 → 直接拖楔子飞轮("越用越懂你"
在角色维度不成立)。

**这是什么(诚实边界)**:是**经验记忆**,不是"角色 RLHF 重训"。角色不会"重写自己"——它
**积累了这个域的可召回经验**(什么方法在这个域管用 / 用户在这个域的偏好 / 踩过的坑),下次在
**同域**干活时带着这些经验。**绝不自动改 identity/soul/COMMITMENT 七文件**(那是人设根,经验
是 belief 层的叠加增量,不覆盖人设)。

**对齐宪法(不另起炉灶,复用既有设施)**:
- 载体 = 一种 Belief(复用认知库,同 decision_pref 决策):`provenance.source == "role_experience"`,
  带 `applies={"domain","role"}` —— 就是 §2.6 的**域私有认知按(域,角色)隔离**。
- 冲突消解走既接线的 supersede(`cognition.conflict.run_supersede_pass`):同一域同一角色矛盾/
  更新的经验自动打 `invalid_at`(失效不删,provenance_rank 把关),不另造。
- 写入走 `MemoryManager.write`(provenance/freshness/去重/落盘都在那)。
- 解析**宁空勿毒**(`llm-output-parser-must-refuse-garbage`):镜像 ingest.parse_facts。
- **无向量**(铁律):召回用 overlap_score 词面+CJK bigram(`matching-is-grep-overlap-tags-no-vectors`)。

**保守触发(避免 Mem0 式垃圾膨胀,宁少勿滥)**:不是每次跑完都沉淀。只在有**明确可复用信号**时:
任务成功且有非平凡方法 / 用户给了纠正反馈 / 踩坑被记。空信号 → 零 LLM、零写入(`should_distill` 门)。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from karvyloop.schemas.cognition import Belief

logger = logging.getLogger(__name__)

# Belief.provenance.source 标记(召回/展示据此筛出角色经验,与普通事实/决策偏好区分)
ROLE_EXPERIENCE_SOURCE = "role_experience"
_KINDS = ("method", "preference", "pitfall")   # 方法管用 / 用户偏好 / 踩坑

# 蒸馏材料里单条经验封顶(挡病态长文;与 ingest 一致)
_MAX_EXP_LEN = 300


# ---- 角色经验编译器 system:从一次任务的结果里抽"这个角色在这个域学到的"----
ROLE_EXPERIENCE_SYSTEM = (
    "你是 KarvyLoop 的角色经验编译器。下面是某个**角色**在某个**业务域**里刚完成的一次任务"
    "(需求 + 它的做法/产出 + 验收结论,可能还有用户的纠正反馈)。\n"
    "从中抽出**这个角色在这个域里学到的、能复用到将来同域同类任务**的经验。三类:\n"
    "- method(方法):什么做法在这个域管用(如「查这个域的数据先核对来源再引用」)。\n"
    "- preference(偏好):用户在这个域偏好什么(如「这个域的报告用户要一句话结论开头」)。\n"
    "- pitfall(踩坑):这次踩到、下次要避开的坑(如「这个域的 API 分页从 0 开始」)。\n"
    "**严格保守,宁少勿滥**:只抽**确有依据、非平凡、能泛化到将来**的;只对这一个具体任务成立的"
    "一次性细节别抽;泛泛空话(如「要认真」)别抽。没有值得沉淀的就输出 []。\n"
    "每条短、自足、脱离上下文也读得懂(别用「它/这个」指代)。\n"
    "严格输出 JSON 数组,元素 "
    "{\"content\":\"<一条经验>\",\"kind\":\"method|preference|pitfall\"};"
    "没有可抽的输出 []。不要输出 JSON 以外的任何文字。"
)


# ---- 保守触发门(在跑 LLM 之前,零成本判"这次值不值得蒸馏")----


@dataclass
class TaskOutcomeSignal:
    """一次角色任务收尾的可沉淀信号(委派执行 / 圆桌收敛都可产出)。"""
    role: str                       # 谁跑的(角色 id)
    domain: str                     # 在哪个域跑的(空/l0 = 不沉淀:通用层不做角色经验)
    requirement: str                # 任务需求
    result: str = ""                # 角色的做法/产出摘要
    success: bool = False           # 任务是否成功(失败任务不沉"方法管用",除非有纠正/踩坑)
    verified: bool = False          # 是否过了独立验收门(过门 = 更可信,非平凡方法值得沉)
    correction: str = ""            # 用户的纠正反馈(最富信号:用户示范了"该怎么做")
    trace_ref: str = ""             # Trace 溯源(provenance)
    ts: float = 0.0


def should_distill(sig: TaskOutcomeSignal) -> bool:
    """**保守门**(避免垃圾膨胀,宁少勿滥):有明确可复用信号才蒸馏,否则零 LLM 零写入。

    沉淀条件(满足任一):
    - 用户给了纠正反馈(`correction` 非空):最强信号,用户亲手示范了标准。
    - 任务成功**且**过了独立验收门(`success and verified`):有验证过的非平凡做法值得记。

    **不沉**:纯失败无纠正(失败=role planning 不健壮,该 role replan 不是记"方法",
    见 `atom-failure-fallback-is-role-replan`)/ 无域(l0 通用层不做角色经验,跨域隔离前提)。
    """
    if not (sig.role or "").strip():
        return False
    dom = (sig.domain or "").strip()
    if not dom or dom == "l0":
        return False   # 无域 → 角色经验无处归属(跨域隔离要 applies.domain);l0 通用层不做
    if (sig.correction or "").strip():
        return True    # 用户纠正 = 最富信号,即便任务没"成功"也值得记(用户示范了标准)
    return bool(sig.success and sig.verified)


# ---- 解析(镜像 ingest.parse_facts / decision_pref.parse_decision_prefs:JSON 严格、宁空勿毒)----


def parse_experiences(text: str) -> list[dict]:
    """解析经验编译器输出 → [{"content","kind"}]。**宁空勿毒**:
    只剥外层 fence → json.loads;像 JSON(以 [ / {)却解析失败 → [](绝不 prose 兜底,
    经验投毒会歪掉角色未来所有同域行为);非 JSON prose 不抽。
    """
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
        for key in ("experiences", "items", "data", "beliefs"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data] if data.get("content") else []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        c = (item.get("content") or "").strip()
        if not c or len(c) > _MAX_EXP_LEN:
            continue
        kind = str(item.get("kind", "method"))
        out.append({"content": c, "kind": kind if kind in _KINDS else "method"})
    return out


# ---- 经验 Belief 约定(载体复用认知库,(域,角色)隔离)----


def make_experience_belief(
    content: str, kind: str, *, domain: str, role: str,
    trace_ref: str = "", now: Optional[float] = None,
) -> Belief:
    """构造一条角色经验 Belief。

    - `provenance.source = "role_experience"` —— 召回/展示据此筛出、标"来源=经验"。
    - `provenance.applies = {"domain","role"}` —— **域私有认知按(域,角色)隔离**(§2.6):
      只在**它自己的域+角色**召回,A 域角色经验绝不漏到 B 域(跨域隔离既定纪律)。
    - `scope="domain"` —— 域专属认知层(purge_domain 删域时随域清)。
    """
    if now is None:
        now = time.time()
    k = kind if kind in _KINDS else "method"
    return Belief(
        content=content.strip(),
        provenance={
            "source": ROLE_EXPERIENCE_SOURCE, "agent": role, "ts": now,
            "kind": k, "trace_ref": (trace_ref or "").strip(),
            "applies": {"domain": domain, "role": role},
        },
        freshness_ts=now,
        scope="domain",
    )


def is_role_experience(b: Belief) -> bool:
    """是不是一条角色经验条(供召回筛选 / 面板展示 / supersede 排除对齐)。"""
    return bool(getattr(b, "provenance", None)) and b.provenance.get("source") == ROLE_EXPERIENCE_SOURCE


# ---- LLM 蒸馏(镜像 ingest.compile_material / decision_pref.compile_decisions)----


def _format_signal(sig: TaskOutcomeSignal) -> str:
    """把一次任务收尾拼成一段材料喂经验编译器。"""
    parts = [f"域:{sig.domain}", f"角色:{sig.role}", f"任务需求:{sig.requirement.strip()}"]
    if sig.result.strip():
        parts.append(f"角色的做法/产出:{sig.result.strip()[:1500]}")
    parts.append(f"验收结论:{'成功且通过独立验收' if (sig.success and sig.verified) else ('成功' if sig.success else '未成功')}")
    if sig.correction.strip():
        parts.append(f"用户的纠正反馈(最重要):{sig.correction.strip()[:800]}")
    return "\n".join(parts)


async def distill_experience(sig: TaskOutcomeSignal, *, gateway: Any,
                             model_ref: str = "") -> list[dict]:
    """跑一次受限 LLM(无工具)→ 候选经验 list。复用 gateway.complete(同 ingest/decision_pref)。

    调用方**必须先过 `should_distill`**(本函数不重复判门,但空材料/空信号会自然返 [])。
    """
    material = _format_signal(sig)
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
    # P1b:角色经验蒸馏 token 归到 role_experience(此前记 unknown,docs/68 P0-9 长尾)
    from karvyloop.llm.token_ledger import token_source
    out = ""
    with token_source("role_experience"):
        async for ev in gateway.complete(
            [{"role": "user", "content": material}], [], ref,
            system=SystemPrompt(static=[ROLE_EXPERIENCE_SYSTEM]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    return parse_experiences(out)


async def sediment_experience(sig: TaskOutcomeSignal, *, mem: Any, gateway: Any,
                              model_ref: str = "", now: Optional[float] = None,
                              conflict_sink: Optional[list] = None) -> list[Belief]:
    """**沉淀总入口**:一次角色任务收尾 → 保守门 → LLM 蒸馏 → 写 role-scoped Belief → supersede。

    - 保守门 `should_distill`:无信号(纯失败无纠正 / 无域)→ 零 LLM 零写入,返 []。
    - 蒸馏出 0 条(宁空勿毒 / LLM 判无可沉)→ 不写。
    - 写入走 `mem.write`(provenance/freshness/去重/落盘在那);写完跑一轮 supersede
      (同域同角色矛盾/更新的旧经验打失效,复用刚接线的冲突消解,不另造)。
    - **D2**:角色经验是人审档(role_experience 受保护)—— 新经验要推翻旧经验时不自动失效,
      supersede 把冲突收进 `conflict_sink`(调用方 console 层升 H2A 冲突卡让你裁;沉淀本身不依赖 console)。
    - 全程 fail-soft:任何异常吞掉只打日志(沉淀是增益,绝不拖垮任务收尾主流程)。
    返回写入的经验 Belief 列表(供调用方 log / 面板;空 = 没沉)。
    """
    if now is None:
        now = time.time()
    try:
        if mem is None or gateway is None:
            return []
        if not should_distill(sig):
            return []
        cands = await distill_experience(sig, gateway=gateway, model_ref=model_ref)
        if not cands:
            return []
        written: list[Belief] = []
        for c in cands:
            content = (c.get("content") or "").strip()
            if not content:
                continue
            b = make_experience_belief(content, c.get("kind", "method"),
                                       domain=sig.domain, role=sig.role,
                                       trace_ref=sig.trace_ref, now=now)
            try:
                mem.write(b)
                written.append(b)
            except Exception as e:
                logger.warning(f"[role_experience] 写经验失败(role={sig.role} domain={sig.domain}): {e}")
        if written:
            # 冲突消解:同域同角色矛盾/更新旧经验 → 打失效(失效不删)。只在本(域,角色)池内比对,
            # 不跨域(supersede 按 scope 取旧池,role-experience 的 applies 天然把它们圈在本域;
            # 但为稳妥,写入的都是 scope="domain",supersede 只比 domain scope 内的条)。
            try:
                from karvyloop.cognition.conflict import run_supersede_pass
                sup = await run_supersede_pass(written, mem=mem, gateway=gateway,
                                               model_ref=model_ref, now=now)
                # D2:角色经验受保护 → 推翻旧经验的冲突收给调用方升 H2A 卡(不自动失效)
                if conflict_sink is not None:
                    conflict_sink.extend(sup.get("conflicts") or [])
            except Exception as e:
                logger.warning(f"[role_experience] supersede 失败(原库不动): {e}")
        if written:
            logger.info(f"[role_experience] 沉淀 {len(written)} 条(role={sig.role} domain={sig.domain})")
        return written
    except Exception as e:
        logger.warning(f"[role_experience] 沉淀异常(不阻断任务收尾): {e}")
        return []


# ---- 召回:同域同角色注入(复用 overlap_score,(域,角色)隔离,跨域不漏)----


def _experience_applies(b: Belief, *, domain: str, role: str) -> bool:
    """一条经验是否属于当前(域,角色)。**跨域隔离 + 镜像兵法**(docs/78 §3.6 谓词①):

    - 域私有经验(applies.domain 非空):域和角色都必须匹配 —— A 域经验绝不进 B 域;
    - **镜像兵法**(applies.domain 空 = 升层产物):同角色在**任何**域都浮出 ——
      角色带着"你的一般方法"跨域干活,这正是回流的全部意义;
    - role 永远必须相等(兵法锁角色,跨角色共享是另一个升层判定,不搭车)。
    """
    if not (domain and role):
        return False   # 无域或无角色情境 → 不召角色经验(通用层不掺角色私有经验)
    ap = b.provenance.get("applies") or {}
    return ap.get("domain", "") in ("", domain) and ap.get("role", "") == role


def recall_role_experiences(beliefs: list[Belief], *, query: str = "", domain: str = "",
                            role: str = "", limit: int = 6,
                            include_invalid: bool = False) -> list[Belief]:
    """筛出属于当前(域,角色)的经验,按相关性·新鲜度排序、封顶 limit。**无向量**。

    - 相关性:`context.relevance.overlap_score`(词面+CJK bigram,与知识/决策召回同款,不漂移)。
    - 失效过滤:被 supersede/归档打了 `invalid_at` 的默认不召(与 recall_block 同规则)。
    - (域,角色)隔离:`_experience_applies` 双匹配 —— 跨域绝不漏。
    """
    from karvyloop.context.relevance import overlap_score
    matched = [
        b for b in beliefs
        if is_role_experience(b)
        and (include_invalid or getattr(b, "invalid_at", None) is None)
        and _experience_applies(b, domain=domain, role=role)
    ]
    matched.sort(key=lambda b: (overlap_score(query, b.content), b.freshness_ts), reverse=True)
    return matched[:max(0, limit)]


def experience_block(beliefs: list[Belief], *, query: str = "", domain: str = "",
                     role: str = "", limit: int = 6) -> str:
    """召回本(域,角色)相关经验 → 拼成注入 governance 的经验块(空 → "")。

    让角色带着"我在这个域学到的"干活。**只偏置、是经验不是硬规则**(不覆盖人设七文件)。
    封顶但**绝不静默漏**:超过 limit 明示"还有 N 条(已按相关性挑最相关的)"。
    """
    exps = recall_role_experiences(beliefs, query=query, domain=domain, role=role, limit=limit + 1)
    if not exps:
        return ""
    shown = exps[:max(0, limit)]
    label = {"method": "方法", "preference": "偏好", "pitfall": "踩坑"}
    lines = [f"【你(「{role}」)在本域积累的经验(这些是经验偏置,不是硬规则;最终仍按人设/治理来)】"]
    for b in shown:
        k = label.get(b.provenance.get("kind", ""), "经验")
        # 分层展示(docs/78 §3.6):镜像兵法(升层产物,无 domain)标"通用" —— 你的一般方法 vs 本域学的
        _ap = b.provenance.get("applies") or {}
        tag = f"{k}·通用" if not _ap.get("domain") else k
        lines.append(f"- [{tag}] {b.content}")
    dropped = len(exps) - len(shown)
    if dropped > 0:
        lines.append(f"(还有 {dropped} 条本域经验未展开,已按与本次相关性挑了最相关的)")
    return "\n".join(lines)


def collect_role_experiences(mem: Any, *, domain: str = "", role: str = "",
                             query: str = "", limit: int = 6) -> str:
    """从 MemoryManager 取全部 Belief → 召回本(域,角色)经验块。governance 装配接缝调这个。

    空 mem / 空域角色 → ""(通用层/私聊不注入角色经验)。fail-soft:异常返 ""。
    """
    if mem is None or not (domain and role):
        return ""
    try:
        beliefs: list[Belief] = []
        idx = mem.index
        for scope in ("personal", "domain"):
            for b in idx.all(scope):
                beliefs.append(b)
        return experience_block(beliefs, query=query, domain=domain, role=role, limit=limit)
    except Exception:
        return ""


__all__ = [
    "ROLE_EXPERIENCE_SOURCE", "ROLE_EXPERIENCE_SYSTEM",
    "TaskOutcomeSignal", "should_distill",
    "parse_experiences", "make_experience_belief", "is_role_experience",
    "distill_experience", "sediment_experience",
    "recall_role_experiences", "experience_block", "collect_role_experiences",
]
