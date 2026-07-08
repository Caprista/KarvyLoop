"""paradigm/agent_spec — 内部 agent 的范式工程内核(range 版 role 范式)。

**为什么有这个文件(Hardy 2026-07-08 拍):** 我们对外角色用 7 文件范式(IDENTITY / SOUL / USER /
COMMITMENT / VERIFY / MEMORY / COMPOSITION),但撑起整个 loop 的内部机器 agent(收敛 / 蒸馏 / 摄入 /
馆员 / 冲突消解 …)全是埋在代码里的裸 system-prompt 字符串,一个都没用范式。开源项目里,源码就是范式
的参考实现——**我们自己的引擎都不用自己的范式,凭什么让别人用、凭什么说范式能工程化 AI 项目?**

**范式覆盖所有 agent,但要老实分层:**
- **persona 层**(USER = 服务谁、MEMORY = 自己长)只对**面向用户、会成长的角色**成立;
- **工程内核**(下面 5 项)对**所有 agent** 成立,内部无状态机器 agent 也不例外。

`AgentSpec` 把一个内部 agent 的工程内核声明成**结构化、可查、可测**的东西(不再是裸字符串),让
"范式覆盖所有 agent"的内部半边落地 = 给扒代码的开发者看到**知行合一**。这不是要把内部 agent 塞进
用户 UI(那会 clutter + "能看不能调";内部机器不进用户面)——纯粹是源码层的一致性与可信度。

第一个采用者:`cognition/converge.py::CONVERGE_AGENT`。散文 prompt(CONVERGE_SYSTEM)+ 解析器
(parse_candidates)是这份 spec 的实现;test_converge_layered 把三者对账,不许漂移。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    """一个内部 agent 的范式工程内核。对应 7 文件里非 persona 的那 5 项。

    刻意**不含** USER(服务谁)/ MEMORY(自己长)—— 内部无状态 agent 不服务某个人、不长记忆,
    硬塞这两项是脚手架浪费(少脚手架多信模型)。分层诚实:persona 层归对外角色,工程核归所有 agent。
    """

    id: str
    identity: str                # 它是谁 / 干什么(≈ IDENTITY,一句话)
    principles: tuple[str, ...]  # 原则 / 纪律(≈ SOUL,每条一句)
    contract: str                # 对系统的契约:只做什么、绝不做什么(≈ COMMITMENT)
    verify: str                  # 输出 / 行为验收 —— 可被测试对账的那句(≈ VERIFY)
    tools: tuple[str, ...] = ()  # 用到的工具(≈ COMPOSITION;纯 LLM agent = 空)

    def __post_init__(self) -> None:
        # 工程核五项都不许空(persona 层缺席是设计,工程核缺席是没写全)
        if not (self.id and self.identity and self.principles and self.contract and self.verify):
            raise ValueError(f"AgentSpec({self.id!r}) 工程内核有空项(identity/principles/contract/verify 必填)")


__all__ = ["AgentSpec"]
