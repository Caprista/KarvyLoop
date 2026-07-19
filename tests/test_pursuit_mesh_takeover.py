"""test_pursuit_mesh_takeover — Pursuit 跨设备接管(docs/88 第三刀 #3)。

单机模拟同主人两台设备(两个 pursuit_store + 两个 mesh 状态目录,盲 relay 同步用事件互拷模拟):

语义锁:
① 注册:committed Pursuit 上 mesh 板(offer 带 pursuit_checkpoint + 自认领),幂等不重复;
   非 committed(待承诺/挂起/终态)不上板;心跳按 lease/3 续租;checkpoint 落后才追加 offer 刷新
   (K_OFFER 只覆写 payload,不动 claim/lease)。
② 接管卡:A lease 过期 → B 弹**现有 KIND_MESH_TAKEOVER 卡**(statement + 已推进几轮 + 完成判据
   人话);lease 未过期不弹;扫描零副作用(H2A 绝不 auto-execute)。
③ ACCEPT = checkpoint 收编:B 的 store 拿到这条(advances=3 带过来**非归零**、gate/statement 对、
   committed、last_advance_ts=0 下一 tick 就推),**不骑 run_task 从头重跑**、不写 K_DONE。
④ 单 owner 不双跑:B 持租时 A 对账标 transferred_to 站开,A 的 tick 不推进(A 回来不抢);
   B 的 tick 正常推进。收编时本地日志已知别台先赢 claim → 不收编。
⑤ checkpoint 语义:advances 取 max(checkpoint, 本机旧账)永不调低(烧钱地板不因换设备绕开);
   B 追完 → K_DONE 随同步回 A → A 折回本地终态。
⑥ 纪元防重弹:同一 pursuit 换 owner 又中断(claim_epoch+1)→ 可再弹;prune 认识纪元键。
测试身份一律 FAKE;不碰真 ~/.karvyloop。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import karvyloop.console.mesh_task_board as board  # noqa: E402
import karvyloop.console.pursuit_tick as pt  # noqa: E402
from karvyloop.cognition.pursuit import PursuitManager  # noqa: E402
from karvyloop.cognition.pursuit_store import PursuitRecord, PursuitStore, new_pursuit_id  # noqa: E402
from karvyloop.karvy.proposal_registry import KIND_MESH_TAKEOVER  # noqa: E402
from karvyloop.mesh.store import MeshLogStore  # noqa: E402
from karvyloop.mesh.tasks import (  # noqa: E402
    K_CLAIM, K_DONE, K_HEARTBEAT, K_OFFER, ST_CLAIMED, ST_DONE, materialize_tasks,
)
from karvyloop.schemas import Pursuit  # noqa: E402

A_ID = "dev-A-FAKE"
B_ID = "dev-B-FAKE"
LEASE_MS = board.TASK_LEASE_S * 1000.0
HB_MS = board.TASK_HEARTBEAT_EVERY_S * 1000.0
STATEMENT = "重构直到测试全绿"
GATE = {"type": "file_exists", "path": "/nonexistent/for-this-test"}


@pytest.fixture(autouse=True)
def _identity_by_dir(monkeypatch, tmp_path):
    """设备身份按 mesh 状态目录判(A/B 两台共用打桩函数;不碰真 relay 密钥)。"""
    def fp(sd):
        s = str(sd or "")
        dev = A_ID if s.endswith("devA") else (B_ID if s.endswith("devB") else "")
        return {"device_id": dev, "capabilities": ["coding", "shell", "big-task"]}
    monkeypatch.setattr(board, "device_fingerprint", fp)


def _mk_device(tmp_path, name):
    """一台设备 = 独立 mesh 状态目录 + 独立 pursuit_store 的最小 app。"""
    sd = tmp_path / name
    sd.mkdir(parents=True, exist_ok=True)
    store = PursuitStore(sd / "pursuits.json")
    app = SimpleNamespace(state=SimpleNamespace(
        mesh_state_dir=sd, pursuit_store=store, pursuit_manager=PursuitManager(memory=None),
        task_registry=None, proposal_registry=None, ws_clients=set(),
        relay_url="wss://relay.FAKE-DO-NOT-DIAL",     # mesh 门:挂了 relay 才对账
        main_loop=None, runtime_kwargs={}, trace=None, memory=None,
        pursuit_advance_interval_s=0.0,
    ))
    return app, sd, store


def _committed_rec(*, advances=3, fails=1, note="推进一拍:已跑一轮") -> PursuitRecord:
    p = Pursuit(id=new_pursuit_id("atom"), level="atom", statement=STATEMENT,
                commitment_condition="", revision_triggers=[], verify_gate=dict(GATE),
                status="committed")
    rec = PursuitRecord(p, title="T", owner="karvy", domain_id="l0")
    rec.advances = advances
    rec.consecutive_failures = fails
    rec.progress_note = note
    return rec


def _sync(src_sd, dst_sd):
    """模拟一轮盲 relay 同步:把 src 有而 dst 没有的事件搬过去(按 event_id 幂等)。"""
    have = {e.event_id for e in MeshLogStore(dst_sd).load_events()}
    new = [e for e in MeshLogStore(src_sd).load_events() if e.event_id not in have]
    MeshLogStore(dst_sd).append(new)


def _events(sd, kind=None, task_id=None):
    evs = MeshLogStore(sd).load_events()
    if kind is not None:
        evs = [e for e in evs if e.kind == kind]
    if task_id is not None:
        evs = [e for e in evs if (e.payload or {}).get("task_id") == task_id]
    return evs


def _board(sd, my_id, now):
    return materialize_tasks(MeshLogStore(sd).open_log(my_id).entries(), now=now)


# 起始墙钟摆在"一个 lease + 2 分钟"之前:ACCEPT/收编内部用**真墙钟**写 claim,
# 种下的 A 租约必须在真 now 已过期,B 的 claim 才裁得进(与生产时序同构)。
NOW0 = int(time.time() * 1000) - int(LEASE_MS) - 120_000


# ---------------------------------------------------------------- ① 注册(发布侧)

def test_committed_pursuit_registers_on_board_idempotently(tmp_path):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    rec = _committed_rec(advances=3)
    storeA.put(rec)
    out = board.publish_pursuit_tasks(appA, now_ms=NOW0)
    assert out["offered"] == 1
    assert len(_events(sdA, K_OFFER, rec.id)) == 1
    assert len(_events(sdA, K_CLAIM, rec.id)) == 1                  # 自认领:本机在追它
    st = _board(sdA, A_ID, NOW0)[rec.id]
    assert st.status == ST_CLAIMED and st.claimer == A_ID
    cp = st.payload["pursuit_checkpoint"]
    assert cp["advances"] == 3 and cp["pursuit"]["statement"] == STATEMENT
    assert cp["pursuit"]["verify_gate"] == GATE
    assert st.payload["intent"] == STATEMENT and st.payload["pursuit_id"] == rec.id
    # 幂等:再对账不重复 offer/claim,也不多余刷新(checkpoint 没漂)
    out2 = board.publish_pursuit_tasks(appA, now_ms=NOW0 + 60_000)
    assert out2["offered"] == 0 and out2["refreshed"] == 0
    assert len(_events(sdA, K_OFFER, rec.id)) == 1
    # 板面快照能看见"这台设备在追这个目标"(pursuit_id 加性列)
    snap = board.board_snapshot(sdA, now_ms=NOW0 + 60_000)
    [row] = snap["tasks_by_device"][A_ID]
    assert row["pursuit_id"] == rec.id and row["intent"] == STATEMENT


def test_non_committed_pursuits_stay_off_board(tmp_path):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    active = _committed_rec()
    active.pursuit = active.pursuit.model_copy(update={"status": "active"})
    suspended = _committed_rec()
    suspended.suspended = True
    done = _committed_rec()
    done.pursuit = done.pursuit.model_copy(update={"status": "done"})
    for r in (active, suspended, done):
        storeA.put(r)
    out = board.publish_pursuit_tasks(appA, now_ms=NOW0)
    assert out["offered"] == 0 and _events(sdA) == []               # 死账/待承诺/挂起不上板


def test_heartbeat_cadence_and_checkpoint_refresh(tmp_path):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    rec = _committed_rec(advances=3)
    storeA.put(rec)
    board.publish_pursuit_tasks(appA, now_ms=NOW0)
    # 60s 后:没到心跳点 → 不刷
    board.publish_pursuit_tasks(appA, now_ms=NOW0 + 60_000)
    assert _events(sdA, K_HEARTBEAT, rec.id) == []
    # 到 lease/3 → 恰一次心跳续租
    at = int(NOW0 + HB_MS)
    out = board.publish_pursuit_tasks(appA, now_ms=at)
    assert out["heartbeats"] == 1
    assert _board(sdA, A_ID, at)[rec.id].lease_until == at + LEASE_MS
    # 推进了一拍(advances 3→4)→ checkpoint 漂了 → 追加 offer 刷 payload,claim/租约不受扰
    rec.advances = 4
    storeA.put(rec)
    out2 = board.publish_pursuit_tasks(appA, now_ms=at + 1_000)
    assert out2["refreshed"] == 1
    st = _board(sdA, A_ID, at + 1_000)[rec.id]
    assert st.payload["pursuit_checkpoint"]["advances"] == 4        # 板上状态跟上了
    assert st.claimer == A_ID and st.claim_epoch == 1               # 刷新不动归属/纪元
    assert st.lease_until == at + LEASE_MS                          # 也不动租约


# ---------------------------------------------------------------- ② 接管卡(H2A)

def _a_publishes_b_sees(tmp_path, *, advances=3):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    appB, sdB, storeB = _mk_device(tmp_path, "devB")
    rec = _committed_rec(advances=advances)
    storeA.put(rec)
    board.publish_pursuit_tasks(appA, now_ms=NOW0)
    _sync(sdA, sdB)
    return appA, sdA, storeA, appB, sdB, storeB, rec


def test_expired_lease_pops_pursuit_takeover_card(tmp_path):
    _appA, sdA, _sA, appB, sdB, _sB, rec = _a_publishes_b_sees(tmp_path)
    # lease 未过期 → 不弹(A 还活着)
    assert board.scan_takeover_proposals(appB, now_ms=NOW0 + 1_000) == []
    expired = int(NOW0 + LEASE_MS + 1)
    before = len(_events(sdB))
    [prop] = board.scan_takeover_proposals(appB, now_ms=expired)
    assert prop.kind == KIND_MESH_TAKEOVER                          # 复用现有接管卡 kind
    assert prop.options == ("ACCEPT", "DEFER", "REJECT")
    assert prop.proposal_id == f"{KIND_MESH_TAKEOVER}-0-{rec.id}-e1"   # 绑 (task, 纪元)
    # 展示三件套:statement + 已推进几轮 + 完成判据人话
    assert STATEMENT in prop.summary
    assert "3 rounds" in prop.summary
    assert GATE["path"] in prop.summary                             # gate 人话(file_exists)
    assert "round 3" in prop.basis and rec.id in prop.basis
    assert prop.payload["source"] == board.PURSUIT_TAKEOVER_SOURCE
    assert prop.payload["pursuit_id"] == rec.id
    assert prop.payload["pursuit_checkpoint"]["advances"] == 3
    assert len(_events(sdB)) == before                              # 扫描零副作用(绝不 auto)
    # 弹过不重弹(同一纪元)
    assert board.scan_takeover_proposals(appB, now_ms=expired + 60_000) == []


# ---------------------------------------------------------------- ③ ACCEPT = 收编续推

def test_accept_adopts_checkpoint_not_rerun(tmp_path, monkeypatch):
    _appA, sdA, _sA, appB, sdB, storeB, rec = _a_publishes_b_sees(tmp_path)
    expired = int(NOW0 + LEASE_MS + 1)
    [prop] = board.scan_takeover_proposals(appB, now_ms=expired)
    rode_run_task = []
    monkeypatch.setattr("karvyloop.console.proposal_handlers._run_task_handler",
                        lambda a: (lambda p: (rode_run_task.append(p) or (True, "x"))))
    ok, detail = board.make_mesh_takeover_handler(appB)(prop)
    assert ok and rode_run_task == []                               # 不骑 run_task 从头重跑
    assert "round 3" in detail
    got = storeB.get(rec.id)
    assert got is not None
    assert got.advances == 3                                        # advances 带过来,非归零
    assert got.consecutive_failures == 1                            # 连败计数也带(地板不重置)
    assert got.pursuit.statement == STATEMENT
    assert got.pursuit.verify_gate == GATE
    assert got.pursuit.status == "committed" and not got.suspended
    assert got.last_advance_ts == 0.0                               # 下一 tick 立刻接着推
    assert got.transferred_to == ""
    assert got.progress_note == rec.progress_note                   # 进度备注随 checkpoint 来
    assert got.last_task_ids == []                                  # 设备本地 task 指针不跨机
    # mesh 账:B claim 了(lease 归 B),但**没有** done(长命活,追完才 complete)
    assert [e.device_id for e in _events(sdB, K_CLAIM, rec.id)][-1] == B_ID
    assert _events(sdB, K_DONE, rec.id) == []
    st = _board(sdB, B_ID, int(time.time() * 1000))[rec.id]
    assert st.status == ST_CLAIMED and st.claimer == B_ID and st.claim_epoch == 2


def test_adopt_never_lowers_advances_below_local(tmp_path, monkeypatch):
    """本机旧账 advances=5 > checkpoint 的 3 → 收编取 max(烧钱地板永不因换设备调低)。"""
    _appA, _sdA, _sA, appB, _sdB, storeB, rec = _a_publishes_b_sees(tmp_path)
    old = _committed_rec(advances=5, fails=2)
    old.pursuit = rec.pursuit.model_copy(update={"status": "committed"})   # 同一条 pursuit id
    storeB.put(old)
    [prop] = board.scan_takeover_proposals(appB, now_ms=int(NOW0 + LEASE_MS + 1))
    ok, _ = board.make_mesh_takeover_handler(appB)(prop)
    assert ok
    got = storeB.get(rec.id)
    assert got.advances == 5 and got.consecutive_failures == 2      # max(cp, 本机旧账)


def test_adopt_refuses_bad_checkpoint_and_missing_store(tmp_path):
    _appA, _sdA, _sA, appB, _sdB, _sB, rec = _a_publishes_b_sees(tmp_path)
    [prop] = board.scan_takeover_proposals(appB, now_ms=int(NOW0 + LEASE_MS + 1))
    # 坏 checkpoint(extra=forbid)→ 诚实拒绝,不投毒本机库
    bad = dict(prop.payload)
    bad["pursuit_checkpoint"] = {"pursuit": {"id": "x", "evil_key": 1}}
    ok, detail = board.make_mesh_takeover_handler(appB)(
        SimpleNamespace(payload=bad))
    assert not ok and appB.state.pursuit_store.all() == []
    # 无 pursuit_store → 诚实拒绝
    appB.state.pursuit_store = None
    ok2, _ = board.make_mesh_takeover_handler(appB)(prop)
    assert not ok2


def test_adopt_stands_down_when_another_device_won_claim(tmp_path):
    """本地日志已知别台(C)先赢了 claim(HLC 更早)→ 不收编不双跑(单 owner)。"""
    _appA, sdA, _sA, appB, sdB, storeB, rec = _a_publishes_b_sees(tmp_path)
    expired = int(NOW0 + LEASE_MS + 1)
    [prop] = board.scan_takeover_proposals(appB, now_ms=expired)
    # C 在 A 租约过期后、B 拍板之前就 claim 了(wall 比 B 即将写的真 now 早 60s),且已同步进 B
    from karvyloop.mesh.synclog import MeshLog
    from karvyloop.mesh.tasks import claim_task
    logC = MeshLog("dev-C-FAKE")
    claim_task(logC, rec.id, wall=int(time.time() * 1000) - 60_000)
    MeshLogStore(sdB).append(logC.entries())
    ok, detail = board.make_mesh_takeover_handler(appB)(prop)
    assert not ok                                                   # 裁给了 C → B 站开
    assert storeB.get(rec.id) is None                               # 没收编 = 不双跑
    st = _board(sdB, B_ID, int(time.time() * 1000))[rec.id]
    assert st.claimer == "dev-C-FAKE"                               # lease 归 C,账清晰


# ---------------------------------------------------------------- ④ 单 owner:A 回来不抢

def _b_took_over(tmp_path, monkeypatch):
    appA, sdA, storeA, appB, sdB, storeB, rec = _a_publishes_b_sees(tmp_path)
    [prop] = board.scan_takeover_proposals(appB, now_ms=int(NOW0 + LEASE_MS + 1))
    ok, _ = board.make_mesh_takeover_handler(appB)(prop)
    assert ok
    _sync(sdB, sdA)                                                 # A 回线,拉到 B 的 claim
    return appA, sdA, storeA, appB, sdB, storeB, rec


def test_owner_back_stands_down_and_does_not_advance(tmp_path, monkeypatch):
    appA, sdA, storeA, appB, sdB, storeB, rec = _b_took_over(tmp_path, monkeypatch)
    advanced = []
    monkeypatch.setattr(pt, "_advance_sync", lambda app, r: (advanced.append((app, r.id)), None)[1])
    # A 的 tick:mesh 对账发现 claimer=B → 标 transferred 站开,不推进(不抢、不双跑)
    resA = asyncio.run(pt.pursuit_tick(appA, now=time.time()))
    assert storeA.get(rec.id).transferred_to == B_ID
    assert resA["mesh"]["transferred"] == 1
    assert advanced == []                                           # A 一步都没推进
    # A 的发布侧也不动 B 的账(不心跳不 claim):tick 里已对账,B 的租约 lease_until 归 B
    st = _board(sdA, A_ID, int(time.time() * 1000))[rec.id]
    assert st.claimer == B_ID
    # B 的 tick 正常接着推(单 owner 是 B)
    resB = asyncio.run(pt.pursuit_tick(appB, now=time.time()))
    assert resB["advanced"] == 1
    assert [aid for (aapp, aid) in advanced] == [rec.id]            # 只有 B 推进了这条


def test_transfer_back_when_lease_returns_to_me(tmp_path, monkeypatch):
    """账回到本机名下(比如 B 放弃后本机重新 claim)→ transferred_to 清掉,恢复推进资格。"""
    appA, sdA, storeA, appB, sdB, storeB, rec = _b_took_over(tmp_path, monkeypatch)
    board.publish_pursuit_tasks(appA, now_ms=int(time.time() * 1000))
    assert storeA.get(rec.id).transferred_to == B_ID
    # B 的租约过期后 A 又(经接管卡 ACCEPT)claim 回来 —— 这里直接写 A 的 claim 模拟裁决结果
    from karvyloop.mesh.tasks import claim_task
    stB = _board(sdA, A_ID, int(time.time() * 1000))[rec.id]
    back_at = int(stB.lease_until + 1)
    store = MeshLogStore(sdA)
    log = store.open_log(A_ID)
    store.append([claim_task(log, rec.id, wall=back_at)])
    out = board.publish_pursuit_tasks(appA, now_ms=back_at + 1_000)
    assert storeA.get(rec.id).transferred_to == ""                  # 收敛:账回来了


# ---------------------------------------------------------------- ⑤ 完成回流

def test_remote_done_folds_back_to_original_owner(tmp_path, monkeypatch):
    appA, sdA, storeA, appB, sdB, storeB, rec = _b_took_over(tmp_path, monkeypatch)
    # A 先对账过一轮(已标 transferred)
    board.publish_pursuit_tasks(appA, now_ms=int(time.time() * 1000))
    # B 把目标追完 → B 对账写 K_DONE
    got = storeB.get(rec.id)
    got.pursuit = got.pursuit.model_copy(update={"status": "done"})
    storeB.put(got)
    outB = board.publish_pursuit_tasks(appB, now_ms=int(time.time() * 1000) + 1_000)
    assert outB["completed"] == 1
    assert [e.device_id for e in _events(sdB, K_DONE, rec.id)] == [B_ID]
    _sync(sdB, sdA)
    # A 对账:远端完成折回本地 → A 不再追,状态收敛为 done
    outA = board.publish_pursuit_tasks(appA, now_ms=int(time.time() * 1000) + 2_000)
    assert outA["folded_done"] == 1
    recA = storeA.get(rec.id)
    assert recA.status == "done" and recA.transferred_to == ""
    assert B_ID[:8] in recA.progress_note                           # 人话:在哪台完成的


# ---------------------------------------------------------------- ⑥ 纪元防重弹 + prune

def test_new_claim_epoch_rearms_takeover_card(tmp_path, monkeypatch):
    """B 接管(纪元2)后又中断 → A 可弹(纪元键不同);同纪元不重弹的旧语义保留。"""
    appA, sdA, storeA, appB, sdB, storeB, rec = _b_took_over(tmp_path, monkeypatch)
    st = _board(sdA, A_ID, int(time.time() * 1000))[rec.id]
    assert st.claim_epoch == 2                                      # A offer+claim=1,B 接管=2
    expired2 = int(st.lease_until + 1)
    [prop] = board.scan_takeover_proposals(appA, now_ms=expired2)   # B 掉线 → A 这边弹卡
    assert prop.proposal_id == f"{KIND_MESH_TAKEOVER}-0-{rec.id}-e2"
    assert prop.payload["pursuit_checkpoint"]["advances"] == 3
    assert board.scan_takeover_proposals(appA, now_ms=expired2 + 60_000) == []   # 同纪元不重弹


def test_prune_seen_understands_epoch_keys(tmp_path):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    rec = _committed_rec()
    storeA.put(rec)
    board.publish_pursuit_tasks(appA, now_ms=NOW0)
    # 手工把纪元键塞进 seen 台账(模拟弹过卡)
    appA.state._mesh_takeover_seen = [f"{rec.id}#e1", "unrelated-task"]
    # 追完 → done 上账
    rec.pursuit = rec.pursuit.model_copy(update={"status": "done"})
    storeA.put(rec)
    board.publish_pursuit_tasks(appA, now_ms=NOW0 + 1_000)
    pruned = board.prune_seen_done(appA, now_ms=NOW0 + 2_000)
    assert pruned == 1                                              # 纪元键剥后缀后按 done 清
    assert appA.state._mesh_takeover_seen == ["unrelated-task"]


# ---------------------------------------------------------------- tick 守门

def test_tick_skips_mesh_without_relay(tmp_path, monkeypatch):
    """没挂 relay(单机)→ tick 不做 mesh 对账(跨网同步是远程访问同一决定的延伸,同一道门)。"""
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    appA.state.relay_url = ""
    storeA.put(_committed_rec())
    monkeypatch.setattr(pt, "_advance_sync", lambda app, r: None)
    res = asyncio.run(pt.pursuit_tick(appA, now=time.time()))
    assert "mesh" not in res
    assert _events(sdA) == []                                       # 一条 mesh 账都不写


def test_tick_publishes_committed_pursuit_when_relay_on(tmp_path, monkeypatch):
    appA, sdA, storeA = _mk_device(tmp_path, "devA")
    rec = _committed_rec(advances=3)
    storeA.put(rec)
    monkeypatch.setattr(pt, "_advance_sync", lambda app, r: None)
    res = asyncio.run(pt.pursuit_tick(appA, now=time.time()))
    assert res["mesh"]["offered"] == 1                              # tick 顺手把它注册上板
    st = _board(sdA, A_ID, int(time.time() * 1000))[rec.id]
    assert st.claimer == A_ID
    assert st.payload["pursuit_checkpoint"]["advances"] == 3
