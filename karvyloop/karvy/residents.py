"""karvy/residents — 原住民(预置角色)引荐式入住(docs/60 研判落地)。

**空屋子问题**:新用户打开 console,角色库空空如也 —— 没啥可干,扭头就走。
解法不是自动塞角色(那不是 H2A),而是**小卡引荐**:角色库为空时出一张
**引荐决策卡**(KIND_RESIDENT_REFERRAL),介绍可入住的原住民;你 ACCEPT 才真建。

**不新造 preset 子系统** —— 全部复用现有机制:
- 原住民 = L2 角色镜像:包内只读资产 `karvyloop/system_residents/<id>/`
  (IDENTITY/SOUL/USER/MEMORY/VERIFY 正文 + resident.json 清单),与
  `system_skills/`、`system_contracts/` 同发版语义(随包升级、数据 reset 动不到)。
- 入住 = 实例化 = `RoleRegistry.create()` 落 `~/.karvyloop/roles/<id>/`,
  **尽责下属契约由 create 统一 seed**(三入口同一份,镜像里刻意不放 COMMITMENT)。
  从这一刻起它是你的实例,升级只更新镜像、绝不覆写你养出来的那一个。
- 安全边界在确定性层:入住时按清单把目录白名单落 `fs_grants` 台账
  (能力总览可见、可撤),不是 prompt 约束 —— 安全是地基。
- 卡走现有 proposal 体系(Proposal + registry + broadcast + handler 注入,
  与 knowledge_tick / weekly_digest 同款先例)。

**静默规则**(不纠缠):
- ACCEPT → 角色建好,状态记 accepted;角色库非空后天然不再触发。
- REJECT → 卡从待决表消失;状态里 offered=True → **永不再提**。
- DEFER  → 卡留在待决表(落盘跨重启),不重复出新卡。
- 引荐卡一生只出一次(offered 落 `~/.karvyloop/residents_referral.json`)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KIND_RESIDENT_REFERRAL = "resident_referral"

#: 清单里未声明时的默认授权操作(读写:整理文件要移动/重命名;删除仍走 H2A 硬闸)
_DEFAULT_GRANT_OPS = ("read", "write")


def system_residents_dir() -> Path:
    """包内**只读**原住民镜像目录(`karvyloop/system_residents/`)。镜像 system_skills_dir。"""
    return Path(__file__).resolve().parent.parent / "system_residents"


def default_state_path() -> Path:
    """引荐状态文件(offered / decision;静默规则的持久依据)。"""
    return Path.home() / ".karvyloop" / "residents_referral.json"


def read_referral_state(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else default_state_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}   # 坏文件当空(fail-safe;顶多重新引荐一次,绝不炸启动)


def write_referral_state(state: dict, path: Optional[Path] = None) -> None:
    p = Path(path) if path else default_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[residents] 引荐状态落盘失败(下次可能重复引荐一次): %s", e)


# ---- 镜像读取(resident.json 清单 + 灵魂文件正文) ----

def _read_slot(d: Path, name: str) -> str:
    p = d / name
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except OSError:
        return ""


def load_resident(resident_id: str, residents_dir: Optional[Path] = None) -> Optional[dict]:
    """读一个原住民镜像 → dict;清单缺失/损坏 → None(宁缺勿毒)。"""
    base = Path(residents_dir) if residents_dir else system_residents_dir()
    d = base / resident_id
    mf = d / "resident.json"
    if not mf.exists():
        return None
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
    except Exception:
        logger.warning("[residents] 镜像清单损坏,跳过: %s", mf)
        return None
    rid = str(manifest.get("id") or resident_id).strip()
    return {
        "id": rid,
        "emoji": str(manifest.get("emoji") or "").strip(),
        "nickname": dict(manifest.get("nickname") or {}),
        "title": dict(manifest.get("title") or {}),
        "pitch": dict(manifest.get("pitch") or {}),
        "first_task": dict(manifest.get("first_task") or {}),
        "skills": [str(s).strip() for s in (manifest.get("skills") or []) if str(s).strip()],
        "grant_dirs": [str(g).strip() for g in (manifest.get("grant_dirs") or []) if str(g).strip()],
        "grant_ops": [str(o).strip() for o in (manifest.get("grant_ops") or _DEFAULT_GRANT_OPS)
                      if str(o).strip() in ("read", "write")],
        "identity": _read_slot(d, "IDENTITY.md"),
        "soul": _read_slot(d, "SOUL.md"),
        "user": _read_slot(d, "USER.md"),
        "memory": _read_slot(d, "MEMORY.md"),
        "verify": _read_slot(d, "VERIFY.md"),
    }


def list_residents(residents_dir: Optional[Path] = None) -> list[dict]:
    """包内(或注入目录)全部原住民镜像,按目录名排序。"""
    base = Path(residents_dir) if residents_dir else system_residents_dir()
    if not base.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "resident.json").exists():
            res = load_resident(child.name, base)
            if res is not None:
                out.append(res)
    return out


def _loc(d: dict, locale: str) -> str:
    """双语字段取值:当前 locale > en > 任意非空。"""
    return str(d.get(locale) or d.get("en") or next((v for v in d.values() if v), "")).strip()


def resident_display_name(res: dict, locale: Optional[str] = None) -> str:
    from karvyloop.i18n import get_locale
    loc = locale or get_locale()
    name = _loc(res.get("nickname") or {}, loc) or res.get("id", "")
    emoji = res.get("emoji") or ""
    return f"{emoji} {name}".strip()


# ---- 触发判定(空角色库 + 静默规则) ----

def should_offer_referral(*, role_registry: Any, state: dict) -> bool:
    """要不要出引荐卡:角色库空 + 从没引荐过。

    静默规则:offered=True 后**永不再出**(REJECT 后不纠缠;DEFER 的卡本就
    留在待决表跨重启存活,不需要重出;ACCEPT 后角色库非空,天然不触发)。
    """
    if role_registry is None:
        return False
    try:
        if len(role_registry) > 0:
            return False
    except Exception:
        return False
    if state.get("decision") or state.get("offered"):
        return False
    return True


# ---- 引荐卡(Proposal;现有卡 UI 通用渲染 summary/basis/options) ----

def proposal_for_resident_referral(residents: list[dict], *, ts: float,
                                   strength: float = 0.85):
    """把可入住的原住民包成一张引荐决策卡(H2A:你 ACCEPT 才真建)。

    payload 全字符串(兼容「改了再批」白名单);proposal_id 按成员集合稳定派生
    (幂等:同一批原住民收敛成同一张卡)。文案走 i18n(出卡时按当前 locale 定稿)。
    """
    from karvyloop.i18n import get_locale, t
    from karvyloop.karvy.atoms import Proposal
    loc = get_locale()
    ids = [r["id"] for r in residents]
    sep = "、" if loc == "zh" else ", "
    names = sep.join(resident_display_name(r, loc) for r in residents)
    pitches = "\n".join(p for p in (_loc(r.get("pitch") or {}, loc) for r in residents) if p)
    basis = (pitches + ("\n\n" if pitches else "") + t("residents.referral.basis_footer")).strip()
    pid = KIND_RESIDENT_REFERRAL + "-0-" + hashlib.sha1(
        ",".join(sorted(ids)).encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=t("residents.referral.summary", names=names),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_RESIDENT_REFERRAL,
        payload={"resident_ids": ",".join(ids)},
        proposal_id=pid,
        basis=basis,
    )


# ---- 入住 = 实例化(复用 RoleRegistry.create;契约由 create 统一 seed) ----

def _seed_grants(res: dict, fs_grants: Any, *, role: str,
                 home: Optional[Path] = None) -> list[str]:
    """按清单把目录白名单落 fs_grants 台账(幂等;敏感地板在 record 内优先)。"""
    if fs_grants is None or not res.get("grant_dirs"):
        return []
    base = Path(home) if home else Path.home()
    ops = list(res.get("grant_ops") or _DEFAULT_GRANT_OPS)
    granted: list[str] = []
    for name in res["grant_dirs"]:
        p = base / name
        try:
            g = fs_grants.record(str(p), ops, role=role, origin="resident_seed")
        except Exception as e:
            logger.warning("[residents] 目录授权失败(跳过 %s): %s", p, e)
            g = None
        if g is not None:
            granted.append(str(p))
    return granted


def instantiate_resident(res: dict, *, role_registry: Any, fs_grants: Any = None,
                         locale: Optional[str] = None,
                         home: Optional[Path] = None) -> dict:
    """入住一个原住民:镜像 → 实例(`RoleRegistry.create` + VERIFY/MEMORY 种子 + fs 白名单)。

    - COMMITMENT(尽责下属契约)由 create 统一 seed —— 引荐不是第四入口,镜像里没有它。
    - 已存在同名角色 → 复用(幂等,不覆写你的实例);白名单仍确保在台账(record 幂等)。
    - VERIFY 走 update_soul(可编辑灵魂槽);MEMORY 是运行时文件,入住时**一次性**
      写入镜像种子(领域常识索引)—— 之后归结晶/记忆管线,系统不再覆写。
    """
    from karvyloop.i18n import get_locale
    loc = locale or get_locale()
    rid = res["id"]
    display = resident_display_name(res, loc)
    if role_registry.get(rid) is not None:
        granted = _seed_grants(res, fs_grants, role=rid, home=home)
        return {"created": False, "role_id": rid, "display_name": display,
                "granted_dirs": granted}
    nickname = _loc(res.get("nickname") or {}, loc)
    title = _loc(res.get("title") or {}, loc)
    try:
        role_registry.create(
            rid,
            identity=res.get("identity", ""),
            soul=res.get("soul", ""),
            user_desc=res.get("user", ""),
            skill_ids=list(res.get("skills") or []),
            nickname=nickname,
            title=title,
        )
    except Exception as e:
        # 技能校验失败(极端:技能库源不含系统技能)→ 降级为不带技能建角色,绝不因引用挡入住
        from karvyloop.roles.registry import UnknownSkillError
        if not isinstance(e, UnknownSkillError):
            raise
        logger.warning("[residents] 技能引用校验未过(%s)—— 降级为无随身技能入住", e)
        role_registry.create(rid, identity=res.get("identity", ""), soul=res.get("soul", ""),
                             user_desc=res.get("user", ""), nickname=nickname, title=title)
    if res.get("verify"):
        role_registry.update_soul(rid, "VERIFY", res["verify"])
    if res.get("memory"):
        try:
            (role_registry.root / rid / "MEMORY.md").write_text(
                f"# MEMORY\n\n{res['memory']}\n", encoding="utf-8")
        except Exception as e:
            logger.warning("[residents] MEMORY 种子写入失败(角色已建,可后补): %s", e)
    granted = _seed_grants(res, fs_grants, role=rid, home=home)
    return {"created": True, "role_id": rid, "display_name": display,
            "granted_dirs": granted}


def make_resident_referral_handler(app: Any):
    """ACCEPT 兑现 handler:真建角色(契约 seed)+ 目录白名单落台账 + 状态记 accepted。

    K5:只在用户 ACCEPT 后被调。幂等:已入住的复用,不覆写实例。
    """
    def handler(proposal) -> tuple[bool, str]:
        from karvyloop.i18n import t
        st = getattr(app, "state", None)
        role_reg = getattr(st, "role_registry", None)
        if role_reg is None:
            return False, t("residents.referral.no_registry")
        fs = getattr(st, "fs_grants", None)
        residents_dir = getattr(st, "residents_dir", None)
        state_path = getattr(st, "residents_state_path", None)
        home = getattr(st, "residents_home", None)
        payload = getattr(proposal, "payload", None) or {}
        ids = [s.strip() for s in str(payload.get("resident_ids") or "").split(",") if s.strip()]
        moved_in: list[str] = []
        granted: list[str] = []
        for rid in ids:
            res = load_resident(rid, residents_dir)
            if res is None:
                logger.warning("[residents] 镜像不在(打包丢了?): %s", rid)
                continue
            try:
                out = instantiate_resident(res, role_registry=role_reg, fs_grants=fs, home=home)
            except Exception as e:
                logger.warning("[residents] 入住 %s 失败: %s", rid, e)
                return False, t("residents.referral.failed", name=rid, error=str(e))
            moved_in.append(out["display_name"])
            granted.extend(out.get("granted_dirs", []))
        if not moved_in:
            return False, t("residents.referral.none_found")
        state = read_referral_state(state_path)
        state.update({"offered": True, "decision": "accepted", "decided_at": time.time()})
        write_referral_state(state, state_path)
        sep = "、" if any("一" <= ch <= "鿿" for ch in "".join(moved_in)) else ", "
        return True, t("residents.referral.accepted",
                       names=sep.join(moved_in),
                       dirs=(sep.join(dict.fromkeys(granted)) or "-"))
    return handler


# ---- console 接线口(boot 时调一次;knowledge_tick 同款 handler 注入先例) ----

async def residents_referral_tick(app: Any) -> dict:
    """空角色库检查 + 出引荐卡(boot_poll 里调一次)。返回 {offered, reason}。"""
    st = getattr(app, "state", None)
    role_reg = getattr(st, "role_registry", None)
    preg = getattr(st, "proposal_registry", None)
    if role_reg is None or preg is None:
        return {"offered": False, "reason": "role/proposal registry 未接"}
    # 卡已挂着(DEFER / 重启后落盘恢复)→ 不重复出
    try:
        for p in preg.pending():
            if getattr(p, "kind", "") == KIND_RESIDENT_REFERRAL:
                return {"offered": False, "reason": "已有待决引荐卡"}
    except Exception:
        pass
    state_path = getattr(st, "residents_state_path", None)
    state = read_referral_state(state_path)
    if not should_offer_referral(role_registry=role_reg, state=state):
        return {"offered": False, "reason": "非空角色库或已引荐过(静默)"}
    residents = list_residents(getattr(st, "residents_dir", None))
    if not residents:
        return {"offered": False, "reason": "包内无原住民镜像"}
    # ACCEPT 兑现 handler 运行时注入(knowledge_tick 先例;万一缺,registry
    # 的"无 handler 卡保留待决"防御兜底,不吞卡)
    handlers = getattr(st, "proposal_handlers", None)
    if isinstance(handlers, dict):
        handlers.setdefault(KIND_RESIDENT_REFERRAL, make_resident_referral_handler(app))
    card = proposal_for_resident_referral(residents, ts=time.time())
    try:
        from karvyloop.console.proposals import broadcast_proposal
        await broadcast_proposal(app, card)   # 内部先 registry.register 再推 WS
    except Exception as e:
        logger.warning("[residents] 引荐卡广播失败(不重试,卡已进待决表则仍可见): %s", e)
    state.update({"offered": True, "proposal_id": card.proposal_id, "offered_at": time.time()})
    write_referral_state(state, state_path)
    return {"offered": True, "proposal_id": card.proposal_id}


__all__ = [
    "KIND_RESIDENT_REFERRAL",
    "system_residents_dir", "default_state_path",
    "read_referral_state", "write_referral_state",
    "load_resident", "list_residents", "resident_display_name",
    "should_offer_referral", "proposal_for_resident_referral",
    "instantiate_resident", "make_resident_referral_handler",
    "residents_referral_tick",
]
