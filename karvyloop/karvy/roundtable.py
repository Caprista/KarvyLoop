"""karvy/roundtable.py — 圆桌:多成员同场应答(ch4 KarvyChat pillar 1)。

群场里小卡当协调者(已落地);**圆桌**更进一步:把同一个问题抛给群里每个成员,
**各自从自己的视角/职务应答**,你一次看到 N 份回答(协作深研 / 定战略的形态)。

核心是纯的、可测:`run_roundtable` 接一个 `drive_member(member)` 注入(由 console 用
真 drive 机器按成员人格驱动),并发收集每个成员的 {speaker, text}。失败的成员被跳过、
不拖垮整桌。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


async def run_roundtable(
    intent: str,
    members: list,
    *,
    drive_member: Callable[[Any], Awaitable[dict]],
    max_seats: int = 6,
) -> list[dict]:
    """圆桌:同一 intent 抛给每个成员,各自应答,**并发**收集 [{speaker, text, ...}]。

    - drive_member(member) -> awaitable dict(至少含 speaker/text);抛错或返 None → 跳过该座。
    - max_seats:封顶并发座位数(防一桌几十人把 token/时长打爆;超出的不上桌,诚实截断)。
    - 顺序保持与 members 一致(gather 保序)。
    """
    seats = [m for m in members][:max(0, max_seats)]
    if not seats or not (intent or "").strip():
        return []

    async def _one(m):
        try:
            r = await drive_member(m)
            return r if (r and (r.get("text") or "").strip()) else None
        except Exception:
            return None

    results = await asyncio.gather(*[_one(m) for m in seats])
    return [r for r in results if r is not None]


async def run_roundtable_session(
    topic: str,
    members: list,
    *,
    member_reply: Callable[[Any, str, list], Awaitable[dict]],
    host_moderate: Callable[..., Awaitable[dict]],
    max_rounds: int = 3,
    max_seats: int = 6,
) -> dict:
    """**小卡主持的圆桌**(ch4 final):围绕 topic 多轮成员发言 + 小卡控场,差不多就收敛。

    主持人(小卡)干三件事:明确主题(开场已框 topic)、防跑偏 + 防冷场(每轮后控场:
    `host_moderate(..., final=False)` 决定 continue/converge,text=拉回主题/点名提示)、
    收敛产出(`host_moderate(..., final=True)` → 结论)。**token 纪律**:max_rounds 封顶 +
    小卡判定"差不多了"就提前收敛,不烧到底(Hardy:小卡控轮次,差不多就停或等 human 追问)。

    - member_reply(member, topic, transcript_so_far) -> {speaker, text}:一个成员就主题
      + 已有讨论发言。
    - 返回 {topic, transcript:[{round,speaker,text}], rounds, converged, conclusion}。
    """
    if not (topic or "").strip() or not members:
        return {"topic": topic, "transcript": [], "rounds": 0,
                "converged": False, "conclusion": ""}
    transcript: list = []
    converged = False
    rounds = 0
    for r in range(max(1, max_rounds)):
        rounds = r + 1
        snapshot = list(transcript)   # 这一轮成员看到的是"已有讨论"(防把自己刚说的喂回去)
        replies = await run_roundtable(
            topic, members,
            drive_member=lambda m, _s=snapshot: member_reply(m, topic, _s),
            max_seats=max_seats,
        )
        if not replies:
            break
        for rep in replies:
            transcript.append({"round": rounds, **rep})
        # 小卡控场:继续 or 收敛(差不多就停 —— token 纪律)
        try:
            d = await host_moderate(topic, transcript, final=False) or {}
        except Exception:
            d = {"action": "converge"}
        if d.get("action") == "converge":
            converged = True
            break
    # 小卡收敛产出(结论 → 上层写进认知库)
    conclusion = ""
    try:
        fin = await host_moderate(topic, transcript, final=True) or {}
        conclusion = fin.get("text", "")
    except Exception:
        conclusion = ""
    return {"topic": topic, "transcript": transcript, "rounds": rounds,
            "converged": converged, "conclusion": conclusion}


__all__ = ["run_roundtable", "run_roundtable_session"]
