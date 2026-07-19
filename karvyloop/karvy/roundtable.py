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

# ---- 结构化收口(主持人裁决)常量 ----
# 收口共识阈值:主持人每轮给 consensus ∈ [0,1],达到它就提前收敛。
# 拍脑袋初值 —— 待 Trace 真数据(consensus 分布 × 收口后满意度)标定。
CONSENSUS_THRESHOLD = 0.75
# 默认轮数上限(调用方可按议题复杂度传入覆盖;任何情况轮数到顶必停 —— 硬兜底)。
DEFAULT_MAX_ROUNDS = 3
# 少数派报告最早生效轮次:第 2 轮起,若只剩 1 个孤立反对 → 综合多数收口 + 记 dissent
# (魔鬼代言人/杠精的标准解法:有价值的反对留档,但不再为它烧轮次)。
MINORITY_REPORT_MIN_ROUND = 2
# dissent 留档上限(条数/单条长度)—— 宁短勿爆,收口产物不被超长分歧撑破。
_MAX_DISSENTS = 8
_MAX_DISSENT_LEN = 200


def normalize_moderation(d: Any) -> dict:
    """把主持人每轮裁决规整成统一形状(纯函数,宁空勿毒)。

    接受两种输入:
    - 旧式:``{"action": "converge"|"continue"}``(词法判定的向后兼容);
    - 结构化:``{"consensus": 0-1, "open_dissents": [...], "recommendation": "..."}``。

    返回 ``{action, consensus, open_dissents, recommendation, structured}``:
    - 显式 action 合法则尊重之;否则结构化输入按 consensus ≥ CONSENSUS_THRESHOLD 定;
    - 类型不对的字段一律丢弃(不猜);完全无可用信号 → continue(没到就再一轮)。
    """
    d = d if isinstance(d, dict) else {}
    action = str(d.get("action") or "").strip().lower()
    consensus = None
    raw_c = d.get("consensus")
    if isinstance(raw_c, (int, float)) and not isinstance(raw_c, bool):
        consensus = min(1.0, max(0.0, float(raw_c)))
    raw_dis = d.get("open_dissents")
    dissents: list[str] = []
    if isinstance(raw_dis, list):
        dissents = [s.strip()[:_MAX_DISSENT_LEN] for s in raw_dis
                    if isinstance(s, str) and s.strip()][:_MAX_DISSENTS]
    rec = d.get("recommendation")
    recommendation = rec.strip()[:400] if isinstance(rec, str) else ""
    structured = consensus is not None
    if action not in ("converge", "continue"):
        action = "converge" if (structured and consensus >= CONSENSUS_THRESHOLD) else "continue"
    return {"action": action, "consensus": consensus, "open_dissents": dissents,
            "recommendation": recommendation, "structured": structured}


async def run_roundtable(
    intent: str,
    members: list,
    *,
    drive_member: Callable[[Any], Awaitable[dict]],
    max_seats: int = 6,
    concurrency: int = 6,
) -> list[dict]:
    """圆桌:同一 intent 抛给每个成员,各自应答,**分批并发**收集 [{speaker, text, ...}]。

    - drive_member(member) -> awaitable dict(至少含 speaker/text);抛错或返 None → 跳过该座。
    - max_seats:**上桌人数**上限(50+ 大桌压测可调大;超出的不上桌,诚实截断 —— 由调用方报)。
    - concurrency:**同时**在打模型的座位数(批大小)。和 max_seats 解耦:一桌 50 人也只 N 路
      并发,**别 50 路同时打一把 key 把响应截断**(多渠道并发截断的老教训)。
    - 顺序保持与 members 一致。
    """
    seats = [m for m in members][:max(0, max_seats)]
    if not seats or not (intent or "").strip():
        return []
    batch = max(1, int(concurrency))

    async def _one(m):
        try:
            r = await drive_member(m)
            return r if (r and (r.get("text") or "").strip()) else None
        except Exception:
            return None

    out: list[dict] = []
    for i in range(0, len(seats), batch):     # 分批:每批最多 `batch` 路并发,批间串行
        wave = await asyncio.gather(*[_one(m) for m in seats[i:i + batch]])
        out.extend(r for r in wave if r is not None)
    return out


async def run_roundtable_session(
    topic: str,
    members: list,
    *,
    member_reply: Callable[[Any, str, list], Awaitable[dict]],
    host_moderate: Callable[..., Awaitable[dict]],
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_seats: int = 6,
    concurrency: int = 6,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """**小卡主持的圆桌**(ch4 final):围绕 topic 多轮成员发言 + 小卡控场,差不多就收敛。

    主持人(小卡)干三件事:明确主题(开场已框 topic)、防跑偏 + 防冷场(每轮后控场:
    `host_moderate(..., final=False)` 给**结构化裁决** {consensus, open_dissents,
    recommendation},或向后兼容的 {"action": converge/continue}),收敛产出
    (`host_moderate(..., final=True)` → 结论)。**token 纪律**:max_rounds 封顶(可配,
    默认 DEFAULT_MAX_ROUNDS;**任何情况轮数到顶必停** —— 硬兜底,圆桌永不无限烧)+
    结构化收口:consensus ≥ CONSENSUS_THRESHOLD 提前收敛。

    **少数派报告规则**(杠精免疫):第 MINORITY_REPORT_MIN_ROUND 轮起,若只剩 1 条孤立
    open_dissent → 不再为它烧轮次,综合多数收口,dissent 记进产物(`dissents`)留档 ——
    有价值的反对不丢、无谓抬杠拖不死圆桌。

    - member_reply(member, topic, transcript_so_far) -> {speaker, text}:一个成员就主题
      + 已有讨论发言。
    - should_cancel():每轮**开始前**查刹车 —— True 就**不再起新一轮**(§0.7 逃生门:人踩刹车),
      拿已有 transcript 直接收敛返回(cancelled=True)。
    - 返回 {topic, transcript:[{round,speaker,text}], rounds, converged, conclusion,
      cancelled, consensus, dissents}(consensus=最后一次结构化度量,旧式主持人 → None;
      dissents=收口时仍开放的关键分歧,保留进产物)。
    """
    if not (topic or "").strip() or not members:
        return {"topic": topic, "transcript": [], "rounds": 0,
                "converged": False, "conclusion": "", "cancelled": False,
                "consensus": None, "dissents": []}
    transcript: list = []
    converged = False
    cancelled = False
    rounds = 0
    consensus: float | None = None
    dissents: list = []
    for r in range(max(1, max_rounds)):
        # §0.7 逃生门:开新一轮前查刹车 —— 中止就不再烧下一轮 token,拿已有的收敛。
        if should_cancel is not None:
            try:
                if should_cancel():
                    cancelled = True
                    break
            except Exception:
                pass
        rounds = r + 1
        snapshot = list(transcript)   # 这一轮成员看到的是"已有讨论"(防把自己刚说的喂回去)
        replies = await run_roundtable(
            topic, members,
            drive_member=lambda m, _s=snapshot: member_reply(m, topic, _s),
            max_seats=max_seats, concurrency=concurrency,
        )
        if not replies:
            break
        for rep in replies:
            transcript.append({"round": rounds, **rep})
        # 小卡控场:结构化裁决(consensus/dissents)或旧式一词;主持人炸了 → 收敛止损。
        try:
            d = await host_moderate(topic, transcript, final=False) or {}
        except Exception:
            d = {"action": "converge"}
        v = normalize_moderation(d)
        if v["structured"]:
            consensus = v["consensus"]
        dissents = v["open_dissents"]     # 最新分歧留档(收口/到顶时它就是少数派报告)
        if v["action"] == "converge":
            converged = True              # 共识达阈值(或旧式主持人拍板)→ 提前收口
            break
        # 少数派报告:第 2 轮起只剩 1 条孤立反对 → 综合多数收口 + dissent 留档,
        # 不再为杠精/魔鬼代言人烧轮次(仍算 converged:多数已齐)。
        if v["structured"] and rounds >= MINORITY_REPORT_MIN_ROUND and len(dissents) == 1:
            converged = True
            break
    # 轮数到顶(for 耗尽)= 硬兜底停轮:converged 保持 False,dissents 如实留档。
    # 被中止 → 不再烧 host 的收敛调用(省 token);拿已有 transcript 老实返回。
    conclusion = ""
    if not cancelled:
        try:
            fin = await host_moderate(topic, transcript, final=True) or {}
            conclusion = fin.get("text", "")
        except Exception:
            conclusion = ""
    return {"topic": topic, "transcript": transcript, "rounds": rounds,
            "converged": converged, "conclusion": conclusion, "cancelled": cancelled,
            "consensus": consensus, "dissents": list(dissents)}


__all__ = ["run_roundtable", "run_roundtable_session", "normalize_moderation",
           "CONSENSUS_THRESHOLD", "DEFAULT_MAX_ROUNDS", "MINORITY_REPORT_MIN_ROUND"]
