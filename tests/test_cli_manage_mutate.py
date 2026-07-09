"""管理面 CLI create/mutate 验收(role create/rm · domain create/archive ·
schedule add/rm/toggle · skill import)。

补 test_cli_manage.py(那份只覆盖 read + memory add):这份覆盖新增的 create/mutate 动词。
纪律:走**真生产路径** —— 用真 registry seed 临时实例,再用 main([...]) 跑 CLI;断言真落盘、
--yes 门、not-found/空 退出码、parse 结构、en/zh help。schedule add 的 NL→LLM 用注入的假解析器
(不触网、不烧 token),验的是"解析结果 → 落库"那段接线。
"""
from __future__ import annotations

import argparse
import json

import pytest

from karvyloop.cli.main import main, _build_parser


# ---- 临时实例 fixture(空实例即可,各测按需 seed 后端) ----

@pytest.fixture()
def instance(tmp_path):
    root = tmp_path / ".karvyloop"
    root.mkdir(parents=True)
    cfg = root / "config.yaml"
    cfg.write_text("models: {}\n", encoding="utf-8")
    return {"root": root, "cfg": str(cfg)}


def _cfg(instance):
    return ["--config", instance["cfg"]]


# 非交互:测试进程 stdin 非 TTY,故 create/mutate 无 --yes 必被拒(H2A 门)。
# 显式把 isatty 钉成 False,不依赖 runner 环境。
@pytest.fixture(autouse=True)
def _no_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


# ============ role create / rm ============

def test_role_create_persists_and_shows(instance, capsys):
    assert main(["role", "create", "--id", "tester", "--identity", "builds things",
                 "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["id"] == "tester" and d["identity"] == "builds things"
    # 真落盘:角色目录 + 7 文件之一存在
    assert (instance["root"] / "roles" / "tester" / "COMPOSITION.yaml").exists()
    # show 能读回
    assert main(["role", "show", "tester"] + _cfg(instance)) == 0
    assert "tester" in capsys.readouterr().out


def test_role_create_requires_yes_off_tty(instance):
    """非 TTY 且无 --yes → 拒(rc=1),绝不悄悄建。"""
    assert main(["role", "create", "--id", "ghost", "--identity", "x"] + _cfg(instance)) == 1
    assert not (instance["root"] / "roles" / "ghost").exists()


def test_role_create_duplicate_exit1(instance, capsys):
    assert main(["role", "create", "--id", "dup", "--yes"] + _cfg(instance)) == 0
    capsys.readouterr()
    # 第二次同 id → 后端 DuplicateRoleError → rc=1
    assert main(["role", "create", "--id", "dup", "--yes"] + _cfg(instance)) == 1


def test_role_rm_yes(instance, capsys):
    assert main(["role", "create", "--id", "gone", "--yes"] + _cfg(instance)) == 0
    capsys.readouterr()
    assert main(["role", "rm", "gone", "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["removed"] == "gone"
    assert not (instance["root"] / "roles" / "gone").exists()


def test_role_rm_not_found_exit1(instance):
    assert main(["role", "rm", "nope", "--yes"] + _cfg(instance)) == 1


def test_role_rm_requires_yes_off_tty(instance):
    assert main(["role", "create", "--id", "keep", "--yes"] + _cfg(instance)) == 0
    # 无 --yes,非 TTY → 拒,角色仍在
    assert main(["role", "rm", "keep"] + _cfg(instance)) == 1
    assert (instance["root"] / "roles" / "keep").exists()


# ============ domain create / archive ============

def test_domain_create_persists(instance, capsys):
    assert main(["domain", "create", "--name", "Sales", "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["name"] == "Sales" and d["lifecycle"] == "active"
    # 真落盘到 domains.json
    persisted = json.loads((instance["root"] / "domains.json").read_text(encoding="utf-8"))
    assert any(x["name"] == "Sales" for x in persisted)


def test_domain_create_subdomain_inherits_value_and_deontic(instance, capsys):
    """带 --parent → create_child:子域继承父的 value.md(D5 只加不删)。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.domain.store import DomainStore
    reg = BusinessDomainRegistry()
    store = DomainStore(instance["root"] / "domains.json")
    parent = reg.create("Parent", "user:ch", value_md_raw="# 价值观\n\nhonesty first\n")
    store.save_all(list(reg.list_all()))

    assert main(["domain", "create", "--name", "Child", "--parent", parent.id,
                 "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["parent_id"] == parent.id
    assert "honesty first" in d["value_md"]   # 继承 value.md


def test_domain_create_bad_parent_exit1(instance):
    assert main(["domain", "create", "--name", "Orphan", "--parent", "dom-xxxx",
                 "--yes"] + _cfg(instance)) == 1


def test_domain_archive_soft_deletes(instance, capsys):
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.domain.store import DomainStore
    reg = BusinessDomainRegistry()
    store = DomainStore(instance["root"] / "domains.json")
    dom = reg.create("Ops", "user:ch")
    store.save_all(list(reg.list_all()))

    assert main(["domain", "archive", dom.id, "--yes"] + _cfg(instance)) == 0
    capsys.readouterr()
    persisted = json.loads((instance["root"] / "domains.json").read_text(encoding="utf-8"))
    assert [x for x in persisted if x["id"] == dom.id][0]["lifecycle"] == "archived"


def test_domain_archive_not_found_exit1(instance):
    assert main(["domain", "archive", "dom-xxxx", "--yes"] + _cfg(instance)) == 1


def test_domain_create_requires_yes_off_tty(instance):
    assert main(["domain", "create", "--name", "NoYes"] + _cfg(instance)) == 1
    assert not (instance["root"] / "domains.json").exists()


# ============ schedule add / rm / toggle ============

def _seed_schedule(instance, cron="0 8 * * *", intent="morning brief"):
    from karvyloop.karvy.scheduler import SchedulerStore
    st = SchedulerStore(instance["root"] / "schedules.json")
    tk = st.add(cron, intent, title="Brief")
    return tk.id


def test_schedule_add_nl_to_cron_json(instance, capsys, monkeypatch):
    """NL→cron:注入假解析器(不触网),验解析结果落库。"""
    # make_schedule_parser 返回一个 (desc, now) -> dict 的闭包;直接替身
    import karvyloop.cli.manage as M
    monkeypatch.setattr(M, "_build_gateway", lambda root, cfg: object())  # 非 None 即过门
    monkeypatch.setattr(
        "karvyloop.karvy.schedule_parser.make_schedule_parser",
        lambda gw, model_ref="": (lambda desc, now="": {
            "cron": "0 8 * * *", "intent": "summarize progress", "title": "Daily"}),
    )
    assert main(["schedule", "add", "every day at 8am summarize progress",
                 "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["cron"] == "0 8 * * *" and d["intent"] == "summarize progress"
    # 真落盘
    from karvyloop.karvy.scheduler import SchedulerStore
    tasks = SchedulerStore(instance["root"] / "schedules.json").all()
    assert any(t.cron == "0 8 * * *" for t in tasks)


def test_schedule_add_no_llm_exit1(instance):
    """没配模型 → NL→cron 起不来 → rc=1(不瞎编)。"""
    # _build_gateway 对空 config(models: {}) 起 gateway,但真解析要模型;
    # 这里直接把 gateway 钉成 None 模拟没配。
    import karvyloop.cli.manage as M
    from unittest.mock import patch
    with patch.object(M, "_build_gateway", lambda root, cfg: None):
        assert main(["schedule", "add", "every day at 8", "--yes"] + _cfg(instance)) == 1


def test_schedule_add_not_understood_exit1(instance, monkeypatch):
    """解析器返 None(没听懂时间)→ rc=1。"""
    import karvyloop.cli.manage as M
    monkeypatch.setattr(M, "_build_gateway", lambda root, cfg: object())
    monkeypatch.setattr(
        "karvyloop.karvy.schedule_parser.make_schedule_parser",
        lambda gw, model_ref="": (lambda desc, now="": None),
    )
    assert main(["schedule", "add", "sometime maybe", "--yes"] + _cfg(instance)) == 1


def test_schedule_rm_yes(instance, capsys):
    tid = _seed_schedule(instance)
    assert main(["schedule", "rm", tid, "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["removed"] == tid
    from karvyloop.karvy.scheduler import SchedulerStore
    assert SchedulerStore(instance["root"] / "schedules.json").get(tid) is None


def test_schedule_rm_not_found_exit1(instance):
    assert main(["schedule", "rm", "nope", "--yes"] + _cfg(instance)) == 1


def test_schedule_toggle_off_then_on(instance, capsys):
    tid = _seed_schedule(instance)
    from karvyloop.karvy.scheduler import SchedulerStore
    store = lambda: SchedulerStore(instance["root"] / "schedules.json")

    assert main(["schedule", "toggle", tid, "--off", "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["enabled"] is False
    assert store().get(tid).enabled is False

    assert main(["schedule", "toggle", tid, "--on", "--yes", "--json"] + _cfg(instance)) == 0
    assert store().get(tid).enabled is True


def test_schedule_toggle_requires_on_or_off():
    """--on/--off 互斥且必填 → 两个都不给 argparse 报错。"""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["schedule", "toggle", "t1"])


def test_schedule_toggle_not_found_exit1(instance):
    assert main(["schedule", "toggle", "nope", "--off", "--yes"] + _cfg(instance)) == 1


# ============ skill import ============

def _make_local_skill(tmp_path, name="my-skill", desc="does a thing"):
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n# body\n", encoding="utf-8")
    return src


def test_skill_import_local_folder(instance, capsys, tmp_path):
    src = _make_local_skill(tmp_path)
    assert main(["skill", "import", str(src), "--yes", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["name"] == "my-skill" and d["untrusted"] is True
    # 真落进技能库
    assert (instance["root"] / "skills" / "my-skill" / "SKILL.md").exists()
    # 出现在 skill list
    assert main(["skill", "list", "--json"] + _cfg(instance)) == 0
    listing = json.loads(capsys.readouterr().out)
    assert any(e["name"] == "my-skill" for e in listing)


def test_skill_import_bad_source_exit1(instance, tmp_path):
    """指到一个没有 SKILL.md 的目录 → import_skill 拒 → rc=1。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["skill", "import", str(empty), "--yes"] + _cfg(instance)) == 1


def test_skill_import_requires_yes_off_tty(instance, tmp_path):
    src = _make_local_skill(tmp_path, name="noyes-skill")
    assert main(["skill", "import", str(src)] + _cfg(instance)) == 1
    assert not (instance["root"] / "skills" / "noyes-skill").exists()


# ============ no instance ============

def test_mutate_no_instance_exit1(tmp_path):
    missing = tmp_path / "gone" / "config.yaml"
    assert main(["role", "create", "--id", "x", "--yes", "--config", str(missing)]) == 1
    assert main(["domain", "create", "--name", "X", "--yes", "--config", str(missing)]) == 1


# ============ parse / dispatch 结构(新动词都解析出正确 cmd/subcmd) ============

@pytest.mark.parametrize("argv,cmd,sub", [
    (["role", "create", "--id", "x"], "role", "create"),
    (["role", "rm", "x"], "role", "rm"),
    (["domain", "create", "--name", "X"], "domain", "create"),
    (["domain", "archive", "d"], "domain", "archive"),
    (["schedule", "add", "q"], "schedule", "add"),
    (["schedule", "rm", "t"], "schedule", "rm"),
    (["schedule", "toggle", "t", "--on"], "schedule", "toggle"),
    (["skill", "import", "src"], "skill", "import"),
])
def test_parse_mutate_noun_verb(argv, cmd, sub):
    args = _build_parser().parse_args(argv)
    assert args.cmd == cmd and args.subcmd == sub


def test_role_create_requires_id_flag():
    """role create 不给 --id → argparse 报错(required)。"""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["role", "create", "--identity", "x"])


def test_domain_create_requires_name_flag():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["domain", "create", "--parent", "p"])


# ============ i18n:新动词 help 走双表 ============

def _role_subparser_help(parser) -> str:
    """挖出 `role` 名词下的子命令 help(create/rm 的 help 在这一层,不在顶层)。"""
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction) and "role" in act.choices:
            return act.choices["role"].format_help()
    raise AssertionError("no role subparser")


def test_mutate_help_localized_en_zh():
    from karvyloop import i18n
    i18n.set_locale(None)
    h_en = _role_subparser_help(_build_parser())
    assert "create a new role" in h_en
    i18n.set_locale("zh")
    h_zh = _role_subparser_help(_build_parser())
    assert "新建一个角色" in h_zh
    i18n.set_locale(None)
