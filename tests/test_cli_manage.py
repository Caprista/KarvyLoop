"""管理面 CLI 验收(名词-动词:role/domain/memory/skill/schedule/token)。

覆盖:每条子命令 parse + dispatch;--json 出机器 JSON;人读出表/摘要;
--config 指向的实例态被真正加载(不硬编码 ~/.karvyloop);create/mutate 的 --yes 门。

纪律:测试走**真生产路径**——用真 registry seed 一个临时实例,再用 main([...]) 跑 CLI。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from karvyloop.cli.main import main, _build_parser


# ---- 临时实例 fixture(用真后端 seed 一份可读实例态)----

@pytest.fixture()
def instance(tmp_path):
    root = tmp_path / ".karvyloop"
    root.mkdir(parents=True)
    cfg = root / "config.yaml"
    cfg.write_text("models: {}\n", encoding="utf-8")

    # 角色
    from karvyloop.roles.registry import RoleRegistry
    RoleRegistry(root / "roles").create(
        "engineer", identity="builds things", nickname="Zhang", title="PM")

    # 业务域
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.domain.store import DomainStore
    reg = BusinessDomainRegistry()
    store = DomainStore(root / "domains.json")
    dom = reg.create("MyDomain", "user:ch", member_query="role:engineer")
    store.save_all(list(reg.list_all()))

    # 定时任务
    from karvyloop.karvy.scheduler import SchedulerStore
    SchedulerStore(root / "schedules.json").add(
        "0 8 * * *", "morning brief", title="Morning brief")

    # token 账本
    from karvyloop.llm.token_ledger import TokenLedger
    led = TokenLedger(root / "tokens.db")
    led.record(source="drive", model="claude", input=100, output=50)
    led.record(source="forge", model="claude", input=200, output=20)
    led.close()

    return {"root": root, "cfg": str(cfg), "domain_id": dom.id}


def _cfg(instance):
    return ["--config", instance["cfg"]]


# ---- role ----

def test_role_list_human(instance, capsys):
    assert main(["role", "list"] + _cfg(instance)) == 0
    out = capsys.readouterr().out
    assert "engineer" in out and "Zhang" in out


def test_role_list_json_shape(instance, capsys):
    assert main(["role", "list", "--json"] + _cfg(instance)) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and data[0]["id"] == "engineer"
    assert "atom_ids" in data[0] and "skill_ids" in data[0]


def test_role_show_json(instance, capsys):
    assert main(["role", "show", "engineer", "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["id"] == "engineer" and d["identity"] == "builds things"


def test_role_show_not_found_exit1(instance, capsys):
    assert main(["role", "show", "nope"] + _cfg(instance)) == 1


# ---- domain ----

def test_domain_list_json(instance, capsys):
    assert main(["domain", "list", "--json"] + _cfg(instance)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["name"] == "MyDomain" and data[0]["lifecycle"] == "active"


def test_domain_show_human(instance, capsys):
    assert main(["domain", "show", instance["domain_id"]] + _cfg(instance)) == 0
    out = capsys.readouterr().out
    assert "MyDomain" in out and "role:engineer" in out


def test_domain_show_not_found_exit1(instance):
    assert main(["domain", "show", "dom-xxxx"] + _cfg(instance)) == 1


# ---- memory ----

def test_memory_add_yes_then_recall(instance, capsys):
    assert main(["memory", "add", "I like dark mode", "--yes"] + _cfg(instance)) == 0
    capsys.readouterr()
    assert main(["memory", "recall", "dark mode"] + _cfg(instance)) == 0
    out = capsys.readouterr().out
    assert "dark mode" in out


def test_memory_add_persists_to_disk(instance):
    """真落盘:add 后 beliefs.json 里有这条(走 MemoryManager.write → BeliefStore)。"""
    assert main(["memory", "add", "coffee no sugar", "--yes"] + _cfg(instance)) == 0
    beliefs = (instance["root"] / "beliefs.json").read_text(encoding="utf-8")
    assert "coffee no sugar" in beliefs


def test_memory_add_off_tty_requires_yes(instance, monkeypatch):
    """非 TTY 且无 --yes → 拒绝(rc=1),绝不悄悄写。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["memory", "add", "should not persist"] + _cfg(instance)) == 1
    bp = instance["root"] / "beliefs.json"
    if bp.exists():
        assert "should not persist" not in bp.read_text(encoding="utf-8")


def test_memory_recall_json_shape(instance, capsys):
    main(["memory", "add", "dark mode preferred", "--yes"] + _cfg(instance))
    capsys.readouterr()
    assert main(["memory", "recall", "dark mode", "--json", "--limit", "3"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["query"] == "dark mode" and isinstance(d["hits"], list)


# ---- skill ----

def test_skill_list_json_includes_system(instance, capsys):
    assert main(["skill", "list", "--json"] + _cfg(instance)) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    # 系统技能随包扫入(rebuild_from_disk 扫 system + user)
    assert any(e["source"] == "system" for e in data)
    assert all({"name", "source", "description"} <= set(e) for e in data)


# ---- schedule ----

def test_schedule_list_json(instance, capsys):
    assert main(["schedule", "list", "--json"] + _cfg(instance)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["cron"] == "0 8 * * *" and data[0]["intent"] == "morning brief"


# ---- token ----

@pytest.mark.parametrize("by", ["source", "model", "day"])
def test_token_report_by_dim_json(instance, capsys, by):
    assert main(["token", "report", "--by", by, "--json"] + _cfg(instance)) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["by"] == by
    assert d["rows"] and d["rows"][0]["total"] == d["rows"][0]["input"] + d["rows"][0]["output"]


def test_token_report_human_table(instance, capsys):
    assert main(["token", "report"] + _cfg(instance)) == 0
    out = capsys.readouterr().out
    assert "drive" in out and "forge" in out


# ---- no instance / empty instance ----

def test_no_instance_exit1(tmp_path, capsys):
    missing = tmp_path / "gone" / "config.yaml"
    assert main(["role", "list", "--config", str(missing)]) == 1


def test_empty_instance_lists_none(tmp_path, capsys):
    root = tmp_path / ".karvyloop"
    root.mkdir(parents=True)
    cfg = root / "config.yaml"
    cfg.write_text("models: {}\n", encoding="utf-8")
    assert main(["role", "list", "--config", str(cfg)]) == 0
    # --json 空实例 → 空数组(机器可读一致)
    assert main(["role", "list", "--json", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "[]" in out


# ---- parse/dispatch structure(所有名词-动词都能解析出正确 cmd/subcmd)----

@pytest.mark.parametrize("argv,cmd,sub", [
    (["role", "list"], "role", "list"),
    (["role", "show", "x"], "role", "show"),
    (["domain", "list"], "domain", "list"),
    (["domain", "show", "d"], "domain", "show"),
    (["memory", "recall", "q"], "memory", "recall"),
    (["memory", "add", "b"], "memory", "add"),
    (["skill", "list"], "skill", "list"),
    (["schedule", "list"], "schedule", "list"),
    (["token", "report"], "token", "report"),
])
def test_parse_noun_verb(argv, cmd, sub):
    args = _build_parser().parse_args(argv)
    assert args.cmd == cmd and args.subcmd == sub


def test_noun_without_verb_errors():
    """光给名词不给动词 → argparse 报错(required subparser)。"""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["role"])


# ---- i18n:管理面 help 走双表(parity 另由 test_i18n 锁)----

def test_manage_help_localized_en_zh(monkeypatch):
    from karvyloop import i18n
    i18n.set_locale(None)
    h_en = _build_parser().format_help()
    assert "manage roles" in h_en
    i18n.set_locale("zh")
    h_zh = _build_parser().format_help()
    assert "管理角色" in h_zh
    i18n.set_locale(None)
