"""cli/manage — 名词-动词管理面 CLI(role/domain/memory/skill/schedule/token）。

**为什么**:执行(run/chat)、生命周期(init/export/…)、可观测(status/doctor/…)都有 CLI,
但**管理面**(角色/域/记忆/技能/定时/token)此前只在 console REST 有,脚本/无头环境够不到。
本模块把这些既有后端搬上 gh 风格的名词-动词子命令:每条 read 命令都支持 `--json`,
create/mutate 在 TTY 上问确认、在非 TTY 上要 `--yes`(H2A 的 CLI 形态)。

**纪律**:
- **不造后端**:每条命令映射到一个既有后端函数/registry(见各 handler 头注)。
- **同一份实例态**:实例根 = config.yaml 的父目录(默认 `~/.karvyloop`),与 console
  `cmd_console` 接线的落盘路径一字不差(roles/ · domains.json · beliefs.json · skills/ ·
  schedules.json · tokens.db)。不硬编码另一套路径。
- **一套约定**:`--json` 出机器 JSON;人读模式出干净表/摘要;所有用户可见串走 i18n(en+zh)。
"""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Optional


# ---- 实例根解析(与 console cmd_console 落盘路径同源)----

def _instance_root(config_path: Optional[str]) -> Path:
    """实例根目录 = config.yaml 的父目录(默认 ~/.karvyloop)。

    console 的 cmd_console 用 `Path.home()/".karvyloop"` 作落盘根,并把 config 的父目录
    当概念缓存 base(entry.py concept_cache 那行)。这里统一:显式 --config → 用它的父目录
    (让 --config 指向别处的实例也对得上);未给 → ~/.karvyloop。二者对默认路径完全一致。
    """
    if config_path:
        return Path(config_path).expanduser().resolve().parent
    return Path.home() / ".karvyloop"


def _require_instance(root: Path, config_path: Optional[str]) -> bool:
    """实例根存在?不存在 → 写指引 stderr,返回 False(caller return 1)。"""
    # config 明确给了但父目录不存在,或默认根不存在 → 当没实例。roles/ 等子目录缺省是允许的
    # (空实例照样能 list 出"还没有"),只要根在。
    if root.exists():
        return True
    from karvyloop.i18n import t
    sys.stderr.write(t("cli.manage.no_instance", path=str(root)) + "\n")
    return False


# ---- registry 构造(不重写后端,只按 console 同款路径实例化)----

def _load_role_registry(root: Path):
    """RoleRegistry over ~/.karvyloop/roles(同 console entry.py:475)。原子库注入做"用不拥有"校验。"""
    from karvyloop.atoms.registry import AtomRegistry, AtomStore
    from karvyloop.roles.registry import RoleRegistry
    atom_registry = AtomRegistry(store=AtomStore(root / "atoms.json"))
    return RoleRegistry(
        root / "roles",
        atom_registry=atom_registry,
        skills_dir=root / "skills",
    )


def _load_domain_registry(root: Path):
    """BusinessDomainRegistry + DomainStore.load_all()（同 console entry.py:458-462,restore 保原 id）。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.domain.store import DomainStore
    reg = BusinessDomainRegistry()
    store = DomainStore(root / "domains.json")
    for d in store.load_all():
        reg.restore(d)
    return reg


def _load_memory(root: Path):
    """MemoryManager over ~/.karvyloop/beliefs.json（同 console entry.py:531-534）。"""
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.concepts import ConceptCache
    return MemoryManager(
        store=BeliefStore(root / "beliefs.json"),
        concept_cache=ConceptCache(root / "concept_cache.json"),
    )


def _load_skill_index(root: Path):
    """SkillIndex.rebuild_from_disk(skills_dir)（同 MainLoop.bootstrap;扫系统 + 用户目录）。"""
    from karvyloop.crystallize.skill_index import SkillIndex
    idx = SkillIndex()
    idx.rebuild_from_disk(root / "skills")
    return idx


def _load_scheduler(root: Path):
    """SchedulerStore over ~/.karvyloop/schedules.json（karvy/scheduler.py 生产路径）。"""
    from karvyloop.karvy.scheduler import SchedulerStore
    return SchedulerStore(root / "schedules.json")


def _load_token_ledger(root: Path):
    """TokenLedger over ~/.karvyloop/tokens.db（llm/token_ledger.py 生产路径,只读查询）。"""
    from karvyloop.llm.token_ledger import TokenLedger
    return TokenLedger(root / "tokens.db")


# ---- 输出小工具 ----

def _emit_json(obj) -> int:
    sys.stdout.write(_json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return 0


def _confirm_or_yes(prompt: str, yes: bool) -> bool:
    """create/mutate 的 H2A(CLI 形态):TTY 上 y/N 确认;非 TTY 要 --yes,否则拒。"""
    if yes:
        return True
    if not sys.stdin.isatty():
        from karvyloop.i18n import t
        sys.stderr.write(t("cli.manage.needs_yes") + "\n")
        return False
    try:
        sys.stdout.write(prompt + " [y/N] ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


# ============ role(over RoleRegistry.list_all / get）============

def cmd_role_list(*, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    roles = _load_role_registry(root).list_all()
    if json_output:
        return _emit_json([r.to_dict() for r in roles])
    if not roles:
        print(t("cli.manage.role_none"))
        return 0
    for r in roles:
        name = r.display_name()
        extra = f"  [{len(r.atom_ids)} atoms, {len(r.skill_ids)} skills]"
        print(f"  {r.id:<24} {name}{extra}")
    return 0


def cmd_role_show(role_id: str, *, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    view = _load_role_registry(root).get(role_id)
    if view is None:
        sys.stderr.write(t("cli.manage.role_not_found", id=role_id) + "\n")
        return 1
    if json_output:
        return _emit_json(view.to_dict())
    print(f"id:        {view.id}")
    print(f"name:      {view.display_name()}")
    if view.model:
        print(f"model:     {view.model}")
    print(f"identity:  {view.identity or '(待充实)'}")
    print(f"atoms:     {', '.join(view.atom_ids) or '-'}")
    print(f"skills:    {', '.join(view.skill_ids) or '-'}")
    print(f"path:      {view.path}")
    return 0


# ============ domain(over BusinessDomainRegistry.list_all / get）============

def _domain_dict(d) -> dict:
    return {
        "id": d.id, "name": d.name, "created_by": d.created_by,
        "created_at": d.created_at, "lifecycle": d.lifecycle,
        "member_query": d.member_query, "parent_id": d.parent_id,
        "value_md": d.value_md.text,
    }


def cmd_domain_list(*, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    domains = _load_domain_registry(root).list_all()
    if json_output:
        return _emit_json([_domain_dict(d) for d in domains])
    if not domains:
        print(t("cli.manage.domain_none"))
        return 0
    for d in domains:
        print(f"  {d.id:<16} {d.name:<24} [{d.lifecycle}]")
    return 0


def cmd_domain_show(domain_id: str, *, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    d = _load_domain_registry(root).get(domain_id)
    if d is None:
        sys.stderr.write(t("cli.manage.domain_not_found", id=domain_id) + "\n")
        return 1
    if json_output:
        return _emit_json(_domain_dict(d))
    print(f"id:           {d.id}")
    print(f"name:         {d.name}")
    print(f"lifecycle:    {d.lifecycle}")
    print(f"created_by:   {d.created_by}")
    print(f"member_query: {d.member_query or '-'}")
    if d.parent_id:
        print(f"parent_id:    {d.parent_id}")
    print(f"value.md:     {d.value_md.text.strip() or '(空)'}")
    return 0


# ============ memory(over MemoryManager.recall_block / write）============

def cmd_memory_recall(query: str, *, config_path: Optional[str] = None,
                      json_output: bool = False, limit: int = 8, scope: str = "personal") -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    mem = _load_memory(root)
    sc = scope if scope in ("personal", "domain") else "personal"
    if json_output:
        # 结构化召回:走 recall_block 的 explain_sink（同一 grep/overlap 路径,无向量)拿命中明细。
        sink: list = []
        mem.recall_block(query, scope=sc, limit=max(0, int(limit)), explain_sink=sink)
        return _emit_json({"query": query, "scope": sc, "hits": sink})
    block = mem.recall_block(query, scope=sc, limit=max(0, int(limit)))
    if not block.strip():
        print(t("cli.manage.memory_none"))
        return 0
    print(block)
    return 0


def cmd_memory_add(belief_text: str, *, config_path: Optional[str] = None,
                   scope: str = "personal", yes: bool = False, json_output: bool = False) -> int:
    """把一句话沉进个人知识库。构造 Belief 与 console 手动沉淀同款(provenance 带 source/ts)。"""
    import time as _time
    from karvyloop.i18n import t
    from karvyloop.schemas import Belief
    text = (belief_text or "").strip()
    if not text:
        sys.stderr.write(t("cli.manage.aborted") + "\n")
        return 1
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    # create/mutate:H2A（TTY 确认 / 非 TTY --yes)
    if not _confirm_or_yes(t("cli.manage.confirm_add"), yes):
        sys.stderr.write(t("cli.manage.aborted") + "\n")
        return 1
    mem = _load_memory(root)
    sc = scope if scope in ("personal", "domain") else "personal"
    now = _time.time()
    belief = Belief(
        content=text,
        provenance={"source": "cli", "agent": "user", "kind": "manual", "ts": now},
        freshness_ts=now,
        scope=sc,
    )
    ok = mem.write(belief)
    if not ok:
        err = getattr(mem, "persist_error", "") or "unknown"
        sys.stderr.write(t("cli.manage.memory_add_failed", error=err) + "\n")
        # 内存写了但没落盘 → 非零退出码,脚本可判(fail-loud,不假装成功)
        if json_output:
            _emit_json({"ok": False, "persisted": False, "error": err, "content": text})
        return 1
    if json_output:
        return _emit_json({"ok": True, "persisted": True, "content": text, "scope": sc})
    print(t("cli.manage.memory_added", belief=text))
    return 0


# ============ skill(over SkillIndex.all）============

def cmd_skill_list(*, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    entries = _load_skill_index(root).all()
    entries = sorted(entries, key=lambda e: (e.source, e.name))
    if json_output:
        return _emit_json([
            {"name": e.name, "description": e.description, "when_to_use": e.when_to_use,
             "scope": e.scope, "source": e.source, "tags": list(e.tags), "path": e.path}
            for e in entries
        ])
    if not entries:
        print(t("cli.manage.skill_none"))
        return 0
    for e in entries:
        desc = (e.description or e.when_to_use or "").replace("\n", " ")[:60]
        print(f"  {e.name:<28} [{e.source}] {desc}")
    return 0


# ============ schedule(over SchedulerStore.all）============

def cmd_schedule_list(*, config_path: Optional[str] = None, json_output: bool = False) -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    tasks = _load_scheduler(root).all()
    if json_output:
        return _emit_json([tk.to_dict() for tk in tasks])
    if not tasks:
        print(t("cli.manage.schedule_none"))
        return 0
    for tk in tasks:
        flag = "on " if tk.enabled else "off"
        title = tk.title or tk.intent[:40]
        print(f"  {tk.id:<14} [{flag}] {tk.cron:<18} {title}")
    return 0


# ============ token(over TokenLedger.by_source / by_model / by_day）============

def cmd_token_report(*, config_path: Optional[str] = None, json_output: bool = False,
                     by: str = "source") -> int:
    from karvyloop.i18n import t
    root = _instance_root(config_path)
    if not _require_instance(root, config_path):
        return 1
    led = _load_token_ledger(root)
    dim = by if by in ("source", "model", "day") else "source"
    rows = {"source": led.by_source, "model": led.by_model, "day": led.by_day}[dim]()
    if json_output:
        return _emit_json({"by": dim, "rows": rows})
    if not rows:
        print(t("cli.manage.token_none"))
        return 0
    print(f"  {dim:<28} {'input':>10} {'output':>10} {'total':>10} {'calls':>7}")
    for r in rows:
        print(f"  {str(r.get(dim, '')):<28} {r['input']:>10} {r['output']:>10} "
              f"{r['total']:>10} {r['calls']:>7}")
    return 0


__all__ = [
    "cmd_role_list", "cmd_role_show",
    "cmd_domain_list", "cmd_domain_show",
    "cmd_memory_recall", "cmd_memory_add",
    "cmd_skill_list",
    "cmd_schedule_list",
    "cmd_token_report",
]
