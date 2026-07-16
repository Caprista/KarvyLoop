"""Stage 3.5 Bootstrap —— LLM 驱动的 agent 拆解(docs/14 §10,M3 升级).

拍 4 v0 的 adapter 是**确定性**的:把外部 manifest 套模板写成 7 文件,tools 只列名字,
**不调 LLM、不出原子、0 token**(J7)。Hardy 2026-06-26 拍定:**导入 agent 该是一次 LLM 拆解**
——读懂 agent → 拆出 role(真人设,非模板占位)+ atom(每个工具/能力 → 公共原子库里一个可复用
原子,能 bind 现有就 bind、不能就建新)+ 识别内含 skill;这次拆解**必然耗 token**。

**为什么要拆出原子(不只是 role)**:护城河是"用得久 → 结晶成抄不走的 role/atom 资产"。把别人 agent
的 tools 留成 COMPOSITION 里的死字符串 = 没沉淀任何可复用资产;拆成公共原子库的原子,以后**任何**角色
都能组合它(甲「买糖」:用不拥有,docs/00 §2.3)。

**宁空勿毒**(复用 [[llm-output-parser-must-refuse-garbage]] 的纪律):LLM 拆解结果要写进持久原子库,
解析必须 JSON 严格 —— 解不出 / 没有合法原子 → 返 None,由调用方**优雅降级**回 v0 确定性 adapter
(不把 prose / 坏 JSON 当原子写进公共池投毒)。

**降级**:gateway is None(--no-llm)→ 调用方不进本阶段,走 v0。本模块只负责"有 LLM 时怎么拆"。
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Optional

from karvyloop.adapter.source import ExternalManifest

# 原子 id 必须 COMPOSITION-safe(同 atoms/registry._ATOM_ID_RE):只允许 [A-Za-z0-9_]+,
# 否则 COMPOSITION.yaml 里 `atom: <name>` 引用不到。LLM 给的名字先过这把尺。
_ATOM_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
_VALID_KINDS = ("task", "daemon")
# 判型白名单(docs/84 #2,判据=宪法"担不担你的责"):折进同一次拆解调用,不加分类器前置。
# 非法/缺失 → "hybrid"(宁保守勿毒:hybrid 走现路径,原子照落、角色照建,不会错杀也不会错落)。
_VALID_AGENT_KINDS = ("decision", "executor", "hybrid", "skill")

# 所有 LLM 控制、会落进公共原子库/atoms.json/磁盘的集合都要封顶(独立对抗验收:id 封了顶,
# 但 tools/原子个数/skills 还能灌爆盘 —— 9.59MB atoms.json/单次导入)。封顶 = 宁空勿毒的一部分。
_MAX_ATOMS = 32        # 真 agent 工具数量级;超出截断(一个工具≈一个原子)
_MAX_TOOLS = 16        # 单原子引用的底层工具数
_MAX_SKILLS = 16       # 识别出的技能数
_MAX_STR = 64          # tool/skill 字符串单条长度(同 id 上限)

DECOMPOSE_SYSTEM = """你是 KarvyLoop 的 Agent 拆解器。用户从外部(Claude/Codex/LangChain…)拿来一个 agent,
你要把它**拆解**成 KarvyLoop 的资产:一个 role(角色)+ 若干 atom(原子)。

概念(严格遵守):
- **atom(原子)= 角色不可再分的可复用构建块**。判据:它能不能被多个角色组合复用?能才是好原子。
- **role(角色)= 人设 + 它组合哪些原子**。从 system_prompt 提炼真实人设,不要套话。

**只有这 6 个是真实可执行工具(原语),别的都是假的**:
  `run_command`(在沙箱里**写代码并运行**——任何计算/处理/转换/调 API/解析/聚类/生成逻辑都用它,
  它是万能执行口)、`read_file`、`write_file`、`edit_file`、`web_search`、`web_fetch`。
**原子的 tools 只能从这 6 个里选。** 绝不要编"semantic_clustering""pdf_extract""send_email"这种
不存在的工具名——那会让原子变成只能空想的 advisory 死原子(对不上真工具)。一个能力该怎么落:
  - 本地计算/数据处理/转换/解析/生成逻辑/调用某个 HTTP API → **`run_command`**(agent 现写代码跑)。
  - 读/写/改文件 → read_file / write_file / edit_file。 联网搜/抓 → web_search / web_fetch。
  - 真要外部系统集成(发邮件、连 Salesforce、发 Slack 这类需凭证/外部服务的)→ **不要编工具**,
    把它写进 `skills`(一项待建/待导入的技能,以后用 MCP 或脚本补)——这是诚实地说"这要一个 skill"。

输入会给你:agent 名、system_prompt、tools 列表、(可能有)skills、以及**已存在的公共原子库**(可复用)。

只输出**一个 JSON 对象**(不要任何解释、不要 markdown 代码围栏),schema:
{
  "agent_kind": "四选一:decision / executor / hybrid / skill(判型,唯一判据见下)",
  "identity": "一句话人设:这个角色是谁、最擅长什么(从 system_prompt 提炼,中文)",
  "soul": "2-4 条工作风格/原则,用 \\n 分隔(从 system_prompt 提炼)",
  "atoms": [
    {
      "id": "snake_case_名字(只许字母数字下划线)",
      "kind": "task 或 daemon(一次性任务用 task,常驻后台用 daemon,默认 task)",
      "purpose": "这个原子做什么(一句话)",
      "tools": ["只能从 run_command/read_file/write_file/edit_file/web_search/web_fetch 里选"],
      "tags": ["2-5个归一化语义标签,英文小写(如 web/search/translate),给跨语言/改写的语义匹配用"],
      "reuse_existing": true 或 false(若已存在公共原子库里有同义原子,填 true 表示复用、不新建)
    }
  ],
  "skills": ["识别出的内含技能名(若无则空数组)"]
}

判型(agent_kind,唯一判据=这个 agent **担不担用户的责**):
- "decision":有身份/立场,**替人做取舍与判断**(顾问/把关/评审这类"谁"——担你的责)。
  atoms 可以为 0(纯人设顾问合法)。
- "executor":只有能力步骤,**谁用都一样**、无立场(转换器/爬虫/查询器这类"工具",不担责的纯执行体)。
- "hybrid":既有真人设立场、又有具体可执行能力,两样都真。**拿不准就填 hybrid**。
- "skill":本质是一段**流程剧本/方法**(step-by-step 的 SOP,教"怎么做"而不是"谁")→ 该进技能库。

硬约束:
- 若给了 tools:列表里**每一个**都要落成至少一个 atom(别漏)。一个工具对一个原子是常态。
- **若没给 tools(纯人设 agent):从 system_prompt 描述的能力提炼原子**——这个角色实际会**做**哪几件
  可复用的事(如"语义聚类异常数据""生成确定性修复逻辑""调研竞品"),每件落一个原子,
  **tools 填 run_command**(这些事都是写代码跑出来的)→ 原子就是真可执行的,不是空想。
  纯外部集成(需凭证/外部服务)→ 进 `skills` 而非编工具。提炼不出任何具体可复用能力(纯性格/态度)
  → atoms 留**空数组**(按顾问人设角色导入,合法)。
- id 只能含字母/数字/下划线。kind 只能是 task / daemon。
- 若公共原子库里已有能复用的,reuse_existing 填 true(仍要在 atoms 里列出该 id)。
- 严格 JSON,不要围栏、不要注释、不要尾随文本。"""


@dataclasses.dataclass(frozen=True)
class AtomProposal:
    """LLM 拆出的一个原子提案(待落进公共原子库)。"""
    id: str
    kind: str              # task / daemon
    purpose: str
    tools: tuple[str, ...]
    tags: tuple[str, ...]   # 归一化语义标签(docs/02 §15.5):给跨语言/改写的标签重叠匹配用(无向量)
    reuse_existing: bool    # True = 库里已有同义原子,引用即可不新建


@dataclasses.dataclass(frozen=True)
class DecompositionResult:
    """一次 LLM 拆解的产物。identity/soul 覆盖 v0 模板占位;atoms 落库 + 进 COMPOSITION。

    agent_kind(docs/84 #2)= 判型,决定路由:decision→建 role(atoms 可 0)/ hybrid→现路径 /
    executor→只落公共原子库不建 role / skill→指路技能库导入(不落 role/atom)。
    """
    identity: str
    soul: str
    atoms: tuple[AtomProposal, ...]
    skills: tuple[str, ...]
    agent_kind: str = "hybrid"   # decision / executor / hybrid / skill(白名单外已在 parse 归一)

    def is_valid(self) -> bool:
        """有效拆解的最低门槛**按型分**(docs/84 #2):
        - decision / hybrid:要真提炼出人设(identity)——有身份立场才配决策席;atoms 可为 0
          (纯人设顾问合法,不该退回 v0 扁平拷——那才是 Hardy 骂的 0-token bug)。
        - executor:纯执行体没有"谁",identity 不作数,要 ≥1 个原子(否则啥也没拆出来)。
        - skill:流程剧本,要 ≥1 个识别出的技能名(否则无从指路技能库)。
        无效 → 调用方降级 v0(如实标 decomposed=False)。"""
        if self.agent_kind == "executor":
            return len(self.atoms) >= 1
        if self.agent_kind == "skill":
            return len(self.skills) >= 1
        return bool(self.identity and self.identity.strip())


def _strip_outer_fence(s: str) -> str:
    """只剥**外层** ```…``` 围栏对(不逐行删,避免穿删合法内容)。"""
    t = s.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def parse_decomposition(text: str) -> Optional[DecompositionResult]:
    """宁空勿毒:严格 JSON 解 LLM 拆解结果 → DecompositionResult;解不出 / 全空产出 → None。

    None = 让调用方降级回 v0 确定性 adapter,**绝不**把坏结果当原子写进公共池。
    注意"合法但零原子"≠垃圾:纯人设 agent(identity 有 / atoms 空)是提示词明许的合法结果,
    返回给调用方由 is_valid() 按 agent_kind 分型把关(docs/84 在场 bug 修)。
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

    atoms: list[AtomProposal] = []
    seen: set[str] = set()
    for a in obj.get("atoms", []) or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id", "")).strip()
        # 长度封顶(同 purpose/identity/soul 都有上限):LLM 控制此串,无界 id 会把超长垃圾
        # 写进公共原子库 + atoms.json + COMPOSITION(独立对抗验收 Defect 1)。原子名本就该短。
        if not aid or len(aid) > 64 or not _ATOM_ID_RE.match(aid) or aid in seen:
            continue                      # 非法/超长/重复 id → 丢这一个(不毒整批)
        kind = str(a.get("kind", "task")).strip() or "task"
        if kind not in _VALID_KINDS:
            kind = "task"
        # 工具串:单条长度 + 条数都封顶(LLM 控制,直落 atoms.json)
        tools = tuple(t for t in (str(x).strip() for x in (a.get("tools") or []))
                      if t and len(t) <= _MAX_STR)[:_MAX_TOOLS]
        # 语义标签(归一化:小写、去空、去重保序、每个 ≤32、≤8 个)—— 给标签重叠匹配用
        _seen_tag: set[str] = set()
        tags_l: list[str] = []
        for x in (a.get("tags") or []):
            tg = str(x).strip().lower()[:32]
            if tg and tg not in _seen_tag:
                _seen_tag.add(tg)
                tags_l.append(tg)
        atoms.append(AtomProposal(
            id=aid, kind=kind, purpose=str(a.get("purpose", "")).strip()[:400],
            tools=tools, tags=tuple(tags_l[:8]), reuse_existing=bool(a.get("reuse_existing", False)),
        ))
        seen.add(aid)
        if len(atoms) >= _MAX_ATOMS:      # 原子总数封顶,截断余下(防灌爆公共池/盘)
            break

    skills = tuple(s for s in (str(x).strip() for x in (obj.get("skills") or []))
                   if s and len(s) <= _MAX_STR)[:_MAX_SKILLS]
    identity = str(obj.get("identity", "")).strip()[:600]
    # 判型白名单(docs/84 #2):非法/缺失 → hybrid(宁保守勿毒,hybrid 走现路径不会错杀/错落)
    agent_kind = str(obj.get("agent_kind", "")).strip().lower()
    if agent_kind not in _VALID_AGENT_KINDS:
        agent_kind = "hybrid"
    # 在场 bug 修(docs/84):提示词明说"纯人设 agent atoms 留空数组合法",此处原先
    # `if not atoms: return None` 把合法结果丢弃 → 降级 v0 扁平拷(烧了 token 白拆)。
    # 改成:atoms / identity / skills **全空**(啥也没拆出来)才 None;有任何一样真产出
    # 都交给 is_valid() 按型把关(decision/hybrid 要 identity,executor 要原子,skill 要技能)。
    if not atoms and not identity and not skills:
        return None
    return DecompositionResult(
        identity=identity,
        soul=str(obj.get("soul", "")).strip()[:1200],
        atoms=tuple(atoms),
        skills=skills,
        agent_kind=agent_kind,
    )


def _format_input(manifest: ExternalManifest, existing_atom_ids: list[str]) -> str:
    """喂给拆解器的料:agent 名 + system_prompt + tools + skills + 已有原子库(供复用判断)。"""
    tool_names = []
    for t in manifest.tools:
        if isinstance(t, dict):
            tool_names.append(str(t.get("name", "") or t.get("type", "")).strip())
        else:
            tool_names.append(str(t).strip())
    tool_names = [n for n in tool_names if n]
    parts = [
        f"agent 名:{manifest.agent_name or manifest.source_id}",
        f"system_prompt:\n{manifest.system_prompt[:4000]}",
        f"tools({len(tool_names)} 个):{', '.join(tool_names) or '(无名)'}",
    ]
    if manifest.skills:
        snames = [str((s.get('name') if isinstance(s, dict) else s) or '').strip() for s in manifest.skills]
        parts.append(f"已声明 skills:{', '.join(n for n in snames if n)}")
    parts.append(f"已存在的公共原子库(可复用):{', '.join(existing_atom_ids) or '(空)'}")
    return "\n\n".join(parts)


async def bootstrap_decompose(
    manifest: ExternalManifest, *, existing_atom_ids: list[str],
    gateway: Any, model_ref: str = "",
) -> Optional[DecompositionResult]:
    """跑一次受限 LLM 拆解(无工具)→ DecompositionResult。gateway.complete 自动入 token 账本。

    复用 decision_pref.compile_decisions 的调用约定(同 gateway.complete 流式收 TextDelta)。
    返回 None(LLM 没出合法结果)→ 调用方降级回 v0 确定性 adapter。
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
    material = _format_input(manifest, existing_atom_ids)
    material, _ = clip_to_tokens(material, LLM_MATERIAL_TOKENS)   # 基建天花板(各字段已截,整段再封顶)
    # 重试一次再降级:并发/网络偶发把 JSON 截断 → parse 返 None,多半重发就好(批量导入
    # 实测 70 个里 1-3 个坏 JSON)。只重 1 次,仍 None 才降级回 v0(不无限烧 token)。
    for _attempt in range(2):
        out = ""
        async for ev in gateway.complete(
            [{"role": "user", "content": material}], [], ref,
            system=SystemPrompt(static=[DECOMPOSE_SYSTEM]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
        result = parse_decomposition(out)
        if result is not None:
            return result
    return None


__all__ = ["AtomProposal", "DecompositionResult", "bootstrap_decompose", "parse_decomposition", "DECOMPOSE_SYSTEM"]
