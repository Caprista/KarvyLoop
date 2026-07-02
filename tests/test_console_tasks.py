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


# ---- 活动时间线(借鉴 Multica:任务=可读的同事,经历了什么持久记在任务身上)----
def test_timeline_records_lifecycle():
    """start → start 事件;中途 add_event(step/blocked);finish → done/error 事件。"""
    reg = TaskRegistry()
    tid = reg.start(who="⚙ 工作流", intent="做登录页")
    reg.add_event(tid, "step", "产品:需求梳理")
    reg.add_event(tid, "blocked", "前端:npm install 失败")
    t = reg.list()[0]
    assert t["blocked"] is True                              # 最新事件是 blocked → 卡片直接可见
    assert t["last_event"]["text"].startswith("前端")
    reg.add_event(tid, "step", "前端:重试成功")
    assert reg.list()[0]["blocked"] is False                 # 恢复推进 → 不再标卡
    reg.finish(tid, result="上线了")
    events = reg.get(tid)["events"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["start", "step", "blocked", "step", "done"]
    assert all("ts" in e for e in events)


def test_timeline_persists_across_reload(tmp_path):
    """时间线随任务落盘 —— 重启后"经历了什么"仍在(不再是前端易失缓存)。"""
    from karvyloop.console.tasks import TaskStore
    store = TaskStore(tmp_path / "tasks.json")
    reg = TaskRegistry(store=store)
    tid = reg.start(who="a", intent="x")
    reg.add_event(tid, "blocked", "卡在依赖")
    reg.finish(tid, error="没跑完")
    reg2 = TaskRegistry(store=TaskStore(tmp_path / "tasks.json"))
    events = reg2.get(tid)["events"]
    assert [e["kind"] for e in events] == ["start", "blocked", "error"]
    assert events[1]["text"] == "卡在依赖"


def test_timeline_cap_keeps_head_and_tail():
    """时间线超上限 → 砍中段,保头部 start + 最新(不无界膨胀)。"""
    reg = TaskRegistry()
    tid = reg.start(who="a", intent="x")
    for i in range(200):
        reg.add_event(tid, "step", f"s{i}")
    events = reg.get(tid)["events"]
    assert len(events) <= 80
    assert events[0]["kind"] == "start"                      # 头部保住
    assert events[-1]["text"] == "s199"                      # 最新保住


def test_add_event_unknown_task_is_noop():
    reg = TaskRegistry()
    reg.add_event("ghost", "step", "x")                      # 不炸
    assert reg.list() == []


# ---- #42 优化③:时间线→Trace 下钻 ----

def test_task_trace_drilldown():
    """/api/task/{id}/trace 把叙述变证据:返回该任务 Trace 切片(工具调用+摘要)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.cognition.trace import TraceEntry, TraceStore
    from karvyloop.console.tasks import TaskRegistry

    class _ML:
        trace = TraceStore()

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=_ML())
    reg = TaskRegistry()
    app.state.task_registry = reg
    tid = reg.start(who="工程师", intent="汇总数据")
    reg.set_conversation(tid, "conv-1", trace_id="drive-trace-9")
    _ML.trace.append(TraceEntry(task_id="drive-trace-9", kind="atom_run", payload={
        "atom_id": "a1", "success": True, "output": "三行结论……",
        "tool_calls": [{"id": "t1", "name": "read_file", "input": {"path": "/tmp/x.csv"}}],
    }))
    client = TestClient(app)
    r = client.get(f"/api/task/{tid}/trace")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["trace_id"] == "drive-trace-9"
    row = body["entries"][0]
    assert row["kind"] == "atom_run" and row["success"] is True
    assert row["tools"][0]["name"] == "read_file" and "x.csv" in row["tools"][0]["input"]
    # 无 trace(--no-llm)→ 诚实 reason 不炸
    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app2.state.task_registry = reg
    r2 = TestClient(app2).get(f"/api/task/{tid}/trace")
    assert r2.json()["ok"] is False and r2.json()["entries"] == []
    # 前端接线在位(不是 backend self-hype)
    import pathlib
    app_js = (pathlib.Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/trace" in app_js and "task.view_trace" in app_js
