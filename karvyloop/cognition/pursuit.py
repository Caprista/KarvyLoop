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

import os
import re
import shlex
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

from karvyloop.schemas import Belief, Pursuit

from .memory import MemoryManager


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


def _gate_file_exists(gate: dict, context: dict) -> bool:
    path_tpl = gate.get("path", "")
    if not path_tpl:
        return False
    # 简单 {var} 替换:从 context 取
    path = path_tpl.format(**{k: v for k, v in context.items() if isinstance(v, (str, int, float))})
    return os.path.isfile(path)


def _gate_predicate(gate: dict, context: dict) -> bool:
    """"key in context AND key == value" 之类 — 复用 eval_condition。"""
    expr = gate.get("expr", "")
    return eval_condition(expr, context)


def _gate_test_pass(gate: dict, context: dict) -> bool:
    """跑测试,exit 0 → True。timeout 由 gate.timeout_s 控制(默认 60s)。

    M1 v1:子进程跑指定命令。生产上应该走 capability(→ sandbox),
    但 verify_gate 是 M1 同步接口,直接 subprocess 跑(失败沙箱化是 P1)。
    """
    cmd = gate.get("cmd", "")
    if not cmd:
        return False
    timeout = float(gate.get("timeout_s", 60.0))
    cwd = gate.get("cwd", None)
    try:
        # 防御:不接 stdin(防 prompt)
        argv = shlex.split(cmd)
        r = subprocess.run(
            argv, capture_output=True, timeout=timeout, cwd=cwd, check=False,
            stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return False


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

    def persist(self, p: Pursuit) -> None:
        """把 Pursuit 落到合适的位置:
          - level=domain → 域公共 KB(简单落盘到 <domain_root>/<domain>/pursuits.md)
          - level=atom/role(personal) → 存为 Belief(私人记忆)

        不抛:持久化失败不阻塞主循环(上层可轮询持久化结果)。
        """
        if p.level == "domain":
            self._persist_domain(p)
        else:
            self._persist_personal(p)

    def _persist_personal(self, p: Pursuit) -> None:
        if self._memory is None:
            return  # 没接 memory manager,no-op
        belief = Belief(
            content=f"[pursuit/{p.status}] {p.statement}",
            provenance={
                "source": "user_explicit",  # Pursuit 是用户级目标,默认 user_explicit
                "agent": "pursuit_manager",
                "ts": 0.0,  # 持久化时刻不是新鲜度;freshness_ts 用当前
                "trace_ref": "",
                "pursuit_id": p.id,
                "level": p.level,
            },
            freshness_ts=_now(),
            scope="personal",  # type: ignore[arg-type]
        )
        self._memory.write(belief)

    def _persist_domain(self, p: Pursuit) -> None:
        if self._domain_root is None:
            return
        # 域 ID 从 statement / id 前缀拿;v1 直接用 id 拆(形如 "domain:<id>:<...>")
        domain_id = p.id.split(":", 2)[1] if p.id.startswith("domain:") else "default"
        path = Path(self._domain_root) / domain_id / "pursuits.md"
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
    "GATE_DISPATCH",
]
