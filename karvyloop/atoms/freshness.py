"""atoms/freshness — 时效性信号检测 + 任务标注(实时信息必须联网查,不许凭训练记忆编)。

病根(Hardy 碎碎念④):role/atom 检索信息时,遇到"今天/最新/现价"这类**实时数据**,
模型可能凭训练记忆直接报数(stale 还自信)。工具在(web_search/web_fetch 默认全给),
但"什么时候必须用"这个**主观表达**在工具描述/system prompt/下发任务文本里都不够硬。

借业界(便宜确定性信号 → 选择性提示,不常驻烧 token):
- 主流助手 system prompt 通行做法:"beyond the knowledge cutoff / rapidly changing /
  real-time data 才搜;稳定知识(概念/历史/写代码)直接答" + 触发场景枚举
  (live scores / current prices / breaking news / up-to-date info)。
- 搜索工具生态通行做法:关键词触发(latest / today / news)先走便宜规则过滤;
  接地(grounding)按"该不该查"打分过阈值才查 —— 我们的正则就是那个零成本的分。

三个出口:
- `FRESHNESS_DISCIPLINE`:system prompt 用的一条时效纪律(coding/prompt.py 静态段引用)。
- `freshness_signals(text)` / `has_freshness_signal(text)`:确定性正则检测(零模型零 token)。
- `annotate_task(text, has_web=)`:任务文本含时效信号 → 追加一行【时效提示】
  (有 web 工具=必须查证;无 web 工具=如实说查不到,绝不编)。幂等:已标注不重标。
"""
from __future__ import annotations

import re
import time

# ---- system prompt 纪律(一条,短;跟随现有 prompt 语言=中文) ----

FRESHNESS_DISCIPLINE = (
    "【时效纪律】涉及「现在/今天/最新/实时」的信息(新闻、价格、汇率、股价、天气、比分、"
    "版本号、发布动态等):**先用 web_search 查证(必要时 web_fetch 读原文)再回答,"
    "并给出来源;绝不凭训练记忆直接报数** —— 你的记忆有截止日期,这类数据大概率已过时。"
    "稳定知识(概念、历史、写代码、本地文件)不用联网。联网工具不可用或查不到时,"
    "如实说明「查不到实时信息」并标注记忆内容可能过时,绝不编。"
)

# ---- 任务标注(下发给 role/atom 的任务文本接缝用) ----

FRESHNESS_NOTE_MARKER = "【时效提示】"

_NOTE_WITH_WEB = (
    f"{FRESHNESS_NOTE_MARKER}此任务涉及实时/最新信息:必须先用 web_search 查证"
    "(必要时 web_fetch 读原文),以查证结果为准作答并附来源;不得凭训练记忆直接回答。"
    "查不到就如实说查不到,绝不编。"
)
_NOTE_NO_WEB = (
    f"{FRESHNESS_NOTE_MARKER}此任务涉及实时/最新信息,但当前没有可用的联网工具"
    "(web_search/web_fetch):请如实告知这部分查不到实时数据,凭记忆给出的内容必须"
    "明确标注「可能已过时」,绝不编造「最新」数据。"
)

# ---- 确定性时效信号(正则;零模型零 token,宁漏勿滥) ----
# 两类信号:① 时间词(今天/现在/最新/latest/today…) ② 易变领域词(汇率/股价/天气/比分/news…)。
# 单独出现任一类即算信号 —— 但易变领域词刻意收窄(如不收裸「价格」,防"把价格字段改成 float"误报)。

_ZH_TEMPORAL = (
    "今天|今日|昨天|明天|现在|此刻|目前|当前|最新|实时|最近|近期|本周|本月|今年|现价"
)
_ZH_VOLATILE = (
    "股价|股市|大盘|汇率|币价|油价|金价|房价|天气|气温|比分|赛果|战况|新闻|头条|行情|开盘|收盘"
)
_EN_TEMPORAL = (
    r"today|tonight|yesterday|tomorrow|right now|currently|latest|newest|most recent"
    r"|up[- ]to[- ]date|real[- ]?time|breaking|this (?:week|month|year)|as of now"
)
_EN_VOLATILE = (
    r"stock price|share price|exchange rate|weather forecast|the weather|breaking news"
    r"|the news|headline|current price|price of"
)

_ZH_RE = re.compile(f"(?:{_ZH_TEMPORAL}|{_ZH_VOLATILE})")
_EN_RE = re.compile(rf"\b(?:{_EN_TEMPORAL}|{_EN_VOLATILE})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def freshness_signals(text: str) -> list[str]:
    """返回命中的时效信号(原文片段,去重保序)。空列表 = 无信号。

    年份规则:提到**今年及以后**的年份(如任务写着 2026)= 时效信号;
    历史年份(2019 的论文)不是 —— 「历史问题」是业界公认的 never-search 反例。
    """
    t = text or ""
    hits: list[str] = []
    hits.extend(_ZH_RE.findall(t))
    hits.extend(m.group(0) for m in _EN_RE.finditer(t))
    this_year = time.localtime().tm_year
    for m in _YEAR_RE.finditer(t):
        if int(m.group(1)) >= this_year:
            hits.append(m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def has_freshness_signal(text: str) -> bool:
    """任务文本是否含时效信号(确定性,零 token)。"""
    return bool(freshness_signals(text))


def freshness_note(*, has_web: bool = True) -> str:
    """给时效任务附加的那行提示(has_web 决定「必须查」还是「如实说查不到」)。"""
    return _NOTE_WITH_WEB if has_web else _NOTE_NO_WEB


def annotate_task(text: str, *, has_web: bool = True) -> str:
    """任务文本含时效信号 → 追加一行【时效提示】;无信号原样返回。幂等(已标注不重标)。

    接缝:forge.generate_and_run(所有 drive/委派/圆桌的慢脑都过它)在组装首条
    user 消息前调用;console 委派路径(proposal_handlers/routes)如需在更上游
    的 requirement 文本上标注,也直接调这个函数。
    """
    t = text or ""
    if not t or FRESHNESS_NOTE_MARKER in t or not has_freshness_signal(t):
        return t
    return f"{t}\n\n{freshness_note(has_web=has_web)}"


__all__ = [
    "FRESHNESS_DISCIPLINE",
    "FRESHNESS_NOTE_MARKER",
    "freshness_signals",
    "has_freshness_signal",
    "freshness_note",
    "annotate_task",
]
