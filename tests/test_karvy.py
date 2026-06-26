"""karvy —— M3 路线 C 拍 3:小卡本体测试(8 个:7 AC + 1 协议)。

设计:docs/20 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import (  # noqa: E402
    BroadcastPayload,
    Envelope,
    EnvelopeType,
    QA,
    sign_envelope,
)
from karvyloop.domain import Address  # noqa: E402

from karvyloop.karvy import (  # noqa: E402
    H2A_ACCEPT,
    H2A_DEFER,
    H2A_REJECT,
    H2ADecision,
    BoardAggregator,
    BoardSnapshot,
    Courier,
    DataCourier,
    KarvyAlreadyInitializedError,
    KarvyCore,
    KarvyRoleError,
    Overseer,
    TaskTracker,
    WorkbenchObserver,
    decision_to_envelope,
    h2a_decide,
)


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def _reset_karvy_singleton():
    """每个测试前重置小卡单例(避免测试间污染)。"""
    KarvyCore.reset_for_test()
    yield
    KarvyCore.reset_for_test()


def _user(domain: str = "dom-1") -> Address:
    return Address(domain_id=domain, role="user", agent_id="ch")


def _engineer(domain: str = "dom-1", agent: str = "eng-1") -> Address:
    return Address(domain_id=domain, role="engineer", agent_id=agent)


def _pm(domain: str = "dom-1", agent: str = "pm-1") -> Address:
    return Address(domain_id=domain, role="pm", agent_id=agent)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _build_envelope(*, env_type: str, from_: Address, to: Address, payload) -> Envelope:
    env = Envelope(
        type=env_type,
        from_=from_,
        by=(),
        to=to,
        payload=payload,
        ts=_now_ts(),
        signature=b"",
    )
    return Envelope(
        type=env.type,
        from_=env.from_,
        by=env.by,
        to=env.to,
        payload=env.payload,
        ts=env.ts,
        signature=sign_envelope(env),
    )


# ---------- AC1: 小卡是 observer(K1)----------

class TestAC1KarvyIsObserver:
    """AC1: KarvyCore().role == 'observer',其他 role 抛 KarvyRoleError(K1)。"""

    def test_default_role_is_observer(self):
        k = KarvyCore()
        assert k.role == "observer"
        assert k.agent_id == "karvy"

    def test_non_observer_role_raises(self):
        with pytest.raises(KarvyRoleError):
            KarvyCore(role="engineer", agent_id="karvy")

    def test_non_karvy_agent_id_raises(self):
        with pytest.raises(KarvyRoleError):
            KarvyCore(role="observer", agent_id="other")

    def test_is_observer_returns_true(self):
        k = KarvyCore()
        assert k.is_observer() is True


# ---------- AC2: 小卡单实例(K6)----------

class TestAC2Singleton:
    """AC2: 两次 KarvyCore() 返同一 ID(K6)。"""

    def test_two_constructions_return_same_instance(self):
        k1 = KarvyCore()
        k2 = KarvyCore()
        assert k1 is k2

    def test_singleton_role_unchanged(self):
        KarvyCore()
        # 第二次构造: 角色不一致抛(K1)
        with pytest.raises(KarvyRoleError):
            KarvyCore(role="engineer", agent_id="karvy")


# ---------- AC3: courier_send 构造 from: user, by: (karvy,)(K2)----------

class TestAC3Courier:
    """AC3: courier_send 输出 envelope 的 from_=user, by=(karvy,)。"""

    def test_courier_send_constructs_coupled(self):
        courier = Courier()
        env = courier.send(
            user_address=_user(),
            to=_engineer(),
            envelope_type="ask",
            payload=QA(question="帮我 review"),
        )
        assert env.from_.role == "user"
        assert len(env.by) == 1
        assert env.by[0].agent_id == "karvy"
        assert env.by[0].role == "observer"

    def test_courier_send_signature_present(self):
        courier = Courier()
        env = courier.send(
            user_address=_user(),
            to=_engineer(),
            envelope_type="ask",
            payload=QA(question="q"),
        )
        assert env.signature  # 非空

    def test_courier_send_rejects_non_user(self):
        courier = Courier()
        with pytest.raises(ValueError):
            courier.send(
                user_address=_engineer(),  # role=engineer,非法
                to=_pm(),
                envelope_type="ask",
                payload=QA(question="q"),
            )

    def test_courier_send_rejects_observer_target(self):
        """K2: courier 不发给其他 observer。"""
        courier = Courier()
        with pytest.raises(ValueError):
            courier.send(
                user_address=_user(),
                to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
                envelope_type="ask",
                payload=QA(question="q"),
            )


# ---------- AC4: 小卡只收 BROADCAST(K3)----------

class TestAC4ObserverSubscribe:
    """AC4: observer.subscribe(TASK_ASSIGN) 返 rejected=True, reason="observer_filter"(K3)。"""

    def test_task_assign_to_karvy_rejected(self):
        wb = WorkbenchObserver()
        env = _build_envelope(
            env_type="task.assign",
            from_=_pm(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload={"task_id": "t1", "description": "d1"},
        )
        result = wb.subscribe_to(env)
        assert result.rejected is True

    def test_broadcast_to_karvy_accepted(self):
        wb = WorkbenchObserver()
        env = _build_envelope(
            env_type="broadcast",
            from_=_pm(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="新任务", tag="task"),
        )
        result = wb.subscribe_to(env)
        assert result.rejected is False
        # 缓存
        snap = wb.snapshot("dom-1")
        assert snap.unread_count == 1


# ---------- AC5: 工作台只读(K4)----------

class TestAC5WorkbenchReadOnly:
    """AC5: 工作台接口列表中没有 domain.apply_*(源码扫描,K4)。"""

    def test_workbench_has_no_apply_methods(self):
        wb = WorkbenchObserver()
        methods = dir(wb)
        for m in methods:
            assert "apply_" not in m, f"WorkbenchObserver.{m} violates K4 read-only"

    def test_workbench_methods_are_read_only(self):
        """WorkbenchObserver 的所有方法都只读,无副作用写入。"""
        wb = WorkbenchObserver()
        methods = [m for m in dir(wb) if not m.startswith("_") and callable(getattr(wb, m))]
        # 没有 mutate 类方法
        for m in methods:
            assert m.startswith(("snapshot", "list_", "fetch_", "subscribe_")), (
                f"unexpected method {m} on WorkbenchObserver"
            )

    def test_snapshot_is_frozen_dataclass(self):
        """BoardSnapshot 是 frozen dataclass(只读)。"""
        import dataclasses
        assert dataclasses.is_dataclass(BoardSnapshot)
        # 验证 frozen
        snap = BoardSnapshot(domain_id="d", karvy_role="observer", broadcasts=(), unread_count=0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.unread_count = 99  # type: ignore


# ---------- AC6: H2A 等用户(K5)----------

class TestAC6H2AWaitForUser:
    """AC6: 调 h2a_decide(proposed) 不立刻返回,等 user_input(ACCEPT) 后才投 A2A ACCEPT(K5)。"""

    def test_h2a_decide_with_user_accept(self):
        def user_input(prompt, user):
            return H2ADecision(
                user_address=user,
                proposal_id="p1",
                decision=H2A_ACCEPT,
            )
        decision = h2a_decide(
            user=_user(),
            proposal_id="p1",
            proposal_summary="投不投?",
            user_input=user_input,
        )
        assert decision.decision == H2A_ACCEPT
        # 转 envelope
        env = decision_to_envelope(decision, to=_pm(domain="dom-1"))
        assert env.type == "accept"
        assert env.from_.role == "user"
        assert env.by == ()  # H2A 是用户直接发,by 空

    def test_h2a_decide_with_user_reject(self):
        def user_input(prompt, user):
            return H2ADecision(
                user_address=user,
                proposal_id="p1",
                decision=H2A_REJECT,
                reason="预算超支",
            )
        decision = h2a_decide(
            user=_user(),
            proposal_id="p1",
            proposal_summary="投不投?",
            user_input=user_input,
        )
        assert decision.decision == H2A_REJECT
        env = decision_to_envelope(decision, to=_pm(domain="dom-1"))
        assert env.type == "reject"
        assert env.payload.reason == "预算超支"

    def test_h2a_decide_defer_does_not_convert(self):
        """DEFER 不转 envelope(用户没拍板)。"""
        def user_input(prompt, user):
            return H2ADecision(
                user_address=user,
                proposal_id="p1",
                decision=H2A_DEFER,
            )
        decision = h2a_decide(
            user=_user(),
            proposal_id="p1",
            proposal_summary="投不投?",
            user_input=user_input,
        )
        assert decision.decision == H2A_DEFER
        # DEFER 不转 envelope
        with pytest.raises(ValueError):
            decision_to_envelope(decision, to=_pm(domain="dom-1"))

    def test_h2a_decide_rejects_non_user(self):
        with pytest.raises(ValueError):
            h2a_decide(
                user=_engineer(),  # 非 user
                proposal_id="p1",
                proposal_summary="q",
            )

    def test_h2a_reject_must_have_reason(self):
        def user_input(prompt, user):
            return H2ADecision(
                user_address=user,
                proposal_id="p1",
                decision=H2A_REJECT,
                reason="",  # 空
            )
        with pytest.raises(ValueError):
            h2a_decide(
                user=_user(),
                proposal_id="p1",
                proposal_summary="q",
                user_input=user_input,
            )


# ---------- AC7: 原子 agent 不参与 A2A(K7)----------

class TestAC7AtomsNoA2A:
    """AC7: 原子 agent 无 EnvelopeRouter 接入(源码扫描,K7)。"""

    def test_atoms_class_source_no_router(self):
        """TaskTracker / BoardAggregator / DataCourier / Overseer 的源码无 EnvelopeRouter。"""
        import inspect
        from karvyloop.karvy import atoms as atoms_mod
        src = inspect.getsource(atoms_mod)
        assert "EnvelopeRouter" not in src, "atoms.py 引用了 EnvelopeRouter(K7 违反)"

    def test_atoms_only_take_workbench(self):
        """4 个原子 agent 的字段只有 workbench(无 router/inbox/audit)。"""
        # dataclass 字段名
        import dataclasses
        for cls in (TaskTracker, BoardAggregator, DataCourier, Overseer):
            field_names = {f.name for f in dataclasses.fields(cls)}
            assert "workbench" in field_names
            # K7 边界:不接 A2A 组件
            assert "router" not in field_names
            assert "inbox" not in field_names
            assert "audit" not in field_names

    def test_task_tracker_filters_task_types(self):
        """TaskTracker 只看 task.*(不参与其他类型)。"""
        wb = WorkbenchObserver()
        # 推 2 个广播
        wb.subscribe_to(_build_envelope(
            env_type="broadcast", from_=_pm(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="t1 进度", tag="task.progress"),
        ))
        wb.subscribe_to(_build_envelope(
            env_type="broadcast", from_=_pm(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="市场洞察", tag="market"),
        ))
        tracker = TaskTracker(workbench=wb)
        # 1 个 task.progress 标签 + 1 个非 task,但 tracker 只看 type.startswith("task.")
        tasks = tracker.tracked_tasks("dom-1")
        # type 都是 broadcast,不是 task.*,所以 tracked_tasks = ()
        assert len(tasks) == 0


# ---------- AC8: 协议不变量 ----------

class TestAC8ProtocolInvariants:
    """AC8: 8 不变量锁定(协议测试)+ 0 LLM(源码扫 openai/anthropic/litellm)。"""

    def test_karvy_address_uses_observer_role(self):
        k = KarvyCore()
        addr = k.address("dom-1")
        assert addr.role == "observer"
        assert addr.agent_id == "karvy"
        assert addr.domain_id == "dom-1"

    def test_overseer_health_check(self):
        """Overseer 健康检查(K7 边界:只看 workbench)。"""
        wb = WorkbenchObserver()
        overseer = Overseer(workbench=wb, health_threshold=2)
        # 空工作台健康
        assert overseer.is_healthy() is True
        # 推 3 个广播,超阈值
        for i in range(3):
            wb.subscribe_to(_build_envelope(
                env_type="broadcast", from_=_pm(),
                to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
                payload=BroadcastPayload(message=f"m{i}"),
            ))
        assert overseer.is_healthy() is False

    def test_data_courier_answer_uses_snapshot(self):
        """DataCourier.answer 只读工作台快照(K4 + K7)。"""
        wb = WorkbenchObserver()
        wb.subscribe_to(_build_envelope(
            env_type="broadcast", from_=_pm(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload=BroadcastPayload(message="m1"),
        ))
        dc = DataCourier(workbench=wb)
        ans = dc.answer("dom-1", "我有什么任务?")
        assert ans["domain_id"] == "dom-1"
        assert ans["question"] == "我有什么任务?"
        assert ans["snapshot"]["broadcast_count"] == 1

    def test_no_llm_imports(self):
        """K8: 不调 LLM。源码扫 openai/anthropic/litellm。"""
        import karvyloop.karvy as mod
        import inspect
        src = inspect.getsource(mod)
        for forbidden in ("openai", "anthropic", "litellm"):
            assert forbidden not in src, f"karvy/{mod.__name__} imports {forbidden}"
