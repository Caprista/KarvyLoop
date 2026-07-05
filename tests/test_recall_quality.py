"""test_recall_quality — 召回**命中率/延迟**回归锁(#61 研判④:压测固化进 CI)。

此前全仓只有行为单测,没有一个"召回质量数字"——同义改写 recall@8=0.00 的结构性盲区
就是这么漏的(标签抽取/缓存早就存在,却从没接进 Belief 召回种子)。本文件把压测收编成
可控小基准(纯虚构语料,自带诚实性断言),锁四件:

① 词面 recall@8 地板(≥0.9):措辞有交集时该拿的都拿到——无向量路线的主力面不许回归。
② 同义改写 recall@8 地板(≥0.8):query 与目标**零词面交集**时,靠预计算概念标签种子召回
   (修前 0.00 → 修后 1.00;数据集自证零交集,防语料腐化后假绿)。
③ 优雅退化:无标签(老库)+ 零词面交集 → 返回**空**,绝不投毒(J10 教训:没相关知识是
   正确答案);词面命中不因标签层存在而受伤。
④ 延迟上界:高重复措辞库(蒸馏产物常态)N=5000 的召回热路径必须毫秒级——hub token
   postings 无界曾把它拖到 ~2s(10k 时 4.4s),修后 ~40ms;上界 750ms 留足 CI 抖动余量,
   仍能逮住回归。

方法论:不追公共基准(厂商互撕数字不可信),自建小基准锁自己的行为。语料全部虚构。
"""
from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.concepts import ConceptCache  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.cognition.spread import spreading_activation_recall  # noqa: E402
from karvyloop.context.relevance import overlap_score  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


# ---- 虚构语料:12 主题 × 3 条目标 + 干扰条补满 ~400 ----
# (kw=目标条里的主题词, para=同义改写 query[与全库零词面交集,有断言自证], tags=模拟 LLM 标签)
TOPICS = [
    ("深色主题", "夜间模式", ["夜间模式", "界面外观"]),
    ("周报草稿", "每周总结文档", ["每周总结", "写作流程"]),
    ("拿铁咖啡", "牛奶浓缩饮品", ["牛奶浓缩饮品", "饮品口味"]),
    ("羽毛球拍", "挥拍运动器材", ["挥拍运动", "锻炼装备"]),
    ("盆栽绿萝", "室内植物养护", ["室内植物", "家居打理"]),
    ("山地骑行", "越野单车路线", ["越野单车", "户外活动"]),
    ("水彩画笔", "颜料绘画工具", ["颜料绘画", "美术用品"]),
    ("桌游之夜", "棋牌聚会活动", ["棋牌聚会", "朋友社交"]),
    ("烤箱面包", "烘焙点心制作", ["烘焙点心", "厨房手艺"]),
    ("旧胶片机", "复古相机收藏", ["复古相机", "收藏爱好"]),
    ("溪流钓鱼", "垂钓水边休闲", ["垂钓休闲", "户外活动"]),
    ("星空观测", "天文望远镜使用", ["天文望远镜", "夜空爱好"]),
]

_FILLER_SUBJ = ["档案室编号", "值班表次序", "货架标签", "门禁卡权限", "打印机队列", "会议室预订"]
_FILLER_PRED = ["每季度轮换一次", "由前台统一登记", "超期后自动作废", "需要提前申请",
                "按楼层划分", "以内部邮件为准"]

_NOW = 1_700_000_000.0


def _belief(content: str, ts: float = _NOW) -> Belief:
    return Belief(content=content, provenance={"source": "ingest", "ts": ts},
                  freshness_ts=ts, scope="personal")


def _corpus(n: int = 400):
    """(beliefs, tags) 对齐列表:每主题 3 条目标(带标签),干扰条无标签。全虚构。"""
    beliefs, tags = [], []
    for kw, _, tag in TOPICS:
        for j in range(3):
            beliefs.append(_belief(f"用户在{kw}方面有固定偏好,第{j + 1}条备忘,和{kw}直接相关。",
                                   ts=_NOW - (j + 1) * 1000))
            tags.append(list(tag))
    i = 0
    while len(beliefs) < n:
        s = _FILLER_SUBJ[i % len(_FILLER_SUBJ)]
        p = _FILLER_PRED[(i * 7 + 3) % len(_FILLER_PRED)]
        beliefs.append(_belief(f"{s}{i}:{p},第{i}项。", ts=_NOW - (i + 5) * 500))
        tags.append([])
        i += 1
    return beliefs, tags


def _recall_at_8(beliefs, query, target_kw, concepts=None) -> float:
    top = spreading_activation_recall(beliefs, query, concepts=concepts, top_k=8)
    return sum(1 for b in top if target_kw in b.content) / 3.0


def test_dataset_is_honest_zero_lexical_overlap():
    """自证:同义改写 query 与**全库任何一条**都零词面交集——②的召回只能来自标签层。
    语料日后被改动导致偷渡词面交集时,这里先红,防假绿。"""
    beliefs, _ = _corpus()
    for _, para, _t in TOPICS:
        for b in beliefs:
            assert overlap_score(para, b.content) == 0, (
                f"数据集腐化:改写 query {para!r} 与 {b.content!r} 有词面交集")


def test_lexical_recall_floor_without_tags():
    """①老库(无标签):词面 query recall@8 ≥ 0.9(修标签层/加 IDF 都不许伤这条主力面)。"""
    beliefs, _ = _corpus()
    r = statistics.mean(_recall_at_8(beliefs, f"{kw}怎么弄", kw) for kw, _, _ in TOPICS)
    assert r >= 0.9, f"词面 recall@8 跌破地板: {r:.2f} < 0.9"


def test_lexical_recall_floor_with_tags():
    """③标签层在场时词面命中不受伤(recall@8 ≥ 0.9)。"""
    beliefs, tags = _corpus()
    r = statistics.mean(_recall_at_8(beliefs, f"{kw}怎么弄", kw, concepts=tags)
                        for kw, _, _ in TOPICS)
    assert r >= 0.9, f"标签层伤了词面命中: {r:.2f} < 0.9"


def test_paraphrase_recall_floor_with_tags():
    """②同义改写(零词面交集)+ 预计算标签:recall@8 ≥ 0.8(修前 0.00,接线后 1.00)。"""
    beliefs, tags = _corpus()
    r = statistics.mean(_recall_at_8(beliefs, para, kw, concepts=tags)
                        for kw, para, _ in TOPICS)
    assert r >= 0.8, f"同义改写 recall@8 跌破地板: {r:.2f} < 0.8(标签种子层断了?)"


def test_paraphrase_without_tags_returns_empty_not_poison():
    """③老库(无标签)+ 零词面交集 → 返回**空**:没相关知识是正确答案,
    绝不靠 freshness 凭空塞无关条(J10 投毒教训)。"""
    beliefs, _ = _corpus()
    for _, para, _t in TOPICS:
        assert spreading_activation_recall(beliefs, para, top_k=8) == []


def test_hub_token_latency_bound():
    """④高重复措辞库(同一模板措辞贯穿全库 = 蒸馏产物常态)N=5000:
    召回热路径(零 LLM,drive 前同步调)不许回到秒级。
    hub-token postings 无界时实测 ~2s(10k 时 4.4s);加界 + IDF 后 ~40ms。
    上界 750ms:对修后余量 ~18x(CI 慢机也稳),对修前病态仍必红。"""
    n = 5000
    beliefs = []
    for i in range(n):
        s = _FILLER_SUBJ[i % len(_FILLER_SUBJ)]
        beliefs.append(_belief(f"{s}{i}:按季度轮换一次,优先走统一流程,第{i}项。",
                               ts=_NOW - i))
    queries = ["按季度轮换的统一流程", "档案室编号第100项", "值班表次序怎么定"]
    elapsed = []
    for q in queries:
        best = min(_timed(beliefs, q) for _ in range(3))   # min-of-3 抗 CI 抖动
        elapsed.append(best)
    mean_ms = statistics.mean(elapsed)
    assert mean_ms < 750.0, (
        f"高重复库召回延迟回归: mean={mean_ms:.0f}ms ≥ 750ms(hub-token 界/IDF 被拆了?)")


def _timed(beliefs, q) -> float:
    t0 = time.perf_counter()
    spreading_activation_recall(beliefs, q, top_k=8)
    return (time.perf_counter() - t0) * 1000.0


# ---- 接线级(不止算法级):recall_block → ConceptCache → 标签种子 ----

def test_recall_block_paraphrase_via_concept_cache(tmp_path):
    """MemoryManager 挂 ConceptCache 后,同义改写 query 能从 recall_block 召回
    (锁的是**接线**:标签抽取/缓存 2026-06 就存在,但召回侧一直没人传参)。"""
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    target = "用户偏好深色主题的界面配色"
    mem.write(_belief(target))
    mem.write(_belief("档案室编号每季度轮换一次"))
    assert overlap_score("夜间模式", target) == 0   # 自证零词面交集
    cc.put(target, ["夜间模式", "界面外观"])
    block = mem.recall_block("夜间模式", scope="personal", limit=8)
    assert target in block
    assert "档案室编号" not in block


def test_recall_block_without_cache_stays_graceful():
    """没挂缓存(老部署)→ 行为与从前一致:零词面交集返回空块,不崩不投毒。"""
    mem = MemoryManager()
    mem.write(_belief("用户偏好深色主题的界面配色"))
    assert mem.recall_block("夜间模式", scope="personal", limit=8) == ""


# ---- 金线:ingest 写入路径打标 → 同义改写召回(端到端,LLM 层 stub)----

class TextDelta:   # 事件按 type(ev).__name__ 识别,stub 类名必须叫 TextDelta
    def __init__(self, t):
        self.text = t


class _GW:
    """按 system prompt 路由的 stub:编译器/概念标签/supersede 三个口各回各的。"""

    def __init__(self):
        self.calls = []

    def resolve_model(self, scope):
        return "stub-model"

    async def complete(self, messages, tools, model_ref, *, system=None):
        sys_text = "\n".join(getattr(system, "static", []) or []) if system else ""
        if "核心概念" in sys_text:
            self.calls.append("concepts")
            yield TextDelta(json.dumps([["夜间模式", "界面外观"]], ensure_ascii=False))
        elif "一致性审查" in sys_text:
            self.calls.append("supersede")
            yield TextDelta('{"pairs":[]}')
        else:
            self.calls.append("ingest")
            yield TextDelta(json.dumps([{"title": "配色偏好", "content": "用户偏好深色主题的界面配色",
                                         "kind": "preference"}], ensure_ascii=False))


@pytest.mark.asyncio
async def test_ingest_tags_then_paraphrase_recall(tmp_path):
    """金线:喂料 → 写入路径批量打标入缓存(与 supersede 同节奏,非打字热路径)→
    同义改写 query 立刻能召回。这是 #61 研判①的完整闭环。"""
    from karvyloop.cognition.ingest import ingest_material
    cc = ConceptCache(tmp_path / "cc.json")
    mem = MemoryManager(concept_cache=cc)
    gw = _GW()
    res = await ingest_material("我喜欢深色配色", gateway=gw, mem=mem)
    assert res.written == 1
    assert "concepts" in gw.calls          # 写入路径真打了标签
    assert cc.tags_for("用户偏好深色主题的界面配色") == ["夜间模式", "界面外观"]
    block = mem.recall_block("夜间模式", scope="personal", limit=8)
    assert "深色主题" in block


@pytest.mark.asyncio
async def test_ingest_without_cache_makes_no_concept_call(tmp_path):
    """没挂缓存 → 写入路径**零**概念抽取调用(不给老部署/测试桩加账单,也锁'不偷跑 LLM')。"""
    from karvyloop.cognition.ingest import ingest_material
    mem = MemoryManager()
    gw = _GW()
    await ingest_material("我喜欢深色配色", gateway=gw, mem=mem)
    assert "concepts" not in gw.calls
