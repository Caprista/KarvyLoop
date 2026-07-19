"""cognition.pursuit — 目标/意图管理器（cognition/pursuit.py）。

规格：docs/modules/pursuit.md
里程碑：M1（M1 v1 = 单任务 Pursuit:commit/is_done;M3 跨层完整）

闭环完整性（#0 §4.3）：commitment + revision + verify 缺一不可 —
  - 光承诺不修订 = 越跑越歪（SlopCodeBench）
  - 缺验证门 = 不知道完没完
  - verify_gate 是确定性的(HR 同 #5):门是判定函数(文件存在/测试通过/谓词),
    绝不是"再问模型"。与 crystallize 关 1 同源
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

from karvyloop.capability.token import mint
from karvyloop.sandbox import default_sandbox
from karvyloop.schemas import Belief, Capability, Pursuit

from .memory import MemoryManager

logger = logging.getLogger(__name__)


# ---- commitment_condition / revision_trigger 表达式(简单 key==value / key in)----

# v1 条件语法:逗号分隔的子句,每个形如:
#   key == value
#   key != value
#   key in context(只要 key 存在)
#   key not in context
# 全部 AND。空字符串 → False 兜底。
_CLAUSE_RE = re.compile(
    r"""^\s*(\w+)\s*(==|!=|in|not_in|not in)\s*(.*?)\s*$""",
    re.IGNORECASE,
)


def _eval_clause(clause: str, context: dict) -> bool:
    m = _CLAUSE_RE.match(clause)
    if not m:
        return False
    key, op, raw_value = m.group(1), m.group(2).lower(), m.group(3).strip()
    has_key = key in context
    if op == "in" or op == "not_in" or op == "not in":
        if op in ("not_in", "not in"):
            return not has_key
        return has_key
    # == / !=
    if not has_key:
        return op == "!="
    actual = context[key]
    # 宽松比较:字符串去引号 + 数字归一
    actual_str = _coerce(actual)
    want_str = _coerce(_strip_quotes(raw_value))
    if op == "==":
        return actual_str == want_str
    return actual_str != want_str


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _coerce(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def eval_condition(condition: str, context: dict) -> bool:
    """v1 简单条件:逗号分隔子句 AND。空 / None → False 兜底。"""
    if not condition or not condition.strip():
        return False
    parts = [p.strip() for p in condition.split(",") if p.strip()]
    return all(_eval_clause(p, context) for p in parts)


# ---- verify_gate:确定性判定(spec §3 + §4)----

class GateError(ValueError):
    """verify_gate 形式非法(spec §4 拒绝"再问模型")。"""


# file_exists path 里的花括号 = 占位符指纹(docs/88 真伤4)。第一刀不做路径模板,含花括号的
# path 判为坏门,创建期就拒(routes/triage 校验);单花括号也算(LLM 常吐 `{date}` / 半开 `{`)。
_PATH_PLACEHOLDER_RE = re.compile(r"[{}]")


def path_has_placeholder(path: str) -> bool:
    """file_exists path 是否含 `{...}` 花括号(疑似模板占位符)。绝对/相对正常路径不含花括号。"""
    return bool(path and isinstance(path, str) and _PATH_PLACEHOLDER_RE.search(path))


def _gate_file_exists(gate: dict, context: dict) -> bool:
    """file_exists 门:路径**字面**存在判定(docs/88 真伤4)。

    **不**做 str.format 模板替换——旧实现 `path.format(**context)` 有两个坑:
      ① 未知占位符 `{date}` → KeyError / 单花括号 `{` → ValueError,异常冒穿 tick(step 早于节流戳
         写入 → last_advance_ts 恒 0、6h 节流永不生效、每 10min 重炸,所有成本地板都够不着);
      ② `{x.__class__.__mro__}` 之类走属性访问 = 信息泄露面。
    路径当字面用:含无法解析占位符的路径是**创建期**就该拒的坏门(routes/triage 用 path_has_placeholder
    拦,宁空勿毒),不是运行期问题;真要模板另立门类型再说(第一刀不做)。context 不再被 file_exists 碰。
    """
    path = gate.get("path", "")
    if not path or not isinstance(path, str):
        return False
    return os.path.isfile(path)


def _gate_predicate(gate: dict, context: dict) -> bool:
    """"key in context AND key == value" 之类 — 复用 eval_condition。"""
    expr = gate.get("expr", "")
    return eval_condition(expr, context)


def split_test_pass_cmd(cmd: str) -> list:
    """把 test_pass 的 cmd 拆成 argv —— **平台感知**(docs/88 真伤3)。单一真理来源:gate 求值
    与 create 时的健全性校验共用它,口径一致。

    - POSIX:直接 shlex.split(posix=True)(反斜杠是转义,符合 shell 习惯)。
    - Windows:POSIX shlex 会把反斜杠路径(C:\\...\\x.py)当转义**拆碎** → 自然写法的 gate 静默永红;
      而纯 `posix=False` 又会把 `-c "code"` 的引号原样留住 → Python 当字符串字面量、静默 exit 0(假过)。
      故用 `posix=False` 先按空白/引号分组(保住反斜杠),再**逐 token 剥掉最外层成对双引号** ——
      两头兼顾(反斜杠路径保真 + 引号参数正确剥壳)。
    """
    cmd = cmd or ""
    if os.name != "nt":
        return shlex.split(cmd, posix=True)
    toks = shlex.split(cmd, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t for t in toks]


# ---- test_pass 沙箱执行(P1 安全缝收口:不可信 gate 命令关进笼子)----
#
# gate.cmd 来自用户 / LLM 判型 = **不可信输入**,人 ACCEPT 后每 tick 执行 → 裸 subprocess.run
# 等于把不可信命令直接喂给宿主(能写宿主任意文件 / 出网 / 无资源上限)。改走既有三后端沙箱
# (Linux bubblewrap / macOS seatbelt / Windows 受限进程),与业界跑测试的做法一致:
#   - fs 范围 = gate 的 cwd(测试要读写项目目录),token 显式授权 read+write。
#   - 网络默认隔离:优先申请"网络隔离档"(Windows→AppContainer 内核 WFP 拒出网;
#     Linux/macOS 无 net grant 本就 unshare-net / deny network*)。拿不到网络隔离(如本机
#     AppContainer 探不通)→ 诚实降到"文件/资源隔离档"(网络未隔离),不静默假装隔离。
#   - 超时 / 输出截断用沙箱自带(TerminateJobObject 杀整棵树 / wait_for kill / UTF-8 截断),
#     替掉手写 subprocess timeout(Windows 上手写 timeout 杀不到子孙进程树)。
#   - **fail-closed 但 fail-loud**:平台无真隔离后端(降级到"无隔离直通"档 available()==False)
#     → 拒跑不可信 gate,判 False,人话原因写进日志(可见处),绝不静默永红。
#
# verify_gate 仍是**确定性零 LLM**:这里只跑子进程读退出码,不问模型(AC3 守门)。

# test_pass 门默认超时(docs/88 真伤5):抬到给沙箱**冷启**留足余量 —— Windows AppContainer 首次
# 装配实测 ~46s,旧 60s 默认在负载下越线把**已过的门**闪成"没完成"(假红,叠真伤1还再等 6h)。
# 理想是把沙箱 setup 耗时与命令执行超时分开计,但 ExecResult 不暴露 setup 完成时刻(接口拿不到)
# → 单纯抬默认值兜冷启开销;待 Trace 真数据标定。保持 per-gate 可配(gate.timeout_s 覆盖)。
GATE_TEST_PASS_DEFAULT_TIMEOUT_S = 300.0

# fail-loud 稳定码(docs/88 真伤7):cognition 层**只出码不出译文**(分层)—— _gate_test_pass 把原因码
# 写进传入的 context["_gate_note_code"],console 层(有 i18n)读码映射成人话写进 rec.progress_note。
GATE_NOTE_KEY = "_gate_note_code"
GATE_NOTE_NO_ISOLATION = "no_isolation"     # 无真隔离后端 → 拒跑不可信 gate(此门永不可完成)
GATE_NOTE_NET_DOWNGRADE = "net_downgrade"   # 网络隔离档拿不到 → 降 first-party 跑(网络未隔离)
GATE_NOTE_TIMED_OUT = "timed_out"           # 沙箱超时强杀
GATE_NOTE_NET_SUSPECT = "net_suspect"       # 失败指纹疑似网络隔离拦截

# 用户可见/日志串(英文常量;用户面人话由 console 层经 i18n 出,见 GATE_NOTE_* 码)。
_GATE_NO_ISOLATION = (
    "test_pass gate refused: this platform has no real isolation sandbox "
    "(degraded / no backend) — an untrusted gate command is NOT run without a cage. "
    "Full isolation on Linux (bubblewrap) / macOS (seatbelt) / Windows (restricted token)."
)
_GATE_NET_DOWNGRADE = (
    "test_pass gate: network-isolated sandbox unavailable on this host; ran under "
    "filesystem + resource isolation with network NOT isolated (honest downgrade)."
)
_GATE_TIMED_OUT = "test_pass gate timed out and was force-killed by the sandbox."

# stderr 里"疑似网络隔离生效/被拦"的确定性指纹(命中 → 把诚实信息抬到 info 级带出)。
_NET_SUSPECT_MARKERS = (
    "10013",                       # WinError 10013(AppContainer 拒出网,中英文都含此码)
    "forbidden by its access permissions",
    "errno 101", "errno 111", "errno 113",   # 网络不可达 / 连接被拒 / 无路由(POSIX)
    "network is unreachable", "connection refused", "no route to host",
    "operation not permitted",     # seatbelt (deny network*)
    "temporary failure in name resolution", "name or service not known", "getaddrinfo",
)


def _sandbox_exec_sync(sandbox: Any, argv: list, *, token, cwd: str,
                       timeout_s: float, max_output_bytes: int = 30_000):
    """把 async `sandbox.exec` 在**同步**代码里跑到出结果 —— 桥两种调用上下文:

      - 本线程**无** running loop(直接 sync 调用 / to_thread 内)→ `asyncio.run`。
      - 本线程**有** running loop(pursuit_tick 是 async,同步调 is_done/step 时事件循环在跑)
        → 在**独立工作线程**里用它自己的新 loop 跑 + join。绝不在**正在跑的** loop 上
        `asyncio.run`/`run_until_complete`(会 RuntimeError 嵌套崩)。

    诚实残余:running-loop 分支的 join **会阻塞调用线程**——这在 is_done/step 保持同步时
    不可避免(它们在 pursuit_tick 的调用点在本刀领地外,不可改),且与旧 subprocess.run 的
    阻塞特征一致;该 tick 是慢侧维护、被 throttle、非热路径。
    """
    def _mk_coro():
        return sandbox.exec(argv, token=token, cwd=cwd, timeout_s=timeout_s,
                            max_output_bytes=max_output_bytes)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 无 running loop → 直接 asyncio.run(新建/收尾一个临时 loop)。
        return asyncio.run(_mk_coro())

    # 有 running loop → 下独立线程跑,绝不嵌套当前 loop。
    box: dict = {}

    def _worker() -> None:
        try:
            box["r"] = asyncio.run(_mk_coro())
        except BaseException as exc:   # 原样跨线程回抛给调用方按类型分流
            box["e"] = exc

    th = threading.Thread(target=_worker, name="pursuit-gate-sandbox", daemon=True)
    th.start()
    th.join()
    if "e" in box:
        raise box["e"]
    return box["r"]


def _interpret_gate_result(res: Any) -> tuple[bool, str, str, str]:
    """ExecResult → (passed, note, log_level, note_code)。note_code 是 GATE_NOTE_* 稳定码(空=不上报)。

    - 干净通过(exit 0)→ (True, "", "", "")。
    - 超时被杀 → (False, 人话, "info", timed_out)。
    - 非 0 退出:疑似网络隔离拦截 → info + net_suspect 码;普通失败(gate 还没做完)→ debug + 无码
      (普通失败每 tick 都发生,别 WARNING 刷屏、别写码打扰;但 stderr 尾始终带出,判不了也留原文)。
    """
    if getattr(res, "timed_out", False):
        return False, _GATE_TIMED_OUT, "info", GATE_NOTE_TIMED_OUT
    if res.exit_code == 0:
        return True, "", "", ""
    tail = b""
    try:
        tail = (res.stderr or b"")[-400:]
    except Exception:
        tail = b""
    txt = tail.decode("utf-8", "replace").strip()
    low = txt.lower()
    suspect = any(m in low for m in _NET_SUSPECT_MARKERS)
    note = f"test_pass gate failed (exit {res.exit_code})"
    if suspect:
        note = ("test_pass gate failed — likely blocked by network isolation "
                f"(exit {res.exit_code})")
    if txt:
        note += f"; stderr tail: {txt}"
    # 普通失败(debug 级)不写码:那是"还没做完",每 tick 都发生,写进 progress_note 会覆盖更有用的
    # 推进备注、且刷屏。只在疑似网络拦(info)时给码。
    return False, note, ("info" if suspect else "debug"), (GATE_NOTE_NET_SUSPECT if suspect else "")


def _run_gate_sandboxed(argv: list, cwd: str, timeout_s: float) -> tuple[bool, str, str, str]:
    """在隔离沙箱里跑 gate argv。返回 (passed, note, log_level, note_code)。

    网络隔离优先(skill-exec 档);拿不到 → 诚实降到 first-party 文件/资源隔离档。
    无真隔离后端 → fail-loud 拒跑(判 False)。note_code 是 GATE_NOTE_* 稳定码,供上层 i18n 出人话。
    """
    try:
        sandbox = default_sandbox()
    except Exception as e:
        return False, f"{_GATE_NO_ISOLATION} (sandbox init failed: {e})", "warning", GATE_NOTE_NO_ISOLATION

    # fail-loud:后端 available()==False(Windows degraded / stub 等"无隔离直通"档)→ 拒跑。
    avail = getattr(sandbox, "available", None)
    if callable(avail):
        try:
            has_isolation = bool(avail())
        except Exception:
            has_isolation = False
        if not has_isolation:
            return False, _GATE_NO_ISOLATION, "warning", GATE_NOTE_NO_ISOLATION

    grants = [Capability(resource=f"fs:{cwd}", ops=["read", "write"])]
    ttl = max(60.0, timeout_s + 30.0)

    # 不可信 gate → 最严档:申请网络隔离。task_id="skill-exec" 让 Windows 受限进程套 AppContainer
    # 内核网络门(免 admin 拒出网);Linux/macOS 无 net grant 本就 unshare-net / deny network*,
    # 该 marker 对它们是 no-op。
    net_iso_token = mint("skill-exec", grants, ttl_seconds=ttl)
    try:
        res = _sandbox_exec_sync(sandbox, argv, token=net_iso_token, cwd=cwd, timeout_s=timeout_s)
        return _interpret_gate_result(res)
    except PermissionError as e:
        # 沙箱拒了"网络隔离档"(如 Windows AppContainer 本机探不通)→ 本机做不出网络隔离。
        # 诚实降到 first-party(文件/资源隔离,网络未隔离)让 gate 仍能跑,而非永红;有真隔离
        # 后端(available()==True)时 first-party 必能跑通(受限令牌 + Job 资源上限)。
        first_party = mint("pursuit-gate", grants, ttl_seconds=ttl)
        try:
            res = _sandbox_exec_sync(sandbox, argv, token=first_party, cwd=cwd, timeout_s=timeout_s)
        except (PermissionError, NotImplementedError, RuntimeError) as e2:
            return False, f"{_GATE_NO_ISOLATION} ({e2})", "warning", GATE_NOTE_NO_ISOLATION
        except (FileNotFoundError, ValueError):
            return False, "", "", ""    # 命令本身拆不出/找不到,非隔离问题,别喊狼来了
        passed, _n, _lvl, _code = _interpret_gate_result(res)
        # 降级本身是安全相关的诚实降档 → warning 带出(生产被 throttle,不刷屏)。
        return passed, f"{_GATE_NET_DOWNGRADE} (net-iso unavailable: {e})", "warning", GATE_NOTE_NET_DOWNGRADE
    except (NotImplementedError, RuntimeError) as e:
        # available() 报可用但 exec 仍说不可用 → fail-loud 拒跑。
        return False, f"{_GATE_NO_ISOLATION} ({e})", "warning", GATE_NOTE_NO_ISOLATION
    except (FileNotFoundError, ValueError):
        # argv 拆不出 / 解释器不存在 → 判 False(沿用旧语义),非隔离失败,不喊狼。
        return False, "", "", ""


def _gate_test_pass(gate: dict, context: dict) -> bool:
    """跑测试,exit 0 → True。**关进沙箱**(P1 安全缝):不可信 gate 命令不再裸喂宿主。

    - 平台感知拆分(docs/88 真伤3):POSIX shlex 在 Windows 会把反斜杠路径当转义拆碎。
    - fs 范围 = gate.cwd(默认进程 cwd);网络默认隔离;超时/截断用沙箱自带。
    - fail-loud:无真隔离后端 → 拒跑 + 日志人话原因,绝不静默永红。
    """
    cmd = gate.get("cmd", "")
    if not cmd:
        return False
    try:
        timeout = float(gate.get("timeout_s", GATE_TEST_PASS_DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout = GATE_TEST_PASS_DEFAULT_TIMEOUT_S
    cwd = gate.get("cwd") or os.getcwd()
    try:
        argv = split_test_pass_cmd(cmd)
    except ValueError:
        return False
    if not argv:
        return False
    passed, note, level, note_code = _run_gate_sandboxed(argv, cwd, timeout)
    if note and level:
        # 日志(诊断):warning=拒跑/降档 · info=超时/疑似网络拦 · debug=普通未过。
        getattr(logger, level, logger.info)(
            "[pursuit verify_gate] %s | cmd=%r cwd=%r", note, cmd, cwd)
    # 真伤7:fail-loud 原因作为**稳定码**写进 context(只 warning/info 级有码;debug"还没完成"无码,
    # 免刷屏/免覆盖推进备注)。console 层(有 i18n)读 GATE_NOTE_KEY 映射成人话进 rec.progress_note。
    if note_code and isinstance(context, dict):
        context[GATE_NOTE_KEY] = note_code
    return passed


GATE_DISPATCH: dict[str, Callable[[dict, dict], bool]] = {
    "file_exists": _gate_file_exists,
    "predicate": _gate_predicate,
    "test_pass": _gate_test_pass,
}


def eval_verify_gate(gate: dict, context: dict) -> bool:
    """verify_gate 确定性判定。门 = 字典 {type, ...};绝不走模型。

    未知 type → raise GateError(spec §4 拒绝隐式默认)。
    """
    if not isinstance(gate, dict):
        raise GateError("verify_gate must be a dict")
    t = gate.get("type")
    fn = GATE_DISPATCH.get(t)
    if fn is None:
        raise GateError(f"unknown verify_gate type: {t!r}; allowed: {sorted(GATE_DISPATCH)}")
    return fn(gate, context)


# ---- PursuitManager ----

class PursuitManager:
    """目标/意图管理器。Pursuit 一等对象的状态机。

    状态(active/committed/revised/done/dropped)由 step() 单向推进;
    commit / should_revise / is_done 单独可调(便于上层组合)。
    """

    def __init__(self, *, memory: Optional[MemoryManager] = None,
                 domain_root: Optional[Path] = None) -> None:
        self._memory = memory
        self._domain_root = domain_root
        self._lock = threading.Lock()

    def commit(self, p: Pursuit, context: dict) -> Pursuit:
        """commitment 条件成立 → 提升为 committed(坚持,不每轮重规划)。"""
        if p.status in ("done", "dropped"):
            # 终止态不参与状态机
            return p
        if eval_condition(p.commitment_condition, context):
            return p.model_copy(update={"status": "committed"})
        # 条件不成立 → 保持 active
        if p.status == "committed":
            return p  # 已经是 committed,条件不再成立时**不**降级(spec §4 防 thrashing)
        return p

    def should_revise(self, p: Pursuit, context: dict) -> bool:
        """revision 触发器命中 → 该重规划(防 drift/slop,BDI 意图修订)。"""
        if p.status not in ("committed", "active"):
            return False
        if not p.revision_triggers:
            return False
        return any(eval_condition(t, context) for t in p.revision_triggers)

    def is_done(self, p: Pursuit, context: dict) -> bool:
        """verify_gate 确定性判定 = deontic 门(HR 同 #5),不靠再问模型。"""
        return eval_verify_gate(p.verify_gate, context)

    def step(self, p: Pursuit, context: dict) -> Pursuit:
        """每个执行节拍:先判完成,再判修订,否则维持承诺(spec §3)。"""
        if self.is_done(p, context):
            return p.model_copy(update={"status": "done"})
        if self.should_revise(p, context):
            return p.model_copy(update={"status": "revised"})
        return self.commit(p, context)

    def persist(self, p: Pursuit, *, domain_id: str = "") -> None:
        """把 Pursuit 落到合适的位置:
          - level=domain → 域公共 KB(落盘到 <domain_root>/<domain_id>/pursuits.md)
          - level=atom/role(personal) → 存为 Belief(私人记忆)

        domain_id(docs/88 真伤3):**真域**从运营层 PursuitRecord.domain_id 线程进来 —— 别再从
        `p.id.split(":")[1]` 拿(那只是 `domain:<随机 12hex>` 的随机段,会把域级完成归档进随机 uuid
        目录、真域丢失)。调用方(pursuit_tick._complete)传 rec.domain_id。

        不抛:持久化失败不阻塞主循环(上层可轮询持久化结果)。
        """
        if p.level == "domain":
            self._persist_domain(p, domain_id=domain_id)
        else:
            self._persist_personal(p)

    def _persist_personal(self, p: Pursuit) -> None:
        if self._memory is None:
            return  # 没接 memory manager,no-op
        # 摘冒档(docs/89 ⑥,Hardy 拍):Pursuit 状态条是**机器投影的执行现实**(完成还过了确定性
        # verify_gate),不是"用户亲口说的"——source 从 user_explicit(人审受保护档)改成
        # `trace_verified`(rank 80,机器派生·不受保护)。① 不再冒充你说话、你的原话不被它压过;
        # ② 不再受保护 → 陈旧状态条(`[pursuit/active]` 被 `[pursuit/done]` 取代后)日常整理能清掉,
        # 不再永久堆积。ts 写真实时刻(修死值 0.0 导致 recent 排序沉底)。
        now = _now()
        belief = Belief(
            content=f"[pursuit/{p.status}] {p.statement}",
            provenance={
                "source": "trace_verified",
                "agent": "pursuit_manager",
                "ts": now,
                "trace_ref": "",
                "pursuit_id": p.id,
                "level": p.level,
            },
            freshness_ts=now,
            scope="personal",  # type: ignore[arg-type]
        )
        self._memory.write(belief)

    def _persist_domain(self, p: Pursuit, *, domain_id: str = "") -> None:
        if self._domain_root is None:
            return
        # 真域从运营层线程进来(真伤3);缺失时落到显式"未分域"目录并 log —— 绝不落随机 uuid
        # (p.id 是 `domain:<随机 12hex>`,拿它当域会把不同域的完成全散进各自随机目录、真域丢失)。
        did = (domain_id or "").strip()
        if not did:
            did = "_unassigned"
            logger.warning(
                "[pursuit] domain 级 Pursuit %s 缺 domain_id → 归档到「%s」目录(非随机 uuid)", p.id, did)
        path = Path(self._domain_root) / did / "pursuits.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 追加(不覆盖;历史)
        ts = _now()
        line = (
            f"- [{ts}] {p.id} status={p.status} level={p.level}\n"
            f"  statement: {p.statement}\n"
            f"  verify_gate: {p.verify_gate}\n"
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _now() -> float:
    import time
    return time.time()


__all__ = [
    "PursuitManager",
    "eval_condition", "eval_verify_gate", "GateError",
    "GATE_DISPATCH", "split_test_pass_cmd", "path_has_placeholder",
    "GATE_TEST_PASS_DEFAULT_TIMEOUT_S", "GATE_NOTE_KEY",
    "GATE_NOTE_NO_ISOLATION", "GATE_NOTE_NET_DOWNGRADE",
    "GATE_NOTE_TIMED_OUT", "GATE_NOTE_NET_SUSPECT",
]
