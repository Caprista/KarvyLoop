"""test_delegated_fs_access_card — 委派执行路径的 fs_access 授权卡覆盖(H2A 能力授予夯实）。

背景（设计对齐结论）：能力缺失**不弹同步 popup**（违 §0.7 尽责下属 + 「怎么样了」反模式：
atom 自主跑、role 重规划、带证据回头，不把人塞进执行 loop）。既有正道 = 工作区外路径碰壁
→ note_denied 攒「想要」→ drive 收尾 raise_fs_access_cards 升 H2A 授权卡（KIND_FS_ACCESS）
→ 你 ACCEPT → 台账放行。

缺口（本测复现 + 锁）：raise_fs_access_cards 此前只挂在**顶层 drive**（routes.api_intent /
ws 意图处理）。**委派执行**（route_to_role / run_task 的 ACCEPT handler → pursue → drive →
工具）碰壁时 note_denied 照记，却**没人在这一轮把它升成卡** —— 用户看不到授权卡，委派活白卡壳。

不变量：
- 委派 ACCEPT 兑现后，这一轮里碰壁的工作区外路径 → 升 KIND_FS_ACCESS 卡（与顶层 drive 同待遇）。
- 敏感路径永不出卡（note_denied 已滤）；同路径卡挂着不重复。
- 无碰壁 → 零新卡（0 回归）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from karvyloop.capability.fs_grants import FsGrantsStore, register_store
from karvyloop.console import build_console_app
from karvyloop.console.proposal_handlers import build_proposal_handlers
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.karvy.proposal_registry import (
    KIND_FS_ACCESS,
    PendingProposalRegistry,
    proposal_for_route,
)


def teardown_function(_fn):
    register_store(None)   # 全局注册表不串测试


def _make_app_with_delegation(monkeypatch, tmp_path, *, outside_paths):
    """建一个真 console app：委派 handler 的 drive 桩会对 outside_paths 逐个 note_denied
    （模拟被委派 role 干活时碰壁工作区外路径），返回 (app, client, proposal)。"""
    from karvyloop.capability.fs_grants import note_denied

    reg = _reg_with_designer()
    d = list(reg.list_all())[0]

    class _Result:
        text = "已完成海报初稿"
        error = ""
        terminal = "completed"

    class _ML:
        def drive(self, requirement, slow_brain=None, prefer=None):
            # 被委派 role 干活时碰壁工作区外路径 → 记「想要」（正是生产工具层的行为）
            for p, op in outside_paths:
                note_denied(p, op)
            return _Result()

    import karvyloop.runtime.main_loop as ml_mod
    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory",
                        lambda **kw: ("sb", kw.get("governance", "")))

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=_ML())
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.domain_registry = reg
    app.state.runtime_kwargs = {"token": None, "sandbox": None, "gateway": None,
                                "workspace_root": str(tmp_path / "ws")}
    st = FsGrantsStore()
    app.state.fs_grants = st
    register_store(st)
    app.state.proposal_handlers = build_proposal_handlers(app)

    p = proposal_for_route(domain_id=d.id, role="设计师", agent_id="设计师",
                           domain_name="设计工作室", requirement="出一版海报", ts=1.0)
    app.state.proposal_registry.register(p)
    return app, TestClient(app), p


def _reg_with_designer():
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    reg = BusinessDomainRegistry()
    reg.create(name="设计工作室", created_by="user:ch",
               value_md_raw="# 价值观\n- 诚实第一", deontic=Deontic(),
               member_query="user:ch AND agent:设计师")
    return reg


def test_delegated_denial_raises_fs_access_card(monkeypatch, tmp_path):
    """委派 ACCEPT → 被委派 role 碰壁工作区外路径 → 这一轮就升 fs_access 授权卡。

    修复前：委派路径不 drain note_denied → 0 卡（复现缺口）。修复后：1 张 KIND_FS_ACCESS。"""
    outside = str(tmp_path / "contracts" / "poster.pdf")
    app, client, p = _make_app_with_delegation(
        monkeypatch, tmp_path, outside_paths=[(outside, "read"), (outside, "write")])

    r = client.post("/api/h2a_decide",
                    json={"proposal_id": p.proposal_id, "decision": "ACCEPT"})
    assert r.status_code == 200, r.text
    assert r.json()["dispatch"]["ok"], r.json()

    cards = [c for c in app.state.proposal_registry.pending() if c.kind == KIND_FS_ACCESS]
    assert len(cards) == 1, "委派执行碰壁的工作区外路径应升成 fs_access 授权卡"
    # 同路径两个 op 合并成一张卡（read+write）
    assert cards[0].payload["path"] == outside
    assert cards[0].payload["ops"] == ["read", "write"]


def test_delegated_no_denial_raises_nothing(monkeypatch, tmp_path):
    """委派执行没碰壁 → 零新卡（0 回归：不无端刷卡）。"""
    app, client, p = _make_app_with_delegation(monkeypatch, tmp_path, outside_paths=[])
    r = client.post("/api/h2a_decide",
                    json={"proposal_id": p.proposal_id, "decision": "ACCEPT"})
    assert r.status_code == 200, r.text
    cards = [c for c in app.state.proposal_registry.pending() if c.kind == KIND_FS_ACCESS]
    assert cards == []


def test_delegated_sensitive_path_never_carded(monkeypatch, tmp_path):
    """委派执行碰的是敏感路径（密钥/ssh）→ note_denied 已滤，永不出授权卡（硬地板）。"""
    import pathlib
    ssh_key = str(pathlib.Path.home() / ".ssh" / "id_rsa")
    app, client, p = _make_app_with_delegation(
        monkeypatch, tmp_path, outside_paths=[(ssh_key, "read")])
    r = client.post("/api/h2a_decide",
                    json={"proposal_id": p.proposal_id, "decision": "ACCEPT"})
    assert r.status_code == 200, r.text
    cards = [c for c in app.state.proposal_registry.pending() if c.kind == KIND_FS_ACCESS]
    assert cards == [], "敏感路径绝不出授权卡（硬地板）"


def test_delegated_denial_raises_card_via_ws(monkeypatch, tmp_path):
    """WS 决策路径同覆盖：委派 ACCEPT 走 WebSocket → 碰壁路径也升 fs_access 卡（两条传输路一致）。"""
    outside = str(tmp_path / "invoices" / "q3.pdf")
    app, client, p = _make_app_with_delegation(
        monkeypatch, tmp_path, outside_paths=[(outside, "read")])
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        ws.send_json({"type": "h2a_decision",
                      "payload": {"proposal_id": p.proposal_id, "decision": "ACCEPT",
                                  "reason": ""}})
        # 收到两条(顺序不定):h2a_envelope(决策回执)+ h2a_proposal(新升的 fs_access 卡实时推)
        got = {}
        for _ in range(2):
            m = ws.receive_json()
            got[m["type"]] = m
        assert "h2a_envelope" in got and not got["h2a_envelope"]["payload"].get("error"), got
        # 委派碰壁的授权卡被**实时推**过来(WS drain 生效)
        assert got.get("h2a_proposal", {}).get("payload", {}).get("kind") == KIND_FS_ACCESS
    cards = [c for c in app.state.proposal_registry.pending() if c.kind == KIND_FS_ACCESS]
    assert len(cards) == 1 and cards[0].payload["path"] == outside
