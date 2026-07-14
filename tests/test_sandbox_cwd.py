"""回归锁:相对路径的"检查基准"与"IO 基准"必须同为 workspace/grant root。

实捕(2026-07,真模型压测 J22 两次复发):模型给 coding 工具传相对 file_path
(如 `quarterly_sales.csv` / `analyze_growth.py`)——检查层(path_allowed →
is_within_workspace)把相对路径按 workspace 拼接判定 → 放行;而落盘层
(token 闸 `open(path)` / WriteTool makedirs / FileState abspath)把同一相对
路径按**进程 CWD** 解析 → 产物写进 pytest 启动目录=源码仓根,且写在 token
授权范围之外(检查一个路径、操作另一个路径)。

钉三跳,零模型:
  1) token 闸(win _util / bubblewrap / seatbelt 三份同语义拷贝):相对路径
     落盘/读取锚定匹配的 grant root,绝不落进程 CWD;越界照拒(0 回归)。
  2) 工具层(Write/Read/Edit):相对 file_path 解析进 workspace_root。
  3) sandbox.exec 的 cwd 真传进子进程(排查时的头号嫌疑,已证无罪 —— 锁住防回归;
     本平台没有可用后端时跳过)。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from karvyloop.cli.run import _make_token
from karvyloop.coding.filestate import FileState
from karvyloop.coding.tools.edit import EditTool
from karvyloop.coding.tools.read import ReadTool
from karvyloop.coding.tools.write import WriteTool
from karvyloop.platform.darwin.seatbelt import SeatbeltSandbox
from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
from karvyloop.platform.win._util import token_gated_read, token_gated_write


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """进程 CWD 切到 workspace 之外的目录 —— 泄漏发生的必要条件。"""
    d = tmp_path / "cwd-else"
    d.mkdir()
    monkeypatch.chdir(d)
    return d


# ---------------------------------------------------------------------------
# 1) token 闸三份拷贝:write_file/read_file 纯 token 闸 IO,平台无关可跨平台实例化
# ---------------------------------------------------------------------------

def _win_write(path, content, tok):
    token_gated_write(path, content, tok)


def _bwrap_write(path, content, tok):
    asyncio.run(BubblewrapSandbox().write_file(path, content, tok))


def _seatbelt_write(path, content, tok):
    asyncio.run(SeatbeltSandbox().write_file(path, content, tok))


def _win_read(path, tok):
    return token_gated_read(path, tok)


def _bwrap_read(path, tok):
    return asyncio.run(BubblewrapSandbox().read_file(path, tok))


def _seatbelt_read(path, tok):
    return asyncio.run(SeatbeltSandbox().read_file(path, tok))


_GATE_WRITES = [("win", _win_write), ("bwrap", _bwrap_write), ("seatbelt", _seatbelt_write)]
_GATE_READS = [("win", _win_read), ("bwrap", _bwrap_read), ("seatbelt", _seatbelt_read)]


@pytest.mark.parametrize("name,gate_write", _GATE_WRITES, ids=[n for n, _ in _GATE_WRITES])
def test_gate_write_relative_lands_in_grant_root_not_cwd(name, gate_write, ws, elsewhere):
    tok = _make_token(str(ws))
    gate_write("probe_rel.txt", b"x", tok)
    assert (ws / "probe_rel.txt").exists(), f"[{name}] 相对路径没落进 grant root"
    assert not (elsewhere / "probe_rel.txt").exists(), \
        f"[{name}] 相对路径写进了进程 CWD(检查按 root、落盘按 CWD 的写穿回归)"


@pytest.mark.parametrize("name,gate_read", _GATE_READS, ids=[n for n, _ in _GATE_READS])
def test_gate_read_relative_reads_grant_root_not_cwd(name, gate_read, ws, elsewhere):
    tok = _make_token(str(ws))
    (ws / "data.txt").write_bytes(b"from-ws")
    (elsewhere / "data.txt").write_bytes(b"from-cwd")   # CWD 里放毒饵
    assert gate_read("data.txt", tok) == b"from-ws", \
        f"[{name}] 相对路径读到了进程 CWD 的文件而不是 grant root 的"


@pytest.mark.parametrize("name,gate_write", _GATE_WRITES, ids=[n for n, _ in _GATE_WRITES])
def test_gate_write_outside_root_still_denied(name, gate_write, ws, elsewhere):
    """0 回归:绝对路径越界照拒(修相对路径不许放松边界)。"""
    tok = _make_token(str(ws))
    with pytest.raises(PermissionError):
        gate_write(str(elsewhere / "evil.txt"), b"x", tok)
    assert not (elsewhere / "evil.txt").exists()


# ---------------------------------------------------------------------------
# 2) 工具层:相对 file_path 解析进 workspace_root(makedirs/filestate/sandbox 同基准)
# ---------------------------------------------------------------------------

class _GateSandbox:
    """只带 token 闸 IO 的最小 sandbox(与三平台 write_file/read_file 同语义)。"""

    async def write_file(self, path, content, token):
        token_gated_write(path, content, token)

    async def read_file(self, path, token):
        return token_gated_read(path, token)


def _tools(ws):
    tok = _make_token(str(ws))
    sb = _GateSandbox()
    fs = FileState()
    return (WriteTool(sb, fs, str(ws), token=tok),
            ReadTool(sb, fs, str(ws), token=tok),
            EditTool(sb, fs, str(ws), token=tok))


def test_write_tool_relative_path_writes_into_workspace(ws, elsewhere):
    w, _r, _e = _tools(ws)
    r = asyncio.run(w({"file_path": "sub/analyze.py", "content": "print('hi')\n"}))
    assert r.ok, r.error_message
    assert (ws / "sub" / "analyze.py").read_text() == "print('hi')\n"
    assert not (elsewhere / "sub").exists() and not (elsewhere / "analyze.py").exists(), \
        "相对 file_path 的产物/父目录落进了进程 CWD(J22 写穿仓根回归)"


def test_read_tool_relative_path_reads_workspace(ws, elsewhere):
    (ws / "notes.txt").write_text("workspace-truth")
    (elsewhere / "notes.txt").write_text("cwd-poison")
    _w, r, _e = _tools(ws)
    res = asyncio.run(r({"file_path": "notes.txt"}))
    assert res.ok, res.error_message
    assert "workspace-truth" in str(res.payload)
    assert "cwd-poison" not in str(res.payload)


def test_edit_tool_relative_path_edits_workspace_file(ws, elsewhere):
    w, r, e = _tools(ws)
    assert asyncio.run(w({"file_path": "app.py", "content": "old\n"})).ok
    assert asyncio.run(r({"file_path": "app.py"})).ok   # HR-4:先读后写
    res = asyncio.run(e({"file_path": "app.py", "old_string": "old", "new_string": "new"}))
    assert res.ok, res.error_message
    assert (ws / "app.py").read_text() == "new\n"
    assert not (elsewhere / "app.py").exists()


def test_write_tool_absolute_outside_workspace_still_denied(ws, elsewhere):
    """0 回归:工具层绝对路径越界照拒。"""
    w, _r, _e = _tools(ws)
    r = asyncio.run(w({"file_path": str(elsewhere / "evil.py"), "content": "x"}))
    assert not r.ok and r.error_code == 1
    assert not (elsewhere / "evil.py").exists()


# ---------------------------------------------------------------------------
# 3) sandbox.exec 的 cwd 一跳不丢(子进程真在 workspace 里跑;无可用后端则跳过)
# ---------------------------------------------------------------------------

def test_sandbox_exec_child_cwd_is_workspace(ws, elsewhere):
    from karvyloop.sandbox.selector import default_sandbox
    sb = default_sandbox()
    tok = _make_token(str(ws))
    argv = ["python3", "-c",
            "open('cwd_probe_exec.txt','w').write('x'); import os; print(os.getcwd())"]
    try:
        r = asyncio.run(sb.exec(argv, token=tok, cwd=str(ws), timeout_s=60))
    except NotImplementedError:
        pytest.skip(f"本平台无可用沙箱后端({type(sb).__name__})—— exec 跳过")
    except RuntimeError as exc:
        pytest.skip(f"沙箱后端不可用:{exc}")
    assert r.exit_code == 0, f"探针命令没跑通: stderr={r.stderr.decode(errors='replace')[:300]}"
    child_cwd = r.stdout.decode(errors="replace").strip()
    assert os.path.realpath(child_cwd) == os.path.realpath(str(ws)), \
        f"子进程 CWD={child_cwd} 不是 workspace —— sandbox.exec 丢了 cwd"
    assert (ws / "cwd_probe_exec.txt").exists(), "重定向/相对写没落进 workspace"
    assert not (elsewhere / "cwd_probe_exec.txt").exists(), \
        "命令产物写进了进程 CWD —— sandbox.exec 的 cwd 一跳丢了"
