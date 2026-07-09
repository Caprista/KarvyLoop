"""coding 工具集（coding/tools/）。

规格：docs/modules/forge.md §2.2（HR-1/HR-4/HR-5/HR-6）。
四件套:read / write / edit / bash。统一 CodingTool 抽象(可被 atoms/orchestration
作为 Tool 协议消费;concurrent_safe 由 is_concurrency_safe 接收 input 动态判定)。
"""

from __future__ import annotations

from typing import Any

from karvyloop.schemas import CapabilityToken

from ._result import CodingResult
from .read import ReadTool
from .write import WriteTool
from .edit import EditTool
from .bash import BashTool
from .web import WebFetchTool, WebSearchTool
from .reconcile import ReconcileReceiptTool


def make_coding_tools(sandbox, file_state, workspace_root: str,
                      *, token: CapabilityToken,
                      read_only: bool = False) -> dict[str, Any]:
    """工厂:返回 {tool_name: instance}。token 在任务生命周期内绑入工具实例。

    read_only=True(loop step3 独立验收者用):只给 read_file + run_command —— 能读产物、
    能跑测试/脚本核验,但**不给** write_file / edit_file,维持作者(maker)/验收者(checker)
    分离。注:run_command(bash)理论上仍能写文件,是已知 loophole,靠验收者 prompt 明令
    "只核验不修改" 约束;P1 上真只读沙箱再硬隔离。
    """
    tools = {
        "read_file": ReadTool(sandbox, file_state, workspace_root, token=token),
        "run_command": BashTool(sandbox, file_state, workspace_root, token=token),
        # 基础能力(Hardy):知识库没命中 → 联网搜/读。只读网络,maker/checker 都给。
        "web_search": WebSearchTool(sandbox, file_state, workspace_root, token=token),
        "web_fetch": WebFetchTool(sandbox, file_state, workspace_root, token=token),
        # 报销的确定性算术 tool(纯计算、只读、maker/checker 都给):expense skill 在 allowed-tools
        # 声明、方法里调 —— 报销员 role 组合该 skill 即得。把"算"从模型脑子里搬到确定性代码(防降级)。
        "reconcile_receipt": ReconcileReceiptTool(),
    }
    if not read_only:
        tools["write_file"] = WriteTool(sandbox, file_state, workspace_root, token=token)
        tools["edit_file"] = EditTool(sandbox, file_state, workspace_root, token=token)
    return tools


__all__ = [
    "CodingResult",
    "ReadTool", "WriteTool", "EditTool", "BashTool", "WebFetchTool", "WebSearchTool",
    "ReconcileReceiptTool", "make_coding_tools",
]
