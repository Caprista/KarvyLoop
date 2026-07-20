"""running-run 注册表 + 协作式中止旗(docs/90 刀3a:停止是安全网,必须秒达)。

此前 executor 9 步循环的 step 0 `if state.abort_requested:` 是死代码 —— 全仓没人置它。
这里补上接线件:任务产生点(drive/workflow/roundtable/pursuit/schedule/proposal)用
`abort_scope(task_id)` 包住执行 → task_id 登进注册表 + 中止旗挂进 contextvar;
cancel 端点 `request_abort(task_id)` 拉响旗 → executor step 0 在**下一轮循环边界**
把 `state.abort_requested` 置 True → 既有 ABORTED_* 收口(合成 tool_result 后 break)。

**中断语义 = 协作式**(既有设计,别改杀进程):当前 LLM 流式 / 单个工具调用跑完才停。

为什么不按任务书原案存 LoopState 引用:executor 每轮**整体替换** state 对象
(executor.py 轮末 `state = LoopState(...)`,快照可回放语义)—— 外部握的引用一轮后就 stale,
置旗写在死对象上。故注册表存**共享可变的 AbortFlag**,executor 每轮查它(contextvar 传递:
穿 `asyncio.to_thread`(copy_context)+ 线程内 `asyncio.run`(Task 复制当前 context),
一路到 forge → executor,不用改 forge/MainLoop 签名)。

零依赖模块(atoms 层最底):console/runtime 都可安全 import。
"""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import Iterator, Optional


class AbortFlag:
    """单个 running run 的协作式中止旗(共享可变对象;set 后 executor 下一轮边界收口)。"""

    __slots__ = ("_requested",)

    def __init__(self) -> None:
        self._requested = False

    def set(self) -> None:
        self._requested = True

    def is_set(self) -> bool:
        return self._requested


class RunningRunRegistry:
    """task_id → 活 run 的 AbortFlag。run 完就清(经 abort_scope);线程安全。

    同一 task_id 重复/嵌套 register 复用同一面旗(别把已拉响的旗盖掉)+ **引用计数**:
    unregister 减到 0 才真弹 —— 否则嵌套 scope(如未来某内层路径也包一圈同 id)里
    内层退出会把外层的旗弹丢,外层 run 从此不可停(对抗验收 E2 实锤的潜伏雷)。
    """

    def __init__(self) -> None:
        self._flags: dict[str, tuple[AbortFlag, int]] = {}   # task_id → (旗, 引用计数)
        self._lock = threading.Lock()

    def register(self, task_id: str) -> AbortFlag:
        with self._lock:
            ent = self._flags.get(task_id)
            if ent is None:
                flag = AbortFlag()
                self._flags[task_id] = (flag, 1)
            else:
                flag = ent[0]
                self._flags[task_id] = (flag, ent[1] + 1)
            return flag

    def unregister(self, task_id: str) -> None:
        with self._lock:
            ent = self._flags.get(task_id)
            if ent is None:
                return
            if ent[1] <= 1:
                self._flags.pop(task_id, None)
            else:
                self._flags[task_id] = (ent[0], ent[1] - 1)

    def request_abort(self, task_id: str) -> bool:
        """拉响某活 run 的中止旗。返回 True=找到活 run 并已置旗;False=没这条活 run
        (可能已跑完 / 该路径没接注册表)—— 调用方如实上报,不装成功。"""
        with self._lock:
            ent = self._flags.get(task_id)
        if ent is None:
            return False
        ent[0].set()
        return True

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._flags


#: 进程级单例(console 单进程;测试可自建 RunningRunRegistry 隔离)。
running_runs = RunningRunRegistry()

_current_flag: contextvars.ContextVar[Optional[AbortFlag]] = contextvars.ContextVar(
    "karvyloop_abort_flag", default=None)


def current_abort_flag() -> Optional[AbortFlag]:
    """executor step 0 用:取当前执行链上挂的中止旗(没挂 = None,零成本)。"""
    return _current_flag.get()


@contextmanager
def abort_scope(task_id: str, *, registry: Optional[RunningRunRegistry] = None,
                ) -> Iterator[Optional[AbortFlag]]:
    """在任务产生点包住执行:注册 task_id→flag + 旗挂 contextvar(穿 to_thread/asyncio.run
    直达 executor)。task_id 空(如 --no-llm / 无 task_registry)→ no-op,零回归。"""
    if not task_id:
        yield None
        return
    reg = registry if registry is not None else running_runs
    flag = reg.register(task_id)
    tok = _current_flag.set(flag)
    try:
        yield flag
    finally:
        _current_flag.reset(tok)
        reg.unregister(task_id)


__all__ = ["AbortFlag", "RunningRunRegistry", "running_runs",
           "current_abort_flag", "abort_scope"]
