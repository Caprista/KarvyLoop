"""self_knowledge — 小卡对系统架构的自我认知(建 agent 指导知识,按意图注入)。

**病根**(Hardy 2026-07-02):系统架构认知大多在开发侧文档里,小卡的人格 prompt 只有
身份+纪律 —— 用户说"我要做个关于 xxx 的 agent",小卡没有任何自有认知去指导构建:
不知道 domain/role/atom/skill 是什么、不知道有现成域模板、更不能替用户落地。

**这层做什么**:
1. `wants_build_guidance(intent)` —— 便宜的确定性关键词门:这句话像不像"建 agent/
   建角色/建团队"类意图。命中才注入知识(不常驻,省 token)。
2. `self_knowledge_block()` —— 紧凑的"架构 101 + 建 agent 方法论"文本,注入小卡的
   system prompt 动态段。**全部 grounded 在真实系统能力**(模板清单从
   `domain.templates.TEMPLATES` 动态生成,不会漂移);tool-reality 纪律:只引用真实
   存在的能力,明确"能替用户做的"vs"只能引导用户去界面点的",不吹没有的。
3. `make_instantiate_template_tool(...)` —— 把 `domain.templates.instantiate_template`
   包成 Tool 挂给小卡:用户选定模板并确认后,小卡能**真的**一键开出域+角色
   (走既有 capability 护栏,policy 表 WORKSPACE_WRITE 下限,同 create_atom)。

注入点:`coding.persona.build_karvy_persona_prompt(intent=...)`(知识)+
`workbench.main_loop_bridge.drive_in_tui`(工具,需调用方传 domain_registry)。
"""
from __future__ import annotations

from typing import Any

# ---- 意图门(确定性关键词,零 token;命中才注入知识/挂工具)----

# 中文按子串匹配(无分词);英文统一小写后匹配。
# 覆盖:建 agent / 建角色 / 建域(开公司)/ 建团队 / 做个助手 + 模板名直呼。
_BUILD_KWS: tuple[str, ...] = (
    # agent / 智能体 / 数字员工
    "agent", "智能体", "数字员工",
    # 角色
    "建角色", "建个角色", "创建角色", "新角色", "加个角色", "造个角色", "配个角色",
    # 域 / 公司
    "建域", "建个域", "开个域", "业务域", "开公司", "开个公司", "开家公司",
    # 团队 / 班子 / 助手
    "建团队", "建个团队", "组个团队", "组建团队", "搭班子", "搭个班子",
    "做个助手", "建个助手", "要个助手", "造个助手", "配个助手",
    # 模板直呼(用户跟进"就开个理财研究所吧"也要命中,不然第二轮工具/知识掉线)
    "研究所", "工作室", "求职战队", "运营部", "模板",
    # 英文
    "create a role", "new role", "build a team", "set up a team",
    "personal assistant", "digital employee",
)


def wants_build_guidance(intent: str) -> bool:
    """这句话像不像"建 agent / 建角色 / 建团队"类意图(确定性,便宜,可能少量误伤 —
    误伤代价只是这一轮多注入一块知识文本)。"""
    if not intent:
        return False
    low = intent.lower()
    return any(k in intent or k in low for k in _BUILD_KWS)


# ---- 架构 101 + 建 agent 方法论(注入文本)----

def _templates_lines() -> list[str]:
    """现成域模板清单 —— 从真实 TEMPLATES 动态生成(不会和代码漂移)。"""
    from karvyloop.domain.templates import list_templates
    lines: list[str] = []
    for t in list_templates():
        roles = "、".join(f"{r['nickname']}({r['title']})" for r in t["roles"])
        lines.append(f"  - {t['emoji']}「{t['name']}」(template_id={t['id']}):"
                     f"{t['description']} 角色:{roles}。")
    return lines


def self_knowledge_block() -> str:
    """小卡的系统自我认知块(只在建 agent 类意图命中时注入 system 动态段)。"""
    parts: list[str] = [
        "【你对 KarvyLoop 系统的自我认知 —— 用户想建 agent/角色/团队时,用这套知识指导他】",
        "",
        "核心概念(只讲这些,别发明新词):",
        "  - 业务域(domain):一家'小公司'= 价值观(value.md,几条原则)+ 硬规矩(deontic 的"
        " forbid/oblige,运行时真的会拦)+ 成员角色。域之间认知隔离,域里的私有认知不外漏。",
        "  - 角色(role):域里干活的'人'= 一句话身份(我是谁、负责什么)+ 性情(做事风格/底线)"
        "+ 尽责契约(创建时系统自动配好,用户不用写)。角色对用户负责:替他干活、向他汇报,拍板永远是用户。",
        "  - 原子(atom):角色的可复用执行能力,对角色负责。角色干活时缺能力会用 create_atom"
        " 自造(先搜公共池,能复用就复用)—— 用户**不用预配**。",
        "  - 技能(skill):做成过的事结晶成 SKILL.md(存方法不存答案)。技能是**用出来的**,"
        "不是建 agent 时配出来的 —— 别让用户预配技能。",
        "  - 工具(tool):角色/你能用 read_file / write_file / edit_file / run_command /"
        " web_search / web_fetch(+ 用户接入的 MCP 工具)。全部走能力护栏,危险操作先问。",
        "",
        "现成域模板('一键开公司',最快的起步方式):",
        *_templates_lines(),
        "",
        "指导用户建 agent 的方法论(五步,别跳):",
        "  1. 问清目标 —— 需求采集协议(docs/47 共创纪律,硬约束不是建议):",
        "     · 四个维度按缺失度问:①目标物(盯什么/产出什么)②节奏(多久要一次/一次性)"
        "③口味(输出长短/严谨度/语言)④边界(不许碰什么/花钱上限)。",
        "     · 问法纪律:**每轮最多 2 个问题**;**每问自带 1-3 个候选答案**让用户直接点选"
        "(例:「多久要一次?A每天早上 B每周一 C用的时候叫它」),别让用户写作文;",
        "     · **最多问 3 轮**;信息够开草案、或用户说「你看着办/就这样吧/别问了」→ **立即停**,"
        "宁可草案带假设标注(「我先假设每周一早上要,不对你说」),不许第 4 轮还在问;",
        "     · 停下后先复述一句「所以你要的是:<目标物>+<节奏>+<口味>+<边界> —— 对吗?」再动工"
        "(示范一个好需求长什么样,这就是教用户提问)。",
        "  2. 选路:目标贴近某个模板 → 直接推荐那个模板(说清里面有谁、各管什么);"
        "都不合身 → 引导自建域+角色(见第 3 步)。",
        "  3. 自建时帮用户把灵魂写好:角色 = 一句话身份 + 一两句性情;域 = 3 条以内价值观 +"
        " 必要的硬规矩(禁止什么 forbid / 必须什么 oblige)。宁少而准,别堆长文。",
        "  4. 验证:开好后先丢一单小任务试跑(模板自带示例开场白可直接用),看顺不顺再调。",
        "  5. 技能让它自己长:角色把活干成,方法会自动结晶成技能,越用越懂用户 —— 不用预配。",
        "",
        "你能真做的 vs 只能引导的(诚实边界,不许吹):",
        "  - 你能真做:用户**明确选定**某个模板并确认后,调 instantiate_domain_template 工具"
        "(参数 template_id)一键开出该域和角色。**必须先确认、后调用**,绝不擅自开;"
        "同名域已存在会被拒,如实转告。若你本轮工具清单里**没有**这个工具,就别声称能开,改为引导。",
        "  - 只能引导(你没有对应工具,让用户到界面点):自建业务域(业务域面板的创建表单:"
        "名字/价值观/硬规矩)、新建或编辑角色(角色面板:身份/性情/花名/职务)、导入外部 agent"
        "(角色面板的导入入口)、@多人工作流和圆桌讨论(在聊天里发起,用户拍板)。",
        "  - 你的边界:你是 observer,不进业务域当业务角色;域开好后活是域里角色干的,"
        "你负责匹配、委派和盯进度。",
        "  - 系统里**没有**的别编:没有模型训练/微调、没有应用市场、没有向量库配置 ——"
        " 上面没列到的功能一律不引用。",
    ]
    return "\n".join(parts)


# ---- 落地工具:instantiate_domain_template(小卡真能替用户开模板)----

def make_instantiate_template_tool(*, domain_registry: Any, role_registry: Any,
                                   domain_store: Any = None,
                                   created_by: str = "user:console"):
    """把 `instantiate_template` 包成小卡可调用的 Tool(经 build_tool,HR-1)。

    护栏:policy 表下限 WORKSPACE_WRITE(同 create_atom:做事中写,只读 checker 拦);
    instantiate 本身幂等安全(同名活跃域拒绝、已存在角色复用)。挂载前提 = 用户这句就是
    建 agent 意图(意图门),真正"拍板"是用户在对话里明确选定模板 —— 知识块里已明令
    小卡先确认后调用。
    """
    from karvyloop.capability import Mode
    from karvyloop.registry.tool import build_tool

    async def _call(inp: dict, token: Any, sandbox: Any) -> Any:
        tid = str((inp or {}).get("template_id") or "").strip()
        if not tid:
            return {"ok": False, "reason": "需要 template_id(先跟用户确认开哪个模板)"}
        from karvyloop.domain.templates import instantiate_template
        return instantiate_template(tid, domain_registry=domain_registry,
                                    role_registry=role_registry, domain_store=domain_store,
                                    created_by=created_by)

    from karvyloop.domain.templates import list_templates
    ids = "、".join(t["id"] for t in list_templates())
    return build_tool(
        name="instantiate_domain_template",
        description=("用户明确选定某个现成域模板并确认后调用:一键开出该业务域+配好灵魂的角色。"
                     f"可用 template_id:{ids}。同名活跃域已存在会被拒(如实转告用户)。"),
        input_schema={"type": "object",
                      "properties": {"template_id": {"type": "string",
                                                     "description": "模板 id(见工具描述)"}},
                      "required": ["template_id"]},
        call=_call,
        required_mode=Mode.WORKSPACE_WRITE,  # 与 policy 表一致(同 create_atom 语义)
    )


__all__ = ["wants_build_guidance", "self_knowledge_block",
           "make_instantiate_template_tool"]
