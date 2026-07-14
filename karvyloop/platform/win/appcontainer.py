"""platform/win/appcontainer.py —— Windows AppContainer(LowBox)网络门(免 admin,纯 ctypes)。

问题:WRITE_RESTRICTED 令牌 + Job 挡不住 socket。带 `net:` 的 token 之前 fail-closed 拒跑,
但**不带 net 声明的第三方脚本 socket 全通** = 网络洞。

真解(免 admin 内核强制):把子进程放进一个**无 internetClient capability 的 AppContainer
(LowBox)令牌**。Windows 内核的 WFP(Windows Filtering Platform)对 AppContainer 进程有一条
**内置默认规则**(MICROSOFT_DEFENDER_SUBLAYER_WSH,与用户防火墙配置无关、恒生效):出站连接
在 FWPM_LAYER_ALE_AUTH_CONNECT_V4/V6 层只放行持有 Internet Capability SID(S-1-15-3-1)的
AppContainer 进程;不持有该 cap 的 AppContainer 进程 → 落到 "Block Outbound Default Rule" →
**内核直接拒**。这条规则**免 admin**(是 Windows 自带的、无需配防火墙),而 `NtCreateLowBoxToken`
只要 medium IL(不需要 SeCreateTokenPrivilege / admin)。业界同款做法(clean-room 只借机制)。

机制链(在 restricted.py 里叠加):
  own token → CreateRestrictedToken(WRITE_RESTRICTED) → **NtCreateLowBoxToken(package SID,
  capabilities=0)** → 一个既写隔离、又落在无网 AppContainer 里的主令牌 → CreateProcessAsUser。
  结果:文件写还是走白名单 ACL(Everyone gate,AppContainer 进程仍是 Everyone 成员),
  网络被内核 WFP 默认规则拒(免 admin、非声明式)。

诚实边界:
  - AppContainer 会额外要求被访问对象(Python 解释器、系统 DLL、临时目录)对
    `ALL APPLICATION PACKAGES`(S-1-15-2-1)可读。Win10/11 系统目录默认已授 —— 一般能跑;
    个别机器/杀软环境可能起不来。故本层只管探测与造令牌:probe 探不通时,上层对第三方
    技能脚本 **fail-closed 拒跑**(不退回只限写、不限网的受限令牌假装隔离)。
  - 网络门只覆盖**出站 TCP/UDP connect**(WFP CONNECT 层);监听/本地回环等非本层目标(第三方
    脚本要联网外传才是威胁模型重点)。
  - 这是 IPv4+IPv6 出站默认块;不做域名级白名单(那是 P1,需要真配 WFP 过滤器 = 要 admin)。

平台无关部分(package SID 字符串推导)可跨平台单测;Win32 调用仅 Windows 定义。
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
from typing import Optional

_IS_WIN = os.name == "nt"

#: 固定的 AppContainer moniker —— 同一 moniker 推导出稳定的 package SID(无需落盘 profile)。
APPCONTAINER_MONIKER = "com.karvyloop.sandbox.nonet"


def _sha256_appcontainer_sid_string(moniker: str) -> str:
    """从 moniker 推导 AppContainer package SID 字符串(与 Win32
    DeriveAppContainerSidFromAppContainerName 同算法,平台无关可单测)。

    算法(公开、Windows 稳定):对 moniker 的 **UTF-16LE 大写**做 SHA-256,取前 28 字节,
    小端解成 7 个 uint32,拼成 `S-1-15-2-<a>-<b>-<c>-<d>-<e>-<f>-<g>`(package SID 前缀
    S-1-15-2 = APP_PACKAGE_AUTHORITY / SECURITY_APP_PACKAGE_BASE_RID)。
    """
    upper = moniker.upper().encode("utf-16-le")
    digest = hashlib.sha256(upper).digest()[:28]
    parts = struct.unpack("<7I", digest)
    return "S-1-15-2-" + "-".join(str(p) for p in parts)


#: 本模块使用的 AppContainer package SID 字符串(稳定,进程间一致)。
APPCONTAINER_SID_STRING = _sha256_appcontainer_sid_string(APPCONTAINER_MONIKER)


if _IS_WIN:
    from ctypes import wintypes

    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _HANDLE = ctypes.c_void_p
    _PSID = ctypes.c_void_p
    _TOKEN_ALL_ACCESS = 0xF01FF

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", _PSID), ("Attributes", wintypes.DWORD)]

    # NTSTATUS NtCreateLowBoxToken(PHANDLE, HANDLE, ACCESS_MASK, POBJECT_ATTRIBUTES,
    #   PSID PackageSid, ULONG CapabilityCount, PSID_AND_ATTRIBUTES Capabilities,
    #   ULONG HandleCount, HANDLE* Handles)
    _ntdll.NtCreateLowBoxToken.argtypes = [
        ctypes.POINTER(_HANDLE), _HANDLE, wintypes.DWORD, ctypes.c_void_p,
        _PSID, wintypes.ULONG, ctypes.c_void_p,
        wintypes.ULONG, ctypes.c_void_p,
    ]
    _ntdll.NtCreateLowBoxToken.restype = ctypes.c_long  # NTSTATUS

    _adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_PSID)]
    _adv.ConvertStringSidToSidW.restype = wintypes.BOOL
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.LocalFree.restype = ctypes.c_void_p

    _PACKAGE_SID: Optional[int] = None

    def _package_sid() -> int:
        """返回稳定 package SID 的裸指针(模块级缓存,进程内不释放)。"""
        global _PACKAGE_SID
        if _PACKAGE_SID is None:
            p = _PSID()
            if not _adv.ConvertStringSidToSidW(APPCONTAINER_SID_STRING, ctypes.byref(p)):
                code = ctypes.get_last_error()
                raise OSError(f"ConvertStringSidToSid(package) 失败(WinError {code})")
            _PACKAGE_SID = p.value
        return _PACKAGE_SID

    def make_lowbox_token(h_source: _HANDLE) -> _HANDLE:
        """在 h_source(可为受限令牌)之上造一个**无 capability** 的 LowBox(AppContainer)主令牌。

        capabilities=0 → 无 internetClient(S-1-15-3-1)→ WFP 默认规则内核拒出站网络。
        NtCreateLowBoxToken 恒返回**主令牌**,可直接喂 CreateProcessAsUser。medium IL 即可,免 admin。
        失败(NTSTATUS<0)抛 OSError,fail-loud 上抛(调用方不静默退回无网络门的令牌假装隔离)。
        """
        h_lowbox = _HANDLE()
        status = _ntdll.NtCreateLowBoxToken(
            ctypes.byref(h_lowbox), h_source, _TOKEN_ALL_ACCESS, None,
            _package_sid(), 0, None, 0, None)
        if status < 0:
            raise OSError(f"NtCreateLowBoxToken 失败(NTSTATUS 0x{status & 0xFFFFFFFF:08X})"
                          " —— 无网 LowBox 造不出来,此次执行不假装隔离")
        return h_lowbox
