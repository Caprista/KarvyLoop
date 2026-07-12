"""mesh/sync_client — 设备 mesh 同步的**客户端一半**:经 relay 调对端 /api/mesh/* 交换 delta。

闭合两设备同步环:本设备(持 MeshLog)用已建的 relay `remote` 客户端(E2E 经 relay)连到对端设备的
console,① GET 对端 frontier ② 算我对它的 delta,连我 frontier 一起 POST /mesh/sync ③ 对端合并 +
回它对我的 delta ④ 我合并 + 持久化。一个来回 = 双向 gossip 收敛(合的落地)。

**同主人**:同步的是"我的认知/任务"在我设备间流动,E2E 经 relay(信使不拆信),不出我边界。
"""
from __future__ import annotations

import json
import time
from typing import Optional

from karvyloop.mesh.store import MeshLogStore
from karvyloop.mesh.synclog import HLC, MeshEvent


async def mesh_sync_with_peer(relay_url: str, peer_room: str, *, fingerprint: str,
                              my_device_id: str, code: Optional[str] = None,
                              state_dir=None) -> dict:
    """跟一台对端设备同步一次 MeshLog(经 relay)。返回 {pulled, pushed}。

    my_device_id = 本设备身份(它的 MeshLog device_id);peer_room/fingerprint/code = 对端 relay-pair 信息。
    """
    from karvyloop.relay.remote import open_remote_session

    store = MeshLogStore(state_dir)
    log = store.open_log(my_device_id)

    ws, sess = await open_remote_session(relay_url, peer_room, fingerprint=fingerprint,
                                         code=code, state_dir=state_dir)
    try:
        # ① 拉对端 frontier
        r = await sess.request("GET", "/api/mesh/frontier")
        if r.get("status") != 200:
            raise RuntimeError(f"peer frontier failed: {r.get('status')} {r.get('error')}")
        peer_fr_raw = (json.loads(r["body"].decode("utf-8")) or {}).get("frontier", {})
        peer_fr = {str(d): HLC.parse(str(v)) for d, v in peer_fr_raw.items()}

        # ② 算我对它的 delta + 我的 frontier,POST /mesh/sync
        my_delta = [e.to_dict() for e in log.delta(peer_fr)]
        my_fr = {d: str(h) for d, h in log.frontier().items()}
        body = json.dumps({"frontier": my_fr, "events": my_delta}).encode("utf-8")
        r2 = await sess.request("POST", "/api/mesh/sync",
                                headers={"content-type": "application/json"}, body=body)
        if r2.get("status") != 200:
            raise RuntimeError(f"peer sync failed: {r2.get('status')} {r2.get('error')}")
        resp = json.loads(r2["body"].decode("utf-8")) or {}

        # ③ 合并对端回的 delta + 持久化
        peer_delta = [MeshEvent.from_dict(e) for e in (resp.get("events") or [])]
        pulled = log.merge(peer_delta, wall=int(time.time() * 1000))
        if pulled:
            try:
                store.persist_new(log)
            except Exception:  # noqa: BLE001 — 持久化失败不吞掉同步结果
                pass
        return {"pulled": pulled, "pushed": int(resp.get("merged", 0))}
    finally:
        await sess.close()


def cmd_mesh_sync(relay_url: str, peer_room: str, fingerprint: str,
                  code: Optional[str] = None, state_dir=None) -> int:
    """`karvyloop mesh-sync --relay … --peer-room … --fingerprint …`:跟一台我的设备同步一次认知/任务。"""
    import asyncio
    import sys

    from karvyloop.mesh.fingerprint import device_fingerprint
    from karvyloop.relay import e2e
    my_id = device_fingerprint(state_dir).get("device_id") or ""
    if not my_id:
        sys.stderr.write("this device has no relay identity — run `karvyloop relay-pair` first\n")
        return 1
    try:
        out = asyncio.run(mesh_sync_with_peer(relay_url, peer_room, fingerprint=fingerprint,
                                              my_device_id=my_id, code=code, state_dir=state_dir))
    except e2e.RelayCryptoUnavailable as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"mesh sync failed: {type(exc).__name__}: {exc}\n")
        return 1
    print(f"synced with peer: pulled {out['pulled']} event(s), pushed {out['pushed']} event(s).")
    return 0


__all__ = ["mesh_sync_with_peer", "cmd_mesh_sync"]
