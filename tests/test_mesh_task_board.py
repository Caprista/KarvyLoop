"""test_mesh_task_board — mesh 任务板实驱(docs/74 §6.2/§6.3 第二环·切片B)。

语义锁:
① 发布侧幂等对账:两轮 tick 不重复 offer;心跳按 lease/3 单调续租(不每 tick 刷);
   finish → done 恰一次;从没上板的死账不补;别台接管中不动别人的账。
② 接活判定:reclaimable(lease 过期)+ feasible + claimer≠本机 才弹;弹过不重弹(含跨重启);
   扫描本身零副作用(H2A:绝不 auto-execute)。
③ H2A 卡形状:kind/summary/basis/payload/稳定 proposal_id。
④ ACCEPT 路径(mock 重跑):claim 上账 → 骑 run_task 重跑 → complete 上账(失败也如实记终态)。
⑤ REJECT:handler 不被调、mesh 日志不动、任务留 reclaimable(别的设备还能接)。
⑥ mesh_tick 接线:发布每轮跑(单机也跑)、接活在同步后跑、卡走 broadcast_proposal。
测试身份一律 FAKE。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import karvyloop.console.mesh_task_board as board  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.karvy.proposal_registry import KIND_MESH_TAKEOVER, PendingProposalRegistry  # noqa: E402
from karvyloop.mesh.store import MeshLogStore  # noqa: E402
from karvyloop.mesh.synclog import MeshLog  # noqa: E402
from karvyloop.mesh.tasks import (  # noqa: E402
    K_CLAIM, K_DONE, K_HEARTBEAT, K_OFFER,
    ST_DONE, ST_RECLAIMABLE,
    claim_task, materialize_tasks, offer_task,
)

MY_ID = "dev-A-FAKE"
PEER_ID = "dev-B-FAKE"
T0 = 1_000_000_000                    # 起始墙钟(毫秒;mesh 日志单位契约)
LEASE_MS = board.TASK_LEASE_S * 1000.0
HB_MS = board.TASK_HEARTBEAT_EVERY_S * 1000.0


@pytest.fixture(autouse=True)
def _fake_identity(monkeypatch):
    """本机身份/能力打桩(不碰真 relay 密钥);测试身份一律 FAKE。"""
    monkeypatch.setattr(board, "device_fingerprint",
                        lambda sd: {"device_id": MY_ID,
                                    "capabilities": ["coding", "shell", "big-task"]})


def _app(sd, reg=None):
    return SimpleNamespace(state=SimpleNamespace(
        mesh_state_dir=sd, task_registry=reg, ws_clients=set(), proposal_registry=None))


def _events(sd, kind=None, task_id=None):
    evs = MeshLogStore(sd).load_events()
    if kind is not None:
        evs = [e for e in evs if e.kind == kind]
    if task_id is not None:
        evs = [e for e in evs if (e.payload or {}).get("task_id") == task_id]
    return evs


def _seed_remote_task(sd, task_id="t-remote-1", *, needs=("coding",), intent="跑每日汇总",
                      wall=T0, claimer=PEER_ID):
    """往盘上日志种一条"别机认领过"的任务(offer+claim,由 claimer 设备产生)。"""
    log = MeshLog(claimer)
    offer_task(log, task_id, list(needs), {"intent": intent, "domain_id": "l0", "role": "",
                                           "source_device": claimer},
               wall=wall, lease_s=LEASE_MS)
    claim_task(log, task_id, wall=wall)
    MeshLogStore(sd).append(log.entries())
    return task_id


# ---- ① 发布侧对账(幂等)----

def test_publish_offers_and_self_claims_once(tmp_path):
    """running 任务上板 = offer+自认领恰一次;第二轮 tick 不重复(幂等靠 materialize 判断)。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="整理周报")
    app = _app(tmp_path, reg)
    out = board.publish_local_tasks(app, now_ms=T0)
    assert out == {"offered": 1, "heartbeats": 0, "completed": 0}
    out2 = board.publish_local_tasks(app, now_ms=T0 + 60_000)     # 下一轮 tick(60s 后)
    assert out2 == {"offered": 0, "heartbeats": 0, "completed": 0}
    assert len(_events(tmp_path, K_OFFER, tid)) == 1
    assert len(_events(tmp_path, K_CLAIM, tid)) == 1
    st = materialize_tasks(MeshLogStore(tmp_path).open_log(MY_ID).entries(),
                           now=T0 + 60_000)[tid]
    assert st.claimer == MY_ID and st.payload["intent"] == "整理周报"
    assert st.payload["source_device"] == MY_ID


def test_publish_heartbeat_renews_monotonically_not_every_tick(tmp_path):
    """心跳按 lease/3 节奏:60s 不心跳,到点心跳一次续租(lease_until 单调升),同刻不重复。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="整理周报")
    app = _app(tmp_path, reg)
    board.publish_local_tasks(app, now_ms=T0)
    _entries = lambda now: materialize_tasks(  # noqa: E731
        MeshLogStore(tmp_path).open_log(MY_ID).entries(), now=now)
    lease0 = _entries(T0)[tid].lease_until
    # 60s 后(一轮 tick):没到心跳点 → 不刷
    board.publish_local_tasks(app, now_ms=T0 + 60_000)
    assert _events(tmp_path, K_HEARTBEAT, tid) == []
    # 到心跳点(lease/3)→ 恰一次心跳,lease 单调续
    at = int(T0 + HB_MS)
    board.publish_local_tasks(app, now_ms=at)
    assert len(_events(tmp_path, K_HEARTBEAT, tid)) == 1
    lease1 = _entries(at)[tid].lease_until
    assert lease1 == at + LEASE_MS and lease1 > lease0
    # 同刻再对账 → 不重复心跳(刚续过,离下个心跳点还远)
    board.publish_local_tasks(app, now_ms=at)
    assert len(_events(tmp_path, K_HEARTBEAT, tid)) == 1


def test_publish_finish_writes_done_exactly_once(tmp_path):
    """本地 finish → mesh done 恰一次(带终态);再对账不重复。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="整理周报")
    app = _app(tmp_path, reg)
    board.publish_local_tasks(app, now_ms=T0)
    reg.finish(tid, result="搞定了")
    out = board.publish_local_tasks(app, now_ms=T0 + 60_000)
    assert out["completed"] == 1
    out2 = board.publish_local_tasks(app, now_ms=T0 + 120_000)
    assert out2["completed"] == 0
    done = _events(tmp_path, K_DONE, tid)
    assert len(done) == 1
    st = materialize_tasks(MeshLogStore(tmp_path).open_log(MY_ID).entries(),
                           now=T0 + 120_000)[tid]
    assert st.status == ST_DONE and st.result["status"] == "done"
    assert "搞定了" in st.result["result"]


def test_publish_skips_never_offered_finished_task(tmp_path):
    """tick 间隙里跑完的短任务(从没上过板)→ 不补账(死账不上板;K_DONE 没 offer 也白写)。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="秒完成的小事")
    reg.finish(tid, result="ok")
    out = board.publish_local_tasks(_app(tmp_path, reg), now_ms=T0)
    assert out == {"offered": 0, "heartbeats": 0, "completed": 0}
    assert _events(tmp_path) == []


def test_publish_does_not_touch_task_claimed_by_other_device(tmp_path):
    """别台接管中(claimer≠我)→ 本机不动它的账(不心跳、终态也不由我记)。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="整理周报")
    app = _app(tmp_path, reg)
    board.publish_local_tasks(app, now_ms=T0)                     # 我 offer+自认领
    # 我的 lease 过期后,B 抢到认领(接管)
    log_b = MeshLog(PEER_ID)
    claim_at = int(T0 + LEASE_MS + 1)
    claim_task(log_b, tid, wall=claim_at)
    MeshLogStore(tmp_path).append(log_b.entries())
    # 我这边任务标了 error(比如重启中断)→ 不写 done(账是 B 的了),也不心跳
    reg.finish(tid, error="进程重启时中断")
    out = board.publish_local_tasks(app, now_ms=claim_at + 60_000)
    assert out == {"offered": 0, "heartbeats": 0, "completed": 0}
    assert _events(tmp_path, K_DONE, tid) == []


def test_publish_honest_reasons(tmp_path, monkeypatch):
    """无 registry / 无 relay 身份 → 诚实 reason,零事件,不崩。"""
    out = board.publish_local_tasks(_app(tmp_path, None), now_ms=T0)
    assert out["reason"] == "no_registry"
    monkeypatch.setattr(board, "device_fingerprint", lambda sd: {"device_id": ""})
    reg = TaskRegistry(cap=10)
    reg.start(who="小卡", intent="x")
    out2 = board.publish_local_tasks(_app(tmp_path, reg), now_ms=T0)
    assert out2["reason"] == "no_identity" and _events(tmp_path) == []


# ---- ② 接活判定 + ③ 卡形状 ----

def test_scan_pops_card_for_reclaimable_feasible_task(tmp_path):
    """别机任务 lease 过期 + 本机可行 → 恰一张 H2A 卡;形状齐(kind/summary/basis/payload/稳定id)。"""
    tid = _seed_remote_task(tmp_path)
    app = _app(tmp_path)
    now = int(T0 + LEASE_MS + 1)
    before = len(_events(tmp_path))
    cards = board.scan_takeover_proposals(app, now_ms=now)
    assert len(cards) == 1
    p = cards[0]
    assert p.kind == KIND_MESH_TAKEOVER
    assert p.options == ("ACCEPT", "DEFER", "REJECT")
    assert p.proposal_id == f"{KIND_MESH_TAKEOVER}-0-{tid}"       # 绑 task_id 的稳定 id
    assert "跑每日汇总" in p.summary                                # 人话:说的是哪件事
    assert tid in p.basis                                          # 依据:哪条任务、凭什么判中断
    assert p.payload["intent"] == "跑每日汇总"                      # run_task 骑行兼容
    assert p.payload["domain_id"] == "l0" and p.payload["role"] == ""
    assert p.payload["mesh_task_id"] == tid
    assert p.payload["source_device"] == PEER_ID
    assert p.payload["source"] == "mesh_task_board.takeover"
    # H2A:扫描本身零副作用(不 claim 不跑,绝不 auto-execute)
    assert len(_events(tmp_path)) == before


def test_scan_skips_active_lease_own_claim_and_infeasible(tmp_path):
    """lease 未过期 / claimer=本机 / needs 不可行 → 都不弹。"""
    _seed_remote_task(tmp_path, "t-alive")                        # lease 还有效
    _seed_remote_task(tmp_path, "t-mine", claimer=MY_ID)          # 本机自己的(发布侧自愈,不弹)
    _seed_remote_task(tmp_path, "t-camera", needs=("camera",))    # 本机没 camera 能力
    app = _app(tmp_path)
    assert board.scan_takeover_proposals(app, now_ms=T0 + 1_000) == []          # 全都没过期
    expired = int(T0 + LEASE_MS + 1)
    cards = board.scan_takeover_proposals(app, now_ms=expired)
    assert [p.payload["mesh_task_id"] for p in cards] == ["t-alive"]  # 只剩 B 的可行任务


def test_scan_pops_once_and_survives_restart(tmp_path):
    """弹过不重弹:同进程第二轮不弹;seen 落盘 → 换个"进程"(新 app)也不弹。"""
    _seed_remote_task(tmp_path)
    app = _app(tmp_path)
    now = int(T0 + LEASE_MS + 1)
    assert len(board.scan_takeover_proposals(app, now_ms=now)) == 1
    assert board.scan_takeover_proposals(app, now_ms=now + 60_000) == []
    app2 = _app(tmp_path)                                          # 重启:app.state 全新
    assert board.scan_takeover_proposals(app2, now_ms=now + 120_000) == []
    assert (tmp_path / board.SEEN_FILE).exists()


def test_scan_refuses_intent_less_task(tmp_path):
    """intent 空 → 不弹(宁空勿弹:没内容的卡人没法拍)。"""
    _seed_remote_task(tmp_path, "t-blank", intent="")
    assert board.scan_takeover_proposals(_app(tmp_path), now_ms=int(T0 + LEASE_MS + 1)) == []


# ---- ④ ACCEPT 路径(mock 重跑)----

def _accepted(tmp_path, monkeypatch, *, run_ok=True, run_detail="重跑完成"):
    tid = _seed_remote_task(tmp_path)
    app = _app(tmp_path)
    now = int(T0 + LEASE_MS + 1)
    [prop] = board.scan_takeover_proposals(app, now_ms=now)
    ran = []

    def _fake_run_task_handler(a):
        def h(p):
            ran.append(p)
            return run_ok, run_detail
        return h
    monkeypatch.setattr("karvyloop.console.proposal_handlers._run_task_handler",
                        _fake_run_task_handler)
    ok, detail = board.make_mesh_takeover_handler(app)(prop)
    return tid, prop, ran, ok, detail


def test_accept_claims_runs_and_completes(tmp_path, monkeypatch):
    """ACCEPT = claim 上账 → 骑 run_task 本地从头重跑 → complete 上账(结果带终态)。"""
    tid, prop, ran, ok, detail = _accepted(tmp_path, monkeypatch)
    assert ok and len(ran) == 1 and ran[0] is prop                # 真骑了 run_task 语义
    assert "重跑完成" in detail
    claims = _events(tmp_path, K_CLAIM, tid)
    assert MY_ID in [e.device_id for e in claims]                  # 本机 claim 上了账
    done = _events(tmp_path, K_DONE, tid)
    assert len(done) == 1 and done[0].device_id == MY_ID
    st = materialize_tasks(MeshLogStore(tmp_path).open_log(MY_ID).entries(),
                           now=int(T0 + LEASE_MS + 10_000))[tid]
    assert st.status == ST_DONE
    assert st.result["ok"] is True and st.result["device"] == MY_ID


def test_accept_failure_still_records_terminal_state(tmp_path, monkeypatch):
    """重跑失败也如实 complete(ok=False):人已看到结果,别让别台围着它继续弹卡。"""
    tid, _prop, _ran, ok, detail = _accepted(tmp_path, monkeypatch,
                                             run_ok=False, run_detail="重跑失败: boom")
    assert not ok and "boom" in detail
    st = materialize_tasks(MeshLogStore(tmp_path).open_log(MY_ID).entries(),
                           now=int(T0 + LEASE_MS + 10_000))[tid]
    assert st.status == ST_DONE and st.result["ok"] is False


# ---- ⑤ REJECT:什么都不做 ----

def test_reject_leaves_task_reclaimable_and_never_runs(tmp_path):
    """REJECT → handler 不被调、日志不动、任务留 reclaimable(别的设备还能接)。"""
    tid = _seed_remote_task(tmp_path)
    app = _app(tmp_path)
    now = int(T0 + LEASE_MS + 1)
    [prop] = board.scan_takeover_proposals(app, now_ms=now)
    registry = PendingProposalRegistry()
    pid = registry.register(prop)
    called = []
    res = registry.decide(pid, "REJECT",
                          handlers={KIND_MESH_TAKEOVER: lambda p: (called.append(1) or (True, "x"))})
    assert res.detail == "rejected" and called == []
    evs = _events(tmp_path)
    assert {e.kind for e in evs} == {K_OFFER, K_CLAIM} and len(evs) == 2   # 只有 B 的原始两条
    st = materialize_tasks(MeshLogStore(tmp_path).open_log(MY_ID).entries(), now=now)[tid]
    assert st.status == ST_RECLAIMABLE                             # 活不丢:别的设备仍可接


def test_takeover_kind_is_hard_excluded_from_silence():
    """结构性保证「绝不 auto-execute」:mesh_takeover 在挣来的静音高危硬排除表里 ——
    无论桶战绩多好都永不自动兑现(设备"判死"是 lease 推断不是事实,自动接=双跑风险)。"""
    from karvyloop.karvy.silence import HIGH_RISK_KINDS
    assert KIND_MESH_TAKEOVER in HIGH_RISK_KINDS


# ---- ⑥ mesh_tick 接线 ----

def test_mesh_tick_runs_publish_even_without_peers(tmp_path, monkeypatch):
    """发布侧每轮都跑(单机也写本地账);无对端 → 不做接活扫描(没同步,新不了鲜)。"""
    import karvyloop.console.mesh_tick as mt
    pub, scans = [], []
    monkeypatch.setattr(mt, "publish_local_tasks",
                        lambda app: (pub.append(1) or {"offered": 0, "heartbeats": 0, "completed": 0}))
    monkeypatch.setattr(mt, "scan_takeover_proposals", lambda app: (scans.append(1) or []))
    out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    assert pub == [1] and scans == []                              # 发布跑了;无对端不扫
    assert out["tasks"] == {"offered": 0, "heartbeats": 0, "completed": 0}


def test_mesh_tick_broadcasts_takeover_cards_after_sync(tmp_path, monkeypatch):
    """有对端:同步后跑接活扫描,卡走 broadcast_proposal(进待决表/推 WS,H2A 由人拍)。"""
    import karvyloop.console.mesh_tick as mt
    from karvyloop.mesh.registry import DeviceRecord, DeviceRegistry
    DeviceRegistry(tmp_path).register(DeviceRecord(
        device_id=PEER_ID, room="m" + "a" * 21, relay_url="wss://peer.relay"))

    async def _fake_sync(*a, **k):
        return {"pulled": 0, "pushed": 0}
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _fake_sync)
    monkeypatch.setattr(mt, "device_fingerprint", lambda sd: {"device_id": MY_ID})
    monkeypatch.setattr(mt, "publish_local_tasks",
                        lambda app: {"offered": 0, "heartbeats": 0, "completed": 0})
    fake_card = SimpleNamespace(proposal_id="mesh_takeover-0-t1", kind=KIND_MESH_TAKEOVER)
    monkeypatch.setattr(mt, "scan_takeover_proposals", lambda app: [fake_card])
    sent = []

    async def _fake_broadcast(app, prop, **k):
        sent.append(prop)
        return 1
    monkeypatch.setattr("karvyloop.console.proposals.broadcast_proposal", _fake_broadcast)
    out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    assert out["synced"] == 1
    assert sent == [fake_card]                                     # 卡真广播了(人拍板)
    assert out["tasks"]["takeover_cards"] == 1
