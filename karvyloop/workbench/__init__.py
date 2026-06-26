"""KarvyLoop Workbench v0 — Textual TUI 落地(M3 批 3,2026-06-16)。

边界(用户决定 2026-06-16 拍 3 启动时定):
- v0 = Textual TUI,**非** docs/20 §6 锁定的"终端 ASCII 占位"
- L4 跨组织按钮 disabled
- L3 跨设备状态条占位("本机 · 跨设备留口 M4+")
- 仅 1 user + 1 karvy observer
- 真实 LLM 不接入(只展示 mock PROPOSE)
- docs/20 §6 改动留 M3 拍 3c 收官时一并改

**灵魂铁律**(UI 不能破):
- K1:小卡永远 observer
- K3:小卡只收 BROADCAST(subscribe_async 必须继承此过滤)
- K4:工作台只读 — grep `apply_` 在本包必须为空
- K5:UI 上 ACCEPT 走 ProposalModal → decision_to_envelope,**不**经 Courier
- A1:UI **不**直接构造 Envelope,只走决策工厂

**K 锁源码扫描验收**(拍 3a 收官时跑):
    grep -rE 'apply_deontic|apply_' karvyloop/workbench/   # 期望为空
    grep -r 'courier_send' karvyloop/workbench/             # 期望为空

设计:docs/20 §3 + plans/snoopy-singing-sunbeam.md。
"""
from .app import WorkbenchApp
from .binding import EnvelopeArrived

__all__ = [
    "WorkbenchApp",
    "EnvelopeArrived",
]