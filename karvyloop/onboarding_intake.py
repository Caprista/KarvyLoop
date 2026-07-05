"""onboarding_intake — 人格采集器(「第一个 10 分钟」旅程开头的 3-5 问)。

产品目标:新用户配好 key 后、点第一个演示 chip 前,用 4 个**有区分度**的问题把
"你怎么拍板"的第一批种子当场种进真实机制 —— 不是星座测试:每题的不同选项必须
导向**可观察的行为差异**(答案立即改变系统行为,且用户随时能在决策偏好面板看到/改/撤)。

种进哪(不新造存储,全部复用决策偏好 Belief,docs/02 §11):
- 每个答案 = 一条 `decision_pref` Belief(`crystallize/decision_pref.make_decision_pref_belief`),
  `explicit=True`(用户明说)、`status="confirmed"`(用户亲手选的 = 拍过板)、
  provenance 带 `origin="user_explicit"` + `intake_q`/`intake_opt`(重来时按题定位替换);
- 立即生效:prealign_block 在每次提案前注入这些标准(预对齐),违背即拦(Cut2)同样认它们;
- `filing` 一题额外被文件管家第一课**确定性**消费(karvy/butler_lesson:by_type/by_time
  两种整理方案,答案不同→方案不同,行为差异当场可见)。

文案纪律(招牌是「越用越像你」):回执说"我先记下你的几条标准,以后拍板时摆在你手边"
(预对齐),**绝不说"我懂你了"** —— 4 个答案只是种子,不是理解。

可跳过可重来:跳过 = 零种子、不惩罚;旅程「重看」(stage→fresh)同时重置采集器,
重答会**替换**同题旧种子(按 provenance.intake_q 定位归档旧条再写新条,不留自相矛盾)。
老用户(旅程已 done/skipped)不弹 —— 采集器只活在旅程 fresh 阶段,搭旅程既有闸门。
"""
from __future__ import annotations

import time
from typing import Any, Optional

#: 采集问题(唯一真理源;前端按 UI 语言取 en/zh)。
#: 设计原则:每题两个选项都**体面**(没有"正确答案"),但导向可观察的行为差异。
INTAKE_QUESTIONS: tuple = (
    {
        "id": "output_style",
        "kind": "taste",
        "question": {
            "en": "When I hand you results, you'd rather I…",
            "zh": "交结果的时候,你更希望我…",
        },
        "options": (
            {
                "id": "conclusion_first",
                "label": {"en": "Lead with the conclusion, process after",
                          "zh": "先给结论,再给过程"},
                "content": {
                    "en": "When reporting or writing for you, lead with the conclusion, "
                          "then the supporting process and evidence.",
                    "zh": "产出先给结论,再给过程与依据。",
                },
            },
            {
                "id": "process_first",
                "label": {"en": "Walk the process first, land on the conclusion",
                          "zh": "先铺过程,最后收结论"},
                "content": {
                    "en": "When reporting or writing for you, walk through the process and "
                          "evidence first, then land on the conclusion.",
                    "zh": "产出先铺过程与依据,最后收结论。",
                },
            },
        ),
    },
    {
        "id": "unsure",
        "kind": "standing",
        "question": {
            "en": "When your agents aren't sure, they should…",
            "zh": "你的 agent 拿不准的时候,应该…",
        },
        "options": (
            {
                "id": "ask_first",
                "label": {"en": "Ask me first", "zh": "先来问我"},
                "content": {
                    "en": "When unsure, ask the user first instead of acting on a guess.",
                    "zh": "拿不准时,先来问我,别按猜测直接做。",
                },
            },
            {
                "id": "draft_first",
                "label": {"en": "Make their best call, show me a finished draft",
                          "zh": "按你的判断先做完给我看"},
                "content": {
                    "en": "When unsure, make your best call, finish a draft, and present it "
                          "with the trade-offs you took — the user decides from there.",
                    "zh": "拿不准时,按你的判断先做出一版给我看,并附上你的取舍——最后我来定。",
                },
            },
        ),
    },
    {
        "id": "tone",
        "kind": "taste",
        "question": {
            "en": "Your preferred working tone:",
            "zh": "你偏好的沟通文风:",
        },
        "options": (
            {
                "id": "direct",
                "label": {"en": "Direct and sharp", "zh": "直给犀利"},
                "content": {
                    "en": "Communicate directly and sharply: lead with the point, name "
                          "problems plainly, skip the padding.",
                    "zh": "沟通直给犀利:直接说重点、直说问题,不用铺垫。",
                },
            },
            {
                "id": "gentle",
                "label": {"en": "Measured and thorough", "zh": "温和周全"},
                "content": {
                    "en": "Communicate in a measured, thorough way: give context, cover the "
                          "caveats, mind the tone.",
                    "zh": "沟通温和周全:讲清来龙去脉、照顾语气、把注意事项说全。",
                },
            },
        ),
    },
    {
        # 这一题被文件管家第一课**确定性**消费(butler_lesson.filing_mode_from_memory):
        # by_type → 图片/文档/安装包分文件夹;by_time → 按 YYYY-MM 归档 —— 答案不同,
        # 第一课给出的整理方案就不同(行为差异当场可见,不是问卷装饰)。
        "id": "filing",
        "kind": "standing",
        "question": {
            "en": "When a folder like Downloads gets messy, you'd sort it…",
            "zh": "下载夹这类杂物堆乱了,你习惯…",
        },
        "options": (
            {
                "id": "by_type",
                "label": {"en": "By type (Images / Documents / Installers)",
                          "zh": "按类型归类(图片/文档/安装包)"},
                "content": {
                    "en": "When organizing files, group them by type (Images / Documents / "
                          "Installers…), not by date.",
                    "zh": "整理文件按类型归类(图片/文档/安装包…),而不是按时间。",
                },
            },
            {
                "id": "by_time",
                "label": {"en": "By time (2026-06 / 2026-07)", "zh": "按时间归类(2026-06/2026-07)"},
                "content": {
                    "en": "When organizing files, group them by time (year-month folders), "
                          "not by type.",
                    "zh": "整理文件按时间归类(年-月文件夹),而不是按类型。",
                },
            },
        ),
    },
)

#: 种子的 provenance 标记:origin=用户明说(区别于 LLM 从行为推断的结晶);
#: intake_q/intake_opt=按题定位(重答替换 + 第一课确定性消费)。
INTAKE_ORIGIN = "user_explicit"


def get_question(qid: str) -> Optional[dict]:
    for q in INTAKE_QUESTIONS:
        if q["id"] == qid:
            return q
    return None


def _get_option(q: dict, opt_id: str) -> Optional[dict]:
    for o in q["options"]:
        if o["id"] == opt_id:
            return o
    return None


def questions_payload() -> list:
    """给前端的问题清单(en/zh 都带,前端按 UI 语言取 —— JOURNEY_TASKS 同款先例)。"""
    return [
        {
            "id": q["id"],
            "question": dict(q["question"]),
            "options": [{"id": o["id"], "label": dict(o["label"])} for o in q["options"]],
        }
        for q in INTAKE_QUESTIONS
    ]


def _loc(d: dict, locale: str) -> str:
    return str(d.get(locale) or d.get("en") or "").strip()


def make_seed_belief(q: dict, opt: dict, *, locale: str = "en", now: Optional[float] = None):
    """一个答案 → 一条决策偏好 Belief(复用 §11 机制,不另起炉灶)。

    explicit=True(用户明说,资格门 1 次即够)、status="confirmed"(用户亲手选的=拍过板,
    以后相反决策只降影响、绝不静默删);evidence 带人话 gist(卡上"来自你的拍板"回执可核)。
    """
    from karvyloop.crystallize.decision_pref import initial_strength, make_decision_pref_belief
    if now is None:
        now = time.time()
    content = _loc(opt["content"], locale)
    gist = ("onboarding: " if locale != "zh" else "入门问答:") \
        + _loc(q["question"], locale) + " → " + _loc(opt["label"], locale)
    b = make_decision_pref_belief(
        content, q.get("kind", "taste"),
        scope="personal",
        evidence=[{"ts": now, "decision": "STATE", "gist": gist}],
        strength=initial_strength(explicit=True, support_count=1),
        status="confirmed", explicit=True, now=now,
    )
    # 采集器专属标记(重答替换 + butler_lesson 确定性消费的定位键)
    b.provenance["origin"] = INTAKE_ORIGIN
    b.provenance["intake_q"] = q["id"]
    b.provenance["intake_opt"] = opt["id"]
    return b


def _existing_intake_seeds(mem: Any, qid: str) -> list:
    """认知库里同题旧种子(重答时归档替换,不留自相矛盾)。"""
    out, seen = [], set()
    for sc in ("personal", "domain"):
        for b in mem.index.all(sc):
            if id(b) in seen:
                continue
            seen.add(id(b))
            prov = getattr(b, "provenance", None) or {}
            if prov.get("source") == "decision_pref" and prov.get("intake_q") == qid:
                out.append(b)
    return out


def seed_answers(answers: dict, *, mem: Any, locale: str = "en",
                 now: Optional[float] = None) -> list:
    """把答案种进认知库(mem=MemoryManager;write 内部落盘 beliefs.json)。

    - 未知题/未知选项:静默忽略(宁缺勿毒,不写垃圾进决策画像);
    - 同题旧种子(重来场景):先归档再写新条(替换语义);
    - 返回真正写入的 Belief 列表(回执/测试用)。
    """
    seeded: list = []
    if mem is None:
        return seeded
    for qid, opt_id in (answers or {}).items():
        q = get_question(str(qid))
        if q is None:
            continue
        opt = _get_option(q, str(opt_id))
        if opt is None:
            continue
        for old in _existing_intake_seeds(mem, q["id"]):
            try:
                mem.archive(old)
            except Exception:
                pass
        b = make_seed_belief(q, opt, locale=locale, now=now)
        mem.write(b)
        seeded.append(b)
    return seeded


__all__ = [
    "INTAKE_QUESTIONS", "INTAKE_ORIGIN",
    "get_question", "questions_payload", "make_seed_belief", "seed_answers",
]
