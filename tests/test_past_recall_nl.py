"""past_recall NL 识别 —— docs/69 Q4:聊天里"你当时怎么理解的"这类**过去认知问句**的确定性识别。

核心取舍(**宁漏勿误**,Hardy):误触发(把当下问题按旧时点召回)比漏识别更糟。
所以有大量**不该触发**的负例断言——尤其"上个月的报表做了吗"这种带时间词但不是问过去认知的。
"""
from __future__ import annotations

from datetime import datetime

from karvyloop.cognition.past_recall import (
    is_past_cognition_query,
    parse_past_ref,
    resolve_as_of,
)

# 固定"现在"= 2026-07-15 中午,让相对时间解析可断言(与真实 time.time 解耦)。
NOW = datetime(2026, 7, 15, 12, 0, 0).timestamp()


# ---- 识别:命中集(是过去认知问句)----

HIT = [
    "你当时以为我在哪家公司?",
    "上个月你觉得我住在哪?",
    "那时候你认为这个项目该怎么做?",
    "以前你是怎么理解我的作息的?",
    "你之前记得我喜欢什么口味的咖啡吗?",
    "3 月的时候你对我的判断是什么?",
    "去年你以为我在做什么工作?",
    "一开始你对我的印象是啥?",
    "你原先认为我是学什么的?",
    "昨天你还觉得我在北京吧?",
]


def test_hits_are_recognized():
    for q in HIT:
        assert is_past_cognition_query(q), f"该识别为过去认知问句却漏了:{q!r}"


# ---- 识别:不命中集(**不是**过去认知问句,绝不能触发)----

MISS = [
    # 带时间词但问的是"事办没办",不是问过去认知 —— 头号误触发风险(注释里点名的例子)
    "上个月的报表做了吗?",
    "上周那个任务完成了没?",
    "昨天让你查的东西有结果吗?",
    "去年的年度总结在哪个文件夹?",
    # 当下/未来指向:即便带认知动词也不该按旧时点召回
    "你现在觉得这个方案怎么样?",
    "你以后会怎么处理这类问题?",
    "接下来你打算怎么做?",
    "你觉得我该买哪台电脑?",          # 纯当下征询,无过去锚
    "你认为明天会下雨吗?",
    # 光有时间词、没有认知动词
    "上个月我们聊了什么?",
    "上周去哪儿玩了?",
    # 普通请求 / 闲聊
    "帮我订个会议室",
    "你好呀",
    "把这段代码改成异步的",
    "现在几点了?",
]


def test_misses_do_not_trigger():
    for q in MISS:
        assert not is_past_cognition_query(q), f"不该触发却误判成过去认知问句(会按旧时点召回):{q!r}"


def test_report_task_with_time_word_is_the_canonical_negative():
    """注释里点名的取舍例子:'上个月的报表做了吗' 绝不能触发 as_of。"""
    assert resolve_as_of("上个月的报表做了吗?", now=NOW) is None


# ---- 时刻解析:相对时间词 → 合理 epoch ----

def test_parse_last_month():
    t = parse_past_ref("上个月你以为我在哪?", now=NOW)
    assert t is not None
    d = datetime.fromtimestamp(t)
    assert (d.year, d.month) == (2026, 6)   # 7 月的上个月 = 6 月


def test_parse_last_week_is_seven_days_ago():
    t = parse_past_ref("上周你怎么想的?", now=NOW)
    assert t is not None
    assert abs((NOW - t) - 7 * 86400) < 2   # 约 7 天前


def test_parse_last_year():
    t = parse_past_ref("去年你以为我做什么工作?", now=NOW)
    assert t is not None
    assert datetime.fromtimestamp(t).year == 2025


def test_parse_explicit_month_this_year():
    t = parse_past_ref("3 月的时候你对我的判断?", now=NOW)   # 3 月已过 → 今年 3 月
    assert t is not None
    d = datetime.fromtimestamp(t)
    assert (d.year, d.month) == (2026, 3)


def test_parse_explicit_month_not_yet_reached_is_last_year():
    """现在是 7 月,问"10 月的时候" → 今年 10 月还没到 → 指去年 10 月(绝不指向未来)。"""
    t = parse_past_ref("10 月的时候你怎么理解的?", now=NOW)
    assert t is not None
    d = datetime.fromtimestamp(t)
    assert (d.year, d.month) == (2025, 10)


def test_parse_unresolvable_returns_none():
    assert parse_past_ref("你当时怎么想的?", now=NOW) is None   # 只有"当时"没有可锚定日期


# ---- resolve_as_of:识别 + 解析的合流(drive 侧唯一入口)----

def test_resolve_as_of_recognized_and_parsed():
    t = resolve_as_of("上个月你以为我在哪家公司?", now=NOW)
    assert t is not None and datetime.fromtimestamp(t).month == 6


def test_resolve_as_of_recognized_but_unparsable_returns_none():
    """识别到是过去认知问句(有'当时'锚),但没有可解析的具体时刻 → None(退当下召回,不猜时间)。"""
    assert resolve_as_of("你当时是怎么理解我的?", now=NOW) is None


def test_resolve_as_of_not_a_query_returns_none():
    assert resolve_as_of("帮我查下天气", now=NOW) is None
