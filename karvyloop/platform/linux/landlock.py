"""platform/linux/landlock.py —— Linux Landlock LSM 文件系统门(内核级路径规则,纯 syscall)。

叠在 bubblewrap 之上做**深度防御**:bwrap 用 mount namespace 隔离(--ro-bind/--bind),
Landlock 再加一层**内核 LSM 路径规则**(workspace 可读写、系统 bin 只读、其余默认拒)。
两层独立:即便某层被绕(bwrap 挂载配置漏洞 / namespace 逃逸),另一层仍拦。Landlock 是
Linux 5.13+ 自带、**无新依赖、免特权**(no_new_privs 即可,不需要 CAP_SYS_ADMIN)。

机制(纯 ctypes 手绑三个 syscall,业界同款做法,clean-room 只借机制):
  landlock_create_ruleset(&attr, sizeof, 0)  → ruleset_fd,attr.handled_access_fs=要管的权限集
  landlock_add_rule(fd, PATH_BENEATH, &{allowed_access, parent_fd=open(dir,O_PATH)}, 0)  逐路径放行
  prctl(PR_SET_NO_NEW_PRIVS, 1)  →  landlock_restrict_self(fd, 0)  自缚(及所有后代,execve 继承)

关键:Landlock ruleset **execve 后仍继承**(man landlock:后代继承 domain 限制)。所以本模块
以**前置 wrapper**方式用:`python -m karvyloop.platform.linux.landlock <rw> <ro> -- bwrap …` ——
先在 wrapper 进程装 Landlock(放行 bwrap 要读的系统目录 + workspace 可写),再 execve 成 bwrap;
bwrap 及其内所有子进程都继承这层内核路径门。

优雅降级(硬要求):
  - 内核不支持 Landlock(旧核 / 未编译 LSM)→ ABI 探测返回 <1 或 ENOSYS → **跳过这层**,
    纯 bwrap 继续跑(fail-closed 语义不变:bwrap 的挂载隔离 + --unshare-net 照旧)。日志说明。
  - handled_access_fs 按**探测到的 ABI 版本**降级(旧核不认新权限位 → create_ruleset EINVAL):
    ABI<2 去 REFER、ABI<3 去 TRUNCATE、ABI<5 去 IOCTL_DEV(best-effort,man landlock 同款)。

纯逻辑(常量 / ABI 掩码 / attr 打包)平台无关可单测(Windows CI 也跑);syscall 仅 Linux 生效。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from typing import Optional

# ---- Landlock FS 访问权限位(uapi/linux/landlock.h,稳定 ABI)----
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13        # ABI 2+
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14     # ABI 3+
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15    # ABI 5+

#: 读一个文件/目录需要的权限(READ_FILE + READ_DIR + EXECUTE:让只读目录也能被遍历/执行其中的 bin)
_READ_ACCESS = (
    LANDLOCK_ACCESS_FS_READ_FILE
    | LANDLOCK_ACCESS_FS_READ_DIR
    | LANDLOCK_ACCESS_FS_EXECUTE
)

#: 写一个目录树需要的全部权限(读 + 写 + 增删 + REFER/TRUNCATE 由 ABI 掩码再裁)
_WRITE_ACCESS = (
    _READ_ACCESS
    | LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_CHAR
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SOCK
    | LANDLOCK_ACCESS_FS_MAKE_FIFO
    | LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_REFER
    | LANDLOCK_ACCESS_FS_TRUNCATE
)

#: 全集(ruleset 的 handled_access_fs 起点,再按 ABI 掩码)
_ALL_FS_ACCESS = _WRITE_ACCESS | LANDLOCK_ACCESS_FS_IOCTL_DEV

#: bwrap 及常见解释器/工具要读执行的系统目录(与 bubblewrap._bind_binary_parents 对齐)
_SYSTEM_RO_DIRS = (
    "/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin",
    "/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/libexec",
    "/etc", "/proc", "/sys", "/dev", "/usr/share", "/opt", "/run",
)

# ---- syscall 号(x86_64 / arm64 一致:create=444, add_rule=445, restrict_self=446)----
# 这几个号在所有主流架构上相同(Landlock 是较新 syscall,统一编号)。
_NR_landlock_create_ruleset = 444
_NR_landlock_add_rule = 445
_NR_landlock_restrict_self = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_O_PATH = 0o10000000


def access_fs_for_abi(abi: int) -> int:
    """按探测到的 ABI 版本裁掉内核不认的权限位(否则 create_ruleset EINVAL)。

    ABI<2 去 REFER、ABI<3 去 TRUNCATE、ABI<5 去 IOCTL_DEV(man landlock best-effort 同款)。
    纯函数,平台无关可单测。
    """
    access = _ALL_FS_ACCESS
    if abi < 5:
        access &= ~LANDLOCK_ACCESS_FS_IOCTL_DEV
    if abi < 3:
        access &= ~LANDLOCK_ACCESS_FS_TRUNCATE
    if abi < 2:
        access &= ~LANDLOCK_ACCESS_FS_REFER
    return access


def rule_access_for_abi(base: int, abi: int) -> int:
    """把某条路径规则的 allowed_access 裁到当前 ABI 支持集(必须是 handled 的子集)。"""
    return base & access_fs_for_abi(abi)


def pack_ruleset_attr(handled_access_fs: int) -> bytes:
    """struct landlock_ruleset_attr 的字节打包(__u64 handled_access_fs; __u64 handled_access_net;
    __u64 scoped)。只管 fs,net/scoped 置 0。纯函数可单测(不依赖内核)。"""
    return (handled_access_fs.to_bytes(8, "little")
            + (0).to_bytes(8, "little")     # handled_access_net
            + (0).to_bytes(8, "little"))    # scoped


def pack_path_beneath_attr(allowed_access: int, parent_fd: int) -> bytes:
    """struct landlock_path_beneath_attr 的字节打包(__u64 allowed_access; __s32 parent_fd)。

    结构后有 4 字节 padding 到 8 对齐 —— syscall 只读前 12 字节有效,补齐无害。纯函数可单测。
    """
    return (allowed_access.to_bytes(8, "little")
            + int(parent_fd).to_bytes(4, "little", signed=True)
            + b"\x00\x00\x00\x00")


def plan_fs_rules(rw_paths: list[str], ro_paths: list[str]) -> list[tuple[str, bool]]:
    """规划要放行的 (path, is_write) 列表:workspace/授权 rw 可写,系统目录 + ro 授权只读。

    去重、只保留真实存在的路径。纯函数(不 syscall)可单测。系统目录固定只读(深度防御:
    即便脚本拿到系统 bin 也只能读/执行、不能改)。
    """
    plan: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for p in rw_paths:
        rp = os.path.realpath(p)
        if rp in seen or not os.path.exists(rp):
            continue
        seen.add(rp)
        plan.append((rp, True))
    for p in list(ro_paths) + list(_SYSTEM_RO_DIRS):
        rp = os.path.realpath(p)
        if rp in seen or not os.path.exists(rp):
            continue
        seen.add(rp)
        plan.append((rp, False))
    return plan


# ---------------------------------------------------------------------------
# 以下真调 syscall,仅 Linux 有意义(其余平台 abi_version()→-1,is_supported()→False)
# ---------------------------------------------------------------------------

def _syscall():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def abi_version() -> int:
    """探测内核 Landlock ABI 版本(≥1 支持;≤0 或非 Linux = 不支持 → 优雅降级)。

    landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION) 返回最高支持 ABI。
    ENOSYS(旧核无此 syscall)/ 任何错误 → -1(调用方跳过 Landlock,纯 bwrap 继续)。
    """
    if not sys.platform.startswith("linux"):
        return -1
    try:
        libc = _syscall()
        ret = libc.syscall(_NR_landlock_create_ruleset, None, ctypes.c_size_t(0),
                           ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION))
        return int(ret) if ret >= 1 else -1
    except Exception:
        return -1


def is_supported() -> bool:
    """内核是否支持 Landlock(ABI≥1)。"""
    return abi_version() >= 1


def apply_landlock(rw_paths: list[str], ro_paths: list[str]) -> bool:
    """在**当前线程**装 Landlock 文件系统门(及后代,execve 继承)。

    返回 True=已装 / False=内核不支持(优雅降级,调用方继续纯 bwrap)。
    任何**非降级**错误(ruleset 造好但 restrict 失败)→ 抛异常 fail-closed,绝不静默无门放行。
    """
    abi = abi_version()
    if abi < 1:
        return False   # 优雅降级:旧核无 Landlock → 跳过,纯 bwrap
    libc = _syscall()
    handled = access_fs_for_abi(abi)
    attr = pack_ruleset_attr(handled)
    buf = ctypes.create_string_buffer(attr, len(attr))
    ruleset_fd = libc.syscall(_NR_landlock_create_ruleset, buf,
                              ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if ruleset_fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"landlock_create_ruleset 失败(errno {err})")
    try:
        for path, is_write in plan_fs_rules(rw_paths, ro_paths):
            allowed = rule_access_for_abi(_WRITE_ACCESS if is_write else _READ_ACCESS, abi)
            try:
                dir_fd = os.open(path, _O_PATH)
            except OSError:
                continue    # 路径打不开(权限/竞态)→ 跳过这条(不放行 = 更严,安全侧)
            try:
                pb = pack_path_beneath_attr(allowed, dir_fd)
                pbuf = ctypes.create_string_buffer(pb, len(pb))
                rc = libc.syscall(_NR_landlock_add_rule, ctypes.c_int(ruleset_fd),
                                  ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
                                  pbuf, ctypes.c_uint32(0))
                if rc:
                    err = ctypes.get_errno()
                    raise OSError(err, f"landlock_add_rule({path}) 失败(errno {err})")
            finally:
                os.close(dir_fd)
        # no_new_privs 是 restrict_self 的前提(非 CAP_SYS_ADMIN 路径)
        if libc.syscall(158, ctypes.c_int(_PR_SET_NO_NEW_PRIVS),  # __NR_prctl=158
                        ctypes.c_ulong(1), ctypes.c_ulong(0),
                        ctypes.c_ulong(0), ctypes.c_ulong(0)):
            err = ctypes.get_errno()
            raise OSError(err, f"prctl(PR_SET_NO_NEW_PRIVS) 失败(errno {err})")
        rc = libc.syscall(_NR_landlock_restrict_self, ctypes.c_int(ruleset_fd),
                          ctypes.c_uint32(0))
        if rc:
            err = ctypes.get_errno()
            raise OSError(err, f"landlock_restrict_self 失败(errno {err})")
        return True
    finally:
        os.close(ruleset_fd)


def _main(argv: list[str]) -> int:
    """前置 wrapper 入口:`landlock <rw_json> <ro_json> -- <cmd...>`。

    装 Landlock(不支持则优雅跳过)后 execve 成 <cmd>;Landlock domain 被 <cmd> 继承。
    """
    if "--" not in argv:
        sys.stderr.write("landlock wrapper 用法:<rw_json> <ro_json> -- <cmd...>\n")
        return 2
    sep = argv.index("--")
    head, cmd = argv[:sep], argv[sep + 1:]
    if len(head) != 2 or not cmd:
        sys.stderr.write("landlock wrapper 参数错误\n")
        return 2
    try:
        rw = json.loads(head[0]); ro = json.loads(head[1])
    except Exception as e:
        sys.stderr.write(f"landlock wrapper JSON 解析失败:{e}\n")
        return 2
    try:
        applied = apply_landlock(rw, ro)
        if not applied:
            sys.stderr.write("[landlock] 内核不支持,优雅降级为纯 bwrap\n")
    except OSError as e:
        # Landlock 装到一半失败 = fail-closed:不 exec(绝不无门放行第三方代码)
        sys.stderr.write(f"[landlock] 装载失败,fail-closed 拒执行:{e}\n")
        return 3
    os.execvp(cmd[0], cmd)
    return 127   # execvp 不返回;到这里说明 exec 失败


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
