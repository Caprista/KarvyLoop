"""mesh/cli — `karvyloop devices`:登记本设备 + 列出我的设备花名册(docs/74 第一刀)。"""
from __future__ import annotations

from typing import Optional


def cmd_devices(label: Optional[str] = None, state_dir=None) -> int:
    """列出"我的设备 mesh":本设备自注册(刷新 last_seen)后,打印花名册 + 能力 + 在线态。

    `--label` 给本设备起个名(如"家里的 Linux");不给则沿用已存的。presence 第一刀 = last_seen
    新鲜度(活心跳待后续)。互访:`karvyloop remote --room <对方 room>`(slice 3a/3b 已建)。
    """
    from karvyloop.mesh.fingerprint import device_fingerprint
    from karvyloop.mesh.registry import DeviceRegistry

    reg = DeviceRegistry(state_dir)
    fp = device_fingerprint(state_dir, label=label)
    reg.register_self(fp)                       # 自注册 + 刷新 last_seen
    devs = reg.list_all()

    if not fp.get("device_id"):
        print("This device has no relay identity yet — run `karvyloop relay-pair` to create one,")
        print("then it becomes addressable in your device mesh.")
    if not devs:
        print("No devices in your mesh yet.")
        return 0

    print(f"Your device mesh ({len(devs)} device{'s' if len(devs) != 1 else ''}):")
    for d in sorted(devs, key=lambda x: (not x.is_self, x.label, x.device_id)):
        tag = "★ this device" if d.is_self else ("● online" if d.online() else "○ offline")
        name = d.label or (d.device_id[:19] + "…" if d.device_id else "?")
        caps = f"{d.os or '?'}/{d.arch or '?'} · sandbox={d.sandbox or '?'} · kl={d.karvyloop or '?'}"
        where = f"  ← remote --room {d.room}" if (d.room and not d.is_self) else ""
        print(f"  {tag:<13} {name:<26} [{caps}]{where}")
    return 0


__all__ = ["cmd_devices"]
