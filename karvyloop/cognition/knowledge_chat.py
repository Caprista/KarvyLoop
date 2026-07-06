"""cognition/knowledge_chat — 「聊知识」模式(docs/66 §F:收敛/沉淀是知识模式,不是全局功能)。

Hardy 2026-07-06 纠正:把收敛做进全局聊天 = 逻辑错乱(正常聊工作,一收敛把工作会话关了)。
正确形态:
- 知识库里单独一个**聊知识模式**;陪聊的是**特殊 Karvy(知识馆员)**——带知识搜索/整理/归纳能力,
  和全局小卡不是一回事;
- "会话=临时存放区、沉淀了才关、开数=欠账"这套生命周期**只活在知识线**里;
- 全局聊天唯一的联动:你说"聊点新知识/认知…"→ 小卡**问**你要不要开收集模式 → 你说是才切换。

实现:知识线 = 一个专属 peer(l0 / librarian / karvy-knowledge)——复用 Conversation 的
peer 隔离(CV-13),知识会话天然和工作线互不干扰;drive 时按 peer 注入馆员人设(记忆召回
已在上游注入,馆员生来就"手边有你的知识库")。
"""
from __future__ import annotations

from typing import Any, Optional

from karvyloop.domain.registry import Address

KNOWLEDGE_DOMAIN = "l0"                    # 知识线住 karvy world(个人层,非业务域)
KNOWLEDGE_ROLE = "librarian"
KNOWLEDGE_AGENT_ID = "karvy-knowledge"


def knowledge_peer() -> Address:
    """知识线的 peer 地址(会话按它隔离存放)。"""
    return Address(domain_id=KNOWLEDGE_DOMAIN, role=KNOWLEDGE_ROLE, agent_id=KNOWLEDGE_AGENT_ID)


def is_knowledge_peer(peer: Optional[Any]) -> bool:
    """当前线是不是「聊知识」模式(duck-type:Address 或带同名属性的对象)。"""
    if peer is None:
        return False
    return (getattr(peer, "domain_id", "") == KNOWLEDGE_DOMAIN
            and getattr(peer, "role", "") == KNOWLEDGE_ROLE
            and (getattr(peer, "agent_id", "") or "") == KNOWLEDGE_AGENT_ID)


# 知识馆员人设(注入 governance 前缀;记忆召回块在上游已注入 —— 馆员"手边有你的知识库")。
KNOWLEDGE_PERSONA = (
    "【你此刻是知识馆员小卡 —— 「聊知识」收集模式】\n"
    "用户来这条线,是为了消化新知识/新认知(链接、文章、一段说法、一个想法),**也可能只是想漫谈**\n"
    "(如「聊聊 AI 时代的产品发展」)——漫谈也是聊知识,直接对等聊起来,不需要 ta 先交材料才开口。\n"
    "你的职责:\n"
    "1) 先给出你自己消化后的观点(你们是两个都读过材料的人对等开聊,不是复读机);\n"
    "2) 对照他已有的知识库(上文召回块):新东西和旧认知哪儿呼应、哪儿矛盾、哪儿是空白,主动指出来;\n"
    "3) 帮他刨深:追问真实意图与出处、点出说法背后的隐含假设、区分「事实/推理/原则」的层次;\n"
    "4) 克制:一轮最多问两个问题,别审讯;他说先存着就存着,不催沉淀。\n"
    "材料纪律:链接的正文若已随消息附上(【链接正文】块),那就是你读到的内容,基于它聊;\n"
    "若标注抓取失败,**老实说没读到、请 ta 贴正文**,绝不凭 URL 字面或名字猜内容(猜=瞎编=投毒)。\n"
    "沉淀纪律:**你不写库**。他点「收敛」你们才总结,他逐条确认过的才算他的认知;没确认的只是语料。"
)


def knowledge_governance(peer: Optional[Any], base: str) -> str:
    """知识线 → 馆员人设前缀进 governance;其他线原样返回(零侵入)。"""
    if not is_knowledge_peer(peer):
        return base
    return (KNOWLEDGE_PERSONA + "\n\n" + (base or "")).strip()


__all__ = ["KNOWLEDGE_DOMAIN", "KNOWLEDGE_ROLE", "KNOWLEDGE_AGENT_ID", "KNOWLEDGE_PERSONA",
           "knowledge_peer", "is_knowledge_peer", "knowledge_governance"]
