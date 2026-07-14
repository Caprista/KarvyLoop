"""Windows 沙箱 Tier 3(RestrictedTokenSandbox)对抗验收 + 平台层纯逻辑单测。

分两块:
  1) 纯逻辑单测(_util):跨平台可跑,锁 skill-exec token 指纹 / argv 翻译 / token 闸 IO /
     ro-rw 派生。无 Win32 依赖。
  2) Tier 3 真对抗(真起受限进程):`sys.platform != win32` 自动 skip;win32 但探测不可用
     (锁定策略/杀软)也 skip。CI 非 Windows 全跳,不 block。

Tier 3 契约(对齐 bwrap/seatbelt「默认拒写 + 白名单」):
  - 合法样本:workspace 内写通过。
  - 恶意样本全拦:① 写 workspace 外 ② 写 %USERPROFILE% ③ 进程炸弹 ④ 吃内存。
  - 网络门做不满 → 带 net: 的 token fail-closed 拒跑(不假装隔离放行)。
  - 超时杀整棵进程树。

本机真跑记录见任务返回;这里锁成回归。为避开杀软对高频 CreateProcessAsUser 的瞬时拦截
(docs/48 A.4 风险②),真对抗只跑必要的少数 exec。
"""

from __future__ import annotations

import os
import sys

import pytest

from karvyloop.platform.win._util import (
    SKILL_EXEC_TASK_ID,
    is_skill_exec_token,
    resolve_argv,
    rw_ro_paths_with_grants,
    token_gated_read,
    token_gated_write,
)
from karvyloop.schemas import Capability, CapabilityToken

pytestmark = pytest.mark.security   # 安全套件:Windows Tier3 沙箱写隔离/进程炸弹对抗


def _tok(ws, *, net=False, ro=None):
    grants = [Capability(resource=f"fs:{ws}", ops=["read", "write"])]
    for p in (ro or []):
        grants.append(Capability(resource=f"fs:{p}", ops=["read"]))
    if net:
        grants.append(Capability(resource="net:api.example.com", ops=["connect"]))
    return CapabilityToken(task_id="t", grants=grants, expiry=9_999_999_999.0)


# ============================================================================
# 1) 纯逻辑单测(跨平台)
# ============================================================================

def test_skill_exec_token_fingerprint():
    """skill_exec 路径签发的 token(task_id=skill-exec)被识别为第三方脚本执行。"""
    from karvyloop.capability.skill_grants import token_for_skill

    class _FM:
        raw = {"trust": "untrusted", "source": "third-party"}
        allowed_tools = ["Read"]

    tok = token_for_skill(_FM(), skill_dir="/sk", workspace="/ws")
    assert tok.task_id == SKILL_EXEC_TASK_ID
    assert is_skill_exec_token(tok) is True
    # 普通第一方 token(cli/agent)不是 skill-exec
    assert is_skill_exec_token(_tok("/ws")) is False


def test_resolve_argv_translates_posix_heads():
    """win32:sh -c → cmd /c(无 sh 时);python3 → sys.executable(不在 PATH 时)。"""
    if os.name != "nt":
        pytest.skip("resolve_argv 仅在 win32 翻译")
    import shutil
    if shutil.which("sh") is None:
        out = resolve_argv(["sh", "-c", "echo hi"])
        assert out[:4] == ["cmd", "/d", "/s", "/c"] and out[4] == "echo hi"
    # python3/python 总是改成 sys.executable(绕开 WindowsApps App Execution Alias 的
    # WinError 1920;裸 python3 在 Windows 常解析成商店 alias reparse point)
    out = resolve_argv(["python3", "x.py"])
    assert out[0] == sys.executable and out[1:] == ["x.py"]
    out2 = resolve_argv(["python", "y.py"])
    assert out2[0] == sys.executable


def test_token_gated_write_read_roundtrip(tmp_path):
    tok = _tok(str(tmp_path))
    p = str(tmp_path / "f.txt")
    token_gated_write(p, b"data", tok)
    assert token_gated_read(p, tok) == b"data"


def test_token_gated_write_rejects_outside(tmp_path):
    tok = _tok(str(tmp_path))
    with pytest.raises(PermissionError):
        token_gated_write(str(tmp_path.parent / "evil.txt"), b"x", tok)


def test_rw_ro_paths_split(tmp_path):
    ro_dir = tmp_path / "ro"; ro_dir.mkdir()
    tok = _tok(str(tmp_path), ro=[str(ro_dir)])
    ro, rw = rw_ro_paths_with_grants(tok)
    assert str(tmp_path) in rw
    assert str(ro_dir) in ro


# ---- AppContainer 网络门:package SID 推导(纯逻辑,跨平台)----

def test_appcontainer_package_sid_is_stable_and_wellformed():
    """moniker → package SID 字符串:S-1-15-2 前缀 + 7 个 uint32,稳定可复现。"""
    from karvyloop.platform.win.appcontainer import (
        APPCONTAINER_SID_STRING,
        _sha256_appcontainer_sid_string,
    )
    sid = _sha256_appcontainer_sid_string("com.karvyloop.sandbox.nonet")
    assert sid.startswith("S-1-15-2-")
    parts = sid.split("-")
    assert len(parts) == 4 + 7          # "S","1","15","2" + 7 个 subauthority
    # 7 个 subauthority 全是 uint32
    for p in sid[len("S-1-15-2-"):].split("-"):
        assert 0 <= int(p) <= 0xFFFFFFFF
    # 稳定:同 moniker 同结果 + 模块常量一致
    assert _sha256_appcontainer_sid_string("com.karvyloop.sandbox.nonet") == sid
    assert APPCONTAINER_SID_STRING == sid
    # 不同 moniker → 不同 SID
    assert _sha256_appcontainer_sid_string("other.moniker") != sid


def test_appcontainer_sid_matches_known_windows_algorithm():
    """SID 推导对齐 Windows DeriveAppContainerSidFromAppContainerName(UTF-16LE 大写 SHA-256)。"""
    import hashlib
    import struct
    from karvyloop.platform.win.appcontainer import _sha256_appcontainer_sid_string
    moniker = "test.app"
    digest = hashlib.sha256(moniker.upper().encode("utf-16-le")).digest()[:28]
    expect = "S-1-15-2-" + "-".join(str(x) for x in struct.unpack("<7I", digest))
    assert _sha256_appcontainer_sid_string(moniker) == expect


# ============================================================================
# 2) Tier 3 真对抗(win32 + 探测可用才跑)
# ============================================================================

requires_tier3 = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Tier 3 RestrictedToken 仅 Windows(非 win32 CI 自动跳)",
)


def _sb():
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    if not RestrictedTokenSandbox.available():
        pytest.skip("RestrictedToken 探测不可用(锁定策略/杀软)—— 本机降 Tier 4")
    # 小内存/进程上限,便于 ③④ 快速触发
    return RestrictedTokenSandbox(job_memory_bytes=256 << 20, active_process_limit=4)


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_legit_workspace_write(tmp_path):
    """合法样本:workspace 内写通过。"""
    sb = _sb()
    tok = _tok(str(tmp_path))
    tgt = str(tmp_path / "ok.txt")
    r = await sb.exec([sys.executable, "-c", f"open(r'{tgt}','w').write('hi'); print('OK')"],
                      token=tok, cwd=str(tmp_path), timeout_s=60)
    assert r.exit_code == 0, r.stderr[-400:]
    assert (tmp_path / "ok.txt").read_text() == "hi"


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_write_outside_workspace_blocked(tmp_path):
    """① 写 workspace 外 → 拒(文件不产生)。"""
    sb = _sb()
    ws = tmp_path / "ws"; ws.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    tok = _tok(str(ws))
    evil = str(outside / "evil.txt")
    r = await sb.exec([sys.executable, "-c", f"open(r'{evil}','w').write('x')"],
                      token=tok, cwd=str(ws), timeout_s=60)
    assert r.exit_code != 0
    assert not (outside / "evil.txt").exists(), "workspace 外被写穿(写隔离失效!)"


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_write_userprofile_blocked(tmp_path):
    """② 写 %USERPROFILE% → 拒。"""
    sb = _sb()
    tok = _tok(str(tmp_path))
    up = os.path.join(os.environ["USERPROFILE"], "klsbx_test_evil.txt")
    if os.path.exists(up):
        os.remove(up)
    r = await sb.exec([sys.executable, "-c", f"open(r'{up}','w').write('x')"],
                      token=tok, cwd=str(tmp_path), timeout_s=60)
    assert r.exit_code != 0
    assert not os.path.exists(up), "%USERPROFILE% 被写穿(写隔离失效!)"


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_process_bomb_capped(tmp_path):
    """③ 进程炸弹 → Job ActiveProcessLimit 拦(第 N 个 spawn 报配额不足 WinError 1816)。"""
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    if not RestrictedTokenSandbox.available():
        pytest.skip("RestrictedToken 探测不可用")
    # 用 limit=2:parent 占 1,第 1 个子进程后即到顶,fork bomb 必被拦。
    sb = RestrictedTokenSandbox(job_memory_bytes=256 << 20, active_process_limit=2)
    tok = _tok(str(tmp_path))
    bomb = (
        "import subprocess,sys\n"
        "ok=0\n"
        "for i in range(20):\n"
        "  try:\n"
        "    subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']); ok+=1\n"
        "  except Exception:\n"
        "    print('CAPPED_AT', ok); break\n"
        "else:\n"
        "  print('NOT_CAPPED', ok)\n"
    )
    r = await sb.exec([sys.executable, "-c", bomb], token=tok, cwd=str(tmp_path), timeout_s=40)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert "CAPPED_AT" in out and "NOT_CAPPED" not in out, f"进程炸弹未被 Job 拦:{out[-200:]}"


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_memory_bomb_blocked(tmp_path):
    """④ 吃内存(>256MiB job 上限)→ 被 Job 内存门拦(MemoryError / 非 0 退出)。"""
    sb = _sb()   # job_memory_bytes=256MiB
    tok = _tok(str(tmp_path))
    # 成功标记放进变量、只在成功分支 print,失败时的 traceback 只会回显源码里的 mem 串,
    # 不会出现 SUCCESS_MARK —— 避免"源码被回显"污染断言。
    mem = ("b = bytearray(1024*1024*1024)\n"
           "print('BIGALLOC_DONE', len(b))\n")
    r = await sb.exec([sys.executable, "-c", mem], token=tok, cwd=str(tmp_path), timeout_s=40)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.exit_code != 0, f"内存炸弹未被 Job 拦(应非 0 退出):{out[-200:]}"
    assert "BIGALLOC_DONE" not in r.stdout.decode("utf-8", "replace"), \
        f"1GiB 分配竟成功(内存门失效!):{out[-200:]}"


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_net_fail_closed(tmp_path):
    """网络门做不满 → 带 net: 的 token fail-closed 拒跑(错误如实说,不假装放行)。"""
    sb = _sb()
    tok = _tok(str(tmp_path), net=True)
    with pytest.raises(PermissionError) as ei:
        await sb.exec([sys.executable, "-c", "print(1)"], token=tok, cwd=str(tmp_path), timeout_s=20)
    msg = str(ei.value)
    assert "net" in msg and ("fail-closed" in msg or "拒跑" in msg)


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_timeout_kills_tree(tmp_path):
    """超时 → TerminateJobObject 杀整棵树,timed_out=True。"""
    sb = _sb()
    tok = _tok(str(tmp_path))
    r = await sb.exec([sys.executable, "-c", "import time; time.sleep(60)"],
                      token=tok, cwd=str(tmp_path), timeout_s=2)
    assert r.timed_out is True and r.exit_code != 0


@requires_tier3
@pytest.mark.asyncio
async def test_tier3_third_party_skill_runs_sandboxed(tmp_path):
    """v1 进步:第三方技能脚本(读取自身 script)在 Tier 3 真跑通(读放宽 + 白名单写)。

    新契约:AppContainer 探不通的机器,三方技能 fail-closed 拒跑(有专测锁)—— 此处的
    "真跑通"样本只在有内核网络门的机器上适用,探不通则 skip。
    """
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    from karvyloop.registry.skill_exec import run_skill_script
    if RestrictedTokenSandbox.available() and not RestrictedTokenSandbox.appcontainer_available():
        pytest.skip("本机 AppContainer 探不通 —— 三方技能 fail-closed 拒跑(新契约),真跑通样本不适用")
    d = tmp_path / "sk" / "demo"; (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\nsource: third-party\ntrust: untrusted\nsignature: imp\n---\n# d\n",
        encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print('THIRD_PARTY_RAN')\n", encoding="utf-8")
    ws = tmp_path / "ws"; ws.mkdir()
    sb = _sb()
    r = await run_skill_script(str(d), "scripts/run.py", sandbox=sb, workspace=str(ws), timeout_s=60)
    assert r.exit_code == 0, r.stderr[-400:]
    assert b"THIRD_PARTY_RAN" in r.stdout


# ---- AppContainer 网络门:第三方脚本默认拒网(真对抗)----

def _skill_tok(ws, *, net=False):
    """构造 skill-exec 指纹的 token(task_id='skill-exec'),模拟第三方脚本执行路径。"""
    grants = [Capability(resource=f"fs:{ws}", ops=["read", "write"])]
    if net:
        grants.append(Capability(resource="net:*", ops=["connect"]))
    return CapabilityToken(task_id=SKILL_EXEC_TASK_ID, grants=grants, expiry=9_999_999_999.0)


@requires_tier3
def test_appcontainer_probe_runs(tmp_path):
    """appcontainer_available() 真探一次(造 LowBox + 起 cmd);只验不崩、返回 bool。"""
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    if not RestrictedTokenSandbox.available():
        pytest.skip("RestrictedToken 探测不可用")
    RestrictedTokenSandbox._appc_cache = None   # 强制重探
    assert isinstance(RestrictedTokenSandbox.appcontainer_available(), bool)


@requires_tier3
@pytest.mark.asyncio
async def test_appcontainer_third_party_default_no_net_blocked(tmp_path):
    """第三方 skill-exec token(无 net 声明)→ 默认拒网:AppContainer 可用则出站被内核拒。

    若本机 AppContainer 探不通(个别机器/杀软)→ skip(新契约:此时三方技能 fail-closed
    拒跑,有专测锁,此处的"内核拒出站"断言不适用)。有 AppContainer 时,出站 connect
    必失败(WFP 默认规则)。
    """
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    if not RestrictedTokenSandbox.available():
        pytest.skip("RestrictedToken 探测不可用")
    if not RestrictedTokenSandbox.appcontainer_available():
        pytest.skip("本机 AppContainer 探不通 —— 三方技能 fail-closed 拒跑(专测锁),此断言不适用")
    sb = RestrictedTokenSandbox(job_memory_bytes=256 << 20, active_process_limit=8)
    tok = _skill_tok(str(tmp_path))   # 第三方、无 net
    # 尝试出站 TCP connect(1.1.1.1:53);AppContainer 无 internetClient → 内核 WFP 拒。
    probe = (
        "import socket, sys\n"
        "try:\n"
        "    s = socket.create_connection(('1.1.1.1', 53), timeout=5)\n"
        "    s.close(); print('NET_OK')\n"
        "except Exception as e:\n"
        "    print('NET_BLOCKED', type(e).__name__)\n"
    )
    r = await sb.exec([sys.executable, "-c", probe], token=tok, cwd=str(tmp_path), timeout_s=40)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert "NET_OK" not in out, f"第三方脚本竟连上外网(网络门失效!):{out[-200:]}"
    assert "NET_BLOCKED" in out, f"未观察到连接被拒:{out[-200:]}"


@requires_tier3
@pytest.mark.asyncio
async def test_appcontainer_third_party_can_still_write_workspace(tmp_path):
    """AppContainer 无网门下,第三方脚本仍能写自己的 workspace(ALL-APP-PACKAGES 白名单)。"""
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    if not RestrictedTokenSandbox.available():
        pytest.skip("RestrictedToken 探测不可用")
    if not RestrictedTokenSandbox.appcontainer_available():
        pytest.skip("本机 AppContainer 探不通(三方技能 fail-closed 拒跑,写通样本不适用)")
    sb = RestrictedTokenSandbox(job_memory_bytes=256 << 20, active_process_limit=8)
    tok = _skill_tok(str(tmp_path))
    tgt = str(tmp_path / "appc_ok.txt")
    r = await sb.exec([sys.executable, "-c", f"open(r'{tgt}','w').write('hi'); print('WROTE')"],
                      token=tok, cwd=str(tmp_path), timeout_s=60)
    assert r.exit_code == 0, r.stderr[-400:]
    assert (tmp_path / "appc_ok.txt").read_text() == "hi"


@requires_tier3
@pytest.mark.asyncio
async def test_appcontainer_third_party_net_grant_still_fail_closed(tmp_path):
    """第三方 skill-exec token 带 net: grant → 仍 fail-closed 拒跑(放行特定网络需 admin)。"""
    sb = _sb()
    tok = _skill_tok(str(tmp_path), net=True)
    with pytest.raises(PermissionError) as ei:
        await sb.exec([sys.executable, "-c", "print(1)"], token=tok, cwd=str(tmp_path), timeout_s=20)
    assert "net" in str(ei.value)


def test_skill_exec_without_appcontainer_fail_closed(tmp_path, monkeypatch):
    """AppContainer 探不通 + 三方 skill-exec token → fail-closed PermissionError。

    旧行为:静默回退到只限写、不限网的受限令牌(“默认断网”落空且无信号)。新契约:
    拒跑并说清原因,一个子进程都不起(纯逻辑,monkeypatch 桩,跨平台可跑)。
    """
    import asyncio
    from karvyloop.platform.win import restricted as R

    sb = R.RestrictedTokenSandbox.__new__(R.RestrictedTokenSandbox)
    sb.job_memory_bytes = 1 << 20
    sb.active_process_limit = 2
    monkeypatch.setattr(R.RestrictedTokenSandbox, "available", staticmethod(lambda: True))
    monkeypatch.setattr(R.RestrictedTokenSandbox, "appcontainer_available",
                        staticmethod(lambda: False))
    spawned = []
    # _run_restricted 是 if _IS_WIN 块内符号,非 Windows CI 上不存在 → raising=False
    # 缺失即创建(本测是跨平台纯逻辑桩测:验 exec 分支,不起真 Windows 进程)。
    monkeypatch.setattr(R, "_run_restricted", lambda *a, **k: spawned.append(a), raising=False)
    tok = _skill_tok(str(tmp_path))   # 第三方、无 net
    with pytest.raises(PermissionError) as ei:
        asyncio.run(sb.exec(["python", "-c", "print(1)"], token=tok, cwd=str(tmp_path)))
    msg = str(ei.value)
    assert "AppContainer" in msg and "fail-closed" in msg and "第三方技能" in msg
    assert not spawned, "fail-closed 应在起任何子进程之前拦下"


def test_skill_exec_with_appcontainer_unchanged(tmp_path, monkeypatch):
    """AppContainer 可用 → 行为不变:三方技能照跑且 net_isolated=True(LowBox 内核门)。"""
    import asyncio
    from karvyloop.platform.win import restricted as R
    from karvyloop.sandbox.exec_result import ExecResult

    seen = {}

    def fake_run(argv, cwd, stdin, timeout_s, max_output_bytes, rw_paths,
                 job_memory, proc_limit, ro_paths=None, net_isolated=False,
                 appc_ro_dirs=None):
        seen["net_isolated"] = net_isolated
        return ExecResult(stdout=b"ran", stderr=b"", exit_code=0)

    sb = R.RestrictedTokenSandbox.__new__(R.RestrictedTokenSandbox)
    sb.job_memory_bytes = 1 << 20
    sb.active_process_limit = 2
    monkeypatch.setattr(R.RestrictedTokenSandbox, "available", staticmethod(lambda: True))
    monkeypatch.setattr(R.RestrictedTokenSandbox, "appcontainer_available",
                        staticmethod(lambda: True))
    monkeypatch.setattr(R, "_run_restricted", fake_run, raising=False)  # 非 Win 上缺失即创建
    monkeypatch.setattr(R, "resolve_argv", lambda a: a)
    tok = _skill_tok(str(tmp_path))
    r = asyncio.run(sb.exec(["python", "-c", "print(1)"], token=tok, cwd=str(tmp_path)))
    assert r.exit_code == 0 and r.stdout == b"ran"
    assert seen["net_isolated"] is True


# ---- sh DLL 初始化即死(0xC0000142)→ 按失败签名换 cmd 重跑(J22 实捕:MSYS sh 在
# 受限令牌下起不来,resolve_argv 只兜"sh 不存在",盲区=存在但跑不动 → run_command 全灭) ----

def test_cmd_shell_fallback_shape():
    from karvyloop.platform.win import restricted as R
    assert R._cmd_shell_fallback(["sh", "-c", "echo hi"]) == ["cmd", "/d", "/s", "/c", "echo hi"]
    assert R._cmd_shell_fallback(["bash", "-c", "x", "extra"]) == ["cmd", "/d", "/s", "/c", "x", "extra"]
    assert R._cmd_shell_fallback(["python", "-V"]) is None      # 只兜 shell 形态
    assert R._cmd_shell_fallback(["sh", "script.sh"]) is None   # 非 -c 形态不兜


def test_sh_dll_init_failed_retries_with_cmd(monkeypatch):
    """exit=0xC0000142 且头是 sh/bash → 换 cmd 重跑一次;其余退出码零行为漂移。"""
    import asyncio
    from karvyloop.platform.win import restricted as R
    from karvyloop.sandbox.exec_result import ExecResult
    from karvyloop.schemas import CapabilityToken

    calls = []

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        if argv[0] in ("sh", "bash"):
            return ExecResult(stdout=b"", stderr=b"", exit_code=0xC0000142)
        return ExecResult(stdout=b"hi", stderr=b"", exit_code=0)

    sb = R.RestrictedTokenSandbox.__new__(R.RestrictedTokenSandbox)
    sb.job_memory_bytes = 1 << 20
    sb.active_process_limit = 2
    monkeypatch.setattr(R, "_run_restricted", fake_run, raising=False)  # 非 Win 上缺失即创建
    monkeypatch.setattr(R.RestrictedTokenSandbox, "available", staticmethod(lambda: True))
    monkeypatch.setattr(R.RestrictedTokenSandbox, "appcontainer_available", staticmethod(lambda: False))
    monkeypatch.setattr(R, "resolve_argv", lambda a: a)   # 保持 sh 头,直击兜底路径
    tok = CapabilityToken(task_id="t", grants=(), expiry=9_999_999_999.0)
    r = asyncio.run(sb.exec(["sh", "-c", "echo hi"], token=tok, cwd="."))
    assert r.exit_code == 0 and r.stdout == b"hi"
    assert calls[0][0] == "sh" and calls[1][:4] == ["cmd", "/d", "/s", "/c"]

    calls.clear()
    r2 = asyncio.run(sb.exec(["python", "-V"], token=tok, cwd="."))  # 非 shell 头:死了也不兜
    assert len(calls) == 1
