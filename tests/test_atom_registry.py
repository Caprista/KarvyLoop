"""公共原子库验收(P1,拍 9.5 #3)。

甲(原子=公共池,角色用不拥有;建角色缺原子就就地建、落公共库)。锁:CRUD + 持久化 + 重名拦。
"""
from __future__ import annotations

import pytest

from karvyloop.atoms.registry import AtomRegistry, AtomStore, DuplicateAtomError
from karvyloop.schemas.atom import AtomSpec


def test_create_and_get():
    reg = AtomRegistry()
    a = reg.create("web_search", "task", "搜网并返回结果", tools=["run_command"])
    assert isinstance(a, AtomSpec)
    assert a.id == "web_search" and a.kind == "task"
    assert reg.get("web_search").tools == ["run_command"]
    assert len(reg) == 1


def test_create_defaults_object_schema():
    reg = AtomRegistry()
    a = reg.create("prd_writer", "task", "写 PRD")
    assert a.input_schema == {"type": "object"}
    assert a.output_schema == {"type": "object"}
    assert a.is_read_only is False  # fail-closed 默认


def test_duplicate_id_rejected():
    """原子是公共库 → 重名必须拦(不偷偷覆盖)。"""
    reg = AtomRegistry()
    reg.create("search", "task", "v1")
    with pytest.raises(DuplicateAtomError):
        reg.create("search", "task", "v2")


def test_empty_id_and_bad_kind_rejected():
    reg = AtomRegistry()
    with pytest.raises(ValueError):
        reg.create("", "task", "x")
    with pytest.raises(ValueError):
        reg.create("z", "neither", "x")


def test_non_composition_safe_id_rejected():
    """带连字符/空格的 id 在 COMPOSITION.yaml `atom: x` 里引用不到 → 拦。"""
    reg = AtomRegistry()
    with pytest.raises(ValueError):
        reg.create("web-search", "task", "x")  # 连字符
    with pytest.raises(ValueError):
        reg.create("web search", "task", "x")  # 空格
    # 下划线 ok
    assert reg.create("web_search", "task", "x").id == "web_search"


def test_remove():
    reg = AtomRegistry()
    reg.create("tmp", "task", "x")
    assert reg.remove("tmp") is True
    assert reg.get("tmp") is None
    assert reg.remove("tmp") is False  # 已无


def test_daemon_kind():
    reg = AtomRegistry()
    a = reg.create("market_poll", "daemon", "定时采集市场数据")
    assert a.kind == "daemon"


def test_persistence_roundtrip(tmp_path):
    """存盘 → 新进程(新 registry)读回(镜像默认持久,§2.1)。"""
    p = tmp_path / "atoms.json"
    store1 = AtomStore(p)
    reg1 = AtomRegistry(store=store1)
    reg1.create("web_search", "task", "搜网", tools=["run_command"], is_read_only=True)
    reg1.create("market_poll", "daemon", "采集")
    # 新 registry 从同一文件读回
    reg2 = AtomRegistry(store=AtomStore(p))
    assert len(reg2) == 2
    got = reg2.get("web_search")
    assert got is not None and got.is_read_only is True and got.tools == ["run_command"]
    assert reg2.get("market_poll").kind == "daemon"


def test_persistence_remove_persists(tmp_path):
    p = tmp_path / "atoms.json"
    reg1 = AtomRegistry(store=AtomStore(p))
    reg1.create("a", "task", "x")
    reg1.create("b", "task", "y")
    reg1.remove("a")
    reg2 = AtomRegistry(store=AtomStore(p))
    assert reg2.get("a") is None and reg2.get("b") is not None


def test_corrupt_store_does_not_crash(tmp_path):
    p = tmp_path / "atoms.json"
    p.write_text("{ not json", encoding="utf-8")
    reg = AtomRegistry(store=AtomStore(p))  # 坏文件 → 空,不炸
    assert len(reg) == 0
