"""external_runtime/bridge — 唯一 Python 实体:起子进程、按配方解析、fail-loud、密钥过滤。

**契约(#71 §3.1)**:start(prompt) → BridgeResult。M1 做一次性 start(reply/stream 留后续)。

**设计目标**:薄(少脚手架多信模型)、fail-loud、进程隔离、密钥不外泄。

**六态 fail-loud(#71 §3.4 / #72 §2.3)**——桥永不吞异常,一律翻成 failed(reason) 结果:
| 起不来 | spawn 抛/立即退非0 | failed,reason=stderr 摘要(已过滤) |
| 长任务跑着 | 进程活+有输出 | (M1 一次性:靠 wall-clock 上限兜) |
| 真挂死 | 超 wall-clock | kill + failed |
| input_required | 解析到审批请求 | failed(input_required),升 H2A(调用侧) |
| 空成功 | 退0+text 空 | failed(不报假成功) |
| 取消 | 用户点停止 | kill(M1 由 wall-clock 兜) |

**安全铁律**:
- 命令用 argv 数组构造,**绝不字符串拼进 shell**(引号逃逸 + 注入面)。
- 凭证不进子进程 env:白名单 env(只放 PATH 等非密)+ 黑名单剔 `*_API_KEY`/`*_TOKEN`。
- stdout/stderr 当"可能含密钥的不可信数据":入 Trace/日志/产出前先过 redact。
- blocked_entrypoints 里的已知泄 key 入口:桥拒调(不靠事后过滤兜)。
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
from typing import Optional

from .recipe import (
    DriveRecipe,
    PARSE_NDJSON,
    PARSE_RAW_TEXT,
    PARSE_SINGLE_JSON,
)
from .redact import compile_extra, redact

# 结果状态
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# input_required 的启发式信号(桥解析到 → failed(input_required),调用侧升 H2A;不静默等)
_INPUT_REQUIRED_MARKERS = (
    "approval required", "permission required", "input_required",
    "awaiting approval", "please confirm", "requires your approval",
)

# 组子进程 env 时黑名单剔的密钥形态(凭证不进子进程 env = 一道防线)
_ENV_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_ACCESS_KEY")
# 白名单:只有这些(+ 配方 env)进子进程 env
_ENV_WHITELIST = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING",
                            "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE"})


@dataclasses.dataclass(frozen=True)
class BridgeResult:
    """一次外部 runtime 驱动的结果(fail-loud:失败也返回结果,不抛)。"""
    status: str                 # done | failed
    text: str = ""              # 最终回复(已过滤)
    reason: str = ""            # 失败原因(已过滤)
    exit_code: Optional[int] = None
    usage: Optional[dict] = None  # 边车解析出的 usage(有元数据出口的 runtime)
    stderr: str = ""            # 已过滤的 stderr 摘要
    input_required: bool = False  # True → 调用侧升 H2A 决策卡

    @property
    def ok(self) -> bool:
        return self.status == STATUS_DONE


def _sanitized_env(recipe: DriveRecipe, base_env: Optional[dict] = None) -> dict:
    """组子进程 env:白名单(非密)+ 配方 env + PATH 前置 extra_path;黑名单剔密钥。

    真 key **绝不进子进程 env**(一道防线)。外部执行体拿不到 key,就没有原始 key 可被打进 stdout。
    """
    src = dict(base_env if base_env is not None else os.environ)
    env: dict[str, str] = {}
    for k, v in src.items():
        ku = k.upper()
        if any(ku.endswith(suf) for suf in _ENV_SECRET_SUFFIXES):
            continue  # 黑名单剔密钥形态
        if ku in _ENV_WHITELIST:
            env[k] = v
    # 配方 env(平台分叉/沙箱兜底,非密)
    for k, v in recipe.env_map().items():
        env[k] = v
    # PATH 前置 extra_path(如 runtime 依赖的 node bin)
    if recipe.extra_path:
        extra = os.pathsep.join(os.path.expanduser(p) for p in recipe.extra_path)
        env["PATH"] = extra + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    return env


def _build_argv(recipe: DriveRecipe, *, prompt: str, session_key: str = "",
                agent_id: str = "", sidecar_path: str = "") -> list[str]:
    """填 argv 模板占位符(值也是 argv 元素,绝不拼 shell)。"""
    subs = {"prompt": prompt, "session_key": session_key,
            "agent_id": agent_id, "sidecar_path": sidecar_path}
    argv = [recipe.resolved_bin()]
    for tok in recipe.argv_template:
        out = tok
        for k, v in subs.items():
            out = out.replace("{" + k + "}", v)
        argv.append(out)
    return argv


def _dig(obj, path: str):
    """按 `a.b[0].c` 路径取值(解析失败 → None,不炸)。"""
    if not path:
        return None
    cur = obj
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return cur


def _parse_single_json(stdout: str, recipe: DriveRecipe) -> tuple[str, Optional[dict]]:
    """single_json:整个 stdout 是一个 JSON,回复取 text_path,元数据取 meta_path。"""
    try:
        obj = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return "", None
    text = _dig(obj, recipe.parse.text_path)
    meta = _dig(obj, recipe.parse.meta_path) if recipe.parse.meta_path else None
    return (str(text) if text is not None else ""), (meta if isinstance(meta, dict) else None)


def _parse_ndjson(stdout: str, recipe: DriveRecipe) -> tuple[str, Optional[dict]]:
    """ndjson:逐行 JSON,取最后一条含 assistant text 的行(阶段事件只当进度,不入最终文本)。"""
    text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        # 常见形态:{"type":"...","text":"..."} 或 {"role":"assistant","content":"..."}
        cand = obj.get("text") or obj.get("content") or _dig(obj, "message.content")
        if isinstance(cand, str) and cand.strip():
            text = cand
    return text, None


def _load_sidecar_usage(sidecar_path: str, extra_pats) -> Optional[dict]:
    """读 --usage-file 边车 JSON,抽 usage(input/output/total_tokens + model)。

    边车文件也当"不可信数据":读出的字符串过 redact 后才解析(防边车含 key)。
    """
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except (OSError, IOError):
        return None
    raw = redact(raw, extra_pats)   # 边车也过滤(纵深防御)
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return {
        "input": int(d.get("input_tokens") or 0),
        "output": int(d.get("output_tokens") or 0),
        "cache_read": int(d.get("cache_read_tokens") or 0),
        "cache_write": int(d.get("cache_write_tokens") or 0),
        "total": int(d.get("total_tokens") or 0),
        "model": str(d.get("model") or ""),
        "provider": str(d.get("provider") or ""),
    }


class SubprocessBridge:
    """通用子进程桥(实现 #71 §3.1 契约的 start)。

    依赖注入:_runner 允许测试注入假子进程(默认 subprocess.run);_env_base 允许测试注入干净 env。
    """

    def __init__(self, recipe: DriveRecipe, *, runner=None, env_base: Optional[dict] = None) -> None:
        self._recipe = recipe
        self._runner = runner or self._real_run
        self._env_base = env_base

    def _real_run(self, argv, *, env, timeout, cwd, egress_token=None):
        # egress_token:按域名 egress allowlist 的能力令牌(非空 allowlist 时构造)。
        # 默认 subprocess.run runner 不做沙箱 → 忽略它(非破坏:签名多一个带默认值的 kwarg)。
        # 沙箱后端 runner(注入 _runner)据此对子进程做域名级 egress 强制/fail-closed。
        return subprocess.run(
            argv, env=env, cwd=cwd or None, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, shell=False)   # shell=False 硬纪律:绝不拼 shell

    def _runner_takes_egress(self) -> bool:
        """runner 是否接受 egress_token kwarg(签名内省,不靠 try/except 试跑 —— 避免对
        有副作用的 runner 双执行)。带 **kwargs 的也算接受。"""
        import inspect
        try:
            sig = inspect.signature(self._runner)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.name == "egress_token" or p.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return False

    def _call_runner(self, argv, *, env, timeout, cwd, egress_token):
        """调 runner,把 egress_token 透传给**接受它**的 runner(沙箱后端);不接受的
        (老式/注入测试 runner)用旧调用形态 —— **非破坏**:既有 runner 零改动仍工作。
        """
        if self._runner_takes_egress():
            return self._runner(argv, env=env, timeout=timeout, cwd=cwd,
                                egress_token=egress_token)
        return self._runner(argv, env=env, timeout=timeout, cwd=cwd)

    def start(self, prompt: str, *, cwd: str = "", session_key: str = "",
              agent_id: str = "main", egress_allowlist: tuple[str, ...] = ()) -> BridgeResult:
        """起一轮:组 argv + 组 env(无 key)+ 跑 + 解析 + 过滤 + fail-loud。

        `egress_allowlist`(非破坏可选,默认空):按域名的 egress(出网)白名单。非空 →
        构造一张 `net_allowlist` 非空的 `CapabilityToken` 传给沙箱后端 runner,对外部子进程
        做**域名级 egress 强制**(平台能焊则真强制、焊不出则 fail-closed 拒网 —— 见各平台沙箱)。
        默认空 = 保持现二元网络行为(C1 默认调用零回归:不构造 token、不改任何既有语义)。
        """
        recipe = self._recipe
        extra_pats = compile_extra(recipe.redact_patterns)

        # 按域名 egress allowlist → 构造能力令牌(net_allowlist 非空);空则不构造(零回归)。
        egress_token = None
        if egress_allowlist:
            import time as _time
            from karvyloop.schemas import CapabilityToken
            egress_token = CapabilityToken(
                task_id=f"ext-egress:{agent_id}",
                grants=[],
                expiry=_time.time() + max(recipe.timeout_wall_s, 60) + 60,
                net_allowlist=tuple(egress_allowlist),
            )

        # 边车路径(有元数据出口的 runtime 才用;临时文件,跑完清)
        sidecar_path = ""
        tmp_handle = None
        if recipe.parse.meta_from_sidecar:
            fd, sidecar_path = tempfile.mkstemp(prefix="ext_usage_", suffix=".json")
            os.close(fd)
            tmp_handle = sidecar_path

        try:
            argv = _build_argv(recipe, prompt=prompt, session_key=session_key,
                               agent_id=agent_id, sidecar_path=sidecar_path)
            # blocked_entrypoints 拒调(已知泄 key 入口,不靠事后过滤兜):首个 argv token 命中即拒
            first = (argv[1] if len(argv) > 1 else "").lower()
            for blocked in recipe.blocked_entrypoints:
                if blocked and blocked.lower() == first:
                    return BridgeResult(status=STATUS_FAILED,
                                        reason=f"入口 {blocked} 在黑名单(已知泄 key),桥拒调")
            env = _sanitized_env(recipe, self._env_base)
            try:
                proc = self._call_runner(argv, env=env, timeout=recipe.timeout_wall_s,
                                         cwd=cwd, egress_token=egress_token)
            except subprocess.TimeoutExpired:
                # 真挂死:超 wall-clock(runner 已 kill)
                return BridgeResult(status=STATUS_FAILED,
                                    reason=f"超时(>{recipe.timeout_wall_s}s)无最终态,已 kill",
                                    input_required=False)
            except FileNotFoundError as e:
                return BridgeResult(status=STATUS_FAILED,
                                    reason=f"起不来(二进制找不到):{redact(str(e), extra_pats)}")
            except Exception as e:  # noqa: BLE001 — 桥永不穿透异常
                return BridgeResult(status=STATUS_FAILED,
                                    reason=f"起不来:{type(e).__name__}: {redact(str(e), extra_pats)}")

            exit_code = int(getattr(proc, "returncode", 1) or 0)
            # stdout/stderr 当不可信数据:入任何面前先过 redact
            stdout = redact(getattr(proc, "stdout", "") or "", extra_pats)
            stderr = redact(getattr(proc, "stderr", "") or "", extra_pats)

            # 退非 ok_codes → failed(读过滤后 stderr)
            if exit_code not in recipe.exit.ok_codes:
                return BridgeResult(status=STATUS_FAILED, exit_code=exit_code, stderr=stderr,
                                    reason=f"退码 {exit_code}:{(stderr or stdout)[:300]}")

            # input_required 启发式:桥解析到审批请求 → failed(input_required),调用侧升 H2A(不静默等)
            low = (stdout + " " + stderr).lower()
            if any(m in low for m in _INPUT_REQUIRED_MARKERS):
                return BridgeResult(status=STATUS_FAILED, exit_code=exit_code, stderr=stderr,
                                    reason="外部执行体要权限/澄清(input_required),升 H2A",
                                    input_required=True)

            # 按 parse.mode 抽最终文本
            if recipe.parse.mode == PARSE_SINGLE_JSON:
                text, meta = _parse_single_json(stdout, recipe)
            elif recipe.parse.mode == PARSE_NDJSON:
                text, meta = _parse_ndjson(stdout, recipe)
            else:  # PARSE_RAW_TEXT:整个(已过滤)stdout 就是答案
                text, meta = stdout.strip(), None

            # 空成功坑:退 0 但产出空 → 判 failed,不报假成功
            if recipe.exit.empty_is_failure and not (text or "").strip():
                return BridgeResult(status=STATUS_FAILED, exit_code=exit_code, stderr=stderr,
                                    reason="退 0 但产出为空(空成功坑),判 failed")

            # usage:边车出口(raw_text_sidecar)读 --usage-file;内嵌 meta(single_json)取 meta
            usage = None
            if recipe.parse.meta_from_sidecar and sidecar_path:
                usage = _load_sidecar_usage(sidecar_path, extra_pats)
            elif isinstance(meta, dict):
                mu = meta.get("usage") if isinstance(meta.get("usage"), dict) else meta
                if isinstance(mu, dict):
                    usage = {
                        "input": int(mu.get("input_tokens") or mu.get("input") or 0),
                        "output": int(mu.get("output_tokens") or mu.get("output") or 0),
                        "total": int(mu.get("total_tokens") or mu.get("total") or 0),
                        "model": str(meta.get("model") or mu.get("model") or ""),
                        "provider": str(meta.get("provider") or ""),
                    }

            return BridgeResult(status=STATUS_DONE, text=text, exit_code=exit_code,
                                usage=usage, stderr=stderr)
        finally:
            if tmp_handle:
                try:
                    os.unlink(tmp_handle)
                except OSError:
                    pass


def bridge_factory(recipe: DriveRecipe, *, env_base: Optional[dict] = None,
                   runner=None) -> SubprocessBridge:
    """(DriveRecipe) -> Bridge(#71 §5 make_external_agent_tool 依赖)。"""
    return SubprocessBridge(recipe, runner=runner, env_base=env_base)


__all__ = ["SubprocessBridge", "BridgeResult", "bridge_factory",
           "STATUS_DONE", "STATUS_FAILED"]
