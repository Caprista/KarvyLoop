"""create_atom:role 自造原子(docs/02 §15.5,Hardy 显式创建路径)。

role 判定**无现有 atom 可用**时显式调用——把"自造能力"从隐式(事后从 Trace 猜=猜不准、产物是
噪声)变成**显式**(调用即信号、产物即结构化 AtomSpec),这是它不空壳的关键。链条:

  search-first(查公共池=消费路径 + 防重复造)→ 没有则 LLM 合成 AtomSpec(宁空勿毒)
  → 过合并闸(近义则复用,不无脑加)→ 出生即 **provisional / origin="self_created"**(④ 生命周期:
    被复用转正、孤儿撤)。

沉淀(`sediment_self_created`):任务被**认可** → 进创建 role 的 composition(成被引用资产、可被别的
role 搜到复用)+ 留 provisional(靠跨 role 复用转正);任务被**拒** → 立即撤(remove)。

安全:自造 atom = provisional + 合并闸 + verify 门(执行路径既有)三层兜,别让一句话铸坏 atom 进公共池。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from karvyloop.registry.tool import build_tool

_WORD = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
_ID_MAXLEN = 64


def _toks(s: str) -> set:
    """token 集 = ascii 词 + **CJK 字符双连(bigram)**。后者补"中文切不出词"的洞 ——
    零模型、零 LLM:把"翻译中文文本"切成 {翻译,译中,中文,文文,文本},lexical 重叠对中文也 work。
    catch 表面近义(换措辞);深层改写交给 daily ③ consolidation(chat 模型,不需 embedding)。"""
    low = (s or "").lower()
    toks = set(_WORD.findall(low))
    for run in _CJK_RUN.findall(low):
        if len(run) == 1:
            toks.add(run)
        else:
            for i in range(len(run) - 1):
                toks.add(run[i:i + 2])
    return toks


def _overlap(a: str, b: str) -> float:
    """token 集**包含度**(交集 / 较小集)—— search-first / 近义判定用的轻量相似度(无 embedding)。
    用包含度而非 Jaccard:问"较小的那串是否大部分被覆盖",对停用词稀释更稳。
    注:基于 ascii token,中文描述切不出词→相似度偏弱(已知局限,语义靠 ③ LLM 合并闸 + ④ 孤儿撤兜底;
    embedding 聚类是备选,docs/14 §11.2)。"""
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def search_pool(desc: str, atom_registry: Any, *, threshold: float = 0.5) -> Optional[str]:
    """查公共池里能干这事的现有原子(消费路径 + 防重复)。返回最佳匹配 id 或 None。

    只在 ≥threshold 时返回;比对原子的 prompt(用途)和 id;**正式原子优先于 provisional**。
    **防假命中(#3)**:要求至少**共享 ≥2 个 token** 才算匹配 —— 否则两个不相干描述偶然只共享
    一个内嵌 ascii 词(PDF/CSV/API…)就会以包含度 1.0 误判同义、把错原子塞给 role。
    """
    dq = _toks(desc)
    if len(dq) < 1:
        return None
    best_id, best_score, best_provisional = None, threshold, True
    for a in atom_registry.list_all():
        prov = bool(getattr(a, "provisional", False))
        # 标签可能是 "en|zh" 双语编码 → 拆出两段都进匹配文本(en 匹配键 + zh 让中文 desc 也能命中)
        _tag_parts: list = []
        for _t in (getattr(a, "tags", []) or []):
            _tag_parts.extend(str(_t).split("|"))
        _tags_txt = " ".join(p for p in _tag_parts if p.strip())
        for cand in (getattr(a, "prompt", "") or "", getattr(a, "id", "") or "", _tags_txt):
            tc = _toks(cand)
            shared = dq & tc
            if len(shared) < 2:            # 单个偶然共享 token 不算命中
                continue
            s = len(shared) / min(len(dq), len(tc))
            if s > best_score or (s >= best_score and best_provisional and not prov):
                best_id, best_score, best_provisional = a.id, s, prov
    return best_id


_SYNTH_SYSTEM = (
    "你把一句『需要什么能力』的描述,凝成一个**单一职责**的可复用原子规格。"
    "只输出严格 JSON(无解释、无 markdown fence):"
    '{"id": "<字母数字下划线的短名>", "prompt": "<这个原子做什么、怎么做的指令>", '
    '"tools": ["<只从允许工具里选>"], '
    '"tags": [{"en": "<英文小写归一化词,如 web/search/translate>", "zh": "<对应中文,如 网页/检索/翻译>"}]}。'
    "允许的工具:run_command, read_file, write_file, edit_file, web_search, web_fetch。"
    "tags 是这个能力的**双语概念标签**(2-5 个):en 给跨语言语义匹配用(务必归一化小写、别用整句),"
    "zh 是给中文用户看的对应中文词。凝不出单一职责的可复用能力就输出 {}。"
)
_REAL_TOOLS = {"run_command", "read_file", "write_file", "edit_file", "web_search", "web_fetch"}


# 双语标签编码(#3b tag 系统):registry 把 tags 存成 list[str],所以每个标签编成 **"en|zh"** 紧凑串
# (en 是语言中立匹配键、小写归一;zh 给中文用户看)。前端按 `|` 拆分双语显示,缺 zh 回退 en(向后兼容
# 旧的纯英文串)。这样既留在 str 形态(registry-safe),又不丢中文——守 grep+overlap+LLM 标签,无向量。
def _tag_en(tag: Any) -> str:
    """取一个标签的**英文匹配键**(en 段;旧纯英文串就是自身;dict 取 en)。小写归一,给 overlap 用。"""
    if isinstance(tag, dict):
        return str(tag.get("en", "") or "").strip().lower()
    return (str(tag).split("|", 1)[0] or "").strip().lower()


def _norm_tags(raw: Any) -> list:
    """归一化标签成 **"en|zh"** 双语串:接受旧英文串 / {"en","zh"} dict / "en|zh" 串。

    en 小写归一(语言中立匹配键),zh 保留中文;缺 zh 回退 en(向后兼容)。
    去空、按 en 去重保序、每段 ≤32 字、≤8 个。
    """
    out: list = []
    seen: set = set()
    for t in (raw or []):
        if t is None:
            continue
        if isinstance(t, dict):
            en = str(t.get("en", "") or "").strip().lower()[:32]
            zh = str(t.get("zh", "") or "").strip()[:32]
        else:
            s = str(t).strip()
            if "|" in s:                       # 已是 "en|zh" 编码
                en, _, zh = s.partition("|")
                en, zh = en.strip().lower()[:32], zh.strip()[:32]
            else:
                en, zh = s.lower()[:32], ""
        if not en and not zh:
            continue
        if not en:
            en = zh.lower()[:32]
        if not zh:
            zh = en                            # 缺 zh 显 en(向后兼容)
        if en in seen:
            continue
        seen.add(en)
        out.append(f"{en}|{zh}")
    return out[:8]


def _tag_overlap(a: list, b: list) -> float:
    """标签集相似(交集/较小集),按 **en 匹配键**比对,要求**共享 ≥2 标签**才算
    (避免单个宽标签如 web 误判同义)。双语编码不影响匹配——只看语言中立的 en 段。"""
    sa = {_tag_en(t) for t in (a or []) if _tag_en(t)}
    sb = {_tag_en(t) for t in (b or []) if _tag_en(t)}
    shared = sa & sb
    if len(shared) < 2:
        return 0.0
    return len(shared) / min(len(sa), len(sb))


def _parse_spec(text: str) -> Optional[dict]:
    """宁空勿毒:严格 JSON 解 AtomSpec 草案。解不出 / id 非法 / 无 prompt → None。tools 只留真工具。"""
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
    if not isinstance(obj, dict):
        return None
    aid = str(obj.get("id", "")).strip()
    prompt = str(obj.get("prompt", "")).strip()
    if not aid or len(aid) > _ID_MAXLEN or not _ID_RE.match(aid) or not prompt:
        return None
    tools = [str(t).strip() for t in (obj.get("tools") or []) if str(t).strip() in _REAL_TOOLS]
    return {"id": aid, "prompt": prompt[:2000], "tools": tools[:8], "tags": _norm_tags(obj.get("tags"))}


async def synthesize_atom_spec(desc: str, *, gateway: Any, model_ref: str = "") -> Optional[dict]:
    """LLM 把能力描述凝成 AtomSpec 草案(宁空勿毒)。失败/凝不出 → None。"""
    from karvyloop.gateway import ResolveScope, SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    msgs = [{"role": "user", "content": f"需要的能力:{desc}"}]
    try:
        text = ""
        # 自造合成:执行路径里 role 造原子的这一小段单拆成一线(否则并进上层 forge,看不清自造在烧)。
        with token_source("atom_synthesis"):
            async for ev in gateway.complete(msgs, [], model_ref or gateway.resolve_model(ResolveScope()),
                                             system=SystemPrompt(static=[_SYNTH_SYSTEM])):
                if type(ev).__name__ == "TextDelta":
                    text += getattr(ev, "text", "")
    except Exception:
        return None
    return _parse_spec(text)


def _unique_id(base: str, atom_registry: Any) -> str:
    aid = base
    i = 2
    while atom_registry.get(aid) is not None:
        aid = f"{base}_{i}"
        i += 1
    return aid


async def create_atom(
    desc: str,
    *,
    gateway: Any,
    atom_registry: Any,
    role_registry: Any = None,
    model_ref: str = "",
    role_id: Optional[str] = None,
) -> dict:
    """role 自造原子(§15.5)。返回 {action: reused|created|failed, atom_id?, reason?}。

    search-first → 没有则合成 → 合并闸近义复用 → 出生 provisional/self_created。
    """
    desc = (desc or "").strip()
    if not desc:
        return {"action": "failed", "reason": "能力描述为空"}
    # 1) search-first:查池(消费路径 + 防重复)。_toks 含 CJK bigram → 中英都能做表面去重,零模型。
    #    深层改写(字面无重叠)交给 daily ③ consolidation(chat 模型语义合并),不在此烧 LLM。
    hit = search_pool(desc, atom_registry, threshold=0.5)
    if hit is not None:
        return {"action": "reused", "atom_id": hit}
    # 2) 合成(宁空勿毒)
    spec = await synthesize_atom_spec(desc, gateway=gateway, model_ref=model_ref)
    if spec is None:
        return {"action": "failed", "reason": "凝不出单一职责的可复用原子"}
    # 3) 合并闸 a:lexical —— 用合成出的用途再查池(更严),近义则复用、不新增
    dup = search_pool(spec["prompt"], atom_registry, threshold=0.7)
    if dup is not None:
        return {"action": "reused", "atom_id": dup}
    # 3b) 合并闸 b:**标签重叠**(语义层,跨语言/改写)—— 合成出的 tags 与池里 atom 的 tags 共享 ≥2
    #     → 视为同义复用。补 lexical 抓不到的深层改写,**无向量**(标签是 LLM 算一次的归一化概念)。
    if spec["tags"]:
        for a in atom_registry.list_all():
            if _tag_overlap(spec["tags"], getattr(a, "tags", []) or []) >= 0.5:
                return {"action": "reused", "atom_id": a.id}
    # 4) 出生即 provisional / self_created(带上 tags 供日后匹配)
    aid = _unique_id(spec["id"], atom_registry)
    try:
        atom_registry.create(aid, "task", spec["prompt"], tools=spec["tools"],
                             provisional=True, origin="self_created", tags=spec["tags"])
    except Exception as e:  # noqa: BLE001
        return {"action": "failed", "reason": f"建原子失败: {e}"}
    return {"action": "created", "atom_id": aid}


_JUDGE_SYSTEM = (
    "你站在『{role}』这个角色的立场,裁定要不要把它这次为完成任务临时造的一个原子(子能力)"
    "**长期留进自己的工具箱**。这是 role 对自己 atom 的综合判断(atom 对 role 负责),综合权衡:"
    "① 人是否认可了这次结果(依据,但不是唯一)② 这个原子是否真出了力 ③ 是否过了验证 "
    "④ 是否通用到值得以后复用(只为这一次的、太窄的别留)。只输出 JSON:"
    '{"keep": true/false, "reason": "<一句话>"}。**拿不准就别留(keep=false)** —— 宁可下次重造,'
    "不拿没把握的原子污染公共库。"
)


async def judge_atom_keep(
    atom_spec: Any,
    *,
    role_id: str,
    role_identity: str = "",
    human_approved: bool,
    contributed: bool,
    verified: bool,
    gateway: Any,
    model_ref: str = "",
) -> dict:
    """role(LLM 站 role 视角)对"这个自造 atom 留不留"做**综合判断**(docs/02 §15.5,Hardy)。

    人对 role 结果的认可是**依据之一**(`human_approved`),非机械闸;role 综合权衡贡献/验证/通用性。
    返回 {keep: bool, reason: str}。**宁空勿毒**:解析失败 → 保守 keep=False(不污染公共库)。
    """
    from karvyloop.gateway import ResolveScope, SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    aid = getattr(atom_spec, "id", "") or ""
    purpose = (getattr(atom_spec, "prompt", "") or "")[:600]
    evidence = (
        f"原子:{aid} —— {purpose}\n"
        f"人是否认可了本次结果:{'是' if human_approved else '否'}\n"
        f"这个原子是否真被用上/出了力:{'是' if contributed else '否'}\n"
        f"是否过了独立验证:{'是' if verified else '否'}\n"
        f"角色身份:{(role_identity or role_id)[:300]}"
    )
    sys = _JUDGE_SYSTEM.replace("{role}", role_id or "该角色")
    try:
        text = ""
        # role 综合裁留不留:与合成同源(自造原子这条线的收尾判断,此前无标 → unknown,P0-9 长尾)。
        with token_source("atom_synthesis"):
            async for ev in gateway.complete([{"role": "user", "content": evidence}], [],
                                             model_ref or gateway.resolve_model(ResolveScope()),
                                             system=SystemPrompt(static=[sys])):
                if type(ev).__name__ == "TextDelta":
                    text += getattr(ev, "text", "")
    except Exception:
        return {"keep": False, "reason": "判断调用失败,保守不留"}
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = (raw[nl + 1:] if nl != -1 else raw).rstrip().removesuffix("```").strip()
    if not raw.startswith("{"):
        return {"keep": False, "reason": "判断无法解析,保守不留"}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"keep": False, "reason": "判断 JSON 坏,保守不留"}
    keep = obj.get("keep")
    if not isinstance(keep, bool):
        return {"keep": False, "reason": "判断未给明确 keep,保守不留"}
    return {"keep": keep, "reason": str(obj.get("reason", ""))[:200]}


def sediment_self_created(
    atom_id: str,
    *,
    approved: bool,
    atom_registry: Any,
    role_registry: Any = None,
    role_id: Optional[str] = None,
) -> dict:
    """任务收尾沉淀(§15.5):认可→进创建 role composition(被引用资产)+ 留 provisional;拒→撤。

    只动 **provisional 且 origin=self_created** 的原子(别误碰正式/合并/导入原子)。
    """
    a = atom_registry.get(atom_id)
    if a is None or not getattr(a, "provisional", False) or getattr(a, "origin", "") != "self_created":
        return {"action": "noop"}
    if not approved:
        # 安全(#2):只删 0 引用孤儿(同 ④ review_provisional)。被某角色 composition 引用则不删,
        # 避免悬空引用(沉淀只在 approved 时 add_atom,故 reject 路径正常即 0 引用;这是兜底)。
        if role_registry is not None:
            for r in role_registry.list_all():
                if atom_id in (getattr(r, "atom_ids", []) or []):
                    return {"action": "kept_referenced"}
        atom_registry.remove(atom_id)
        return {"action": "reverted"}
    # 认可:进创建 role 的 composition(成被引用资产 → ④ 巡检不当孤儿撤、靠复用转正)
    composed = False
    if role_registry is not None and role_id:
        try:
            composed = bool(role_registry.add_atom(role_id, atom_id))
        except Exception:
            composed = False
    return {"action": "kept", "composed_into_role": composed}


def make_self_create_tool(
    *,
    gateway: Any,
    atom_registry: Any,
    role_registry: Any = None,
    model_ref: str = "",
    role_id: Optional[str] = None,
    minted: Optional[list] = None,
):
    """把 create_atom 包成可被执行器调用的 Tool(默认挂给 role:无 atom 可用时启用)。

    `minted`:给了就把本次**新造**(action=created)的 atom_id append 进去 —— 让调用方(委派
    执行口)在任务收尾时对这些 atom 做沉淀(认可→留+入 composition / 拒→撤)。
    """
    from karvyloop.capability import Mode

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        desc = str((inp or {}).get("capability") or (inp or {}).get("description") or "").strip()
        if not desc:
            return {"ok": False, "reason": "需要 capability(描述你需要的能力)"}
        res = await create_atom(desc, gateway=gateway, atom_registry=atom_registry,
                                role_registry=role_registry, model_ref=model_ref, role_id=role_id)
        if minted is not None and res.get("action") == "created" and res.get("atom_id"):
            minted.append(res["atom_id"])
        return res

    return build_tool(
        name="create_atom",
        description=("当现有原子都无法完成当前任务时调用:描述你需要的能力,系统会先在公共原子库里找"
                     "能复用的,没有才造一个新的(出生为试用,被复用才转正)。"),
        input_schema={"type": "object",
                      "properties": {"capability": {"type": "string", "description": "你需要的能力"}},
                      "required": ["capability"]},
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,  # #4:与 policy 表一致(做事中写,只读 checker 拦)
    )


__all__ = ["search_pool", "synthesize_atom_spec", "create_atom",
           "judge_atom_keep", "sediment_self_created", "make_self_create_tool"]
