"""test_run_command_sensitive_floor — run_command 敏感路径地板(分层防御纵深)。

背景(结构已核):run_command 的敏感读封闭是**三层叠加**,本测试锁其中的工具层 + OS 层:
  1. capability 决策链 step6(authorize/_safety_check)对命令串跑 is_sensitive_path 硬拦
     —— 已在 test_fs_grants.py::test_capability_chain_sensitive_and_granted_write 覆盖。
  2. **工具层预检**(本文件):BashTool.__call__ 在 exec 前对命令串扫 SENSITIVE_MARKERS,
     即便被绕过上游 capability 闸单独调用也不裸奔。诚实边界:字符串预检可被绕过,非密封。
  3. **OS 层敏感地板**(本文件):
     - macOS Seatbelt deny 子集 = SENSITIVE_MARKERS **全集**(单一真相源,不再手抄小清单)。
     - Windows 降级档(无 OS 隔离)在 exec 边界也跑同一套预检,挡最常见直读密钥。
     - Linux bwrap:workspace-only mount 天然封闭(读工作区外的 ~/.karvyloop 直接不存在)。

诚实:预检是**防御纵深不是密封**;真封闭靠 OS 沙箱(bwrap mount / seatbelt deny)。
所有 fixture 路径都是**假的带 FAKE 标注或系统标准敏感路径**,断言不打印任何真实密钥值。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from karvyloop.capability.fs_grants import (  # noqa: E402
    SENSITIVE_MARKERS, scan_command_for_sensitive)
from karvyloop.coding.filestate import FileState  # noqa: E402
from karvyloop.coding.tools.bash import BashTool  # noqa: E402
from karvyloop.sandbox.exec_result import ExecResult  # noqa: E402

pytestmark = pytest.mark.security  # 安全套件:run_command 敏感路径地板(密钥/凭据永不经 shell 外泄)


class _RecordingSandbox:
    """记录 exec 是否被调用 —— 预检命中时 exec 绝不该被触达(密封点在预检)。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=30.0,
                   max_output_bytes=30_000) -> ExecResult:
        self.calls.append(list(argv))
        return ExecResult(stdout=b"ok", stderr=b"", exit_code=0,
                          timed_out=False, truncated=False)


def _bash(ws: str):
    sb = _RecordingSandbox()
    return BashTool(sb, FileState(), ws, token=object()), sb


# ---- (1) 工具层预检:cat <sensitive> 被拦,exec 从不触达 ----

# 用系统标准敏感路径 + 我们自己的敏感文件(非真实密钥值,只是路径形态)
_SENSITIVE_CMDS = [
    "cat ~/.karvyloop/config.yaml",              # 我们的 API key 文件
    "cat ~/.karvyloop/console.runtime.json",     # console 访问令牌
    "cat ~/.ssh/id_rsa",                         # ssh 私钥
    "type C:\\Users\\FAKE-USER\\.ssh\\id_rsa",   # Windows 形态直读私钥
    "cat ../.env",                               # 惯例密钥文件(相对路径)
    "dd if=/etc/shadow of=/tmp/x",               # 系统凭据
    "cat ~/.aws/credentials",                    # 云凭据
    "python -c \"print(open('/home/FAKE/.aws/credentials').read())\"",  # 间接读(路径明文出现)
]


@pytest.mark.parametrize("cmd", _SENSITIVE_CMDS)
def test_run_command_blocks_sensitive_reads(tmp_path, cmd):
    tool, sb = _bash(str(tmp_path))
    r = asyncio.run(tool({"command": cmd}))
    assert r.ok is False, f"敏感命令未被拦:{cmd!r}"
    assert "受保护路径" in (r.error_message or ""), r.error_message
    assert sb.calls == [], f"预检命中却仍触达 exec(密封点失效):{cmd!r}"


# ---- (2) 零回归:正常工作区命令照常过预检并执行 ----

_BENIGN_CMDS = [
    "ls -la",
    "grep -rn TODO src/",
    "python build.py",
    "echo hi > out.txt",
    "pytest -q tests/",
    "git status",
    "cat main.py",                # 普通工作区文件名,不含敏感标记
]


@pytest.mark.parametrize("cmd", _BENIGN_CMDS)
def test_run_command_workspace_ops_zero_regression(tmp_path, cmd):
    tool, sb = _bash(str(tmp_path))
    r = asyncio.run(tool({"command": cmd}))
    assert r.ok is True, f"正常命令被误伤:{cmd!r} -> {r.error_message!r}"
    assert sb.calls, f"正常命令没能触达 exec(预检误伤):{cmd!r}"


# ---- (3) 扫描器单元:命中返回具体标记 / 良性返回 None ----

def test_scan_command_for_sensitive_unit():
    assert scan_command_for_sensitive("cat ~/.karvyloop/config.yaml") == "/.karvyloop/config.yaml"
    assert scan_command_for_sensitive("cat ~/.ssh/id_rsa") in ("/.ssh", "id_rsa")
    assert scan_command_for_sensitive("cat ~/.karvyloop/console.runtime.json") \
        == "/.karvyloop/console.runtime.json"
    assert scan_command_for_sensitive("cat ../.env") == "/.env"
    # 良性:零命中
    for benign in ("ls -la", "grep foo *.py", "python build.py", "echo hi > out.txt", ""):
        assert scan_command_for_sensitive(benign) is None, benign


# ---- (4) macOS Seatbelt:deny 子集 = SENSITIVE_MARKERS 全集(单一真相源覆盖断言)----

def test_seatbelt_deny_covers_all_sensitive_markers():
    import time as _t

    from karvyloop.platform.darwin.seatbelt import build_profile, _marker_to_sbpl_regex
    from karvyloop.schemas import Capability, CapabilityToken

    tok = CapabilityToken(task_id="t",
                          grants=[Capability(resource="fs:/tmp/ws", ops=["write"])],
                          expiry=_t.time() + 60)
    prof = build_profile(tok)
    # fail-closed 骨架仍在
    assert "(deny default)" in prof
    assert "(allow file-read*)" in prof
    # **全集覆盖**:每一条 SENSITIVE_MARKER 都出一条大小写无关子串正则 deny(单一真相源)
    missing = [m for m in SENSITIVE_MARKERS if _marker_to_sbpl_regex(m) not in prof]
    assert not missing, f"seatbelt deny 漏了这些敏感标记(手抄小清单回归?):{missing}"
    # 旧版漏掉、现已覆盖的几类(回归哨兵)
    for m in ("/.karvyloop/console.runtime.json", "/.env", "/.config/gcloud", "/cookies"):
        assert _marker_to_sbpl_regex(m) in prof, f"仍漏:{m}"
    # deny 行都是硬 deny(读写皆拒)
    assert prof.count("(deny file-read* file-write*") >= len(SENSITIVE_MARKERS)


def test_seatbelt_regex_is_case_insensitive_and_escaped():
    from karvyloop.platform.darwin.seatbelt import _marker_to_sbpl_regex
    # 字母→[Xx] 大小写类(macOS 上 'Login Data'/'Cookies' 大写不折叠就漏)
    assert _marker_to_sbpl_regex("/cookies") == r".*/[Cc][Oo][Oo][Kk][Ii][Ee][Ss].*"
    # 点被转义为字面量,不当通配
    assert r"\." in _marker_to_sbpl_regex("/.env")
    # 空格保留(login data)
    assert " " in _marker_to_sbpl_regex("/login data")


# ---- (5) Windows 降级档:exec 边界预检生效(诚实弱隔离层)----

def _degraded_first_party_token(fs_path: str):
    from karvyloop.schemas import Capability, CapabilityToken
    return CapabilityToken(
        task_id="t",  # 非 skill-exec → 第一方
        grants=[Capability(resource=f"fs:{fs_path}", ops=["read", "write"]),
                Capability(resource=f"fs:{fs_path}", ops=["exec"])],
        expiry=9_999_999_999.0,
    )


def test_windows_degraded_preflight_blocks_sensitive(tmp_path):
    from karvyloop.platform.win.degraded import DegradedWindowsSandbox
    sb = DegradedWindowsSandbox()
    tok = _degraded_first_party_token(str(tmp_path))
    with pytest.raises(PermissionError) as ei:
        asyncio.run(sb.exec(["sh", "-c", "cat ~/.karvyloop/config.yaml"],
                            token=tok, cwd=str(tmp_path)))
    msg = str(ei.value)
    assert "受保护路径" in msg and "/.karvyloop/config.yaml" in msg
    assert "降级" in msg or "弱隔离" in msg  # 诚实标注弱隔离层


def test_windows_degraded_benign_passes_preflight(tmp_path):
    """良性命令过预检 —— 用未覆盖的 cwd 让它停在 cwd 闸(证明没被敏感预检拦下,零误伤)。"""
    from karvyloop.platform.win.degraded import DegradedWindowsSandbox
    sb = DegradedWindowsSandbox()
    # token 只授某个别处目录 → 良性命令过预检后应停在"cwd 未覆盖"而非"敏感路径"
    other = str(tmp_path / "granted")
    (tmp_path / "granted").mkdir()
    tok = _degraded_first_party_token(other)
    with pytest.raises(PermissionError) as ei:
        asyncio.run(sb.exec(["sh", "-c", "echo hi"],
                            token=tok, cwd=str(tmp_path / "uncovered")))
    msg = str(ei.value)
    assert "受保护路径" not in msg, "良性命令被敏感预检误伤"
    assert "未覆盖执行目录" in msg, msg  # 证明预检放行、停在 cwd 闸


# ---- (6) Linux bwrap:workspace-only mount 封闭(静态实证 argv,不 bind $HOME/敏感路径)----

def test_bwrap_mount_closure_excludes_home_and_sensitive(tmp_path, monkeypatch):
    """bwrap argv 只 bind 工作区 + 二进制目录;$HOME/~/.karvyloop 及任何敏感路径**绝不 bind**。

    读工作区外敏感路径在 bwrap 下失败的根因 = mount 封闭(路径在沙箱内根本不存在)。
    真机跑 bwrap 需 Linux;这里跨平台**静态实证 argv**:拦截 create_subprocess_exec 取到
    真实拼出的 bwrap 命令,断言其 bind 列表不含 $HOME / 敏感标记路径。
    """
    from karvyloop.platform.linux import bubblewrap as bw
    from karvyloop.capability.fs_grants import is_sensitive_path
    from karvyloop.schemas import Capability, CapabilityToken

    ws = tmp_path / "ws"
    ws.mkdir()
    tok = CapabilityToken(task_id="t",
                          grants=[Capability(resource=f"fs:{ws}", ops=["write"])],
                          expiry=9_999_999_999.0)

    captured: dict = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, stdin=None):
            return b"", b""

        def kill(self):
            pass

    async def _fake_exec(*argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc()

    monkeypatch.setattr(bw.BubblewrapSandbox, "available", staticmethod(lambda: True))
    # 不让 Landlock wrapper 扰动 argv(纯 bwrap 命令便于断言)
    monkeypatch.setattr(bw.BubblewrapSandbox, "_wrap_landlock",
                        classmethod(lambda cls, bwrap, ro, rw: bwrap))
    monkeypatch.setattr(bw.asyncio, "create_subprocess_exec", _fake_exec)

    sb = bw.BubblewrapSandbox()
    asyncio.run(sb.exec(["sh", "-c", "echo hi"], token=tok, cwd=str(ws), timeout_s=5.0))
    argv = captured["argv"]

    # 工作区被 rw bind
    assert "--bind" in argv
    joined = " ".join(argv)
    home = os.path.expanduser("~")
    # $HOME/.karvyloop 绝不出现在任何 bind 里
    assert (home + "/.karvyloop").replace("\\", "/") not in joined.replace("\\", "/")

    # 收集所有 bind/ro-bind 的源路径,断言无一是敏感路径
    bind_sources = [argv[i + 1] for i, a in enumerate(argv)
                    if a in ("--bind", "--ro-bind") and i + 1 < len(argv)]
    sensitive_bound = [b for b in bind_sources if is_sensitive_path(b)]
    assert not sensitive_bound, f"bwrap 竟 bind 了敏感路径:{sensitive_bound}"
    # 工作区确实在 bind 源里(证明是真拼了命令而非空跑)
    assert any(str(ws).replace("\\", "/") in b.replace("\\", "/") for b in bind_sources)
