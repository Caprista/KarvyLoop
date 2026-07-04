"""recall — 召回(快脑/慢脑 路由)（crystallize/recall.py）。

规格:docs/modules/crystallize.md §3 recall.py + §4 HR-7
- 命中已结晶技能 → 快脑(直接调用,不走 ReAct)
- 未命中 → 慢脑(完整 ReAct,通过 forge)
- HR-7:召回时 verify_proof + trace_refs 必须存在(否则 fail-closed 拒)
- M1.5:auto-restore —— 命中归档技能时自动从 store.restore(可逆 evict 的入口)

M1.5 升级:
  - 召回策略 v1.1:intent token 经同义词+月份+停用词归一再算 overlap
  - auto-restore:命中项的 sig 处于 store.archived → 自动调 store.restore,
    把"刚恢复"的事实返回给 caller(RecallHit.restored 字段)
  - 接受 SkillIndex:有就优先用它(快、不读盘);没有再走磁盘兜底
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from karvyloop.registry.skill_lock import reject_tampered_untrusted
from karvyloop.registry.skills import parse_frontmatter

from .signature import _intent_cluster
from .skill_index import SkillIndex
from .store import UsageStore


_STOP_DIGITS = re.compile(r"\d+")
_STOP_PUNCT = re.compile(r"[^\w\s]+")
_STOP_WS = re.compile(r"\s+")
_CJK_RUN = re.compile(r"[一-鿿]+")


# ---- improve.py 写回段 ↔ 重跑上下文(闭环的"读"半条腿)----

def _guidance_headers() -> tuple[str, ...]:
    """improve.py 写回 SKILL.md 的**指导段** header(单一真理来源:improve.py 的常量)。

    这些段是"使用中沉淀的偏好/纠正/评语",**不是方法本身** —— 重跑组装时要单独带上
    并标注"必须遵守",而不是混在 `## Steps` 里被当成"上次的打法"照抄。
    """
    from .improve import KIND_TO_HEADER, ROLE_CRITIQUE_HEADER, ROLE_LESSON_HEADER
    return tuple(KIND_TO_HEADER.values()) + (ROLE_CRITIQUE_HEADER, ROLE_LESSON_HEADER)


# 既非方法也非指导的段(审计痕,喂 LLM 是噪声):从重跑上下文里整段剥掉。
_STRIP_HEADERS = ("## Changelog",)


def split_body_guidance(body: str) -> tuple[str, str]:
    """把 SKILL.md body 拆成 (方法, 指导)。

    - 方法 = `## Goal` / `## Steps` 等原始正文(减去指导段与 Changelog);
    - 指导 = improve.py 写回的 `## Add/Remove/Modify/Preferences/Corrections/
      Role critique/Lessons` 段(含 header,便于 LLM 看清类别)。
    段边界与 improve._insert_into_section 同约定:以 "## " 行为界。
    """
    headers = _guidance_headers()
    method: list[str] = []
    guidance: list[str] = []
    mode = "method"
    for line in (body or "").splitlines():
        if line.startswith("## "):
            s = line.strip()
            if s in headers:
                mode = "guidance"
            elif s in _STRIP_HEADERS:
                mode = "strip"
            else:
                mode = "method"
        if mode == "method":
            method.append(line)
        elif mode == "guidance":
            guidance.append(line)
    return "\n".join(method).strip(), "\n".join(guidance).strip()


def _tokenize(text: str) -> set[str]:
    s = (text or "").lower()
    s = _STOP_DIGITS.sub(" ", s)
    s = _STOP_PUNCT.sub(" ", s)
    s = _STOP_WS.sub(" ", s).strip()
    toks = set(s.split())
    # 中文无空格切不出词:整句一个 token,tags/when_to_use 的中文永不重叠 → 对 CJK 连续段
    # 补 2 字滑窗(bigram),与 cluster.intent_tokens / context.relevance 同思路(无向量,
    # [[matching-is-grep-overlap-tags-no-vectors]])。纯英文文本 0 变化。
    for tok in tuple(toks):
        for seg in _CJK_RUN.findall(tok):
            toks.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


def _is_cjk_pair(tok: str) -> bool:
    """是否 2 字中文对(bigram 产物或 2 字中文词,词面上无法区分)。"""
    return len(tok) == 2 and all("一" <= ch <= "鿿" for ch in tok)


def _load_skill_index(skills_dir: Path) -> list[dict]:
    """从 skills_dir 读所有 SKILL.md,构造轻量索引(无 SkillIndex 时的兜底)。

    返回:[{name, when_tokens, desc_tokens, scope, source, manifest, body, path, sig}, ...]
    """
    out: list[dict] = []
    if not skills_dir.is_dir():
        return out
    for p in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            fm, body = parse_frontmatter(p)
        except OSError:
            continue
        if not fm.name:
            continue
        # 完整性锁:扫盘兜底也不装载被篡改的 untrusted 技能(对抗验收:别让 body 混进召回上下文)
        if reject_tampered_untrusted(skills_dir, p.parent.name, fm.raw or {}):
            continue
        when_tokens = _tokenize(fm.when_to_use)
        desc_tokens = _tokenize(fm.description)
        all_tokens = when_tokens | desc_tokens | _tokenize(" ".join(fm.tags or ()))   # P3-c 语义标签层
        out.append({
            "name": fm.name,
            "when_tokens": when_tokens,
            "desc_tokens": desc_tokens,
            "all_tokens": all_tokens,
            "scope": fm.scope or "user",
            # source=system(出厂方法技能)→ 跨场可见;缺省按 user(隔离语义不变)
            "source": (str((fm.raw or {}).get("source", "")).strip().lower() or "user"),
            "raw": fm.raw or {},
            "body": body,
            "path": str(p),
            "sig": fm.signature,
            "result_reuse": fm.result_reuse or "dynamic",
        })
    return out


@dataclass
class RecallHit:
    """召回命中(供快脑使用)。"""
    name: str
    body: str
    path: str
    score: float  # 匹配度(M1 简版:交集 token 数 / 意图 token 数)
    manifest: dict
    # M1.5:auto-restore 标识 —— 命中项原本在归档、被自动恢复 → True
    # caller 可据此打一条"已复活技能 X"的提示给用户
    restored: bool = False
    sig: str = ""  # 命中 sig(SkillIndex 命中时填;兜底路径无 sig 也允许空)
    # #2 §13:'dynamic'(默认)=命中重跑不回放;'stable'=可回放缓存结果
    result_reuse: str = "dynamic"
    # improve.py 写回的偏好/纠正/评语段(split_body_guidance 抽出;重跑组装必须带上并标注遵守)
    guidance: str = ""
    # Top-K 有界组合(GoSkills Start/Support 契约):**主命中**随身携带的 ≤2 个**支持技能** ——
    # 与主技能/意图 overlap 达阈值、且**语义标签互补**(补新覆盖面,不是重复覆盖同一意图)的次命中。
    # 重跑组装时作"另外你还可以参考这些方法"附加(预算内);拿不准就空 → 退回纯 Top-1(零回归)。
    # 只有**主命中**填这个字段;support 自身的 supports 恒空(不做递归组合,避免包爆炸)。
    supports: list["RecallHit"] = field(default_factory=list)


# ---- Top-K 有界组合(支持技能挑选)----
#
# 保守优先(错误组合比不组合更伤,Voyager/AWM/GoSkills 一致证据:组合是复利来源,但错配是投毒):
#  - 支持技能与**当前意图**的 overlap 覆盖度必须 ≥ _SUPPORT_MIN_INTENT_OVERLAP(与意图真相关);
#  - 且必须**语义互补**:它带来主技能标签集之外的**新单元** ≥ _SUPPORT_MIN_NOVEL 个
#    (否则只是重复覆盖同一意图 = 冗余,不带);
#  - 且不能是主技能本身(同 name/同 sig 去重)。
#  - 最多 _MAX_SUPPORTS 个(有界)。任一条不满足 → 不带该技能(宁空勿滥)。
_MAX_SUPPORTS = 2
_SUPPORT_MIN_INTENT_OVERLAP = 0.34   # 支持技能与意图的覆盖度门(保守:约 1/3 意图 token 命中才够格)
# 语义互补门:支持技能必须补上主技能**未覆盖**的意图面 ≥ 这么多个新匹配单元。
# 意图 token 被 _intent_cluster 归一后**上限 5 个**(signature.py:116),主命中常吃掉 3-4 个 →
# 剩给支持的互补面很窄;故门设 1(补≥1 个新面即算互补,退回冗余=novel 0 才排除)。**保守仍在**:
# ① 还要过 _SUPPORT_MIN_INTENT_OVERLAP 覆盖度门(与意图真相关);② novel=0(纯重复覆盖同一意图)一律排除。
_SUPPORT_MIN_NOVEL = 1
_SUPPORT_METHOD_CLIP = 1200          # 单个支持技能方法段喂进重跑上下文的字符上限(预算内,防挤爆主技能)


def compose_rerun_context(hit: "RecallHit", intent: str) -> str:
    """dynamic 命中后的**重跑上下文组装**(修订闭环的"读"半条腿)。

    把 body 拆成方法 + 指导两块分别标注:方法段照做但重得结果;指导段
    (improve.py 写回的偏好/纠正/role 评语/lessons)**必须遵守**,且与方法冲突时
    以指导为准 —— 否则 `## Remove`("以后别 X")混在"上次证明可行的打法"里,
    LLM 会把它当步骤照抄,写回等于白写。
    """
    method, guidance = split_body_guidance(hit.body if hit is not None else "")
    if not method and not guidance:
        # 兜底只为"无 header 的裸 body"设(理论防御)。方法/指导都拆空但 body 非空,
        # 只可能是 body 全由被剥的审计段组成(如只剩 ## Changelog)—— 审计痕不是方法,
        # 按空方法处理,**绝不**把 Changelog 复活进"已有方法"块(旧日期/旧 trace 会投毒重跑)。
        raw = ((hit.body if hit is not None else "") or "").strip()
        has_stripped = any(line.strip() in _STRIP_HEADERS for line in raw.splitlines())
        method = "" if has_stripped else raw
    parts: list[str] = []
    if method:
        parts.append(
            "[已有方法 —— 上次解决同类任务证明可行的打法,照它的步骤做,"
            "但**必须用当前输入重新得出结果,绝不照搬旧结论/旧数据**]\n"
            f"{method}"
        )
    if guidance:
        parts.append(
            "[使用中沉淀的偏好/纠正/评语 —— 重跑时**必须遵守**;"
            "与上面步骤冲突时,以这里为准]\n"
            f"{guidance}"
        )
    # Top-K 有界组合:主技能之外的**支持技能方法**作"你还可以参考"附加(GoSkills Support 契约)。
    # 只带方法段(不带各自的指导段:那是它们自己场景的纠正,混进来会串味),clip 进预算内。
    supports = getattr(hit, "supports", None) or [] if hit is not None else []
    for i, sup in enumerate(supports[:_MAX_SUPPORTS], 1):
        sup_method, _sup_guidance = split_body_guidance(sup.body or "")
        sup_method = (sup_method or "").strip()
        if not sup_method:
            continue
        clipped = sup_method if len(sup_method) <= _SUPPORT_METHOD_CLIP else (
            sup_method[:_SUPPORT_METHOD_CLIP].rstrip() + " …")
        parts.append(
            f"[另外你还可以参考这个相关技能的方法(支持技能 {i}:{sup.name}) —— "
            "非必须,只在对当前任务有帮助时借鉴,别硬套]\n"
            f"{clipped}"
        )
    parts.append(f"[当前任务]\n{intent}")
    return "\n\n".join(parts)


def load_bound_skills(
    names: list[str],
    *,
    skills_dir: Path,
    skill_index: Optional[SkillIndex] = None,
) -> list[RecallHit]:
    """加载角色**显式绑定**的技能(COMPOSITION.yaml `skills:`)—— 绑定即生效,不靠模糊召回。

    与 recall 的区别:recall 凭意图 token 碰匹配(碰运气);这里按名字**直接取**,
    保证一个角色随身声明的技能一定在场(docs/00 §2.2 + 角色"用不拥有"技能)。
    名字查不到的技能静默跳过(可能被归档/删除;不阻断 drive)。score=1.0(绑定=满信)。
    """
    if not names:
        return []
    want = list(dict.fromkeys(n for n in names if n))  # 去重保序
    by_name: dict[str, dict] = {}
    if skill_index is not None and len(skill_index) > 0:
        for entry in skill_index.all():
            if entry.name in want and entry.name not in by_name:
                by_name[entry.name] = {"path": entry.path, "sig": entry.sig}
    # 索引没覆盖到的,扫盘兜底(按目录名 == 技能名约定)
    for nm in want:
        if nm in by_name:
            continue
        p = Path(skills_dir) / nm / "SKILL.md"
        if p.is_file():
            by_name[nm] = {"path": str(p), "sig": ""}
    out: list[RecallHit] = []
    for nm in want:
        info = by_name.get(nm)
        if info is None:
            continue
        try:
            fm, body = parse_frontmatter(Path(info["path"]))
        except OSError:
            continue
        # 完整性锁:绑定直取(含扫盘兜底)同样不装载被篡改的 untrusted 技能。
        # 对抗验收点破的尖角:被篡改的技能被索引拒收后,这里的扫盘兜底反而会接住它 —— 必须同门。
        if reject_tampered_untrusted(Path(info["path"]).parent.parent, nm, fm.raw or {}):
            continue
        out.append(RecallHit(name=nm, body=body, path=info["path"], score=1.0,
                             manifest=fm.raw or {}, sig=info.get("sig", ""),
                             result_reuse=fm.result_reuse or "dynamic",
                             guidance=split_body_guidance(body)[1]))
    return out


def _select_supports(
    main: "RecallHit",
    main_overlap: set[str],
    scored: list[dict],
) -> list["RecallHit"]:
    """从过门候选池里挑主命中的**支持技能**(Top-K 有界组合;GoSkills Start/Support 契约)。

    保守判据(错误组合比不组合更伤 → 阈值宁高勿滥,拿不准就不带 → 退回纯 Top-1):
      ① 不是主技能本身(同 name / 同 sig 去重);
      ② 与**当前意图**的覆盖度 ≥ _SUPPORT_MIN_INTENT_OVERLAP(与意图真相关,不是碰巧一个词);
      ③ **语义互补**:它与意图的交集里,落在**主技能未覆盖**面(main_overlap 之外)的新单元
         ≥ _SUPPORT_MIN_NOVEL —— 只重复覆盖主技能已覆盖的那部分意图 = 冗余,不带
         (这正是"覆盖重复的不带、退 Top-1"的判据)。
    命中面大的支持技能优先(novel 多 → intent_cov 高),最多 _MAX_SUPPORTS 个。
    """
    main_name = main.name
    main_sig = main.sig or ""
    picked: list[tuple[float, int, RecallHit]] = []
    for order, s in enumerate(scored):
        c = s["cand"]
        if c["name"] == main_name:
            continue
        if main_sig and c.get("sig", "") == main_sig:   # 同一技能不同索引路径去重
            continue
        if s["intent_cov"] < _SUPPORT_MIN_INTENT_OVERLAP:
            continue
        # 语义互补:该候选命中意图、但落在主技能**没覆盖**的意图面上的新单元数
        novel = len(s["overlap"] - main_overlap)
        if novel < _SUPPORT_MIN_NOVEL:
            continue   # 只重复覆盖主技能已覆盖的意图 → 冗余,不带(保守)
        # 排序键:新覆盖面越大越优先(novel),打平看意图覆盖度(intent_cov),再退回稳定顺序。
        picked.append(((-float(novel), -s["intent_cov"], order), order, RecallHit(
            name=c["name"], body=c["body"], path=c["path"],
            score=s["intent_cov"], manifest=c.get("raw", {}), sig=c.get("sig", ""),
            result_reuse=c.get("result_reuse", "dynamic"),
            guidance=split_body_guidance(c["body"])[1],
        )))
    picked.sort(key=lambda t: t[0])
    return [h for _k, _o, h in picked[:_MAX_SUPPORTS]]


def recall(
    intent: str,
    *,
    skills_dir: Path,
    scope: str = "user",
    store: Optional[UsageStore] = None,
    skill_index: Optional[SkillIndex] = None,
    prefer: Optional[list[str]] = None,
    satisfaction: Optional[object] = None,
) -> Optional[RecallHit]:
    """按意图召回一个最匹配的已结晶技能;没命中 → None(走慢脑)。

    匹配规则 v1.1:intent 经 normalize 后取 token 集(中文补 CJK bigram),跟每个 skill 的
    when_to_use+description+tags token 集求交集;交集非空 + scope 一致即命中
    (**例外**:source=system 的出厂方法技能跨场可见,不受 scope 过滤);
    取交集覆盖度最大者。

    skill_index:有就优先用它(快、不读盘);没有走 _load_skill_index 兜底。
    store:有就启用 auto-restore —— 命中项若已归档,自动调 store.restore。
    prefer:当前角色**绑定**的技能名 —— 这些技能即便意图 token 弱匹配也给 +0.5 加权,
      让角色随身声明的技能在打平/接近时胜出(绑定优先于碰运气,但不绕过 scope)。
    """
    intent_tokens = _tokenize(_intent_cluster(intent))
    if not intent_tokens:
        return None
    prefer_set = set(prefer or [])

    candidates: list[dict] = []
    if skill_index is not None and len(skill_index) > 0:
        for entry in skill_index.all():
            # scope 过滤:user 技能严格同场(隔离语义不变);**system 来源放行全场** ——
            # 出厂方法技能是镜像资产(人人一样、不含用户私数据),业务域(scope=domain)
            # 正是最该用 data-analyst 这类方法技能的场,不该被 scope:user 挡在门外。
            if entry.scope != scope and getattr(entry, "source", "user") != "system":
                continue
            try:
                _fm, body = parse_frontmatter(Path(entry.path))
            except OSError:
                continue
            # P3-c 三层匹配的语义层:LLM 语义标签并进匹配集(词面 overlap 之上的语义命中面;
            # 标签是 daily 慢侧打的,无向量 —— [[matching-is-grep-overlap-tags-no-vectors]])
            all_tokens = (_tokenize(entry.when_to_use) | _tokenize(entry.description)
                          | _tokenize(" ".join(_fm.tags or ())))
            candidates.append({
                "name": entry.name,
                "body": body,
                "path": entry.path,
                "all_tokens": all_tokens,
                "raw": _fm.raw or {},
                "sig": entry.sig,
                # §13:SkillIndex 不存 result_reuse,从盘上 frontmatter 读(否则恒 dynamic、stable 永不回放)
                "result_reuse": _fm.result_reuse or "dynamic",
            })
    else:
        for c in _load_skill_index(skills_dir):
            # 同上:system 来源放行全场,user 技能严格同场
            if c["scope"] != scope and c.get("source", "user") != "system":
                continue
            candidates.append({
                "name": c["name"],
                "body": c["body"],
                "path": c["path"],
                "all_tokens": c["all_tokens"],
                "raw": c["raw"],
                "sig": c.get("sig", ""),
            })

    best: Optional[RecallHit] = None
    # 排序键 = (意图匹配主分, 满意度裁决分, 独立验据标)。**字典序**:意图匹配**绝对优先**,
    # 满意度**只在意图打平时**裁决 —— 绝不能盖过真实的匹配差(对抗验收 MEDIUM:+0.3 加权曾能
    # 翻盘 20-30% 的匹配差 = 召回错技能;改成严格平手裁决兑现"只在打平时")。第三键(docs/44
    # 断⑭):frontmatter `verified: true`(独立验收 PASS 过)> false/缺标 —— 只在前两键全平
    # 时破平,Trace 派生的真实使用信号(满意度)仍优先于出生标。
    best_key: tuple[float, float, float] = (-1.0, -1.0, -1.0)
    best_overlap: set[str] = set()   # 主命中的意图交集(支持技能语义互补判据的参照)
    scored: list[dict] = []          # 通过门槛的候选(带 overlap/覆盖度),Top-K 组合从这里挑支持技能
    for c in candidates:
        overlap = intent_tokens & c["all_tokens"]
        if not overlap:
            continue
        # CJK bigram 门(借 cluster._MIN_SHARED 同思路):交集若**只**是 1 个 2 字中文对,
        # 不算信号 —— 否则"报表"这种通用词会把无关意图吸进技能(实证:"生成报表"被
        # data-analyst 的 tag 单词截胡 → 挤掉本该结晶的新技能)。≥2 个共享单元才命中;
        # 英文整词/长词命中不受此门(0 回归)。
        if len(overlap) == 1 and _is_cjk_pair(next(iter(overlap))):
            continue
        primary = len(overlap) / max(1, len(intent_tokens))
        if c["name"] in prefer_set:
            primary += 0.5  # 绑定优先仍属"意图/归属"主分(不绕 scope,只加权)
        # docs/40:满意度回到行为 —— 意图打平时,role 评出来"更管用"的技能胜出(新近度加权,抗滞后)。
        # 无满意度 store / 无样本 → 0(优雅降级);异常吞掉不拖垮召回。
        secondary = 0.0
        if satisfaction is not None and c.get("sig"):
            try:
                # 置信分(贝叶斯收缩):用得少的技能往先验缩,不靠几次走运的高均值抢召回。
                sat = satisfaction.confidence_overall(c["sig"])
                if sat is not None:
                    secondary = float(sat)
            except Exception:
                pass
        # 独立验据标(断⑭"诚实结晶"):简易 YAML 解析回来是字符串,"true" 才算有独立验据;
        # 缺标(老技能/未验)与 false 同级 —— 都是"没有独立验据",不追溯惩罚也不伪造资历。
        verified_rank = 1.0 if str((c.get("raw") or {}).get("verified", "")).strip().lower() == "true" else 0.0
        key = (primary, secondary, verified_rank)
        # Top-K 组合候选池:记下每个过门候选的 overlap 与"意图覆盖度"(len(overlap)/len(intent)),
        # 供选主命中后挑支持技能(不含 prefer 加权 —— 覆盖度看的是与意图的真实相关,别被绑定加权虚高)。
        intent_cov = len(overlap) / max(1, len(intent_tokens))
        scored.append({"cand": c, "overlap": overlap, "intent_cov": intent_cov,
                       "all_tokens": c["all_tokens"], "key": key})
        if key > best_key:
            best = RecallHit(
                name=c["name"],
                body=c["body"],
                path=c["path"],
                score=primary,
                manifest=c["raw"],
                sig=c.get("sig", ""),
                result_reuse=c.get("result_reuse", "dynamic"),
                guidance=split_body_guidance(c["body"])[1],
            )
            best_key = key
            best_overlap = overlap

    # Top-K 有界组合:选完主命中后,从其余过门候选里挑 ≤2 个**语义互补**的支持技能(保守,宁空勿滥)。
    if best is not None:
        best.supports = _select_supports(best, best_overlap, scored)

    # auto-restore:命中项若在 store 归档集合里 → 翻出来
    if best is not None and store is not None and best.sig:
        if store.is_archived(best.sig):
            store.restore(best.sig)
            best.restored = True
        # 拍 9:每次召回命中都 +1(recall_count 是真"用进"信号,
        # 比 usage_count 更准;evict 应优先看这个)。
        store.recall_count_inc(best.sig)
    return best


__all__ = ["RecallHit", "recall", "load_bound_skills",
           "split_body_guidance", "compose_rerun_context"]
