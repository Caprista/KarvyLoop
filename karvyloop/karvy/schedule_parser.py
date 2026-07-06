"""schedule_parser — 自然语言 → 定时任务(Hardy:我多半用语言描述,语义识别要跟上)。

把"每天早上 8 点把昨天进展汇总给我" 这类话,解析成 {cron, intent, title, target_role}。
cron 是机器执行的真相,NL 是入口。和 result_classifier 一个套路:小 LLM 调用 + 严格 JSON + 兜底。

铁律:解析不出 / cron 非法 / 任何异常 → 返回 None(让上层提示"没听懂,换种说法"),绝不乱编一个时间。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_SYS = (
    "你把用户的**定时任务**描述解析成 JSON。字段:\n"
    "- cron: 标准 5 字段 cron 表达式(分 时 日 月 周)。例:每天8点=`0 8 * * *`;每周一9点=`0 9 * * 1`;"
    "每小时=`0 * * * *`;每30分钟=`*/30 * * * *`。\n"
    "- intent: 到点要做的事(去掉时间词,保留干什么)。\n"
    "- title: 极短标题(≤10 字)。\n"
    "- target_role: 若指明要**某个角色**去做,填那个角色名;没指明填空串。\n"
    "参考相对时间用『当前时间』——它带显式时区 offset;推算「每天下午3点」「明早」「下周一」等相对时间"
    "**一律按该时区**,生成的 cron 也按此时区语义(cron 由本机按本地时区执行)。**只输出 JSON,无其它**:\n"
    '{"cron":"0 8 * * *","intent":"汇总昨天进展","title":"每日进展","target_role":""}\n'
    "解析不出明确的时间规律 → 输出 {\"cron\":\"\"}(不要瞎编)。"
)


def local_now_str() -> str:
    """本机当前时间 → ISO8601 显式 offset + 星期 + 时区标注,如 `2026-07-06T15:30:00+08:00 Sunday (本机时区,UTC+08:00)`。

    业界做法:给 LLM 参考的"当前时间"必须声明时区,否则"每天下午3点"/"明早"这类相对时间有错解风险。
    只显式化、不换算——语义仍是服务器本地时区(cron 由本机执行),croniter 执行侧不动。
    """
    now = datetime.now().astimezone()   # 标准库拿 offset-aware 本地时间,不引第三方
    z = now.strftime("%z")              # 如 +0800
    return f"{now.isoformat(timespec='seconds')} {now.strftime('%A')} (本机时区,UTC{z[:3]}:{z[3:]})"


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def make_schedule_parser(gateway: Any, model_ref: str = "") -> Optional[Callable[[str, str], Optional[dict]]]:
    """造同步解析器闭包 (description, now_str) → {cron,intent,title,target_role} | None。

    gateway 为空 → None。内部 asyncio.run 调 gateway(在同步上下文跑,合法)。
    """
    if gateway is None:
        return None

    def parse(description: str, now_str: str = "") -> Optional[dict]:
        import asyncio

        from karvyloop.gateway import ResolveScope
        from karvyloop.gateway.system import SystemPrompt
        from karvyloop.karvy.scheduler import _valid_cron
        usr = f"当前时间:{now_str}\n定时任务描述:{(description or '')[:500]}"
        out = ""

        async def _go():
            nonlocal out
            ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
            async for ev in gateway.complete([{"role": "user", "content": usr}], [], ref,
                                             system=SystemPrompt(static=[_SYS])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
        try:
            asyncio.run(_go())
        except Exception as e:
            logger.warning(f"[schedule_parser] 解析失败: {e}")
            return None
        d = _extract_json(out)
        cron = str(d.get("cron", "") or "").strip()
        intent = str(d.get("intent", "") or "").strip()
        if not _valid_cron(cron) or not intent:
            return None   # 没听懂明确时间规律 / 无意图 → 让上层提示重说,不瞎编
        return {
            "cron": cron, "intent": intent,
            "title": (str(d.get("title", "") or "").strip() or intent)[:60],
            "target_role": str(d.get("target_role", "") or "").strip(),
        }

    return parse


__all__ = ["local_now_str", "make_schedule_parser"]
