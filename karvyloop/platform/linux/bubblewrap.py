"""Bubblewrap 沙箱（platform/linux/bubblewrap.py）。

规格：docs/modules/sandbox.md §3。
实现要点：
  1) --unshare-all 起步，按 token.fs 显式放开（fail-closed）
  2) token.net 缺 → --unshare-net 兜底（二元网络）
  2b) **按域名 egress allowlist**(token.net_allowlist 非空):目标 = 隔离 netns 的唯一 egress
     经免 root 用户态网络栈(pasta/slirp4netns)转给本机 allowlist 代理 → 域名级真强制。
     部件(1)用户态 allowlist 代理**已焊已测**(egress_proxy.py:HTTP CONNECT+SOCKS5,命中放行
     否则确定性拒、IP 字面量拒);部件(2)"不可绕过的 netns→代理专用路由"装配**尚未真机验证**。
     安全是地基,宁 fail-closed 不假安全 → 在(2)验证前 **allowlist 非空一律 fail-closed 拒网**
     (--unshare-net),**绝不**退回可被无视的 *_PROXY env 假放行。待(2)落地即翻真强制(P1)。
  3) 超时强杀 + 输出字节截断（UTF-8 边界，HR-9 同源）
  4) 不接 L7 内容过滤（范围外，留 P1）
  5) **Landlock 深度防御**（landlock.py）：内核支持则在 bwrap **之内**再叠一层 Landlock LSM
     文件系统门（workspace 可写、系统 bin 只读、其余默认拒）——mount 隔离 + 内核路径规则
     双层。旧核不支持 → 优雅降级为纯 bwrap（fail-closed 语义不变）。免特权（no_new_privs）。
     顺序必须是「bwrap … -- python3 landlock.py … -- payload」：套在 bwrap 外面会因 /proc
     被设为只读而让 bwrap 写不了 /proc/self/uid_map（`setting up uid map: Permission denied`）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Optional

from karvyloop.capability import is_within_workspace, resolve_in_workspace
from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.sandbox.mounts import has_net, mounts_from_token, net_allowlist_of
from karvyloop.schemas import CapabilityToken


def _truncate_utf8(data: bytes, limit: int) -> tuple[bytes, bool]:
    """UTF-8 边界截断（HR-9 同源）。返回 (data, truncated)。"""
    if len(data) <= limit:
        return data, False
    # 找到 limit 之前最后一个完整 UTF-8 字符边界
    cut = limit
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut], True


def _existing_mount_paths(paths: list[str]) -> list[str]:
    """过滤掉不存在与重复的挂载路径。

    bwrap 对**源路径不存在**是硬失败(`bwrap: Can't find source path …`),整条命令直接跑不起来。
    而挂载面是"只减不加"的:授权台账里残留的死路径(角色被改名/删掉、技能搬家)、token 里
    已消失的工作区,都应当静默跳过 —— 少一个挂载 = 更严,绝不能让整条命令连带失败。
    纯函数,平台无关可单测。
    """
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        ap = os.path.abspath(p)
        if ap in seen or not os.path.exists(ap):
            continue
        seen.add(ap)
        out.append(ap)
    return out


def humanize_bwrap_error(stderr: str) -> str:
    """把 bwrap 的英文硬失败翻成一句中文提示(空串 = 没有可翻的)。

    bwrap 的报错很短且不解释原因,用户只看得到 `bwrap: Can't find source path …`,
    往往以为是命令写错。这里按已知根因给一句可执行的提示,附在 stderr 之后;
    **不改 exit code、不吞原始报错**,诊断信息仍然完整。
    """
    s = stderr or ""
    hints = (
        ("Can't find source path",
         "挂载源路径不存在(角色被改名/删除、技能换目录后台账里残留了旧路径)。"
         "挂载层会自动跳过死路径,出现这句说明是别处显式要求挂它;请到能力总览撤销该死授权。"),
        ("setting up uid map",
         "创建用户命名空间失败:宿主禁用了 unprivileged userns"
         "(sysctl user.max_user_namespaces / 容器 seccomp 策略)。"),
        ("No such file or directory",
         "沙箱内找不到要执行的命令(或其解释器目录没有被 ro-bind 进来)。"),
        ("Permission denied",
         "沙箱内权限不足:目标路径没有按可写挂载,或 Landlock 规则把它限制了。"),
        ("bwrap: execvp",
         "沙箱内 exec 失败:命令或解释器路径在沙箱里不可见。"),
    )
    for needle, hint in hints:
        if needle in s:
            return hint
    return ""


def mounts_from_token(token: CapabilityToken) -> tuple[list[str], list[str]]:
    """兼容 re-export:见 karvyloop.sandbox.mounts.mounts_from_token。"""
    from karvyloop.sandbox.mounts import mounts_from_token as _impl
    return _impl(token)


def has_net(token: CapabilityToken) -> bool:
    """兼容 re-export:见 karvyloop.sandbox.mounts.has_net。"""
    from karvyloop.sandbox.mounts import has_net as _impl
    return _impl(token)


def _bind_binary_parents(bwrap: list[str], argvs: list[list[str]]) -> None:
    """解析每个 argv[0] 并把它的父目录 ro-bind 进沙箱,让 `sleep` / `sh` 等可见。

    处理三处:
      1. argv[0] 是绝对路径 → 直接 bind 父目录
      2. argv[0] 是裸名(无 /) → shutil.which 解析,可能命中 /usr/bin/<x>、/bin/<x>
      3. 解析不到 → 不 bind(让 bwrap 报 "No such file" 给上层,可观测)

    `argvs` 是若干条命令(run_command 场景里是**里层 landlock wrapper + 真正 payload** 两条):
    两条都要看 —— 只绑 wrapper 的 python3 会让 payload 自己的解释器(如 .venv/bin/python)
    在沙箱里"不存在"(ENOENT)。

    同时确保:
      - /bin 和 /usr/bin 至少有一个被 bind(常见工具集)
      - ld 解析需要的 /lib /lib64 也被 bind
    """
    candidate_paths: set[str] = set()
    for argv in argvs:
        if not argv:
            continue
        first = argv[0]
        if "/" in first:
            candidate_paths.add(first)
        else:
            resolved = shutil.which(first)
            if resolved:
                candidate_paths.add(resolved)

    # 把候选路径的父目录 + 一些基础库目录加入
    bind_dirs: set[str] = set()
    for p in candidate_paths:
        bind_dirs.add(os.path.dirname(p))
    # ld / glibc 解析需要的目录(常见)
    for d in ("/bin", "/usr/bin", "/lib", "/lib64", "/usr/lib", "/usr/lib64"):
        if os.path.isdir(d):
            bind_dirs.add(d)

    for d in sorted(bind_dirs):
        bwrap += ["--ro-bind", d, d]

    # 让 PATH 在沙箱内仍可用(很多脚本/子进程靠它)
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    bwrap += ["--setenv", "PATH", path_env]


class BubblewrapSandbox:
    """Linux bwrap 沙箱。需要 bwrap 在 PATH（apt: bubblewrap）。"""

    name = "bubblewrap"

    #: Landlock 支持性探测缓存(None=未探测;避免每次 exec 都 syscall 探)
    _landlock_supported: Optional[bool] = None

    @staticmethod
    def available() -> bool:
        return shutil.which("bwrap") is not None

    @classmethod
    def _landlock_inner(cls, rw: list[str], ro: list[str]) -> list[str]:
        """内核支持 Landlock → 返回装进沙箱内部的 `python3 landlock.py <rw> <ro> --` 前缀;
        不支持 → 空列表(优雅降级,零回归)。

        **顺序很关键**:Landlock 必须装在 bwrap **里面**,不能套在外面。
        bwrap 自己要 `unshare(CLONE_NEWUSER)` 再写 `/proc/self/uid_map`;外层 Landlock 把
        `/proc` 设为只读 → bwrap 报 `setting up uid map: Permission denied`,整个沙箱起不来
        (实测)。装在里层则只限制 payload,且 domain 被其后所有子进程继承。

        里层只 ro-bind 单个 landlock.py + 用系统 python3 跑,不暴露 venv/源码目录。
        """
        import json
        if cls._landlock_supported is None:
            try:
                from karvyloop.platform.linux.landlock import is_supported
                cls._landlock_supported = is_supported()
            except Exception:
                cls._landlock_supported = False
        if not cls._landlock_supported:
            return []
        return [cls._landlock_python(), cls._landlock_file(),
                json.dumps(rw), json.dumps(ro), "--"]

    @staticmethod
    def _landlock_python() -> str:
        """沙箱内可用的 python3(/usr/bin 已 ro-bind,与系统解释器同源)。"""
        return shutil.which("python3") or "/usr/bin/python3"

    @staticmethod
    def _landlock_file() -> str:
        from karvyloop.platform.linux import landlock as _ll
        return os.path.abspath(_ll.__file__)


    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        if not argv:
            raise ValueError("argv 必须非空")
        if not self.available():
            raise RuntimeError(
                "bubblewrap 不可用（PATH 中没有 bwrap）—— Linux 上 `apt install bubblewrap`"
            )

        ro, rw = mounts_from_token(token)
        # 授权台账(fs_grants):人批过的工作区外路径也挂进沙箱(read→ro / write→rw)。
        # 敏感地板在台账 allows/record 层已绝对优先,这里只会拿到干净授权。
        try:
            from karvyloop.capability.fs_grants import get_store
            _st = get_store()
            if _st is not None:
                import time as _t
                for g in _st.list():
                    if g.get("expired"):
                        continue
                    (rw if "write" in (g.get("ops") or []) else ro).append(g["path"])
        except Exception:
            pass
        # 死路径/重复路径一律丢弃:台账里可能残留已被改名/删掉的角色技能文件,
        # 而 bwrap 只要有一条 --ro-bind 的源不存在就整体失败(实测 error_code 10)。
        ro, rw = _existing_mount_paths(ro), _existing_mount_paths(rw)
        rw_set = set(rw)
        ro = [p for p in ro if p not in rw_set]   # 同一路径读写都给 → 按可写挂
        net = has_net(token)
        allowlist = net_allowlist_of(token)

        # ---- 按域名 egress allowlist(外部子进程成员化的确定性网络地基)----
        # allowlist 非空 = 只放行这些域名、其余拒。需**域名级强制**,且强制必须**不可绕过**。
        #
        # 域名级强制的两个部件:
        #   (1) 用户态 allowlist 代理(egress_proxy.AllowlistProxy)—— **已焊、已测**:HTTP CONNECT +
        #       SOCKS5,命中 allowlist 放行、否则确定性拒(连 socket 都不给建),IP 字面量拒(防绕过)。
        #   (2) 让子进程的**唯一 egress** 强制走该代理(否则客户端可无视 *_PROXY 直连 = 假放行)。
        #       这要求「隔离 netns + 唯一路由指向代理」的 netns 装配(pasta/slirp4netns 免 root 栈)。
        #
        # 部件(2)的**不可绕过 netns 装配尚未落地/未在真机验证**。安全是地基,宁 fail-closed 不假安全:
        # 在(2)被真机验证前,**allowlist 非空一律 fail-closed 拒网**(--unshare-net 全关)——
        # **绝不**退回"仅 *_PROXY env"(可被子进程无视 = 假放行,违纪律)。这与 macOS/Windows 当前
        # 同为 fail-closed 短板,但部件(1)代理已作为可复用地基落地并测通,待(2)装配即可翻成真强制。
        # allowlist 空 = 保持二元(net 决定全放/全拒),零回归。
        force_unshare_net = False
        if allowlist:
            # TODO(P1):落地并**真机验证**不可绕过的 netns→代理专用路由(pasta/slirp4netns),
            #   验证通过后在此起 AllowlistProxy 并把 net 交给它夹;在那之前恒 fail-closed。
            force_unshare_net = True
            net = False

        bwrap: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--chdir", cwd,
        ]
        if not net or force_unshare_net:
            bwrap.append("--unshare-net")
        for p in ro:
            bwrap += ["--ro-bind", p, p]
        for p in rw:
            bwrap += ["--bind", p, p]

        # 默认只挂 /proc /dev /tmp —— `sleep` / `sh` / `cat` 等
        # 都在 /usr/bin、/bin,沙箱里直接调会 "No such file or directory"。
        # 用 shutil.which 解析 argv[0] 并 ro-bind 它的父目录 —— 标准 bubblewrap
        # 实战做法。token 决定哪些 fs 路径可写,这一步只让 binary 可见,不放开写。
        # 工作区路径显式交给 payload:技能目录/实例目录在沙箱里恒只读,技能脚本据此把
        # 产物目录默认落到可写的工作区(KARVY_WORKSPACE/{cases,snapshots,out,…}),
        # 而不是要求模型自己 export 一串变量。
        if cwd and os.path.isdir(cwd):
            bwrap += ["--setenv", "KARVY_WORKSPACE", os.path.abspath(cwd)]

        # Landlock 深度防御:装在 bwrap **里层**(理由见 _landlock_inner 的 docstring)。
        # 只 ro-bind 一个 landlock.py 脚本文件,用系统 python3 跑 —— 不暴露 venv/源码目录。
        ll_prefix = self._landlock_inner(rw, ro)
        if ll_prefix:
            ll_file = self._landlock_file()
            bwrap += ["--ro-bind", ll_file, ll_file]

        # 沙箱内可见的 binary 目录按「里层解释器 + 真正 payload」一起解析:bwrap 起的是
        # python3(可能解析到 venv 下的路径),它的父目录同样得 ro-bind 进去;payload 自己的
        # 解释器(如 .venv/bin/python)也一样,否则里层 wrapper execvp 时 ENOENT。
        _bind_binary_parents(bwrap, [ll_prefix, list(argv)])

        bwrap += ["--"] + ll_prefix + list(argv)

        proc = await asyncio.create_subprocess_exec(
            *bwrap, stdin=asyncio.subprocess.PIPE,
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
        # bwrap 自己的硬失败(挂载源不存在 / userns 被禁 …)只有英文一行,补一句中文提示。
        # 只在 stderr 里出现 bwrap 自己的前缀、且整条命令非 0 退出时追加;payload 自己的
        # stderr(cat: No such file …)一个字都不动。
        if err and (proc.returncode or 0) != 0 and b"bwrap:" in err:
            hint = humanize_bwrap_error(err.decode("utf-8", errors="replace"))
            if hint:
                err += ("\n[沙箱] %s\n" % hint).encode("utf-8")
        return ExecResult(
            stdout=out, stderr=err, exit_code=proc.returncode or 0,
            timed_out=timed_out, truncated=truncated,
        )

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        """只接受 token 覆盖的 fs 路径；写越界 = 拒绝。"""
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
