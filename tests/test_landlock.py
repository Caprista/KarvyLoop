"""Landlock 深度防御(platform/linux/landlock.py)—— 纯逻辑单测 + Linux 真内核对抗。

分两块:
  1) 纯逻辑(跨平台,Windows CI 也跑):ABI 掩码降级 / attr 字节打包 / 规则规划 / wrapper 拼装。
     锁住"旧核不认新权限位就去掉"这个 best-effort 契约,防回归。
  2) Linux 真内核(sys.platform != linux 自动 skip;内核不支持 Landlock 也 skip):真装 Landlock
     后 workspace 内可写、workspace 外写被内核拒。CI 非 Linux 全跳,不 block。

不支持 Landlock 的内核 → apply_landlock 返回 False(优雅降级),bwrap 行为零回归。
"""

from __future__ import annotations

import os
import sys

import pytest

from karvyloop.platform.linux import landlock as L


# ============================================================================
# 1) 纯逻辑(跨平台)
# ============================================================================

def test_access_fs_abi_downgrade():
    """ABI<2 去 REFER、<3 去 TRUNCATE、<5 去 IOCTL_DEV;ABI≥5 全保留。"""
    a5 = L.access_fs_for_abi(5)
    assert a5 & L.LANDLOCK_ACCESS_FS_IOCTL_DEV
    assert a5 & L.LANDLOCK_ACCESS_FS_TRUNCATE
    assert a5 & L.LANDLOCK_ACCESS_FS_REFER

    a4 = L.access_fs_for_abi(4)
    assert not (a4 & L.LANDLOCK_ACCESS_FS_IOCTL_DEV)   # <5 去 IOCTL_DEV
    assert a4 & L.LANDLOCK_ACCESS_FS_TRUNCATE

    a2 = L.access_fs_for_abi(2)
    assert not (a2 & L.LANDLOCK_ACCESS_FS_TRUNCATE)    # <3 去 TRUNCATE
    assert a2 & L.LANDLOCK_ACCESS_FS_REFER

    a1 = L.access_fs_for_abi(1)
    assert not (a1 & L.LANDLOCK_ACCESS_FS_REFER)       # <2 去 REFER
    assert not (a1 & L.LANDLOCK_ACCESS_FS_TRUNCATE)
    assert not (a1 & L.LANDLOCK_ACCESS_FS_IOCTL_DEV)
    # 基础读写位 ABI 1 就有
    assert a1 & L.LANDLOCK_ACCESS_FS_READ_FILE
    assert a1 & L.LANDLOCK_ACCESS_FS_WRITE_FILE


def test_rule_access_is_subset_of_handled():
    """任一路径规则的 allowed_access 必须是 ruleset handled_access_fs 的子集(内核硬要求)。"""
    for abi in (1, 2, 3, 4, 5):
        handled = L.access_fs_for_abi(abi)
        write_rule = L.rule_access_for_abi(L._WRITE_ACCESS, abi)
        read_rule = L.rule_access_for_abi(L._READ_ACCESS, abi)
        assert write_rule & ~handled == 0, f"ABI{abi} write 规则越出 handled"
        assert read_rule & ~handled == 0, f"ABI{abi} read 规则越出 handled"
        # 读规则不含任何写位
        assert not (read_rule & L.LANDLOCK_ACCESS_FS_WRITE_FILE)


def test_pack_ruleset_attr_layout():
    """struct landlock_ruleset_attr = u64 handled_fs + u64 handled_net + u64 scoped。"""
    b = L.pack_ruleset_attr(0xABCD)
    assert len(b) == 24
    assert int.from_bytes(b[0:8], "little") == 0xABCD
    assert int.from_bytes(b[8:16], "little") == 0     # net 不管
    assert int.from_bytes(b[16:24], "little") == 0    # scoped 不管


def test_pack_path_beneath_attr_layout():
    """struct landlock_path_beneath_attr = u64 allowed_access + s32 parent_fd(+pad)。"""
    b = L.pack_path_beneath_attr(0x7, 5)
    assert len(b) == 16   # 12 有效 + 4 pad(8 对齐)
    assert int.from_bytes(b[0:8], "little") == 0x7
    assert int.from_bytes(b[8:12], "little", signed=True) == 5


def test_plan_fs_rules_marks_rw_vs_ro(tmp_path):
    """workspace(rw)标 is_write=True;系统目录标只读;不存在的路径丢弃;去重。"""
    ws = tmp_path / "ws"; ws.mkdir()
    ro = tmp_path / "ro"; ro.mkdir()
    ghost = str(tmp_path / "nope")   # 不存在
    plan = L.plan_fs_rules([str(ws), ghost, str(ws)], [str(ro)])
    d = {p: w for p, w in plan}
    assert d[os.path.realpath(str(ws))] is True
    assert d[os.path.realpath(str(ro))] is False
    assert os.path.realpath(ghost) not in d              # 不存在的被丢
    # 系统目录(至少一个存在的)被加进来且只读
    sys_ro = [p for p, w in plan if p in {os.path.realpath(x) for x in L._SYSTEM_RO_DIRS}]
    for p in sys_ro:
        assert d[p] is False


def test_abi_version_non_linux_is_unsupported():
    """非 Linux → abi_version()==-1 / is_supported()==False(优雅降级路径)。"""
    if sys.platform.startswith("linux"):
        pytest.skip("此断言只验非 Linux 的降级返回")
    assert L.abi_version() == -1
    assert L.is_supported() is False


def test_bubblewrap_wrap_landlock_degrades_when_unsupported(monkeypatch):
    """内核不支持 Landlock → _wrap_landlock 原样返回 bwrap(零回归)。"""
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    monkeypatch.setattr(BubblewrapSandbox, "_landlock_supported", None, raising=False)
    monkeypatch.setattr("karvyloop.platform.linux.landlock.is_supported", lambda: False)
    bwrap = ["bwrap", "--die-with-parent", "--", "echo", "hi"]
    out = BubblewrapSandbox._wrap_landlock(list(bwrap), ["/ro"], ["/rw"])
    assert out == bwrap   # 未包裹


def test_bubblewrap_wrap_landlock_prepends_wrapper_when_supported(monkeypatch):
    """内核支持 → _wrap_landlock 前置 `python -m ...landlock <rw> <ro> -- bwrap …`。"""
    import json
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    monkeypatch.setattr(BubblewrapSandbox, "_landlock_supported", None, raising=False)
    monkeypatch.setattr("karvyloop.platform.linux.landlock.is_supported", lambda: True)
    bwrap = ["bwrap", "--", "echo", "hi"]
    out = BubblewrapSandbox._wrap_landlock(list(bwrap), ["/ro"], ["/rw"])
    assert out[0] == sys.executable
    assert out[1:3] == ["-m", "karvyloop.platform.linux.landlock"]
    assert json.loads(out[3]) == ["/rw"]
    assert json.loads(out[4]) == ["/ro"]
    assert out[5] == "--"
    assert out[6:] == bwrap   # wrapper 后原样接 bwrap


def test_wrapper_main_bad_args():
    """wrapper 参数缺 -- / 缺 cmd → 非 0 退出,不 exec。"""
    assert L._main(["only", "two"]) == 2                 # 无 --
    assert L._main(["rw", "ro", "--"]) == 2              # -- 后无 cmd
    assert L._main(["[]", "notjson", "--", "echo"]) == 2  # JSON 坏


# ============================================================================
# 2) Linux 真内核对抗(linux + Landlock 支持才跑)
# ============================================================================

requires_landlock = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and L.is_supported()),
    reason="需 Linux + 内核 Landlock(ABI≥1)",
)


@requires_landlock
def test_real_landlock_blocks_write_outside_workspace(tmp_path):
    """真装 Landlock:子进程 workspace 内可写、workspace 外写被内核拒。

    用子进程(fork)装 Landlock 再试写 —— 不污染测试进程本身(Landlock 不可逆)。
    """
    import subprocess
    ws = tmp_path / "ws"; ws.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    # 子进程:装 Landlock(rw=ws)后,写 ws/ok(应成)+ 写 outside/evil(应被拒)
    code = (
        "import sys, json\n"
        "from karvyloop.platform.linux.landlock import apply_landlock\n"
        f"applied = apply_landlock([{str(ws)!r}], [])\n"
        "assert applied, 'landlock 未装上'\n"
        "ok_err = evil_err = None\n"
        "try:\n"
        f"    open({str(ws / 'ok.txt')!r}, 'w').write('x')\n"
        "except Exception as e:\n"
        "    ok_err = repr(e)\n"
        "try:\n"
        f"    open({str(outside / 'evil.txt')!r}, 'w').write('x')\n"
        "except Exception as e:\n"
        "    evil_err = repr(e)\n"
        "print(json.dumps({'ok_err': ok_err, 'evil_err': evil_err}))\n"
    )
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    import json
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["ok_err"] is None, f"workspace 内写不该失败:{res['ok_err']}"
    assert res["evil_err"] is not None, "workspace 外写竟成功(Landlock 门失效!)"
    assert not (outside / "evil.txt").exists()
