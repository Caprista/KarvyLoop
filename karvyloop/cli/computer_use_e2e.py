"""computer-use 真机 E2E 驱动(docs/99 刀2:门到门在**真桌面**上跑一遍).

为什么单独一个手动脚本:computer use 要控**你自己的图形桌面**,headless server / CI /
Windows 都跑不了(没真 DISPLAY)—— 只有在一台有 Linux 图形会话(Wayland/GNOME 等)的机器上
才有意义。所以它不进自动测,是"你坐在 Ubuntu 前、有 15 分钟"时一条命令跑的门到门验。

它复用**已验过的真实驱动路径**(不手搓新接线):
  - cli._runtime.resolve_runtime  → 真 gateway(你 config.yaml 里的模型,如 MiniMax)
  - coding.tools.mcp_tool.connect_mcp_agent_tools → 连上游 computer-use MCP server
  - atoms.run                     → 真 tool-use loop(执行器本身就是 loop,不重造)
  - capability.computer_gate      → 会话同意门 + 高危弹卡(刀1);脚本显式开同意(loud banner)

────────────────────────────────────────────────────────────────────────
运行手册(在你的 **Ubuntu 图形桌面**上,不是 headless VM):

  1. 装 KarvyLoop(拿到 slice-A 感知修复 + computer_use 预设):
         pip install -U "git+https://github.com/Caprista/KarvyLoop"
  2. 装上游 computer-use MCP server(需 node/npm;缺就 `sudo apt install -y nodejs npm`):
         npm install -g @agent-sh/computer-use-linux
     (首次跑 npx 也会自动装;全局装更快更稳)
  3. 有 config.yaml 且配了会**视觉**的模型(你的 MiniMax 有视觉):~/.karvyloop/config.yaml
     没有就先 `karvyloop init` 再把 minimax key 填进去。
  4. 跑(默认做一个"看屏"任务,最安全、正好验 slice-A 感知):
         python -m karvyloop.cli.computer_use_e2e
     换任务:
         python -m karvyloop.cli.computer_use_e2e "Open the Files app and tell me what's in the sidebar"
     指定模型:
         python -m karvyloop.cli.computer_use_e2e --model minimax/MiniMax-M2 "..."

  会打印每一步(工具调用 + 是否真拿到截图 + 模型在想什么),并把全程写进
  ~/.karvyloop/computer_use_e2e_<时间>.log 供回看。

安全:脚本**显式打开**机器控制(会话同意门,刀1 默认是关的)并大字提示;高危/不可逆动作
(删除、往密码框键入、危险组合键)仍会被结构性扣下要确认 —— 这是设计,不是 bug。跑完即结束,
不常驻。别在有敏感东西开着的桌面上乱跑陌生任务。
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Optional

# 上游 computer-use MCP server(stdio):`computer-use-linux mcp`;npx 拉起其 bin,首启会装。
_CU_COMMAND = "npx"
_CU_ARGS = ["-y", "@agent-sh/computer-use-linux", "mcp"]
_CU_SERVER = "computer_use"

# slice-B(a11y-优先的 planner 引导,模型无关)。据真机 E2E 观察改硬(2026-08-01):M3 首轮
# 直接调 screenshot+focused_window 而没先 get_app_state → 把"先 get_app_state"写成命令式;
# window_control 后端部分环境不可用 → 明说别依赖 list/focused_window;补按键示例 + 每步后再观察。
_SYSTEM_GUIDANCE = (
    "You operate a real Linux desktop through computer-control tools (all named "
    "mcp_computer_use_*). Key tools: get_app_state (returns a screenshot AND the accessibility "
    "tree together — your primary way to perceive the screen), screenshot (image only), "
    "list_apps / list_windows / focused_window (may be UNAVAILABLE on some setups — do not depend "
    "on them), click, type_text, press_key, scroll, drag, activate_window.\n\n"
    "How to work (accessibility-first, model-agnostic):\n"
    "1. ALWAYS begin by calling get_app_state — do NOT use screenshot alone. get_app_state gives "
    "you both the picture and the accessibility tree (elements with roles, labels and indices) in "
    "one call.\n"
    "2. To act on an element, prefer clicking it by its accessibility index / semantic selector "
    "(robust, resolution-independent). Only fall back to pixel coordinates (read from the "
    "screenshot) when the element is not in the accessibility tree.\n"
    "3. Use press_key for keys and chords ('super' opens the activities overview, 'Return' "
    "confirms), and type_text to type literal text.\n"
    "4. After EVERY action, call get_app_state again to confirm what changed before deciding the "
    "next step. Never chain blind actions.\n"
    "5. If unsure, observe rather than guess. Irreversible or credential actions (deleting, typing "
    "into password fields, dangerous key combos like shift+delete) are structurally held for the "
    "owner's approval — do not try to force them.\n"
    "When the task is complete, report in one or two sentences what you did and what you observed."
)

_DEFAULT_TASK = ("Take a screenshot of the screen and tell me which application is in focus and "
                 "what you can see. Use get_app_state so you can actually see the screen.")


def _short(v: Any, n: int = 300) -> str:
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def summarize_event(ev: Any) -> Optional[str]:
    """一个执行器事件 → 一行人话(纯函数,便于回看 + 单测)。无关事件返回 None。"""
    cls = type(ev).__name__
    if cls == "ToolCallEvent":
        blk = getattr(ev, "block", None) or ev
        name = getattr(blk, "name", "") or getattr(ev, "name", "")
        inp = getattr(blk, "input", None)
        if inp is None:
            inp = getattr(ev, "input", None)
        return f"→ CALL {name}({_short(inp, 200)})"
    if cls == "ToolResultEvent":
        r = getattr(ev, "result", None)
        name = getattr(r, "name", "?")
        err = getattr(r, "is_error", False)
        saw_img = bool(getattr(r, "images", None))
        tag = "ERROR" if err else "ok"
        img = "  📷 screenshot delivered to planner" if saw_img else ""
        body = getattr(r, "error_reason", "") if err else _short(getattr(r, "content", ""), 200)
        return f"← RESULT {name} [{tag}]{img}  {body}"
    if cls == "TextEvent":
        txt = getattr(ev, "text", "") or ""
        return f"💭 {_short(txt, 500)}" if txt.strip() else None
    if cls == "TerminalEvent":
        return f"■ DONE reason={getattr(ev, 'reason', '?')}"
    return None


async def _run(task: str, *, config_path: Optional[Path], model_ref: Optional[str],
               max_turns: int, log) -> int:
    def emit(line: str) -> None:
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    # 1) 真 gateway(复用 resolve_runtime;失败 fail-loud,不静默)
    from karvyloop.cli._runtime import resolve_runtime
    rt = resolve_runtime(config_path=config_path)
    gw = (rt.runtime_kwargs or {}).get("gateway")
    token = (rt.runtime_kwargs or {}).get("token")
    model = model_ref or (rt.runtime_kwargs or {}).get("model_ref")
    if gw is None or token is None:
        emit(f"✗ 起不来:没拿到 gateway/token(config={rt.config_path},build_error="
             f"{rt.build_error})。先 `karvyloop init` 并填一个**有视觉**的模型 key。")
        return 2
    emit(f"✓ gateway 就绪,planner 模型 = {model}")

    # 2) 连上游 computer-use MCP server(stdio;失败 fail-loud —— 多半是没装 node/server)
    from karvyloop.coding.tools.mcp_tool import connect_mcp_agent_tools
    from karvyloop.mcp_client import McpServerConfig
    cfg = McpServerConfig(name=_CU_SERVER, command=_CU_COMMAND, args=list(_CU_ARGS))
    emit(f"… 连 computer-use server: {_CU_COMMAND} {' '.join(_CU_ARGS)}")
    try:
        group_ctx, tools = await connect_mcp_agent_tools([cfg])
    except Exception as e:
        emit(f"✗ 连不上 computer-use server: {type(e).__name__}: {e}\n"
             f"  多半是没装:`npm install -g @agent-sh/computer-use-linux`(需 node/npm),"
             f"或这台机器没有图形桌面会话。")
        return 3

    async with group_ctx:
        emit(f"✓ 连上,拿到 {len(tools)} 个工具: {', '.join(sorted(tools))}")

        # 3) doctor 就绪报告(直接调,诊断用)。**带超时**:doctor 在某些会话下会卡在门户/截屏
        #    探测上(VM 门到门实测:SSH 里 doctor 工具调用 90s 不返回)—— 诊断步绝不能拖死整个
        #    E2E,超时就跳过、直接去跑真任务。
        doc = tools.get(f"mcp_{_CU_SERVER}_doctor")
        if doc is not None:
            try:
                res = await asyncio.wait_for(doc({}), timeout=20)
                emit(f"🩺 doctor: {_short(getattr(res, 'payload', res), 800)}")
            except asyncio.TimeoutError:
                emit("🩺 doctor 探测 >20s 未返回,跳过(不影响后面真跑;多半卡在截屏门户探测)。")
            except Exception as e:
                emit(f"🩺 doctor 调用失败(继续): {type(e).__name__}: {e}")

        # 4) 显式开会话同意门(刀1 默认关;这里是"留 flag 后开发用"的那个 flag)
        from karvyloop.capability.computer_gate import enable_computer_control
        enable_computer_control()
        emit("⚠️  已显式开启【机器控制】—— 这台桌面的鼠标/键盘/屏幕现在可被 planner 操作。"
             "高危/不可逆动作仍会被扣下要确认。跑完即止。")

        # 5) 真跑(atoms.run;a11y-优先 v1 引导 + FULL 模式满足 computer 的 capability 下限)
        from karvyloop.atoms import run as atoms_run
        from karvyloop.capability.policy import Mode
        from karvyloop.schemas import AtomSpec
        atom = AtomSpec(
            id="cu-e2e", kind="task",
            prompt=_SYSTEM_GUIDANCE + "\n\nTask: " + task,
            input_schema={"type": "object"}, output_schema={"type": "object"},
            tools=sorted(tools), model=model or "")
        emit(f"\n===== 任务:{task} =====")
        n_calls = n_shots = 0
        try:
            async for ev in atoms_run(atom, {"intent": task}, token,
                                      gateway=gw, tools=tools,
                                      default_mode=Mode.FULL, max_turns=max_turns):
                line = summarize_event(ev)
                if line:
                    emit(line)
                if type(ev).__name__ == "ToolCallEvent":
                    n_calls += 1
                if type(ev).__name__ == "ToolResultEvent" and getattr(
                        getattr(ev, "result", None), "images", None):
                    n_shots += 1
        except Exception as e:
            emit(f"✗ 跑任务时炸了: {type(e).__name__}: {e}")
            return 4
        emit(f"\n===== 收尾:{n_calls} 次工具调用,planner 真看到 {n_shots} 张截图 =====")
        emit("回看要点:①doctor 是否 ready ②planner 有没有先 get_app_state 观察 "
             "③截图有没有真到 planner(📷 标记)④它按 a11y 元素点还是瞎猜坐标 ⑤高危有没有被扣下。")
    return 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m karvyloop.cli.computer_use_e2e",
        description="computer-use 真机门到门(需 Linux 图形桌面 + 会视觉的模型)。")
    p.add_argument("task", nargs="?", default=_DEFAULT_TASK, help="要 planner 做的任务(自然语言)")
    p.add_argument("--config", default="", help="config.yaml 路径(默认 ~/.karvyloop/config.yaml)")
    p.add_argument("--model", default="", help="覆盖 planner 模型引用(默认取 config 默认模型)")
    p.add_argument("--max-turns", type=int, default=24, help="最多 tool-use 轮数(默认 24)")
    a = p.parse_args(argv)

    log_path = Path.home() / ".karvyloop" / f"computer_use_e2e_{int(time.time())}.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8")
    except Exception:
        log = sys.stderr  # 家目录写不了也别拦着,退到 stderr
    print(f"[computer-use E2E] 日志 → {log_path}", flush=True)
    try:
        rc = asyncio.run(_run(
            a.task,
            config_path=Path(a.config) if a.config else None,
            model_ref=a.model or None,
            max_turns=a.max_turns,
            log=log))
    finally:
        if log is not sys.stderr:
            log.close()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
