"""test_pursuit_gate_sandbox — verify_gate 的 test_pass 关进三后端沙箱(P1 安全缝第三刀件④)。

不可信 gate 命令(来自用户 / LLM 判型)人 ACCEPT 后每 tick 执行 —— 旧实现裸 subprocess.run
把它直接喂宿主(可写宿主任意文件 / 出网 / 无资源上限)。本刀改走 `karvyloop.sandbox` 的现成
三后端沙箱(Linux bubblewrap / macOS seatbelt / Windows 受限进程),与业界跑测试同款做法。

覆盖:
- **同步/异步桥**两个调用上下文(核心易错点):
  · 无 running loop(直接 sync 调用)→ asyncio.run;
  · 有 running loop(pursuit_tick 是 async,同步调 is_done)→ 独立工作线程跑 + join,绝不嵌套崩。
- **fs 范围 = cwd 的测试锁**:沙箱内真能读+写 cwd 下文件(否则自然写法 gate 静默永红)。
- **网络默认隔离**:优先申请网络隔离档(token task_id=skill-exec);拿不到 → 诚实降 first-party。
- **fail-closed 但 fail-loud**:无真隔离后端(available()==False)→ 拒跑不可信 gate + 日志人话原因。
- **超时用沙箱自带**:timed_out → 判 False + 带出。
- file_exists / predicate 门不动;split_test_pass_cmd 不动。

真机(有真隔离后端时)另跑真沙箱:green(读写 cwd)/ timeout 强杀 / 网络隔离真生效。
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition import pursuit  # noqa: E402
from karvyloop.cognition.pursuit import (  # noqa: E402
    PursuitManager, eval_verify_gate, GateError,
)
from karvyloop.sandbox import ExecResult, default_sandbox  # noqa: E402
from karvyloop.schemas import Pursuit  # noqa: E402


# --------------------------------------------------------------------------- fakes
class FakeSandbox:
    """可注入的假沙箱:记录每次 exec 的 token/argv/cwd/线程,按 token 类型可定制行为。"""

    def __init__(self, *, available=True, result=None, exc=None,
                 skillexec_exc=None, firstparty_result=None):
        self._available = available
        self._result = result if result is not None else ExecResult(b"", b"", 0)
        self._exc = exc
        self._skillexec_exc = skillexec_exc
        self._firstparty_result = firstparty_result
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000):
        self.calls.append({
            "task_id": getattr(token, "task_id", None),
            "grants": [(g.resource, tuple(g.ops)) for g in getattr(token, "grants", [])],
            "argv": list(argv), "cwd": cwd, "timeout_s": timeout_s,
            "thread": threading.get_ident(),
        })
        if getattr(token, "task_id", None) == "skill-exec" and self._skillexec_exc is not None:
            raise self._skillexec_exc
        if self._exc is not None:
            raise self._exc
        if getattr(token, "task_id", None) == "pursuit-gate" and self._firstparty_result is not None:
            return self._firstparty_result
        return self._result


def _gate(cmd="echo hi", **kw):
    g = {"type": "test_pass", "cmd": cmd}
    g.update(kw)
    return g


# --------------------------------------------------------------- 桥:同步上下文(无 loop)
def test_bridge_sync_context_asyncio_run(monkeypatch):
    """无 running loop 的直接 sync 调用 → asyncio.run 分支;网络隔离档 token(skill-exec)+ fs=cwd。"""
    fake = FakeSandbox(result=ExecResult(b"", b"", 0))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    assert eval_verify_gate(_gate(cwd="/proj"), {}) is True
    assert len(fake.calls) == 1
    c = fake.calls[0]
    assert c["task_id"] == "skill-exec"                 # 默认申请网络隔离档
    assert ("fs:/proj", ("read", "write")) in c["grants"]   # fs 范围=cwd,读写
    assert c["cwd"] == "/proj"
    # 无 loop 分支:exec 在当前线程用临时 loop 跑(asyncio.run)
    assert c["thread"] == threading.get_ident()


# --------------------------------------------------------------- 桥:异步上下文(有 loop)
def test_bridge_running_loop_context_offloads_to_worker(monkeypatch):
    """pursuit_tick 式:async 函数里**同步**调 is_done → 有 running loop → 下独立工作线程跑,
    绝不在正在跑的 loop 上 asyncio.run(会 RuntimeError 崩)。断言:不崩 + 结果正确 + 真跑在别的线程。"""
    fake = FakeSandbox(result=ExecResult(b"", b"", 0))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    main_thread = threading.get_ident()
    mgr = PursuitManager(memory=None)
    p = Pursuit(id="atom:x", level="atom", statement="s", commitment_condition="",
                revision_triggers=[], verify_gate=_gate(cwd="/proj"), status="committed")

    async def _like_pursuit_tick():
        # 与 pursuit_tick 同构:在 async 体里**同步**调 is_done(事件循环正在本线程跑)
        return mgr.is_done(p, {})

    res = asyncio.run(_like_pursuit_tick())
    assert res is True                                  # 没有 nested-loop RuntimeError
    assert len(fake.calls) == 1
    assert fake.calls[0]["thread"] != main_thread        # 真在工作线程跑,没嵌套当前 loop


def test_bridge_running_loop_exception_propagates(monkeypatch):
    """工作线程里 exec 抛的异常要原样跨线程回抛给调用方分流(这里 PermissionError → 降档路径)。"""
    fake = FakeSandbox(available=True,
                       skillexec_exc=PermissionError("no appcontainer"),
                       firstparty_result=ExecResult(b"", b"", 0))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)

    async def _run():
        return eval_verify_gate(_gate(cwd="/proj"), {})

    assert asyncio.run(_run()) is True
    assert [c["task_id"] for c in fake.calls] == ["skill-exec", "pursuit-gate"]


# --------------------------------------------------------------- fail-loud:无真隔离后端拒跑
def test_fail_loud_no_isolation_backend_refuses(monkeypatch, caplog):
    """available()==False(Windows degraded / stub 等无隔离直通档)→ 拒跑不可信 gate + WARNING 人话。"""
    fake = FakeSandbox(available=False)
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    with caplog.at_level(logging.WARNING, logger="karvyloop.cognition.pursuit"):
        assert eval_verify_gate(_gate(), {}) is False
    assert fake.calls == []                              # 绝不执行不可信命令
    msgs = " ".join(r.getMessage().lower() for r in caplog.records)
    assert "refused" in msgs or "no real isolation" in msgs   # 绝不静默永红


# --------------------------------------------------------------- 网络隔离拿不到 → 诚实降档
def test_network_downgrade_falls_back_and_warns(monkeypatch, caplog):
    """skill-exec 网络隔离档被拒(如 AppContainer 探不通)→ 降 first-party 跑 + WARNING 标注网络未隔离。"""
    fake = FakeSandbox(available=True,
                       skillexec_exc=PermissionError("AppContainer unavailable"),
                       firstparty_result=ExecResult(b"", b"", 0))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    with caplog.at_level(logging.WARNING, logger="karvyloop.cognition.pursuit"):
        assert eval_verify_gate(_gate(cwd="/proj"), {}) is True
    assert [c["task_id"] for c in fake.calls] == ["skill-exec", "pursuit-gate"]
    assert any("downgrade" in r.getMessage().lower() for r in caplog.records)


# --------------------------------------------------------------- 超时用沙箱自带
def test_timeout_reported_as_false(monkeypatch, caplog):
    """沙箱 timed_out=True → 判 False + info 带出(超时/截断走沙箱自带,不再手写)。"""
    fake = FakeSandbox(result=ExecResult(b"", b"", 1, timed_out=True))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    with caplog.at_level(logging.INFO, logger="karvyloop.cognition.pursuit"):
        assert eval_verify_gate(_gate("sleep 99", timeout_s=1), {}) is False
    assert any("timed out" in r.getMessage().lower() for r in caplog.records)
    assert fake.calls[0]["timeout_s"] == 1.0             # gate.timeout_s 透传给沙箱


# --------------------------------------------------------------- 普通未过不刷屏 WARNING
def test_normal_failure_is_quiet(monkeypatch, caplog):
    """gate 还没做完(exit 1、非网络)每 tick 都发生 → debug,别 WARNING 刷屏。"""
    fake = FakeSandbox(result=ExecResult(b"", b"assert failed", 1))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    with caplog.at_level(logging.WARNING, logger="karvyloop.cognition.pursuit"):
        assert eval_verify_gate(_gate(), {}) is False
    assert not caplog.records                            # WARNING 级别下一条不落


# --------------------------------------------------------------- 空命令 / 拆不出
def test_empty_and_unsplittable_cmd(monkeypatch):
    fake = FakeSandbox()
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    assert eval_verify_gate(_gate(cmd=""), {}) is False   # 空 → False,不进沙箱
    assert fake.calls == []


# --------------------------------------------------------------- 真伤5:默认超时给冷启留余量
def test_default_timeout_gives_cold_start_headroom(monkeypatch):
    """真伤5:test_pass 门默认超时抬到给沙箱**冷启**留足余量(≥300s)——旧 60s 在负载下把已过的门
    闪成"没完成"(假红,叠真伤1还再等 6h)。gate.timeout_s 仍可 per-gate 覆盖。"""
    from karvyloop.cognition.pursuit import GATE_TEST_PASS_DEFAULT_TIMEOUT_S
    assert GATE_TEST_PASS_DEFAULT_TIMEOUT_S >= 300.0
    fake = FakeSandbox(result=ExecResult(b"", b"", 0))
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: fake)
    # gate 不给 timeout_s → 用默认;透传给沙箱(不再是旧 60s)
    assert eval_verify_gate(_gate(), {}) is True
    assert fake.calls[0]["timeout_s"] == GATE_TEST_PASS_DEFAULT_TIMEOUT_S
    # per-gate 覆盖仍生效(短超时给会超时的命令按时强杀,不伤原本的杀树能力)
    fake.calls.clear()
    eval_verify_gate(_gate(timeout_s=5), {})
    assert fake.calls[0]["timeout_s"] == 5.0


# --------------------------------------------------------------- 真伤7:fail-loud 稳定码进 context
def test_gate_note_code_written_into_context(monkeypatch):
    """真伤7:_gate_test_pass 把 fail-loud 原因作为**稳定码**写进 context["_gate_note_code"]
    (cognition 层只出码不出译文,分层);普通失败(debug)不写码(免刷屏/免覆盖推进备注)。"""
    from karvyloop.cognition.pursuit import (
        GATE_NOTE_KEY, GATE_NOTE_NO_ISOLATION, GATE_NOTE_NET_DOWNGRADE,
        GATE_NOTE_TIMED_OUT,
    )
    # 无真隔离后端 → no_isolation 码
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: FakeSandbox(available=False))
    ctx = {}
    assert eval_verify_gate(_gate(), ctx) is False
    assert ctx.get(GATE_NOTE_KEY) == GATE_NOTE_NO_ISOLATION
    # 超时 → timed_out 码
    monkeypatch.setattr(pursuit, "default_sandbox",
                        lambda: FakeSandbox(result=ExecResult(b"", b"", 1, timed_out=True)))
    ctx = {}
    assert eval_verify_gate(_gate(), ctx) is False
    assert ctx.get(GATE_NOTE_KEY) == GATE_NOTE_TIMED_OUT
    # 网络隔离档拿不到 → 降 first-party + net_downgrade 码(passed 仍可 True)
    monkeypatch.setattr(pursuit, "default_sandbox", lambda: FakeSandbox(
        available=True, skillexec_exc=PermissionError("no appcontainer"),
        firstparty_result=ExecResult(b"", b"", 0)))
    ctx = {}
    assert eval_verify_gate(_gate(cwd="/proj"), ctx) is True
    assert ctx.get(GATE_NOTE_KEY) == GATE_NOTE_NET_DOWNGRADE
    # 普通失败(exit 1、非网络)→ debug 级,**不写码**(每 tick 都发生,别刷屏/别覆盖推进备注)
    monkeypatch.setattr(pursuit, "default_sandbox",
                        lambda: FakeSandbox(result=ExecResult(b"", b"assert failed", 1)))
    ctx = {}
    assert eval_verify_gate(_gate(), ctx) is False
    assert GATE_NOTE_KEY not in ctx


# --------------------------------------------------------------- 其它门不动
def test_other_gates_unaffected(tmp_path):
    f = tmp_path / "x.txt"
    assert eval_verify_gate({"type": "file_exists", "path": str(f)}, {}) is False
    f.write_text("ok", encoding="utf-8")
    assert eval_verify_gate({"type": "file_exists", "path": str(f)}, {}) is True
    assert eval_verify_gate({"type": "predicate", "expr": "a == b"}, {"a": "b"}) is True
    with pytest.raises(GateError):
        eval_verify_gate({"type": "ask_llm"}, {})


# =========================================================================== 真机沙箱
def _isolation_available() -> bool:
    try:
        sb = default_sandbox()
    except Exception:
        return False
    av = getattr(sb, "available", None)
    try:
        return bool(av()) if callable(av) else False
    except Exception:
        return False


_REAL = pytest.mark.skipif(
    not _isolation_available(),
    reason="本机无真隔离后端(available()==False)—— 真沙箱用例跳过(fail-loud 逻辑已由 fake 覆盖)")


@_REAL
def test_real_green_gate_reads_and_writes_cwd(tmp_path):
    """**fs=cwd 测试锁**:真沙箱内 python 脚本读自身(cwd 内)+ 写产物(cwd 内)+ 读回 → exit 0 → gate True;
    宿主也看得到写出的文件(证明 fs 授权真覆盖 cwd,不是静默永红)。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os,sys\n"
        "p=os.path.join(os.getcwd(),'gate_out.txt')\n"
        "open(p,'w',encoding='utf-8').write('hello-gate')\n"
        "sys.exit(0 if open(p,encoding='utf-8').read()=='hello-gate' else 7)\n",
        encoding="utf-8")
    g = _gate(cmd=f'"{sys.executable}" "{probe}"', cwd=str(tmp_path))
    assert eval_verify_gate(g, {}) is True
    assert (tmp_path / "gate_out.txt").read_text(encoding="utf-8") == "hello-gate"


@_REAL
def test_real_timeout_is_force_killed(tmp_path):
    """会超时的 gate:沙箱自带超时强杀 → 判 False,且远早于命令自身的 60s 结束。"""
    g = _gate(cmd=f'"{sys.executable}" -c "import time; time.sleep(60)"',
              cwd=str(tmp_path), timeout_s=3)
    t0 = time.time()
    assert eval_verify_gate(g, {}) is False
    assert time.time() - t0 < 45           # 按时强杀(含 AppContainer 装配开销),不等满 60s


@_REAL
def test_real_network_isolation(tmp_path):
    """网络默认隔离真生效:沙箱内尝试出网,隔离档下连不出去 → 脚本 exit 9 → gate False。
    若本机做不出网络隔离(降到 first-party,网络未隔离)→ 出网成功 → skip 并如实说边界。"""
    netpy = tmp_path / "net.py"
    netpy.write_text(
        "import socket,sys\n"
        "try:\n"
        "    s=socket.create_connection(('1.1.1.1',53),timeout=5); s.close(); sys.exit(0)\n"
        "except Exception:\n"
        "    sys.exit(9)\n",
        encoding="utf-8")
    g = _gate(cmd=f'"{sys.executable}" "{netpy}"', cwd=str(tmp_path))
    passed = eval_verify_gate(g, {})
    if passed:
        pytest.skip("本机沙箱内可出网 —— 该档不支持网络隔离(已诚实降 first-party 文件/资源隔离)；"
                    "网络隔离在 Windows 需 AppContainer / Linux 需 unshare-net / macOS deny network*")
    assert passed is False                 # 连不出去 → exit 9 → gate False = 网络真被隔离
