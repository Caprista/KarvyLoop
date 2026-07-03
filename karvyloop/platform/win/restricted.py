"""RestrictedTokenSandbox(platform/win/restricted.py)—— Windows Tier 3。

机制(纯 ctypes 手绑 Win32,零新依赖;业界同款做法,按仓纪律 clean-room 只借机制):

  写隔离  `CreateRestrictedToken(WRITE_RESTRICTED, restricting=[Everyone(S-1-1-0)])`
          造受限主令牌 → `CreateProcessAsUserW` 起子进程(受限令牌派生自本进程令牌,
          免 admin、免 SE_ASSIGNPRIMARYTOKEN)。`WRITE_RESTRICTED` 语义:子进程的每次
          **写**访问都要过两遍 DACL 检查(正常组 + restricting 组),第二遍只认
          restricting SID。**读/遍历不受 restricting 限制**(WRITE_RESTRICTED 只约束写),
          所以本机文件照常可读(读隔离 v1 放宽)。
          用 Everyone 作 write-gate SID:用户目录(%USERPROFILE%/%TEMP%/其他工程)
          的 DACL 只授用户 SID、不授 Everyone 写 → restricting 组第二遍拒 → **写全拒**。
          执行前只对 token rw 白名单(workspace + fs_grants 台账)临时加
          "allow Everyone GENERIC_ALL(含子目录/文件继承)" ACE → 白名单内可写;
          run 结束 REVOKE 撤 ACE。对齐 bwrap/seatbelt「默认拒写 + 白名单」契约。
          (实现说明:曾试 RESTRICTED(S-1-5-12) 作 write-gate,本机实测用户所属对象的
           DACL 授它写不被兑现 → 连自家 workspace 都写不进;Everyone 作 gate 实测可用且
           隔离完整——用户目录不授 Everyone 写这一事实正是隔离来源。)
  资源上限 Job Object:JOB_MEMORY(默认 2 GiB)+ ACTIVE_PROCESS(默认 64,防进程炸弹)
          + KILL_ON_JOB_CLOSE;超时/收尾 `TerminateJobObject` 杀整棵进程树
          (Windows 上 proc.kill() 杀不到子孙,Job 是唯一可靠杀树面)。
  网络门  **本层做不满**(restricted token / Job / IL 都挡不住 socket;防火墙/WFP
          规则需一次性 admin,违背免 admin + Home 版硬约束)→ 带 `net:` grant 的
          token **fail-closed 拒跑**,错误如实说,绝不"假装隔离地放行"。
  超时/截断 UTF-8 边界截断照抄 bubblewrap/seatbelt(与沙箱机制无关)。
  write_file/read_file 纯 token 闸 IO,跨平台同语义。

诚实边界(如实标注,不吹):
  - 无 `net:` 的 token 在本层**技术上无法断网**(bwrap `--unshare-net` 在免 admin 的
    Windows 上没有等价物)。写隔离守住"不能篡改";"未授权不能外传"只能靠上层收口
    (第三方默认拒网、授网是人的决定),不能靠内核兜。
  - 读隔离 v1 放宽(对齐 macOS seatbelt 先例)。
  - 世界可写(DACL 已授 Everyone 写)的既有路径不在拒写范围 —— 用 Everyone 作
    write-gate 的取舍;用户 profile / 系统目录默认不授 Everyone 写,都在拒写范围。
  - 反蓄意逃逸不承诺(计划任务/COM broker 类逃逸面业界参照同样承认挡不住);
    定位 = 防误操作 + 一般不可信脚本。安全是地基不是招牌。

探测:available() 真跑一次探测(造受限令牌 + Job + spawn `cmd /c exit 7` 收回退出码),
结果类级缓存;失败(锁定策略机 CreateRestrictedToken 报 87、杀软拦 CreateProcessAsUser
等)→ selector 降 Tier 4(DegradedWindowsSandbox),不崩。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import subprocess
import tempfile
import threading
from typing import Optional

from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.sandbox.mounts import has_net
from karvyloop.schemas import CapabilityToken

from ._util import (
    _truncate_utf8,
    resolve_argv,
    rw_ro_paths_with_grants,
    token_gated_read,
    token_gated_write,
)

_NET_FAIL_CLOSED = (
    "此次执行的 token 带 `net:` 授权,但 Windows Tier-3 沙箱(restricted token)"
    "无法实现网络门:断网/放行控制需要 admin 级防火墙规则,违背免 admin 约束 —— "
    "fail-closed 拒跑,不假装隔离地放行。此技能需网络请在 Linux/macOS(完整沙箱)"
    "上运行,或撤销该技能的网络授权后重试。"
)

# ---------------------------------------------------------------------------
# Win32 绑定(仅 Windows 定义;模块在任何平台可安全 import)
# ---------------------------------------------------------------------------

_IS_WIN = os.name == "nt"

if _IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)

    _HANDLE = ctypes.c_void_p
    _PSID = ctypes.c_void_p

    # ---- 常量 ----
    _TOKEN_ASSIGN_PRIMARY = 0x0001
    _TOKEN_DUPLICATE = 0x0002
    _TOKEN_QUERY = 0x0008
    _DISABLE_MAX_PRIVILEGE = 0x1
    _WRITE_RESTRICTED = 0x8
    _TOKEN_GROUPS_CLASS = 2                # TokenGroups
    _SE_GROUP_LOGON_ID = 0xC0000000

    _GENERIC_ALL = 0x10000000
    _GENERIC_READ = 0x80000000
    _GENERIC_EXECUTE = 0x20000000
    _GRANT_ACCESS = 1
    _REVOKE_ACCESS = 4
    _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3   # CONTAINER|OBJECT_INHERIT_ACE
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_WELL_KNOWN_GROUP = 5
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 4

    _CREATE_SUSPENDED = 0x4
    _CREATE_UNICODE_ENVIRONMENT = 0x400
    # 注意:不能用 CREATE_NO_WINDOW —— 它会给子进程配新 conhost,而 conhost 连接在
    # WRITE_RESTRICTED 令牌下过不了访问检查 → 子进程 0xC0000142(STATUS_DLL_INIT_FAILED)。
    # 本机变体矩阵实测:DETACHED_PROCESS(完全无控制台,std 全走管道)才能跑通。
    _DETACHED_PROCESS = 0x8
    _STARTF_USESTDHANDLES = 0x100
    _HANDLE_FLAG_INHERIT = 0x1
    _WAIT_TIMEOUT = 0x102

    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    # ---- 结构体 ----
    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", _PSID), ("Attributes", wintypes.DWORD)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", _HANDLE), ("hStdOutput", _HANDLE), ("hStdError", _HANDLE)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", _HANDLE), ("hThread", _HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [("pMultipleTrustee", ctypes.c_void_p),
                    ("MultipleTrusteeOperation", ctypes.c_int),
                    ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int),
                    ("ptstrName", ctypes.c_void_p)]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [("grfAccessPermissions", wintypes.DWORD),
                    ("grfAccessMode", ctypes.c_int),
                    ("grfInheritance", wintypes.DWORD), ("Trustee", _TRUSTEE_W)]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    # ---- 原型 ----
    _k32.GetCurrentProcess.restype = _HANDLE
    _adv.OpenProcessToken.argtypes = [_HANDLE, wintypes.DWORD, ctypes.POINTER(_HANDLE)]
    _adv.OpenProcessToken.restype = wintypes.BOOL
    _adv.CreateRestrictedToken.argtypes = [
        _HANDLE, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_void_p,     # SidsToDisable
        wintypes.DWORD, ctypes.c_void_p,     # PrivilegesToDelete
        wintypes.DWORD, ctypes.c_void_p,     # SidsToRestrict
        ctypes.POINTER(_HANDLE)]
    _adv.CreateRestrictedToken.restype = wintypes.BOOL
    _adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_PSID)]
    _adv.ConvertStringSidToSidW.restype = wintypes.BOOL
    _adv.GetTokenInformation.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _adv.GetTokenInformation.restype = wintypes.BOOL
    _adv.GetLengthSid.argtypes = [_PSID]
    _adv.GetLengthSid.restype = wintypes.DWORD
    _adv.EqualSid.argtypes = [_PSID, _PSID]
    _adv.EqualSid.restype = wintypes.BOOL
    _adv.CreateProcessAsUserW.argtypes = [
        _HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
    _adv.CreateProcessAsUserW.restype = wintypes.BOOL
    _adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    _adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _adv.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(_EXPLICIT_ACCESS_W),
                                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _adv.SetEntriesInAclW.restype = wintypes.DWORD
    _adv.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _adv.GetExplicitEntriesFromAclW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG),
        ctypes.POINTER(ctypes.POINTER(_EXPLICIT_ACCESS_W))]
    _adv.GetExplicitEntriesFromAclW.restype = wintypes.DWORD
    _k32.CreatePipe.argtypes = [ctypes.POINTER(_HANDLE), ctypes.POINTER(_HANDLE),
                                ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.DWORD]
    _k32.CreatePipe.restype = wintypes.BOOL
    _k32.SetHandleInformation.argtypes = [_HANDLE, wintypes.DWORD, wintypes.DWORD]
    _k32.SetHandleInformation.restype = wintypes.BOOL
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = _HANDLE
    _k32.SetInformationJobObject.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [_HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.ResumeThread.argtypes = [_HANDLE]
    _k32.ResumeThread.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [_HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [_HANDLE, wintypes.UINT]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [_HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.LocalFree.restype = ctypes.c_void_p

    def _winerr(what: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(f"{what} 失败(WinError {code}):{ctypes.FormatError(code)}")

    def _string_sid(s: str) -> int:
        p = _PSID()
        if not _adv.ConvertStringSidToSidW(s, ctypes.byref(p)):
            raise _winerr(f"ConvertStringSidToSid({s})")
        return p.value    # LocalAlloc 所有权:模块级缓存,进程内不释放

    # 模块级 SID 缓存(进程生命周期;裸指针 int)
    _SID_EVERYONE: Optional[int] = None
    _SID_RESTRICTED: Optional[int] = None

    def _well_known_sids() -> tuple[int, int]:
        global _SID_EVERYONE, _SID_RESTRICTED
        if _SID_EVERYONE is None:
            _SID_EVERYONE = _string_sid("S-1-1-0")       # Everyone
        if _SID_RESTRICTED is None:
            _SID_RESTRICTED = _string_sid("S-1-5-12")    # NT AUTHORITY\RESTRICTED
        return _SID_EVERYONE, _SID_RESTRICTED

    def _open_own_token() -> _HANDLE:
        h = _HANDLE()
        want = _TOKEN_DUPLICATE | _TOKEN_QUERY | _TOKEN_ASSIGN_PRIMARY
        if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), want, ctypes.byref(h)):
            raise _winerr("OpenProcessToken")
        return h

    def _logon_sid(h_tok: _HANDLE):
        """从当前令牌组里找 LogonSID(SE_GROUP_LOGON_ID)。返回 (psid|None, keepalive_buf)。"""
        need = wintypes.DWORD(0)
        _adv.GetTokenInformation(h_tok, _TOKEN_GROUPS_CLASS, None, 0, ctypes.byref(need))
        buf = ctypes.create_string_buffer(need.value)
        if not _adv.GetTokenInformation(h_tok, _TOKEN_GROUPS_CLASS, buf, need,
                                        ctypes.byref(need)):
            return None, buf
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        # TOKEN_GROUPS = DWORD GroupCount + padding + SID_AND_ATTRIBUTES[GroupCount]
        arr_off = ctypes.sizeof(ctypes.c_void_p)   # DWORD 按指针对齐后数组起点
        arr = ctypes.cast(ctypes.byref(buf, arr_off),
                          ctypes.POINTER(_SID_AND_ATTRIBUTES))
        for i in range(count):
            if (arr[i].Attributes & _SE_GROUP_LOGON_ID) == _SE_GROUP_LOGON_ID:
                return arr[i].Sid, buf
        return None, buf

    def _make_write_restricted_token() -> _HANDLE:
        """WRITE_RESTRICTED;restricting=[Everyone(S-1-1-0)]。

        WRITE_RESTRICTED 只对**写**访问施加 restricting 组第二遍检查;读/遍历走正常令牌
        (读隔离 v1 放宽)。Everyone 作 write-gate:用户目录不授 Everyone 写 → 写全拒;
        白名单目录执行前临时授 Everyone 写 → 可写。不加 DISABLE_MAX_PRIVILEGE(它会抹掉
        SeChangeNotifyPrivilege 导致遍历检查处处触发,本机实测反而废掉 Python 起动)。
        """
        h_tok = _open_own_token()
        try:
            everyone, _restricted = _well_known_sids()
            sids = [everyone]
            arr = (_SID_AND_ATTRIBUTES * len(sids))()
            for i, s in enumerate(sids):
                arr[i].Sid = s
                arr[i].Attributes = 0
            h_new = _HANDLE()
            ok = _adv.CreateRestrictedToken(
                h_tok, _WRITE_RESTRICTED,
                0, None, 0, None, len(sids), arr, ctypes.byref(h_new))
            if not ok:
                raise _winerr("CreateRestrictedToken")
            return h_new
        finally:
            _k32.CloseHandle(h_tok)

    def _make_job(job_memory: int, proc_limit: int) -> _HANDLE:
        h_job = _k32.CreateJobObjectW(None, None)
        if not h_job:
            raise _winerr("CreateJobObject")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_JOB_MEMORY)
        info.BasicLimitInformation.ActiveProcessLimit = proc_limit
        info.JobMemoryLimit = job_memory
        if not _k32.SetInformationJobObject(
                h_job, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(h_job)
            raise _winerr("SetInformationJobObject")
        return h_job

    def _set_dacl_entry(path: str, mode: int, perms: int = _GENERIC_ALL) -> None:
        """对 path 加(GRANT)/撤(REVOKE)Everyone(write-gate SID)的白名单 ACE(含继承传播)。

        perms:GRANT 时授的权限(GENERIC_ALL=写白名单 / GENERIC_READ|EXECUTE=读+遍历白名单)。
        """
        everyone, _restricted = _well_known_sids()
        p_sd = ctypes.c_void_p()
        p_old = ctypes.c_void_p()
        rc = _adv.GetNamedSecurityInfoW(
            path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
            None, None, ctypes.byref(p_old), None, ctypes.byref(p_sd))
        if rc:
            raise OSError(f"GetNamedSecurityInfo({path}) 失败(WinError {rc})")
        try:
            ea = _EXPLICIT_ACCESS_W()
            ea.grfAccessPermissions = perms
            ea.grfAccessMode = mode
            ea.grfInheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT
            ea.Trustee.TrusteeForm = _TRUSTEE_IS_SID
            ea.Trustee.TrusteeType = _TRUSTEE_IS_WELL_KNOWN_GROUP
            ea.Trustee.ptstrName = everyone
            p_new = ctypes.c_void_p()
            rc = _adv.SetEntriesInAclW(1, ctypes.byref(ea), p_old, ctypes.byref(p_new))
            if rc:
                raise OSError(f"SetEntriesInAcl({path}) 失败(WinError {rc})")
            try:
                rc = _adv.SetNamedSecurityInfoW(
                    path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
                    None, None, p_new, None)
                if rc:
                    raise OSError(f"SetNamedSecurityInfo({path}) 失败(WinError {rc})")
            finally:
                _k32.LocalFree(p_new)
        finally:
            _k32.LocalFree(p_sd)

    def _grant_gate_write(path: str) -> None:
        _set_dacl_entry(path, _GRANT_ACCESS, _GENERIC_ALL)

    def _grant_gate_read(path: str) -> None:
        _set_dacl_entry(path, _GRANT_ACCESS, _GENERIC_READ | _GENERIC_EXECUTE)

    def _revoke_gate_write(path: str) -> None:
        _set_dacl_entry(path, _REVOKE_ACCESS)

    def _gate_ace_count(path: str) -> int:
        """path DACL 里显式授给 write-gate SID(Everyone/S-1-1-0)的 ACE 数(测试/审计用)。"""
        everyone, _restricted = _well_known_sids()
        p_sd = ctypes.c_void_p()
        p_dacl = ctypes.c_void_p()
        rc = _adv.GetNamedSecurityInfoW(
            path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
            None, None, ctypes.byref(p_dacl), None, ctypes.byref(p_sd))
        if rc:
            raise OSError(f"GetNamedSecurityInfo({path}) 失败(WinError {rc})")
        try:
            n = wintypes.ULONG(0)
            entries = ctypes.POINTER(_EXPLICIT_ACCESS_W)()
            rc = _adv.GetExplicitEntriesFromAclW(p_dacl, ctypes.byref(n),
                                                 ctypes.byref(entries))
            if rc:
                raise OSError(f"GetExplicitEntriesFromAcl({path}) 失败(WinError {rc})")
            try:
                count = 0
                for i in range(n.value):
                    t = entries[i].Trustee
                    if t.TrusteeForm == _TRUSTEE_IS_SID and t.ptstrName and \
                            _adv.EqualSid(t.ptstrName, everyone):
                        count += 1
                return count
            finally:
                _k32.LocalFree(entries)
        finally:
            _k32.LocalFree(p_sd)

    def _make_pipe(parent_reads: bool) -> tuple[int, int]:
        """返回 (parent_end, child_end) 裸句柄;child_end 可继承、parent_end 不可。"""
        sa = _SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(sa)
        sa.bInheritHandle = True
        r, w = _HANDLE(), _HANDLE()
        if not _k32.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
            raise _winerr("CreatePipe")
        parent, child = (r.value, w.value) if parent_reads else (w.value, r.value)
        if not _k32.SetHandleInformation(parent, _HANDLE_FLAG_INHERIT, 0):
            raise _winerr("SetHandleInformation")
        return parent, child

    def _env_block(overrides: dict) -> ctypes.Array:
        env = dict(os.environ)
        env.update(overrides)
        items = sorted(env.items(), key=lambda kv: kv[0].upper())
        block = "".join(f"{k}={v}\0" for k, v in items) + "\0"
        buf = (ctypes.c_wchar * (len(block) + 1))()
        buf[: len(block)] = block
        return buf

    def _drain(fd: int, sink: bytearray, keep: int, total: list) -> None:
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                total[0] += len(chunk)
                if len(sink) < keep:
                    sink.extend(chunk)     # 超出 keep 丢弃但继续 drain,防子进程写管道阻塞
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _run_restricted(argv: list[str], cwd: str, stdin: bytes, timeout_s: float,
                        max_output_bytes: int, rw_paths: list[str],
                        job_memory: int, proc_limit: int,
                        ro_paths: Optional[list[str]] = None) -> ExecResult:
        """同步核心:ACL 白名单 → 受限令牌 → Job → CreateProcessAsUser → 收割。"""
        import msvcrt

        applied: list[str] = []          # 已加白名单 ACE 的路径(收尾 REVOKE)
        tmpdir: Optional[str] = None
        h_tok = h_job = None
        pi = _PROCESS_INFORMATION()
        try:
            # 1) 白名单 ACE(去重、realpath、必须存在):rw 授写、ro 授读+遍历(RX)。
            seen: set[str] = set()
            for p in rw_paths:
                rp = os.path.realpath(p)
                key = rp.lower()
                if key in seen or not os.path.exists(rp):
                    continue
                seen.add(key)
                _grant_gate_write(rp)
                applied.append(rp)
            for p in (ro_paths or []):
                rp = os.path.realpath(p)
                key = rp.lower()
                if key in seen or not os.path.exists(rp):
                    continue
                seen.add(key)
                _grant_gate_read(rp)
                applied.append(rp)

            # 2) 子进程专用 TEMP(落在白名单内,继承 RESTRICTED ACE → 可写;
            #    否则子进程连临时文件都写不了,很多脚本直接翻车)
            env_overrides: dict = {}
            tmp_host = next((p for p in applied if os.path.isdir(p)), None)
            if tmp_host is not None:
                tmpdir = tempfile.mkdtemp(prefix=".klsbx-tmp-", dir=tmp_host)
                env_overrides = {"TMP": tmpdir, "TEMP": tmpdir, "TMPDIR": tmpdir}

            # 3) 受限令牌 + Job
            h_tok = _make_write_restricted_token()
            h_job = _make_job(job_memory, proc_limit)

            # 4) 管道
            out_parent, out_child = _make_pipe(parent_reads=True)
            err_parent, err_child = _make_pipe(parent_reads=True)
            in_parent, in_child = _make_pipe(parent_reads=False)

            # 5) 起进程(挂起)→ 进 Job → 恢复
            si = _STARTUPINFOW()
            si.cb = ctypes.sizeof(si)
            si.dwFlags = _STARTF_USESTDHANDLES
            si.hStdInput = in_child
            si.hStdOutput = out_child
            si.hStdError = err_child
            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            env_buf = _env_block(env_overrides)
            ok = _adv.CreateProcessAsUserW(
                h_tok, None, cmdline, None, None, True,
                _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _DETACHED_PROCESS,
                env_buf, cwd, ctypes.byref(si), ctypes.byref(pi))
            if not ok:
                err_code = ctypes.get_last_error()
                for h in (out_parent, out_child, err_parent, err_child, in_parent, in_child):
                    _k32.CloseHandle(h)
                exc = _winerr("CreateProcessAsUser(受限令牌起子进程;若 WinError 5 "
                              "多为杀软拦截,需加例外)")
                # WinError 5(ACCESS_DENIED)/ 1920(CANT_ACCESS_FILE)在高频起子进程时多为
                # 杀软瞬时拦截(docs/48 A.4 风险②)—— 标记为 transient 让 exec 重试一次。
                exc.transient_spawn = err_code in (5, 1920)   # type: ignore[attr-defined]
                raise exc
            # 父进程不再持有子端句柄(否则读端永远等不到 EOF)
            for h in (out_child, err_child, in_child):
                _k32.CloseHandle(h)
            if not _k32.AssignProcessToJobObject(h_job, pi.hProcess):
                _k32.TerminateProcess(pi.hProcess, 1)
                raise _winerr("AssignProcessToJobObject(进不了 Job = 没有资源上限,"
                              "fail-closed 不裸跑)")
            _k32.ResumeThread(pi.hThread)
            _k32.CloseHandle(pi.hThread)

            # 6) IO 线程
            keep = max_output_bytes + 65536
            out_buf, err_buf = bytearray(), bytearray()
            out_total, err_total = [0], [0]
            out_fd = msvcrt.open_osfhandle(out_parent, os.O_RDONLY | os.O_BINARY)
            err_fd = msvcrt.open_osfhandle(err_parent, os.O_RDONLY | os.O_BINARY)
            in_fd = msvcrt.open_osfhandle(in_parent, os.O_WRONLY | os.O_BINARY)
            threads = [
                threading.Thread(target=_drain, args=(out_fd, out_buf, keep, out_total), daemon=True),
                threading.Thread(target=_drain, args=(err_fd, err_buf, keep, err_total), daemon=True),
            ]
            for t in threads:
                t.start()

            def _feed_stdin() -> None:
                try:
                    if stdin:
                        os.write(in_fd, stdin)
                except OSError:
                    pass
                finally:
                    try:
                        os.close(in_fd)
                    except OSError:
                        pass

            tin = threading.Thread(target=_feed_stdin, daemon=True)
            tin.start()

            # 7) 等待 / 超时杀整棵树
            timed_out = False
            rc_wait = _k32.WaitForSingleObject(pi.hProcess, int(timeout_s * 1000))
            if rc_wait == _WAIT_TIMEOUT:
                timed_out = True
            # 无论主进程是否正常退出,统一 TerminateJobObject 收尾:
            #   - 超时 → 杀整棵树(契约)
            #   - 正常退出但有后台子孙残留 → 一并收掉(bwrap --die-with-parent 同精神),
            #     同时保证管道 EOF、reader 线程必然收敛
            _k32.TerminateJobObject(h_job, 1)
            _k32.WaitForSingleObject(pi.hProcess, 10_000)

            exit_code = wintypes.DWORD(0)
            _k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
            for t in threads:
                t.join(timeout=15)
            tin.join(timeout=5)

            out, t1 = _truncate_utf8(bytes(out_buf), max_output_bytes)
            err, _t2 = _truncate_utf8(bytes(err_buf), max_output_bytes)
            truncated = t1 or out_total[0] > max_output_bytes
            code = 1 if timed_out else int(exit_code.value)
            return ExecResult(stdout=out, stderr=err, exit_code=code,
                              timed_out=timed_out, truncated=truncated)
        finally:
            if pi.hProcess:
                _k32.CloseHandle(pi.hProcess)
            if h_job:
                _k32.CloseHandle(h_job)      # KILL_ON_JOB_CLOSE 兜底杀残留
            if h_tok:
                _k32.CloseHandle(h_tok)
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)
            for p in applied:                # 撤白名单 ACE(REVOKE 含继承传播)
                try:
                    _revoke_gate_write(p)
                except OSError:
                    pass                     # 残留 ACE 只对白名单目录放行,run 后即撤,无长期提权面


# ---------------------------------------------------------------------------
# Sandbox 实现
# ---------------------------------------------------------------------------

class RestrictedTokenSandbox:
    """Windows Tier-3 沙箱:WRITE_RESTRICTED 令牌写隔离 + Job Object 资源上限。

    网络门做不满 → 带 `net:` 的 token fail-closed 拒跑(见模块 docstring 诚实边界)。
    """

    name = "win-restricted"

    #: 探测结果类级缓存(None=未探测)
    _available_cache: Optional[bool] = None

    def __init__(self, *, job_memory_bytes: int = 2 << 30,
                 active_process_limit: int = 64):
        self.job_memory_bytes = job_memory_bytes
        self.active_process_limit = active_process_limit

    @classmethod
    def available(cls) -> bool:
        """真探测:造受限令牌 + Job + 真 spawn `cmd /c exit 7` 收回退出码。

        锁定策略机(CreateRestrictedToken 报 87)/ 杀软拦 CreateProcessAsUser →
        False,selector 降 Tier 4,不崩。结果进程内缓存。
        """
        if not _IS_WIN:
            return False
        if cls._available_cache is not None:
            return cls._available_cache
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        ok = False
        # 探测重试一次:CreateProcessAsUser 偶发被杀软瞬时拦(WinError 5/1920,docs/48 A.4
        # 风险②)—— 单次噪声不该误判整机不可用。两次都失败才判 False → 降 Tier 4。
        for _ in range(2):
            try:
                r = _run_restricted([comspec, "/d", "/c", "exit 7"], cwd=os.getcwd(),
                                    stdin=b"", timeout_s=30.0, max_output_bytes=4096,
                                    rw_paths=[], job_memory=256 << 20, proc_limit=8)
                if r.exit_code == 7 and not r.timed_out:
                    ok = True
                    break
            except Exception:
                pass
        cls._available_cache = ok
        return cls._available_cache

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        if not argv:
            raise ValueError("argv 必须非空")
        # 网络门 fail-closed:先于一切平台调用(错误如实说,不假装隔离)
        if has_net(token):
            raise PermissionError(_NET_FAIL_CLOSED)
        if not self.available():
            raise RuntimeError(
                "RestrictedToken 沙箱在本机探测失败(锁定策略/杀软拦截?)—— "
                "selector 会降级到 DegradedWindowsSandbox,不应直接调到这里")
        ro, rw = rw_ro_paths_with_grants(token)
        argv = resolve_argv(list(argv))
        # 起子进程偶发被杀软瞬时拦(WinError 5/1920,docs/48 A.4 风险②)→ 重试一次,
        # 两次都因瞬时拦截失败才上抛。非 transient 错误(权限门等)立即抛,不掩盖。
        last: Optional[BaseException] = None
        for _ in range(2):
            try:
                return await asyncio.to_thread(
                    _run_restricted, argv, cwd, stdin, timeout_s, max_output_bytes,
                    rw, self.job_memory_bytes, self.active_process_limit, ro)
            except OSError as e:
                if not getattr(e, "transient_spawn", False):
                    raise
                last = e
        assert last is not None
        raise last

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        """纯 token 闸 IO(与 bubblewrap/seatbelt 同语义)。"""
        token_gated_write(path, content, token)

    async def read_file(self, path: str, token: CapabilityToken) -> bytes:
        return token_gated_read(path, token)
