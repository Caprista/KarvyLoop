"""test_pursuit_triage — 聊天里的跨天目标判型 create(docs/88 §7 第二刀)。

覆盖:
- 粗筛 looks_like_pursuit:正反例(真目标 ×2 / 闲聊 / 一次性任务 / 含糊感慨 / 问句 / 编排)。
- 宁空勿毒:LLM 返坏 JSON / 坏 gate 类型 / 拆不出的 test_pass cmd → 放弃判型,**零创建**。
- 好路径:粗筛+LLM 判型 → PursuitStore 有 active 记录 + KIND_PURSUIT_COMMIT 承诺卡升起
  (绝不自动 committed —— H2A 铁律);payload 带 origin=karvy_triage。
- ACCEPT → committed(走既有 handler);REJECT → 判型建的记录清掉不留垃圾;
  显式 API 建的(origin 空)REJECT 记录保留(第一刀语义 0 回归)。
- 接线:maybe_route_to_role 走到判型(与圆桌同一早返回模式);粗筛不过 → 不烧 LLM。
LLM 全部用假 gateway(CI 可跑);真模型冒烟另行。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.pursuit_store import PursuitStore  # noqa: E402
from karvyloop.console.proposal_handlers import build_proposal_handlers  # noqa: E402
from karvyloop.console.routes_pursuit import create_pursuit_with_commit_card  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_PURSUIT_COMMIT, PendingProposalRegistry,
)
from karvyloop.karvy.pursuit_triage import (  # noqa: E402
    ORIGIN_KARVY_TRIAGE, looks_like_pursuit, maybe_pursuit_triage, parse_pursuit_draft,
)

# 两条"真目标"句(粗筛必须过);其余是绝不能误触的反例。
GOAL_1 = "帮我把 CI 一直推进到全绿为止"
GOAL_2 = "这周之内把 tests 目录的测试全修绿"


# 类名必须正好 TextDelta(判型收流用 type(ev).__name__)
class TextDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeGateway:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        yield TextDelta(self._payload)


def _fake_app(tmp_path, *, gateway=None, with_store=True):
    """最小 fake app.state(镜像 test_pursuit_first_cut._fake_app 的形状)。"""
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pursuit_store=(PursuitStore(tmp_path / "pursuits.json") if with_store else None),
        task_registry=TaskRegistry(),
        proposal_registry=PendingProposalRegistry(),
        main_loop=None, memory=None, trace=None,
        runtime_kwargs={"gateway": gateway, "model_ref": "",
                        "workspace_root": str(tmp_path)},
        taste_predictions=None, decision_log=None,
        ws_clients=set(),
    ))
    app.state.proposal_handlers = build_proposal_handlers(app)
    return app


def _good_llm_json(tmp_path) -> str:
    return ('{"is_pursuit": true, "title": "CI 全绿", '
            '"statement": "把 CI 一直推进到全绿为止", '
            '"gate": {"type": "file_exists", "path": "' + (tmp_path / "green.txt").as_posix() + '"}, '
            '"revision_triggers": []}')


def _commit_cards(app):
    return [p for p in app.state.proposal_registry.pending()
            if getattr(p, "kind", "") == KIND_PURSUIT_COMMIT]


# ---------------------------------------------------------------- 粗筛(保守:宁漏勿滥)
def test_prefilter_accepts_true_multiday_goals():
    assert looks_like_pursuit(GOAL_1)   # "一直…为止" + "帮我把"
    assert looks_like_pursuit(GOAL_2)   # "这周之内" + "把…修绿"


def test_prefilter_rejects_chitchat():
    assert not looks_like_pursuit("今天天气不错,聊聊天呗")


def test_prefilter_rejects_one_shot_task():
    # 单次任务:有指令形("帮我把")但没有任何持续/期限信号 → 不触发
    assert not looks_like_pursuit("帮我把这个文件重命名成 report.md")


def test_prefilter_rejects_vague_suspect():
    # 疑似但含糊:有"一直"却不是委托(感慨)→ 不触发
    assert not looks_like_pursuit("我一直觉得这个项目挺好的")


def test_prefilter_rejects_questions():
    # 问句是征询不是委托(有期限+动作也不触发)
    assert not looks_like_pursuit("这周之内能把测试修完吗")
    assert not looks_like_pursuit("怎么把 CI 一直推进到全绿?")


def test_prefilter_rejects_orchestration_and_short():
    assert not looks_like_pursuit("去产品研发域找几个人帮我分析下竞品")   # 编排,非持久目标
    assert not looks_like_pursuit("修绿")                                # 太短
    assert not looks_like_pursuit("")


# ---------------------------------------------------------------- 解析(宁空勿毒)
def test_parse_rejects_prose_and_bad_json():
    assert parse_pursuit_draft("我觉得这是个跨天目标,建议建一个 Pursuit") is None
    assert parse_pursuit_draft('{"is_pursuit": true, "gate":') is None
    assert parse_pursuit_draft("") is None


def test_parse_rejects_not_pursuit_and_bad_gate_type():
    assert parse_pursuit_draft('{"is_pursuit": false}') is None
    assert parse_pursuit_draft(
        '{"is_pursuit": true, "statement": "s", "gate": {"type": "predicate", "expr": "a==b"}}') is None
    assert parse_pursuit_draft('{"is_pursuit": true, "statement": "s"}') is None   # 缺 gate
    assert parse_pursuit_draft('{"is_pursuit": "yes", "statement": "s", '
                               '"gate": {"type": "file_exists", "path": "/x"}}') is None  # 非字面 true


def test_parse_rejects_unsplittable_test_pass_cmd():
    # 未闭合引号:和 gate 求值同一口径拆不出 argv → 放弃(绝不让永红 gate 进库)
    assert parse_pursuit_draft(
        '{"is_pursuit": true, "statement": "s", '
        '"gate": {"type": "test_pass", "cmd": "python \\"unclosed"}}') is None
    assert parse_pursuit_draft(
        '{"is_pursuit": true, "statement": "s", "gate": {"type": "test_pass", "cmd": ""}}') is None


def test_parse_good_drafts_including_fenced():
    d = parse_pursuit_draft(
        '```json\n{"is_pursuit": true, "title": "T", "statement": "把测试修绿", '
        '"gate": {"type": "test_pass", "cmd": "python -m pytest tests/foo -x -q"}, '
        '"revision_triggers": ["budget_exhausted == true"]}\n```')
    assert d is not None
    assert d.gate == {"type": "test_pass", "cmd": "python -m pytest tests/foo -x -q"}
    assert d.statement == "把测试修绿" and d.title == "T"
    assert d.revision_triggers == ("budget_exhausted == true",)
    # statement 缺 → 回退用户原句(原句就是目标的事实源)
    d2 = parse_pursuit_draft('{"is_pursuit": true, "gate": {"type": "file_exists", "path": "out.md"}}',
                             intent=GOAL_1)
    assert d2 is not None and d2.statement == GOAL_1


# ---------------------------------------------------------------- 好路径:记录 + 承诺卡
def test_triage_good_path_creates_record_and_raises_commit_card(tmp_path):
    gw = FakeGateway(_good_llm_json(tmp_path))
    app = _fake_app(tmp_path, gateway=gw)
    out = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out is not None and out["routed"] is True and out["text"], out
    assert gw.calls == 1
    # PursuitStore 有记录,且**只是 active**(绝不自动 committed —— H2A 铁律)
    recs = app.state.pursuit_store.all()
    assert len(recs) == 1 and recs[0].status == "active"
    # 承诺卡升起,payload 带判型来源标
    cards = _commit_cards(app)
    assert len(cards) == 1
    assert cards[0].payload.get("pursuit_id") == recs[0].id
    assert cards[0].payload.get("origin") == ORIGIN_KARVY_TRIAGE


def test_triage_bad_json_creates_nothing(tmp_path):
    gw = FakeGateway("这不是 JSON,我觉得可以建一个 Pursuit")
    app = _fake_app(tmp_path, gateway=gw)
    out = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out is None
    assert gw.calls == 1                       # 粗筛过了,真烧了一次判型
    assert app.state.pursuit_store.all() == []  # 但绝不带半坏数据创建
    assert app.state.proposal_registry.pending() == []


def test_triage_bad_gate_type_creates_nothing(tmp_path):
    gw = FakeGateway('{"is_pursuit": true, "statement": "s", '
                     '"gate": {"type": "predicate", "expr": "a==b"}}')
    app = _fake_app(tmp_path, gateway=gw)
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_2)) is None
    assert app.state.pursuit_store.all() == []
    assert app.state.proposal_registry.pending() == []


def test_triage_unsplittable_cmd_creates_nothing(tmp_path):
    gw = FakeGateway('{"is_pursuit": true, "statement": "s", '
                     '"gate": {"type": "test_pass", "cmd": "python \\"unclosed"}}')
    app = _fake_app(tmp_path, gateway=gw)
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_2)) is None
    assert app.state.pursuit_store.all() == []


def test_triage_llm_says_not_pursuit(tmp_path):
    gw = FakeGateway('{"is_pursuit": false}')
    app = _fake_app(tmp_path, gateway=gw)
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_1)) is None
    assert app.state.pursuit_store.all() == []


def test_triage_prefilter_miss_burns_no_llm(tmp_path):
    gw = FakeGateway(_good_llm_json(tmp_path))
    app = _fake_app(tmp_path, gateway=gw)
    assert asyncio.run(maybe_pursuit_triage(app, "帮我把这个文件重命名成 report.md")) is None
    assert gw.calls == 0   # 粗筛不过 → 零 token


def test_triage_no_store_no_llm_burn(tmp_path):
    gw = FakeGateway(_good_llm_json(tmp_path))
    app = _fake_app(tmp_path, gateway=gw, with_store=False)
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_1)) is None
    assert gw.calls == 0   # pursuit 未接线 → 不判型不烧钱


# ---------------------------------------------------------------- ACCEPT / REJECT
def test_accept_commits_pursuit(tmp_path):
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    card = _commit_cards(app)[0]
    res = app.state.proposal_registry.decide(card.proposal_id, "ACCEPT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.ok
    recs = app.state.pursuit_store.all()
    assert len(recs) == 1 and recs[0].status == "committed"


def test_reject_cleans_triaged_record(tmp_path):
    """人 REJECT 判型建的承诺卡 → 记录清掉,不留垃圾(docs/88 第二刀)。"""
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert len(app.state.pursuit_store.all()) == 1
    card = _commit_cards(app)[0]
    res = app.state.proposal_registry.decide(card.proposal_id, "REJECT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.ok and res.detail == "rejected"
    assert app.state.pursuit_store.all() == []          # 记录随卡清掉
    assert app.state.proposal_registry.pending() == []  # 卡也没了


def test_reject_keeps_explicit_api_record(tmp_path):
    """显式 API/面板建的(origin 空)REJECT 只关卡,记录保留 —— 第一刀语义 0 回归。"""
    app = _fake_app(tmp_path)
    res = asyncio.run(create_pursuit_with_commit_card(
        app, statement="重构直到 pytest tests/foo 全绿",
        verify_gate={"type": "test_pass", "cmd": "pytest -q tests/foo"}))
    # 响应形状与第一刀 POST 一致(前端在消费)
    assert set(res.keys()) == {"ok", "pursuit_id", "status", "commit_proposal_id", "gate_desc"}
    assert res["ok"] is True and res["status"] == "active"
    card = _commit_cards(app)[0]
    assert card.payload.get("origin") == ""   # 亲手建的
    app.state.proposal_registry.decide(card.proposal_id, "REJECT",
                                       handlers=app.state.proposal_handlers)
    recs = app.state.pursuit_store.all()
    assert len(recs) == 1 and recs[0].status == "active"   # 保留,可稍后手动承诺


# ---------------------------------------------------------------- 接线:maybe_route_to_role
def _mgr():
    import tempfile
    from pathlib import Path

    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    m = ConversationManager(ConversationStore(Path(tempfile.mkdtemp()) / "conv"))
    m.start()
    return m


def test_route_wiring_reaches_triage_and_raises_card(tmp_path):
    """私聊小卡说跨天目标 → maybe_route_to_role 早返回判型结果 + 承诺卡升起
    (与圆桌提案同一早返回模式;record_turn 由既有调用方代码做)。"""
    from karvyloop.console.routes import maybe_route_to_role
    gw = FakeGateway(_good_llm_json(tmp_path))
    app = _fake_app(tmp_path, gateway=gw)
    out = asyncio.run(maybe_route_to_role(app, _mgr(), GOAL_2))
    assert out is not None and out["routed"] is True, out
    assert out["text"]                       # 非空文案 → 调用方 record_turn 有内容可记
    assert gw.calls == 1
    assert len(_commit_cards(app)) == 1
    assert len(app.state.pursuit_store.all()) == 1


def test_route_wiring_chitchat_untouched(tmp_path):
    """闲聊经过接线不触发判型也不烧 LLM(0 回归:返 None 走正常 drive)。"""
    from karvyloop.console.routes import maybe_route_to_role
    gw = FakeGateway(_good_llm_json(tmp_path))
    app = _fake_app(tmp_path, gateway=gw)
    out = asyncio.run(maybe_route_to_role(app, _mgr(), "今天天气不错,聊聊天呗"))
    assert out is None
    assert gw.calls == 0
    assert app.state.pursuit_store.all() == []


# ---------------------------------------------------------------- 去重闸(对抗验收 P2 收口)
def test_dedup_same_gate_no_second_record(tmp_path):
    """同句重发 → gate 完全相等 → 不建第二条,回"已经在追"文案(多卡打扰收口)。"""
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_1)) is not None
    out2 = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out2 is not None and out2["routed"] is True and out2["text"]  # 早返回有文案可 record_turn
    assert len(app.state.pursuit_store.all()) == 1
    assert len(_commit_cards(app)) == 1


def test_dedup_similar_statement_diff_gate(tmp_path):
    """statement 近似(词面重合 ≥0.7)但 gate 不同 → 仍判同款,不建第二条。"""
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_1)) is not None
    gw2 = FakeGateway('{"is_pursuit": true, "statement": "把 CI 一直推进到全绿", '
                      '"gate": {"type": "file_exists", "path": "'
                      + (tmp_path / "other.txt").as_posix() + '"}}')
    app.state.runtime_kwargs = {"gateway": gw2, "model_ref": "",
                                "workspace_root": str(tmp_path)}
    out2 = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out2 is not None and out2["text"]
    assert len(app.state.pursuit_store.all()) == 1


def test_dedup_still_active_after_commit(tmp_path):
    """已 ACCEPT(committed)的追求同样挡重复 —— store.active 含 committed。"""
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    card = _commit_cards(app)[0]
    app.state.proposal_registry.decide(card.proposal_id, "ACCEPT",
                                       handlers=app.state.proposal_handlers)
    out2 = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out2 is not None and out2["text"]
    assert len(app.state.pursuit_store.all()) == 1


def test_dedup_paused_record_points_to_continue(tmp_path):
    """真伤2:命中的重复目标若是**挂起/改方向**记录,文案改成"去面板点继续"(不是笼统"已经在追"),
    让用户对僵住的记录有路可走。"""
    from karvyloop import i18n
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    assert asyncio.run(maybe_pursuit_triage(app, GOAL_1)) is not None
    # 把那条记录置为挂起(模拟被地板挂起)
    rec = app.state.pursuit_store.all()[0]
    rec.pursuit = rec.pursuit.model_copy(update={"status": "revised"})
    rec.suspended = True
    app.state.pursuit_store.put(rec)
    out2 = asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    assert out2 is not None
    stmt = (rec.pursuit.statement or "")[:80]
    # "暂停了 —— 去继续"文案,不是笼统"已经在追"
    assert out2["text"] == i18n.t("pursuit.triage.duplicate_paused", statement=stmt)
    assert out2["text"] != i18n.t("pursuit.triage.duplicate", statement=stmt)
    assert len(app.state.pursuit_store.all()) == 1   # 不建第二条


def test_triage_rejects_placeholder_path(tmp_path):
    """真伤4:LLM 判型吐 `{date}` 模板路径 → parse_pursuit_draft 放弃(宁空勿毒),不带坏 gate 进库。"""
    draft = parse_pursuit_draft(
        '{"is_pursuit": true, "statement": "写日报", '
        '"gate": {"type": "file_exists", "path": "/reports/{date}.md"}}')
    assert draft is None
    # 正常路径仍收
    ok = parse_pursuit_draft(
        '{"is_pursuit": true, "statement": "写日报", '
        '"gate": {"type": "file_exists", "path": "/reports/daily.md"}}')
    assert ok is not None and ok.gate["path"] == "/reports/daily.md"


def test_dedup_different_goal_still_creates(tmp_path):
    """真不同的目标(词面不重合+gate 不同)绝不被去重闸误挡 —— 宁建勿吞真需求。"""
    app = _fake_app(tmp_path, gateway=FakeGateway(_good_llm_json(tmp_path)))
    asyncio.run(maybe_pursuit_triage(app, GOAL_1))
    gw2 = FakeGateway('{"is_pursuit": true, "statement": "每周整理一份竞品动态观察", '
                      '"gate": {"type": "file_exists", "path": "'
                      + (tmp_path / "weekly.md").as_posix() + '"}}')
    app.state.runtime_kwargs = {"gateway": gw2, "model_ref": "",
                                "workspace_root": str(tmp_path)}
    out2 = asyncio.run(maybe_pursuit_triage(app, "帮我每周把竞品动态一直盯下去,直到项目结束为止"))
    assert out2 is not None
    assert len(app.state.pursuit_store.all()) == 2
    assert len(_commit_cards(app)) == 2
