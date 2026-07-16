"""Seatbelt 沙箱（platform/darwin/seatbelt.py）—— macOS 适配器。

规格：docs/modules/sandbox.md §4（macOS 适配器）。镜像 Linux bubblewrap 的 fail-closed 契约,
只把执行隔离从 `bwrap` 换成 macOS 自带的 `sandbox-exec`（Seatbelt / SBPL profile）。

实现要点（与 bubblewrap 对齐）：
  1) `(deny default)` 起步,按 token.fs 显式放开**写**（fail-closed）。
  2) token 无 `net:` → `(deny network*)`；有 → `(allow network*)`（二元网络）。
  2b) **按域名 egress allowlist**(token.net_allowlist 非空):**macOS 短板 —— fail-closed**。
     SBPL 网络规则是 **host/port(IP)级**,`(allow network* (remote ip ...))` 在**连接建立时**
     按 IP 匹配,而**域名在解析前**就被翻成 IP —— SBPL **无法按域名字符串**判定(会被 DNS
     rebind / 多 IP 绕过)。域名级真强制需在解析层挂代理(Linux 那条路),Seatbelt 单靠 SBPL
     **表达不出域名粒度**。故遵守"宁拒不假放行"地基纪律:allowlist 非空 → **`(deny network*)`
     拒网**(fail-closed),不假装按域名放行。诚实标注:macOS 域名级 = **fail-closed 兜底**短板。
  3) 超时强杀 + 输出字节截断（UTF-8 边界,与 bubblewrap 同源）。
  4) write_file / read_file 是**纯 token 闸 IO**,跨平台一致,直接照搬 bubblewrap 语义。

围栏已在真 Mac（macOS 26.5.1 / Apple Silicon）上对抗式验证：写工作区外 / 写 $HOME / 未授权联网
全部 `Operation not permitted`；授权后联网 http 200。详见 tests/test_seatbelt_profile.py。

**v1 诚实边界（P1 收紧）**：`(allow file-read*)` —— 读**默认**放宽（macOS 上限制读极脆、易废掉工具）。
安全地基靠**写隔离 + 网络门**守（不能篡改、未授权不能外传）；一般读隔离列入 P1。env 不清洗（同 v1）。
**例外(已收紧)**：敏感路径(密钥/凭据/ssh/云凭据/浏览器 cookie)对**读也 deny** —— deny 子集统一到
`fs_grants.SENSITIVE_MARKERS` **单一真相源全集**(见 `_sensitive_deny_lines`),SBPL 后写规则赢,
盖过上面的 `(allow file-read*)`。诚实:此为 OS 层敏感地板,与 run_command 工具层预检、capability
决策链 step6 三层叠加;正则方言真强制以真 Mac 上 `test_seatbelt_profile.py` 的对抗验证为准。
"""

from __future__ import annotations

import asyncio
import os
import shutil

from karvyloop.capability import is_within_workspace, resolve_in_workspace
from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.sandbox.mounts import has_net, mounts_from_token, net_allowlist_of
from karvyloop.schemas import CapabilityToken


def _truncate_utf8(data: bytes, limit: int) -> tuple[bytes, bool]:
    """UTF-8 边界截断（与 bubblewrap 同源）。返回 (data, truncated)。"""
    if len(data) <= limit:
        return data, False
    cut = limit
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut], True


def _sbpl_str(path: str) -> str:
    """把路径转成 SBPL 字符串字面量（转义 \\ 和 "）。"""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---- 敏感地板:单一真相源(SENSITIVE_MARKERS)派生的 deny 规则 ----
# 元字符表:正则里需转义的字符(`/` 不是元字符,不转义 —— 免得触发某些 ERE 方言的
# "backslash 接普通字符" 未定义行为)。markers 已归一化(小写、/ 分隔、无反斜杠)。
_SBPL_RE_META = frozenset(r".^$*+?()[]{}|\\")


def _marker_to_sbpl_regex(marker: str) -> str:
    """把一个 SENSITIVE_MARKER 子串转成**大小写不敏感、匹配任意位置**的 SBPL 正则片段。

    忠实映射 is_sensitive_path 的"归一化后子串匹配"语义:它把路径先小写再对 marker 做子串比对,
    而 SBPL 看到的是真实大小写的路径。故这里对每个 ASCII 字母生成 `[Xx]` 字符类(POSIX 括号
    表达式,TRE 稳吃)做大小写无关匹配 —— 否则 macOS 上浏览器凭据 'Login Data' / 'Cookies'
    是大写、不折叠就漏。前后加 `.*` 兼容"整串匹配"与"子串搜索"两种 SBPL 语义,双保险。
    """
    body: list[str] = []
    for ch in marker:
        if ch.isascii() and ch.isalpha():
            body.append("[" + ch.upper() + ch.lower() + "]")
        elif ch in _SBPL_RE_META:
            body.append("\\" + ch)
        else:
            body.append(ch)
    return ".*" + "".join(body) + ".*"


def _concrete_sensitive_paths(marker: str, home: str) -> list[str]:
    """把**可锚定**的标记翻成真实位置(仍是单一真相源派生,非手抄一份小清单):
    `$HOME` 下的 dotpath、`/etc/*` 绝对路径。裸名标记(id_rsa / cookies / tokens.db /
    login data / appdata... 无从确定锚点)→ 返回空,交给 _marker_to_sbpl_regex 的正则覆盖。
    """
    if marker.startswith("/etc/"):
        return [marker]
    if marker.startswith("/."):   # /.ssh /.karvyloop/config.yaml /.env /.config/gcloud ...
        return [home + marker]
    return []


def _sensitive_deny_lines() -> list[str]:
    """对**全部 SENSITIVE_MARKERS**(从 fs_grants 导入,单一真相源)生成 SBPL deny 行。

    每个标记两种互补形式:
      (a) 大小写无关子串正则 —— 忠实 is_sensitive_path(匹配规范路径里任意位置的标记;也是
          唯一能表达 id_rsa / cookies / login data 这类裸名标记与 $HOME 外 dotfile 的形式)。
      (b) 真实位置的具体 subpath/literal deny —— 已在真 Mac 验证过的 SBPL 形式,作为"腰带":
          万一正则方言意外欠匹配,最高价值密钥仍被这层实锚 deny 兜住(deny+deny 幂等无害)。
    """
    from karvyloop.capability.fs_grants import SENSITIVE_MARKERS
    home = os.path.expanduser("~")
    lines: list[str] = []
    for m in SENSITIVE_MARKERS:
        lines.append(f'(deny file-read* file-write* (regex #"{_marker_to_sbpl_regex(m)}"))')
        for concrete in _concrete_sensitive_paths(m, home):
            kind = "subpath" if os.path.isdir(concrete) else "literal"
            lines.append(f"(deny file-read* file-write* ({kind} {_sbpl_str(os.path.realpath(concrete))}))")
    return lines


def build_profile(token: CapabilityToken) -> str:
    """从 token 生成 Seatbelt（SBPL）profile —— fail-closed,只放开写工作区 + 按 token 决定网络。

    纯函数,平台无关可单测（无需 macOS）。realpath 留给 exec 时按真实 cwd/挂载解析。
    """
    _ro, rw = mounts_from_token(token)
    # 授权台账(fs_grants):人批过的工作区外可写路径也放开写(读 v1 本就放宽)
    try:
        from karvyloop.capability.fs_grants import get_store
        _st = get_store()
        if _st is not None:
            rw = list(rw) + [g["path"] for g in _st.list()
                             if not g.get("expired") and "write" in (g.get("ops") or [])]
    except Exception:
        pass
    net = has_net(token)
    # 按域名 egress allowlist 非空 → macOS 短板:SBPL 无法按域名判定(见模块 docstring 2b)。
    # 遵守"宁拒不假放行":拒网(fail-closed),不假装按域名放行。
    egress_allowlist = net_allowlist_of(token)
    if egress_allowlist:
        net = False   # fail-closed:allowlist 语义在 macOS 收紧为拒网
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec*)",
        "(allow file-read*)",                       # v1:读放宽(见模块 docstring)
        '(allow file-write-data (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))',
        '(allow file-ioctl (literal "/dev/null") (literal "/dev/tty"))',
        "(allow sysctl-read)",
        "(allow mach-lookup)",
    ]
    # 只对 token 给的可写路径放开写（realpath:macOS /tmp→/private/tmp 等符号链接,Seatbelt 认真实路径）
    subpaths = [f"(subpath {_sbpl_str(os.path.realpath(p))})" for p in rw]
    if subpaths:
        lines.append("(allow file-write* " + " ".join(subpaths) + ")")
    if egress_allowlist:
        # 域名级 egress 在 macOS = fail-closed(SBPL 表达不出域名粒度);注释里留审计痕。
        lines.append("; egress allowlist requested but domain-level unenforceable in SBPL"
                     " -> fail-closed deny (see module docstring 2b)")
    lines.append("(allow network*)" if net else "(deny network*)")
    # 敏感地板(fs_grants 单一真相源):对**全部 SENSITIVE_MARKERS** deny —— 不再手抄一份小清单
    # (旧版只 deny 5 条,漏了 console.runtime.json / .env / 云凭据 / 浏览器 cookie)。
    # SBPL 后写的规则赢:这些 deny 盖过上面的 (allow file-read*) 读放宽,也盖过已授写路径
    # (敏感绝对优先)。见 _sensitive_deny_lines。
    lines.extend(_sensitive_deny_lines())
    return "\n".join(lines)


class SeatbeltSandbox:
    """macOS sandbox-exec 沙箱。需要 `sandbox-exec` 在 PATH（系统自带 /usr/bin/sandbox-exec）。"""

    name = "seatbelt"

    @staticmethod
    def available() -> bool:
        return shutil.which("sandbox-exec") is not None

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        if not argv:
            raise ValueError("argv 必须非空")
        if not self.available():
            raise RuntimeError("sandbox-exec 不可用 —— 此非 macOS 或系统被裁剪")

        profile = build_profile(token)
        real_cwd = os.path.realpath(cwd)
        cmd = ["sandbox-exec", "-p", profile, *list(argv)]

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=real_cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        out, truncated = _truncate_utf8(out, max_output_bytes)
        return ExecResult(
            stdout=out, stderr=err, exit_code=proc.returncode or 0,
            timed_out=timed_out, truncated=truncated,
        )

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        """只接受 token 覆盖的 fs 路径；写越界 = 拒绝（与 bubblewrap 同语义）。"""
        for g in token.grants:
            if g.resource.startswith("fs:") and (not g.ops or "write" in g.ops):
                root = g.resource[3:]
                if is_within_workspace(path, root):
                    # 检查把相对 path 按 root 拼接判定 → 落盘锚定同一 root;
                    # 否则 open(相对路径) 按进程 CWD 落盘 = 写穿 grant 界。
                    target = resolve_in_workspace(path, root)
                    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                    with open(target, "wb") as f:
                        f.write(content)
                    return
        raise PermissionError(f"token 未覆盖写 {path}")

    async def read_file(self, path: str, token: CapabilityToken) -> bytes:
        for g in token.grants:
            if g.resource.startswith("fs:"):
                root = g.resource[3:]
                if is_within_workspace(path, root):
                    with open(resolve_in_workspace(path, root), "rb") as f:
                        return f.read()
        raise PermissionError(f"token 未覆盖读 {path}")
