"""roles/registry — 角色库(L2 镜像 CRUD,M3+ 拍 9.5 #3-P1)。

设计:docs/00 §2.4(角色=配方+灵魂)+ schemas/role.py + adapter(7 文件物化格式)
+ ethos/auditor(COMPOSITION atom 引用校验)。

**角色镜像 = 一个 agent 目录**(不另起炉灶,沿用 adapter/validator 的格式):
  IDENTITY/SOUL/USER/MEMORY/COMMITMENT/VERIFY.md + COMPOSITION.yaml
  COMPOSITION.yaml 头 `<!-- step_id: COMPOSITION -->`,用 `atom: <name>` 引用**公共原子库**的原子。

**甲(买糖)**:角色"用"原子不"拥有";COMPOSITION 里引的原子必须在公共原子库注册
(create 时校验存在;缺 → 先去原子库建,API 层给"就地建"入口)。

**镜像 vs 实例(§2.1)**:这里管的是镜像(目录定义,可分发);实例(记忆)是用出来的,不在这。
**持久化**:角色目录本身就是持久态,落 `~/.karvyloop/roles/<role_id>/`。
"""
from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import Optional

# 7 槽位(与 adapter/planner SLOT_NAMES 对齐;COMPOSITION 是 .yaml,其余 .md)
SLOT_NAMES: tuple[str, ...] = (
    "IDENTITY", "SOUL", "USER", "MEMORY", "COMMITMENT", "VERIFY", "COMPOSITION",
)

# 角色 id = 目录名:Unicode 字母/数字/下划线/连字符(支持中文角色名如「设计师」;
# 角色不经 COMPOSITION 的 `atom:` ASCII 正则引用,故可放宽到 Unicode。排除空格/路径分隔符)。
_ROLE_ID_RE = re.compile(r"^[\w\-]+$", re.UNICODE)
# COMPOSITION.yaml 里的 atom 引用(与 ethos/auditor 同款抓法)
_ATOM_REF_RE = re.compile(r"atom:\s*([A-Za-z0-9_]+)")
# COMPOSITION.yaml 里的 skill 引用(L0 技能;角色"用不拥有",同 atom;
# 技能名 kebab-case,放宽到 \w\- 与 skill_index 的 name 一致)。
_SKILL_REF_RE = re.compile(r"skill:\s*([\w\-]+)")


def _slot_filename(slot: str) -> str:
    return "COMPOSITION.yaml" if slot == "COMPOSITION" else f"{slot}.md"


def _composition_yaml(role_id: str, atom_ids: list[str],
                      skill_ids: Optional[list[str]] = None) -> str:
    """物化一个合法 COMPOSITION.yaml(含 step_id 头 + atom 引用 + skill 引用)。

    `skills:` 与 `atoms:` 平行 —— 角色编写时**直接引用**公共技能库里的技能(docs/00 §2.2:
    L0 技能可被任何 L1 原子调用;这里在角色层声明"这个角色随身带哪几个技能",绑定即生效,
    不靠快脑模糊召回碰运气)。"用不拥有":引的技能必须已在技能库(create 时校验)。
    """
    skill_ids = skill_ids or []
    lines = [
        "<!-- step_id: COMPOSITION -->",
        f"role: {role_id}",
        "created_by: console",
        "atoms:",
    ]
    for aid in atom_ids:
        lines.append(f"  - atom: {aid}")
    if not atom_ids:
        lines.append("  # (暂未挑原子;以后从公共原子库加)")
    lines.append("skills:")
    for sid in skill_ids:
        lines.append(f"  - skill: {sid}")
    if not skill_ids:
        lines.append("  # (暂未挑技能;可从技能库引用或导入第三方)")
    return "\n".join(lines) + "\n"


def _stub_slot(slot: str) -> str:
    """非 IDENTITY 的灵魂文件最小 stub —— 保 7 文件齐性,内容 P2 再充实。"""
    return f"# {slot}\n\n(待充实)\n"


class RoleView:
    """读回的一个角色镜像视图(从目录解析)。"""

    def __init__(self, role_id: str, identity: str, atom_ids: list[str], path: Path,
                 *, nickname: str = "", title: str = "", model: str = "",
                 skill_ids: Optional[list[str]] = None) -> None:
        self.id = role_id
        self.identity = identity
        self.atom_ids = atom_ids
        self.path = path
        self.nickname = nickname   # brick4:花名(在某域里的人名,如"张三")
        self.title = title         # brick4:职务(如"产品经理")
        self.model = model         # 角色级模型引用(空=层叠到域/全局 default;#1 §3.1 软默认层叠)
        self.skill_ids = list(skill_ids or [])  # 角色随身技能(L0,引用公共技能库;绑定即生效)

    def to_dict(self) -> dict:
        return {"id": self.id, "identity": self.identity,
                "atom_ids": list(self.atom_ids), "path": str(self.path),
                "nickname": self.nickname, "title": self.title, "model": self.model,
                "skill_ids": list(self.skill_ids)}

    def display_name(self) -> str:
        """场内显示名:花名(职务) / 花名 / role_id —— 给"哟吼/张三(产品经理)"那种表述用。"""
        name = self.nickname or self.id
        return f"{name}({self.title})" if self.title else name


class DuplicateRoleError(ValueError):
    """角色 id 已存在。"""


class UnknownAtomError(ValueError):
    """COMPOSITION 引用的原子不在公共原子库(先去建/买糖)。"""


class UnknownSkillError(ValueError):
    """COMPOSITION 引用的技能不在技能库(先结晶/导入第三方)。"""


def _locked(fn):
    """#6 并发:护住写角色文件的方法(create/update/update_soul/add_atom/rewrite)——磁盘 read-modify-write,
    多协作并行改同一角色不加锁会互相盖(如两次 create_atom 同时 add_atom 少一个)。RLock 可重入,方法互调不死锁。"""
    import functools

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class RoleRegistry:
    """角色库:管 agent 目录(7 文件 + COMPOSITION.yaml)。

    atom_registry 注入(可选):create 时校验挑的原子都已注册(甲:用不拥有,但得存在)。
    skills_dir / skill_index 注入(可选):create/update 时校验引的技能都在技能库
    (同"用不拥有":角色引用技能,技能本体住技能库)。
    """

    def __init__(self, roles_dir, *, atom_registry: Optional[object] = None,
                 skills_dir: Optional[object] = None,
                 skill_index: Optional[object] = None) -> None:
        self._root = Path(roles_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._atoms = atom_registry
        self._skills_dir = Path(skills_dir) if skills_dir is not None else None
        self._skill_index = skill_index
        # #6 并发:角色 7 文件是磁盘上的 read-modify-write;多协作并行 sediment/编辑同一角色不加锁会
        # 互相盖(如两次 create_atom 同时 add_atom → 少一个)。RLock 护住所有写文件的方法(见各方法)。
        self._lock = threading.RLock()

    def _known_skills(self) -> Optional[set[str]]:
        """技能库已知技能名集合(用于 use-not-own 校验);无注入源 → None(跳过校验)。

        优先 skill_index(内存、快);否则扫 skills_dir。两者都没 → 不校验(宽松,不挡建角色)。
        """
        names: set[str] = set()
        idx = self._skill_index
        if idx is not None:
            try:
                for entry in idx.all():
                    nm = getattr(entry, "name", None)
                    if nm:
                        names.add(nm)
                if names or len(idx) == 0:
                    # 有索引就以它为准(空索引=确实没技能,不回退扫盘冒充)
                    return names
            except Exception:
                pass
        if self._skills_dir is not None and self._skills_dir.is_dir():
            for p in sorted(self._skills_dir.glob("*/SKILL.md")):
                names.add(p.parent.name)
            # 系统技能(包内只读,如 data-analyst / file-butler)在扫盘兜底路径也算"已知"——
            # skill_index 路径本就把 system_skills 扫进索引(SkillIndex.rebuild_from_disk),
            # 两条校验路径必须同语义;否则 --no-llm / 无索引时原住民带系统技能入住会被误拒。
            try:
                from karvyloop.registry.skills import system_skills_dir
                for p in sorted(system_skills_dir().glob("*/SKILL.md")):
                    names.add(p.parent.name)
            except Exception:
                pass
            return names
        return None

    def _validate_skills(self, skill_ids: list[str]) -> None:
        known = self._known_skills()
        if known is None:
            return  # 无校验源 → 宽松放过(导入/结晶前也能先声明)
        missing = [s for s in skill_ids if s not in known]
        if missing:
            raise UnknownSkillError(
                f"这些技能还不在技能库:{missing}(先结晶,或从第三方导入)"
            )

    @property
    def root(self) -> Path:
        """角色库根目录(外部 agent 导入物化到这下面 → 直接进库)。"""
        return self._root

    # ---- 建(物化一个合法 agent 目录)----
    @_locked
    def create(
        self,
        role_id: str,
        *,
        identity: str = "",
        soul: str = "",
        user_desc: str = "",
        atom_ids: Optional[list[str]] = None,
        skill_ids: Optional[list[str]] = None,  # 角色随身技能(引用技能库;绑定即生效)
        nickname: str = "",   # brick4:花名(进某域时的人名)
        title: str = "",      # brick4:职务
        model: str = "",      # 角色级模型引用(空=默认)
    ) -> RoleView:
        rid = (role_id or "").strip()
        if not rid:
            raise ValueError("role_id(角色名)不能为空")
        if not _ROLE_ID_RE.match(rid):
            raise ValueError(f"role_id「{rid}」只能含字母/数字/下划线/连字符")
        d = self._root / rid
        if d.exists():
            raise DuplicateRoleError(f"角色「{rid}」已存在")
        picks = list(atom_ids or [])
        skills = list(skill_ids or [])
        # 甲:引的原子必须在公共原子库(缺 → 先买糖)
        if self._atoms is not None:
            missing = [a for a in picks if self._atoms.get(a) is None]
            if missing:
                raise UnknownAtomError(
                    f"这些原子还没在公共原子库:{missing}(先去原子库建,或用'就地建'入口)"
                )
        # 同:引的技能必须在技能库(use-not-own)
        self._validate_skills(skills)
        # 物化 7 文件;IDENTITY/SOUL/USER 由用户填(创建时有意义的);
        # MEMORY/COMMITMENT/VERIFY 是**运行时**文件(记忆随用而长、承诺在 pursuit 时、验证在判定时),
        # 不是创建时输入 —— 先 stub,由运行时填,不是"没写完"。
        filled = {"IDENTITY": identity, "SOUL": soul, "USER": user_desc}
        d.mkdir(parents=True)
        for slot in SLOT_NAMES:
            fname = _slot_filename(slot)
            if slot == "COMPOSITION":
                content = _composition_yaml(rid, picks, skills)
            elif slot in filled:
                body = (filled[slot] or "").strip() or "(待充实)"
                content = f"# {slot}\n\n{body}\n"
            elif slot == "COMMITMENT":
                # docs/02 §15.1.5:每个 role 都 seed 系统默认「尽责下属」协作契约
                # (可见可编、按 role/域可覆盖)。三入口共用这一条 seed(系统默认创建 +
                # LLM 导入都走 RoleRegistry.create;v0 导入走 planner._COMMITMENT_SYNTH)。
                from karvyloop.paradigm.contract import seed_commitment_md
                content = seed_commitment_md()
            else:
                content = _stub_slot(slot)
            (d / fname).write_text(content, encoding="utf-8")
        # brick4:花名/职务存进 profile.json(身份显示用,与七魂分开)
        import json as _json
        nn, tt, mdl = nickname.strip(), title.strip(), (model or "").strip()
        (d / "profile.json").write_text(
            _json.dumps({"nickname": nn, "title": tt, "model": mdl}, ensure_ascii=False), encoding="utf-8")
        return RoleView(rid, identity.strip(), picks, d, nickname=nn, title=tt, model=mdl,
                        skill_ids=skills)

    # ---- 读 ----
    @_locked
    def update(self, role_id: str, *, identity: Optional[str] = None,
               model: Optional[str] = None, nickname: Optional[str] = None,
               title: Optional[str] = None,
               skill_ids: Optional[list[str]] = None,
               atom_ids: Optional[list[str]] = None) -> Optional["RoleView"]:
        """编辑角色(P0 审计:此前写错只能删重建)。只改传入字段:identity(人格)/ model / 花名 / 职务 /
        skill_ids(随身技能)/ atom_ids(可用原子)。不存在返 None。七魂里只让改 IDENTITY;MEMORY/COMMITMENT
        等运行时文件走 update_soul 不动这里。改 skill_ids/atom_ids 时重写 COMPOSITION.yaml(未传的那段保留)。"""
        rid = (role_id or "").strip()
        d = self._root / rid
        if not d.exists():
            return None
        if skill_ids is not None or atom_ids is not None:
            comp = d / "COMPOSITION.yaml"
            comp_txt = comp.read_text(encoding="utf-8") if comp.exists() else ""
            cur_atoms = _ATOM_REF_RE.findall(comp_txt)
            cur_skills = _SKILL_REF_RE.findall(comp_txt)
            new_atoms = list(atom_ids) if atom_ids is not None else cur_atoms
            new_skills = list(skill_ids) if skill_ids is not None else cur_skills
            if skill_ids is not None:
                self._validate_skills(new_skills)
            comp.write_text(_composition_yaml(rid, new_atoms, new_skills), encoding="utf-8")
        if identity is not None:
            body = (identity or "").strip() or "(待充实)"
            (d / _slot_filename("IDENTITY")).write_text(f"# IDENTITY\n\n{body}\n", encoding="utf-8")
        if model is not None or nickname is not None or title is not None:
            import json as _json
            prof = {"nickname": "", "title": "", "model": ""}
            pf = d / "profile.json"
            if pf.exists():
                try:
                    prof.update(_json.loads(pf.read_text(encoding="utf-8")))
                except Exception:
                    pass
            if model is not None:
                prof["model"] = (model or "").strip()
            if nickname is not None:
                prof["nickname"] = (nickname or "").strip()
            if title is not None:
                prof["title"] = (title or "").strip()
            pf.write_text(_json.dumps(prof, ensure_ascii=False), encoding="utf-8")
        return self.get(rid)

    @_locked
    def rewrite_atom_refs(self, role_id: str, mapping: dict) -> bool:
        """把 COMPOSITION 的原子引用按 mapping(old_id→new_id)改写 + 去重(原子语义合并用,§11.2)。
        保留 skills 段不动。真改了返 True;角色不在 / 没引到任何 old → False(幂等)。"""
        rid = (role_id or "").strip()
        comp = self._root / rid / "COMPOSITION.yaml"
        if not comp.exists():
            return False
        txt = comp.read_text(encoding="utf-8")
        cur_atoms = _ATOM_REF_RE.findall(txt)
        cur_skills = _SKILL_REF_RE.findall(txt)
        new_atoms: list[str] = []
        seen: set[str] = set()
        changed = False
        for a in cur_atoms:
            na = mapping.get(a, a)
            if na != a:
                changed = True
            if na not in seen:
                seen.add(na)
                new_atoms.append(na)
        if not changed:
            return False
        comp.write_text(_composition_yaml(rid, new_atoms, list(cur_skills)), encoding="utf-8")
        return True

    @_locked
    def add_atom(self, role_id: str, atom_id: str) -> bool:
        """把一个原子加进角色 COMPOSITION 的 atoms 段(docs/02 §15.5 沉淀:自造 atom 被认可后入 composition)。

        已在则 no-op。返回是否真加了。不存在的角色返 False。
        """
        rid = (role_id or "").strip()
        aid = (atom_id or "").strip()
        if not rid or not aid:
            return False
        comp = self._root / rid / "COMPOSITION.yaml"
        if not comp.exists():
            return False
        txt = comp.read_text(encoding="utf-8")
        cur_atoms = _ATOM_REF_RE.findall(txt)
        if aid in cur_atoms:
            return False
        cur_skills = _SKILL_REF_RE.findall(txt)  # 保留 skills 段不丢
        comp.write_text(_composition_yaml(rid, cur_atoms + [aid], cur_skills), encoding="utf-8")
        return True

    # ---- 范式可见可编(docs/00 §2.4 / docs/02 §15.1.5;让用户看见+能改七层范式,不再 write-once)----
    # 用户可编辑的灵魂层:IDENTITY(人设)/SOUL(性格原则)/USER(服务对象)/COMMITMENT(契约/承诺)/
    # VERIFY(验证标准)。MEMORY=运行时(只读展示)、COMPOSITION=走 atom/skill 编辑,不在此。
    _EDITABLE_SOUL_SLOTS: tuple[str, ...] = ("IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY")

    def _slot_body(self, role_id: str, slot: str) -> str:
        """读一个槽的**正文**(剥掉 create 写的 `# SLOT` 头),给可见可编的干净内容。"""
        p = self._root / role_id / _slot_filename(slot)
        if not p.exists():
            return ""
        raw = p.read_text(encoding="utf-8").strip()
        head = f"# {slot}"
        if raw.startswith(head):
            raw = raw[len(head):].lstrip("\n").strip()
        return raw

    def read_paradigm(self, role_id: str) -> Optional[dict]:
        """读一个角色的**完整七层范式**(给编辑页看见范式是什么)。不存在 → None。"""
        d = self._root / role_id
        comp = d / "COMPOSITION.yaml"
        if not comp.exists():
            return None
        comp_txt = comp.read_text(encoding="utf-8")
        out: dict = {
            "role_id": role_id,
            "atom_ids": _ATOM_REF_RE.findall(comp_txt),
            "skill_ids": _SKILL_REF_RE.findall(comp_txt),
            "editable_slots": list(self._EDITABLE_SOUL_SLOTS),
        }
        for slot in ("IDENTITY", "SOUL", "USER", "MEMORY", "COMMITMENT", "VERIFY"):
            out[slot.lower()] = self._slot_body(role_id, slot)
        return out

    @_locked
    def update_soul(self, role_id: str, slot: str, text: str) -> bool:
        """编辑一个**可编辑灵魂槽**(SOUL/USER/COMMITMENT/VERIFY/IDENTITY)。不再 write-once。

        slot 非法(MEMORY/COMPOSITION/未知)或角色不存在 → False。空文本 → 回退 stub(保七文件齐性)。
        """
        s = (slot or "").strip().upper()
        if s not in self._EDITABLE_SOUL_SLOTS:
            return False
        d = self._root / role_id
        if not (d / "COMPOSITION.yaml").exists():
            return False
        body = (text or "").strip() or "(待充实)"
        (d / _slot_filename(s)).write_text(f"# {s}\n\n{body}\n", encoding="utf-8")
        return True

    def get(self, role_id: str) -> Optional[RoleView]:
        d = self._root / role_id
        comp = d / "COMPOSITION.yaml"
        ident = d / "IDENTITY.md"
        if not comp.exists():
            return None
        comp_txt = comp.read_text(encoding="utf-8")
        atom_ids = _ATOM_REF_RE.findall(comp_txt)
        skill_ids = _SKILL_REF_RE.findall(comp_txt)
        identity = ""
        if ident.exists():
            txt = ident.read_text(encoding="utf-8")
            # 去掉 "# IDENTITY" 标题行,取正文
            identity = re.sub(r"^#\s*IDENTITY\s*\n+", "", txt).strip()
        # brick4:读花名/职务/模型(profile.json;无则空,display_name 回退 role_id)
        nickname = title = model = ""
        prof = d / "profile.json"
        if prof.exists():
            try:
                import json as _json
                pd = _json.loads(prof.read_text(encoding="utf-8"))
                nickname = (pd.get("nickname") or "").strip()
                title = (pd.get("title") or "").strip()
                model = (pd.get("model") or "").strip()
            except Exception:
                pass
        return RoleView(role_id, identity, atom_ids, d, nickname=nickname, title=title,
                        model=model, skill_ids=skill_ids)

    def list_all(self) -> list[RoleView]:
        out: list[RoleView] = []
        for child in sorted(self._root.iterdir()):
            if child.is_dir() and (child / "COMPOSITION.yaml").exists():
                v = self.get(child.name)
                if v is not None:
                    out.append(v)
        return out

    def __len__(self) -> int:
        return sum(
            1 for c in self._root.iterdir()
            if c.is_dir() and (c / "COMPOSITION.yaml").exists()
        )

    # ---- 删 ----
    def remove(self, role_id: str) -> bool:
        d = self._root / role_id
        if d.is_dir() and (d / "COMPOSITION.yaml").exists():
            shutil.rmtree(d)
            return True
        return False


__all__ = [
    "RoleRegistry", "RoleView", "SLOT_NAMES",
    "DuplicateRoleError", "UnknownAtomError", "UnknownSkillError",
]
