"""mesh/cli — `karvyloop devices`:登记本设备 + 列花名册 + **知情删除**(docs/74)。"""
from __future__ import annotations

from typing import Optional


def cmd_devices_remove(target: str, *, yes: bool = False, state_dir=None) -> int:
    """删除一台设备(按指纹/设备 id 前缀或 label 匹配)——**知情的 H2A**(docs/74 §6.2):

    删前算**能力增量**(它独占的、其它设备都没有的能力)。收窄(delta 非空)→ 打印会永久失去
    什么 + **要求 --yes 再确认**;不收窄 → 只降资源不降能力,轻确认直接删。
    """
    from karvyloop.mesh.registry import DeviceRegistry
    from karvyloop.mesh.schedule import capability_delta_on_remove

    reg = DeviceRegistry(state_dir)
    devs = reg.list_all()
    t = (target or "").strip().lower()
    hits = [d for d in devs
            if d.device_id.lower().startswith(t) or (d.label and d.label.lower() == t)]
    if not hits:
        print(f"No device matched: {target}  (list with: karvyloop devices)")
        return 1
    if len(hits) > 1:
        print(f"Ambiguous target ({len(hits)} devices matched) — use a longer fingerprint prefix:")
        for d in hits:
            print(f"  {d.device_id}  {d.label or ''}")
        return 1
    dev = hits[0]
    name = dev.label or dev.device_id
    lost = capability_delta_on_remove(dev, devs)
    if dev.is_self:
        print(f"⚠ '{name}' is THIS device — removing it from the roster doesn't uninstall anything,")
        print("  but other devices will stop planning work for it.")
    if lost:
        # 能力边界收窄 → 风险警告 + 再确认(没 --yes 不动手)
        print(f"⚠ Removing '{name}' will PERMANENTLY lose capabilities no other device provides:")
        print(f"    {', '.join(sorted(lost))}")
        print("  Tasks that need these will become impossible to run in your mesh.")
        if not yes:
            print(f"\nNot removed. To confirm:  karvyloop devices --remove {target} --yes")
            return 1
    if not yes and not lost and dev.is_self:
        print(f"\nNot removed. To confirm:  karvyloop devices --remove {target} --yes")
        return 1
    reg.remove(dev.device_id)
    note = "capability boundary narrowed" if lost else "no capability lost (covered by other devices)"
    print(f"Removed '{name}' from your mesh ({note}).")
    return 0


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


__all__ = ["cmd_devices", "cmd_devices_remove"]
