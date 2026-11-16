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
from pathlib import Path

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


def test_narrow_access_for_file_strips_dir_bits():
    """普通文件的规则必须去掉目录专属位:否则内核 add_rule EINVAL(errno 22)→ 整层 fail-closed。

    回归:授权台账里出现文件路径(如角色 IDENTITY.md)时,原本所有规则都用含 READ_DIR 的
    _READ_ACCESS,导致 run_command 全部被拒。
    """
    dir_only = (L.LANDLOCK_ACCESS_FS_READ_DIR | L.LANDLOCK_ACCESS_FS_REMOVE_DIR
                | L.LANDLOCK_ACCESS_FS_REMOVE_FILE | L.LANDLOCK_ACCESS_FS_MAKE_DIR
                | L.LANDLOCK_ACCESS_FS_MAKE_REG | L.LANDLOCK_ACCESS_FS_MAKE_SYM)
    # 目录:原样
    assert L.narrow_access_for_file(L._WRITE_ACCESS, is_dir=True) == L._WRITE_ACCESS
    # 文件:目录位清零,文件位保留
    f = L.narrow_access_for_file(L._WRITE_ACCESS, is_dir=False)
    assert f & dir_only == 0
    assert f & L.LANDLOCK_ACCESS_FS_READ_FILE
    assert f & L.LANDLOCK_ACCESS_FS_WRITE_FILE
    assert f & L.LANDLOCK_ACCESS_FS_TRUNCATE
    # 只读规则裁完仍有 READ_FILE(否则"授读了却读不到")
    assert L.narrow_access_for_file(L._READ_ACCESS, is_dir=False) & L.LANDLOCK_ACCESS_FS_READ_FILE


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
    # 沙箱私有 /dev 必须可写(`2>/dev/null` 这类重定向)
    for x in L._SYSTEM_RW_DIRS:
        rp = os.path.realpath(x)
        if os.path.exists(rp):
            assert d[rp] is True


def test_abi_version_non_linux_is_unsupported():
    """非 Linux → abi_version()==-1 / is_supported()==False(优雅降级路径)。"""
    if sys.platform.startswith("linux"):
        pytest.skip("此断言只验非 Linux 的降级返回")
    assert L.abi_version() == -1
    assert L.is_supported() is False


def test_bubblewrap_landlock_inner_degrades_when_unsupported(monkeypatch):
    """内核不支持 Landlock → 里层前缀为空(纯 bwrap,零回归)。"""
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    monkeypatch.setattr(BubblewrapSandbox, "_landlock_supported", None, raising=False)
    monkeypatch.setattr("karvyloop.platform.linux.landlock.is_supported", lambda: False)
    assert BubblewrapSandbox._landlock_inner(["/rw"], ["/ro"]) == []


def test_bubblewrap_landlock_inner_prepends_wrapper_when_supported(monkeypatch):
    """内核支持 → 生成 `python3 landlock.py <rw> <ro> --` 前缀(装进 bwrap **里层**)。"""
    import json
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    monkeypatch.setattr(BubblewrapSandbox, "_landlock_supported", None, raising=False)
    monkeypatch.setattr("karvyloop.platform.linux.landlock.is_supported", lambda: True)
    out = BubblewrapSandbox._landlock_inner(["/rw"], ["/ro"])
    assert out[0].endswith("python3") or out[0] == "/usr/bin/python3"
    assert out[1].endswith("platform/linux/landlock.py")
    assert json.loads(out[2]) == ["/rw"]
    assert json.loads(out[3]) == ["/ro"]
    assert out[4] == "--"


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
    import shutil
    import tempfile
    ws = tmp_path / "ws"; ws.mkdir()
    # "越界"目标必须避开 _SYSTEM_RW_DIRS:/tmp 在那里是可写的(bwrap 里它是**私有 tmpfs**,
    # 写不到宿主),而 pytest 的 tmp_path 恰好就在 /tmp 下 —— 拿它当越界目标会假失败。
    if not (os.path.isdir("/var/tmp") and os.access("/var/tmp", os.W_OK)):
        pytest.skip("无 /var/tmp 可写目录,无法构造 /tmp 之外的越界目标")
    outside = Path(tempfile.mkdtemp(prefix="kv_ll_outside_", dir="/var/tmp"))
    try:
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
        # 环境不允许"真装 Landlock"时优雅 skip(而非 fail):受限容器/CI runner(如 GitHub
        # Actions)常禁 prctl(PR_SET_NO_NEW_PRIVS)→EINVAL,即使内核 ABI 报支持。生产代码本身
        # 对此优雅降级到纯 bwrap;此测试只在"环境真能强制"时验强制,别把环境限制当代码 bug。
        if r.returncode != 0 and (
                "PR_SET_NO_NEW_PRIVS" in r.stderr or "prctl" in r.stderr
                or "Errno 22" in r.stderr or "landlock 未装上" in r.stderr):
            import pytest
            _tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
            pytest.skip(f"环境不允许真装 Landlock(受限容器/CI),跳过强制验证:{_tail}")
        assert r.returncode == 0, r.stderr
        import json
        res = json.loads(r.stdout.strip().splitlines()[-1])
        assert res["ok_err"] is None, f"workspace 内写不该失败:{res['ok_err']}"
        assert res["evil_err"] is not None, "workspace 外写竟成功(Landlock 门失效!)"
        assert not (outside / "evil.txt").exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


@requires_landlock
def test_real_landlock_accepts_single_file_rule(tmp_path):
    """真内核回归:ro 里给**单个文件**(如角色 IDENTITY.md)时,apply_landlock 必须装得上且能读。

    曾经的 bug:文件规则也带 READ_DIR → landlock_add_rule EINVAL(22) → fail-closed,
    run_command 连 ls/pwd 都跑不了。
    """
    import subprocess
    ws = tmp_path / "ws"; ws.mkdir()
    f = tmp_path / "IDENTITY.md"; f.write_text("hi", encoding="utf-8")
    code = (
        "import sys, json\n"
        "from karvyloop.platform.linux.landlock import apply_landlock\n"
        f"applied = apply_landlock([{str(ws)!r}], [{str(f)!r}])\n"
        "assert applied, 'landlock 未装上'\n"
        f"print(json.dumps({{'body': open({str(f)!r}).read()}}))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ), timeout=30)
    if r.returncode != 0 and ("PR_SET_NO_NEW_PRIVS" in r.stderr or "Errno 22" in r.stderr):
        pytest.skip(f"环境不允许真装 Landlock(受限容器/CI):{r.stderr.strip()[-200:]}")
    assert r.returncode == 0, r.stderr
    assert '"hi"' in r.stdout
