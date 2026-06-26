"""store — 业务域定义持久化(M3+ 拍 9.2c-持久化)。

设计:docs/18 业务域 + docs/26 §C(对话按 domain_id 分区 → domain_id 必须稳定)。

**为什么必须持久化**:业务域是**用户创造的数据**(像建公司)。本地优先 = 用户造的一切
(对话/记忆/技能/业务域)默认持久,不持久才是要解释的特例。且对话文件按 `<domain_id>/`
分区(docs/26),domain_id **必须跨重启稳定**,否则旧业务域对话对不上 → 必须存原 id。

**形态**:`~/.karvyloop/domains.json` 一个数组,每项一个域(含原 id);atomic 写。
重建走 `ValueMd.parse` / `Deontic` / `Routine`,**保留原 id/created_at/lifecycle/parent_id**。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .deontic import Deontic
from .registry import BusinessDomain, Routine
from .value import ValueMd


def domain_to_dict(d: BusinessDomain) -> dict:
    """BusinessDomain → JSON-able dict(value.md 存 text,load 时 re-parse)。"""
    return {
        "id": d.id,
        "name": d.name,
        "created_by": d.created_by,
        "created_at": d.created_at,
        "lifecycle": d.lifecycle,
        "value_md": d.value_md.text,
        "deontic": {
            "forbid": list(d.deontic.forbid),
            "oblige": list(d.deontic.oblige),
            "permit": list(d.deontic.permit),
        },
        "member_query": d.member_query,
        "routine": {
            "daily": list(d.routine.daily),
            "weekly": list(d.routine.weekly),
        },
        "parent_id": d.parent_id,
    }


def domain_from_dict(rec: dict) -> BusinessDomain:
    """dict → BusinessDomain(保留原 id 等;value.md re-parse)。"""
    deo = rec.get("deontic", {}) or {}
    rou = rec.get("routine", {}) or {}
    return BusinessDomain(
        id=rec["id"],
        name=rec.get("name", ""),
        created_by=rec.get("created_by", ""),
        created_at=rec.get("created_at", ""),
        lifecycle=rec.get("lifecycle", "active"),
        value_md=ValueMd.parse(rec.get("value_md", "")),  # 9.4d:缺省 = 空灵魂(可选)
        deontic=Deontic(
            forbid=tuple(deo.get("forbid", ())),
            oblige=tuple(deo.get("oblige", ())),
            permit=tuple(deo.get("permit", ())),
        ),
        member_query=rec.get("member_query", ""),
        routine=Routine(
            daily=tuple(rou.get("daily", ())),
            weekly=tuple(rou.get("weekly", ())),
        ),
        parent_id=rec.get("parent_id"),
    )


class DomainStore:
    """业务域定义的磁盘存储(JSON 数组,atomic 写)。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[BusinessDomain]:
        """读回所有域(保留原 id)。文件不存在 / 坏 → 返空(不阻塞启动)。"""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        out: list[BusinessDomain] = []
        for rec in data:
            if not isinstance(rec, dict):
                continue
            try:
                out.append(domain_from_dict(rec))
            except Exception:
                continue  # 坏项跳过,不阻塞其它域
        return out

    def save_all(self, domains) -> None:
        """整存(atomic:.tmp → replace)。"""
        payload = json.dumps([domain_to_dict(d) for d in domains], ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = ["DomainStore", "domain_to_dict", "domain_from_dict"]
