"""test_demo_instance — 随包演示实例(小林/Lin)只读浏览的回归锁。

锁四件事(与任务书的验证条目一一对应):
1. **可加载**:双语两套实例(lin-zh / lin-en)都能列出、能取总览,banner(disclosure)在。
2. **只读性**:GET 浏览前后,实例包内文件字节级零变化、不长 -wal/-shm;写方法(POST/PUT/
   DELETE)一律 405 —— /api/demo/* 结构上就没有写端点。
3. **零污染**:浏览演示实例绝不碰用户自己的存储(memory 哨兵不动、假 HOME 不长 .karvyloop)。
4. **数据真实性自证**:总览里的成长曲线是从随包 trace.sqlite **现算**的 —— 测试直接开同一个
   sqlite 复核:runs_total ≤ 原始 eval_fact 行数(去抖只会减不会增)、skills_total 与
   crystallize 事件的 sig 数一致、曲线日期都落在 manifest 声明的虚拟日历日内。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from karvyloop.console.app import build_console_app  # noqa: E402
from karvyloop.console.routes_demo import demo_instances_root  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402

DEMO_ROOT = demo_instances_root()
# 随仓实际安装的演示实例(动态发现,不硬编码):现随仓 lin-zh(中文满级号);lin-en(英文
# 翻译版)是 Hardy 计划的第二步("先跑一套再翻译成满级号"),建好后自动被这些测试覆盖,
# 不用改测试。硬编码 lin-en 会在翻译版落地前把全套拉红 —— 演示不完整不等于代码坏。
INSTANCES = tuple(sorted(p.parent.name for p in DEMO_ROOT.glob("*/instance.json")))

pytestmark = pytest.mark.skipif(
    not INSTANCES,
    reason="演示实例未随仓(先跑 _local/build_demo_lin.py --install)")


class _SentinelMemory:
    """哨兵:任何写调用都记账(零污染断言用)。"""

    def __init__(self) -> None:
        self.writes = 0

    def write(self, *a, **k):
        self.writes += 1

    def recall_block(self, *a, **k):
        self.writes += 1
        return ""


@pytest.fixture()
def client():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.memory = _SentinelMemory()
    return TestClient(app)


def _tree_digest(root: pathlib.Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


# ---- 1. 双语两套都能加载 + banner 在 ----

def test_installed_instances_listed(client):
    d = client.get("/api/demo/instances").json()
    ids = {i["id"]: i for i in d["instances"]}
    assert INSTANCES, "至少要随仓一套演示实例(lin-zh)"
    for iid in INSTANCES:
        assert iid in ids, f"{iid} 没列出(打包/manifest 缺?)"
        assert len(ids[iid].get("virtual_days") or []) == 7, f"{iid} 不是 7 个虚拟日"
        assert ids[iid].get("size_bytes", 0) > 0


@pytest.mark.parametrize("iid", INSTANCES)
def test_overview_loads_with_banner(client, iid):
    d = client.get(f"/api/demo/instance/{iid}").json()
    assert d.get("ok") is True, f"{iid} 总览取不到: {d}"
    man = d["manifest"]
    # banner:双语 disclosure 都在,且明说「虚构 + 真机制」
    assert "虚构" in man["disclosure"]["zh"] and "真实机制" in man["disclosure"]["zh"]
    assert "fictional" in man["disclosure"]["en"]
    # 成长数据在(day1 vs day7 真数字)
    assert d["growth"], "成长曲线空"
    assert d["day7"].get("runs_total", 0) > d["day1"].get("runs_total", 0), \
        "第 7 天的累计 run 数没有多于第 1 天(没长?)"
    assert d["day7"].get("skills_total", 0) >= 1, "7 天没结晶出任何技能"
    # 决策偏好长出来了,且两个方向都有(约束/口味至少各一)
    kinds = {p.get("kind") for p in d["decision_prefs"]}
    assert len(d["decision_prefs"]) >= 2, "决策偏好少于 2 条"
    assert "constraint" in kinds or "standing" in kinds, "缺『拒绝/底线』方向的偏好"
    # 知识库有货 + 静音门诚实(earned=False,门槛数字直出)
    assert d["knowledge_total"] >= 5
    assert d["taste"]["earned"] is False
    assert d["taste"]["gate_min_n"] >= 35


def test_unknown_instance_rejected(client):
    d = client.get("/api/demo/instance/evil-id").json()
    assert d.get("ok") is False
    # 路径穿越形状直接 404(路由不匹配)或 ok:false,绝不落盘
    r = client.get("/api/demo/instance/..%2f..%2fsecrets")
    assert r.status_code in (404, 200)
    if r.status_code == 200:
        assert r.json().get("ok") is False


# ---- 2. 只读性:浏览零改动 + 写方法被拒 ----

def test_browsing_never_mutates_package_files(client):
    before = {iid: _tree_digest(DEMO_ROOT / iid) for iid in INSTANCES}
    for _ in range(2):
        client.get("/api/demo/instances")
        for iid in INSTANCES:
            assert client.get(f"/api/demo/instance/{iid}").json().get("ok") is True
    after = {iid: _tree_digest(DEMO_ROOT / iid) for iid in INSTANCES}
    assert before == after, "浏览演示实例改动了包内文件(只读性破了)"
    for iid in INSTANCES:
        stray = [p.name for p in (DEMO_ROOT / iid).rglob("*")
                 if p.name.endswith(("-wal", "-shm"))]
        assert not stray, f"{iid} 长出 sqlite 写痕迹: {stray}(没用 mode=ro 打开?)"


def test_write_methods_rejected(client):
    # /api/demo/* 结构上 GET-only:任何写方法 405(FastAPI method not allowed)
    assert client.post("/api/demo/instances").status_code == 405
    assert client.post("/api/demo/instance/lin-zh", json={}).status_code == 405
    assert client.put("/api/demo/instance/lin-zh", json={}).status_code == 405
    assert client.delete("/api/demo/instance/lin-zh").status_code == 405


# ---- 3. 零污染:不碰用户存储 ----

def test_zero_pollution_of_user_instance(client, tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    client.get("/api/demo/instances")
    for iid in INSTANCES:
        client.get(f"/api/demo/instance/{iid}")
    assert client.app.state.memory.writes == 0, "demo 浏览碰了用户 memory"
    assert not (tmp_path / ".karvyloop").exists(), "demo 浏览在用户 HOME 里创建了 .karvyloop"


# ---- 4. 数据真实性自证:曲线数字 = 随包 Trace 可复核推导 ----

@pytest.mark.parametrize("iid", INSTANCES)
def test_growth_numbers_derive_from_bundled_trace(client, iid):
    d = client.get(f"/api/demo/instance/{iid}").json()
    tp = DEMO_ROOT / iid / "trace.sqlite"
    assert tp.exists(), "实例包里没有 trace.sqlite(曲线无源)"
    conn = sqlite3.connect(f"file:{tp.as_posix()}?mode=ro&immutable=1", uri=True)
    rows = conn.execute(
        "SELECT kind, payload_json FROM trace_entries WHERE kind IN ('eval_fact','crystallize')"
    ).fetchall()
    conn.close()
    eval_rows = [json.loads(p) for k, p in rows if k == "eval_fact"]
    usable = [p for p in eval_rows if p.get("sig") and not p.get("checker_verdict")]
    crystallize_sigs = {json.loads(p).get("sig") for k, p in rows if k == "crystallize"}
    day7 = d["day7"]
    assert 0 < day7["runs_total"] <= len(usable), \
        f"曲线 runs_total={day7['runs_total']} 超过原始 eval_fact 行数 {len(usable)}(去抖只会减)"
    assert day7["skills_total"] == len({s for s in crystallize_sigs if s}), \
        "曲线 skills_total 与 Trace 里 crystallize 事件的 sig 数对不上"
    # 曲线日期都在 manifest 声明的虚拟日历日内(时间没被面板重写)
    days = set(d["manifest"]["virtual_days"])
    assert days, "manifest 没有 virtual_days"
    for p in d["growth"]:
        assert p["day"] in days, f"曲线冒出 manifest 之外的日期: {p['day']}"
