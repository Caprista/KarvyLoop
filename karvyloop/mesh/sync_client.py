"""mesh/sync_client — 设备 mesh 同步的**客户端一半**:经 relay 调对端 /api/mesh/* 交换 delta。

闭合两设备同步环:本设备(持 MeshLog)用已建的 relay `remote` 客户端(E2E 经 relay)连到对端设备的
console,① GET 对端 frontier ② 算我对它的 delta,连我 frontier 一起 POST /mesh/sync ③ 对端合并 +
回它对我的 delta ④ 我合并 + 持久化。一个来回 = 双向 gossip 收敛(合的落地)。

**同主人**:同步的是"我的认知/任务"在我设备间流动,E2E 经 relay(信使不拆信),不出我边界。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from karvyloop.mesh.store import MeshLogStore
from karvyloop.mesh.synclog import HLC, MeshEvent

logger = logging.getLogger(__name__)


async def mesh_sync_with_peer(relay_url: str, peer_room: str, *, fingerprint: str,
                              my_device_id: str, code: Optional[str] = None,
                              state_dir=None, my_relay_url: Optional[str] = None) -> dict:
    """跟一台对端设备同步一次 MeshLog(经 relay)。返回 {pulled, pushed}。

    my_device_id = 本设备身份(它的 MeshLog device_id);peer_room/fingerprint/code = 对端 relay-pair 信息。
    顺手互换能力广告(docs/74 花名册双录):POST 体带我的 advert(对端把我入册),
    frontier 响应里对端的 advert 落进我的花名册 + mark_seen —— 一次同步双方花名册都齐。
    my_relay_url = 我自己的 relay(广告"怎么连回我"用);None → 沿用拨出用的 relay_url
    (同主人设备共用一台 relay 的常态)。

    **身份**:mesh 拨出用本机**设备身份**(relay_key,= device_id 那把指纹),不是接入端
    remote_key —— 对端授权表里记下的就是本设备 mesh 身份,双方免码互拨/回配才对得上号。

    **同主人一步互配(docs/74 对等语义)**:首配(code 非空)整个来回成功后,把对端 console
    身份回写进**我方**已配对表(scope full)—— 它反向拨我不再要第二枚码。三重门全在才写:
    ① 我方主动用一次性码发起(code 非空;复连 code=None 不写)② 指纹 pin 验证通过
    (open_remote_session 里 client_complete,不过就抛了)③ full scope 已证 —— read 分享码
    在对端咽喉就把 POST /api/mesh/sync 403 掉(scope_read_only),整个同步失败,走不到回写;
    **绝不**因收到 advert / 被动被配就信任别人(advert 只进花名册,不进授权表)。
    """
    from karvyloop.relay.remote import open_remote_session

    store = MeshLogStore(state_dir)
    log = store.open_log(my_device_id)
    try:                                  # 我的能力广告(无 relay 身份 → device_id 空,对端会丢弃)
        from karvyloop.mesh.fingerprint import device_advert
        my_advert = device_advert(
            state_dir, relay_url=(my_relay_url if my_relay_url is not None else relay_url))
    except Exception:  # noqa: BLE001 — 广告失败不挡同步
        my_advert = {}

    ws, sess = await open_remote_session(relay_url, peer_room, fingerprint=fingerprint,
                                         code=code, state_dir=state_dir,
                                         use_device_identity=True)
    try:
        # ① 拉对端 frontier(+ 它的能力广告)
        r = await sess.request("GET", "/api/mesh/frontier")
        if r.get("status") != 200:
            raise RuntimeError(f"peer frontier failed: {r.get('status')} {r.get('error')}")
        peer_body = json.loads(r["body"].decode("utf-8")) or {}
        peer_fr_raw = peer_body.get("frontier", {})
        peer_fr = {str(d): HLC.parse(str(v)) for d, v in peer_fr_raw.items()}

        # ② 算我对它的 delta + 我的 frontier + 我的 advert,POST /mesh/sync
        my_delta = [e.to_dict() for e in log.delta(peer_fr)]
        my_fr = {d: str(h) for d, h in log.frontier().items()}
        body = json.dumps({"frontier": my_fr, "events": my_delta,
                           "advert": my_advert}).encode("utf-8")
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
        # ④ 对端入我花名册(双录另一半)。register_peer 宁空勿毒(旧对端无 advert → 丢弃);
        #    mark_seen 兜底刷新鲜度(探活:成功连到 = 它活着)。失败不吞同步结果。
        try:
            from karvyloop.mesh.registry import DeviceRegistry
            reg = DeviceRegistry(state_dir)
            reg.register_peer(peer_body.get("advert") or {})
            peer_id = str(peer_body.get("device_id") or "")
            if peer_id:
                reg.mark_seen(peer_id)
        except Exception:  # noqa: BLE001
            pass
        # ⑤ 同主人一步互配(docs/74;不变量三重门见函数 docstring):首配整来回成功 → 对端
        #    console 身份(指纹 pin 验过的 sess.peer_pub)回写我方已配对表(scope full),
        #    它反向拨我免第二枚码。复连(code=None)不写;peer_pub 拿不到/坏 → trust_peer
        #    宁空勿毒拒写。label 取对端 advert 的设备名(纯展示,trust_peer 里再消毒)。
        if code:
            try:
                from karvyloop.relay.pairing import PairingStore
                adv = peer_body.get("advert") or {}
                label = str(adv.get("label") or "") if isinstance(adv, dict) else ""
                peer_pub = getattr(sess, "peer_pub", b"") or b""
                if PairingStore(state_dir).trust_peer(peer_pub, label=label):
                    logger.info("[mesh-sync] paired back peer console (scope=full) — "
                                "it can now dial us without a second code")
            except Exception as e:  # noqa: BLE001 — 回配失败不吞同步结果,但必须出声:
                logger.warning(     # 否则对端反向拨会被 pairing_rejected,用户又要第二枚码
                    f"[mesh-sync] pair-back failed ({type(e).__name__}) — "
                    f"peer will still need a code to dial us back")
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
