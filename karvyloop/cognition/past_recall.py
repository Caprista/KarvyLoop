"""past_recall — 聊天里"你**当时**怎么理解的?"这类**过去认知问句**的确定性识别 + 时刻解析。

docs/69 Q4 收尾:把 recall_block(as_of=T) 接到 drive 侧。识别用**纯正则**(热路径零成本、
零 LLM),解析用小工具(相对时间词 → epoch 秒)。命中 → 用 as_of=T 召回"那个时点算数的记忆",
让小卡回答"上个月我以为你在哪家公司"用的是当时的旧认知,而不是当下已更新的事实。

设计取舍(**宁漏勿误**,Hardy 明确):误触发(把当下问题按旧时点召回,漏掉刚学到的新事实)
比漏识别(退化成当下召回,顶多没吃到时点红利)**更糟**。所以门收得很窄:
  - 光有时间词(「上个月」)**不触发** —— "上个月的报表做了吗"是问任务,不是问过去认知;
  - 必须同时命中**认知动词**(以为/觉得/认为/理解/记得/看法/印象/以为是…),且指向**过去**
    (「当时」「那时候」「以前」「之前」或一个可解析的过去时间词);
  - 未来/现在指向(「以后」「现在」「接下来」)一律不触发。
解析不出确切时刻(只有「当时」没有可锚定的日期)→ 仍触发识别但 **as_of=None**(照当下召回,
绝不猜一个时刻);调用方据此决定是否加 as_of 标。

对外零依赖(标准库),供 console/routes.py 与 console/ws.py 两条 drive 入口共用一份逻辑。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

# ---- 认知动词:问的是"你(小卡)脑子里当时怎么想的",不是问某件事办没办 ----
# 收窄到"关于认知/看法/记忆"的词;「知道」单独处理(易和"你知道X吗"这类当下问混,需强过去锚)。
_COGNITION = (
    "以为", "认为", "觉得", "理解", "记得", "印象", "看法", "想法", "判断", "估计",
    "了解成", "理解成", "以为是", "记成", "当成",
)
# 过去指向词(时间锚):有其一,配上认知动词 = 过去认知问句。
_PAST_MARKERS = ("当时", "那时候", "那时", "以前", "之前", "早先", "原先", "起初", "最初", "一开始")
# 明确指向未来/现在 → 一票否决(即便凑巧带了认知动词,也不是"回忆过去认知")。
_NONPAST = ("以后", "之后会", "接下来", "将来", "未来", "现在", "如今", "目前", "眼下", "此刻", "待会", "等会")

# 相对时间词 → 解析成"那个时段的代表时刻"(epoch 秒)。只认几个高频、无歧义的。
# 「上个月」= 上个自然月的月中(取中点,避免月初/月末边界打架);「上周」= 7 天前;「去年」= 365 天前。
_REL_DAYS = {
    "上周": 7, "上星期": 7, "上礼拜": 7,
    "前天": 2, "昨天": 1,
    "上上周": 14,
}


def _month_ref(now: float, months_back: int) -> float:
    """now 往前 months_back 个自然月 → 该月 15 号本地零点的 epoch(月中点,躲开月初/月末边界)。"""
    dt = datetime.fromtimestamp(now)
    y, m = dt.year, dt.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    try:
        anchor = datetime(y, m, 15)
    except ValueError:
        return now - months_back * 30 * 86400   # 兜底:粗略天数(不会走到,防御性)
    return anchor.timestamp()


def parse_past_ref(text: str, *, now: Optional[float] = None) -> Optional[float]:
    """从问句里解析出**过去时刻** T(epoch 秒);解析不出 → None(绝不猜)。

    只认无歧义的相对时间词:上个月 / 上周 / 上星期 / 去年 / 昨天 / 前天 / 「N 月(的时候)」。
    「N 月」= 今年该月 15 号(若该月还没到 → 去年同月,避免指向未来)。绝对年月不做(个人尺度够用即止)。
    """
    if not text:
        return None
    now = time.time() if now is None else now
    s = text

    # 上个月 / 上上个月
    if "上上个月" in s or "上上月" in s:
        return _month_ref(now, 2)
    if "上个月" in s or "上月" in s:
        return _month_ref(now, 1)
    # 去年
    if "去年" in s or "前年" in s:
        back = 2 if "前年" in s else 1
        dt = datetime.fromtimestamp(now)
        try:
            return datetime(dt.year - back, 7, 1).timestamp()   # 那年年中
        except ValueError:
            return now - back * 365 * 86400
    # 上周 / 昨天 / 前天 等固定天数
    for word, days in _REL_DAYS.items():
        if word in s:
            return now - days * 86400
    # 「X 月(的时候)」:1-12 月 → 今年该月 15 号;若已过未来 → 去年同月
    m = re.search(r"(?<![0-9])([1-9]|1[0-2])\s*月", s)
    if m:
        mon = int(m.group(1))
        dt = datetime.fromtimestamp(now)
        year = dt.year
        try:
            cand = datetime(year, mon, 15)
        except ValueError:
            return None
        if cand.timestamp() > now:            # 今年这个月还没到 → 指的是去年
            try:
                cand = datetime(year - 1, mon, 15)
            except ValueError:
                return None
        return cand.timestamp()
    return None


def is_past_cognition_query(text: str) -> bool:
    """这句是不是在问"你(小卡)**过去**是怎么理解/记得/认为的"(而非问某事办没办、或问当下)?

    门(宁漏勿误):
      1) 不含未来/现在指向词(_NONPAST);
      2) 至少一个认知动词(_COGNITION);
      3) 且【有过去锚词(_PAST_MARKERS)】或【parse_past_ref 能解出一个过去时刻】。
    只有(2)没(3) = 可能是当下问"你觉得X怎么样",不触发。
    """
    if not text:
        return False
    s = text.strip()
    if any(w in s for w in _NONPAST):
        return False
    if not any(v in s for v in _COGNITION):
        return False
    if any(w in s for w in _PAST_MARKERS):
        return True
    return parse_past_ref(s) is not None


def resolve_as_of(text: str, *, now: Optional[float] = None) -> Optional[float]:
    """drive 侧单一入口:是过去认知问句 **且** 能解析出时刻 → 返回 T;否则 None(照当下召回)。

    注意:`is_past_cognition_query` 命中但只有「当时」没有可锚定日期 → 这里返 None
    (识别到了、但没时刻可用 → 不启用 as_of,退化当下召回,绝不编时间)。调用方拿到非 None
    才在解释链上加"按 X 时点的记忆"标。
    """
    if not is_past_cognition_query(text):
        return None
    return parse_past_ref(text, now=now)


__all__ = ["parse_past_ref", "is_past_cognition_query", "resolve_as_of"]
