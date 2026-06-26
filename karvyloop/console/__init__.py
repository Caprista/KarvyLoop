"""karvyloop.console — 本地 HTML 控制台(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

K 边界:
- K3:WorkbenchObserver 只 emit BROADCAST(继承既有过滤)
- K4:**只读**控制台 — 0 `domain.apply_*` 调用(grep gate 锁)
- K5:H2A 决策**必须**经 `decision_to_envelope` 工厂 — 0 `Envelope(` 偷构

借:Q5 自造≠闭门造车 — 借 FastAPI/uvicorn(Web 通用基建,清单里 1 行),
                  借 WorkbenchObserver/MainLoop/decision_to_envelope(不重写),
                  **自造**只 FastAPI wiring + 静态文件 mount(~250 LoC 必然)。
"""
from .app import build_console_app
from .proposals import ProposalPump, broadcast_proposal

__version__ = "0.1.0"
__all__ = [
    "build_console_app",
    "ProposalPump",
    "broadcast_proposal",
    "__version__",
]
