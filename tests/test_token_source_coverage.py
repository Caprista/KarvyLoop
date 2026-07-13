"""P0-9(内部审计 docs/68)覆盖回归:此前几处会发起 LLM 调用的用户面路径没打
`token_source` 标 → 账本 by_source 记成 "unknown"(看板"谁烧钱"失真)。

本测试用一个记录 `current_source()` 的桩网关,断言这些入口在调 gateway.complete
时上下文里的 source 是**具体来源名**、不是 "unknown"。复现证据:去掉对应
`with token_source(...)` 包裹,断言立刻回落 "unknown"(下方注释说明,不写进产品码)。

覆盖:知识馆员(distill)、决策偏好抽取+调和(楔子进料口)、圆桌主持。
"""
import asyncio

from karvyloop.llm.token_ledger import current_source


class TextDelta:
    """调用方按 `type(ev).__name__ == "TextDelta"` 判流事件 → 类名必须真叫 TextDelta。"""

    def __init__(self, text):
        self.text = text


class _RecordingGateway:
    """桩:complete() 时把当时的 token_source 记进 seen;产出一个最小 TextDelta 流。"""

    def __init__(self):
        self.seen: list[str] = []

    def resolve_model(self, _scope):
        return "fake/model"

    async def complete(self, _messages, _tools, _ref, system=None, **_kw):
        self.seen.append(current_source())
        yield TextDelta("ok")


def test_knowledge_distill_labels_source():
    from karvyloop.console.distill_engine import _distill_analyze, _distill_chat_reply
    gw = _RecordingGateway()
    asyncio.run(_distill_analyze(gw, "", "一段材料"))
    session = {"transcript": [], "fetched": "料", "summary": "总结"}
    asyncio.run(_distill_chat_reply(gw, "", session, "追问"))
    assert gw.seen == ["knowledge_distill", "knowledge_distill"]
    assert "unknown" not in gw.seen


def test_decision_ask_labels_source():
    """可追问决策卡(docs/77):追问也是用户面 LLM 路径,必须标 decision_ask
    (否则 by_source 看不到追问在烧;对抗验收点名的可选加固,补掉不留欠账)。"""
    import types

    from karvyloop.console.decision_card_wire import decision_card_ask
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    gw = _RecordingGateway()
    reg = PendingProposalRegistry()
    reg.register(Proposal(summary="部署预发", options=("ACCEPT", "DEFER", "REJECT"),
                          strength=0.9, evidence_refs=(), habit_id=0, model_ref="x/y",
                          ts=0.0, kind="run_task", payload={}, basis="低风险"))
    pid = next(iter(reg.pending())).proposal_id
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        proposal_registry=reg, memory=None,
        runtime_kwargs={"gateway": gw, "model_ref": ""}))
    asyncio.run(decision_card_ask(app, proposal_id=pid, question="风险大吗?", transcript=[]))
    assert gw.seen == ["decision_ask"]
    assert "unknown" not in gw.seen


def test_decision_pref_extraction_labels_source():
    from karvyloop.crystallize.decision_pref import (
        DecisionSample, compile_decisions, reconcile_decisions,
    )
    gw = _RecordingGateway()
    samples = [DecisionSample(decision="STATE", context="对外邮件先过目", reason="")]
    asyncio.run(compile_decisions(samples, gateway=gw))
    asyncio.run(reconcile_decisions(samples, existing=["旧偏好"], gateway=gw))
    # 楔子进料口:两次都必须记 decision_pref(此前无标 → unknown,by_source 看不到楔子在烧)
    assert gw.seen == ["decision_pref", "decision_pref"]


def test_roundtable_host_labels_source():
    from karvyloop.console.roundtable_engine import _host_moderate_call
    gw = _RecordingGateway()
    transcript = [{"speaker": "甲", "text": "建议 A"}, {"speaker": "乙", "text": "支持"}]
    asyncio.run(_host_moderate_call(gw, "", "定价", transcript, final=False))
    assert gw.seen == ["roundtable"]
    assert "unknown" not in gw.seen


# ---- 长尾 6 处覆盖(docs/68 P0-9 搭车)----------------------------------------
# 同一复现口径:去掉对应 `with token_source(...)` 包裹,断言立刻回落 "unknown"
# (下方注释说明,不写进产品码)。


def test_workflow_plan_labels_source():
    # 群内协作 workflow 编排:@多人 → 小卡设计 DAG(此前无标 → unknown,看板看不到编排在烧)
    from karvyloop.console.workflow_engine import _workflow_plan_llm
    gw = _RecordingGateway()
    roles = [{"role_id": "r1", "display": "分析师"}, {"role_id": "r2", "display": "写手"}]
    asyncio.run(_workflow_plan_llm(gw, "", "做个市场分析再写稿", roles))
    assert gw.seen == ["workflow_plan"]
    assert "unknown" not in gw.seen


def test_result_classify_labels_source():
    # 结晶时判"结果可否缓存回放"+ 起可读短名:两个 make_* 闭包同源(result_classify)
    from karvyloop.crystallize.result_classifier import make_result_classifier, make_skill_namer
    gw = _RecordingGateway()
    classify = make_result_classifier(gw, "")
    classify("把 3 公里换算成米", "3000 米", [])          # 无联网工具 → 真走 LLM 判
    namer = make_skill_namer(gw, "")
    namer("把 CSV 转成 JSON")
    assert gw.seen == ["result_classify", "result_classify"]
    assert "unknown" not in gw.seen


def test_atom_synthesis_labels_source():
    # role 自造原子:合成 AtomSpec + role 综合裁留不留,两处同源(atom_synthesis)。
    # synthesize 此前落上层 forge、judge 此前 unknown → 都拆出成独立一线。
    from karvyloop.atoms.self_create import judge_atom_keep, synthesize_atom_spec
    gw = _RecordingGateway()
    asyncio.run(synthesize_atom_spec("把一段中文翻成英文", gateway=gw, model_ref=""))

    class _Spec:
        id = "translate_zh_en"
        prompt = "把中文翻成英文"

    asyncio.run(judge_atom_keep(
        _Spec(), role_id="r1", role_identity="翻译角色",
        human_approved=True, contributed=True, verified=True, gateway=gw, model_ref=""))
    assert gw.seen == ["atom_synthesis", "atom_synthesis"]
    assert "unknown" not in gw.seen


def test_decision_card_violation_labels_source():
    # 决策卡 Cut2 守线(建卡路径):此前无标 → unknown。注意静音路径已由上层 silence_cut2 覆盖,
    # 所以标必须打在**建卡 caller**(_attach_violations)、不是 check_violations 内部(否则会把
    # 静音路径的 silence_cut2 冲掉)。这里直接测建卡 caller 的包裹。
    from karvyloop.console import decision_card_wire

    class _State:
        runtime_kwargs = {"gateway": _RecordingGateway()}

    class _App:
        state = _State()

    app = _App()
    aligned = [{"content": "对外邮件先过目", "receipt": [], "kind_label": "约束"}]
    d: dict = {}
    decision_card_wire._attach_violations(app, d, aligned, "要发对外邮件", "直接群发", {})
    seen = app.state.runtime_kwargs["gateway"].seen
    assert seen == ["decision_card"]
    assert "unknown" not in seen


def test_topic_name_labels_source():
    # 工作流/圆桌主题名压缩(2b):主题太长 → LLM 压成短标签(此前无标 → unknown)
    from karvyloop.console.routes import _refine_run_title
    gw = _RecordingGateway()
    long_intent = "我们下个季度要不要进军东南亚市场并且重构一下定价策略以及渠道" * 2
    asyncio.run(_refine_run_title(gw, "", long_intent))
    assert gw.seen == ["topic_name"]
    assert "unknown" not in gw.seen


def test_agent_import_labels_source():
    # agent 导入拆解:调用方(routes_roles）已用 `with token_source("agent_import")` 包住,
    # bootstrap_decompose 内部**不再重复打标**(避免覆盖上层已正确归属的标)。这里断言
    # 在上层 token_source 生效时,内部 gateway.complete 记到的正是 agent_import。
    from karvyloop.adapter.bootstrap import bootstrap_decompose
    from karvyloop.adapter.source import ExternalManifest
    from karvyloop.llm.token_ledger import token_source

    gw = _RecordingGateway()
    manifest = ExternalManifest(
        source_id="claude", source_path="<test>", system_prompt="做点事", tools=())
    with token_source("agent_import"):
        asyncio.run(bootstrap_decompose(manifest, existing_atom_ids=[], gateway=gw, model_ref=""))
    # 桩产出解析不出合法结果 → 内部重试 1 次(共 2 次 complete);两次都应归到上层 agent_import。
    assert gw.seen == ["agent_import", "agent_import"]
    assert "unknown" not in gw.seen
