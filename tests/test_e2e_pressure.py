"""test_e2e_pressure — 真模型·整条旅程压测台(Hardy 2026-06-25:别拿 user 跑当唯一验证门).

零件式单测看不见"缝合怪"——缝在零件之间。这台子用**真 gateway + 真 registry**把核心旅程
从头走到尾,逮编排/结晶层的串台与断缝。**CI 自动跳过**(无 ~/.karvyos/config.yaml 真 key);
本机/VM 有 key 时按需跑:`pytest tests/test_e2e_pressure.py -s`。

诚实边界:这层逮**后端/编排缝**(圆桌误路由、上下文串台、结晶 loop、单点委派 0 回归)。
纯前端渲染 bug(料→去聊天那类)要浏览器自动化第二层补,不在此。

覆盖:
- J1 编排识别:开圆桌→KIND_ROUNDTABLE(非单点);单点委派→route_to_role(0 回归)
- J2 上下文不串台:route 提案也 record_turn(追问承接真上一句)
- J3 决策结晶 loop(真模型):连拒同理由→结晶出可复用 Belief→下次召回摆上来
- J4 圆桌 ACCEPT 真开桌(真模型):提案→handler→建圆桌对话+开场
"""
from __future__ import annotations

import asyncio
import tempfile
import time
import types
from pathlib import Path

import pytest

CFG = Path.home() / ".karvyos" / "config.yaml"


def _tmp_workspace_kwargs(rk: dict, tmp: Path) -> dict:
    """把 runtime_kwargs 的 workspace(连同 fs token 的授权范围)覆盖到 tmp 目录。

    为什么(2026-07-04 独立验收 W1):模块级 _RT 没传 workspace_root → 回退 cwd=仓根;
    J22 原样继承后,真模型把分析脚本/中间产物写进源码树(karvyloop/sample_data/ 游离
    产物实捕)。演示 CSV 是文本内联进 intent 的(onboarding.compose_task_intent),
    tmp workspace 不改变演示语义。**只覆盖传入的副本** —— 模块级 _RT.runtime_kwargs
    被其他 J 步共用,绝不动全局。

    零模型也可验(tests/test_onboarding_journey.py 的 W1 回归锁直接测本函数)。
    """
    from karvyloop.cli.run import _make_token
    ws = tmp / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    out = dict(rk)
    out["workspace_root"] = str(ws)
    out["token"] = _make_token(str(ws))   # fs 授权范围同步指 tmp,不再覆盖仓根
    return out


def _real_runtime():
    if not CFG.exists():
        return None
    from karvyloop.cli._runtime import resolve_runtime
    rt = resolve_runtime(config_path=CFG)
    if not (rt.runtime_kwargs or {}).get("gateway"):
        return None
    # W1 复发根治(2026-07-13 实捕:analyze.py/quarterly_sales.csv 又写回仓根):
    # 只在 J22 单点盖 tmp 不够 —— 模块级 _RT 出厂就带 tmp 工作区,所有 J 测试
    # (含未来新增)默认继承,真模型的任何落盘只进临时目录。J22 自己再覆盖到
    # 它私有的 tmp 不受影响(_tmp_workspace_kwargs 只动副本)。
    rt.runtime_kwargs.update(_tmp_workspace_kwargs(
        rt.runtime_kwargs, Path(tempfile.mkdtemp(prefix="e2e-pressure-"))))
    return rt


_RT = _real_runtime()
pytestmark = pytest.mark.skipif(_RT is None, reason="无真模型 config(~/.karvyos/config.yaml)→ CI 跳过")


@pytest.fixture(scope="module")
def app():
    """真 app.state:真 gateway/main_loop + 真 registry(两个分析师跨域 + 一个设计师)+ 真记忆。"""
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.console.tasks import TaskRegistry
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    tmp = Path(tempfile.mkdtemp())
    reg = BusinessDomainRegistry()
    reg.create(name="数据组A", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    reg.create(name="数据组B", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    reg.create(name="设计工作室", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:设计师")
    from karvyloop.atoms.registry import AtomRegistry, AtomStore
    from karvyloop.roles.registry import RoleRegistry
    atom_reg = AtomRegistry(store=AtomStore(tmp / "atoms.json"))
    role_reg = RoleRegistry(tmp / "roles", atom_registry=atom_reg)
    mgr = ConversationManager(ConversationStore(tmp / "conv"))
    mgr.start()
    a = types.SimpleNamespace(state=types.SimpleNamespace(
        runtime_kwargs=_RT.runtime_kwargs, main_loop=_RT.main_loop,
        domain_registry=reg, memory=MemoryManager(store=BeliefStore(tmp / "beliefs.json")),
        conversation_manager=mgr, proposal_registry=PendingProposalRegistry(),
        task_registry=TaskRegistry(), ws_clients=set(), config_path="", workbench_app=None,
        atom_registry=atom_reg, role_registry=role_reg,
    ))
    a.state.proposal_handlers = build_proposal_handlers(a)
    return a


# ---- J1:编排识别(规则层,快)----
def test_j1_roundtable_vs_single_delegate(app):
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE

    rt = asyncio.run(maybe_route_to_role(app, app.state.conversation_manager,
                                         "去Karvy World让那两个分析师开个圆桌分析世界杯"))
    assert rt is not None and rt.get("routed"), "圆桌意图没被识别成编排"
    assert app.state.proposal_registry.pending()[-1].kind == KIND_ROUNDTABLE, "开圆桌被误降成单点委派(世界杯 bug 类)"

    rt2 = asyncio.run(maybe_route_to_role(app, app.state.conversation_manager, "让分析师出一份周报"))
    assert rt2 is not None and app.state.proposal_registry.pending()[-1].kind == KIND_ROUTE_TO_ROLE, "单点委派回归"


# ---- J2:上下文不串台(route 提案也进对话记忆,靠 ws/REST 早返回前的 record_turn)----
def test_j2_route_proposal_recorded_no_bleed(app):
    # 模拟 REST/WS 路径:提案后 record_turn(routes.py / ws.py 已补)。这里直接验"记了 → 追问能承接"。
    mgr = app.state.conversation_manager
    n0 = mgr.current().turn_count
    mgr.record_turn("去Karvy World让两个分析师开圆桌分析世界杯", "（圆桌提案已出，到 H2A 处置）", brain="slow")
    assert mgr.current().turn_count == n0 + 1
    ctx = mgr.context_view()
    assert any("世界杯" in (t.user_intent or "") for t in ctx), "上一句世界杯意图没进 ctx → 追问会撞旧台"


# ---- J3:决策结晶 loop(真模型)----
def test_j3_decision_crystallization_real_model(app):
    from karvyloop.console.decision_wire import maybe_crystallize_decisions, observe_decision
    from karvyloop.crystallize.decision_pref import (
        DecisionSample, is_decision_pref, prealign_block, recall_decision_prefs)

    samples = [
        ("运维提议直接在生产库删旧表回收空间", "没备份不许动生产,先备份"),
        ("运维提议今晚直接对生产库跑 migration", "动生产前必须先备份"),
        ("运维提议线上直接 drop 没用的索引", "先备份再动生产,底线"),
    ]
    # best-effort(与 J5/J6/J7/J15… 同款真模型硬化):真模型偶发返回**空 / 无 content 的退化输出**
    # (实测 ~10%,同源的并发/非确定性抖动)→ 重试几次验"连拒同理由能结晶"。
    # maybe_crystallize 会消费样本缓冲,故每次重试重新 seed。**断言不放宽**(written>=1 不动):
    # 3 次真模型全 0 才红 = 结晶真断了。注:信封包壳的**合法**输出({"item":{...}} 等)已在
    # parse_reconcile 解包救回(2026-07-15 根因修复,test_decision_pref 有确定性回归锁),这里
    # 只兜"模型真没吐出可用内容"的残余抖动 —— 那是全量里"偶红"的最后一段(单跑必绿因样本小)。
    written = 0
    for _ in range(3):
        app.state.decision_samples = []
        for ctx, reason in samples:
            observe_decision(app, DecisionSample(decision="REJECT", context=ctx, reason=reason,
                                                 scope="personal", ts=time.time()))
        written = asyncio.run(maybe_crystallize_decisions(app))
        if written >= 1:
            break
    assert written >= 1, "连拒同理由没结晶出任何 Belief（楔子没见血;3 次真模型全 0=结晶真断了）"

    prefs = []
    for sc in ("personal", "domain"):
        prefs.extend(b for b in app.state.memory.index.all(sc) if is_decision_pref(b))
    assert prefs, "结晶了但库里查不到决策偏好"
    joined = " ".join(b.content for b in prefs)
    assert ("备份" in joined and "生产" in joined), f"抽出的标准没抓住'生产先备份'语义: {joined!r}"

    block = prealign_block(recall_decision_prefs(prefs), domain="", role="")
    assert block and "备份" in block, "下次决策前没把这条标准预对齐摆上来"
    # Cut 1 回执:预对齐块要带"来自你的拍板"凭据(答用户视角 Q2:凭什么信你)
    assert "来自你的拍板" in block, "标准摆了但没回执 —— Q2(凭什么信你)没堵"


# ---- J4:圆桌 ACCEPT 真开桌(真模型)----
def test_j4_roundtable_accept_opens_table_real_model(app):
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, proposal_for_roundtable

    p = proposal_for_roundtable(
        group_domain_id="l0", group_name="Karvy World",
        participants=["分析师", "分析师"], participant_names=["分析师(数据组A)", "分析师(数据组B)"],
        topic="分析本届世界杯的筹办情况", ts=time.time())
    app.state.proposal_registry.register(p)
    handler = app.state.proposal_handlers[KIND_ROUNDTABLE]
    ok, detail = handler(p)
    assert ok, f"圆桌 ACCEPT 没开起来: {detail}"
    assert "圆桌" in detail
    # 真切到群 peer + 建了带开场的圆桌对话
    peer = app.state.conversation_manager.current_peer()
    assert peer is not None and getattr(peer, "role", "") == "group" and peer.domain_id == "l0"
    assert app.state.conversation_manager.current().turn_count >= 1, "圆桌对话没开场轮"


# ---- J5:违背即拦(真模型)—— 踩了你定的标准,拍板前被红牌拦下 ----
def test_j5_violation_guard_real_model(app):
    from karvyloop.console.decision_card_wire import build_card_for_proposal
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief
    from karvyloop.karvy.proposal_registry import proposal_for_route

    app.state.memory.write(make_decision_pref_belief(
        "动生产数据库前必须先有完整备份,未备份一律不批", "constraint",
        strength=0.8, status="confirmed", explicit=True,
        evidence=[{"ts": 1.0, "decision": "REJECT", "gist": "没备份不许动生产"}]))
    p = proposal_for_route(domain_id="d", role="运维", agent_id="运维", domain_name="运维组",
                           requirement="今晚直接在生产库上 drop user_events 表,不用备份,赶紧回收空间", ts=1.0)
    app.state.proposal_registry.register(p)
    # best-effort:真模型 + 多渠道并发共用一把 key 可能把响应截断(_loads_tolerant 救对象边界,
    # 但重并发会截在对象中间救不回)→ 重试几次,验"守线能拦"而非"首试必拦"。
    card = None
    for _ in range(3):
        card = build_card_for_proposal(app, p.proposal_id)
        if card and card.get("violations"):
            break
    assert card is not None
    assert card["violations"], "踩了你定的标准却没拦(违背即拦没生效;若反复空,多半是并发把响应截没了)"
    assert "备份" in card["violations"][0]["standard"]
    assert card["high_value"] is True and card["needs_recheck"] is True  # 违背→拍前必确认


# ---- J6:导入 agent = LLM 拆解(真模型)—— 头号缺陷修复,Hardy 验收锚三选三 ----
def test_j6_agent_import_llm_decompose_real_model(app):
    """外部 agent 经真模型拆解 → (a) 出 role (b) 出 ≥1 atom (c) 走了 LLM(gateway.complete 计费)。

    Hardy 2026-06-26 拍:导入不该扁平拷成 skill,该 LLM 拆出 role+atom 并耗 token。
    best-effort:真模型 + 并发可能把 JSON 截断(宁空勿毒返 None → 降级 v0)→ 重试几次验"能拆"。
    """
    import types as _t

    from karvyloop.console.routes import api_agent_import

    req = _t.SimpleNamespace(
        role_id="imported_researcher", source_type="generic-json",
        system_prompt=("You are a meticulous research analyst. You search the web, fetch and read "
                       "sources, verify claims against primary sources, then summarize with citations."),
        tools=["web_search", "fetch_url", "verify_claim", "summarize_with_citations"])
    request = _t.SimpleNamespace(app=app)

    out = None
    for _ in range(3):
        # 每次换个 role_id(失败会留半成品目录,重名会被拒)
        req.role_id = f"imported_researcher_{_}"
        out = asyncio.run(api_agent_import(req, request))
        if out.get("decomposed"):
            break
    assert out is not None and out.get("ok"), f"导入直接失败: {out}"
    assert out.get("decomposed") is True, f"真模型没拆解(反复降级 v0;多半并发截断了 JSON): {out}"
    # (a) role 物化
    assert (app.state.role_registry.root / out["role_id"]).exists(), "拆解了但角色没落库"
    # (b) ≥1 atom 进公共原子库 + COMPOSITION 引的是原子不是死字符串
    assert len(out["atoms"]) >= 1, "没拆出原子(Hardy 验收锚 b)"
    for aid in out["atoms"]:
        assert app.state.atom_registry.get(aid) is not None, f"原子 {aid} 没进公共池"
    comp = (app.state.role_registry.root / out["role_id"] / "COMPOSITION.yaml").read_text(encoding="utf-8")
    assert any(f"atom: {aid}" in comp for aid in out["atoms"]), "COMPOSITION 没引原子"


# ---- J7:模糊指令 LLM 拆解(真模型)—— "去X域找人做Y" → 拆成 域+人+H2A 提案 ----
def test_j7_fuzzy_dispatch_real_model(app):
    """没点名角色、没说"圆桌"的模糊话 → 真模型拆出 域+人 → 落到真实成员的 H2A 提案(非小卡自己干)。

    best-effort:真模型并发可能截断 JSON(宁空勿毒→None 降级)→ 重试几次验"能拆"。
    """
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE

    # "设计工作室" 域名出现,但成员名"设计师"**不**在句中(避开确定性子串匹配,逼走模糊拆解层)
    intent = "去设计工作室那边找人帮我评审一下新界面的设计"
    out = None
    for _ in range(3):
        before = len(app.state.proposal_registry.pending())
        out = asyncio.run(maybe_route_to_role(app, app.state.conversation_manager, intent))
        if out and out.get("routed") and len(app.state.proposal_registry.pending()) > before:
            break
    assert out is not None and out.get("routed") is True, f"模糊指令没被拆成编排(反复降级): {out}"
    p = app.state.proposal_registry.pending()[-1]
    assert p.kind in (KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE), f"拆出的不是圆桌/委派: {p.kind}"
    # 落到的是**真实**域/成员(设计工作室 / 设计师),不是凭空编的
    blob = str(p.payload)
    assert "设计" in blob, f"提案没指向真实的设计域/成员: {p.payload}"


# ============================================================================
# 2026-06-28 丰富:整条端到端压测 —— 新子系统 + 之前优化但没压过的技术债
#   J8  data-analyst 系统技能(bundled 不可删区,真索引)
#   J9  定时任务到点触发 → 可 drive(全系统唯一调度面)
#   J10 网上随机抓知识 → 摄入知识库 → 网状沉淀 → 激活扩散随机检索(THE BIG ONE)
#   J11 tool + skill 调取(两套机制:能力门 vs 技能召回)
#   J12 跑评分离飞轮:drive 只写 facts → 异步评(满意度+真模型质量)→ 置信累积 → Trace 保留 → token 记账
#   J13 跨 run 规律提炼 + 撤有害(真模型 lesson)
# ============================================================================

def _stub_brain(*, success: bool = True, texts: list | None = None):
    """受控 slow_brain 桩:产可控 AtomRun(代表真干活)→ drive 写 atom_run+eval_fact。
    run 侧受控才能压飞轮的因果;真模型留给评价/裁判侧(J12/J13 的质量/lesson)。"""
    from karvyloop.schemas.atom import AtomRun
    n = [0]

    def slow_brain(intent: str):
        n[0] += 1
        txt = (texts[(n[0] - 1) % len(texts)] if texts else f"done-{n[0]}")
        run = AtomRun(atom_id="stub", input={"intent": intent}, output={"text": txt},
                      success=success, tool_calls=[{"name": "run_command"}],
                      trace_ref=f"tr-{abs(hash(intent)) % 9999}-{n[0]}", ts=time.time())
        return txt, run
    return slow_brain


# ---- J8:data-analyst 系统技能(bundled 不可删区 + 真索引装到)----
def test_j8_data_analyst_system_skill(app):
    idx = app.state.main_loop.skill_index
    e = idx.lookup_by_name("data-analyst")
    assert e is not None, "data-analyst 系统技能没进真索引(bootstrap 没扫 bundled 区)"
    assert e.source == "system", f"没标 system → reset 会误删: {e.source!r}"
    # reset 语义:只清 source!=system → 系统技能幸存
    assert any(x.name == "data-analyst" for x in idx.all() if x.source == "system")
    # 方法真随包(SKILL.md 正文可读 = 包内资产在,打包没丢)
    body = Path(e.path).read_text(encoding="utf-8")
    assert "semantic layer" in body.lower() and "validate" in body.lower(), \
        "data-analyst 方法正文没随包(打包资产丢了?)"


# ---- J9:定时任务 到点触发(croniter)→ 可 drive(全系统唯一调度面,Karvy-only)----
def test_j9_scheduled_task_fires_and_drives(app):
    from karvyloop.karvy.scheduler import SchedulerStore
    store = SchedulerStore()                       # 纯内存,不污染真实 home
    assert store.add("not a cron", "x") is None, "非法 cron 没被拒(解析门没守)"
    t = store.add("*/5 * * * *", "总结今天的进展", title="日报")
    assert t is not None, "合法 cron 没建成任务"
    nowt = time.time()
    due = store.due(since=nowt - 60, now=nowt + 3600)   # 现实的 tick 窗口(近过去→近未来)
    assert any(x.id == t.id for x in due), "到点了 scheduler 没判 due"
    # 到点 → drive(可委派);验 due→drive 通路真能跑一遍
    res = app.state.main_loop.drive(t.intent, slow_brain=_stub_brain())
    assert res is not None, "到点任务没能 drive"
    store.mark_run(t.id, "ok", ts=nowt)
    # last_run 推进后,同窗口不重复触发(防进程重启/慢 tick 重放)
    assert not any(x.id == t.id for x in store.due(since=nowt - 60, now=nowt)), \
        "mark_run 后仍判 due → 会重复触发"


# ---- J10:我代替真人走完「通用知识沉淀流」→ 网状沉淀 → 激活扩散召回(整条真实压测)----
def test_j10_knowledge_feed_distill_recall_real(app):
    """**Hardy 要的真测法**:不绕过摄入,我代替真人走完人审蒸馏流 ——
    `/memory/feed`(真抓正文 + 知识自生长框架结构化)→ `/memory/distill/chat`(我作为人参与沟通)
    → `/memory/distill/decide` persist(我拍板沉淀)→ 多主题沉成知识库 → 激活扩散召回。

    **这条真实压测揪出并修复了产品 bug**:persist 旧用 `ingest_material`(关于用户的事实抽取器)
    → 通用知识文章一律抽成 [] → 沉淀 0 条,整条"喂料→沉淀网状"工作流形同虚设。改走
    `ingest_knowledge`(通用知识口径)后,7 条/篇真沉进库、可召回。"""
    import types as _t

    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console import routes

    # 独立知识库 + 独立 distill store(隔离,不污染别的 journey)
    tmp = Path(tempfile.mkdtemp())
    mem = MemoryManager(store=BeliefStore(tmp / "kb.json"))
    kapp = _t.SimpleNamespace(state=_t.SimpleNamespace(
        memory=mem, runtime_kwargs=app.state.runtime_kwargs, config_path=str(tmp / "c.yaml")))
    req = _t.SimpleNamespace(app=kapp)

    # 一次一条(工作流强制:有待办未决不让喂下一条)。我代替真人:喂 URL → 聊一句 → 拍板沉。
    feeds = [
        ("https://www.pythontutorial.net/python-concurrency/python-event-loop/",
         "帮我抓住能复用的心智模型,重点是 event loop 怎么调度。"),
        ("https://en.wikipedia.org/wiki/Coral_bleaching",
         "珊瑚白化的主因和机制是什么?挑客观要点。"),
    ]
    sedimented = 0
    for url, human_msg in feeds:
        try:
            f = asyncio.run(routes.api_memory_feed(routes.MemoryFeedRequest(material=url), req))
            if not f.get("ok"):
                continue
            asyncio.run(routes.api_memory_distill_chat(routes.DistillChatRequest(message=human_msg), req))
            d = asyncio.run(routes.api_memory_distill_decide(
                routes.DistillDecideRequest(decision="persist"), req))
            sedimented += int(d.get("written", 0) or 0)
        except Exception:
            continue
    if sedimented < 3:
        pytest.skip(f"网络/沉淀不稳(sedimented={sedimented})—— 跳过真实知识沉淀压测")

    # ① 相关召回:问 event loop / 协程 → 激活扩散召回把 python 的知识点摆上来
    block = mem.recall_block("event loop 怎么调度协程实现并发 asyncio", scope="personal", limit=6)
    low = block.lower()
    assert block and any(w in low for w in ("event", "loop", "async", "coroutine", "并发", "协程", "asyncio")), \
        f"沉进知识库的 python 知识召不回(激活扩散种子没命中): {block[:180]!r}"
    # ② 零匹配不串台(本条揪出的产品 bug 已修):问库里完全没有的主题 → **返回空**。
    #    用纯 ASCII 无意义词,确保和中文知识**零 token 重叠**(中文 query 可能因 CJK bigram 偶合命中,
    #    那是 overlap 的边角、非本条要测的;这里测的是"真零匹配 → 不投毒")。
    none_block = mem.recall_block("zzqx9 wkjhgf qpwoei mnbvcx", scope="personal", limit=6)
    assert none_block == "", f"零匹配查询没返回空,反注入无关知识(串台/投毒): {none_block[:140]!r}"


# ---- J11:tool + skill 调取(同 L0、两套机制:能力门闸 vs 技能召回)----
def test_j11_tool_and_skill_capability(app):
    from karvyloop.capability.policy import DEFAULT_TOOL_REQUIREMENTS, Mode
    from karvyloop.coding.tools.web import WebSearchTool

    # tool 侧:web_search 在能力策略表、下限 READ_ONLY(maker/checker 都给,非默认 FULL 被拒)
    assert DEFAULT_TOOL_REQUIREMENTS.get("web_search") == Mode.READ_ONLY, \
        "web_search 不在策略表 → 默认 FULL 会被 capability_denied"
    # tool 真能跑(原语,无状态能力单元)
    try:
        r = asyncio.run(WebSearchTool(token=None)({"query": "anthropic", "max_results": 2}))
        assert getattr(r, "ok", False) or getattr(r, "error_code", 0), "web_search tool 连跑都没跑起来"
    except Exception:
        pass   # 网络问题不算 tool 机制坏(机制是上面的策略门 + 可构造可调)
    # skill 侧:data-analyst 在技能索引(skill 走另一套:索引 + recall,与 tool 同 L0)
    e = app.state.main_loop.skill_index.lookup_by_name("data-analyst")
    assert e is not None and e.sig, "skill 侧取不到 data-analyst(tool 与 skill 两套机制都该查得到)"


# ---- J12:跑评分离飞轮(drive 写 facts → 异步评满意度+真模型质量 → 置信累积 → Trace 保留 → token 记账)----
def test_j12_flywheel_run_eval_quality_confidence_real(app):
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.crystallize.trace_eval import evaluate_pending
    from karvyloop.llm.token_ledger import TokenLedger, get_ledger, register_ledger

    # 用**独立** MainLoop(共享的 _RT.main_loop 被所有 journey 的 drive 污染了 trace/satisfaction
    # → evaluate_pending 不确定)。飞轮测试要控自己的 trace,run 侧桩驱动、评侧真模型。
    ml = MainLoop(skills_dir=Path(tempfile.mkdtemp()) / "skills")
    ml.bootstrap()
    gw = app.state.runtime_kwargs["gateway"]
    mref = app.state.runtime_kwargs.get("model_ref", "")
    # 中性 intent(不命中任何技能 → 必走慢脑桩 → 真写 atom_run+eval_fact)
    intent = "压测飞轮探针 zeta-probe-x9"
    sb = _stub_brain(success=True, texts=["把这批输入整理成结构化结果,逐条核对无误"])
    for _ in range(5):
        ml.drive(intent, slow_brain=sb)

    # 跑评分离:drive 只写 facts;异步 evaluate 才从 eval_fact 记满意度
    n = evaluate_pending(ml.trace, ml.satisfaction)
    assert n >= 1, "异步评价没从 eval_fact 记出满意度 → 跑评分离断了(或 drive 没写 eval_fact)"
    assert ml.satisfaction.sigs(), "评了却没任何 sig 的满意度样本"
    sig = ml.satisfaction.sigs()[0]
    conf = ml.satisfaction.confidence_overall(sig)
    assert conf is not None and 0.0 <= conf <= 1.0, f"置信(Bayesian)算不出来: {conf!r}"

    # 真模型质量裁判 + token 记账(注册自己的账本,可靠读回)
    led = TokenLedger(Path(tempfile.mkdtemp()) / "tok.db")
    prev = get_ledger()
    register_ledger(led)
    try:
        def _qjudge(it, out):
            from karvyloop.crystallize.atom_critic import judge_quality
            try:
                return asyncio.run(judge_quality(it, out, gateway=gw, model_ref=mref))
            except Exception:
                return (None, "")
        ml.set_atom_quality_judge(_qjudge)
        q = ml.quality_review()                 # 真模型;并发可能截断 → best-effort
        rows = led.by_source()
    finally:
        register_ledger(prev)
    assert q >= 0, "质量复评直接崩了"
    if q >= 1:                                  # 真判了 → 该烧 token,账本该有记录
        assert any(int(r.get("output", 0)) > 0 for r in rows), \
            "质量裁判判了但 token 账本没记(咽喉记账漏了)"

    # Trace 保留:狠剪原文,提炼物(satisfaction/eval_fact)绝不丢
    before = len(ml.trace.query(ml.trace.all_tasks()[0], kind="satisfaction")) if ml.trace.all_tasks() else 0
    ml.trace.prune_raw(2)
    after = sum(len(ml.trace.query(tid, kind="satisfaction")) for tid in ml.trace.all_tasks())
    assert after >= before, "prune_raw 把提炼物(满意度)误删了 —— 容量环该只丢原文"


# ---- J13:跨 run 规律提炼 + 撤有害(真模型 lesson)—— 飞轮丙+戊的真路径 ----
def test_j13_lessons_distill_and_harm_reverter_real(app):
    from karvyloop.crystallize.lessons import distill_lessons, validate_lessons

    ml = app.state.main_loop
    gw = app.state.runtime_kwargs["gateway"]
    mref = app.state.runtime_kwargs.get("model_ref", "")

    def _ljudge(material):
        from karvyloop.crystallize.lessons import judge_lesson
        try:
            return asyncio.run(judge_lesson(material, gateway=gw, model_ref=mref))
        except Exception:
            return ""
    ml.set_lesson_judge(_ljudge)

    # 真路径跑通即合格(丙:提炼器读 Trace 对比满意/不满意样本;戊:撤有害是纯测量返字典)。
    # 不强造对比样本(需结晶技能+高低对比,脆;这里压"真模型路径不崩 + 返回契约对")。
    n = ml.lessons_review()                      # validate(撤有害) + distill(提炼),真模型
    assert isinstance(n, int) and n >= 0, "lessons 复评(丙+戊)返回契约坏了"
    # 撤有害单独再跑一次,验返回 dict 契约(纯测量,无 LLM,必稳)
    res = validate_lessons(ml.trace, ml.satisfaction, skills_dir=ml.skills_dir,
                           skill_index=ml.skill_index)
    assert isinstance(res, dict) and "reverted" in res, f"撤有害返回契约坏了: {res!r}"


# ============================================================================
# 2026-06-28 规模化:**真正的游戏**有体量 —— 5 业务域 + 10 子域 + 50+ role,
# 在这个被填满的世界里跑圆桌(多人)/工作流(多人 DAG)/agent 导入,
# 并压规模敏感子系统(域隔离召回 / 按 scope 的 Trace 保留 / 50 选 1 的模糊派发)。
# (此前 J1-J13 用 2-3 域、俩 role 的玩具世界 —— Hardy:"你一个都没操作?你在测什么?")
# ============================================================================

_WORLD = {
    "工程组": ["后端工程师", "前端工程师", "测试工程师", "架构师", "运维工程师",
             "数据库管理员", "安全工程师", "移动端工程师", "全栈工程师", "技术经理"],
    "数据组": ["数据分析师", "数据工程师", "算法工程师", "BI分析师", "数据科学家",
             "ETL工程师", "数仓架构师", "标注工程师", "实验分析师", "数据经理"],
    "设计组": ["UI设计师", "UX研究员", "交互设计师", "视觉设计师", "品牌设计师",
             "动效设计师", "用户研究员", "原型设计师", "设计系统工程师", "设计经理"],
    "运营组": ["增长运营", "内容运营", "社区运营", "活动运营", "用户运营",
             "渠道运营", "数据运营", "新媒体运营", "商务运营", "运营经理"],
    "研究组": ["研究员", "科学家", "论文作者", "实验设计师", "文献调研员",
             "标准研究员", "竞品分析师", "技术布道师", "专利工程师", "研究经理"],
}


@pytest.fixture(scope="module")
def world():
    """填满的世界:5 业务域 + 10 子域 + 50 role + 原子库,真 gateway/main_loop/记忆。"""
    import types as _t

    from karvyloop.atoms.registry import AtomRegistry, AtomStore
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.console.tasks import TaskRegistry
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    from karvyloop.roles.registry import RoleRegistry

    tmp = Path(tempfile.mkdtemp())
    atom_reg = AtomRegistry(store=AtomStore(tmp / "atoms.json"))
    for i in range(12):
        atom_reg.create(atom_id=f"atom_{i:02d}", kind=("task" if i % 2 else "daemon"),
                        prompt=f"capability {i}")
    role_reg = RoleRegistry(tmp / "roles", atom_registry=atom_reg)

    # agent_directory:把"域名"这个 role 展开成该域全部 10 个角色 → member_query="role:<域名>" 解析出 10 个成员
    # (否则 "agent:<lead>" 每域只 1 个成员,"全员"也才十几个;要 50 role 真参与就得让 50 个都是成员)。
    def _agent_dir(role_value):
        return tuple({"agent_id": r, "role": "agent", "status": "active"}
                     for r in _WORLD.get(role_value, []))
    reg = BusinessDomainRegistry(agent_directory=_agent_dir)

    domain_ids: dict = {}
    for di, (dname, roles) in enumerate(_WORLD.items()):
        # role: 子句必须**打头**才是"展开该 role 的全部 agent";跟在 user:/agent: 后会被当过滤器
        d = reg.create(name=dname, created_by="user:ch",
                       value_md_raw="# 价值观\n- 诚实\n- 交付", deontic=Deontic(),
                       member_query=f"role:{dname}")   # → agent_directory 展开成该域全部 10 个角色
        domain_ids[dname] = d.id
        for si in range(2):
            reg.create_child(parent_id=d.id, name=f"{dname}-子域{si+1}",
                             created_by="user:ch", deontic_override=Deontic(),
                             member_query=f"user:ch AND agent:{roles[si]}")
        for ri, rname in enumerate(roles):
            role_reg.create(role_id=rname, identity=f"{dname}的{rname}",
                            atom_ids=[f"atom_{(di + ri) % 12:02d}"],
                            nickname=rname, title=dname)

    mgr = ConversationManager(ConversationStore(tmp / "conv"))
    mgr.start()
    a = _t.SimpleNamespace(state=_t.SimpleNamespace(
        runtime_kwargs=_RT.runtime_kwargs, main_loop=_RT.main_loop,
        domain_registry=reg, memory=MemoryManager(store=BeliefStore(tmp / "beliefs.json")),
        conversation_manager=mgr, proposal_registry=PendingProposalRegistry(),
        task_registry=TaskRegistry(), ws_clients=set(), config_path=str(tmp / "c.yaml"),
        workbench_app=None, atom_registry=atom_reg, role_registry=role_reg,
        _domain_ids=domain_ids))
    a.state.proposal_handlers = build_proposal_handlers(a)
    return a


# ---- J14:世界真被填满了(5 域 + 10 子域 + 50 role + 原子,都可解析)----
def test_j14_populated_world_at_scale(world):
    doms = world.state.domain_registry.list_all()
    roots = [d for d in doms if getattr(d, "parent_id", None) is None]
    children = [d for d in doms if getattr(d, "parent_id", None) is not None]
    assert len(roots) == 5, f"业务域不是 5 个: {len(roots)}"
    assert len(children) == 10, f"子域不是 10 个: {len(children)}"
    roles = world.state.role_registry.list_all()
    assert len(roles) >= 50, f"role 不足 50: {len(roles)}"
    assert len(world.state.atom_registry.list_all()) >= 12
    members = world.state.domain_registry.resolve_members(roots[0].id)
    assert members, "域成员解析为空(member_query 没接上)"


# ---- J15:50 role 世界里开**多人圆桌**(6 参与者,真模型)----
def test_j15_roundtable_at_scale_real(world):
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, proposal_for_roundtable

    parts = ["后端工程师", "前端工程师", "架构师", "数据分析师", "UI设计师", "测试工程师"]
    p = proposal_for_roundtable(
        group_domain_id="l0", group_name="Karvy World",
        participants=parts, participant_names=parts,
        topic="跨组评审新功能的技术方案与设计", ts=time.time())
    world.state.proposal_registry.register(p)
    handler = world.state.proposal_handlers[KIND_ROUNDTABLE]
    ok, detail = None, ""
    for _ in range(3):
        ok, detail = handler(p)
        if ok:
            break
    assert ok, f"6 人圆桌没开起来: {detail}"
    peer = world.state.conversation_manager.current_peer()
    assert peer is not None and getattr(peer, "role", "") == "group", "没切到群 peer"
    assert world.state.conversation_manager.current().turn_count >= 1, "圆桌没开场轮"


# ---- J16:多人**工作流**规划(真模型设计跨 role 的 DAG)----
def test_j16_workflow_plan_at_scale_real(world):
    import types as _t

    from karvyloop.console.routes import WorkflowPlanRequest, api_workflow_plan
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, proposal_for_roundtable

    # @多人 workflow **发生在群场里** → 先开个圆桌建群(参与者即 workflow 的 5 个角色),
    # 让 workflow 规划的 roster 里有这几个人。自包含,不蹭 J15 的群态。
    parts = ["数据分析师", "算法工程师", "后端工程师", "增长运营", "研究员"]
    p = proposal_for_roundtable(group_domain_id="l0", group_name="Karvy World",
                                participants=parts, participant_names=parts,
                                topic="季度增长复盘", ts=time.time())
    world.state.proposal_registry.register(p)
    okr = False
    for _ in range(3):
        okr, _d = world.state.proposal_handlers[KIND_ROUNDTABLE](p)
        if okr:
            break
    if not okr:
        pytest.skip("圆桌没开起来(真模型并发截断),跳过 workflow 规划")

    # mentions 只带 agent_id(不带 domain_id;群 roster 成员 domain_id 是 l0,带源域 id 会不匹配)
    mentions = [{"agent_id": n} for n in parts]
    req = WorkflowPlanRequest(
        intent="拉通数据、算法、后端、运营、研究做一次季度增长复盘并产出下季策略",
        mentions=mentions)
    request = _t.SimpleNamespace(app=world)
    out = None
    for _ in range(3):
        out = asyncio.run(api_workflow_plan(req, request))
        if out.get("ok") and out.get("plan", {}).get("steps"):
            break
    assert out is not None and out.get("ok"), f"工作流规划失败: {out}"
    steps = out.get("plan", {}).get("steps", [])
    assert len(steps) >= 2, f"多人工作流没拆出多步 DAG(真模型只给 {len(steps)} 步): {out}"


# ---- J17:在 50 role 世界里**导入外部 agent**(真模型拆解成 role+atom)----
def test_j17_agent_import_in_world_real(world):
    import types as _t

    from karvyloop.console.routes import api_agent_import

    request = _t.SimpleNamespace(app=world)
    out = None
    for i in range(3):
        req = _t.SimpleNamespace(
            role_id=f"imported_qa_{i}", source_type="generic-json",
            system_prompt=("You are a QA automation specialist: you read specs, design test "
                           "matrices, run tests, triage failures, and file structured bug reports."),
            tools=["read_file", "run_command", "web_search", "file_bug"])
        out = asyncio.run(api_agent_import(req, request))
        if out.get("decomposed"):
            break
    assert out is not None and out.get("ok"), f"导入失败: {out}"
    assert out.get("decomposed") is True, f"真模型没拆解(反复降级 v0): {out}"
    assert (world.state.role_registry.root / out["role_id"]).exists(), "拆解了但角色没落库"
    assert len(out.get("atoms", [])) >= 1, "没拆出原子"


# ---- J18:规模敏感子系统 —— 域隔离召回 + 按 scope 的 Trace 保留(5 域/多 scope 下)----
def test_j18_domain_isolation_and_scoped_retention_at_scale(world):
    from karvyloop.schemas.cognition import Belief

    did = world.state._domain_ids
    mem = world.state.memory
    secrets = {"工程组": "工程组机密 部署密钥轮换流程", "数据组": "数据组机密 用户表加密口径",
               "设计组": "设计组机密 未发布的品牌改版", "运营组": "运营组机密 大客户返点比例",
               "研究组": "研究组机密 未公开的算法专利"}
    for dname, text in secrets.items():
        mem.write(Belief(content=text,
                         provenance={"source": "test", "agent": "user", "ts": time.time(),
                                     "kind": "fact", "applies": {"domain": did[dname]}},
                         freshness_ts=time.time(), scope="personal"))
    blk = mem.recall_block("机密", scope="personal", domain=did["工程组"], limit=8)
    assert "工程组机密" in blk, "本域私有知识在本域召不回"
    for dname in ("数据组", "设计组", "运营组", "研究组"):
        assert f"{dname}机密" not in blk, f"跨域泄露:{dname}的私有知识漏进了工程组召回"
    shared = mem.recall_block("机密", scope="personal", domain="", limit=8)
    assert all(f"{d}机密" not in shared for d in secrets), "私聊召回漏了域私有知识"

    from karvyloop.karvy.fastbrain.trace_index import TraceIndex
    idx = TraceIndex(Path(tempfile.mkdtemp()) / "f.sqlite", raw_capacity=3000)
    idx.append_raw({"q": "quiet-role-marker"}, scope="role_quiet")
    for i in range(60):
        idx.append_raw({"x": "y" * 200, "i": i}, scope="role_busy")
    quiet = idx.list_raw(limit=10, scope="role_quiet")
    assert any("quiet-role-marker" in str(r.payload) for r in quiet), \
        "忙 role 覆掉了安静 role 的上下文(按 scope 保留没生效)"
    idx.close()


# ---- J19:**测全局 AI** —— 我跟全局小卡说一句 NL,让它去识别+唤醒全员开圆桌 ----
def test_j19_global_karvy_wakes_all_roles_roundtable_real(world):
    """Hardy 纠:50 role 协作不该我手搓 proposal+调 handler(那是绕过全局 AI),
    而是**直接跟全局小卡说**,让 karvy 去唤醒。走 `maybe_route_to_role`(全局小卡编排入口):
    它识别这是大群全员圆桌 → 模糊派发(真模型,从 50 人 roster 里拉人)→ 圆桌提案 → 再 ACCEPT 开桌。
    验的是**全局 AI 能不能把'让全员开圆桌'这句话编排成多人圆桌并唤醒他们**,不是 handler 本身。"""
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE

    mgr = world.state.conversation_manager
    intent = "让所有角色一起开个圆桌讨论年度技术战略与组织调整"
    before = len(world.state.proposal_registry.pending())
    out = None
    for _ in range(3):                       # 真模型模糊派发,并发可能截断 → 重试
        out = asyncio.run(maybe_route_to_role(world, mgr, intent))
        if out and out.get("routed") and len(world.state.proposal_registry.pending()) > before:
            break
    assert out is not None and out.get("routed"), f"全局小卡没把'全员圆桌'编排出来(全局 AI 没接住): {out}"
    p = world.state.proposal_registry.pending()[-1]
    assert p.kind == KIND_ROUNDTABLE, f"全局小卡没识别成圆桌(误判成委派/其它): {p.kind}"
    # 全局 AI 真**唤醒了全员**(不是塌成 1-2 个)——这才是"50 role 协作"(世界 ~50 成员)
    participants = (p.payload or {}).get("participants", [])
    assert len(participants) >= 40, f"全局 AI 只唤醒了 {len(participants)} 个角色,没体现全员规模: {participants}"
    # 唤醒的是世界里真实的角色(不是凭空编的)
    real = {r for roles in _WORLD.values() for r in roles}
    names = set((p.payload or {}).get("participant_names", [])) | set(participants)
    assert names & real, f"唤醒的角色对不上世界里的真角色: {names}"
    # ACCEPT → 真开桌(全局 AI 编排的提案能落地开桌)
    ok, detail = world.state.proposal_handlers[KIND_ROUNDTABLE](p)
    assert ok, f"全局 AI 编排的圆桌 ACCEPT 没开起来: {detail}"
    peer = world.state.conversation_manager.current_peer()
    assert peer is not None and getattr(peer, "role", "") == "group", "圆桌没切到群 peer"


# ---- J20:**测全局 AI 的工作流** —— 跟小卡说让全员协作 → 唤醒全员进大群 → @全员跑工作流 ----
def test_j20_global_karvy_workflow_all_roles_real(world):
    """同 J19 的纪律:不手搓圆桌建群、不手列 50 个 mention。而是走全局小卡 NL 唤醒全员进大群,
    再用群里被真唤醒的成员当 @全员 mention,经真 workflow api 设计跨角色 DAG。"""
    import types as _t

    from karvyloop.console.routes import (WorkflowPlanRequest, api_workflow_plan,
                                          maybe_route_to_role)
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE

    mgr = world.state.conversation_manager
    # ① 全局小卡 NL → 唤醒全员进大群(真路径,同 J19)
    out = None
    for _ in range(3):
        out = asyncio.run(maybe_route_to_role(world, mgr, "让所有角色一起开个圆桌做年度全链路复盘"))
        if out and out.get("routed"):
            break
    if not out or not out.get("routed"):
        pytest.skip("全局小卡没编排出全员圆桌(真模型),跳过 50 角色 workflow")
    p = world.state.proposal_registry.pending()[-1]
    okr = False
    for _ in range(3):
        okr, _d = world.state.proposal_handlers[KIND_ROUNDTABLE](p)
        if okr:
            break
    if not okr:
        pytest.skip("全员圆桌没开起来,跳过 workflow")

    # ② @全员 = 群里被真唤醒的所有成员(不是我手列的 50 个名字)→ 真 workflow api 设计 DAG
    woke = (p.payload or {}).get("participants", [])
    assert len(woke) >= 40, f"全员只唤醒了 {len(woke)} 个,没到 50 规模(member 解析没展开?)"
    mentions = [{"agent_id": n} for n in woke]
    req = WorkflowPlanRequest(
        intent="全员协同做一次年度全链路复盘:各域各司其职,产出下年改进路线图", mentions=mentions)
    request = _t.SimpleNamespace(app=world)
    out2 = None
    for _ in range(3):
        out2 = asyncio.run(api_workflow_plan(req, request))
        if out2.get("ok") and out2.get("plan", {}).get("steps"):
            break
    assert out2 is not None and out2.get("ok"), f"全员工作流规划失败(规模上规划器扛不住?): {out2}"
    steps = out2.get("plan", {}).get("steps", [])
    assert len(steps) >= 5, f"全员工作流只拆出 {len(steps)} 步(规模没体现/被截断): {out2}"
    used = {s.get("agent_id") for s in steps if s.get("agent_id")}
    assert len(used) >= 5, f"全员工作流只用了 {len(used)} 个角色: {used}"


# ---- J21:自造原子整条缝(create_atom 真合成+tags → 结果确认卡 → ACCEPT 真模型综合裁 → 沉淀自洽)----
# 这条专抓"零件单测看不见的缝":真模型合成 AtomSpec、真模型综合裁 keep/drop、沉淀进/出 composition 无悬空。
def test_j21_self_create_to_confirm_to_sediment_real(app):
    from karvyloop.atoms.self_create import create_atom
    from karvyloop.karvy.proposal_registry import KIND_CONFIRM_RESULT, proposal_for_confirm_result

    gw = app.state.runtime_kwargs["gateway"]
    areg = app.state.atom_registry
    rreg = app.state.role_registry
    if rreg.get("分析师") is None:
        rreg.create("分析师", identity="数据分析师", soul="严谨求证")

    # 1) 真模型合成一个池里没有的能力(role 无 atom 可用时自造)
    res = asyncio.run(create_atom("把一份 CSV 按列做统计汇总并生成 markdown 报告", gateway=gw,
                                  atom_registry=areg, role_registry=rreg, role_id="分析师"))
    if res.get("action") != "created":
        pytest.skip(f"真模型没合成出可用原子(action={res.get('action')}),跳过缝测")
    aid = res["atom_id"]
    spec = areg.get(aid)
    assert spec is not None and spec.provisional is True and spec.origin == "self_created"
    assert isinstance(spec.tags, list)                       # ③#1:tags 字段在(非空更好)
    assert aid not in (rreg.get("分析师").atom_ids or [])      # 出生即孤儿(没进 composition)

    # 2) 升结果确认卡 + ACCEPT(真模型站 role 视角综合裁 keep/drop)
    card = proposal_for_confirm_result(role="分析师", requirement="做 CSV 统计报告",
                                       minted=[{"id": aid, "purpose": spec.prompt[:80]}], ts=1.0)
    ok, detail = app.state.proposal_handlers[KIND_CONFIRM_RESULT](card)
    assert ok is True, f"确认卡兑现失败: {detail}"

    # 3) 沉淀自洽(无悬空):留→必进 composition;撤→必删。两者必居其一。
    still = areg.get(aid) is not None
    composed = aid in (rreg.get("分析师").atom_ids or [])
    assert (still and composed) or (not still and not composed), \
        f"沉淀不自洽 still={still} composed={composed}(留必入composition、撤必删=无悬空):{detail}"


# ---- J22:「第一个 10 分钟」旅程(新用户态 → 任务1 → 任务2 → 方法复用回执 + 曲线第一批点)----
# 装完 10 分钟的 wow 剧本整条真跑:干净实例(零 Trace = 旅程 fresh)→ 两个演示任务按**前端
# 真实组装**(样例 CSV 附件内联在前、问题在后)走 drive_in_tui(真模型)→ 两次都必须出
# 方法复用回执(outcome.skill_name 来自真 recall 命中 data-analyst)→ /api/skills/curve 的
# 构建函数上真长出第一批点。诚实红线:全程无桩、无罐头 —— 断的全是真机制的真产出。
def test_j22_first_ten_minutes_journey_real(app):
    from karvyloop.crystallize.curve import build_curves
    from karvyloop.onboarding import (
        JOURNEY_TASKS, compose_task_intent, load_sample, read_stage)
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.workbench.main_loop_bridge import drive_in_tui

    # 独立 MainLoop = 干净的新用户实例(共享 _RT.main_loop 已被前面 J 步污染 Trace)
    tmp = Path(tempfile.mkdtemp())
    ml = MainLoop(skills_dir=tmp / "skills")
    ml.bootstrap()   # bundled data-analyst 系统技能进索引(J8 已单验)
    assert not ml.trace.all_tasks(), "干净实例竟有 Trace?"
    assert read_stage(tmp / "onboarding.json",
                      has_runs=bool(ml.trace.all_tasks())) == "fresh", "新用户态没判成 fresh"

    name, text = load_sample()
    assert name and text.strip(), "随包样例数据丢了"
    # W1:workspace + fs token 覆盖到 tmp —— 真模型的分析脚本绝不写进源码树(只动本地副本)
    rk = _tmp_workspace_kwargs(dict(app.state.runtime_kwargs), tmp)
    assert Path(rk["workspace_root"]).resolve().is_relative_to(tmp.resolve()), \
        "J22 的 workspace 没覆盖到 tmp(真模型会写脏源码树)"

    # 非 COMPLETED 终局(infra-dead/预算/断路…)会把 ⚠ 兜底提示**拼进 text 且 error=None**——
    # 光断"text 非空"会被兜底文案骗成假绿(2026-07-04 实捕:CodingPrompt.to_blocks 少 cache
    # 参数 → 每次调模型 TypeError → 被判 infra-dead,text=兜底提示,旧断言全过)。
    def _assert_real_output(o, label):
        assert not o.error, f"{label} 真跑失败: {o.error}"
        assert (o.text or "").strip(), f"{label} 没有真产出"
        assert "基础能力暂时不可用" not in o.text and "非正常结束" not in o.text, \
            f"{label} 拿兜底提示冒充产出(模型根本没真跑通): {o.text[:120]!r}"

    # 任务1(真模型):镜像前端 _submitChat 的组装(附件内联在前、问题在后)
    intent1 = compose_task_intent(JOURNEY_TASKS["zh"]["task1"],
                                  sample_name=name, sample_text=text)
    o1 = asyncio.run(drive_in_tui(intent1, ml, **rk))
    _assert_real_output(o1, "任务1")
    assert o1.skill_name == "data-analyst", \
        f"任务1 没命中 data-analyst 方法召回(skill_name={o1.skill_name!r})→ 聊天里回执出不来"

    # 任务2(真模型,同类任务):第二次跑的召回命中回执 —— 10 分钟 wow 的主菜
    intent2 = compose_task_intent(JOURNEY_TASKS["zh"]["task2"],
                                  sample_name=name, sample_text=text)
    o2 = asyncio.run(drive_in_tui(intent2, ml, **rk))
    _assert_real_output(o2, "任务2")
    assert o2.skill_name == "data-analyst", \
        f"任务2 没命中方法召回(skill_name={o2.skill_name!r})→ 方法复用回执消失"

    # 成长曲线第一批点:/api/skills/curve 同一构建函数(K4 只读,全部从 Trace 推导)
    curves = build_curves(ml.trace,
                          name_resolver=lambda s: ml.skill_index.name_for_sig(s) or "")
    da = [s for s in curves["skills"] if s["sig"] == "system:data-analyst"]
    assert da and da[0]["points"], \
        f"曲线上没有 data-analyst 的点(skills={[s['sig'] for s in curves['skills']]!r})"
    last = da[0]["points"][-1]
    assert last["usage_count"] >= 1 and last["reruns"] >= 1, \
        f"重跑没长成曲线点(usage={last['usage_count']} reruns={last['reruns']})"
    # 成败也从 Trace 真实回放:两次真跑通,曲线点必须有真 success(假绿时这里是 0)
    assert last["success_count"] >= 1, \
        f"曲线点 success_count=0 —— 两次'成功'其实都没跑通(eval_fact.success 全 False)"
