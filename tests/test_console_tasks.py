"""任务看板登记验收(9.5 P2)。"""
from __future__ import annotations

from karvyloop.console.tasks import TaskRegistry


def test_start_running_then_finish_done():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", domain_id="l0", role="", intent="写个文件")
    tasks = reg.list()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "running" and tasks[0]["who"] == "小卡"
    assert tasks[0]["intent"] == "写个文件"
    reg.finish(tid, result="文件已建")
    assert reg.list()[0]["status"] == "done"
    assert reg.list()[0]["result"] == "文件已建"
    assert reg.list()[0]["finished"] is not None


def test_finish_error():
    reg = TaskRegistry()
    tid = reg.start(who="设计师", domain_id="dom-x", role="设计师", intent="x")
    reg.finish(tid, error="炸了")
    t = reg.list()[0]
    assert t["status"] == "error" and t["result"] == "炸了"


def test_newest_first():
    reg = TaskRegistry()
    reg.start(who="a", intent="1")
    reg.start(who="b", intent="2")
    assert [t["who"] for t in reg.list()] == ["b", "a"]


def test_cap_eviction():
    reg = TaskRegistry(cap=3)
    ids = [reg.start(who=str(i), intent=str(i)) for i in range(5)]
    assert len(reg.list()) == 3
    # 最旧两个被挤掉,finish 它们不炸(no-op)
    reg.finish(ids[0], result="late")
    assert all(t["who"] in {"2", "3", "4"} for t in reg.list())


def test_result_truncated():
    reg = TaskRegistry()
    tid = reg.start(who="x", intent="x")
    reg.finish(tid, result="z" * 1000)
    assert len(reg.list()[0]["result"]) <= 280


def test_persist_and_reload(tmp_path):
    """loop-step2:任务落盘 → 新进程(新 registry)读回(重启记得住)。"""
    from karvyloop.console.tasks import TaskStore
    p = tmp_path / "tasks.json"
    reg1 = TaskRegistry(store=TaskStore(p))
    tid = reg1.start(who="小卡", domain_id="l0", intent="写文件")
    reg1.finish(tid, result="搞定了 ✓")
    reg1.start(who="设计师", domain_id="dom-x", role="设计师", intent="画图")
    # 新 registry 从同一文件读回
    reg2 = TaskRegistry(store=TaskStore(p))
    items = reg2.list()
    assert len(items) == 2
    assert items[0]["who"] == "设计师"  # newest-first 顺序保住
    done = reg2.get(tid)
    assert done is not None and done["status"] == "done" and done["result_full"] == "搞定了 ✓"


def test_running_marked_interrupted_on_reload(tmp_path):
    """重启时仍 running 的任务 = 进程中断 → 标成 error/interrupted,不假装还在跑。"""
    from karvyloop.console.tasks import TaskStore
    p = tmp_path / "tasks.json"
    reg1 = TaskRegistry(store=TaskStore(p))
    tid = reg1.start(who="小卡", intent="一个没跑完的活")  # 没 finish → 落盘时是 running
    reg2 = TaskRegistry(store=TaskStore(p))
    t = reg2.get(tid)
    assert t is not None and t["status"] == "error"
    assert "中断" in t["result_full"]


def test_corrupt_store_no_crash(tmp_path):
    from karvyloop.console.tasks import TaskStore
    p = tmp_path / "tasks.json"
    p.write_text("{ not json", encoding="utf-8")
    reg = TaskRegistry(store=TaskStore(p))
    assert reg.list() == []


def test_detail_has_full_result():
    """P3 M2:结果文档 —— get(id) 带完整结果(列表只带摘要)。"""
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="写代码")
    reg.finish(tid, result="z" * 1000)
    # 列表:截断摘要,无 result_full
    assert "result_full" not in reg.list()[0]
    # 详情:完整
    d = reg.get(tid)
    assert d is not None and len(d["result_full"]) == 1000
    assert reg.get("nope") is None
