"""console/distill_engine.py — 认知库沉淀工作流引擎(P2-e:拆 routes.py,领域引擎下沉,行为零变化)。

从 routes.py 纯搬移:喂料→抓取分析→知识自生长框架结构化→交流→你拍板沉淀/拒绝 的引擎侧
(待办沉淀存储 / 前端视图 / 分析与交流 LLM 调用);/api/memory/* HTTP 端点仍留在 routes.py。
一次一条、持久化(重启续),不结束不开下一条。用 LLM Wiki/知识自生长框架结构化(others/卡帕西)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _distill_store(app):
    """单条待办沉淀的持久化存储(lazy)。"""
    st = getattr(app.state, "distill_store", None)
    if st is None:
        import pathlib
        from karvyloop.cognition.distill_session import DistillSessionStore
        cfgp = getattr(app.state, "config_path", "") or ""
        base = pathlib.Path(cfgp).parent if cfgp else (pathlib.Path.home() / ".karvyloop")
        st = DistillSessionStore(base / "pending_distill.json")
        app.state.distill_store = st
    return st


def _distill_public(s):
    """给前端的视图:去掉抓来的大段正文(只留摘要/来源/交流/阶段)。"""
    if not s:
        return None
    return {"id": s.get("id"), "summary": s.get("summary", ""),
            "source_url": s.get("source_url", ""), "material": (s.get("material") or "")[:300],
            "already_fed": int(s.get("already_fed", 0)),   # >0 → 这份喂过了,沉淀会 supersede 换新版
            "transcript": s.get("transcript", []), "phase": s.get("phase", "awaiting")}


_DISTILL_FRAMEWORK = (
    "你是小卡。用户分享了一份材料给个人知识库。用「知识自生长 / LLM Wiki」框架**分析并结构化**它,"
    "总结给用户看(**还没沉淀**,等用户确认)。按这个结构,清晰小标题 + bullet,简洁抓重点别堆字:\n"
    "1. **这是什么** —— 核心主题 / 来源类型;\n"
    "2. **核心概念 / 实体** —— 像 wiki 的概念页/实体页,抽 3-6 个要点;\n"
    "3. **关系** —— 它们之间怎么关联(谁支撑谁 / 谁对比谁);\n"
    "4. **值得沉淀的要点** —— 将来能复用的(关于这个主题、或关于用户);\n"
    "5. **建议沉淀吗** —— 一句话给建议 + 为什么。**沉淀抽的是正文里的具体知识,不是主题价值。"
    "若你主要靠链接/仓库名/常识**推断**、没读到实质正文,就如实说『没抓到正文,建议先贴正文、"
    "或跟我补充几句关键点再沉淀』,别只因主题有价值就建议沉淀(否则会出现'建议沉淀了、点沉淀却抽不出东西')。**\n"
    "若材料里有用户自己的背景,优先结合。"
)


async def _distill_analyze(gw, model_ref, content, user_ctx="") -> str:
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    usr = (f"[关于用户的已知背景]\n{user_ctx}\n\n" if user_ctx else "") + f"[分享的材料]\n{content}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[_DISTILL_FRAMEWORK])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[distill] 分析失败: {e}")
    return out.strip() or "(分析失败,稍后重试)"


async def _distill_chat_reply(gw, model_ref, session, message) -> str:
    """沉淀前的交流:你对这份料追问/补充,小卡围绕材料+当前总结回应。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是小卡,正在和用户讨论一份待沉淀进知识库的材料。基于下面的材料和你的结构化总结,"
            "回应用户的追问/补充,帮他判断要不要沉淀。简洁、对话式。")
    convo = "\n".join(f"{x['who']}: {x['text']}" for x in session.get("transcript", []))
    usr = (f"[材料]\n{(session.get('fetched') or session.get('material') or '')[:6000]}\n\n"
           f"[你的结构化总结]\n{session.get('summary', '')}\n\n"
           f"[此前交流]\n{convo}\n\n[用户最新一句]\n{message}")
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[distill] 交流失败: {e}")
    return out.strip() or "(没接上,再说一次?)"
