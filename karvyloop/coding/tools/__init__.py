"""coding 工具集（coding/tools/）。

规格：docs/modules/forge.md §2.2（HR-1/HR-4/HR-5/HR-6）。
四件套:read / write / edit / bash。统一 CodingTool 抽象(可被 atoms/orchestration
作为 Tool 协议消费;concurrent_safe 由 is_concurrency_safe 接收 input 动态判定)。
"""

from __future__ import annotations

import logging
from typing import Any

from karvyloop.schemas import CapabilityToken

from ._result import CodingResult
from .read import ReadTool
from .write import WriteTool
from .edit import EditTool
from .bash import BashTool
from .web import WebFetchTool, WebSearchTool
from .reconcile import ReconcileReceiptTool

logger = logging.getLogger(__name__)

# role 级工具预设(B 方向最小一刀):COMPOSITION.yaml `tools:` 段的组名 → 工具名集合。
# `mcp` / `computer` 是动态前缀组(组内成员运行时才知道),在 apply_tool_preset 里按前缀展开。
_TOOL_PRESET_GROUPS: dict[str, frozenset[str]] = {
    "coding": frozenset({"read_file", "write_file", "edit_file", "run_command",
                         "reconcile_receipt"}),
    "network": frozenset({"web_search", "web_fetch"}),
    "create_atom": frozenset({"create_atom"}),
}


def apply_tool_preset(tools: dict[str, Any], preset) -> dict[str, Any]:
    """按 role 工具预设过滤工具集(白名单语义)。

    preset = COMPOSITION.yaml `tools:` 段引用的组名/显式工具名列表。
    - 组名:`coding` / `network` / `create_atom` 查表展开;`mcp` = 所有 mcp_*;
      `computer` = 所有 mcp_computer_use_*(独立于 mcp 可单列 —— 桌面控制可单独授予)。
    - 显式工具名:直通(与组展开结果并集)。
    - 未知名:忽略 + log(fail-soft —— 写错预设名不该把 role 搞成零工具砖)。
    - preset 为空/None → 原样返回(调用方保证只在 preset 非空时调;此处双保险)。

    安全边界:本过滤是**可见性收窄**(prompt 噪音 + 攻击面),不是权限闸 ——
    权限仍由 capability authorize / policy 下限表在执行咽喉兜底(过滤只是第一道工序)。
    """
    if not preset:
        return tools
    allowed: set[str] = set()
    unknown: list[str] = []
    for item in preset:
        name = str(item or "").strip()
        if not name:
            continue
        if name in _TOOL_PRESET_GROUPS:
            allowed |= _TOOL_PRESET_GROUPS[name]
        elif name == "mcp":
            allowed |= {k for k in tools if k.startswith("mcp_")}
        elif name == "computer":
            allowed |= {k for k in tools if k.startswith("mcp_computer_use_") or k == "computer"}
        elif name in tools:
            allowed.add(name)
        else:
            unknown.append(name)
    if unknown:
        logger.warning("[tool_preset] 未知工具/组名被忽略: %s", unknown)
    return {k: v for k, v in tools.items() if k in allowed}


def make_coding_tools(sandbox=None, file_state=None, workspace_root: str = ".",
                      *, token: CapabilityToken,
                      read_only: bool = False,
                      seams=None) -> dict[str, Any]:
    """工厂:返回 {tool_name: instance}。token 在任务生命周期内绑入工具实例。

    sandbox 可省(capability/seams):不传则从能力缝注册表解析 sandbox provider
    (selector.default_sandbox 选用时已登记)。传了 = 旧路径,优先用(现状零回归)。
    seams 参数可注入自定义注册表(测试/上层定制);None = 用全局 SEAMS。

    read_only=True(loop step3 独立验收者用):只给 read_file + run_command —— 能读产物、
    能跑测试/脚本核验,但**不给** write_file / edit_file,维持作者(maker)/验收者(checker)
    分离。注:run_command(bash)理论上仍能写文件,是已知 loophole,靠验收者 prompt 明令
    "只核验不修改" 约束;P1 上真只读沙箱再硬隔离。
    """
    if sandbox is None:
        # 从能力缝解析(selector 已注册);缝里没有 → 兜底显式选一次(仍会注册)。
        try:
            from karvyloop.capability.seams import SEAMS, SLOT_SANDBOX
            registry = seams if seams is not None else SEAMS
            sandbox = registry.resolve(SLOT_SANDBOX.name)
        except Exception:
            sandbox = None
        if sandbox is None:
            from karvyloop.sandbox.selector import default_sandbox
            sandbox = default_sandbox()
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
    "ReconcileReceiptTool", "make_coding_tools", "apply_tool_preset",
]
