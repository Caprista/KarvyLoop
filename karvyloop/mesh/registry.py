"""mesh/registry — 同主人设备花名册(owner-side,持久化)。

"我有哪些设备、各自什么能力、上次见到是什么时候"。键 = device_id(relay 身份指纹)。
本设备自注册(register_self);其它设备通过配对/手动加入。**presence 第一刀 = last_seen 新鲜度**
(活 presence 靠 relay 长连生死,待后续)。持久化经 state_dir/devices.json(原子写,0600)。

同主人全信任:这里只列**我自己的**设备,不涉及"分享给别人"(那条走 docs/73 的 opaque+scope)。
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Optional

STATE_FILE = "devices.json"
# 多久没见 = 判离线(presence 第一刀:纯 last_seen 新鲜度;活心跳待后续)。
ONLINE_WINDOW_S = 90.0


def _default_dir() -> Path:
    return Path.home() / ".karvyloop"


@dataclasses.dataclass
class DeviceRecord:
    """一台设备的花名册项(能力指纹 + 怎么连它 + 上次见到)。"""
    device_id: str
    label: str = ""
    os: str = ""
    arch: str = ""
    sandbox: str = ""
    karvyloop: str = ""
    relay_url: str = ""            # 怎么跨网连到它(它的 relay);空=同网/未知
    room: str = ""                 # 它的 relay 房间号
    last_seen: float = 0.0
    is_self: bool = False

    def online(self, now: Optional[float] = None) -> bool:
        _now = time.time() if now is None else now
        return self.last_seen > 0 and (_now - self.last_seen) < ONLINE_WINDOW_S

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DeviceRecord":
        d = d or {}
        return DeviceRecord(
            device_id=str(d.get("device_id") or ""),
            label=str(d.get("label") or ""),
            os=str(d.get("os") or ""), arch=str(d.get("arch") or ""),
            sandbox=str(d.get("sandbox") or ""), karvyloop=str(d.get("karvyloop") or ""),
            relay_url=str(d.get("relay_url") or ""), room=str(d.get("room") or ""),
            last_seen=float(d.get("last_seen") or 0.0), is_self=bool(d.get("is_self")))


class DeviceRegistry:
    """同主人设备花名册(持久化;用户数据默认存盘 —— [[user-data-persists-by-default]])。"""

    def __init__(self, base_dir: "Optional[Path | str]" = None) -> None:
        self.dir = Path(base_dir) if base_dir else _default_dir()

    @property
    def _path(self) -> Path:
        return self.dir / STATE_FILE

    def _load(self) -> dict:
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
        os.replace(tmp, self._path)

    def register(self, rec: DeviceRecord) -> None:
        """加/更新一台设备(按 device_id 去重覆盖)。device_id 空 → 忽略(没身份不入册)。"""
        if not rec.device_id:
            return
        state = self._load()
        devs = state.setdefault("devices", {})
        devs[rec.device_id] = rec.to_dict()
        self._save(state)

    def register_self(self, fingerprint: dict, *, relay_url: str = "", room: str = "",
                      now: Optional[float] = None) -> Optional[DeviceRecord]:
        """把本设备(指纹)登记进花名册(is_self=True,last_seen=now)。无 device_id → None。"""
        did = str((fingerprint or {}).get("device_id") or "")
        if not did:
            return None
        rec = DeviceRecord(
            device_id=did, label=str(fingerprint.get("label") or ""),
            os=str(fingerprint.get("os") or ""), arch=str(fingerprint.get("arch") or ""),
            sandbox=str(fingerprint.get("sandbox") or ""),
            karvyloop=str(fingerprint.get("karvyloop") or ""),
            relay_url=relay_url, room=room,
            last_seen=(time.time() if now is None else now), is_self=True)
        self.register(rec)
        return rec

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        d = self._load().get("devices", {}).get(device_id or "")
        return DeviceRecord.from_dict(d) if d else None

    def mark_seen(self, device_id: str, now: Optional[float] = None) -> bool:
        """更新一台设备的 last_seen(每次成功连到它时调 → presence 新鲜)。"""
        state = self._load()
        devs = state.get("devices", {})
        if device_id not in devs:
            return False
        devs[device_id]["last_seen"] = time.time() if now is None else now
        self._save(state)
        return True

    def remove(self, device_id: str) -> bool:
        state = self._load()
        devs = state.get("devices", {})
        if device_id not in devs:
            return False
        devs.pop(device_id, None)
        self._save(state)
        return True

    def list_all(self) -> list:
        return [DeviceRecord.from_dict(d) for d in self._load().get("devices", {}).values()]


__all__ = ["DeviceRecord", "DeviceRegistry", "ONLINE_WINDOW_S"]
