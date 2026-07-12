"""mesh/skill_bridge — 结晶技能(SKILL.md)↔ MeshLog 的桥(outbox 式,docs/74 §5.2 slice3a)。

**只做"出生"(create-sync)+ 墓碑删除**(影响评估:atoms 演进/合并那半要 work-stealing 先落地,
排 slice3b)。技能是**设备无关的方法体**(SKILL.md=Agent Skills 开放标准),避开了 atom 的
executable/unresolved 设备相对雷 —— 是更干净更有价值的一刀("你在 A 结晶的方法,B 上直接有")。

**天然无回声**:crystallize() **只在本地结晶时调**(远端技能走 write_skill_md 应用,不走 crystallize)
→ 发射点只可能是本地,不需回声抑制(比 belief 桥还省一层)。

**Event-Carried State Transfer**:事件带**完整 SKILL.md 原文**(幂等 apply=写文件);同 name 目录已在
→ 跳过(create-only,不覆盖不演进);墓碑事件删目录。**幂等键=技能名**(sig-fallback 命名保证
同 sig 同名,recall/list 四处同源,crystallize.py:530)。

**Hardy 拍的产品语义 + 影响评估边界**:只同步**用户结晶的技能**(crystallize 本就是 user 结晶路径);
**system 技能(随包发版)排除**;第三方/untrusted 导入技能(锁/授网/scripts 设备本地)**不同步**。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from karvyloop.mesh.synclog import MeshEvent, MeshLog

logger = logging.getLogger(__name__)

K_SKILL = "skill-crystallized"    # 与 docs/74 事件词根一致
K_SKILL_REMOVED = "skill-removed"  # 墓碑:删除传播(化解"删了又被对账复活"雷)

# system 技能(随包发版,每台都有)绝不同步;scope=="user" 才是"你的结晶"。
_SYNC_SCOPES = ("user",)

# 模块级发射器:console 启动时 register;crystallize() 本地结晶后 notify(它是函数不是有态管理器)。
_emitter: Optional[Callable[[str, str, str, str], None]] = None


def register_emitter(fn: Optional[Callable[[str, str, str, str], None]]) -> None:
    """console 接线:注册"本地结晶→发 MeshLog 事件"的发射器(None=解绑)。"""
    global _emitter
    _emitter = fn


def notify_crystallized(name: str, sig: str, skill_md_text: str, scope: str) -> None:
    """crystallize() 落盘后调这里(defensive:未接 mesh / 非 user scope / 异常 都静默不影响结晶)。"""
    fn = _emitter
    if fn is None or (scope or "") not in _SYNC_SCOPES:
        return
    try:
        fn(name, sig, skill_md_text, scope)
    except Exception:  # noqa: BLE001 — 发事件失败绝不打断结晶(同步是增益,结晶是地基)
        logger.warning("[mesh] skill 结晶事件没发出(结晶本身成功)")


def attach_skill_emitter(log: MeshLog, store=None) -> None:
    """把发射器接到共享日志:本地结晶 → 追加 K_SKILL 事件(带完整 SKILL.md)+ 持久化日志。"""
    def _emit(name: str, sig: str, skill_md_text: str, scope: str) -> None:
        import time
        log.append(K_SKILL, {"name": name, "signature": sig, "scope": scope,
                             "skill_md": skill_md_text, "origin_device": log.device_id},
                   wall=int(time.time() * 1000))
        if store is not None:
            store.persist_new(log)
    register_emitter(_emit)


def apply_skill_events(events: Iterable[MeshEvent], skills_dir) -> int:
    """把远端同步来的技能事件应用到本地技能树。返回真落地(写/删)条数。

    - K_SKILL:**同 name 目录已在 → 跳过**(create-only 幂等,不覆盖不演进);否则 write_skill_md。
    - K_SKILL_REMOVED:墓碑 → 删该技能目录(化解删了又被对账复活)。
    - 坏事件跳过(宁空勿毒:坏 SKILL.md 不进技能库)。
    """
    from karvyloop.crystallize.crystallize import write_skill_md
    root = Path(skills_dir)
    applied = 0
    for ev in (events or []):
        p = ev.payload or {}
        name = str(p.get("name") or "")
        if not name:
            continue
        try:
            if ev.kind == K_SKILL:
                md = p.get("skill_md")
                if not isinstance(md, str) or not md.strip():
                    continue                                   # 宁空勿毒
                if (root / name / "SKILL.md").exists():
                    continue                                   # 幂等:已在 → 不覆盖(create-only)
                write_skill_md(root / name, md, skills_root=root)
                applied += 1
            elif ev.kind == K_SKILL_REMOVED:
                import shutil
                d = root / name
                if d.is_dir() and d.resolve().parent == root.resolve():   # 防越界删
                    shutil.rmtree(d, ignore_errors=True)
                    applied += 1
        except Exception:  # noqa: BLE001 — 单条坏事件不拖垮整轮
            logger.warning(f"[mesh] 应用技能事件失败,已跳过(name={name})")
            continue
    return applied


def reconcile_skills_from_log(log: MeshLog, skills_dir) -> int:
    """启动对账:把日志里的技能事件应用到本地(幂等;盖 CLI 离线 mesh-sync 合并的缝)。"""
    return apply_skill_events([e for e in log.entries()
                               if e.kind in (K_SKILL, K_SKILL_REMOVED)], skills_dir)


__all__ = ["K_SKILL", "K_SKILL_REMOVED", "register_emitter", "notify_crystallized",
           "attach_skill_emitter", "apply_skill_events", "reconcile_skills_from_log"]
